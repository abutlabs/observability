"""JIP-3 wire format: the JAM codec pieces it uses, every message it defines, both ways.

JIP-3 (Telemetry, polkadot-fellows/JIPs) messages travel over TCP as `u32 LE length ++
content`, the content JAM-serialized (Gray Paper appendix C): u8/u16/u32/u64 fixed-width
little-endian, `len++` sequences prefixed by the GP variable-length natural. The first
message on a connection is the node information message; every later one is an event:

    Timestamp (u64, microseconds since the JAM Common Era) ++ u8 discriminator ++ fields

This module decodes (`decode_node_info`, `decode_event`) and encodes (`encode_node_info`,
`encode_event`, `frame`) every message of the spec. The encoder is what the synthetic
sender (sender.py) and the tests use; decoding what we encode is the round trip the tests
check for every event.

Values are plain Python: ints, bools, bytes (hashes, peer ids), str, None (Option), lists
and dicts (structs). Enumerations decode to their names ("local", "remote", ...).
"""
import datetime
import ipaddress

# JAM Common Era: 1200 UTC on 1 January 2025 (GP: "the Common Era"), in unix seconds.
JCE_UNIX = 1735732800

PROTOCOL_VERSION = 1


class DecodeError(Exception):
    pass


# ---- reading ---------------------------------------------------------------------------
class Reader:
    def __init__(self, data, off=0, end=None):
        self.data, self.off = data, off
        self.end = len(data) if end is None else end

    def remaining(self):
        return self.end - self.off

    def take(self, n):
        if n < 0 or self.off + n > self.end:
            raise DecodeError("truncated: need %d bytes at offset %d, have %d"
                              % (n, self.off, self.remaining()))
        b = self.data[self.off:self.off + n]
        self.off += n
        return bytes(b)

    def int(self, n):
        return int.from_bytes(self.take(n), "little")

    def natural(self):
        """GP C.6 variable-length natural: a first byte whose leading ones count the
        extra bytes (0..8)."""
        b = self.int(1)
        ones = 0
        while ones < 8 and b & (0x80 >> ones):
            ones += 1
        if ones == 8:
            return self.int(8)
        high = b & (0xff >> (ones + 1))
        return (high << (8 * ones)) | self.int(ones)


def encode_natural(x):
    """GP C.6: [0] for 0; [2^8 - 2^(8-l) + floor(x / 2^(8l))] ++ E_l(x mod 2^(8l)) for the
    l in 0..7 with 2^(7l) <= x < 2^(7(l+1)); [255] ++ E_8(x) above."""
    if x < 0 or x >= 1 << 64:
        raise ValueError("natural out of range: %r" % x)
    for l in range(8):
        if x < 1 << (7 * (l + 1)):
            return bytes([256 - (1 << (8 - l)) + (x >> (8 * l))]) + \
                (x & ((1 << (8 * l)) - 1)).to_bytes(l, "little")
    return b"\xff" + x.to_bytes(8, "little")


# ---- types -----------------------------------------------------------------------------
class T:
    """A codec type: dec(reader, ctx) -> value, enc(value, ctx) -> bytes."""

    def dec(self, r, ctx):
        raise NotImplementedError

    def enc(self, v, ctx):
        raise NotImplementedError


class Int(T):
    def __init__(self, n):
        self.n = n

    def dec(self, r, ctx):
        return r.int(self.n)

    def enc(self, v, ctx):
        return int(v).to_bytes(self.n, "little")


class Bool(T):
    def dec(self, r, ctx):
        b = r.int(1)
        if b > 1:
            raise DecodeError("bool byte %d" % b)
        return bool(b)

    def enc(self, v, ctx):
        return b"\x01" if v else b"\x00"


class Fixed(T):
    """[u8; n]: bytes."""

    def __init__(self, n):
        self.n = n

    def dec(self, r, ctx):
        return r.take(self.n)

    def enc(self, v, ctx):
        v = bytes(v)
        if len(v) != self.n:
            raise ValueError("want %d bytes, got %d" % (self.n, len(v)))
        return v


class Str(T):
    """String<N> = len++[u8], len <= N, valid UTF-8."""

    def __init__(self, maxlen):
        self.maxlen = maxlen

    def dec(self, r, ctx):
        n = r.natural()
        if n > self.maxlen:
            raise DecodeError("string of %d bytes exceeds String<%d>" % (n, self.maxlen))
        try:
            return r.take(n).decode("utf-8")
        except UnicodeDecodeError as e:
            raise DecodeError("string not UTF-8: %s" % e)

    def enc(self, v, ctx):
        b = v.encode("utf-8")
        if len(b) > self.maxlen:
            raise ValueError("string of %d bytes exceeds String<%d>" % (len(b), self.maxlen))
        return encode_natural(len(b)) + b


class Opt(T):
    def __init__(self, t):
        self.t = t

    def dec(self, r, ctx):
        tag = r.int(1)
        if tag == 0:
            return None
        if tag != 1:
            raise DecodeError("Option tag %d" % tag)
        return self.t.dec(r, ctx)

    def enc(self, v, ctx):
        return b"\x00" if v is None else b"\x01" + self.t.enc(v, ctx)


class Seq(T):
    """len++[T]"""

    def __init__(self, t):
        self.t = t

    def dec(self, r, ctx):
        n = r.natural()
        if n > r.remaining():                 # every element is at least one byte
            raise DecodeError("sequence of %d elements in %d bytes" % (n, r.remaining()))
        return [self.t.dec(r, ctx) for _ in range(n)]

    def enc(self, v, ctx):
        return encode_natural(len(v)) + b"".join(self.t.enc(x, ctx) for x in v)


class Struct(T):
    def __init__(self, *fields):
        self.fields = fields                  # (name, type)

    def dec(self, r, ctx):
        return {name: t.dec(r, ctx) for name, t in self.fields}

    def enc(self, v, ctx):
        return b"".join(t.enc(v[name], ctx) for name, t in self.fields)


class Enum8(T):
    """A single-byte enumeration, decoded to its name (an unknown value stays an int)."""

    def __init__(self, *names):
        self.names = names

    def dec(self, r, ctx):
        b = r.int(1)
        return self.names[b] if b < len(self.names) else b

    def enc(self, v, ctx):
        return bytes([self.names.index(v) if isinstance(v, str) else int(v)])


class PerCore(T):
    """A per-core array whose length is set by the core count C: `[u8; C]` (bits=False)
    or the bitfield `[u8; ceil(C / 8)]` (bits=True). When the node's C is unknown the
    length comes from the message: everything but the `tail` bytes of fixed fields that
    follow. When C is known (node info parameters) the two must agree; a disagreement is
    noted in ctx["warnings"] and the message length wins."""

    def __init__(self, bits, tail):
        self.bits, self.tail = bits, tail

    def _want(self, cores):
        return (cores + 7) // 8 if self.bits else cores

    def dec(self, r, ctx):
        n = r.remaining() - self.tail
        if n < 0:
            raise DecodeError("per-core array: message too short")
        cores = (ctx or {}).get("cores")
        if cores is not None and n != self._want(cores):
            (ctx.setdefault("warnings", [])).append(
                "per-core array of %d bytes, core count %d wants %d" % (n, cores, self._want(cores)))
        b = r.take(n)
        return b if self.bits else list(b)

    def enc(self, v, ctx):
        return bytes(v)


U8, U16, U32, U64 = Int(1), Int(2), Int(4), Int(8)
BOOL = Bool()
HASH = Fixed(32)
PEER_ID = Fixed(32)
VRF = Fixed(32)
REASON = Str(128)
EVENT_ID = U64
SLOT = U32
EPOCH = U32
CORE = U16
SERVICE = U32
SHARD = U16
GAS = U64
SIDE = Enum8("local", "remote")                                 # Connection Side
DIRECTION = Enum8("ascending_exclusive", "descending_inclusive")
GUARANTEE_DISCARD = Enum8("reported_on_chain", "replaced_by_better", "cannot_be_reported",
                          "too_many_guarantees", "other")
PREIMAGE_FORGET = Enum8("provided_on_chain", "not_requested_on_chain", "failed_to_acquire",
                        "too_many_announced", "bad_length", "other")
PREIMAGE_DISCARD = Enum8("provided_on_chain", "not_requested_on_chain", "too_many_preimages",
                         "other")


class PeerAddress(T):
    """[u8; 16] (IPv6) ++ u16 (port): decoded to {"ip": str, "port": int}; an IPv4-mapped
    address reads as plain IPv4."""

    def dec(self, r, ctx):
        ip = ipaddress.IPv6Address(r.take(16))
        port = r.int(2)
        return {"ip": str(ip.ipv4_mapped or ip), "port": port}

    def enc(self, v, ctx):
        ip = ipaddress.ip_address(v["ip"])
        if ip.version == 4:
            ip = ipaddress.IPv6Address("::ffff:" + str(ip))
        return ip.packed + int(v["port"]).to_bytes(2, "little")


PEER_ADDR = PeerAddress()

BLOCK_OUTLINE = Struct(("size", U32), ("hash", HASH), ("tickets", U32), ("preimages", U32),
                       ("preimages_size", U32), ("guarantees", U32), ("assurances", U32),
                       ("dispute_verdicts", U32))
EXEC_COST = Struct(("gas", GAS), ("ns", U64))
IS_AUTHORIZED_COST = Struct(("total", EXEC_COST), ("compile_ns", U64), ("host_calls", EXEC_COST))
REFINE_COST = Struct(("total", EXEC_COST), ("compile_ns", U64),
                     ("historical_lookup", EXEC_COST), ("machine_expunge", EXEC_COST),
                     ("peek_poke_pages", EXEC_COST), ("invoke", EXEC_COST),
                     ("other_host_calls", EXEC_COST))
ACCUMULATE_COST = Struct(("calls", U32), ("transfers", U32), ("items", U32),
                         ("total", EXEC_COST), ("compile_ns", U64),
                         ("read_write", EXEC_COST), ("lookup", EXEC_COST),
                         ("query_solicit_forget_provide", EXEC_COST),
                         ("info_new_upgrade_eject", EXEC_COST), ("transfer", EXEC_COST),
                         ("transfer_processing_gas", GAS), ("other_host_calls", EXEC_COST))
IMPORT_SPEC = Struct(("root", HASH), ("index", U16))
WORK_ITEM_OUTLINE = Struct(("service", SERVICE), ("payload_size", U32),
                           ("refine_gas_limit", GAS), ("accumulate_gas_limit", GAS),
                           ("extrinsics_size", U32), ("imports", Seq(IMPORT_SPEC)),
                           ("exports", U16))
WORK_PACKAGE_OUTLINE = Struct(("size", U32), ("hash", HASH), ("anchor", HASH),
                              ("lookup_anchor_slot", SLOT), ("prerequisites", Seq(HASH)),
                              ("items", Seq(WORK_ITEM_OUTLINE)))
WORK_REPORT_OUTLINE = Struct(("hash", HASH), ("bundle_size", U32), ("erasure_root", HASH),
                             ("segments_root", HASH))
GUARANTEE_OUTLINE = Struct(("report", HASH), ("slot", SLOT), ("guarantors", Seq(U16)))
ACCUMULATED = Struct(("service", SERVICE), ("cost", ACCUMULATE_COST))
SEGMENT_SHARD = Struct(("segment", U16), ("shard", U16))


# ---- the events ------------------------------------------------------------------------
# discriminator -> (name, [(field, type), ...]), in the spec's order
EVENTS = {
    # meta
    0: ("dropped", [("last_timestamp", U64), ("count", U64)]),
    # status
    10: ("status", [("peers", U32), ("validator_peers", U32), ("block_announcement_peers", U32),
                    ("guarantees_per_core", PerCore(False, 20)), ("shards", U32),
                    ("shards_size", U64), ("preimages", U32), ("preimages_size", U32)]),
    11: ("best_block_changed", [("slot", SLOT), ("hash", HASH)]),
    12: ("finalized_block_changed", [("slot", SLOT), ("hash", HASH)]),
    13: ("sync_status_changed", [("synced", BOOL)]),
    # networking
    20: ("connection_refused", [("peer_address", PEER_ADDR)]),
    21: ("connecting_in", [("peer_address", PEER_ADDR)]),
    22: ("connect_in_failed", [("connecting_in", EVENT_ID), ("reason", REASON)]),
    23: ("connected_in", [("connecting_in", EVENT_ID), ("peer", PEER_ID)]),
    24: ("connecting_out", [("peer", PEER_ID), ("peer_address", PEER_ADDR)]),
    25: ("connect_out_failed", [("connecting_out", EVENT_ID), ("reason", REASON)]),
    26: ("connected_out", [("connecting_out", EVENT_ID)]),
    27: ("disconnected", [("peer", PEER_ID), ("terminator", Opt(SIDE)), ("reason", REASON)]),
    28: ("peer_misbehaved", [("peer", PEER_ID), ("reason", REASON)]),
    # block authoring/importing
    40: ("authoring", [("slot", SLOT), ("parent", HASH)]),
    41: ("authoring_failed", [("authoring", EVENT_ID), ("reason", REASON)]),
    42: ("authored", [("authoring", EVENT_ID), ("outline", BLOCK_OUTLINE)]),
    43: ("importing", [("slot", SLOT), ("outline", BLOCK_OUTLINE)]),
    44: ("block_verification_failed", [("importing", EVENT_ID), ("reason", REASON)]),
    45: ("block_verified", [("importing", EVENT_ID)]),
    46: ("block_execution_failed", [("block", EVENT_ID), ("reason", REASON)]),
    47: ("block_executed", [("block", EVENT_ID), ("accumulated", Seq(ACCUMULATED))]),
    # block distribution
    60: ("block_announcement_stream_opened", [("peer", PEER_ID), ("opener", SIDE)]),
    61: ("block_announcement_stream_closed", [("opened", EVENT_ID), ("closer", SIDE),
                                             ("reason", REASON)]),
    62: ("block_announced", [("peer", PEER_ID), ("announcer", SIDE), ("slot", SLOT),
                             ("hash", HASH)]),
    63: ("sending_block_request", [("peer", PEER_ID), ("hash", HASH), ("direction", DIRECTION),
                                   ("max_blocks", U32)]),
    64: ("receiving_block_request", [("peer", PEER_ID)]),
    65: ("block_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    66: ("block_request_sent", [("request", EVENT_ID)]),
    67: ("block_request_received", [("request", EVENT_ID), ("hash", HASH),
                                    ("direction", DIRECTION), ("max_blocks", U32)]),
    68: ("block_transferred", [("request", EVENT_ID), ("slot", SLOT), ("outline", BLOCK_OUTLINE),
                               ("last", BOOL)]),
    # safrole tickets
    80: ("generating_tickets", [("epoch", EPOCH)]),
    81: ("ticket_generation_failed", [("generating", EVENT_ID), ("reason", REASON)]),
    82: ("tickets_generated", [("generating", EVENT_ID), ("vrf_outputs", Seq(VRF))]),
    83: ("ticket_transfer_failed", [("peer", PEER_ID), ("sender", SIDE), ("ce132", BOOL),
                                    ("reason", REASON)]),
    84: ("ticket_transferred", [("peer", PEER_ID), ("sender", SIDE), ("ce132", BOOL),
                                ("epoch", EPOCH), ("attempt", U8), ("vrf_output", VRF)]),
    # guaranteeing
    90: ("work_package_submission", [("builder", PEER_ID), ("ce146", BOOL)]),
    91: ("work_package_being_shared", [("primary", PEER_ID)]),
    92: ("work_package_failed", [("package", EVENT_ID), ("reason", REASON)]),
    93: ("duplicate_work_package", [("package", EVENT_ID), ("core", CORE), ("hash", HASH)]),
    94: ("work_package_received", [("package", EVENT_ID), ("core", CORE),
                                   ("outline", WORK_PACKAGE_OUTLINE)]),
    95: ("authorized", [("package", EVENT_ID), ("cost", IS_AUTHORIZED_COST)]),
    96: ("extrinsic_data_received", [("package", EVENT_ID)]),
    97: ("imports_received", [("package", EVENT_ID)]),
    98: ("sharing_work_package", [("package", EVENT_ID), ("secondary", PEER_ID)]),
    99: ("work_package_sharing_failed", [("package", EVENT_ID), ("secondary", PEER_ID),
                                         ("reason", REASON)]),
    100: ("bundle_sent", [("package", EVENT_ID), ("secondary", PEER_ID)]),
    101: ("refined", [("package", EVENT_ID), ("costs", Seq(REFINE_COST))]),
    102: ("work_report_built", [("package", EVENT_ID), ("outline", WORK_REPORT_OUTLINE)]),
    103: ("work_report_signature_sent", [("package", EVENT_ID)]),
    104: ("work_report_signature_received", [("package", EVENT_ID), ("secondary", PEER_ID)]),
    105: ("guarantee_built", [("package", EVENT_ID), ("outline", GUARANTEE_OUTLINE)]),
    106: ("sending_guarantee", [("guarantee", EVENT_ID), ("recipient", PEER_ID)]),
    107: ("guarantee_send_failed", [("sending", EVENT_ID), ("reason", REASON)]),
    108: ("guarantee_sent", [("sending", EVENT_ID)]),
    109: ("guarantees_distributed", [("package", EVENT_ID)]),
    110: ("receiving_guarantee", [("sender", PEER_ID)]),
    111: ("guarantee_receive_failed", [("receiving", EVENT_ID), ("reason", REASON)]),
    112: ("guarantee_received", [("receiving", EVENT_ID), ("outline", GUARANTEE_OUTLINE)]),
    113: ("guarantee_discarded", [("outline", GUARANTEE_OUTLINE), ("reason", GUARANTEE_DISCARD)]),
    # availability distribution
    120: ("sending_shard_request", [("guarantor", PEER_ID), ("erasure_root", HASH),
                                    ("shard", SHARD)]),
    121: ("receiving_shard_request", [("assurer", PEER_ID)]),
    122: ("shard_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    123: ("shard_request_sent", [("request", EVENT_ID)]),
    124: ("shard_request_received", [("request", EVENT_ID), ("erasure_root", HASH),
                                     ("shard", SHARD)]),
    125: ("shards_transferred", [("request", EVENT_ID)]),
    126: ("distributing_assurance", [("anchor", HASH), ("bitfield", PerCore(True, 0))]),
    127: ("assurance_send_failed", [("distributing", EVENT_ID), ("recipient", PEER_ID),
                                    ("reason", REASON)]),
    128: ("assurance_sent", [("distributing", EVENT_ID), ("recipient", PEER_ID)]),
    129: ("assurance_distributed", [("distributing", EVENT_ID)]),
    130: ("assurance_receive_failed", [("sender", PEER_ID), ("reason", REASON)]),
    131: ("assurance_received", [("sender", PEER_ID), ("anchor", HASH)]),
    # bundle recovery
    140: ("sending_bundle_shard_request", [("audit", EVENT_ID), ("assurer", PEER_ID),
                                           ("shard", SHARD)]),
    141: ("receiving_bundle_shard_request", [("auditor", PEER_ID)]),
    142: ("bundle_shard_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    143: ("bundle_shard_request_sent", [("request", EVENT_ID)]),
    144: ("bundle_shard_request_received", [("request", EVENT_ID), ("erasure_root", HASH),
                                            ("shard", SHARD)]),
    145: ("bundle_shard_transferred", [("request", EVENT_ID)]),
    146: ("reconstructing_bundle", [("audit", EVENT_ID), ("trivial", BOOL)]),
    147: ("bundle_reconstructed", [("audit", EVENT_ID)]),
    148: ("sending_bundle_request", [("audit", EVENT_ID), ("guarantor", PEER_ID)]),
    149: ("receiving_bundle_request", [("auditor", PEER_ID)]),
    150: ("bundle_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    151: ("bundle_request_sent", [("request", EVENT_ID)]),
    152: ("bundle_request_received", [("request", EVENT_ID), ("erasure_root", HASH)]),
    153: ("bundle_transferred", [("request", EVENT_ID)]),
    # segment recovery
    160: ("work_package_hash_mapped", [("package", EVENT_ID), ("work_package", HASH),
                                       ("segments_root", HASH)]),
    161: ("segments_root_mapped", [("package", EVENT_ID), ("segments_root", HASH),
                                   ("erasure_root", HASH)]),
    162: ("sending_segment_shard_request", [("package", EVENT_ID), ("assurer", PEER_ID),
                                            ("ce140", BOOL), ("shards", Seq(SEGMENT_SHARD))]),
    163: ("receiving_segment_shard_request", [("sender", PEER_ID), ("ce140", BOOL)]),
    164: ("segment_shard_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    165: ("segment_shard_request_sent", [("request", EVENT_ID)]),
    166: ("segment_shard_request_received", [("request", EVENT_ID), ("count", U16)]),
    167: ("segment_shards_transferred", [("request", EVENT_ID)]),
    168: ("reconstructing_segments", [("package", EVENT_ID), ("segments", Seq(U16)),
                                      ("trivial", BOOL)]),
    169: ("segment_reconstruction_failed", [("reconstructing", EVENT_ID), ("reason", REASON)]),
    170: ("segments_reconstructed", [("reconstructing", EVENT_ID)]),
    171: ("segment_verification_failed", [("package", EVENT_ID), ("segments", Seq(U16)),
                                          ("reason", REASON)]),
    172: ("segments_verified", [("package", EVENT_ID), ("segments", Seq(U16))]),
    173: ("sending_segment_request", [("package", EVENT_ID), ("previous_guarantor", PEER_ID),
                                      ("segments", Seq(U16))]),
    174: ("receiving_segment_request", [("guarantor", PEER_ID)]),
    175: ("segment_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    176: ("segment_request_sent", [("request", EVENT_ID)]),
    177: ("segment_request_received", [("request", EVENT_ID), ("count", U16)]),
    178: ("segments_transferred", [("request", EVENT_ID)]),
    # preimage distribution
    190: ("preimage_announcement_failed", [("peer", PEER_ID), ("announcer", SIDE),
                                           ("reason", REASON)]),
    191: ("preimage_announced", [("peer", PEER_ID), ("announcer", SIDE), ("service", SERVICE),
                                 ("hash", HASH), ("length", U32)]),
    192: ("announced_preimage_forgotten", [("service", SERVICE), ("hash", HASH),
                                           ("length", U32), ("reason", PREIMAGE_FORGET)]),
    193: ("sending_preimage_request", [("peer", PEER_ID), ("hash", HASH)]),
    194: ("receiving_preimage_request", [("peer", PEER_ID)]),
    195: ("preimage_request_failed", [("request", EVENT_ID), ("reason", REASON)]),
    196: ("preimage_request_sent", [("request", EVENT_ID)]),
    197: ("preimage_request_received", [("request", EVENT_ID), ("hash", HASH)]),
    198: ("preimage_transferred", [("request", EVENT_ID), ("length", U32)]),
    199: ("preimage_discarded", [("hash", HASH), ("length", U32), ("reason", PREIMAGE_DISCARD)]),
}

NAMES = {d: name for d, (name, _) in EVENTS.items()}
BY_NAME = {name: d for d, name in NAMES.items()}


class Event:
    """One decoded event: its discriminator, name, timestamp (us since the JCE), fields."""
    __slots__ = ("disc", "name", "ts", "fields", "warnings")

    def __init__(self, disc, name, ts, fields, warnings=None):
        self.disc, self.name, self.ts, self.fields = disc, name, ts, fields
        self.warnings = warnings or []

    def __repr__(self):
        return "Event(%d %s ts=%d %r)" % (self.disc, self.name, self.ts, self.fields)


class UnknownEvent(DecodeError):
    """An event whose discriminator the spec does not define (yet). The frame's length
    delimits it, so the stream stays in step; the caller counts and skips it."""

    def __init__(self, disc, ts):
        super().__init__("unknown event discriminator %d" % disc)
        self.disc, self.ts = disc, ts


def decode_event(content, cores=None):
    """An Event from one message's content (the frame without its length prefix).
    `cores`: the node's core count C from its node info, if known."""
    r = Reader(content)
    ts = r.int(8)
    disc = r.int(1)
    if disc not in EVENTS:
        raise UnknownEvent(disc, ts)
    name, fields = EVENTS[disc]
    ctx = {"cores": cores}
    values = {f: t.dec(r, ctx) for f, t in fields}
    if r.remaining():
        raise DecodeError("%s: %d trailing bytes" % (name, r.remaining()))
    return Event(disc, name, ts, values, ctx.get("warnings"))


def encode_event(disc, fields, ts, cores=None):
    """The content bytes of an event message (frame() adds the length)."""
    if isinstance(disc, str):
        disc = BY_NAME[disc]
    _, spec = EVENTS[disc]
    ctx = {"cores": cores}
    return ts.to_bytes(8, "little") + bytes([disc]) + b"".join(t.enc(fields[f], ctx)
                                                                for f, t in spec)


def frame(content):
    return len(content).to_bytes(4, "little") + content


def unframe(buf):
    """Split a byte buffer into complete message contents: (contents, rest)."""
    out, off = [], 0
    while len(buf) - off >= 4:
        n = int.from_bytes(buf[off:off + 4], "little")
        if len(buf) - off - 4 < n:
            break
        out.append(bytes(buf[off + 4:off + 4 + n]))
        off += 4 + n
    return out, bytes(buf[off:])


# ---- the node information message -------------------------------------------------------
# JAM Parameters = the GP fetch host call, case 0 (appendix B, Omega_Y). Field names are the
# GP symbols; the widths are the encoding's. GP 0.7.0-0.7.2: 134 bytes; GP 0.8.0: 122 (N,
# V, W_E and W_P left the list).
_P07 = [("B_I", 8), ("B_L", 8), ("B_S", 8), ("C", 2), ("D", 4), ("E", 4), ("G_A", 8),
        ("G_I", 8), ("G_R", 8), ("G_T", 8), ("H", 2), ("I", 2), ("J", 2), ("K", 2), ("L", 4),
        ("N", 2), ("O", 2), ("P", 2), ("Q", 2), ("R", 2), ("T", 2), ("U", 2), ("V", 2),
        ("W_A", 4), ("W_B", 4), ("W_C", 4), ("W_E", 4), ("W_M", 4), ("W_P", 4), ("W_R", 4),
        ("W_T", 4), ("W_X", 4), ("Y", 4)]
_P08 = [f for f in _P07 if f[0] not in ("N", "V", "W_E", "W_P")]
PARAM_LAYOUTS = {"gp-0.7": _P07, "gp-0.8": _P08}
PARAM_SIZES = {k: sum(w for _, w in v) for k, v in PARAM_LAYOUTS.items()}   # 134, 122
CORE_COUNT_OFFSET = 24          # C follows B_I, B_L, B_S in every layout since GP 0.6

# the tiny and full configurations' core counts (spec label of jam_node_info)
SPEC_BY_CORES = {2: "tiny", 341: "full"}

NODE_INFO_TAIL = Struct(("genesis", HASH), ("peer_id", PEER_ID), ("peer_address", PEER_ADDR),
                        ("flags", U32), ("impl_name", Str(32)), ("impl_version", Str(32)),
                        ("gp_version", Str(16)), ("note", Str(512)))


def decode_params(raw, layout):
    r = Reader(raw)
    return {name: r.int(w) for name, w in PARAM_LAYOUTS[layout]}


def encode_params(values, layout):
    return b"".join(int(values.get(name, 0)).to_bytes(w, "little")
                    for name, w in PARAM_LAYOUTS[layout])


def _tail_at(content, off):
    """The node info fields after the parameters, if they parse to exactly the end."""
    try:
        r = Reader(content, off)
        v = NODE_INFO_TAIL.dec(r, {})
        return v if r.remaining() == 0 else None
    except DecodeError:
        return None


def decode_node_info(content, max_search=4096):
    """The node information message as a dict:

        version, params_raw, params_len, params_layout ("gp-0.7", "gp-0.8" or "unknown"),
        params (the named fields when the layout is known), cores (C or None), genesis,
        peer_id, peer_address, flags, impl_name, impl_version, gp_version, note

    The parameters have no length prefix, so their length is found: each known layout is
    tried; when more than one leaves a clean parse of the rest, the GP version string
    decides; when none does, every length up to `max_search` is tried and the unique one
    that parses cleanly (preferring a GP-version-looking string) wins."""
    if not content:
        raise DecodeError("empty node info")
    version = content[0]
    clean = {}
    for layout, size in PARAM_SIZES.items():
        tail = _tail_at(content, 1 + size)
        if tail is not None:
            clean[layout] = (size, tail)
    layout = None
    if len(clean) == 1:
        layout = next(iter(clean))
    elif len(clean) > 1:
        for name, (_, tail) in clean.items():
            if tail["gp_version"].startswith(name[3:]):
                layout = name
        layout = layout or sorted(clean)[-1]
    if layout:
        size, tail = clean[layout]
    else:
        found = []
        for size in range(0, min(len(content) - 1, max_search) + 1):
            tail = _tail_at(content, 1 + size)
            if tail is not None:
                found.append((size, tail))
        if not found:
            raise DecodeError("node info: no parameter length leaves a valid message "
                              "(%d bytes)" % len(content))
        versionish = [f for f in found if f[1]["gp_version"][:1].isdigit()]
        size, tail = (versionish or found)[0]
        layout = "unknown"
    raw = content[1:1 + size]
    out = {"version": version, "params_raw": raw, "params_len": size, "params_layout": layout,
           "params": decode_params(raw, layout) if layout in PARAM_LAYOUTS else None,
           "cores": int.from_bytes(raw[CORE_COUNT_OFFSET:CORE_COUNT_OFFSET + 2], "little")
           if layout in PARAM_LAYOUTS else None}
    out.update(tail)
    return out


def encode_node_info(genesis, peer_id, peer_address, impl_name, impl_version, gp_version,
                     note="", flags=0, params=None, layout="gp-0.8", params_raw=None,
                     version=PROTOCOL_VERSION):
    """The node information message content. params: {GP symbol: value} (missing -> 0);
    params_raw overrides with literal bytes (to test layouts this module doesn't know)."""
    raw = params_raw if params_raw is not None else encode_params(params or {}, layout)
    return bytes([version]) + raw + NODE_INFO_TAIL.enc(
        {"genesis": genesis, "peer_id": peer_id, "peer_address": peer_address, "flags": flags,
         "impl_name": impl_name, "impl_version": impl_version, "gp_version": gp_version,
         "note": note}, {})


# ---- time ------------------------------------------------------------------------------
def unix_from_jce_us(ts):
    return JCE_UNIX + ts / 1e6


def unix_ns_from_jce_us(ts):
    """Unix nanoseconds of a JIP-3 timestamp, exactly (Loki's timestamps)."""
    return (JCE_UNIX * 1_000_000 + ts) * 1000


def jce_us_from_unix(t):
    return int(round((t - JCE_UNIX) * 1e6))


def iso(ts):
    """ISO 8601 UTC of a JIP-3 timestamp (microseconds)."""
    return datetime.datetime.fromtimestamp(unix_from_jce_us(ts), datetime.timezone.utc) \
        .isoformat(timespec="microseconds").replace("+00:00", "Z")


# ---- presentation ----------------------------------------------------------------------
def to_json(v, hexlen=16, max_items=16):
    """A JSON-friendly copy of a decoded value: bytes as short hex (the first `hexlen` hex
    digits: enough to correlate hashes and peers across nodes), long lists cut to
    `max_items` with the full count kept."""
    if isinstance(v, (bytes, bytearray)):
        h = bytes(v).hex()
        return h[:hexlen] if hexlen and len(h) > hexlen else h
    if isinstance(v, dict):
        return {k: to_json(x, hexlen, max_items) for k, x in v.items()}
    if isinstance(v, list):
        if len(v) > max_items:
            return {"count": len(v), "first": [to_json(x, hexlen, max_items) for x in v[:max_items]]}
        return [to_json(x, hexlen, max_items) for x in v]
    return v
