# The jam_* metrics

## One vocabulary for every client

Each JAM client that exports metrics names them its own way, and some export none. A
dashboard written for one client's names shows nothing for another's. The fix is a small
common set that every client can export under the same names. This repository proposes one
in [`docs/metrics.md`](../../../docs/metrics.md): the `jam_*` metrics. The platform
dashboards read only these, so they work for any client that exports them, and the JIP-3
receiver and JIP-2 exporter derive the same series for clients that do not.

## The endpoint

- `GET /metrics`, in the Prometheus text format (version 0.0.4), on a port the operator
  chooses; default **9615**.
- Names start with `jam_`. A client may export its own metrics beside them under its own
  prefix (lasair uses `lasair_`).
- A node does **not** export `net`, `run_id`, `node` or `instance`. The collector attaches
  those, because only the collector knows which network and which run this is.
- Counters end in `_total`. Durations are histograms in seconds, named `_seconds`.

## Three metric types

Every metric is one of three types, and the type decides how you may query it.

**Gauge**: a value that goes up and down, read as is. `jam_best_slot` is the slot of the
node's best block right now; `jam_peers{role="validator"}` is how many validator peers it
has.

**Counter**: a running total that only goes up (and resets to 0 when the process
restarts). `jam_blocks_imported_total` is every block imported since start. A counter's raw
value is rarely interesting; its *rate* is. You almost always read a counter through
`rate(...)` or `increase(...)` (lesson 3.7).

**Histogram**: a distribution of observations, exported as three series.
`jam_block_import_seconds` becomes, for example (each exporter picks its own bucket
bounds):

```text
jam_block_import_seconds_bucket{le="0.05"}   observations ≤ 0.05 s (cumulative)
jam_block_import_seconds_bucket{le="0.1"}    observations ≤ 0.1 s
...
jam_block_import_seconds_bucket{le="+Inf"}   every observation
jam_block_import_seconds_sum                 the sum of all observed values
jam_block_import_seconds_count               how many observations
```

From these you get percentiles (`histogram_quantile`) and averages (`_sum` over `_count`).

## The families

Condensed from [`docs/metrics.md`](../../../docs/metrics.md) (G gauge, C counter, H histogram):

| Family | Metrics | What they tell you |
|---|---|---|
| Identity | `jam_node_info` (G, always 1) | Labels say what the node is: `client`, `client_version`, `gp_version`, `spec` (`tiny`, `full`), `chain_id`, `validator_index`, `peer_id` |
| Chain | `jam_best_slot`, `jam_best_height`, `jam_finalized_slot`, `jam_finalized_height` (G) | Where the node's head and finality are |
| | `jam_blocks_authored_total`, `jam_blocks_imported_total`, `jam_reorgs_total` (C) | Authoring, importing, re-orgs |
| | `jam_block_import_seconds` (H) | Time to import one block |
| | `jam_grandpa_round`, `jam_grandpa_set_id` (G) | Finality gadget progress |
| Network | `jam_peers{role}` (G) | Connected peers, `validator` or `other` |
| | `jam_network_streams_total`, `jam_network_bytes_total` (C, by `protocol`, `direction`) | JAMNP-S traffic per stream kind (`up0`, `ce128`...) |
| | `jam_network_request_seconds` (H), `jam_network_errors_total{reason}` (C) | Request latency, failures |
| Work-packages | `jam_wp_received_total`, `jam_wp_refused_total{reason}`, `jam_wp_expired_total{reason}` (C) | The guarantor pipeline's intake and losses |
| | `jam_wp_stage_seconds{stage}` (H) | Time to each stage of the pipeline |
| | `jam_wp_anchor_age_slots` (H) | How old a package's anchor was when it arrived |
| | `jam_refine_seconds` (H), `jam_refine_gas_total` (C) | Refine time and gas |
| Availability, audits, tickets | `jam_assurances_signed_total`, `jam_audit_judgments_total`, `jam_tickets_generated_total`, ... (C) | The rest of a validator's duties |

Label values are always from a short fixed list. A metric never uses a block or package
hash as a label: every distinct label value is a new series, and hashes would make
millions of them.

## What it looks like on the wire

This is a real excerpt of a `/metrics` page: the JIP-3 receiver's, after a synthetic node
sent it three rounds of events (lab 2 shows how to produce it yourself; the receiver adds
the `net`, `run_id`, `node`, `client` and `source` labels because it is a collector):

```text
jam_best_slot{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 9148364
jam_blocks_authored_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 3
jam_peers{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3",role="validator"} 4
jam_wp_refused_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3",reason="duplicate"} 3
```

## Where the sources do not agree yet

The set is a proposal, and two details still differ between the document and lasair, the
first client to export it.

**Stage names.** `jam_wp_stage_seconds{stage}` is measured two different ways:

- [`docs/metrics.md`](../../../docs/metrics.md) and the JIP-3 receiver use the stages
  `receive`, `authorize`, `refine`, `report`, `guarantee`, `distribute`, each timed **from
  the previous stage**.
- lasair's own export uses `shared`, `refined`, `cosigned`, `guaranteed`, `included`,
  `available`, `audited`, `accumulated`, `finalized`, each timed **from the moment the
  guarantor accepted the package**.

Both are useful, but they are different measurements. Compare stage times within one
client, not across clients, until the proposal settles.

**Protocol names.** The document spells the `protocol` label in lower case (`up0`,
`ce128`); lasair uses the JAMNP-S spelling (`UP-0`, `CE-128`). A query that matches one
spelling misses the other, so check which one a client uses before comparing traffic.

## Why not just use each client's own names?

You can, for a client's own dashboards, and lasair does. But a network of mixed clients
needs a view where one line is one node, whatever runs it. That view can only be built on
names they share. The `jam_*` set is deliberately small, so that adopting it is cheap.

Next: [Three paths, one label model](02-three-paths-one-label-model.md)
