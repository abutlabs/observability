# Network overview and Chain health

The two platform dashboards that work for any client. Both read only the `jam_*` series,
and both take `max by (node, client)` so that a node shows once whichever paths report it.

## Network overview (`obs-overview`)

**Which nodes make up this net, what client runs each, and is every node keeping up?**
It is Grafana's home page.

### The top row

| Stat | Reads | Worry when |
|---|---|---|
| Run | the run's record: active, *run ended HH:MM:SS — net shut down* (grey), or *net stalled since HH:MM:SS — run not ended* (red) | red: no node's best slot has moved for a minute and the run has not ended |
| Nodes advancing | nodes whose best slot moved within a minute of the net's newest move, at the run's end (or now, if it has not ended) | fewer than you started |
| Clients | distinct `client` values among them | a client you expected is missing |
| Newest best slot / Newest finalized slot | the highest of each | they stop moving |
| Head spread · PASS ≤ 3 slots | newest best slot minus the oldest | red: some node is more than 3 slots behind |
| Nodes stopped during the run · PASS = 0 | nodes whose best slot stopped advancing more than a minute before the rest of the net's, within the run | red: a node died or lost its path while the others kept going |

*Nodes stopped during the run* is judged relative to the rest of the net, not against the
clock: a net shut down all at once at the end of its run stops together and is not
counted. It never turns red just because the run ended. Lesson 3.6 walks through this.

### The Nodes table

One row per node: `node`, `client`, `state` (*advancing*, *stopped during the run*, or
*net stalled*), `version` and `GP` (from `jam_node_info`), `best slot`, `finalized slot`,
`finality lag` (red above 12 slots, one epoch on the tiny spec), `peers`, `paths`,
`last advanced` (when its best slot last moved), and `stopped`, `why` and `exit code`
(from the lifecycle service's Docker events, empty if it never stopped in the range).
**paths** is how many ingestion paths describe the node: `metrics` (its own `/metrics`),
`jip2`, `jip3`. A PolkaJam node with telemetry on shows 2 (JIP-3 and JIP-2). Versions come
from the node's own metrics or its JIP-3 node information; JIP-2 cannot tell, so a
JIP-2-only node has an empty version. Click a row for Node detail (lesson 3.6).

### The panels below

- **Best slot per node** and **Finalized slot per node**: the lines climb together. One
  that flattens has stopped importing (best) or finalizing (finalized).
- **Blocks authored per minute, by client**: each client's share of authoring. Every
  client with validators should have a line above zero.
- **Block import time, by client**: p50 and p99 of `jam_block_import_seconds`.
- **Peers per node.**
- **Work-packages received per minute, by client**: received and refused.
- **Guarantor pipeline, p50 per stage**: stacked time per stage. Read it within one client:
  stage names and what they measure differ between sources (lesson 2.1).
- **Refine time, by client**: p50 and p99 of `jam_refine_seconds`.
- **Which path reports each node**: how many nodes each path describes.
- **Reorgs per 10 min.**

## Chain health (`obs-chain`)

**Is the net one chain that keeps growing and finalizing, on every node?** Its text panel
says it: best and finalized slots should climb together, finality lag should stay under one
epoch (12 slots on tiny), and head agreement should read one head.

### The top row: five verdicts

| Stat | Passes when | How it is computed |
|---|---|---|
| Nodes stopped during the run | 0 | as on the overview |
| One head | PASS | `jam_net_one_head` from the JIP-2 exporter (every node up, one block at the common slot, none more than 3 slots behind); without JIP-2, the spread of best slots stands in |
| Finality lag, worst node | ≤ 12 slots | best slot minus finalized slot, on the node that lags most |
| Finalized in 5 min, slowest node | > 0 slots | how far the slowest node's finalized block moved in the last 5 minutes; 0 means finality stalled somewhere |
| Finality conflicts | 0 | `jam_finality_conflicts_total`: a finalized slot seen with two hashes, a safety failure |

### The panels

- **Best slot per node**, **Finalized slot per node**: as above. Finalized lines climb in
  steps.
- **Finality lag per node**, with a dashed line at one epoch. Lag that rises and falls
  well under the line is normal; lag that climbs steadily toward it is not.
- **Head agreement**: distinct heads at the common slot, distinct finalized blocks, and the
  best-slot spread. All should sit at 1, 1 and a small number.
- **Height per node**: blocks since genesis, best solid and finalized dashed, for nodes
  that export heights in their own metrics (JIP-2 and JIP-3 carry slots only).
- **Peers per node**, and how many nodes answer the JIP-2 exporter.

## Reading them together

The overview tells you *who is there*; Chain health tells you *whether they agree*. A
typical first look at any run:

1. Overview: the Run tile grey (*run ended ... — net shut down*) or blue (*active*), the
   right number of nodes and clients, no red stats, every node's paths as expected.
2. Chain health: five green stats. If one is red, its time series show when and on which
   node. *Nodes stopped during the run* stays green on a finished run: a teardown stops
   every node together and is never counted as a node stopping early (lesson 3.6).

### What they said about the case study's failing run

On `lasair6-20260928T153915Z`, the run in track 5, both are calm for the whole run: six
lasair nodes, `jam_net_one_head` at 1 on every sample, finality lag never above 2 slots on
any node, no finality conflict. The soak's own chain checks agree: one head on 630 of 630
samples, 659 finalized slots hash-checked, state parity on all six nodes.

The lesson to keep: **a green Chain health does not mean the network is doing its
job.** The chain was healthy, and the exchange running on it was
failing. The dashboards that showed the failure were further down the list.

Next: [Logs and obs self-health](03-logs-and-self-health.md)
