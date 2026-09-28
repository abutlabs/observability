# PromQL exercises

Goes with [lesson 3.7](../content/03-dashboards/07-promql-basics.md). Part A reads queries
taken from the dashboards, part B finds mistakes, part C writes queries. `$net` and
`$run_id` are the dashboard variables; in Explore, replace them with a real net and run.

## A. What does it compute?

### A1

```promql
60 * rate(lasair_blocks_authored_total{net="$net",run_id="$run_id"}[2m])
```

<details>
<summary>Answer</summary>

Blocks each lasair node authored per minute, one line per node, averaged over the last 2
minutes at each point. `rate` gives per second; times 60 gives per minute. (*Blocks
authored per minute*, lasair validator duties.)

</details>

### A2

```promql
max(max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"}))
  - min(max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"}))
```

<details>
<summary>Answer</summary>

The head spread: the newest best slot on any node minus the oldest. The inner
`max by (node, client)` first collapses the paths that report the same node, so a node seen
through both JIP-3 and JIP-2 counts once. (*Head spread · PASS ≤ 3 slots*, Network
overview.)

</details>

### A3

```promql
histogram_quantile(0.99, sum by (client, le) (rate(jam_block_import_seconds_bucket{net="$net",run_id="$run_id"}[5m])))
```

<details>
<summary>Answer</summary>

The 99th percentile of block import time, per client: 99% of imports in the last 5 minutes
took less than this. Summing by `client, le` merges all nodes of a client and keeps the
bucket bounds (`le`) that `histogram_quantile` needs. (*Block import time, by client*,
Network overview.)

</details>

### A4

```promql
count(max by (node) (last_over_time(jam_best_slot{net="$net",run_id="$run_id"}[15m])))
  - (count(max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"})) or vector(0))
```

<details>
<summary>Answer</summary>

Nodes that reported a best slot at some point in the last 15 minutes, minus nodes that
report one now: the nodes that **stopped reporting**. `or vector(0)` makes "no node reports
now" count as 0 instead of making the whole expression empty. After a run ends, this reads
the number of nodes for 15 minutes (lesson 3.6).

</details>

### A5

```promql
sum(1 - up{job=~"obs-.*"})
```

<details>
<summary>Answer</summary>

How many of the stack's own services are down. `up` is 1 for every target that answered
its last scrape and 0 for one that did not; `1 - up` is 1 for each one down. (*obs
services down · PASS = 0*, obs self-health.)

</details>

## B. Find the mistake

### B1

"Slots per second on the failing run":

```promql
rate(jam_best_slot{run_id="lasair6-20260928T153915Z"}[10m])
```

<details>
<summary>Answer</summary>

`jam_best_slot` is a **gauge**, and `rate` is for counters: it treats every decrease as a
counter reset, so a re-org that moves the best slot back would show a huge jump. For a
gauge, use `deriv`, which fits a line through the samples:
`deriv(max(jam_best_slot{run_id="lasair6-20260928T153915Z"})[10m:])` returns about 0.27 to
0.30 across that run.

</details>

### B2

"Median refine time across all nodes":

```promql
histogram_quantile(0.5, sum(rate(lasair_guarantor_refine_seconds_bucket{run_id="$run_id"}[5m])))
```

<details>
<summary>Answer</summary>

`sum(...)` without `by (le)` adds all the buckets together into one number, so
`histogram_quantile` has no bucket bounds left to work with and returns nothing useful.
Keep `le`: `sum by (le) (rate(...[5m]))`.

</details>

### B3

"The best slot of node lm0":

```promql
jam_best_slot{net="lasair6", node="lm0"}
```

<details>
<summary>Answer</summary>

No `run_id`: over a long time range this mixes every run of `lasair6` into one confusing
graph, and at a single moment it may return several series. Always select the run.

</details>

### B4

"Packages expired in the whole failing run", evaluated at the run's end:

```promql
sum(lasair_ce133_expired_total{run_id="lasair6-20260928T153915Z"})
```

<details>
<summary>Answer</summary>

It depends on the evaluation time. An instant query only sees series that are still
current. At the run's recorded end (`time=1790613808`) it happens to return 17; evaluated
90 seconds later it returns nothing, because the nodes had stopped and their series had
gone stale. Ask for the highest value within the run instead:
`sum(max_over_time(lasair_ce133_expired_total{run_id="..."}[70m]))` returns 17 at the end
and for an hour after it.

</details>

## C. Write it

### C1

Blocks each node imported per minute, for one run.

<details>
<summary>Answer</summary>

```promql
60 * rate(jam_blocks_imported_total{net="$net",run_id="$run_id"}[2m])
```

On lasair builds without the `jam_*` names, use `lasair_blocks_imported_total`. On the
failing run, about 17 per node per minute half an hour in.

</details>

### C2

How many nodes run each client?

<details>
<summary>Answer</summary>

```promql
count by (client) (max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"}))
```

On the failing run: `{client="lasair"} 6`.

</details>

### C3

The share of the offered load that the DEX turned away (errors plus busy replies, over
offered operations).

<details>
<summary>Answer</summary>

```promql
((sum(loadgen_op_errors_total{net="$net",run_id="$run_id"}) or vector(0))
  + (sum(loadgen_ops_busy_total{net="$net",run_id="$run_id"}) or vector(0)))
  / sum(loadgen_ops_total{net="$net",run_id="$run_id"})
```

The `or vector(0)` parts matter: on a healthy run the error and busy counters may never be
created, and without them the whole expression would be empty instead of 0. On the failing
run it reaches about 0.36 by the end of the load, against a threshold of 0.0001. (*Load
turned away · PASS ≤ 0.01%*, DEX.)

</details>

### C4

Refine speed in gas per second, per client, from the standard metrics.

<details>
<summary>Answer</summary>

```promql
sum by (client) (rate(jam_refine_gas_total{net="$net",run_id="$run_id"}[5m]))
  / sum by (client) (rate(jam_refine_seconds_sum{net="$net",run_id="$run_id"}[5m]))
```

Gas used per second of refine time. Rate both, sum both the same way, then divide. (The
lasair PVM dashboard does the same by `service`.)

</details>

### C5

The median time from acceptance to `refined` on lasair nodes, over the whole time range.

<details>
<summary>Answer</summary>

```promql
histogram_quantile(0.5, sum by (le) (increase(jam_wp_stage_seconds_bucket{net="$net",run_id="$run_id",stage="refined"}[$__range])))
```

`increase(...[$__range])` counts observations over the whole dashboard range instead of a
moving window; `stage="refined"` picks lasair's stage (lesson 2.1: other sources use other
stage names). (The work-package lifecycle dashboard's bars.)

</details>
