# jip3-receiver

A telemetry server for [JIP-3](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-3.md),
the JAM telemetry standard. Any JAM node started with `--telemetry HOST:PORT` connects to
it and streams its events; the receiver turns them into Prometheus metrics and Loki log
lines with the same labels as every other obs source (`net`, `run_id`, `node`, `client`).

## What it accepts

TCP, one connection per node. Every message is `u32` little-endian length, then the
content, JAM-serialized (Gray Paper appendix C). The first message is the node
information message; every later one is an event (timestamp, discriminator, fields).

- Every event the spec defines is decoded (`codec.py`, 115 events: 0, 10-13, 20-28,
  40-47, 60-68, 80-84, 90-113, 120-131, 140-153, 160-178, 190-199).
- The node information message's JAM parameters (the GP `fetch` case 0 encoding) have
  no length prefix. The receiver knows the GP 0.7.x layout (134 bytes) and the GP 0.8.0
  layout (122 bytes); for any other it finds the one length that leaves a valid message.
  The core count read from the parameters sizes the per-core arrays of events 10 and
  126; without it, the message length does.
- Event IDs are implicit (one per event, plus the count after a "dropped" event), as the
  spec defines them; they link a pipeline's events (importing to block executed, a
  work-package submission to its stages).
- An event it does not know is counted (`jip3_events_total{event="unknown_<n>"}`) and
  skipped; a malformed one is counted (`jip3_decode_errors_total`) and skipped. Neither
  closes the connection. A message over `JIP3_MAX_MESSAGE` bytes does, because the
  stream can no longer be trusted.

## Ports and environment

| | Default | |
|---|---|---|
| `JIP3_LISTEN` | `0.0.0.0:9910` | where nodes connect |
| `JIP3_METRICS_PORT` | `9911` | `/metrics`, and `/connections` (who is connected, which label rule matched) |
| `JIP3_LOKI_URL` | `http://loki:3100/loki/api/v1/push` | Loki push API; empty turns log shipping off |
| `JIP3_TARGETS` | `/etc/obs/targets/jip3/*.json` | registered native nodes (`obs register ... --jip3`) |
| `DOCKER_SOCK` | `/var/run/docker.sock` | maps a connection to its container; skipped if absent |
| `JIP3_MAX_MESSAGE` | `1048576` | largest message accepted |
| `JIP3_FORGET_SECS` | `21600` | a node's series are dropped this long after it disconnects |

## Pointing a node at it

- A container on a net the obs stack watches (its containers carry `org.abutlabs.obs.*`
  labels): `--telemetry obs-jip3:9910`. netjoin attaches the receiver to the net's
  network under that alias.
- A process on the host: `--telemetry 127.0.0.1:9910`.

## Which node is it

The first rule that applies:

1. **container**: the connection comes from a running container's IP. Its
   `org.abutlabs.obs.*` labels give the node's labels (the same defaults Alloy uses).
2. **address**: the node's own address in its node information (Peer Address) is a
   container's IP.
3. **target**: a registered target's `host:port` is the node's JAMNP-S address (the port
   must match, and the IP too when both name a specific host).
4. **self**: the node's self-description: `net="jip3"`,
   `node="<implementation>-<first 4 bytes of its peer id, hex>"`, `client` the
   implementation name in lower case.

## Metrics

Standard series (`docs/metrics.md`), each labelled `net`, `run_id`,
`node`, `client` and `source="jip3"`:

| Metric | From |
|---|---|
| `jam_node_info` | node information message |
| `jam_best_slot`, `jam_finalized_slot` | 11, 12 |
| `jam_peers{role="validator"\|"other"}` | 10 |
| `jam_blocks_authored_total` | 42 |
| `jam_blocks_imported_total`, `jam_block_import_seconds` | 43 to 47 |
| `jam_wp_received_total` | 94 |
| `jam_wp_refused_total{reason}` | 92 (reason from its text), 93 (`duplicate`) |
| `jam_wp_stage_seconds{stage}` | `receive` 90/91 to 94, `authorize` to 95, `refine` to 101, `report` to 102, `guarantee` to 105, `distribute` to 109 |
| `jam_refine_seconds`, `jam_refine_gas_total` | 101 (total refine cost of all items) |
| `jam_assurances_signed_total` | 126 |
| `jam_tickets_generated_total` | 82 (outputs) |
| `jam_tickets_transferred_total{direction="in"\|"out"}` | 84 |

Receiver series, labelled `net`, `run_id`, `node`, `client`: `jip3_events_total{event}`,
`jip3_dropped_events_total`, `jip3_decode_errors_total{reason}`, `jip3_connected`,
`jip3_connections_total`, `jip3_last_event_timestamp_seconds`, `jip3_loki_dropped_total`.

## Logs

One JSON line per event in Loki, stream `{net, run_id, node, client, source="jip3"}`,
timestamped with the event's own time: `{"event": "block_executed", "id": 12, "ts":
"2026-09-28T18:27:01.123456Z", "block": 9, "accumulated": [...]}`. Hashes and peer ids are
cut to their first 16 hex digits; lists longer than 16 items keep their count and first 16.
A `node_info` line opens each connection and `connection_closed` ends it.

```
{source="jip3", net="lasair-pj"} | json | event="work_package_failed"
```

## The synthetic sender

`sender.py` is a stand-in node built from the same codec: node information (GP 0.8.0,
tiny), then scripted rounds of a busy validator (authoring, importing, the whole guarantor
pipeline, a refused and a duplicate work-package, assurances, tickets, a dropped event).

```
python3 jip3/sender.py --port 9910 --count 10 --rate 50
python3 jip3/sender.py --port 9910 --every-event      # one of each of the 115 events
```

Tests: `python3 -m unittest discover -s tests -p 'test_jip3.py'`.
