#!/usr/bin/env python3
"""A synthetic JIP-3 sender: a stand-in JAM node for testing a telemetry server.

    python3 jip3/sender.py [--host 127.0.0.1] [--port 9910] [--node-name sender-0]
                           [--count 1] [--rate 50] [--every-event] [--hold SECS]

It connects, sends the node information message (GP 0.8.0 parameters, tiny: 2 cores)
and then, `--count` times (0: until interrupted), one scripted round of a busy
validator's life, each event encoded from the spec (codec.py):

    status; authoring -> authored -> block executed; best block changed;
    importing -> block verified -> block executed; finalized block changed;
    the whole primary guarantor pipeline (90, 94, 95, 96, 97, 98, 100, 101, 102, 104,
    105, 106, 108, 109); a duplicate (93) and a failed (92) work-package; an assurance
    (126, 128, 129); tickets (80, 82, 84); and every fifth round a "dropped" event.

`--every-event` sends one of every event the spec defines instead (sample values), the
interop check for a receiver's decoder. `--rate` paces events per second (0: flat out).
Event IDs follow the spec: one per event, plus the count after a "dropped" event.
"""
import argparse
import hashlib
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codec  # noqa: E402

# the tiny configuration's parameters (GP 0.8.0 symbols; the 0.7 layout's extra four too)
TINY = {"B_I": 10, "B_L": 1, "B_S": 100, "C": 2, "D": 32, "E": 12, "G_A": 10_000_000,
        "G_I": 50_000_000, "G_R": 1_000_000_000, "G_T": 20_000_000, "H": 8, "I": 16, "J": 8,
        "K": 3, "L": 24, "N": 3, "O": 8, "P": 6, "Q": 80, "R": 4, "T": 128, "U": 5, "V": 6,
        "W_A": 64_000, "W_B": 13_794_305, "W_C": 4_000_000, "W_E": 4, "W_M": 3072, "W_P": 1026,
        "W_R": 49_152, "W_T": 128, "W_X": 3072, "Y": 10}


def h(*parts):
    """A deterministic 32-byte value (a stand-in hash, peer id or VRF output)."""
    return hashlib.blake2b("/".join(str(p) for p in parts).encode(), digest_size=32).digest()


def sample(t, cores=2, seed=0):
    """A sample value of codec type t (for --every-event and the tests' round trips)."""
    if isinstance(t, codec.Int):
        return (seed * 7919 + 13) % (1 << (8 * t.n))
    if isinstance(t, codec.Bool):
        return bool(seed % 2)
    if isinstance(t, codec.Fixed):
        return h("sample", seed, t.n)[:t.n] if t.n <= 32 else (h("sample", seed) * 2)[:t.n]
    if isinstance(t, codec.Str):
        return ("sample reason %d, ünïcode" % seed)[:t.maxlen]
    if isinstance(t, codec.Opt):
        return sample(t.t, cores, seed)
    if isinstance(t, codec.Seq):
        return [sample(t.t, cores, seed + i) for i in range(2)]
    if isinstance(t, codec.Struct):
        return {name: sample(ft, cores, seed + i) for i, (name, ft) in enumerate(t.fields)}
    if isinstance(t, codec.Enum8):
        return t.names[seed % len(t.names)]
    if isinstance(t, codec.PerCore):
        return bytes([0b1] * ((cores + 7) // 8)) if t.bits else [seed % 4] * cores
    if isinstance(t, codec.PeerAddress):
        return {"ip": "10.0.0.%d" % (seed % 250 + 1), "port": 40000 + seed % 1000}
    raise TypeError(t)


def sample_event(disc, cores=2, seed=0):
    _, fields = codec.EVENTS[disc]
    return {name: sample(t, cores, seed + i) for i, (name, t) in enumerate(fields)}


def outline(tag, size=4096):
    return {"size": size, "hash": h("block", tag), "tickets": 0, "preimages": 0,
            "preimages_size": 0, "guarantees": 1, "assurances": 2, "dispute_verdicts": 0}


def exec_cost(gas, ms):
    return {"gas": gas, "ns": int(ms * 1e6)}


REFINE = {"total": exec_cost(1_500_000, 250), "compile_ns": 2_000_000,
          "historical_lookup": exec_cost(0, 0), "machine_expunge": exec_cost(0, 0),
          "peek_poke_pages": exec_cost(0, 0), "invoke": exec_cost(0, 0),
          "other_host_calls": exec_cost(20_000, 1)}
ACCUMULATE = {"calls": 1, "transfers": 0, "items": 1, "total": exec_cost(300_000, 4),
              "compile_ns": 1_000_000, "read_write": exec_cost(5000, 0.1),
              "lookup": exec_cost(0, 0), "query_solicit_forget_provide": exec_cost(0, 0),
              "info_new_upgrade_eject": exec_cost(0, 0), "transfer": exec_cost(0, 0),
              "transfer_processing_gas": 0, "other_host_calls": exec_cost(1000, 0.01)}


class Sender:
    """One telemetry connection, numbering its events as the spec does."""

    def __init__(self, host, port, rate=0.0, cores=2, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.rate, self.cores = rate, cores
        self.next_id = 0
        self.sent = 0

    def raw(self, content):
        self.sock.sendall(codec.frame(content))

    def node_info(self, name, impl="jip3-sender", impl_version="0.1.0", gp="0.8.0",
                  layout="gp-0.8", jamnp=("127.0.0.1", 40000), note=None, params=None):
        self.raw(codec.encode_node_info(
            genesis=h("genesis"), peer_id=h("peer", name), peer_address={"ip": jamnp[0], "port": jamnp[1]},
            impl_name=impl, impl_version=impl_version, gp_version=gp, layout=layout,
            params=dict(params or TINY, C=self.cores), note=note if note is not None else "node=%s" % name))

    def send(self, name, fields, ts=None):
        """Send one event; returns its event ID."""
        ts = codec.jce_us_from_unix(time.time()) if ts is None else ts
        self.raw(codec.encode_event(name, fields, ts, self.cores))
        eid = self.next_id
        self.next_id += fields["count"] if name == "dropped" else 1
        self.sent += 1
        if self.rate:
            time.sleep(1.0 / self.rate)
        return eid

    def close(self):
        self.sock.close()

    def round(self, i, name="sender"):
        """One scripted round (see the module doc); event times follow a plausible
        schedule from now, in microseconds."""
        base = codec.jce_us_from_unix(time.time())
        slot = int((time.time() - codec.JCE_UNIX) // 6)
        at = lambda ms: base + int(ms * 1000)   # noqa: E731
        peer = h("peer", name, "neighbour")
        s = self.send
        s("status", {"peers": 5, "validator_peers": 4, "block_announcement_peers": 5,
                     "guarantees_per_core": [1] * self.cores, "shards": 10, "shards_size": 40960,
                     "preimages": 0, "preimages_size": 0}, at(0))
        a = s("authoring", {"slot": slot, "parent": h("block", slot - 1)}, at(1))
        s("authored", {"authoring": a, "outline": outline(slot)}, at(20))
        s("block_executed", {"block": a, "accumulated": [{"service": 100, "cost": ACCUMULATE}]}, at(41))
        s("best_block_changed", {"slot": slot, "hash": h("block", slot)}, at(42))
        imp = s("importing", {"slot": slot + 1, "outline": outline(slot + 1)}, at(100))
        s("block_verified", {"importing": imp}, at(115))
        s("block_executed", {"block": imp, "accumulated": []}, at(130))
        s("finalized_block_changed", {"slot": slot - 2, "hash": h("block", slot - 2)}, at(131))
        wp = s("work_package_submission", {"builder": h("builder"), "ce146": False}, at(200))
        s("work_package_received", {"package": wp, "core": 0, "outline": {
            "size": 1200, "hash": h("wp", name, i), "anchor": h("block", slot), "lookup_anchor_slot": slot,
            "prerequisites": [], "items": [{
                "service": 100, "payload_size": 512, "refine_gas_limit": 5_000_000,
                "accumulate_gas_limit": 1_000_000, "extrinsics_size": 0,
                "imports": [], "exports": 0}]}}, at(205))
        s("authorized", {"package": wp, "cost": {"total": exec_cost(40_000, 2), "compile_ns": 500_000,
                                                 "host_calls": exec_cost(1000, 0.1)}}, at(225))
        s("extrinsic_data_received", {"package": wp}, at(226))
        s("imports_received", {"package": wp}, at(227))
        s("sharing_work_package", {"package": wp, "secondary": peer}, at(228))
        s("bundle_sent", {"package": wp, "secondary": peer}, at(235))
        s("refined", {"package": wp, "costs": [REFINE]}, at(525))
        s("work_report_built", {"package": wp, "outline": {
            "hash": h("report", name, i), "bundle_size": 1400, "erasure_root": h("er", name, i),
            "segments_root": h("sr", name, i)}}, at(535))
        s("work_report_signature_received", {"package": wp, "secondary": peer}, at(570))
        g = s("guarantee_built", {"package": wp, "outline": {
            "report": h("report", name, i), "slot": slot, "guarantors": [0, 1]}}, at(585))
        sg = s("sending_guarantee", {"guarantee": g, "recipient": peer}, at(586))
        s("guarantee_sent", {"sending": sg}, at(600))
        s("guarantees_distributed", {"package": wp}, at(605))
        dup = s("work_package_submission", {"builder": h("builder"), "ce146": True}, at(700))
        s("duplicate_work_package", {"package": dup, "core": 0, "hash": h("wp", name, i)}, at(702))
        bad = s("work_package_submission", {"builder": h("builder"), "ce146": False}, at(710))
        s("work_package_failed", {"package": bad, "reason": "anchor too old"}, at(715))
        d = s("distributing_assurance", {"anchor": h("block", slot),
                                         "bitfield": bytes([0b01] * ((self.cores + 7) // 8))}, at(800))
        s("assurance_sent", {"distributing": d, "recipient": peer}, at(805))
        s("assurance_distributed", {"distributing": d}, at(810))
        t = s("generating_tickets", {"epoch": slot // 12 + 1}, at(900))
        s("tickets_generated", {"generating": t, "vrf_outputs": [h("vrf", i, 0), h("vrf", i, 1)]}, at(950))
        s("ticket_transferred", {"peer": peer, "sender": "remote", "ce132": False, "epoch": slot // 12 + 1,
                                 "attempt": 0, "vrf_output": h("vrf", "in", i)}, at(960))
        if i % 5 == 4:
            s("dropped", {"last_timestamp": at(990), "count": 3}, at(970))

    def every_event(self):
        for disc in sorted(codec.EVENTS):
            name = codec.NAMES[disc]
            fields = sample_event(disc, self.cores, disc)
            if name == "dropped":
                fields["count"] = 1
            self.send(name, fields)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9910)
    ap.add_argument("--node-name", default="sender-0", help="seeds the peer id; sent in the note")
    ap.add_argument("--impl", default="jip3-sender", help="implementation name in the node info")
    ap.add_argument("--impl-version", default="0.1.0")
    ap.add_argument("--gp", default="0.8.0", help="GP version string in the node info")
    ap.add_argument("--layout", default="gp-0.8", choices=sorted(codec.PARAM_LAYOUTS))
    ap.add_argument("--cores", type=int, default=2)
    ap.add_argument("--jamnp", default="127.0.0.1:40000",
                    help="the node's JAMNP-S address, sent as its Peer Address")
    ap.add_argument("--count", type=int, default=1, help="scripted rounds (0: forever)")
    ap.add_argument("--rate", type=float, default=50, help="events per second (0: flat out)")
    ap.add_argument("--every-event", action="store_true", help="one of every event instead")
    ap.add_argument("--hold", type=float, default=0, help="seconds to stay connected at the end")
    a = ap.parse_args(argv)
    ip, _, port = a.jamnp.rpartition(":")
    s = Sender(a.host, a.port, a.rate, a.cores)
    s.node_info(a.node_name, a.impl, a.impl_version, a.gp, a.layout, (ip, int(port)))
    try:
        if a.every_event:
            s.every_event()
        else:
            i = 0
            while a.count == 0 or i < a.count:
                s.round(i, a.node_name)
                i += 1
        if a.hold:
            time.sleep(a.hold)
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
    print("sent node info + %d events (next event id %d)" % (s.sent, s.next_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
