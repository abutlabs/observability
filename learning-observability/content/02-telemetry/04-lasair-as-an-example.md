# lasair as an example

lasair, the OCaml JAM client, reports through all three kinds of signal: Prometheus
metrics, one log line per event on stdout, and optional JIP-3 telemetry. Its reference is
`observability/README.md` in the lasair repository. This lesson is the tour you need to
read its dashboards and to diagnose it.

## Metrics

Start a node with `--metrics-port P` (containers use 9615). Any HTTP request to that port
returns every metric; there is no other path to learn.

Current lasair builds export the whole `jam_*` set, and then a great deal more under
`lasair_`, grouped by what the node is doing:

| Family | A few of its metrics |
|---|---|
| Chain | `lasair_finality_lag_blocks`, `lasair_import_busy_slot` (stuck non-zero: the import hung), `lasair_validators_active` |
| Authoring and tickets | `lasair_sealer_slots_total` (slots it was entitled to), `lasair_authored_rejected_total`, `lasair_author_block_seconds` |
| Work-packages (CE-133/146) | `lasair_wp_outcomes_total{outcome, reason, last_stage}`, `lasair_wp_inflight`, `lasair_ce133_queue_depth`, `lasair_guarantor_refine_seconds` |
| Co-guaranteeing (CE-134), distribution (CE-135) | `lasair_ce134_cosigned_total`, `lasair_ce134_refused_total{reason}`, `lasair_ce135_included_total` |
| Assurances, availability, audits, disputes | `lasair_assurances_received_total`, `lasair_avail_store_shards`, `lasair_audit_evaluation_seconds` |
| Finality (GRANDPA) | `lasair_grandpa_commits_total`, `lasair_grandpa_equivocations_total`, `lasair_finality_voters` |
| Networking (JAMNP-S over QUIC) | `lasair_net_messages_total`, `lasair_quic_open_timeouts_total`, `lasair_validators_connected` |
| PVM and refine | `lasair_refine_items_total{service, result}`, `lasair_refine_gas_per_second`, `lasair_is_authorized_seconds` |
| Memory and store | `lasair_mem_rss_bytes`, `lasair_mem_ocaml_heap_bytes`, `lasair_store_blocks` |
| Liveness | `lasair_<loop>_heartbeat` (Unix time a background loop last ran), `lasair_status_alive_time` |
| Logs and telemetry | `lasair_log_lines_total{level, component}`, `lasair_jip3_connected`, `lasair_jip3_dropped_total` |

Two of them deserve a closer look, because the case study depends on them.

**`lasair_wp_outcomes_total{outcome, reason, last_stage}`** records how every work-package
ended: `accumulated`, `expired` (reason `anchor_too_old` or `lookup_anchor_too_old`),
`abandoned`, `dropped`, `refused` at intake, and so on. `last_stage` is the furthest stage
it reached. So `outcome="expired", last_stage="refined"` reads: *refined, but never
guaranteed in time*.

**`lasair_guarantor_refine_seconds`** is the time the guarantor's worker took to refine one
whole package. lasair exports each histogram as the usual `_bucket`, `_sum` and `_count`
**plus** a plain gauge with the latest observation, so a single scrape shows the last
value: `lasair_guarantor_refine_seconds` alone is the most recent package's refine time.

### Old names and new names

Where lasair had its own name for a standard metric, it still exports the old name as an
alias of the same series:

| Standard name | lasair's older name |
|---|---|
| `jam_best_slot` | `lasair_slot` |
| `jam_best_height` | `lasair_block_height` |
| `jam_finalized_slot` | `lasair_finalized_slot` |
| `jam_blocks_imported_total` | `lasair_blocks_imported_total` |
| `jam_blocks_authored_total` | `lasair_blocks_authored_total` |
| `jam_wp_anchor_age_slots` | `lasair_ce133_anchor_age_slots` |
| `jam_assurances_signed_total` | `lasair_assurances_signed_total` |

Builds from before the `jam_*` work, such as lasair 2.1.2, export only the older names.
The case study's runs used such builds, so its queries use the older names.

## Logs

lasair writes one line per event on stdout. By default the line is for people:

```text
[ce133] package 0x3f1c9a02.. EXPIRED (anchor_too_old): its context can no longer be reported; the builder must resubmit
```

With `LASAIR_LOG_FORMAT=json` every line is one JSON object instead, the same message plus
fields you can filter on:

```json
{"ts":"2026-09-28T21:14:03.512Z","level":"warn","component":"ce133","msg":"package 0x3f1c9a02.. EXPIRED (anchor_too_old): its context can no longer be reported; the builder must resubmit","package":"0x3f1c9a02"}
```

(Both examples are from lasair's README.)

- `component` is the line's leading tag: `ce133` (work-package intake and the guarantor
  pipeline's decisions), `ce134`, `ce135`, `guarantor` (the refine worker), `grandpa`,
  `sync`, `peer`, `audit`, `store` and others; `import` for rejected blocks, `status` for
  the 3-second `STATUS` line, `node` for everything else.
- `level` is `debug`, `info`, `warn` or `error`. Where the code does not say, lasair reads
  it from the words it uses for trouble: `EXPIRED`, `refused`, `timed out` are `warn`;
  `EQUIVOCATION`, exceptions and a stalled head are `error`.
- `package`, `report`, `block`, `core`, `slot`, `peer` and a few more appear when the line
  names them.

Every line, in either format, is also counted: `lasair_log_lines_total{level, component}`.
So a dashboard can graph warnings per component without reading a single log line.

The lasair dashboards' log panels filter on the JSON fields, so they show lines only when
the nodes run with `LASAIR_LOG_FORMAT=json`. Plain lines are still in Loki and still
searchable with a text filter (lesson 3.8).

## JIP-3

Start lasair with `--telemetry HOST:PORT` (or `LASAIR_TELEMETRY=HOST:PORT`). Events never
wait on the network: they queue (at most 16,384), and a sender thread writes them. If the
queue fills, events are dropped and the next one sent is preceded by a *Dropped* event
with the count, so event ids stay correct. After a reconnect (backoff 1 s doubling to
30 s) the node information is sent again. The sender's own health is exported as
`lasair_jip3_connected`, `lasair_jip3_queue`, `lasair_jip3_events_total` and
`lasair_jip3_dropped_total`.

lasair sends most of the events a validator produces: status, best and finalized block,
connections, authoring, importing, block announcements, ticket generation, the whole
guarantor pipeline, receiving guarantees, assurances. It does not yet send block-request,
shard-request, bundle-recovery, segment-recovery or preimage events; lasair's README has
the full table.

## What to take from this

A client's own metrics go much deeper than the standard set. The standard set lets you
compare clients; the client's own set lets you explain one. In the case study you will
start from standard views (the chain is fine), move to a service's metrics (the DEX is
refusing), and end in lasair's own (`lasair_guarantor_refine_seconds`, then the `ce133`
log lines).

Next: [Plugging in a new client](05-plugging-in-a-client.md)
