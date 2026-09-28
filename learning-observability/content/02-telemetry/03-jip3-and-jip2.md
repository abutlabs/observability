# JIP-3 and JIP-2: telemetry for any client

Two JAM standards let the stack see a node without that node exporting a single Prometheus
metric: JIP-3, where the node pushes events, and JIP-2, where the stack asks the node.

## JIP-3: the node pushes events

[JIP-3](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-3.md) defines a telemetry
stream. A node started with `--telemetry HOST:PORT` opens one TCP connection and sends
messages. Each is a 4-byte length (`u32`, little-endian) followed by the message itself, in
the Gray Paper's encoding:

1. first a **node information** message: the client's name and version, the Gray Paper
   version, the chain's parameters, the genesis hash, the node's key and address;
2. then one message per **event**: a timestamp, a discriminator (the event's number) and
   its fields.

The events cover a validator's life: status (every few seconds), best and finalized block
changes, connections, authoring, importing, block requests, tickets, the guarantor
pipeline from work-package submission to guarantee distribution, assurances, bundle and
segment recovery, preimages. The receiver ([`jip3/`](../../../jip3/README.md)) decodes all
115 events the JIP defines.

### What the receiver does with an event

Two things, every time:

**It updates metrics.** From the events it derives the standard series, labelled
`source="jip3"`:

| Metric | From events |
|---|---|
| `jam_node_info` | node information |
| `jam_best_slot`, `jam_finalized_slot` | 11 best block changed, 12 finalized block changed |
| `jam_peers{role}` | 10 status |
| `jam_blocks_authored_total` | 42 authored |
| `jam_blocks_imported_total`, `jam_block_import_seconds` | 43 importing to 47 block executed |
| `jam_wp_received_total` | 94 work-package received |
| `jam_wp_refused_total{reason}` | 92 work-package failed (reason from its text), 93 duplicate |
| `jam_wp_stage_seconds{stage}` | 90/91 to 94 `receive`, then 95 `authorize`, 101 `refine`, 102 `report`, 105 `guarantee`, 109 `distribute` |
| `jam_refine_seconds`, `jam_refine_gas_total` | 101 refined |
| `jam_assurances_signed_total` | 126 |
| `jam_tickets_generated_total`, `jam_tickets_transferred_total` | 82, 84 |

It also counts every event by name, `jip3_events_total{event}`, and exports its own health:
`jip3_connected`, `jip3_decode_errors_total`, `jip3_dropped_events_total`,
`jip3_loki_dropped_total`.

**It writes a log line.** Each event becomes one JSON line in Loki, in the stream
`{net, run_id, node, client, source="jip3"}`, timestamped with the event's own time.
Hashes and peer ids are cut to their first 16 hex digits. Real lines from a PolkaJam node
(`pj3`) on 2026-09-28:

```json
{"event":"best_block_changed","id":7263,"ts":"2026-09-28T18:54:24.351555Z","slot":9148144,"hash":"30c0cd1d0449c887"}
{"event":"status","id":7266,"ts":"2026-09-28T18:54:26.256867Z","peers":5,"validator_peers":5,"block_announcement_peers":3,"guarantees_per_core":[0,0],"shards":0,"shards_size":0,"preimages":0,"preimages_size":0}
{"event":"refined","id":4243,"ts":"2026-09-28T18:51:26.213794Z","package":4238,"costs":[{"total":{"gas":113507145,"ns":82772834},"compile_ns":193250,"historical_lookup":{"gas":0,"ns":0},"machine_expunge":{"gas":0,"ns":0},"peek_poke_pages":{"gas":0,"ns":0},"invoke":{"gas":0,"ns":0},"other_host_calls":{"gas":3035,"ns":3374}}]}
```

The `id` fields link events: `"package":4238` in the `refined` event points at the event
with id 4238, the submission it belongs to. That is how the receiver times the pipeline's
stages.

### Which node is it?

A TCP connection does not say which container it came from, so the receiver works it out,
first rule that applies:

1. **container**: the connection's source IP is a running container's; that container's
   `org.abutlabs.obs.*` labels name the node.
2. **address**: the address the node reports in its node information is a container's IP.
3. **target**: a target registered with `./obs register ... --jip3` has that JAMNP-S
   address.
4. **self**: the node's own description: `net="jip3"`, `node="<implementation>-<first 4
   bytes of its peer id>"`, `client` = the implementation name in lower case.

If a JIP-3 node shows up under `net="jip3"` with a name like `polkajam-1a2b3c4d`, rule 4
applied: the receiver could not match it to a container. Check that the container carries
`org.abutlabs.obs.*` labels (so netjoin put the receiver on its network).

### Robustness

An event the receiver does not know is counted as `jip3_events_total{event="unknown_<n>"}`
and skipped; a malformed one is counted in `jip3_decode_errors_total` and skipped. Neither
closes the connection. A message larger than `JIP3_MAX_MESSAGE` (1 MiB) does, because the
stream can no longer be trusted. A node's series are dropped 6 hours after it disconnects.

## JIP-2: the stack asks the node

[JIP-2](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-2.md) is the node RPC every
client serves. The exporter ([`jip2/`](../../../jip2/README.md)) keeps one WebSocket per
node and, every 3 seconds, asks each for `bestBlock`, `finalizedBlock` and `syncState`
(and `parameters` once). A node that does not answer within 2 seconds is marked down for
that round and never delays the others.

Per node it exports `jam_best_slot`, `jam_finalized_slot`, `jam_node_up`,
`jam_head_lag_slots` (newest best slot on the net minus this node's),
`jam_finality_lag_slots`, and the first 48 bits of each block hash.

### Agreement by hash

Then it does something no single node can: it compares the nodes. It takes the lowest best
slot among the nodes that answered (the *common slot*), finds each node's block at or below
it by walking parent links, and compares the hashes. Per network it exports:

| Metric | Meaning |
|---|---|
| `jam_net_heads` | distinct blocks at the common slot; 1 means one head |
| `jam_net_one_head` | 1 if every node is up, holds one block, and none is more than 3 slots behind |
| `jam_net_final_heads` | distinct finalized blocks; 1 means finality agrees |
| `jam_finality_conflicts_total` | a finalized slot seen with two different hashes: a safety failure |

Why hashes and not slots? Two nodes on two forks can report the same best slot. Only the
hash says they hold the same block.

## What each path cannot see

- **JIP-2** knows only what the RPC answers: slots, hashes, sync state. No identity labels
  beyond the spec (derived from the core count), no timings.
- **JIP-3** events carry no byte counts, and the JIP's auditing and finality sections are
  still marked TODO, so audits and GRANDPA are not in it yet.
- **Scraping** sees whatever the client chose to export, and nothing if it exports nothing.

That is why the platform dashboards combine them, and why a client's own metrics (like
lasair's, next lesson) go deeper than any standard.

## Try it without Docker

[Lab 2](../../exercises/lab-2-jip3-without-docker.md) runs the receiver as a plain Python
process and feeds it from the synthetic sender in [`jip3/sender.py`](../../../jip3/sender.py),
a stand-in node that plays scripted rounds of a busy validator. Ten minutes, no network
needed.

Next: [lasair as an example](04-lasair-as-an-example.md)
