# LogQL exercises

Goes with [lesson 3.8](../content/03-dashboards/08-logql-basics.md). Where an answer quotes
a result, it came from the `lasair-pj` run `lasair-pj-20260928T184439Z` (three lasair and
three PolkaJam validators) on 2026-09-28.

## A. What does it return?

### A1

```logql
{net="lasair6", client="lasair"} |= "EXPIRED"
```

<details>
<summary>Answer</summary>

Every line from the lasair containers of `lasair6` (every run, since no `run_id` is
selected) that contains `EXPIRED`: the guarantor's "package ... EXPIRED (reason)" lines.

</details>

### A2

```logql
{net="lasair-pj", source="jip3"} | json | event="peer_misbehaved"
```

<details>
<summary>Answer</summary>

Every JIP-3 *peer misbehaved* event (event 28) any node pushed on `lasair-pj`: each one a
node's complaint about a peer, with the peer and a reason. On that network only the
PolkaJam nodes push JIP-3, so these are PolkaJam's complaints (lesson 6.2).

</details>

### A3

```logql
sum by (node) (count_over_time({net="$net", run_id="$run_id", node=~"$node"} |~ "$search" [$__auto]))
```

<details>
<summary>Answer</summary>

Log lines per node per interval that match the search box, as numbers: the Logs
dashboard's *Log lines per node*. `count_over_time` turns lines into a count; `sum by
(node)` merges each node's streams (its container output and its JIP-3 events).

</details>

## B. Find the mistake

### B1

"Every line mentioning a package, on any network":

```logql
{} |= "0x3f1c9a02"
```

<details>
<summary>Answer</summary>

Loki needs at least one label matcher that selects something; `{}` is refused. Name the
network at least: `{net="lasair6"} |= "0x3f1c9a02"`.

</details>

### B2

"How many packages expired on lm0?"

```logql
sum(count_over_time({net="lasair-pj", node="lm0"} |= "anchor_too_old" [1d]))
```

<details>
<summary>Answer</summary>

`anchor_too_old` also matches intake refusals, `lookup_anchor_too_old`. On the `lasair-pj`
run this returns 25 for `lm0`: 1 expiry and 12 refusals logged as two lines each. Filter on
the expiry itself: `|= "EXPIRED"` returns 1.

</details>

### B3

"Errors from the lasair nodes":

```logql
{net="lasair6", client="lasair"} | json | level="error"
```

<details>
<summary>Answer</summary>

It only works if the nodes run with `LASAIR_LOG_FORMAT=json`. lasair's default lines are
plain text (`[ce133] ...`): `| json` fails on them (they get an `__error__` label), no
`level` label exists, and nothing matches. For plain lines, filter the text:
`|~ "(?i)error"`. Or turn JSON logging on and keep the query.

</details>

### B4

"Every line about package 0x3f1c9a02":

```logql
{net="lasair6", package="0x3f1c9a02"}
```

<details>
<summary>Answer</summary>

`package` is not a stream label. Stream labels are few and fixed (`net`, `run_id`, `node`,
`client`, `job`, `source`, `container`); a package hash lives inside the line. Use a filter:
`{net="lasair6"} |= "0x3f1c9a02"`, or, on JSON lines,
`{net="lasair6"} | json | package="0x3f1c9a02"`.

</details>

## C. Write it

### C1

Every line from `lm0` in one run that contains "refused", in any case.

<details>
<summary>Answer</summary>

```logql
{net="lasair6", run_id="<run_id>", node="lm0"} |~ "(?i)refused"
```

</details>

### C2

How many packages expired on each lasair node over a day.

<details>
<summary>Answer</summary>

```logql
sum by (node) (count_over_time({net="lasair-pj", client="lasair"} |= "EXPIRED" [1d]))
```

On the `lasair-pj` run: `lm0` 1, `lm1` 1.

</details>

### C3

PolkaJam's complaints, counted by node and reason.

<details>
<summary>Answer</summary>

```logql
sum by (node, reason) (count_over_time({net="lasair-pj", source="jip3"} | json | event="peer_misbehaved" [2d]))
```

On the `lasair-pj` run:

| node | `Bad incoming stream protocol 153` | `Invalid guarantee: No signature from peer eecgw…` | `… en5ej…` |
|---|---|---|---|
| pj3 | 9 | 5 | 2 |
| pj4 | 6 | 5 | 2 |
| pj5 | 6 | 5 | 2 |

The reason text names a peer, so the "No signature" complaints split into one row per
peer named.

</details>

### C4

JIP-3 events per second, per node, for a network.

<details>
<summary>Answer</summary>

```logql
sum by (node) (rate({net="<net>", source="jip3"} [1m]))
```

`rate` on a log query counts lines per second. Every JIP-3 event is one line.

</details>

### C5

Warnings per component from lasair nodes that log JSON, per 10 minutes.

<details>
<summary>Answer</summary>

```logql
sum by (component) (count_over_time({net="<net>", client="lasair"} | json | level="warn" [10m]))
```

The metric `lasair_log_lines_total{level="warn"}` gives the same numbers from Prometheus,
without reading a single line.

</details>
