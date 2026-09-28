#!/usr/bin/env python3
"""lifecycle: the life of every observed container, and of every recorded run, as data.

A dashboard can say a node stopped reporting; this says why. It watches Docker and the
run records the obs CLI writes, and turns both into log lines and metrics that carry the
label model (net, run_id, node, client, job):

  containers  every Docker event of an observed container (any org.abutlabs.obs.* label):
              create, start, restart, kill (with its signal), oom, stop, die (with its exit
              code) and destroy, becomes
                * one JSON line in Loki, stream {source="lifecycle", kind="container", net,
                  run_id, node, client, job, container}, fields event, msg and details;
                * metrics: obs_container_running, obs_container_events_total{event},
                  obs_container_restarts_total, obs_container_last_exit_code and
                  obs_container_stopped_timestamp_seconds{reason, during_run};
              and every running one's CPU and memory, from the Docker stats API
              (obs_container_cpu_seconds_total, obs_container_memory_bytes).
  runs        every run record (`obs begin`, `obs end`, `obs register` write them to
              OBS_STATE/runs) becomes obs_run_info{net, run_id, kind, link_from, link_to},
              obs_run_start_timestamp_seconds and obs_run_end_timestamp_seconds, and a Loki
              line at the run's start and at its end (stream {source="lifecycle",
              kind="run", net, run_id}). link_from and link_to are the dashboard range that
              shows the whole run (unix ms, 1 min margins; link_to is "now" while it runs):
              the dashboards' "whole run" link reads them.

Why a container stopped (reason), settled when it is removed, restarts, or SETTLE seconds
after it died (Docker reports a stop just before or just after the die):

  oom       the kernel killed it for memory
  teardown  stopped (docker stop, compose stop or down) and then removed, or stopped after
            its run ended: the net was shut down
  stopped   stopped (docker stop) while its run was active, and not removed
  killed    killed by a signal (docker kill, docker rm -f) with no stop
  crashed   exited non-zero by itself
  exited    exited 0 by itself

during_run is "true" when the stop was not a teardown and the container's run had not
ended (a run whose end is recorded up to SETTLE seconds after the die counts as ended:
tooling may record the end just after taking the net down).

Metrics on :9914. Environment: LIFECYCLE_RUNS (run records, /etc/obs/runs),
LIFECYCLE_LOKI_URL (push URL; empty: no lines), LIFECYCLE_STATS_SECS (10),
LIFECYCLE_SETTLE_SECS (20), DOCKER_SOCK. Stdlib only (lib/obslib.py).
"""
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import obslib  # noqa: E402

METRICS_PORT = int(os.environ.get("LIFECYCLE_METRICS_PORT", "9914"))
RUNS_DIR = os.environ.get("LIFECYCLE_RUNS", "/etc/obs/runs")
LOKI_URL = os.environ.get("LIFECYCLE_LOKI_URL", "http://loki:3100/loki/api/v1/push")
STATS_SECS = float(os.environ.get("LIFECYCLE_STATS_SECS", "10"))
SETTLE_SECS = float(os.environ.get("LIFECYCLE_SETTLE_SECS", "20"))
RUNS_SECS = 5.0
RESYNC_SECS = 60.0
DROP_AFTER = 900.0            # a removed container's series stay this long, then go
RUN_KEEP_DAYS = 31            # runs that ended longer ago than this are not exported
LOKI_MAX_AGE_DAYS = 7         # Loki refuses older lines (loki.yml reject_old_samples)

EVENTS = ("create", "start", "restart", "kill", "oom", "stop", "die", "destroy")
LBL = ("net", "run_id", "node", "client", "job")
SIGNALS = {"1": "SIGHUP", "2": "SIGINT", "3": "SIGQUIT", "6": "SIGABRT", "9": "SIGKILL",
           "15": "SIGTERM"}


def log(msg):
    print("%s lifecycle: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def signame(sig):
    return SIGNALS.get(str(sig), "signal %s" % sig) if sig not in (None, "") else ""


# ---- deciding why a container stopped (pure: the tests drive it with plain values) ------
def classify(exit_code, signals, oom, stopped, removed, run_ended):
    """reason: oom, teardown, stopped, killed, crashed or exited (module docstring)."""
    if oom:
        return "oom"
    if stopped:
        return "teardown" if (removed or run_ended) else "stopped"
    if signals:
        return "killed"
    return "exited" if exit_code == 0 else "crashed"


def describe(reason, exit_code, signals, during_run):
    """The sentence a person reads in the dashboard and in the logs."""
    code = "exit code %s" % exit_code
    sig = signame(signals[-1]) if signals else ""
    forced = "; SIGKILL after the stop timed out" if reason in ("teardown", "stopped") and \
        len(signals) > 1 and str(signals[-1]) == "9" else ""
    text = {
        "oom": "OOM-killed (out of memory), %s" % code,
        "teardown": "stopped with its net (teardown), %s%s" % (code, forced),
        "stopped": "stopped by docker stop, %s%s" % (code, forced),
        "killed": "killed by %s (docker kill), %s" % (sig or "a signal", code),
        "crashed": "crashed: exited by itself with %s" % code,
        "exited": "exited by itself, %s" % code,
    }[reason]
    return text + (", while its run was active" if during_run else "")


def run_ended(rec, t):
    """True iff the run record says the run ended by t + SETTLE_SECS."""
    end = (rec or {}).get("end")
    return bool(end) and end <= t + SETTLE_SECS


# ---- containers ---------------------------------------------------------------------------
class Container:
    def __init__(self, cid, name, labels, image=""):
        self.id, self.name, self.image = cid, name, image
        self.labels = {k: v for k, v in obslib.obs_labels(labels, name).items() if k in LBL}
        self.labels["container"] = name
        self.running = False
        self.started_once = False
        self.reset()
        self.pending = None          # (die time, exit code) until the stop is settled
        self.removed_at = None

    def reset(self):
        self.signals, self.oom, self.stop_at = [], False, None


class Tracker:
    """Container state from Docker events; emits Loki lines through `emit(stream, ns,
    line)` and keeps the metrics. `runs(run_id)` returns a run record or None."""

    def __init__(self, metrics, emit, runs):
        self.emit, self.runs = emit, runs
        self.c = {}                  # container id -> Container
        lab = LBL + ("container",)
        self.m_running = metrics.gauge("obs_container_running", "1 while the container runs.", lab)
        self.m_events = metrics.counter("obs_container_events_total", "Docker events of the container.",
                                        lab + ("event",))
        self.m_restarts = metrics.counter("obs_container_restarts_total",
                                          "Starts after the container had stopped (restart policy, "
                                          "docker restart, docker start).", lab)
        self.m_exit = metrics.gauge("obs_container_last_exit_code", "Exit code of the container's last stop.", lab)
        self.m_stopped = metrics.gauge("obs_container_stopped_timestamp_seconds",
                                       "When the container last stopped, and why (reason; during_run: its "
                                       "run was still active). Gone once it starts again.",
                                       lab + ("reason", "during_run"))
        self.m_cpu = metrics.counter("obs_container_cpu_seconds_total", "CPU time the container used.", lab)
        self.m_mem = metrics.gauge("obs_container_memory_bytes",
                                   "Memory the container uses, less its inactive page cache (working set).", lab)
        self.families = (self.m_running, self.m_events, self.m_restarts, self.m_exit, self.m_stopped,
                         self.m_cpu, self.m_mem)

    # -- lines
    def line(self, c, t, event, msg, **extra):
        body = {"event": event, "msg": "%s %s" % (c.labels["node"], msg)}
        body.update({k: v for k, v in extra.items() if v not in (None, "")})
        stream = dict(c.labels, source="lifecycle", kind="container")
        self.emit(stream, int(t * 1e9), json.dumps(body, separators=(",", ":")))

    # -- state from an inspect (at start-up and resync: no lines, no counts)
    def adopt(self, info):
        cid = info["Id"]
        if cid in self.c:
            return self.c[cid]
        cfg = info.get("Config") or {}
        c = Container(cid, obslib.container_name(info), cfg.get("Labels") or {}, cfg.get("Image", ""))
        st = info.get("State") or {}
        c.running = bool(st.get("Running"))
        c.started_once = bool(st.get("StartedAt", "").strip("0-:TZ."))
        self.c[cid] = c
        self.m_running.labels(**c.labels).set(int(c.running))
        if not c.running and c.started_once:
            self.m_exit.labels(**c.labels).set(st.get("ExitCode", 0))
        return c

    def forget_missing(self, live_ids, now):
        """Containers Docker no longer lists (events missed): removed now."""
        for cid, c in list(self.c.items()):
            if cid not in live_ids and c.removed_at is None:
                self.settle(c, now, removed=True)
                c.removed_at = now

    # -- events
    def event(self, ev):
        """One Docker event (a /events entry: its attributes carry the container's labels)."""
        action = (ev.get("Action") or ev.get("status") or "").split(":")[0]
        if action not in EVENTS:
            return
        cid = (ev.get("Actor") or {}).get("ID") or ev.get("id")
        attrs = (ev.get("Actor") or {}).get("Attributes") or {}
        if not obslib.observed(attrs):
            return
        t = ev.get("timeNano", 0) / 1e9 or ev.get("time", 0) or time.time()
        c = self.c.get(cid)
        if c is None:
            c = Container(cid, attrs.get("name", cid[:12]), attrs, attrs.get("image", ""))
            self.c[cid] = c
        self.m_events.labels(event=action, **c.labels).inc()
        getattr(self, "on_" + action)(c, t, attrs)

    def on_create(self, c, t, attrs):
        self.m_running.labels(**c.labels).set(0)
        self.line(c, t, "create", "created (%s)" % (c.image or "image unknown"), image=c.image)

    def on_start(self, c, t, attrs):
        if c.pending:
            self.settle(c, t)
        restart = c.started_once
        c.started_once, c.running = True, True
        c.reset()
        self.m_running.labels(**c.labels).set(1)
        self.m_stopped.remove(**c.labels)
        if restart:
            self.m_restarts.labels(**c.labels).inc()
        self.line(c, t, "start", "restarted" if restart else "started")

    def on_restart(self, c, t, attrs):
        self.line(c, t, "restart", "restarted by docker restart")

    def on_kill(self, c, t, attrs):
        c.signals.append(attrs.get("signal", ""))
        self.line(c, t, "kill", "sent %s" % signame(attrs.get("signal")), signal=signame(attrs.get("signal")))

    def on_oom(self, c, t, attrs):
        c.oom = True
        self.line(c, t, "oom", "ran out of memory")

    def on_stop(self, c, t, attrs):
        c.stop_at = t
        self.line(c, t, "stop", "stop requested (docker stop, compose stop or down)")

    def on_die(self, c, t, attrs):
        try:
            code = int(attrs.get("exitCode", ""))
        except ValueError:
            code = -1
        c.running = False
        c.pending = (t, code)
        self.m_running.labels(**c.labels).set(0)
        self.m_exit.labels(**c.labels).set(code)

    def on_destroy(self, c, t, attrs):
        if c.pending:
            self.settle(c, t, removed=True)
        c.removed_at = t
        self.line(c, t, "destroy", "removed")

    # -- settling a stop, and dropping old containers
    def settle(self, c, now, removed=False):
        if not c.pending:
            return
        t, code = c.pending
        c.pending = None
        stopped = c.stop_at is not None and abs(c.stop_at - t) <= SETTLE_SECS
        ended = run_ended(self.runs(c.labels["run_id"]), t)
        reason = classify(code, c.signals, c.oom, stopped, removed, ended)
        during = reason != "teardown" and not ended
        self.m_stopped.remove(**c.labels)
        self.m_stopped.labels(reason=reason, during_run=str(during).lower(), **c.labels).set(t)
        self.line(c, t, "die", describe(reason, code, c.signals, during), reason=reason, exit_code=code,
                  signal=signame(c.signals[-1]) if c.signals else "", during_run=during)

    def tick(self, now):
        for cid, c in list(self.c.items()):
            if c.pending and now - c.pending[0] >= SETTLE_SECS:
                self.settle(c, now)
            if c.removed_at is not None and now - c.removed_at >= DROP_AFTER:
                del self.c[cid]
                # a container recreated under the same name keeps the series going
                if not any(o.name == c.name for o in self.c.values()):
                    for f in self.families:
                        f.remove(container=c.name)

    # -- resources
    def stats(self, c, s):
        """One Docker stats sample (GET /containers/{id}/stats?stream=false)."""
        cpu = ((s.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")
        if cpu is not None:
            self.m_cpu.labels(**c.labels).set(cpu / 1e9)
        mem = s.get("memory_stats") or {}
        if "usage" in mem:
            st = mem.get("stats") or {}
            inactive = st.get("inactive_file", st.get("total_inactive_file", 0))
            self.m_mem.labels(**c.labels).set(max(0, mem["usage"] - inactive))


# ---- runs --------------------------------------------------------------------------------
class Runs:
    """The run records as metrics, and a Loki line at each run's start and end."""

    def __init__(self, metrics, emit, directory, loki_query=None):
        self.emit, self.dir, self.loki_query = emit, directory, loki_query
        self.recs = {}
        self.announced = set()       # (run_id, event) already in Loki
        self.m_info = metrics.gauge("obs_run_info", "1 per recorded run; link_from and link_to (unix ms, "
                                    "to=now while it runs) are the dashboard range that shows it whole.",
                                    ("net", "run_id", "kind", "link_from", "link_to"))
        self.m_start = metrics.gauge("obs_run_start_timestamp_seconds", "When the run began (obs begin).",
                                     ("net", "run_id"))
        self.m_end = metrics.gauge("obs_run_end_timestamp_seconds", "When the run ended (obs end); absent "
                                   "while it runs.", ("net", "run_id"))

    def get(self, run_id):
        """The record of run_id, read now (a stop is judged against the freshest end)."""
        rec = obslib.read_runs(self.dir).get(run_id) if run_id else None
        return rec or self.recs.get(run_id)

    def sync(self, now):
        self.recs = obslib.read_runs(self.dir)
        info, start, end = {}, {}, {}
        for rid, r in self.recs.items():
            rng = obslib.run_range(r)
            if not rng or (r.get("end") and r["end"] < now - RUN_KEEP_DAYS * 86400):
                continue
            info[(r["net"], rid, r.get("kind") or "", rng[0], rng[1])] = 1
            start[(r["net"], rid)] = r["start"]
            if r.get("end"):
                end[(r["net"], rid)] = r["end"]
            self.announce(r, "run_begin", r["start"], now)
            if r.get("end"):
                self.announce(r, "run_end", r["end"], now)
        self.m_info.set_all(info)
        self.m_start.set_all(start)
        self.m_end.set_all(end)

    def announce(self, r, event, t, now):
        key = (r["run_id"], event)
        if key in self.announced or t < now - LOKI_MAX_AGE_DAYS * 86400:
            return
        stream = {"source": "lifecycle", "kind": "run", "net": r["net"], "run_id": r["run_id"]}
        ns = int(t * 1e9)
        if self.loki_query is not None:
            found = self.loki_query(stream, ns, event)
            if found is None:        # Loki unreachable: ask again next time
                return
            if found:
                self.announced.add(key)
                return
        kind = " (%s)" % r["kind"] if r.get("kind") else ""
        msg = ("run %s began%s" % (r["run_id"], kind) if event == "run_begin"
               else "run %s ended: the net shut down" % r["run_id"])
        self.emit(stream, ns, json.dumps({"event": event, "msg": msg}, separators=(",", ":")))
        self.announced.add(key)


def loki_query_fn(push_url):
    """A function (stream, ns, event) -> True/False (is that line in Loki?), None when Loki
    does not answer: lines are pushed once, whatever restarts this service."""
    base = push_url.split("/loki/api/")[0]

    def query(stream, ns, event):
        sel = ",".join('%s="%s"' % (k, v) for k, v in sorted(stream.items()))
        q = urllib.parse.urlencode({"query": '{%s} |= `"event":"%s"`' % (sel, event),
                                    "start": ns - 1000000000, "end": ns + 1000000000, "limit": 1})
        try:
            with urllib.request.urlopen(base + "/loki/api/v1/query_range?" + q, timeout=5) as r:
                res = json.load(r)["data"]["result"]
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            return None
        return any(s.get("values") for s in res)
    return query


# ---- Docker -------------------------------------------------------------------------------
class Service:
    def __init__(self, docker, metrics, emit, runs_dir, loki_query=None):
        self.docker = docker
        self.lock = threading.Lock()
        self.runs = Runs(metrics, emit, runs_dir, loki_query)
        self.tracker = Tracker(metrics, emit, self.runs.get)
        self.m_errors = metrics.counter("obs_lifecycle_errors_total", "Docker API calls that failed.")
        self.m_containers = metrics.gauge("obs_lifecycle_containers", "Observed containers tracked.")

    def inspect(self, cid):
        try:
            return self.docker.get("/containers/%s/json" % cid)
        except obslib.DockerError:
            return None

    def resync(self, now):
        try:
            cs = self.docker.observed_containers(all=True)
        except (OSError, obslib.DockerError) as e:
            self.m_errors.inc()
            log("resync: %s" % e)
            return
        for c in cs:
            if c["Id"] not in self.tracker.c:
                info = self.inspect(c["Id"])
                if info:
                    with self.lock:
                        self.tracker.adopt(info)
        with self.lock:
            self.tracker.forget_missing({c["Id"] for c in cs}, now)
            self.m_containers.set(sum(1 for c in self.tracker.c.values() if c.removed_at is None))

    def handle(self, ev):
        with self.lock:
            self.tracker.event(ev)

    def poll_stats(self):
        with self.lock:
            running = [c for c in self.tracker.c.values() if c.running]
        for c in running:
            try:
                s = self.docker.get("/containers/%s/stats" % c.id, stream="false", **{"one-shot": "true"})
            except (OSError, obslib.DockerError):
                continue                  # stopped in between: the events say so
            with self.lock:
                self.tracker.stats(c, s or {})


def watch(docker, q):
    """Feed container events into q; reconnect with backoff; None after a gap (resync)."""
    backoff = 1
    while True:
        try:
            for ev in docker.events({"type": ["container"], "event": list(EVENTS)}):
                backoff = 1
                q.put(ev)
        except (OSError, obslib.DockerError) as e:
            log("event stream: %s; retrying in %d s" % (e, backoff))
        time.sleep(backoff)
        backoff = min(30, backoff * 2)
        q.put(None)


def stats_loop(svc):
    while True:
        time.sleep(STATS_SECS)
        try:
            svc.poll_stats()
        except Exception as e:           # noqa: BLE001 - keep polling
            log("stats: %s" % e)


def main():
    metrics = obslib.Metrics()
    dropped = metrics.counter("obs_lifecycle_loki_dropped_total", "Lifecycle lines not delivered to Loki.")
    pusher = obslib.LokiPusher(LOKI_URL, lambda s, n: dropped.inc(n), log=log) if LOKI_URL else None

    def emit(stream, ns, line):
        if pusher:
            pusher.put(tuple(sorted(stream.items())), ns, line)

    svc = Service(obslib.Docker(), metrics, emit, RUNS_DIR, loki_query_fn(LOKI_URL) if LOKI_URL else None)
    obslib.serve_http(metrics, METRICS_PORT)
    q = queue.Queue()
    threading.Thread(target=watch, args=(svc.docker, q), daemon=True).start()
    threading.Thread(target=stats_loop, args=(svc,), daemon=True).start()
    log("runs %s, loki %s, settle %ss" % (RUNS_DIR, LOKI_URL or "off", SETTLE_SECS))
    svc.resync(time.time())
    next_runs = next_resync = 0.0
    while True:
        try:
            ev = q.get(timeout=1.0)
            if ev is None:
                next_resync = 0.0
            else:
                svc.handle(ev)
        except queue.Empty:
            pass
        now = time.time()
        with svc.lock:
            svc.tracker.tick(now)
        if now >= next_runs:
            try:
                svc.runs.sync(now)
            except Exception as e:       # noqa: BLE001 - a bad record must not stop the service
                log("runs: %s" % e)
            next_runs = now + RUNS_SECS
        if now >= next_resync:
            svc.resync(now)
            next_resync = now + RESYNC_SECS


if __name__ == "__main__":
    main()
