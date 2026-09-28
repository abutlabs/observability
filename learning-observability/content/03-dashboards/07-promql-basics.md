# PromQL basics

Every panel is a PromQL query, and when a panel is not enough you write your own. This
lesson covers the part of PromQL the dashboards use, with queries taken from them.

## Where to run a query

- **Grafana Explore**: http://localhost:3300/explore, datasource *Prometheus*. Type a query,
  pick a time range, switch between table and graph. Every panel has *Explore* in its menu,
  which opens its query there.
- **The HTTP API**, for scripts. A real call against the case study's failing run
  (`time` is the Unix time to evaluate at, here the run's end):

```text
$ curl -s -G 'http://localhost:9390/api/v1/query' \
    --data-urlencode 'query=sum(max_over_time(lasair_ce133_guaranteed_total{run_id="lasair6-20260928T153915Z"}[70m]))' \
    --data-urlencode 'time=1790613808'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790613808,"140"]}]}}
```

140 work-items guaranteed by the six nodes in that hour. Keep that number: the passing run
will beat it.

## Selecting series

A metric name alone selects every series of that name. Labels in braces narrow it:

```promql
jam_best_slot{net="lasair6", run_id="lasair6-20260928T153915Z"}
```

| Matcher | Means |
|---|---|
| `node="lm0"` | equal |
| `node!="lm0"` | not equal |
| `node=~"lm.*"` | matches the regular expression (anchored: the whole value) |
| `node!~"lm[0-2]"` | does not match |

Dashboards write `{net="$net",run_id="$run_id"}`: Grafana fills in the variables. Always
select the run. Without `run_id`, a query mixes every run of the net.

## Instant and range vectors

`jam_best_slot{...}` is an **instant vector**: one value per series, at the evaluation time.
`jam_best_slot{...}[5m]` is a **range vector**: every sample of the last 5 minutes, per
series. You cannot graph a range vector directly; you pass it to a function.

Do not confuse these with the instant and range *queries* of lesson 0.2. A range query
draws a graph by evaluating an expression at many moments across the time range; a range
vector is the window of samples, `[5m]`, that one evaluation looks back over. A graph of
`rate(x[5m])` uses both: at every point of the graph, it looks back 5 minutes.

## Counters: `rate` and `increase`

A counter's raw value is a running total since the process started. What you want is how
fast it grows:

```promql
60 * rate(lasair_blocks_authored_total{net="$net",run_id="$run_id"}[2m])
```

`rate(x[2m])` is the per-second growth over the last 2 minutes; times 60 is per minute.
That is the *Blocks authored per minute* panel of lasair validator duties.

```promql
increase(lasair_ce133_expired_total{net="$net",run_id="$run_id"}[10m])
```

`increase(x[10m])` is how much it grew in 10 minutes: *Packages expired, per 10 min*.

Both handle a counter that resets to 0 when a node restarts. The window must hold at least
two samples; with 5-second scrapes, `[2m]` is plenty.

## Aggregating: `sum`, `max`, `min`, `count` ... `by`

```promql
sum by (op) (rate(loadgen_ops_total{net="$net",run_id="$run_id"}[1m]))
```

Adds the series up, keeping one per `op`: the DEX dashboard's *Offered load*. `by (...)`
keeps the listed labels; `without (...)` drops them. Without either, you get one number.

```promql
max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"})
```

One series per node even when two paths (say JIP-3 and JIP-2) report the same node: the
platform dashboards' standard way to read a node.

```promql
count(max by (node) (jam_best_slot{net="$net",run_id="$run_id"}))
```

Nodes reporting.

## Arithmetic and comparisons

```promql
max(max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"}))
  - min(max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"}))
```

The head spread: newest best slot minus the oldest.

A comparison **filters**: `x > 0` keeps only the series whose value is above 0. The
platform dashboards use `jam_finalized_slot > 0` so a node that has not finalized yet
draws no line instead of a line at zero. With `bool`, a comparison returns 1 or 0 instead:
`(max(...) - min(...)) <= bool 3` is 1 when the spread is at most 3.

## Histograms

For a histogram `x_seconds`, a percentile:

```promql
histogram_quantile(0.5, sum by (le) (rate(lasair_guarantor_refine_seconds_bucket{net="$net",run_id="$run_id"}[5m])))
```

Always `rate` the `_bucket` series first, then `sum by (le)` (keep `le`; add `node` to get
one line per node), then `histogram_quantile`. The result is interpolated between bucket
bounds, so it is an estimate.

An average is the sum over the count:

```promql
rate(lasair_guarantor_refine_seconds_sum{net="$net",run_id="$run_id"}[5m])
  / rate(lasair_guarantor_refine_seconds_count{net="$net",run_id="$run_id"}[5m])
```

That is the *Refine seconds (average per package)* panel, which you will meet again in the
case study.

## Subqueries and `deriv`

Two more tools appear in the case study.

A **subquery**, `(expression)[70m:15s]`, evaluates any expression every 15 seconds over the
last 70 minutes and gives back a range vector, so a function like `max_over_time` can run
over it. Leave out the step, `[10m:]`, and Prometheus picks one. The case study uses it to
find the worst five-minute average of a whole run:

```promql
max_over_time((max(rate(lasair_guarantor_refine_seconds_sum{run_id="$run_id"}[5m])
  / rate(lasair_guarantor_refine_seconds_count{run_id="$run_id"}[5m])))[70m:15s])
```

**`deriv`** is the per-second slope of a **gauge** (`rate` is for counters). Slots per
second:

```promql
deriv(max(jam_best_slot{run_id="$run_id"})[10m:])
```

## When nothing happened

A counter that never incremented may not exist at all, and `sum` of nothing is nothing, so
a stat would show "no data". Many pass/fail stats append `or vector(0)`:

```promql
sum(increase(obs_netjoin_errors_total[1h])) or vector(0)
```

## Querying a finished run

A dashboard evaluates at every point of its time range. A single number for a whole run
needs care:

- `max_over_time(x[70m])` at the run's end: the highest value in the last 70 minutes. For a
  counter that did not reset, that is its final value (the API call above).
- `increase(x[$__range])` in Grafana: growth over the dashboard's whole range. The lasair
  dashboards' "(range)" stats use it.
- Remember that a counter's final value may differ from what a script read earlier.
  In the failing run the load generator's error counter ends at 523, while the soak, which
  read it when the load stopped, counted 519: a few more calls failed before the load
  generator stopped.

Practice: [PromQL exercises](../../exercises/promql.md).

Next: [LogQL basics](08-logql-basics.md)
