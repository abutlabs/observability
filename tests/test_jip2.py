"""Tests for the JIP-2 exporter (jip2/exporter.py) against fake JIP-2 nodes.

The fake node is a WebSocket server written here, independently of jip2/rpc.py, serving a
shared block tree. Hermetic: no Docker, no network beyond 127.0.0.1.

    python3 -m unittest discover -s tests
"""
import base64
import hashlib
import json
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jip2"))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import exporter  # noqa: E402

exporter.log = lambda msg: None          # quiet: the tests read the metrics, not the log

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---- a fake chain and fake JIP-2 nodes ----------------------------------------------
def H(name):
    """A deterministic 32-byte header hash for a block name."""
    return hashlib.sha256(name.encode()).digest()


class Tree:
    """Blocks by name: slot and parent. a0..a10 is the main chain; b8, b9 fork off a7."""

    def __init__(self):
        self.blocks = {"a0": (0, None)}
        for i in range(1, 11):
            self.blocks["a%d" % i] = (i, "a%d" % (i - 1))
        self.blocks["b8"] = (8, "a7")
        self.blocks["b9"] = (9, "b8")
        self.by_hash = {H(n): n for n in self.blocks}

    def desc(self, name):
        return {"header_hash": base64.b64encode(H(name)).decode(), "slot": self.blocks[name][0]}


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class FakeNode:
    """A JIP-2 node on 127.0.0.1: bestBlock, finalizedBlock (unless final is None),
    parent, parameters, syncState, answered from the shared tree."""

    def __init__(self, tree, best, final=None, cores=2, peers=5):
        self.tree, self.best, self.final, self.cores, self.peers = tree, best, final, cores, peers
        self.calls = []
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.url = "ws://127.0.0.1:%d" % self._sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        self._stop = True
        self._sock.close()

    def methods(self):
        t = self.tree

        def parent(h):
            name = t.by_hash.get(base64.b64decode(h))
            if name is None or t.blocks[name][1] is None:
                raise RpcError(1, "Block unavailable")
            return t.desc(t.blocks[name][1])

        m = {"bestBlock": lambda: t.desc(self.best),
             "parent": parent,
             "parameters": lambda: {"V1": {"core_count": self.cores, "epoch_period": 12,
                                           "slot_period_sec": 6, "val_count": 6}},
             "syncState": lambda: {"num_peers": self.peers, "status": "Completed"}}
        if self.final is not None:
            m["finalizedBlock"] = lambda: t.desc(self.final)
        return m

    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _exact(conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("gone")
            buf += chunk
        return buf

    def _serve(self, conn):
        with conn:
            try:
                req = b""
                while b"\r\n\r\n" not in req:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    req += chunk
                key = re.search(rb"(?i)sec-websocket-key:\s*(\S+)", req).group(1).decode()
                accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
                conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                              "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
                while True:
                    b0, b1 = self._exact(conn, 2)
                    n = b1 & 0x7F
                    if n == 126:
                        n = struct.unpack("!H", self._exact(conn, 2))[0]
                    elif n == 127:
                        n = struct.unpack("!Q", self._exact(conn, 8))[0]
                    mask = self._exact(conn, 4) if b1 & 0x80 else b"\0" * 4
                    data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._exact(conn, n)))
                    if b0 & 0x0F == 0x8:
                        return
                    msg = json.loads(data)
                    self.calls.append(msg["method"])
                    try:
                        fn = self.methods()[msg["method"]]
                        out = {"jsonrpc": "2.0", "id": msg["id"], "result": fn(*msg["params"])}
                    except RpcError as e:
                        out = {"jsonrpc": "2.0", "id": msg["id"],
                               "error": {"code": e.code, "message": e.message}}
                    except KeyError:
                        out = {"jsonrpc": "2.0", "id": msg["id"],
                               "error": {"code": -32601, "message": "Method not found"}}
                    body = json.dumps(out).encode()
                    head = struct.pack("!BB", 0x81, len(body)) if len(body) < 126 else \
                        struct.pack("!BBH", 0x81, 126, len(body))
                    conn.sendall(head + body)
            except (ConnectionError, OSError, ValueError):
                return


def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "ws://127.0.0.1:%d" % port


# ---- reading the exposition --------------------------------------------------------
LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (\S+)$')


def parse(text):
    """{(name, frozenset(labels)): value} for every sample line."""
    out = {}
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        m = LINE.match(ln)
        assert m, ln
        labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(2) or ""))
        out[(m.group(1), frozenset(labels.items()))] = float(m.group(3))
    return out


def value(samples, name, **labels):
    """The one sample of `name` whose labels include `labels` (None if none)."""
    hits = [v for (n, ls), v in samples.items() if n == name and set(labels.items()) <= ls]
    assert len(hits) <= 1, (name, labels, hits)
    return hits[0] if hits else None


def T(url, node, client="polkajam", net="n1", run_id="n1-r1"):
    return exporter.Target(url, {"net": net, "run_id": run_id, "node": node, "client": client})


class Base(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.fakes = []
        self.targets = []

    def tearDown(self):
        for f in self.fakes:
            f.stop()
        if getattr(self, "ex", None):
            self.ex.close()

    def node(self, name, best, final=None, client="polkajam", **kw):
        f = FakeNode(self.tree, best, final, **kw)
        self.fakes.append(f)
        self.targets.append(T(f.url, name, client))
        return f

    def run_round(self):
        if not hasattr(self, "ex"):
            self.ex = exporter.Exporter(lambda: list(self.targets), interval=1, timeout=2, max_lag=3)
        self.ex.refresh(force=True)
        self.ex.round()
        return parse(self.ex.render())


# ---- tests ---------------------------------------------------------------------------
class Exporting(Base):
    def test_per_node_and_net_view(self):
        self.node("n0", "a10", "a8")
        self.node("n1", "a10", "a8")
        self.node("n3", "b9", "a7")                     # on the fork
        self.node("n4", "a10", None, client="other")    # serves no finalizedBlock
        self.targets.append(T(closed_port_url(), "n5"))  # down
        s = self.run_round()
        src = {"source": "jip2", "net": "n1", "run_id": "n1-r1"}
        self.assertEqual(value(s, "jam_best_slot", node="n0", **src), 10)
        self.assertEqual(value(s, "jam_finalized_slot", node="n0", **src), 8)
        self.assertEqual(value(s, "jam_finality_lag_slots", node="n0", **src), 2)
        self.assertEqual(value(s, "jam_best_slot", node="n3", **src), 9)
        self.assertEqual(value(s, "jam_head_lag_slots", node="n3", **src), 1)
        self.assertEqual(value(s, "jam_best_hash48", node="n0", **src), int(H("a10").hex()[:12], 16))
        # n4: best block, no finality
        self.assertEqual(value(s, "jam_best_slot", node="n4", client="other", **src), 10)
        self.assertIsNone(value(s, "jam_finalized_slot", node="n4", **src))
        self.assertIsNone(value(s, "jam_finality_lag_slots", node="n4", **src))
        # n5 down: up 0 and nothing else
        self.assertEqual(value(s, "jam_node_up", node="n5", **src), 0)
        self.assertIsNone(value(s, "jam_best_slot", node="n5", **src))
        self.assertEqual(value(s, "jam_node_up", node="n0", **src), 1)
        # the common slot is 9: n3 holds b9, the others a9
        self.assertEqual(value(s, "jam_net_heads", **src), 2)
        self.assertEqual(value(s, "jam_head_agree", node="n0", **src), 1)
        self.assertEqual(value(s, "jam_head_agree", node="n4", **src), 1)
        self.assertEqual(value(s, "jam_head_agree", node="n3", **src), 0)
        # finality lines up at slot 7 (n3 finalized a7): one block
        self.assertEqual(value(s, "jam_net_final_heads", **src), 1)
        self.assertEqual(value(s, "jam_final_agree", node="n3", **src), 1)
        self.assertEqual(value(s, "jam_net_finalized_slot", **src), 7)
        self.assertEqual(value(s, "jam_net_head_slot", **src), 10)
        self.assertEqual(value(s, "jam_net_nodes", **src), 5)
        self.assertEqual(value(s, "jam_net_nodes_up", **src), 4)
        self.assertEqual(value(s, "jam_net_one_head", **src), 0)
        self.assertEqual(value(s, "jam_finality_conflicts_total", **src), 0)
        # identity: spec from the core count; syncState's peers
        self.assertEqual(value(s, "jam_node_info", node="n0", spec="tiny", client="polkajam", **src), 1)
        self.assertEqual(value(s, "jip2_sync_peers", node="n0", **src), 5)
        self.assertEqual(value(s, "jip2_sync_completed", node="n0", **src), 1)
        # errors are counted by method; the down node's connect failure is one of them
        self.assertGreaterEqual(value(s, "jip2_poll_errors_total", node="n5", method="bestBlock"), 1)
        self.assertEqual(value(s, "jip2_poll_seconds_count", node="n0"), 1)

    def test_one_head_when_every_node_agrees_within_the_lag(self):
        self.node("n0", "a10", "a8")
        self.node("n1", "a9", "a8")
        self.node("n2", "a8", "a7", cores=341)
        s = self.run_round()
        self.assertEqual(value(s, "jam_net_heads", net="n1"), 1)
        self.assertEqual(value(s, "jam_net_one_head", net="n1"), 1)
        self.assertEqual(value(s, "jam_net_divergence_slots", net="n1"), 0)
        self.assertEqual(value(s, "jam_node_info", node="n2", spec="full"), 1)

    def test_a_lagging_node_breaks_one_head(self):
        self.node("n0", "a10", "a8")
        self.node("n1", "a10", "a8")
        self.node("n2", "a5", "a4")                     # 5 slots behind, bound is 3
        s = self.run_round()
        self.assertEqual(value(s, "jam_head_lag_slots", node="n2"), 5)
        self.assertEqual(value(s, "jam_net_heads", net="n1"), 1)      # same chain at slot 5
        self.assertEqual(value(s, "jam_net_one_head", net="n1"), 0)
        self.assertEqual(value(s, "jam_net_diverged_samples_total", net="n1"), 1)

    def test_a_finality_conflict_is_counted_once_and_kept(self):
        f0 = self.node("n0", "a10", "a8")
        f1 = self.node("n1", "a10", "a8")
        s = self.run_round()
        self.assertEqual(value(s, "jam_finality_conflicts_total", net="n1"), 0)
        f1.final = "b8"                                  # slot 8 finalized as b8 as well as a8
        f1.best = "b9"
        s = self.run_round()
        self.assertEqual(value(s, "jam_finality_conflicts_total", net="n1"), 1)
        self.assertEqual(value(s, "jam_net_final_heads", net="n1"), 2)
        self.assertEqual(value(s, "jam_final_agree", node="n1"), 0)      # a tie: no modal block
        f1.final, f1.best = "a8", "a10"
        s = self.run_round()
        self.assertEqual(value(s, "jam_finality_conflicts_total", net="n1"), 1)
        self.assertEqual(f0.best, "a10")

    def test_nets_are_judged_apart(self):
        a = FakeNode(self.tree, "a10", "a8")
        b = FakeNode(self.tree, "b9", "a7")
        self.fakes += [a, b]
        self.targets += [T(a.url, "x0", net="netA", run_id="netA-1"),
                         T(b.url, "y0", net="netB", run_id="netB-1")]
        s = self.run_round()
        self.assertEqual(value(s, "jam_net_heads", net="netA"), 1)
        self.assertEqual(value(s, "jam_net_heads", net="netB"), 1)
        self.assertEqual(value(s, "jam_net_one_head", net="netA"), 1)

    def test_parameters_are_read_once_per_connection(self):
        f = self.node("n0", "a10", "a8")
        self.run_round()
        self.run_round()
        self.assertEqual(f.calls.count("parameters"), 1)
        self.assertEqual(f.calls.count("bestBlock"), 2)

    def test_a_node_without_finalized_block_is_asked_once(self):
        f = self.node("n4", "a10", None)
        self.run_round()
        self.run_round()
        self.assertEqual(f.calls.count("finalizedBlock"), 1)


class Discovery(Base):
    def test_removed_targets_leave_the_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            f = FakeNode(self.tree, "a10", "a8")
            self.fakes.append(f)
            path = os.path.join(d, "native--lasair.json")
            with open(path, "w") as fh:
                json.dump([{"targets": [f.url.replace("ws://", "")],
                            "labels": {"net": "native", "run_id": "native-r1", "node": "v0",
                                       "client": "lasair", "job": "lasair"}}], fh)
            self.ex = exporter.Exporter(lambda: exporter.file_targets(os.path.join(d, "*.json")),
                                        interval=1, timeout=2)
            self.ex.refresh(force=True)
            self.ex.round()
            s = parse(self.ex.render())
            self.assertEqual(value(s, "jam_best_slot", node="v0", client="lasair", run_id="native-r1"), 10)
            self.assertEqual(value(s, "jip2_poll_seconds_count", node="v0"), 1)
            os.remove(path)
            self.ex.refresh(force=True)
            self.ex.round()
            s = parse(self.ex.render())
            self.assertIsNone(value(s, "jam_best_slot", node="v0"))
            self.assertIsNone(value(s, "jip2_poll_seconds_count", node="v0"))
            self.assertIsNone(value(s, "jam_net_nodes", net="native"))
            self.assertIsNone(value(s, "jam_finality_conflicts_total", net="native"))

    def test_file_targets_default_labels(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.json"), "w") as fh:
                json.dump([{"targets": ["host.docker.internal:19800", "ws://h:1/rpc", "junk"],
                            "labels": {"net": "k", "job": "lasair"}}], fh)
            with open(os.path.join(d, "broken.json"), "w") as fh:
                fh.write("{half")
            ts = exporter.file_targets(os.path.join(d, "*.json"))
        self.assertEqual([t.url for t in ts], ["ws://host.docker.internal:19800", "ws://h:1/rpc"])
        self.assertEqual(ts[0].labels, {"net": "k", "run_id": "k-adhoc",
                                        "node": "host.docker.internal:19800", "client": "lasair"})

    def test_docker_labels(self):
        P = "org.abutlabs.obs."
        containers = [
            {"Names": ["/lasair-pj-pj3-1"], "Labels": {
                P + "jip2": "19803", P + "net": "lasair-pj", P + "run_id": "lasair-pj-20260928T200000Z",
                P + "client": "polkajam", "com.docker.compose.project": "lasair-pj",
                "com.docker.compose.service": "pj3"}},
            {"Names": ["/lasair6-reader1-1"], "Labels": {
                P + "jip2": "ws://lasair6-reader1-1:19990", P + "node": "lm1", P + "client": "lasair",
                "com.docker.compose.project": "lasair6", "com.docker.compose.service": "reader1"}},
            {"Names": ["/plain"], "Labels": {P + "jip2": "19800"}},
            {"Names": ["/nolabel"], "Labels": {P + "scrape": "true"}},
            {"Names": ["/optout"], "Labels": {P + "jip2": "19800", P + "enable": "false"}},
            {"Names": ["/bad"], "Labels": {P + "jip2": "not a port"}},
            {"Names": ["/lasair-pj-reader-1"], "Labels": {
                P + "jip2": "19990", P + "jip2.node": "lm0", P + "jip2.client": "lasair",
                P + "net": "lasair-pj", P + "logs": "true", "com.docker.compose.service": "reader"}},
        ]
        ts = exporter.docker_targets(containers)
        self.assertEqual([(t.url, t.labels) for t in ts], [
            ("ws://lasair-pj-pj3-1:19803", {"net": "lasair-pj", "run_id": "lasair-pj-20260928T200000Z",
                                             "node": "pj3", "client": "polkajam"}),
            ("ws://lasair6-reader1-1:19990", {"net": "lasair6", "run_id": "lasair6-adhoc",
                                               "node": "lm1", "client": "lasair"}),
            ("ws://plain:19800", {"net": "docker", "run_id": "docker-adhoc", "node": "plain",
                                  "client": "plain"}),
            # a proxy names the node it serves; its own logs keep node=reader
            ("ws://lasair-pj-reader-1:19990", {"net": "lasair-pj", "run_id": "lasair-pj-adhoc",
                                               "node": "lm0", "client": "lasair"}),
        ])

    def test_endpoint_url(self):
        u = exporter.endpoint_url
        self.assertEqual(u("19800", "pj0"), "ws://pj0:19800")
        self.assertIsNone(u("19800"))
        self.assertEqual(u("wss://node.example/rpc"), "wss://node.example/rpc")
        self.assertEqual(u("10.0.0.1:19800"), "ws://10.0.0.1:19800")
        self.assertEqual(u("[::1]:19800"), "ws://[::1]:19800")
        self.assertIsNone(u("http://x:1"))

    def test_static_nodes(self):
        ts = exporter.static_targets(["pj0,polkajam,ws://127.0.0.1:19800"], "dev")
        self.assertEqual(ts[0].labels, {"net": "dev", "run_id": "dev-adhoc", "node": "pj0",
                                        "client": "polkajam"})
        with self.assertRaises(ValueError):
            exporter.static_targets(["pj0,ws://x:1"], "dev")


class Slow(Base):
    def test_a_node_that_never_answers_does_not_stall_the_round(self):
        import time
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(4)                        # accepts, never completes the handshake
        try:
            self.node("n0", "a10", "a8")
            self.targets.append(T("ws://127.0.0.1:%d" % silent.getsockname()[1], "slow"))
            self.ex = exporter.Exporter(lambda: list(self.targets), interval=1, timeout=0.5)
            self.ex.refresh(force=True)
            t0 = time.monotonic()
            self.ex.round()
            took = time.monotonic() - t0
            s = parse(self.ex.render())
        finally:
            silent.close()
        self.assertLess(took, 2.5)
        self.assertEqual(value(s, "jam_node_up", node="slow"), 0)
        self.assertEqual(value(s, "jam_node_up", node="n0"), 1)
        self.assertEqual(value(s, "jam_best_slot", node="n0"), 10)


class Once(unittest.TestCase):
    def test_once_prints_the_metrics(self):
        import contextlib
        import io
        tree = Tree()
        f = FakeNode(tree, "a10", "a8")
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = exporter.main(["--once", "--node", "pj0,polkajam,%s" % f.url, "--net", "dev",
                                    "--docker-sock", "/nonexistent", "--targets", ""])
        finally:
            f.stop()
        self.assertEqual(rc, 0)
        s = parse(out.getvalue())
        self.assertEqual(value(s, "jam_best_slot", node="pj0", net="dev"), 10)


if __name__ == "__main__":
    unittest.main()
