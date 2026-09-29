"""Tests for the JIP-3 receiver (jip3/): the codec against the spec, the node information
message's parameter layouts, framing, event-ID linking, the jam_* metrics a synthetic
sender's stream produces, the Loki lines, and the label rules. Hermetic: ephemeral ports,
a fake Loki, a temporary targets file, no Docker.

    python3 -m unittest discover -s tests
"""
import asyncio
import http.server
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jip3"))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import codec  # noqa: E402
import receiver  # noqa: E402
import sender  # noqa: E402

receiver.log = lambda msg: None          # the receiver's progress lines, not the tests'

LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (\S+)$')
PAIR = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_metrics(text):
    """{(name, frozenset(labels.items())): value}"""
    out = {}
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        m = LINE.match(ln)
        assert m, ln
        out[(m.group(1), frozenset(PAIR.findall(m.group(2) or "")))] = float(m.group(3))
    return out


def value(metrics, name, **labels):
    """Sum of the series of `name` whose labels include `labels`."""
    want = set((k, str(v)) for k, v in labels.items())
    return sum(v for (n, lb), v in metrics.items() if n == name and want <= lb)


class FakeLoki(http.server.BaseHTTPRequestHandler):
    pushes = []

    def do_POST(self):
        FakeLoki.pushes.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


class Running:
    """A Receiver serving on an ephemeral port in a background event loop."""

    def __init__(self, rx):
        self.rx = rx
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.server = asyncio.run_coroutine_threadsafe(rx.serve("127.0.0.1", 0), self.loop).result(5)
        self.port = self.server.sockets[0].getsockname()[1]

    def metrics(self):
        return parse_metrics(self.rx.metrics.render())

    def wait(self, cond, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond(self.metrics()):
                return True
            time.sleep(0.02)
        return False

    def stop(self):
        self.server.close()
        asyncio.run_coroutine_threadsafe(self.server.wait_closed(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()
        if self.rx.loki:
            self.rx.loki.stop()


def node_info_bytes(name="n", layout="gp-0.8", cores=2, jamnp=("127.0.0.1", 40000), impl="PolkaJam"):
    return codec.encode_node_info(sender.h("genesis"), sender.h("peer", name),
                                  {"ip": jamnp[0], "port": jamnp[1]}, impl, "0.1.29", "0.8.0",
                                  layout=layout, params=dict(sender.TINY, C=cores))


class Codec(unittest.TestCase):
    def test_natural_encoding_matches_gp_c6(self):
        vectors = {0: "00", 1: "01", 127: "7f", 128: "8080", 255: "80ff", 16383: "bfff",
                   16384: "c00040", 2 ** 21 - 1: "dfffff", 2 ** 21: "e0000020",
                   2 ** 56 - 1: "fe" + "ff" * 7, 2 ** 56: "ff" + "00" * 7 + "01",
                   2 ** 64 - 1: "ff" + "ff" * 8}
        for x, hx in vectors.items():
            self.assertEqual(codec.encode_natural(x).hex(), hx, x)
            self.assertEqual(codec.Reader(bytes.fromhex(hx)).natural(), x, x)

    def test_every_event_round_trips(self):
        self.assertEqual(len(codec.EVENTS), 115)
        for disc in sorted(codec.EVENTS):
            for cores in (2, 341):
                f = sender.sample_event(disc, cores, disc)
                c = codec.encode_event(disc, f, 987654321, cores)
                self.assertEqual(c[:8], (987654321).to_bytes(8, "little"))
                self.assertEqual(c[8], disc)
                for known in (cores, None):     # with C from node info, and from the length
                    ev = codec.decode_event(c, known)
                    self.assertEqual((ev.disc, ev.name, ev.ts, ev.fields, ev.warnings),
                                     (disc, codec.NAMES[disc], 987654321, f, []), (disc, cores, known))

    def test_option_none_and_enums(self):
        f = {"peer": sender.h("p"), "terminator": None, "reason": ""}
        self.assertEqual(codec.decode_event(codec.encode_event(27, f, 1)).fields, f)
        f["terminator"] = "remote"
        c = codec.encode_event(27, f, 1)
        self.assertEqual(c[9 + 32:9 + 34], b"\x01\x01")          # Some(Remote)
        self.assertEqual(codec.decode_event(c).fields, f)

    def test_status_and_bitfield_sizes_follow_the_core_count(self):
        f = sender.sample_event(10, 341, 1)
        c = codec.encode_event(10, f, 1)
        self.assertEqual(len(c), 9 + 341 + 32)
        f = sender.sample_event(126, 341, 1)
        self.assertEqual(len(codec.encode_event(126, f, 1)), 9 + 32 + 43)   # ceil(341 / 8)
        ev = codec.decode_event(codec.encode_event(10, sender.sample_event(10, 2, 1), 1), 3)
        self.assertEqual(len(ev.fields["guarantees_per_core"]), 2)          # the length wins
        self.assertEqual(len(ev.warnings), 1)                               # and says so

    def test_malformed_events(self):
        with self.assertRaises(codec.UnknownEvent) as e:
            codec.decode_event((5).to_bytes(8, "little") + bytes([250]) + b"xyz")
        self.assertEqual(e.exception.disc, 250)
        good = codec.encode_event(11, {"slot": 7, "hash": sender.h("b")}, 1)
        with self.assertRaises(codec.DecodeError):
            codec.decode_event(good[:-1])                                   # truncated
        with self.assertRaises(codec.DecodeError):
            codec.decode_event(good + b"\x00")                              # trailing byte
        with self.assertRaises(codec.DecodeError):
            codec.decode_event(codec.encode_event(13, {"synced": True}, 1)[:-1] + b"\x02")  # bool 2
        long_reason = (1).to_bytes(8, "little") + bytes([28]) + sender.h("p") + \
            codec.encode_natural(129) + b"x" * 129
        with self.assertRaises(codec.DecodeError):
            codec.decode_event(long_reason)                                 # String<128>

    def test_param_layouts_are_the_gray_papers(self):
        # GP fetch case 0: 0.7.x 33 fields / 134 bytes; 0.8.0 drops N, V, W_E, W_P
        self.assertEqual(codec.PARAM_SIZES, {"gp-0.7": 134, "gp-0.8": 122})
        self.assertEqual(len(codec.PARAM_LAYOUTS["gp-0.7"]), 33)
        self.assertEqual([n for n, _ in codec.PARAM_LAYOUTS["gp-0.8"]][14:22],
                         ["L", "O", "P", "Q", "R", "T", "U", "W_A"])

    def test_node_info_every_layout_and_the_fallback(self):
        for layout in ("gp-0.7", "gp-0.8"):
            info = codec.decode_node_info(node_info_bytes("x", layout, cores=341,
                                                          jamnp=("10.231.6.13", 41603)))
            self.assertEqual((info["version"], info["params_layout"], info["params_len"], info["cores"]),
                             (1, layout, codec.PARAM_SIZES[layout], 341))
            self.assertEqual(info["params"]["P"], 6)
            self.assertEqual(info["peer_address"], {"ip": "10.231.6.13", "port": 41603})
            self.assertEqual((info["impl_name"], info["impl_version"], info["gp_version"]),
                             ("PolkaJam", "0.1.29", "0.8.0"))
            self.assertEqual(info["peer_id"], sender.h("peer", "x"))
        raw = codec.encode_node_info(sender.h("g"), sender.h("p"), {"ip": "::", "port": 1}, "X", "1",
                                     "0.9.0", params_raw=bytes(150), note="n" * 300)
        info = codec.decode_node_info(raw)
        self.assertEqual((info["params_layout"], info["params_len"], info["cores"], info["note"]),
                         ("unknown", 150, None, "n" * 300))
        with self.assertRaises(codec.DecodeError):
            codec.decode_node_info(b"\x01" + b"\xff" * 40)

    def test_unframe_splits_and_keeps_the_rest(self):
        a, b = codec.frame(b"abc"), codec.frame(b"")
        msgs, rest = codec.unframe(a + b + codec.frame(b"defg")[:5])
        self.assertEqual((msgs, rest), ([b"abc", b""], codec.frame(b"defg")[:5]))


class Linking(unittest.TestCase):
    """Event IDs and the bounded pending maps, fed straight into a Connection."""

    def setUp(self):
        self.rx = receiver.Receiver(max_pending=4)
        self.conn = receiver.Connection(self.rx, "10.0.0.1")
        info = codec.decode_node_info(node_info_bytes())
        self.conn.node_info(b"", {"net": "t", "run_id": "t-1", "node": "v0", "client": "polkajam"},
                            "self", info)

    def send(self, name, fields, ts):
        return self.conn.message(codec.encode_event(name, fields, ts, 2))

    def m(self):
        return parse_metrics(self.rx.metrics.render())

    def test_ids_advance_by_one_and_by_the_dropped_count(self):
        self.send("importing", {"slot": 1, "outline": sender.outline(1)}, 1_000_000)      # id 0
        self.send("dropped", {"last_timestamp": 1_000_100, "count": 3}, 1_000_050)        # id 1
        self.assertEqual(self.conn.next_id, 4)
        self.send("importing", {"slot": 2, "outline": sender.outline(2)}, 2_000_000)      # id 4
        self.send("block_executed", {"block": 4, "accumulated": []}, 2_250_000)          # id 5
        self.send("block_executed", {"block": 0, "accumulated": []}, 3_000_000)          # id 6
        self.send("block_executed", {"block": 2, "accumulated": []}, 3_000_000)          # dropped id
        m = self.m()
        self.assertEqual(value(m, "jam_blocks_imported_total"), 2)
        self.assertEqual(value(m, "jam_block_import_seconds_count"), 2)
        self.assertAlmostEqual(value(m, "jam_block_import_seconds_sum"), 0.25 + 2.0)
        self.assertEqual(value(m, "jip3_dropped_events_total", node="v0"), 3)

    def test_block_stages_of_an_imported_block(self):
        o = sender.outline(7)
        imp = self.conn.next_id
        self.send("importing", {"slot": 7, "outline": o}, 1_000_000)
        self.send("block_verified", {"importing": imp}, 1_100_000)
        self.send("block_executed", {"block": imp, "accumulated": []}, 1_300_000)
        self.send("best_block_changed", {"slot": 7, "hash": o["hash"]}, 1_400_000)
        self.send("best_block_changed", {"slot": 7, "hash": o["hash"]}, 1_900_000)     # once per stage
        self.send("finalized_block_changed", {"slot": 7, "hash": o["hash"]}, 9_000_000)
        self.send("finalized_block_changed", {"slot": 8, "hash": sender.h("unseen")}, 9_500_000)
        m = self.m()
        for stage, age in (("verified", .1), ("executed", .3), ("best", .4), ("finalized", 8.0)):
            self.assertEqual(value(m, "jam_block_stage_seconds_count", stage=stage), 1, stage)
            self.assertAlmostEqual(value(m, "jam_block_stage_seconds_sum", stage=stage), age, msg=stage)
        self.assertNotIn(o["hash"], self.conn.blocks)                   # forgotten once final

    def test_block_stages_of_an_authored_block(self):
        a, o = self.conn.next_id, sender.outline(3)
        self.send("authoring", {"slot": 3, "parent": sender.h("parent")}, 2_000_000)
        self.send("authored", {"authoring": a, "outline": o}, 2_500_000)
        self.send("finalized_block_changed", {"slot": 3, "hash": o["hash"]}, 5_000_000)
        m = self.m()
        self.assertAlmostEqual(value(m, "jam_block_stage_seconds_sum", stage="authored"), 0.5)
        self.assertAlmostEqual(value(m, "jam_block_stage_seconds_sum", stage="finalized"), 3.0)

    def test_a_leading_dropped_event_only_sets_the_next_id(self):
        conn = receiver.Connection(self.rx, "10.0.0.2")                  # a reconnect
        conn.node_info(b"", {"net": "t", "run_id": "t-1", "node": "v1", "client": "lasair"},
                       "self", codec.decode_node_info(node_info_bytes()))
        conn.message(codec.encode_event("dropped", {"last_timestamp": 5, "count": 500}, 5, 2))
        self.assertEqual(conn.next_id, 500)
        self.assertEqual(value(self.m(), "jip3_dropped_events_total", node="v1"), 0)

    def test_pending_maps_are_bounded(self):
        for i in range(10):
            self.send("importing", {"slot": i, "outline": sender.outline(i)}, 1000 + i)
        self.assertEqual(list(self.conn.importing), [6, 7, 8, 9])

    def test_stages_of_a_secondary_guarantor(self):
        wp = self.conn.next_id
        self.send("work_package_being_shared", {"primary": sender.h("p")}, 0)
        self.send("work_package_received", {"package": wp, "core": 1, "outline": sender.sample(
            codec.WORK_PACKAGE_OUTLINE)}, 10_000)
        self.send("authorized", {"package": wp, "cost": sender.sample(codec.IS_AUTHORIZED_COST)}, 30_000)
        self.send("refined", {"package": wp, "costs": [sender.REFINE]}, 330_000)
        self.send("work_report_built", {"package": wp, "outline": sender.sample(codec.WORK_REPORT_OUTLINE)},
                  340_000)
        self.send("work_report_signature_sent", {"package": wp}, 345_000)
        m = self.m()
        for stage, secs in (("receive", .01), ("authorize", .02), ("refine", .3), ("report", .01)):
            self.assertEqual(value(m, "jam_wp_stage_seconds_count", stage=stage), 1, stage)
            self.assertAlmostEqual(value(m, "jam_wp_stage_seconds_sum", stage=stage), secs, 6)
        self.assertEqual(value(m, "jam_wp_stage_seconds_count", stage="guarantee"), 0)
        self.assertNotIn(wp, self.conn.packages)

    def test_refusal_reasons(self):
        self.assertEqual(receiver.refusal_reason("Anchor too old"), "anchor")
        self.assertEqual(receiver.refusal_reason("work-package queue is full"), "queue_full")
        self.assertEqual(receiver.refusal_reason("not authorized"), "unauthorized")
        self.assertEqual(receiver.refusal_reason("stream reset"), "other")

    def test_misbehaviour_reports_by_peer_and_reason(self):
        known = codec.decode_node_info(node_info_bytes())["peer_id"]   # v0's own key
        other = bytes([0xab] * 32)
        self.send("peer_misbehaved", {"peer": other, "reason": "Invalid guarantee: No signature from peer"}, 1)
        self.send("peer_misbehaved", {"peer": other, "reason": "Invalid guarantee: No signature from peer"}, 2)
        self.send("peer_misbehaved", {"peer": other, "reason": "Bad incoming stream protocol 153"}, 3)
        self.send("peer_misbehaved", {"peer": known, "reason": "Bad incoming stream protocol 153"}, 4)
        m = self.m()
        self.assertEqual(value(m, "jam_peer_misbehaviour_total", peer="abababab",
                               reason="Invalid guarantee"), 2)
        self.assertEqual(value(m, "jam_peer_misbehaviour_total", peer="abababab",
                               reason="Bad incoming stream protocol N"), 1)
        self.assertEqual(value(m, "jam_peer_misbehaviour_total", peer="v0",
                               reason="Bad incoming stream protocol N"), 1)
        self.assertEqual(receiver.short_reason(""), "unspecified")
        self.assertLessEqual(len(receiver.short_reason("x" * 200)), 60)

    def test_forgetting_a_node_after_it_left(self):
        self.send("best_block_changed", {"slot": 5, "hash": sender.h("b")}, 1)
        self.conn.closed()
        self.assertEqual(value(self.m(), "jip3_connected", node="v0"), 0)
        self.rx.sweep(now=time.time() + self.rx.forget_secs - 10)
        self.assertEqual(value(self.m(), "jam_best_slot", node="v0"), 5)
        self.rx.sweep(now=time.time() + self.rx.forget_secs + 10)
        self.assertEqual([k for k in self.m() if dict(k[1]).get("node") == "v0"], [])


class Labels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(self.tmp.name, "rehearsal--lasair.json"), "w") as fh:
            json.dump([{"targets": ["host.docker.internal:40123"],
                        "labels": {"net": "rehearsal", "run_id": "rehearsal-r1", "job": "lasair",
                                   "node": "v3", "client": "lasair"}},
                       {"targets": ["10.0.0.9:40124"], "labels": {"net": "rehearsal", "node": "v4"}}], fh)
        self.lb = receiver.Labeler(None, os.path.join(self.tmp.name, "*.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def info(self, ip, port, name="x"):
        return codec.decode_node_info(node_info_bytes(name, jamnp=(ip, port)))

    def test_rules(self):
        self.assertEqual(self.lb.resolve("127.0.0.1", self.info("0.0.0.0", 40123)),
                         ({"net": "rehearsal", "run_id": "rehearsal-r1", "node": "v3", "client": "lasair"},
                          "target"))
        # a target with an IP matches only that IP when the node names one ...
        lb, rule = self.lb.resolve("127.0.0.1", self.info("10.0.0.8", 40124))
        self.assertEqual(rule, "self")
        # ... and by port when it does not; defaults for the labels the file leaves out
        self.assertEqual(self.lb.resolve("127.0.0.1", self.info("::", 40124)),
                         ({"net": "rehearsal", "run_id": "rehearsal-adhoc", "node": "v4",
                           "client": "polkajam"}, "target"))
        lb, rule = self.lb.resolve("127.0.0.1", self.info("10.0.0.1", 1, "zz"))
        self.assertEqual((lb, rule), ({"net": "jip3", "run_id": "jip3-adhoc",
                                       "node": "polkajam-" + sender.h("peer", "zz")[:4].hex(),
                                       "client": "polkajam"}, "self"))
        self.assertEqual(self.lb.resolve("127.0.0.1", None)[0]["node"], "unknown-00000000")


class EndToEnd(unittest.TestCase):
    """A synthetic sender against a receiver on an ephemeral port, Loki faked."""

    def setUp(self):
        FakeLoki.pushes = []
        self.loki = http.server.HTTPServer(("127.0.0.1", 0), FakeLoki)
        threading.Thread(target=self.loki.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(self.tmp.name, "t--lasair.json"), "w") as fh:
            json.dump([{"targets": ["host.docker.internal:40123"],
                        "labels": {"net": "testnet", "run_id": "testnet-r1", "node": "v3",
                                   "client": "lasair", "job": "lasair"}}], fh)
        rx = receiver.Receiver(
            loki_url="http://127.0.0.1:%d/loki/api/v1/push" % self.loki.server_port,
            labeler=receiver.Labeler(None, os.path.join(self.tmp.name, "*.json")),
            max_message=4096, loki_interval=0.05)
        self.r = Running(rx)

    def tearDown(self):
        self.r.stop()
        self.loki.shutdown()
        self.loki.server_close()
        self.tmp.cleanup()

    def test_a_senders_rounds_become_jam_metrics_and_loki_lines(self):
        s = sender.Sender("127.0.0.1", self.r.port)
        s.node_info("v3", impl="lasair", impl_version="2.2.0", jamnp=("0.0.0.0", 40123))
        for i in range(5):
            s.round(i, "v3")
        sent = s.sent
        s.close()
        L = {"net": "testnet", "run_id": "testnet-r1", "node": "v3", "client": "lasair"}
        self.assertTrue(self.r.wait(lambda m: value(m, "jip3_connected", **L) == 0 and
                                    value(m, "jip3_events_total", **L) == sent))
        m = self.r.metrics()
        J = dict(L, source="jip3")
        expect = {"jam_blocks_authored_total": 5, "jam_blocks_imported_total": 5,
                  "jam_block_import_seconds_count": 5, "jam_wp_received_total": 5,
                  "jam_assurances_signed_total": 5, "jam_tickets_generated_total": 10,
                  "jam_refine_seconds_count": 5, "jam_refine_gas_total": 5 * 1_500_000}
        for name, v in expect.items():
            self.assertEqual(value(m, name, **J), v, name)
        self.assertEqual(value(m, "jam_wp_refused_total", reason="duplicate", **J), 5)
        self.assertEqual(value(m, "jam_wp_refused_total", reason="anchor", **J), 5)
        self.assertEqual(value(m, "jam_tickets_transferred_total", direction="in", **J), 5)
        for stage in ("receive", "authorize", "refine", "report", "guarantee", "distribute"):
            self.assertEqual(value(m, "jam_wp_stage_seconds_count", stage=stage, **J), 5, stage)
        self.assertAlmostEqual(value(m, "jam_wp_stage_seconds_sum", stage="refine", **J), 5 * 0.3, 6)
        self.assertEqual(value(m, "jam_peers", role="validator", **J), 4)
        self.assertEqual(value(m, "jam_peers", role="other", **J), 1)
        self.assertGreater(value(m, "jam_best_slot", **J), 9_000_000)
        self.assertEqual(value(m, "jam_finalized_slot", **J), value(m, "jam_best_slot", **J) - 2)
        self.assertEqual(value(m, "jip3_dropped_events_total", **L), 3)
        self.assertEqual(value(m, "jip3_decode_errors_total", **L), 0)
        info = [dict(lb) for (n, lb) in m if n == "jam_node_info"]
        self.assertEqual(len(info), 1)
        self.assertEqual({k: info[0][k] for k in ("client", "client_version", "gp_version", "spec", "chain_id",
                                                  "validator_index", "peer_id", "source", "node")},
                         {"client": "lasair", "client_version": "2.2.0", "gp_version": "0.8.0", "spec": "tiny",
                          "chain_id": sender.h("genesis")[:8].hex(), "validator_index": "",
                          "peer_id": sender.h("peer", "v3").hex(), "source": "jip3", "node": "v3"})

        deadline = time.time() + 5
        while time.time() < deadline:
            self.r.rx.loki.flush()
            streams = [s for p in FakeLoki.pushes for s in p["streams"]]
            if sum(len(s["values"]) for s in streams) >= sent + 2:
                break
            time.sleep(0.02)
        self.assertEqual({json.dumps(s["stream"], sort_keys=True) for s in streams},
                         {json.dumps(dict(L, source="jip3"), sort_keys=True)})
        lines = [json.loads(v[1]) for s in streams for v in s["values"]]
        events = [ln["event"] for ln in lines]
        self.assertEqual(events[0], "node_info")
        self.assertEqual(events[-1], "connection_closed")
        self.assertEqual(len(events), sent + 2)
        executed = [ln for ln in lines if ln["event"] == "block_executed"]
        self.assertEqual(executed[0]["block"], 1)                 # links to "authoring", id 1
        self.assertEqual(executed[0]["accumulated"][0]["service"], 100)
        best = next(ln for ln in lines if ln["event"] == "best_block_changed")
        self.assertEqual(len(best["hash"]), 16)                    # hashes: short hex
        ns = int(next(v[0] for s in streams for v in s["values"] if '"best_block_changed"' in v[1]))
        self.assertEqual(ns % 1000, 0)
        self.assertEqual(codec.iso(ns // 1000 - codec.JCE_UNIX * 1_000_000), best["ts"])

    def raw(self, *chunks, read_close=False):
        s = socket.create_connection(("127.0.0.1", self.r.port), timeout=5)
        for c in chunks:
            s.sendall(c)
            time.sleep(0.002)
        if read_close:
            s.settimeout(5)
            return s, s.recv(1)
        return s, None

    def test_framing_split_empty_unknown_truncated_and_oversize(self):
        L = {"node": "polkajam-" + sender.h("peer", "f")[:4].hex()}
        stream = codec.frame(node_info_bytes("f")) + codec.frame(b"") + \
            codec.frame((1).to_bytes(8, "little") + bytes([250]) + b"future fields") + \
            codec.frame(codec.encode_event(11, {"slot": 42, "hash": sender.h("b")}, 5)[:-3]) + \
            codec.frame(codec.encode_event(11, {"slot": 43, "hash": sender.h("b")}, 6))
        chunks = [stream[i:i + 3] for i in range(0, len(stream), 3)]      # split frames
        s, _ = self.raw(*chunks)
        self.assertTrue(self.r.wait(lambda m: value(m, "jam_best_slot", **L) == 43))
        m = self.r.metrics()
        self.assertEqual(value(m, "jip3_decode_errors_total", reason="empty", **L), 1)
        self.assertEqual(value(m, "jip3_decode_errors_total", reason="decode", **L), 1)
        self.assertEqual(value(m, "jip3_events_total", event="unknown_250", **L), 1)
        self.assertEqual(value(m, "jip3_connected", **L), 1)             # still connected
        conn = next(iter(self.r.rx.active))
        self.assertEqual(conn.next_id, 4)                                # every message had an ID
        s.sendall((5000).to_bytes(4, "little"))                          # > max_message
        s.settimeout(5)
        self.assertEqual(s.recv(1), b"")                                 # the receiver hung up
        s.close()
        self.assertTrue(self.r.wait(lambda m: value(m, "jip3_decode_errors_total", reason="oversize", **L) == 1
                                    and value(m, "jip3_connected", **L) == 0))

    def test_every_event_from_the_sender_decodes(self):
        s = sender.Sender("127.0.0.1", self.r.port)
        s.node_info("all")
        s.every_event()
        s.close()
        L = {"node": "jip3-sender-" + sender.h("peer", "all")[:4].hex()}
        self.assertTrue(self.r.wait(lambda m: value(m, "jip3_events_total", **L) == 115))
        m = self.r.metrics()
        self.assertEqual(value(m, "jip3_decode_errors_total", **L), 0)
        names = {dict(lb)["event"] for (n, lb) in m if n == "jip3_events_total" and ("node", L["node"]) in lb}
        self.assertEqual(names, set(codec.NAMES.values()))


if __name__ == "__main__":
    unittest.main()
