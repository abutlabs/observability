#!/usr/bin/env python3
"""jip2-exporter: every JAM node's chain view over JIP-2, as Prometheus metrics.

For clients with no /metrics of their own (and as a cross-check of those that have one),
it polls each node's JIP-2 RPC (bestBlock, finalizedBlock, syncState, parameters) and
exports the standard jam_* series (docs/metrics.md) plus the
collector's view of the net: how far each node lags, whether the nodes hold one block at
the common slot, whether finality ever conflicts.

    python3 jip2/exporter.py                       # the service (discovery, /metrics)
    python3 jip2/exporter.py --once --node pj0,polkajam,ws://127.0.0.1:19800
                                                   # one round, print the metrics, exit

Nodes are discovered, and re-discovered every JIP2_DISCOVERY seconds:

  Docker   running containers labelled org.abutlabs.obs.jip2=<port | ws:// URL>; a port
           means ws://<container name>:<port> (netjoin attaches this exporter to the
           container's networks). net, run_id, node, client come from the container's
           org.abutlabs.obs.* labels (lib/obslib.py obs_labels).
  files    file_sd JSON (JIP2_TARGETS, default /etc/obs/targets/jip2/*.json, what
           `obs register --jip2` writes): target host:port or a ws:// URL, labels net,
           run_id, node, client. For native nodes: host.docker.internal:<port>.
  --node   NAME,CLIENT,URL (repeatable; or JIP2_NODES, specs separated by spaces or ';'),
           labelled net=JIP2_NET (default "static").

Every round (JIP2_INTERVAL, default 3 s) each node is polled on its own persistent
WebSocket, concurrently; a node that does not answer within JIP2_TIMEOUT is down for the
round and a slow node never delays the others. A node that fails on the transport backs
off (1 s doubling to 10 s). Then, per net (net, run_id), the heads are lined up:
each node's newest block at or below the lowest best slot (the
common slot), found by walking parent(), is compared by hash; likewise for finalized
blocks.

Env: JIP2_METRICS_PORT (9912), JIP2_INTERVAL (3), JIP2_TIMEOUT (2), JIP2_MAX_LAG (3),
JIP2_DISCOVERY (5), JIP2_TARGETS, JIP2_NODES, JIP2_NET, DOCKER_SOCK
(/var/run/docker.sock; discovery through Docker is skipped when it is absent).
Stdlib only.
"""
import argparse
import collections
import concurrent.futures
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
sys.path.insert(0, HERE)
import obslib  # noqa: E402
import rpc  # noqa: E402

LABEL = obslib.PREFIX + "jip2"
SOURCE = "jip2"
NODE_LABELS = ("net", "run_id", "node", "client")
SPEC_BY_CORES = {2: "tiny", 341: "full"}
MAX_BACKOFF = 10.0
FINAL_MEMORY = 4096            # finalized slots remembered per net, for conflicts


def log(msg):
    print("jip2-exporter: %s" % msg, file=sys.stderr, flush=True)


# ---- targets -----------------------------------------------------------------------
class Target(tuple):
    """(url, labels): labels = {net, run_id, node, client}. Hashable identity."""
    __slots__ = ()

    def __new__(cls, url, labels):
        return tuple.__new__(cls, (url, tuple(labels[k] for k in NODE_LABELS)))

    @property
    def url(self):
        return self[0]

    @property
    def labels(self):
        return dict(zip(NODE_LABELS, self[1]))


def endpoint_url(value, host=None):
    """A JIP-2 WebSocket URL from a label or target value: a ws:// or wss:// URL as it is,
    host:port as ws://host:port, a bare port as ws://<host>:<port>; None otherwise."""
    v = (value or "").strip()
    if re.fullmatch(r"wss?://\S+", v):
        return v
    if re.fullmatch(r"\d{1,5}", v):
        return "ws://%s:%s" % (host, v) if host else None
    if re.fullmatch(r"[A-Za-z0-9_.\-]+:\d{1,5}|\[[0-9A-Fa-f:.]+\]:\d{1,5}", v):
        return "ws://" + v
    return None


def docker_targets(containers):
    """Targets from /containers/json entries labelled org.abutlabs.obs.jip2."""
    out = []
    for c in containers:
        labels = c.get("Labels") or {}
        if LABEL not in labels or not obslib.observed(labels):
            continue
        name = obslib.container_name(c)
        url = endpoint_url(labels[LABEL], name)
        if not url:
            log("%s: bad %s label %r (want a port or a ws:// URL)" % (name, LABEL, labels[LABEL]))
            continue
        lb = obslib.obs_labels(labels, name)
        # a proxy serving another node's RPC (lasair-reader in front of a lasair node)
        # names the node it describes: org.abutlabs.obs.jip2.node / .jip2.client
        for k in ("node", "client"):
            if labels.get(LABEL + "." + k):
                lb[k] = labels[LABEL + "." + k]
        out.append(Target(url, {k: lb[k] for k in NODE_LABELS}))
    return out


def file_targets(pattern):
    """Targets from file_sd JSON files (what `obs register --jip2` writes)."""
    out = []
    for addr, lb in obslib.read_targets(pattern):
        url = endpoint_url(addr)
        if not url:
            continue
        net = lb.get("net") or "file"
        node = lb.get("node") or re.sub(r"^wss?://", "", addr).split("/")[0]
        out.append(Target(url, {"net": net, "run_id": lb.get("run_id") or "%s-adhoc" % net,
                                "node": node,
                                "client": lb.get("client") or lb.get("job") or "unknown"}))
    return out


def static_targets(specs, net):
    """Targets from NAME,CLIENT,URL specs."""
    out = []
    for spec in specs:
        parts = [p.strip() for p in spec.split(",")]
        if len(parts) != 3 or not endpoint_url(parts[2]):
            raise ValueError("bad node %r: want NAME,CLIENT,URL" % spec)
        out.append(Target(endpoint_url(parts[2]), {"net": net, "run_id": "%s-adhoc" % net,
                                                   "node": parts[0], "client": parts[1]}))
    return out


# ---- one node ----------------------------------------------------------------------
def _block(d):
    """A JIP-2 Block Descriptor as {"slot": int, "hash": hex}."""
    return {"slot": int(d["slot"]), "hash": rpc.unb64(d["header_hash"]).hex()}


def hash48(h):
    """The first 48 bits of a hex header hash, as an integer (exact in a float64)."""
    return int(h[:12], 16) if h else None


class Node:
    """One node's connection, what it last said, and its backoff."""

    def __init__(self, target, timeout, metrics):
        self.target, self.labels = target, target.labels
        self.client = rpc.Jip2Client(target.url, timeout)
        self.metrics = metrics
        self.obs = None                 # the latest completed observation
        self.params, self.params_gen = None, -1
        self.unsupported = set()        # methods the node answered "method not found"
        self.failures, self.next_try = 0, 0.0
        self.busy = None                # the in-flight poll (a Future)

    def _error(self, method):
        self.metrics["errors"].labels(method=method, **self.labels).inc()

    def _optional(self, method, *args):
        """A method the node may not serve: None if it does not (remembered) or fails."""
        if method in self.unsupported:
            return None
        try:
            return self.client.call(method, *args)
        except rpc.Jip2Error as e:
            if e.code == rpc.METHOD_NOT_FOUND:
                self.unsupported.add(method)
            else:
                self._error(method)
            return None

    def observe(self, now=None):
        """Poll the node once; store and return the observation."""
        now = time.time() if now is None else now
        o = {"t": now, "up": False, "best": None, "final": None, "peers": None, "sync": None,
             "error": None}
        if now < self.next_try:
            o["error"] = "backing off after %d failures" % self.failures
            self.obs = o
            return o
        t0 = time.monotonic()
        method = "bestBlock"
        try:
            o["best"] = _block(self.client.call("bestBlock"))
            if self.params is None or self.params_gen != self.client.generation:
                method = "parameters"
                p = self._optional("parameters")
                self.params = p.get("V1") if isinstance(p, dict) and isinstance(p.get("V1"), dict) else {}
                self.params_gen = self.client.generation
            method = "finalizedBlock"
            f = self._optional("finalizedBlock")
            o["final"] = _block(f) if f else None
            method = "syncState"
            s = self._optional("syncState")
            if isinstance(s, dict):
                o["peers"] = s.get("num_peers")
                o["sync"] = s.get("status")
            o["up"] = True
            self.failures, self.next_try = 0, 0.0
        except (OSError, rpc.Jip2Error, KeyError, TypeError, ValueError) as e:
            self._error(method)
            o["error"] = "%s: %s: %s" % (method, type(e).__name__, e)
            if isinstance(e, OSError):
                self.failures += 1
                self.next_try = time.time() + min(MAX_BACKOFF, 2.0 ** (self.failures - 1))
                self.client.close()
        self.metrics["poll"].labels(**self.labels).observe(time.monotonic() - t0)
        self.obs = o
        return o

    def parent(self, h):
        d = self.client.call("parent", rpc.b64(bytes.fromhex(h)))
        return int(d["slot"]), rpc.unb64(d["header_hash"]).hex()

    def close(self):
        self.client.close()


class ParentCache:
    """header hash -> (parent slot, parent hash): header hashes are content-addressed, so
    any node's answer serves every node. Bounded, oldest first out."""

    def __init__(self, size=20000):
        self.size, self.map = size, collections.OrderedDict()
        self.lock = threading.Lock()

    def get(self, h):
        with self.lock:
            return self.map.get(h)

    def put(self, h, parent):
        with self.lock:
            self.map[h] = parent
            while len(self.map) > self.size:
                self.map.popitem(last=False)


def ancestor_at(node, block, slot, max_hops, cache):
    """The newest block at or below `slot` on the chain ending at `block` ({"slot",
    "hash"}), walking parent() (cached); None past max_hops or on any error."""
    cur_slot, cur = block["slot"], block["hash"]
    try:
        for _ in range(max_hops + 1):
            if cur_slot <= slot:
                return {"slot": cur_slot, "hash": cur}
            p = cache.get(cur)
            if p is None:
                p = node.parent(cur)
                cache.put(cur, p)
            cur_slot, cur = p
    except (OSError, rpc.Jip2Error, KeyError, TypeError, ValueError):
        return None
    return None


def _modal(values):
    """The value most nodes share, or None when the top count is tied."""
    c = collections.Counter(values).most_common()
    if not c or (len(c) > 1 and c[0][1] == c[1][1]):
        return None
    return c[0][0]


# ---- the exporter ------------------------------------------------------------------
class Exporter:
    def __init__(self, discover, interval=3.0, timeout=2.0, max_lag=3, discovery_every=5.0):
        self.discover = discover            # () -> [Target]
        self.interval, self.timeout, self.max_lag = interval, timeout, max_lag
        self.discovery_every = discovery_every
        self.nodes = {}                     # Target -> Node
        self.cache = ParentCache()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=128)
        self.lock = threading.Lock()        # rounds update the gauges under it; /metrics renders under it
        self.last_discovery = 0.0
        self.final_seen = {}                # (net, run_id) -> {slot: {hashes}}
        self.episodes = {}                  # (net, run_id) -> (t, top slot) of the divergence start
        m = self.m = obslib.Metrics()
        g = lambda name, help_, labels=NODE_LABELS + ("source",): m.gauge(name, help_, labels)  # noqa: E731
        net_labels = ("net", "run_id", "source")
        self.g = {
            "node_info": g("jam_node_info", "1 per node; identity as far as JIP-2 reveals it",
                           NODE_LABELS + ("source", "client_version", "gp_version", "spec",
                                          "chain_id", "validator_index", "peer_id")),
            "up": g("jam_node_up", "1 if the node answered this round's head reads"),
            "best": g("jam_best_slot", "slot of the node's best block"),
            "final": g("jam_finalized_slot", "slot of the node's latest finalized block"),
            "best48": g("jam_best_hash48", "first 48 bits of the best block's header hash: equal = same block"),
            "final48": g("jam_finalized_hash48", "first 48 bits of the finalized block's header hash"),
            "head_lag": g("jam_head_lag_slots", "slots the node's best block trails the net's newest best block"),
            "fin_lag": g("jam_finality_lag_slots", "best slot minus finalized slot on the node"),
            "head_agree": g("jam_head_agree", "1 if the node's block at the common slot is the one most nodes hold"),
            "final_agree": g("jam_final_agree", "1 if the node's block at the common finalized slot is the one most nodes hold"),
            "sync_peers": g("jip2_sync_peers", "peers with an open block announcement stream (JIP-2 syncState num_peers)"),
            "sync_done": g("jip2_sync_completed", "1 if JIP-2 syncState says Completed, 0 if InProgress"),
            "net_nodes": g("jam_net_nodes", "nodes watched", net_labels),
            "net_up": g("jam_net_nodes_up", "nodes that answered this round", net_labels),
            "net_head": g("jam_net_head_slot", "newest best slot among the nodes", net_labels),
            "net_final": g("jam_net_finalized_slot", "lowest finalized slot among the nodes (final everywhere)", net_labels),
            "net_heads": g("jam_net_heads", "distinct blocks the nodes hold at the common slot (1 = one head)", net_labels),
            "net_final_heads": g("jam_net_final_heads", "distinct blocks the nodes hold at the common finalized slot", net_labels),
            "one_head": g("jam_net_one_head", "1 if this round is one head: all up, one block, lag within the bound", net_labels),
            "divergence": g("jam_net_divergence_slots", "length of the current divergence episode in slots (0 = none)", net_labels),
            "targets": m.gauge("jip2_targets", "nodes discovered (containers, target files, --node)"),
            "last_round": m.gauge("jip2_last_round_timestamp_seconds", "unix time the last round finished"),
        }
        self.c = {
            "conflicts": m.counter("jam_finality_conflicts_total",
                                   "finalized slots seen with two different hashes (a safety failure)", net_labels),
            "rounds": m.counter("jam_net_samples_total", "rounds taken", net_labels),
            "diverged": m.counter("jam_net_diverged_samples_total", "rounds that were not one head", net_labels),
        }
        self.node_metrics = {
            "poll": m.histogram("jip2_poll_seconds", "time to poll one node (bestBlock, finalizedBlock, syncState)",
                                NODE_LABELS, buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2, 5)),
            "errors": m.counter("jip2_poll_errors_total", "failed JIP-2 calls, by method", NODE_LABELS + ("method",)),
        }

    # -- discovery
    def refresh(self, force=False):
        now = time.time()
        if not force and now - self.last_discovery < self.discovery_every:
            return
        self.last_discovery = now
        try:
            found = set(self.discover())
        except Exception as e:                    # noqa: BLE001 - keep the known nodes
            log("discovery failed: %s: %s" % (type(e).__name__, e))
            return
        for t in [t for t in self.nodes if t not in found]:
            node = self.nodes.pop(t)
            node.close()
            for fam in self.node_metrics.values():
                fam.remove(**t.labels)
            log("dropped %s %s (%s)" % (t.labels["net"], t.labels["node"], t.url))
        for t in sorted(found - set(self.nodes)):
            self.nodes[t] = Node(t, self.timeout, self.node_metrics)
            log("watching %s %s %s (%s)" % (t.labels["net"], t.labels["node"], t.labels["client"], t.url))
        live = {(t.labels["net"], t.labels["run_id"]) for t in self.nodes}
        for key in [k for k in self.final_seen if k not in live]:
            del self.final_seen[key]
            self.episodes.pop(key, None)
            for fam in self.c.values():
                fam.remove(net=key[0], run_id=key[1])

    # -- one round
    def _wait(self, futures, budget):
        if futures:
            concurrent.futures.wait(futures, timeout=budget)

    def round(self):
        self.refresh()
        start = time.time()
        polls = []
        for node in self.nodes.values():
            if node.busy is None or node.busy.done():
                node.busy = self.pool.submit(node.observe)
                polls.append(node.busy)
        self._wait(polls, self.timeout + 0.5)
        fresh = start - self.interval         # a poll that finished late but recently still counts
        obs = {}
        for t, node in self.nodes.items():
            o = node.obs
            obs[t] = o if (o and o["t"] >= fresh and node.busy.done()) else \
                {"t": start, "up": False, "best": None, "final": None, "peers": None,
                 "sync": None, "error": "no answer within %.1f s" % self.timeout}
        nets = collections.defaultdict(list)
        for t in self.nodes:
            nets[(t.labels["net"], t.labels["run_id"])].append(t)
        aligned = {}
        for key, ts in nets.items():
            aligned[key] = self._align(ts, obs)
        with self.lock:
            self._publish(nets, obs, aligned, start)
        return obs

    def _align(self, ts, obs):
        """{which: {"slot": common slot, "blocks": {target: block or None}}} for "best" and
        "final", over the nodes up with a hash for that head."""
        epoch = 12
        for t in ts:
            p = self.nodes[t].params or {}
            if p.get("epoch_period"):
                epoch = int(p["epoch_period"])
                break
        out = {}
        for which, hops in (("best", 2 * self.max_lag + 4), ("final", min(max(epoch, 16), 64))):
            have = [t for t in ts if obs[t]["up"] and obs[t][which]]
            if not have:
                continue
            slot = min(obs[t][which]["slot"] for t in have)
            futs = {t: self.pool.submit(ancestor_at, self.nodes[t], obs[t][which], slot, hops, self.cache)
                    for t in have}
            self._wait(list(futs.values()), self.timeout)
            out[which] = {"slot": slot, "blocks": {t: (f.result() if f.done() else None)
                                                   for t, f in futs.items()}}
        return out

    def _publish(self, nets, obs, aligned, start):
        for fam in self.g.values():
            fam.clear()
        self.g["targets"].labels().set(len(self.nodes))
        for (net, run_id), ts in sorted(nets.items()):
            nl = {"net": net, "run_id": run_id, "source": SOURCE}
            up = [t for t in ts if obs[t]["up"] and obs[t]["best"]]
            top = max((obs[t]["best"]["slot"] for t in up), default=None)
            al = aligned.get((net, run_id), {})
            head = al.get("best", {}).get("blocks", {})
            final = al.get("final", {}).get("blocks", {})
            hashes = {t: b["hash"] for t, b in head.items() if b}
            unaligned = [t for t, b in head.items() if not b]
            fhashes = {t: b["hash"] for t, b in final.items() if b}
            modal, fmodal = _modal(list(hashes.values())), _modal(list(fhashes.values()))
            lag = {t: top - obs[t]["best"]["slot"] for t in up}
            heads = len(set(hashes.values()))
            ok = (bool(up) and len(up) == len(ts) and heads <= 1 and not unaligned
                  and all(v <= self.max_lag for v in lag.values()))
            self.g["net_nodes"].labels(**nl).set(len(ts))
            self.g["net_up"].labels(**nl).set(len(up))
            if top is not None:
                self.g["net_head"].labels(**nl).set(top)
            finals = [obs[t]["final"]["slot"] for t in up if obs[t]["final"]]
            if finals:
                self.g["net_final"].labels(**nl).set(min(finals))
            if head:
                self.g["net_heads"].labels(**nl).set(heads)
            if final:
                self.g["net_final_heads"].labels(**nl).set(len(set(fhashes.values())))
            self.g["one_head"].labels(**nl).set(int(ok))
            self.c["rounds"].labels(**nl).inc()
            key = (net, run_id)
            if ok:
                self.episodes.pop(key, None)
                self.g["divergence"].labels(**nl).set(0)
            else:
                self.c["diverged"].labels(**nl).inc()
                ep = self.episodes.setdefault(key, (start, top or 0))
                slot_secs = next((float(self.nodes[t].params.get("slot_period_sec"))
                                  for t in ts if (self.nodes[t].params or {}).get("slot_period_sec")), 6.0)
                self.g["divergence"].labels(**nl).set(
                    round(max((top or 0) - ep[1], (start - ep[0]) / slot_secs), 1))
            # finality conflicts: a finalized slot seen with two hashes, from any node's
            # finalized block or its aligned ancestor
            seen = self.final_seen.setdefault(key, {})
            conflicts = self.c["conflicts"].labels(**nl)
            for b in [obs[t]["final"] for t in up if obs[t]["final"]] + [b for b in final.values() if b]:
                hs = seen.setdefault(b["slot"], set())
                if b["hash"] not in hs:
                    hs.add(b["hash"])
                    if len(hs) > 1:
                        conflicts.inc()
            conflicts.inc(0)
            if len(seen) > FINAL_MEMORY:
                for s in sorted(seen)[:FINAL_MEMORY // 4]:
                    del seen[s]
            for t in sorted(ts):
                lb = dict(t.labels, source=SOURCE)
                o, node = obs[t], self.nodes[t]
                params = node.params or {}
                cores = params.get("core_count")
                self.g["node_info"].labels(
                    client_version="", gp_version="", chain_id="", validator_index="", peer_id="",
                    spec=SPEC_BY_CORES.get(int(cores), "") if isinstance(cores, int) else "", **lb).set(1)
                self.g["up"].labels(**lb).set(int(t in up))
                if t not in up:
                    continue
                b, f = o["best"], o["final"]
                self.g["best"].labels(**lb).set(b["slot"])
                self.g["best48"].labels(**lb).set(hash48(b["hash"]))
                self.g["head_lag"].labels(**lb).set(lag[t])
                if f:
                    self.g["final"].labels(**lb).set(f["slot"])
                    self.g["final48"].labels(**lb).set(hash48(f["hash"]))
                    self.g["fin_lag"].labels(**lb).set(b["slot"] - f["slot"])
                if t in hashes:
                    self.g["head_agree"].labels(**lb).set(int(hashes[t] == modal))
                elif t in unaligned:
                    self.g["head_agree"].labels(**lb).set(0)
                if t in fhashes:
                    self.g["final_agree"].labels(**lb).set(int(fhashes[t] == fmodal))
                if isinstance(o["peers"], int):
                    self.g["sync_peers"].labels(**lb).set(o["peers"])
                if o["sync"] in ("Completed", "InProgress"):
                    self.g["sync_done"].labels(**lb).set(int(o["sync"] == "Completed"))
        self.g["last_round"].labels().set(time.time())

    def render(self):
        with self.lock:
            return self.m.render()

    def close(self):
        for node in self.nodes.values():
            node.close()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def run(self):
        while True:
            started = time.time()
            try:
                self.round()
            except Exception as e:                # noqa: BLE001 - keep serving
                log("round failed: %s: %s" % (type(e).__name__, e))
            time.sleep(max(0.0, self.interval - (time.time() - started)))


def make_discover(args):
    """() -> [Target] over every configured source."""
    static = static_targets(args.node or [], args.net)
    docker = obslib.Docker(args.docker_sock) if os.path.exists(args.docker_sock) else None
    if docker is None and not args.once:
        log("no Docker socket at %s: Docker discovery off" % args.docker_sock)

    last_docker = []

    def discover():
        out = list(static)
        if args.targets:
            out += file_targets(args.targets)
        if docker is not None:
            try:
                last_docker[:] = docker_targets(docker.containers())
            except (OSError, ValueError, obslib.DockerError) as e:
                # keep the containers found last time; a Docker hiccup must not drop them
                log("docker discovery failed, keeping %d known: %s" % (len(last_docker), e))
            out += last_docker
        return out
    return discover


def main(argv=None):
    env = os.environ
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("--port", type=int, default=int(env.get("JIP2_METRICS_PORT", 9912)))
    ap.add_argument("--interval", type=float, default=float(env.get("JIP2_INTERVAL", 3)))
    ap.add_argument("--timeout", type=float, default=float(env.get("JIP2_TIMEOUT", 2)))
    ap.add_argument("--max-lag", type=int, default=int(env.get("JIP2_MAX_LAG", 3)))
    ap.add_argument("--discovery", type=float, default=float(env.get("JIP2_DISCOVERY", 5)))
    ap.add_argument("--targets", default=env.get("JIP2_TARGETS", "/etc/obs/targets/jip2/*.json"))
    ap.add_argument("--docker-sock", default=env.get("DOCKER_SOCK", "/var/run/docker.sock"))
    ap.add_argument("--node", action="append",
                    default=[s for s in re.split(r"[;\s]+", env.get("JIP2_NODES", "")) if s],
                    help="NAME,CLIENT,URL (repeatable)")
    ap.add_argument("--net", default=env.get("JIP2_NET", "static"), help="the net label of --node nodes")
    ap.add_argument("--once", action="store_true", help="one round, print the metrics, exit")
    a = ap.parse_args(argv)
    ex = Exporter(make_discover(a), a.interval, a.timeout, a.max_lag, a.discovery)
    if a.once:
        ex.refresh(force=True)
        ex.round()
        sys.stdout.write(ex.render())
        ex.close()
        return 0
    obslib.serve_http(ex.m, a.port, routes={"/metrics": lambda: ("text/plain; version=0.0.4", ex.render())})
    log("serving /metrics on :%d; every %.1f s" % (a.port, a.interval))
    ex.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
