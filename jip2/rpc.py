"""A JIP-2 client: JSON-RPC 2.0 over a WebSocket, Python stdlib only.

JIP-2 (github.com/polkadot-fellows/JIPs, "Node RPC") is the client-neutral RPC a JAM node
serves, usually on ws://<node>:19800. Parameters are passed by position; binary values
(Blob, Hash) are padded base64 (RFC 4648).

The WebSocket is a minimal RFC 6455 client: text frames out (masked, as a client must),
text, binary and continuation frames in, ping answered with pong, close honoured. Calls
are serialized on one connection, so a client is safe to share between threads. It opens
no subscriptions; a notification that arrives anyway is skipped.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"     # RFC 6455 section 1.3
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 16 << 20                                  # refuse a message larger than 16 MiB
METHOD_NOT_FOUND = -32601                               # JSON-RPC 2.0


class WebSocketError(ConnectionError):
    """The WebSocket failed: bad handshake, protocol violation, or the peer closed."""


class Jip2Error(Exception):
    """The node answered a call with a JSON-RPC error object."""

    def __init__(self, code, message, data=None):
        super().__init__("JIP-2 error %s: %s%s" % (code, message, "" if data is None else " (%s)" % data))
        self.code, self.message, self.data = code, message, data


def accept_key(key):
    """The Sec-WebSocket-Accept value a server must return for this Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()


def _xor(data, mask):
    n = len(data)
    if n == 0:
        return b""
    return (int.from_bytes(data, "big")
            ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")


def encode_frame(opcode, payload, mask=None):
    """One final frame; a client masks every frame it sends (RFC 6455 section 5.3)."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    bit = 0x80 if mask is not None else 0
    if n < 126:
        head += bytes([bit | n])
    elif n < 1 << 16:
        head += bytes([bit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([bit | 127]) + struct.pack(">Q", n)
    return head + payload if mask is None else head + mask + _xor(payload, mask)


class WebSocket:
    """A blocking RFC 6455 client connection (ws:// or wss://)."""

    def __init__(self, url, timeout=5.0):
        u = urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise WebSocketError("not a WebSocket URL: %s" % url)
        host, port = u.hostname, u.port or (443 if u.scheme == "wss" else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            if u.scheme == "wss":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            sock.settimeout(timeout)
            self._sock, self._buf = sock, b""
            self._handshake(u, host, port)
        except BaseException:
            sock.close()
            raise

    def _handshake(self, u, host, port):
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + ("?%s" % u.query if u.query else "")
        default_port = port == (443 if u.scheme == "wss" else 80)
        host = "[%s]" % host if ":" in host else host
        self._sock.sendall((
            "GET %s HTTP/1.1\r\nHost: %s\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
            % (path, host if default_port else "%s:%d" % (host, port), key)).encode())
        while b"\r\n\r\n" not in self._buf:
            if len(self._buf) > 16384:
                raise WebSocketError("handshake response headers too long")
            self._fill()
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            raise WebSocketError("handshake refused: %s" % lines[0])
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise WebSocketError("handshake: bad Sec-WebSocket-Accept")

    def _fill(self):
        chunk = self._sock.recv(65536)
        if not chunk:
            raise WebSocketError("connection closed by peer")
        self._buf += chunk

    def _take(self, n):
        while len(self._buf) < n:
            self._fill()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send(self, opcode, payload):
        self._sock.sendall(encode_frame(opcode, payload, mask=os.urandom(4)))

    def send_text(self, text):
        self._send(OP_TEXT, text.encode())

    def _frame(self):
        b0, b1 = self._take(2)
        if b0 & 0x70:
            raise WebSocketError("reserved bits set (no extension was negotiated)")
        fin, opcode, n = bool(b0 & 0x80), b0 & 0x0F, b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._take(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._take(8))[0]
        if n > MAX_MESSAGE:
            raise WebSocketError("frame of %d bytes exceeds the limit" % n)
        mask = self._take(4) if b1 & 0x80 else None
        payload = self._take(n)
        return fin, opcode, (_xor(payload, mask) if mask else payload)

    def recv(self):
        """The next complete data message (str for text, bytes for binary). Ping is
        answered; close raises WebSocketError."""
        parts, kind = [], None
        while True:
            fin, opcode, payload = self._frame()
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                try:
                    self._send(OP_CLOSE, payload[:2])
                except OSError:
                    pass
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                raise WebSocketError("closed by peer (code %s)" % code)
            if opcode in (OP_TEXT, OP_BIN):
                if kind is not None:
                    raise WebSocketError("new data frame inside a fragmented message")
                kind = opcode
            elif opcode == OP_CONT:
                if kind is None:
                    raise WebSocketError("continuation frame without a message")
            else:
                raise WebSocketError("unknown opcode %#x" % opcode)
            parts.append(payload)
            if sum(map(len, parts)) > MAX_MESSAGE:
                raise WebSocketError("message exceeds the limit")
            if fin:
                data = b"".join(parts)
                return data.decode() if kind == OP_TEXT else data

    def close(self):
        try:
            self._send(OP_CLOSE, struct.pack(">H", 1000))
        except OSError:
            pass
        self._sock.close()


def unb64(s):
    return base64.b64decode(s, validate=True)


def b64(data):
    return base64.b64encode(bytes(data)).decode()


class Jip2Client:
    """JIP-2 over one lazily opened WebSocket. A call that fails on the transport is retried
    once on a fresh connection. `generation` counts the connections opened, so a caller
    can tell a reconnect happened (and re-read what it caches, such as parameters)."""

    def __init__(self, url, timeout=5.0):
        self.url, self.timeout = url, timeout
        self.generation = 0
        self._ws, self._next_id = None, 0
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self._drop()

    def _drop(self):
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def call(self, method, *params, retry=True):
        """`method`(*params)'s "result"; raises Jip2Error (the node answered an error) or
        OSError (WebSocketError included: the transport failed)."""
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._ws is None:
                        self._ws = WebSocket(self.url, self.timeout)
                        self.generation += 1
                    self._next_id += 1
                    rid = self._next_id
                    self._ws.send_text(json.dumps(
                        {"jsonrpc": "2.0", "id": rid, "method": method, "params": list(params)}))
                    while True:
                        msg = json.loads(self._ws.recv())
                        # ours: our id, or null on an error the node could not tie to a request;
                        # a message without "id" is a notification
                        if isinstance(msg, dict) and "id" in msg and msg["id"] in (rid, None):
                            break
                except (OSError, ValueError) as e:
                    self._drop()
                    if attempt or not retry:
                        raise e if isinstance(e, OSError) else WebSocketError("bad JSON-RPC frame: %s" % e)
                    continue
                if "error" in msg:
                    err = msg["error"] or {}
                    raise Jip2Error(err.get("code"), err.get("message"), err.get("data"))
                return msg.get("result")
