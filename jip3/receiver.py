#!/usr/bin/env python3
"""jip3-receiver: a JIP-3 telemetry server for JAM nodes of any client.

A node started with `--telemetry HOST:PORT` connects here over TCP and sends its node
information message, then one message per event (JIP-3, polkadot-fellows/JIPs). For each
connection the receiver:

  * works out which node it is: net, run_id, node, client (the obs label model; rules in
    Labeler below: the container that connected, the node's own address, a registered
    target, or the node's self-description);
  * decodes every event of the spec (codec.py) and turns them into Prometheus metrics on
    /metrics: the standard jam_* series (docs/metrics.md, "From JIP-3"),
    labelled source="jip3", and raw jip3_* counters;
  * ships every event as one JSON log line to Loki, stream labels
    {net, run_id, node, client, source="jip3"}, timestamped with the event's own time.

Environment:
  JIP3_LISTEN          TCP address for nodes (default 0.0.0.0:9910)
  JIP3_METRICS_PORT    HTTP port for /metrics and /connections (default 9911)
  JIP3_LOKI_URL        Loki push URL (default http://loki:3100/loki/api/v1/push; empty: off)
  JIP3_TARGETS         registered native nodes, file_sd JSON (default /etc/obs/targets/jip3/*.json)
  DOCKER_SOCK          Docker socket for mapping a peer to its container (default
                       /var/run/docker.sock; absent: skipped)
  JIP3_MAX_MESSAGE     largest accepted message in bytes (default 1048576)
  JIP3_FORGET_SECS     drop a node's series this long after it disconnects (default 21600)

Stdlib only; lib/obslib.py is imported from ../lib.
"""
import asyncio
import collections
import ipaddress
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
import codec  # noqa: E402
import obslib  # noqa: E402

LBL = ("net", "run_id", "node", "client")
JAM = LBL + ("source",)
SOURCE = "jip3"

# jam_wp_refused_total{reason}: the documented vocabulary, from a failure's free text
REFUSAL_KEYWORDS = [("duplicate", "duplicate"), ("unauthori", "unauthorized"),
                    ("authoriz", "unauthorized"), ("anchor", "anchor"), ("queue", "queue_full"),
                    ("full", "queue_full"), ("capacity", "queue_full"), ("refine", "refine")]

# the guarantor pipeline: event -> the stage it ends (jam_wp_stage_seconds{stage})
STAGES = {94: "receive", 95: "authorize", 101: "refine", 102: "report", 105: "guarantee",
          109: "distribute"}


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg), flush=True)


def refusal_reason(text):
    t = (text or "").lower()
    for kw, reason in REFUSAL_KEYWORDS:
        if kw in t:
            return reason
    return "other"


class Bounded(collections.OrderedDict):
    """An insertion-ordered map that forgets its oldest entries past `cap`: the pending
    event IDs a connection waits on can never grow without limit."""

    def __init__(self, cap):
        super().__init__()
        self.cap = cap

    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        while len(self) > self.cap:
            self.popitem(last=False)


# ---- metrics ---------------------------------------------------------------------------
class JamMetrics:
    def __init__(self, m):
        self.m = m
        sec = (.001, .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120)
        self.node_info = m.gauge(
            "jam_node_info", "Node identity (always 1), from the JIP-3 node information message.",
            ("net", "run_id", "node", "client", "client_version", "gp_version", "spec",
             "chain_id", "validator_index", "peer_id", "source"))
        self.best_slot = m.gauge("jam_best_slot", "Slot of the node's best block.", JAM)
        self.finalized_slot = m.gauge("jam_finalized_slot", "Slot of the latest finalized block.", JAM)
        self.peers = m.gauge("jam_peers", "Connected peers by role (validator, other).", JAM + ("role",))
        self.authored = m.counter("jam_blocks_authored_total", "Blocks this node authored.", JAM)
        self.imported = m.counter("jam_blocks_imported_total",
                                  "Blocks from other authors this node imported and executed.", JAM)
        self.import_seconds = m.histogram("jam_block_import_seconds",
                                          "From the start of an import to its execution.", JAM, sec)
        self.wp_received = m.counter("jam_wp_received_total",
                                     "Work-packages received from builders or primary guarantors.", JAM)
        self.wp_refused = m.counter("jam_wp_refused_total",
                                    "Work-packages refused or failed in the guarantor pipeline.",
                                    JAM + ("reason",))
        self.wp_stage = m.histogram("jam_wp_stage_seconds",
                                    "Time to reach each guarantor pipeline stage from the previous one.",
                                    JAM + ("stage",), sec)
        self.refine_seconds = m.histogram("jam_refine_seconds",
                                          "Wall-clock refine time of one work-package (all items).", JAM, sec)
        self.refine_gas = m.counter("jam_refine_gas_total", "Gas used by refine.", JAM)
        self.assurances = m.counter("jam_assurances_signed_total",
                                    "Availability assurances signed and distributed.", JAM)
        self.tickets_generated = m.counter("jam_tickets_generated_total", "Safrole tickets generated.", JAM)
        self.tickets_transferred = m.counter("jam_tickets_transferred_total",
                                             "Safrole tickets sent (out) or received (in).",
                                             JAM + ("direction",))
        self.events = m.counter("jip3_events_total", "JIP-3 events received, by event name.",
                                LBL + ("event",))
        self.dropped = m.counter("jip3_dropped_events_total",
                                 "Events the node reported dropped (JIP-3 'dropped' events).", LBL)
        self.decode_errors = m.counter("jip3_decode_errors_total",
                                       "Messages that did not decode, by reason.", LBL + ("reason",))
        self.connected = m.gauge("jip3_connected", "Open telemetry connections from the node.", LBL)
        self.connections = m.counter("jip3_connections_total", "Telemetry connections accepted.", LBL)
        self.last_event = m.gauge("jip3_last_event_timestamp_seconds",
                                  "Unix time of the node's latest event (its own clock).", LBL)
        self.loki_dropped = m.counter("jip3_loki_dropped_total",
                                      "Event log lines not delivered to Loki (queue full or push failed).",
                                      LBL)

    def forget(self, labels):
        """Remove every series of one node (net, run_id, node)."""
        key = {k: labels[k] for k in ("net", "run_id", "node")}
        for f in self.m.families.values():
            if all(k in f.labelnames for k in key):
                f.remove(**key)


# ---- labels ----------------------------------------------------------------------------
def _norm_ip(ip):
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return ""
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(a)


def _specific(ip):
    """An address that names one host (not ::, 0.0.0.0, a loopback)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_unspecified or a.is_loopback)


class Labeler:
    """Which node a connection is, as {net, run_id, node, client}, by the first rule that
    applies:

      container  the TCP peer's IP is a running container's (Docker socket): its
                 org.abutlabs.obs.* labels (obslib.obs_labels);
      address    else the node's own address (node info Peer Address) is a container's;
      target     else a registered target (JIP3_TARGETS, `obs register ... --jip3`) whose
                 host:port is the node's JAMNP-S address (the port, and the IP when both
                 name one host);
      self       else the node's self-description: net "jip3", node
                 "<implementation>-<first 4 bytes of its peer id>".
    """

    def __init__(self, docker_sock=None, targets_glob=None):
        self.docker = None
        if docker_sock and os.path.exists(docker_sock):
            self.docker = obslib.Docker(docker_sock)
        self.targets_glob = targets_glob

    def _container(self, ip):
        if not self.docker or not _specific(ip):
            return None
        try:
            c = self.docker.container_by_ip(ip)
        except (OSError, obslib.DockerError) as e:
            log("docker lookup of %s failed: %s" % (ip, e))
            return None
        if not c:
            return None
        lb = obslib.obs_labels(obslib.container_labels(c), obslib.container_name(c))
        return {k: lb[k] for k in LBL}

    def _target(self, info):
        if not self.targets_glob or not info:
            return None
        ip, port = info["peer_address"]["ip"], info["peer_address"]["port"]
        for addr, labels in obslib.read_targets(self.targets_glob):
            host, _, p = addr.rpartition(":")
            host = host.strip("[]")
            if not p.isdigit() or int(p) != port:
                continue
            try:
                ipaddress.ip_address(host)
                host_is_ip = True
            except ValueError:
                host_is_ip = False
            if host_is_ip and _specific(ip) and _specific(host) and _norm_ip(host) != _norm_ip(ip):
                continue
            net = labels.get("net") or "jip3"
            job = labels.get("job") or ""
            return {"net": net, "run_id": labels.get("run_id") or "%s-adhoc" % net,
                    "node": labels.get("node") or host,
                    "client": labels.get("client") or job or _impl(info)}
        return None

    def resolve(self, tcp_ip, info):
        """(labels, rule) for a connection from tcp_ip whose node info is `info` (a dict
        from codec.decode_node_info, or None when it did not decode)."""
        lb = self._container(_norm_ip(tcp_ip))
        if lb:
            return lb, "container"
        if info:
            lb = self._container(_norm_ip(info["peer_address"]["ip"]))
            if lb:
                return lb, "address"
            lb = self._target(info)
            if lb:
                return lb, "target"
        impl = _impl(info)
        peer = info["peer_id"][:4].hex() if info else "00000000"
        return {"net": "jip3", "run_id": "jip3-adhoc", "node": "%s-%s" % (impl, peer),
                "client": impl}, "self"


def _impl(info):
    name = (info or {}).get("impl_name") or "unknown"
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name.lower()) or "unknown"


# ---- one connection --------------------------------------------------------------------
class Connection:
    """The state of one node's telemetry stream: its labels, the next implicit event ID,
    and the bounded maps of events still waiting for their follow-up."""

    def __init__(self, rx, tcp_ip):
        self.rx, self.tcp_ip = rx, tcp_ip
        self.labels = None
        self.info = None
        self.rule = None
        self.cores = None
        self.next_id = 0
        self.events = 0
        self.errors_logged = 0
        cap = rx.max_pending
        self.authoring = Bounded(cap)       # "authoring" id -> ts
        self.importing = Bounded(cap)       # "importing" id -> ts
        self.packages = Bounded(cap)        # submission/sharing id -> {"prev": ts, "guarantee": ts}

    # labels used on every series of this node
    def _l(self, **extra):
        d = dict(self.labels)
        d.update(extra)
        return d

    def _j(self, **extra):
        return self._l(source=SOURCE, **extra)

    def error(self, reason, detail=""):
        self.rx.jm.decode_errors.labels(**self._l(reason=reason)).inc()
        if self.errors_logged < 5:
            self.errors_logged += 1
            log("%s: %s %s" % (self.labels["node"], reason, detail))

    def node_info(self, content, labels, rule, info=None, error=None):
        """Called once, with the resolved labels (decode happens before, in the receiver)."""
        self.labels, self.rule, self.info = labels, rule, info
        jm = self.rx.jm
        jm.connections.labels(**self._l()).inc()
        self.rx.connected(self, +1)
        if error:
            self.error("node_info", error)
            return
        self.cores = info["cores"]
        jm.node_info.remove(net=labels["net"], run_id=labels["run_id"], node=labels["node"])
        jm.node_info.labels(
            net=labels["net"], run_id=labels["run_id"], node=labels["node"], client=labels["client"],
            client_version=info["impl_version"], gp_version=info["gp_version"],
            spec=codec.SPEC_BY_CORES.get(info["cores"], "") if info["cores"] is not None else "",
            chain_id=info["genesis"][:8].hex(), validator_index="", peer_id=info["peer_id"].hex(),
            source=SOURCE).set(1)
        line = {"event": "node_info", "version": info["version"], "impl_name": info["impl_name"],
                "impl_version": info["impl_version"], "gp_version": info["gp_version"],
                "params_len": info["params_len"], "params_layout": info["params_layout"],
                "cores": info["cores"], "genesis": info["genesis"].hex()[:16],
                "peer_id": info["peer_id"].hex()[:16], "peer_address": info["peer_address"],
                "flags": info["flags"], "note": info["note"], "label_rule": rule}
        self.rx.ship(self.labels, time.time_ns(), line)

    def message(self, content):
        """One event message (after the node info)."""
        eid = self.next_id
        self.next_id += 1
        jm = self.rx.jm
        if not content:
            self.error("empty")
            return None
        try:
            ev = codec.decode_event(content, self.cores)
        except codec.UnknownEvent as e:
            jm.events.labels(**self._l(event="unknown_%d" % e.disc)).inc()
            return None
        except codec.DecodeError as e:
            self.error("decode", str(e))
            return None
        self.events += 1
        for w in ev.warnings:
            self.error("cores", w)
        jm.events.labels(**self._l(event=ev.name)).inc()
        jm.last_event.labels(**self._l()).set(codec.unix_from_jce_us(ev.ts))
        if ev.disc == 0:
            # the event after a "dropped" event E has the ID of E plus the number dropped
            n = ev.fields["count"]
            jm.dropped.labels(**self._l()).inc(n)
            self.next_id = eid + n
        try:
            self.derive(eid, ev)
        except (KeyError, TypeError, ValueError) as e:     # a field we could not use
            self.error("derive", "%s: %s" % (ev.name, e))
        line = {"event": ev.name, "id": eid, "ts": codec.iso(ev.ts)}
        line.update(codec.to_json(ev.fields))
        self.rx.ship(self.labels, codec.unix_ns_from_jce_us(ev.ts), line)
        return ev

    def _stage(self, ref, ts, stage):
        p = self.packages.get(ref)
        if p is None:
            return
        if ts >= p["prev"]:
            self.rx.jm.wp_stage.labels(**self._j(stage=stage)).observe((ts - p["prev"]) / 1e6)
        p["prev"] = ts

    def derive(self, eid, ev):
        """The jam_* series this event feeds."""
        jm, f, d, ts = self.rx.jm, ev.fields, ev.disc, ev.ts
        if d == 10:
            validators = f["validator_peers"]
            jm.peers.labels(**self._j(role="validator")).set(validators)
            jm.peers.labels(**self._j(role="other")).set(max(0, f["peers"] - validators))
        elif d == 11:
            jm.best_slot.labels(**self._j()).set(f["slot"])
        elif d == 12:
            jm.finalized_slot.labels(**self._j()).set(f["slot"])
        elif d == 40:
            self.authoring.put(eid, ts)
        elif d == 41:
            self.authoring.pop(f["authoring"], None)
        elif d == 42:
            jm.authored.labels(**self._j()).inc()
        elif d == 43:
            self.importing.put(eid, ts)
        elif d == 44:
            self.importing.pop(f["importing"], None)
        elif d == 46:
            self.importing.pop(f["block"], None)
            self.authoring.pop(f["block"], None)
        elif d == 47:
            start = self.importing.pop(f["block"], None)
            if start is not None:
                jm.imported.labels(**self._j()).inc()
                if ts >= start:
                    jm.import_seconds.labels(**self._j()).observe((ts - start) / 1e6)
            self.authoring.pop(f["block"], None)
        elif d in (90, 91):
            self.packages.put(eid, {"prev": ts, "guarantee": None})
        elif d == 92:
            jm.wp_refused.labels(**self._j(reason=refusal_reason(f["reason"]))).inc()
            self.packages.pop(f["package"], None)
        elif d == 93:
            jm.wp_refused.labels(**self._j(reason="duplicate")).inc()
        elif d == 94:
            jm.wp_received.labels(**self._j()).inc()
            self._stage(f["package"], ts, STAGES[d])
        elif d in (95, 102):
            self._stage(f["package"], ts, STAGES[d])
        elif d == 101:
            costs = f["costs"]
            jm.refine_seconds.labels(**self._j()).observe(sum(c["total"]["ns"] for c in costs) / 1e9)
            jm.refine_gas.labels(**self._j()).inc(sum(c["total"]["gas"] for c in costs))
            self._stage(f["package"], ts, STAGES[d])
        elif d == 105:
            p = self.packages.get(f["package"])
            if p is not None and p["guarantee"] is None:     # a second 105 (all 3 sigs) is not a stage
                self._stage(f["package"], ts, STAGES[d])
                p["guarantee"] = ts
        elif d == 109:
            self._stage(f["package"], ts, STAGES[d])
            self.packages.pop(f["package"], None)
        elif d == 103:
            self.packages.pop(f["package"], None)
        elif d == 126:
            jm.assurances.labels(**self._j()).inc()
        elif d == 82:
            jm.tickets_generated.labels(**self._j()).inc(len(f["vrf_outputs"]))
        elif d == 84:
            # Connection Side (Sender): local = we sent it, remote = the peer did
            direction = "out" if f["sender"] == "local" else "in"
            jm.tickets_transferred.labels(**self._j(direction=direction)).inc()

    def closed(self):
        if self.labels:
            self.rx.connected(self, -1)
            self.rx.ship(self.labels, time.time_ns(),
                         {"event": "connection_closed", "events": self.events,
                          "next_id": self.next_id})


# ---- the server ------------------------------------------------------------------------
class Receiver:
    def __init__(self, metrics=None, loki_url=None, labeler=None, max_message=1 << 20,
                 max_pending=4096, forget_secs=21600, loki_interval=1.0):
        self.metrics = metrics or obslib.Metrics()
        self.jm = JamMetrics(self.metrics)
        self.labeler = labeler or Labeler()
        self.max_message, self.max_pending, self.forget_secs = max_message, max_pending, forget_secs
        self.loki = (obslib.LokiPusher(loki_url, self._loki_dropped, loki_interval, log=log)
                     if loki_url else None)
        self.active = {}                    # Connection -> labels
        self.open_count = collections.Counter()   # label key -> open connections
        self.gone = {}                      # label key -> unix time the last one closed

    @staticmethod
    def _key(labels):
        return tuple(labels[k] for k in LBL)

    def _loki_dropped(self, stream, n):
        self.jm.loki_dropped.labels(**{k: stream.get(k, "") for k in LBL}).inc(n)

    def ship(self, labels, ns, line):
        if self.loki:
            stream = tuple(sorted(dict(labels, source=SOURCE).items()))
            self.loki.put(stream, ns, json.dumps(line, separators=(",", ":"), default=str))

    def connected(self, conn, delta):
        key = self._key(conn.labels)
        self.open_count[key] += delta
        if delta > 0:
            self.active[conn] = conn.labels
            self.gone.pop(key, None)
        else:
            self.active.pop(conn, None)
            if self.open_count[key] <= 0:
                del self.open_count[key]
                self.gone[key] = time.time()
        self.jm.connected.labels(**conn.labels).set(self.open_count.get(key, 0))

    def sweep(self, now=None):
        """Forget the series of nodes gone for longer than forget_secs."""
        now = now or time.time()
        for key, t in list(self.gone.items()):
            if now - t > self.forget_secs:
                self.jm.forget(dict(zip(LBL, key)))
                del self.gone[key]

    def connections(self):
        return [{"labels": c.labels, "rule": c.rule, "tcp_peer": c.tcp_ip, "events": c.events,
                 "impl": (c.info or {}).get("impl_name"), "impl_version": (c.info or {}).get("impl_version"),
                 "gp_version": (c.info or {}).get("gp_version"),
                 "params_layout": (c.info or {}).get("params_layout"), "cores": c.cores}
                for c in list(self.active)]

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("", 0)
        conn = Connection(self, _norm_ip(peer[0]))
        first = True
        try:
            while True:
                n = int.from_bytes(await reader.readexactly(4), "little")
                if n > self.max_message:
                    if conn.labels:
                        conn.error("oversize", "%d bytes > %d: closing" % (n, self.max_message))
                    else:
                        log("%s: %d-byte first message > %d: closing" % (conn.tcp_ip, n, self.max_message))
                    break
                content = await reader.readexactly(n) if n else b""
                if first:
                    first = False
                    info, err = None, None
                    try:
                        info = codec.decode_node_info(content)
                    except codec.DecodeError as e:
                        err = str(e)
                    labels, rule = await asyncio.to_thread(self.labeler.resolve, conn.tcp_ip, info)
                    conn.node_info(content, labels, rule, info, err)
                    if info:
                        log("%s connected from %s (rule %s): %s %s, GP %s, params %s/%d bytes, cores %s, "
                            "protocol %d" % (labels["node"], conn.tcp_ip, rule, info["impl_name"],
                                             info["impl_version"], info["gp_version"],
                                             info["params_layout"], info["params_len"], info["cores"],
                                             info["version"]))
                    else:
                        log("%s connected from %s (rule %s): node info did not decode: %s"
                            % (labels["node"], conn.tcp_ip, rule, err))
                    continue
                conn.message(content)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if conn.labels:
                log("%s disconnected after %d events" % (conn.labels["node"], conn.events))
            conn.closed()
            writer.close()

    async def serve(self, host, port):
        return await asyncio.start_server(self.handle, host, port)


async def _sweeper(rx, every=60):
    while True:
        await asyncio.sleep(every)
        rx.sweep()


def main():
    host, _, port = os.environ.get("JIP3_LISTEN", "0.0.0.0:9910").rpartition(":")
    metrics_port = int(os.environ.get("JIP3_METRICS_PORT", "9911"))
    loki = os.environ.get("JIP3_LOKI_URL", "http://loki:3100/loki/api/v1/push")
    rx = Receiver(loki_url=loki or None,
                  labeler=Labeler(os.environ.get("DOCKER_SOCK", "/var/run/docker.sock"),
                                  os.environ.get("JIP3_TARGETS", "/etc/obs/targets/jip3/*.json")),
                  max_message=int(os.environ.get("JIP3_MAX_MESSAGE", str(1 << 20))),
                  forget_secs=float(os.environ.get("JIP3_FORGET_SECS", "21600")))
    obslib.serve_http(rx.metrics, metrics_port, routes={
        "/connections": lambda: ("application/json", json.dumps(rx.connections(), indent=1) + "\n")})

    async def run():
        srv = await rx.serve(host or "0.0.0.0", int(port))
        log("jip3-receiver: JIP-3 on %s:%s, /metrics on :%d, Loki %s, Docker labels %s"
            % (host or "0.0.0.0", port, metrics_port, loki or "off",
               "on" if rx.labeler.docker else "off"))
        asyncio.get_running_loop().create_task(_sweeper(rx))
        async with srv:
            await srv.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        if rx.loki:
            rx.loki.stop()


if __name__ == "__main__":
    main()
