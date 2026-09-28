"""obslib: the helpers the obs services share (netjoin, jip2-exporter, jip3-receiver,
lifecycle) and the obs CLI.

Stdlib only, so every service runs on a plain python:3.12-alpine image with this
directory mounted next to its own code.

  Docker             the Docker Engine API over its unix socket
  observed(labels)   is a container opted in to obs (any org.abutlabs.obs.* label)?
  obs_labels(...)    a container's net, run_id, node, client, job: its labels, with the
                     same defaults Grafana Alloy applies (alloy/config.alloy)
  Metrics            a minimal Prometheus registry and its text exposition
  serve_http(...)    /metrics (and any extra routes) on a background thread
  read_targets(...)  file_sd JSON target files (what `obs register` writes)
  LokiPusher         batches log lines to Loki's push API
  read_runs(...)     the run records `obs begin` and `obs end` write
  run_range(rec)     the dashboard range that shows a whole run

The label model is the contract every ingestion path follows (README.md, "Labels"):
every series and every log line carries net, run_id, node and client.
"""
import collections
import glob
import http.client
import http.server
import json
import math
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PREFIX = "org.abutlabs.obs."
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"


# ---- labels ------------------------------------------------------------------------
def observed(labels):
    """True iff a container opted in to obs: it carries at least one org.abutlabs.obs.*
    label (and has not set org.abutlabs.obs.enable=false)."""
    labels = labels or {}
    if labels.get(PREFIX + "enable", "").lower() == "false":
        return False
    return any(k.startswith(PREFIX) for k in labels)


def obs_labels(labels, name=""):
    """{net, run_id, node, client, job} of a container, from its labels.

    The defaults match alloy/config.alloy exactly, so a metric Alloy scrapes, a log line
    it ships and a series the JIP-2 exporter or JIP-3 receiver derives for the same
    container carry the same labels:

      net     org.abutlabs.obs.net, else the compose project, else "docker"
      node    org.abutlabs.obs.node, else the compose service, else the container name
      job     org.abutlabs.obs.job, else the compose service, else the container name
      client  org.abutlabs.obs.client, else the job
      run_id  org.abutlabs.obs.run_id, else "<net>-adhoc"
    """
    labels = labels or {}
    name = (name or "").lstrip("/")

    def get(key):
        return labels.get(PREFIX + key) or ""

    service = labels.get(COMPOSE_SERVICE) or ""
    net = get("net") or labels.get(COMPOSE_PROJECT) or "docker"
    job = get("job") or service or name
    return {"net": net,
            "run_id": get("run_id") or "%s-adhoc" % net,
            "node": get("node") or service or name,
            "client": get("client") or job,
            "job": job}


def container_name(c):
    """A container's name from a /containers/json entry or an inspect result."""
    if c.get("Names"):
        return c["Names"][0].lstrip("/")
    return (c.get("Name") or "").lstrip("/")


def container_labels(c):
    return c.get("Labels") or (c.get("Config") or {}).get("Labels") or {}


def container_networks(c):
    """{network name: IPv4 address ('' before the container starts)}."""
    nets = ((c.get("NetworkSettings") or {}).get("Networks")) or {}
    return {n: (v or {}).get("IPAddress") or "" for n, v in nets.items()}


# ---- Docker Engine API -------------------------------------------------------------
class DockerError(Exception):
    def __init__(self, status, body):
        super().__init__("docker API %s: %s" % (status, body[:300]))
        self.status, self.body = status, body


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


class Docker:
    """The Docker Engine API on a unix socket (default /var/run/docker.sock)."""

    def __init__(self, sock=None):
        self.sock = sock or os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")

    def request(self, method, path, body=None, query=None, timeout=30):
        if query:
            path += "?" + urllib.parse.urlencode(
                {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in query.items()})
        conn = _UnixConnection(self.sock, timeout)
        try:
            conn.request(method, path, body=None if body is None else json.dumps(body),
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read().decode("utf-8", "replace")
        finally:
            conn.close()
        if r.status >= 400:
            raise DockerError(r.status, raw)
        return json.loads(raw) if raw.strip() else None

    def get(self, path, **query):
        return self.request("GET", path, query=query or None)

    def post(self, path, body=None, **query):
        return self.request("POST", path, body=body, query=query or None)

    def containers(self, all=False, filters=None):
        q = {"all": "true" if all else "false"}
        if filters:
            q["filters"] = filters
        return self.get("/containers/json", **q) or []

    def events(self, filters=None, timeout=None):
        """Yield event dicts as they happen (blocks). Returns when the stream ends."""
        path = "/events"
        if filters:
            path += "?" + urllib.parse.urlencode({"filters": json.dumps(filters)})
        conn = _UnixConnection(self.sock, timeout)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            if r.status >= 400:
                raise DockerError(r.status, r.read().decode("utf-8", "replace"))
            while True:
                line = r.readline()
                if not line:
                    return
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        finally:
            conn.close()

    def observed_containers(self, all=False):
        """Every container opted in to obs, as /containers/json entries."""
        return [c for c in self.containers(all=all) if observed(c.get("Labels"))]

    def container_by_ip(self, ip):
        """The running container that holds `ip` on one of its networks, or None."""
        if not ip:
            return None
        for c in self.containers():
            if ip in container_networks(c).values():
                return c
        return None


# ---- Prometheus exposition ---------------------------------------------------------
def _escape(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt(v):
    if v == math.inf:
        return "+Inf"
    if v == -math.inf:
        return "-Inf"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(float(v)) if isinstance(v, float) else str(v)


class _Child:
    def __init__(self, family):
        self.family, self.value = family, 0.0
        if family.kind == "histogram":
            self.counts = [0] * len(family.buckets)
            self.sum, self.count = 0.0, 0

    def inc(self, v=1.0):
        with self.family.lock:
            self.value += v

    def set(self, v):
        with self.family.lock:
            self.value = float(v)

    def observe(self, v):
        with self.family.lock:
            for i, b in enumerate(self.family.buckets):
                if v <= b:
                    self.counts[i] += 1
            self.sum += v
            self.count += 1


class Family:
    def __init__(self, name, help_, kind, labelnames, buckets=None):
        self.name, self.help, self.kind = name, help_, kind
        self.labelnames = tuple(labelnames)
        self.buckets = tuple(sorted(buckets or ())) + ((math.inf,) if kind == "histogram" else ())
        self.children = {}
        self.lock = threading.Lock()

    def labels(self, **kv):
        key = tuple(str(kv.get(n, "")) for n in self.labelnames)
        with self.lock:
            ch = self.children.get(key)
            if ch is None:
                ch = self.children[key] = _Child(self)
        return ch

    # a family without labels is its own single child
    def inc(self, v=1.0):
        self.labels().inc(v)

    def set(self, v):
        self.labels().set(v)

    def observe(self, v):
        self.labels().observe(v)

    def remove(self, **kv):
        """Drop every child whose labels match all of kv (a node gone, a net down)."""
        with self.lock:
            for key in [k for k in self.children
                        if all(k[self.labelnames.index(n)] == str(v) for n, v in kv.items())]:
                del self.children[key]

    def clear(self):
        with self.lock:
            self.children.clear()

    def set_all(self, rows):
        """Replace every child at once: rows = {label tuple (in labelnames order): value}.
        A scrape sees the old set or the new one, never a gap between them."""
        new = {}
        for key, v in rows.items():
            ch = _Child(self)
            ch.value = float(v)
            new[tuple(str(x) for x in key)] = ch
        with self.lock:
            self.children = new

    def render(self):
        out = ["# HELP %s %s" % (self.name, self.help), "# TYPE %s %s" % (self.name, self.kind)]
        with self.lock:
            items = sorted(self.children.items())
            for key, ch in items:
                base = ",".join('%s="%s"' % (n, _escape(v)) for n, v in zip(self.labelnames, key))
                if self.kind != "histogram":
                    out.append("%s{%s} %s" % (self.name, base, _fmt(ch.value)) if base
                               else "%s %s" % (self.name, _fmt(ch.value)))
                    continue
                sep = "," if base else ""
                for b, n in zip(self.buckets, ch.counts):
                    out.append('%s_bucket{%s%sle="%s"} %d' % (self.name, base, sep, _fmt(b), n))
                lbl = "{%s}" % base if base else ""
                out.append("%s_sum%s %s" % (self.name, lbl, _fmt(ch.sum)))
                out.append("%s_count%s %d" % (self.name, lbl, ch.count))
        return "\n".join(out)


class Metrics:
    """A registry: families by name, rendered in the Prometheus text format."""

    def __init__(self):
        self.families = {}

    def _family(self, name, help_, kind, labelnames, buckets=None):
        f = self.families.get(name)
        if f is None:
            f = self.families[name] = Family(name, help_, kind, labelnames, buckets)
        return f

    def counter(self, name, help_, labelnames=()):
        return self._family(name, help_, "counter", labelnames)

    def gauge(self, name, help_, labelnames=()):
        return self._family(name, help_, "gauge", labelnames)

    def histogram(self, name, help_, labelnames=(), buckets=(.005, .01, .025, .05, .1, .25, .5,
                                                            1, 2.5, 5, 10, 25, 60)):
        return self._family(name, help_, "histogram", labelnames, buckets)

    def render(self):
        return "\n".join(f.render() for _, f in sorted(self.families.items())) + "\n"


def serve_http(metrics, port, host="0.0.0.0", routes=None):
    """Serve GET /metrics (and routes: {path: fn() -> (content type, body str)}) on a
    daemon thread; returns the server."""
    routes = dict(routes or {})
    routes.setdefault("/metrics", lambda: ("text/plain; version=0.0.4", metrics.render()))
    routes.setdefault("/-/ready", lambda: ("text/plain", "ready\n"))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            fn = routes.get(self.path.split("?")[0])
            if not fn:
                self.send_error(404)
                return
            ctype, body = fn()
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- file_sd targets ---------------------------------------------------------------
def read_targets(pattern):
    """[(address, labels)] from every file_sd JSON file matching `pattern` (the format
    `obs register` writes: [{"targets": [...], "labels": {...}}, ...]). Unreadable or
    half-written files are skipped; the next read picks them up."""
    out = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as fh:
                groups = json.load(fh)
        except (OSError, ValueError):
            continue
        for g in groups if isinstance(groups, list) else []:
            for t in g.get("targets") or []:
                out.append((t, dict(g.get("labels") or {})))
    return out


# ---- Loki ------------------------------------------------------------------------
class LokiPusher:
    """Batches log lines and pushes them to Loki's JSON push API about every `interval`
    seconds. The queue is bounded: past `maxsize` lines are dropped and counted through
    on_drop(stream labels, n)."""

    def __init__(self, url, on_drop, interval=1.0, maxsize=20000, batch=5000, log=None):
        self.url, self.on_drop, self.interval, self.batch = url, on_drop, interval, batch
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.q = queue.Queue(maxsize)
        self.pushed = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def put(self, stream, ns, line):
        try:
            self.q.put_nowait((stream, ns, line))
        except queue.Full:
            self.on_drop(dict(stream), 1)

    def flush(self):
        """Push everything queued now (the background thread does this every interval)."""
        with self._lock:
            while True:
                items = []
                while len(items) < self.batch:
                    try:
                        items.append(self.q.get_nowait())
                    except queue.Empty:
                        break
                if not items:
                    return
                self._push(items)

    def _push(self, items):
        streams = collections.OrderedDict()
        for stream, ns, line in items:
            streams.setdefault(stream, []).append([str(ns), line])
        body = {"streams": [{"stream": dict(s), "values": v} for s, v in streams.items()]}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            self.pushed += len(items)
        except (OSError, urllib.error.URLError) as e:
            self.log("loki push failed (%d lines): %s" % (len(items), e))
            for s, v in streams.items():
                self.on_drop(dict(s), len(v))

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.flush()
            except Exception as e:             # noqa: BLE001 - the pusher must not die
                self.log("loki pusher: %s" % e)

    def stop(self):
        self._stop.set()
        self.flush()


# ---- runs --------------------------------------------------------------------------
RUN_MARGIN = 60           # seconds shown before a run's start and after its end


def read_runs(directory):
    """{run_id: record} of every run record in `directory` (what `obs begin`, `obs end`
    and `obs register` write: run_id, net, kind, start, end, meta). Unreadable or
    half-written files are skipped; the next read picks them up."""
    out = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("run_id") and rec.get("net"):
            out[rec["run_id"]] = rec
    return out


def run_range(rec, margin=RUN_MARGIN):
    """(from, to) of the dashboard range that shows a whole run, in unix ms, with `margin`
    seconds either side; to is "now" while the run is running. None without a start."""
    if not rec or not rec.get("start"):
        return None
    frm = int(rec["start"] * 1000) - margin * 1000
    to = int(rec["end"] * 1000) + margin * 1000 if rec.get("end") else "now"
    return frm, to
