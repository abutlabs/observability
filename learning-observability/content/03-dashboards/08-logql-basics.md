# LogQL basics

Loki's query language looks like PromQL on purpose. A query starts by choosing **streams**
by their labels, then **filters** the lines, optionally **parses** them, and optionally
turns them into **numbers**.

## Where to run a query

- **Grafana Explore**, datasource *Loki*. You get the lines, and a graph of how many there
  were over time.
- **The Logs dashboard**, for quick searches with its search box.
- **The HTTP API**: `http://localhost:3100/loki/api/v1/query_range` for lines,
  `.../query` for a number at one time. Times are in nanoseconds.

The examples below were run on 2026-09-28 against a `lasair-pj` run (3 lasair and 3
PolkaJam validators), run id `lasair-pj-20260928T184439Z`.

## 1. Choose streams

Every stream has the labels you know, `net`, `run_id`, `node` and `client`, plus `source`
(`docker` for container output, `jip3` for telemetry events). Container output also has
`job` and `container`.

```logql
{net="lasair-pj", client="lasair"}
```

At least one label matcher is required, and the same matchers as PromQL apply (`=`, `!=`,
`=~`, `!~`). Select the run as well once you know it: `run_id="..."`.

## 2. Filter lines

| Filter | Keeps lines that |
|---|---|
| `\|= "EXPIRED"` | contain the text |
| `!= "STATUS"` | do not contain it |
| `\|~ "(?i)refused\|expired"` | match the regular expression |
| `!~ "debug"` | do not match it |

```logql
{net="lasair-pj", client="lasair"} |= "EXPIRED"
```

Real result (two of the lines):

```text
{node="lm0", client="lasair", source="docker", ...}
  [ce133] package 0x0f6d6f346e4d111b.. EXPIRED (anchor_too_old): its context can no longer be reported; the builder must resubmit
{node="lm1", client="lasair", source="docker", ...}
  [ce133] package 0xeb6f8852b86920eb.. EXPIRED (anchor_too_old): its context can no longer be reported; the builder must resubmit
```

Filters are fast; put them before any parsing.

**Be exact about what you match.** On the same run, `|= "anchor_too_old"` matches 25 lines
on `lm0`, while `|= "EXPIRED"` matches 1. The other 24 are 12 refusals, each logged as two
lines, `[ce133] refused a CE-133 submission: lookup_anchor_too_old` and
`[ce133] refused package 0x… for core …: lookup_anchor_too_old`: packages turned away at
the door because their lookup anchor was already too old. That is a different event from a
package that was accepted and then expired, but both lines contain `anchor_too_old`.

## 3. Parse

`| json` turns a JSON line's fields into labels you can filter on. JIP-3 events are JSON,
and so are lasair's lines with `LASAIR_LOG_FORMAT=json`:

```logql
{net="lasair-pj", source="jip3"} | json | event="peer_misbehaved"
```

Real result, one line:

```json
{"event":"peer_misbehaved","id":3301,"ts":"2026-09-28T18:50:26.275860Z","peer":"4418fb8c85bb3985","reason":"Invalid guarantee: No signature from peer eecgwpgwq3noky4ijm4jmvjtmuzv44qvigciusxakq5epnrfj2utb"}
```

For lasair JSON logs, the fields are `level`, `component`, `msg`, and `package`, `slot`,
`core` and so on when a line names them:

```logql
{net="lasair6", client="lasair"} | json | level=~"warn|error"
{net="lasair6", client="lasair"} | json | component="ce133" | package="0x3f1c9a02"
```

To extract only some fields, name them: `| json event="event"` creates the label `event`
and nothing else. The Logs dashboard's *JIP-3 events by type* does that.

A line that is not JSON gets the label `__error__`. The lasair dashboards drop those with
`| __error__=""`.

## 4. Count

Wrap a query in a range function and it returns numbers, like PromQL:

```logql
sum by (node) (count_over_time({net="lasair-pj", client="lasair"} |= "EXPIRED" [1d]))
```

Real result: `lm0` 1, `lm1` 1. And the telemetry version:

```logql
sum by (node, reason) (count_over_time({net="lasair-pj", source="jip3"} | json | event="peer_misbehaved" [2d]))
```

returns one number per PolkaJam node and reason: how often each PolkaJam node said a peer
misbehaved, and why. Track 6 reads that result.

Other range functions: `rate` (lines per second), `bytes_over_time`, and, after
`| unwrap <field>`, the numeric ones (`avg_over_time`, `max_over_time`,
`quantile_over_time`) over a number inside the line.

In Grafana, write `[$__auto]` as the range and Grafana picks one that fits the graph.

## Labels or filters?

Stream labels are few and fixed (`net`, `run_id`, `node`...). Everything else, like a
package hash or an event name, stays *inside* the line and is reached with a filter or a
parser. This is deliberate: Loki indexes only labels, and a label per package would create
a stream per package.

Practice: [LogQL exercises](../../exercises/logql.md).

Next: [What a soak proves](../04-soak-tests/01-what-a-soak-proves.md)
