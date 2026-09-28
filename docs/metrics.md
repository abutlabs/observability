# The `jam_*` metrics

The metric names, labels and endpoint this stack reads from JAM nodes. Its platform
dashboards use only these, so they work for any client that exposes them, and the JIP-2
exporter and JIP-3 receiver derive the same series for clients that don't.

## Why

A JAM network can mix clients. Today each client exports its own metric names, or none,
so every dashboard is client-specific and a mixed network has no common view. If every
client exposed the same small set of Prometheus metrics, one set of dashboards would
serve any network, whatever its client mix.

They complement the two JIPs that already cover node observability:

- [JIP-2](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-2.md) (node RPC): a
  collector polls a node for its chain view.
- [JIP-3](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-3.md) (telemetry): a node
  pushes an event stream to a telemetry server.

Pull metrics are the cheapest of the three for a node to offer and the easiest to
operate: any Prometheus-compatible collector scrapes them.

## The endpoint

- HTTP `GET /metrics`, Prometheus text exposition format (version 0.0.4), on a port the
  operator chooses. Default **9615**, with a CLI option `--metrics HOST:PORT` (or the
  client's usual option style).
- Metric names start with `jam_`. Clients may export their own metrics beside them under
  their own prefix.
- A node does **not** export `net`, `run_id`, `node` or `instance` labels: the collector
  attaches those from its configuration (which network, which run, which node).
- Counters end in `_total`; durations are histograms in seconds (`_seconds`).

## The metrics

Types: G gauge, C counter, H histogram.

### Identity

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `jam_node_info` | G | `client`, `client_version`, `gp_version`, `spec`, `chain_id`, `validator_index`, `peer_id` | Always 1. `client`: implementation name, lower case (`lasair`, `polkajam`). `gp_version`: the Gray Paper version implemented (`0.8.0`). `spec`: `tiny` or `full`. `chain_id`: first 8 bytes of the genesis header hash, hex. `validator_index`: the node's index in the active set, empty if none. `peer_id`: Ed25519 public key, hex. |

### Chain

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `jam_best_slot` | G | | Slot of the node's best block. |
| `jam_best_height` | G | | Blocks from genesis to the best block. |
| `jam_finalized_slot` | G | | Slot of the latest finalized block. |
| `jam_finalized_height` | G | | Blocks from genesis to the latest finalized block. |
| `jam_blocks_authored_total` | C | | Blocks this node authored. |
| `jam_blocks_imported_total` | C | | Blocks from other authors this node imported (verified and executed). |
| `jam_block_import_seconds` | H | | From the start of an import to the end of its execution. |
| `jam_reorgs_total` | C | | Best-block changes to a block that does not descend from the previous best block. |
| `jam_grandpa_round` | G | | Current GRANDPA round. |
| `jam_grandpa_set_id` | G | | Current GRANDPA authority set id. |

### Network (JAMNP-S)

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `jam_peers` | G | `role` | Connected peers; `role` is `validator` or `other` (they partition: `sum(jam_peers)` is every peer). |
| `jam_network_streams_total` | C | `protocol`, `direction` | Streams opened. `protocol`: the stream kind as JAMNP-S names it, lower case (`up0`, `ce128`, `ce133` ...). `direction`: `in` or `out`. |
| `jam_network_bytes_total` | C | `protocol`, `direction` | Payload bytes sent (`out`) and received (`in`). |
| `jam_network_request_seconds` | H | `protocol` | Request/response streams (CE), from opening the stream to the complete response. |
| `jam_network_errors_total` | C | `protocol`, `reason` | Failed connections (`protocol="connection"`) and streams. `reason` is a short fixed vocabulary (`timeout`, `refused`, `reset`, `malformed`, `other`), never free text. |
| `jam_peer_misbehaviour_total` | C | `peer`, `reason` | Peers this node judged misbehaving. `peer`: the peer's node name where known, else the first 8 hex digits of its Ed25519 key. `reason`: the client's own words, cut to their first clause with digits masked (at most 60 characters), so the set stays small. |

### Work-packages (guaranteeing)

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `jam_wp_received_total` | C | | Work-packages received from builders (CE 133/146) or primary guarantors (CE 134). |
| `jam_wp_refused_total` | C | `reason` | Work-packages refused or failed in the pipeline. `reason`: `duplicate`, `unauthorized`, `anchor`, `queue_full`, `refine`, `other`. |
| `jam_wp_stage_seconds` | H | `stage` | Time spent reaching each stage of the guarantor pipeline, from the previous stage: `receive` (stream opened to package received), `authorize`, `refine`, `report` (work-report built), `guarantee` (guarantee built), `distribute` (guarantees distributed). |
| `jam_wp_expired_total` | C | `reason` | Accepted work-packages dropped before they were guaranteed (`anchor`: the anchor left the recent-history window; `timeout`; `other`). |
| `jam_wp_anchor_age_slots` | H | | How old a work-package's anchor block was, in slots, when the package arrived. |
| `jam_refine_seconds` | H | | Wall-clock time of one work-package's refine (all its items). |
| `jam_refine_gas_total` | C | | Gas used by refine. |

### Availability, audits, tickets

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `jam_assurances_signed_total` | C | | Availability assurances this node signed and distributed. |
| `jam_assurances_included_total` | C | | Assurances included in blocks this node authored. |
| `jam_audit_reports_audited_total` | C | | Work-reports this node audited. |
| `jam_audit_judgments_total` | C | `direction`, `outcome` | Audit judgments `sent` or `received`; `outcome`: `accepted`, `rejected`, `duplicate`. |
| `jam_tickets_generated_total` | C | | Safrole tickets this node generated. |
| `jam_tickets_transferred_total` | C | `direction` | Safrole tickets sent (`out`) or received (`in`), CE 131/132. |

## Clients without the endpoint

The same series can be derived for a node that does not (yet) serve `/metrics`, so the
dashboards still work. The abutlabs observability stack does both:

- **From JIP-2** (a collector polls the node's RPC): `jam_best_slot`,
  `jam_finalized_slot` (from `bestBlock` and `finalizedBlock`) and `jam_node_info`.
- **From JIP-3** (the node pushes events to a telemetry server): `jam_node_info` (node
  information message), `jam_best_slot` and `jam_finalized_slot` (events 11 and 12),
  `jam_peers` (event 10), `jam_blocks_authored_total` (42), `jam_blocks_imported_total`
  and `jam_block_import_seconds` (43 to 47), `jam_wp_received_total` (94),
  `jam_wp_refused_total` (92, 93), `jam_wp_stage_seconds` (90 to 109),
  `jam_refine_seconds` and `jam_refine_gas_total` (101), `jam_assurances_signed_total`
  (126), `jam_tickets_generated_total` (82), `jam_tickets_transferred_total` (84),
  `jam_peer_misbehaviour_total` (28).

A derived series carries a `source` label (`jip2`, `jip3`) naming the path it came from.
A dashboard that aggregates `max by (node) (...)` shows one line per node whichever path
supplied it.

## Beside the `jam_*` set

The platform dashboards also read the stack's own series: `obs_run_*` (every recorded run)
and `obs_container_*` (each labelled container's lifecycle, CPU and memory), both from the
lifecycle service (README.md, "Container lifecycle"). Node detail falls back to lasair's
own names where a lasair image predates a `jam_*` one (`lasair_finalized_slot`,
`lasair_block_height`, `lasair_blocks_authored_total`, `lasair_ce133_*`, ...): each such
panel reads `(jam_x) or (lasair_x)`, so a client with the standard name never needs it.
