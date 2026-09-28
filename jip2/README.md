# jip2-exporter

Reads every JAM node's chain view over [JIP-2](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-2.md)
(the node RPC every client serves) and exports it as Prometheus metrics. It covers clients
that expose no `/metrics` and push no JIP-3 telemetry, and it is the collector's
independent view of whether the nodes of a net agree.

## What it polls

Every `JIP2_INTERVAL` seconds, each node on its own persistent WebSocket, concurrently:
`bestBlock`, `finalizedBlock`, `syncState`, and `parameters` once per connection. A method a
node answers with "method not found" is not asked again (a node without finality simply has
no finalized series). A node that does not answer within `JIP2_TIMEOUT` is down for the round
and never delays the others; one that fails on the transport backs off (1 s doubling to 10 s).

Then, per net (`net`, `run_id`), the heads are lined up: the
common slot is the lowest best slot among the nodes that answered; each node's newest block
at or below it is found by walking `parent()` (cached by hash) and compared by hash. The
same for finalized blocks.

## Discovery

- **Docker.** A running container labelled `org.abutlabs.obs.jip2` with a port
  (`ws://<container name>:<port>`) or a full `ws://` / `wss://` URL. Its `net`, `run_id`,
  `node` and `client` come from its other `org.abutlabs.obs.*` labels (`lib/obslib.py`,
  the same defaults Alloy applies). netjoin attaches the exporter to the container's
  networks, so container names resolve. A proxy that serves another node's RPC names the
  node it describes with `org.abutlabs.obs.jip2.node` (and `.jip2.client`); its own logs
  keep its own `node` label.
- **Files.** file_sd JSON matching `JIP2_TARGETS` (default `/etc/obs/targets/jip2/*.json`,
  what `obs register --jip2` writes): targets `host:port` or a `ws://` URL, labels `net`,
  `run_id`, `node`, `client`. For native nodes use `host.docker.internal:<port>`.
- **Static.** `--node NAME,CLIENT,URL` (repeatable) or `JIP2_NODES`, labelled
  `net=$JIP2_NET`.

A node that disappears from discovery disappears from the metrics.

## Metrics (`:9912/metrics`)

Per node, labelled `net`, `run_id`, `node`, `client`, `source="jip2"`:

| Metric | Meaning |
|---|---|
| `jam_best_slot`, `jam_finalized_slot` | the node's best and latest finalized slot (standard, `docs/proposals/jam-node-metrics.md`) |
| `jam_node_info` | 1; `spec` from the core count (2 `tiny`, 341 `full`); other identity labels empty (JIP-2 does not reveal them) |
| `jam_node_up` | 1 if the node answered this round |
| `jam_head_lag_slots` | newest best slot on the net minus this node's |
| `jam_finality_lag_slots` | best slot minus finalized slot |
| `jam_best_hash48`, `jam_finalized_hash48` | first 48 bits of the header hash (equal = same block) |
| `jam_head_agree`, `jam_final_agree` | 1 if the node's block at the common (finalized) slot is the one most nodes hold |
| `jip2_sync_peers`, `jip2_sync_completed` | `syncState`: peers with an open block announcement stream; 1 if `Completed` |

Per net, labelled `net`, `run_id`, `source="jip2"`: `jam_net_nodes`, `jam_net_nodes_up`,
`jam_net_head_slot`, `jam_net_finalized_slot` (lowest), `jam_net_heads` and
`jam_net_final_heads` (distinct blocks at the common slot; 1 = one head),
`jam_net_one_head` (every node up, one block, none more than `JIP2_MAX_LAG` slots behind),
`jam_net_divergence_slots`, `jam_net_samples_total`, `jam_net_diverged_samples_total`,
`jam_finality_conflicts_total` (a finalized slot seen with two hashes: a safety failure).

The exporter's own: `jip2_poll_seconds` (histogram per node), `jip2_poll_errors_total`
(per node and `method`), `jip2_targets`, `jip2_last_round_timestamp_seconds`.

## Settings

| Env | Default | |
|---|---|---|
| `JIP2_METRICS_PORT` | 9912 | `/metrics` |
| `JIP2_INTERVAL` | 3 | seconds between rounds |
| `JIP2_TIMEOUT` | 2 | seconds a call may take |
| `JIP2_MAX_LAG` | 3 | slots a node may trail and still be one head |
| `JIP2_DISCOVERY` | 5 | seconds between discoveries |
| `JIP2_TARGETS` | `/etc/obs/targets/jip2/*.json` | file targets |
| `JIP2_NODES`, `JIP2_NET` | | static nodes and their net label |
| `DOCKER_SOCK` | `/var/run/docker.sock` | Docker discovery is off without it |

## Example

One round against a node on this machine, printed:

```sh
python3 jip2/exporter.py --once --node pj0,polkajam,ws://127.0.0.1:19800
```

A container opting in (compose):

```yaml
labels:
  org.abutlabs.obs.jip2: "19800"
  org.abutlabs.obs.client: polkajam
```

Tests: `python3 -m unittest discover -s tests -p test_jip2.py` (fake JIP-2 nodes, no Docker).
