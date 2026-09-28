# When something looks wrong: the drill-down path

A red stat, a missing node, a line that stops. The first reaction is to guess. The better
one is to walk down, each level answering one question, until the answer is in front of
you.

```text
  1. Run tile        is the run active, ended on purpose, or stalled?
        │
  2. whole run       (if the range does not cover it) set it to the run's own start → end
        │
  3. Nodes table     which node, and how: advancing, stopped during the run, net stalled?
        │
  4. Node detail     why it stopped: Why it stopped, Last words, Lifecycle
        │
  5. Explore         its logs across the whole net, or any metric by hand
```

Two rules make the top of this path trustworthy, and both come from the same idea: judge a
run by what happened *during* it, not by the clock at the moment you happen to be looking.

- **"Stopped during the run" is relative, not absolute.** A node counts as stopped only if
  its best slot went quiet more than a minute before the rest of the net's did. A network
  taken down all at once — every node stopping within the same few seconds — is a
  teardown, and a teardown is never counted, however long ago it happened.
- **An ended run is judged at its end.** Once a run has a recorded end, every run-scoped
  stat and table on it reads its last figures as of that end, not as of "now". A month-old
  finished run still shows what was true when it finished, not "no data".

## Level 1: the Run tile

The top-left tile of the Network overview reads the run's own record:

- **blue, "run active since HH:MM:SS"** — no end recorded yet;
- **grey, "run ended HH:MM:SS — net shut down"** — it has an end, and that is all this
  tile says: the run is over, nothing here is a failure;
- **red, "net stalled since HH:MM:SS — run not ended"** — no node's best slot has moved
  for over a minute, and there is *no* recorded end. This is the one that means look
  closer: the net has gone quiet without anyone telling the stack it was supposed to.

A run with no `obs begin`/`obs end` record at all (lesson 1.4) shows "no run record"; the
tile has nothing to read.

## Level 2: whole run

If the time range does not cover the whole run — the default *Last 1 hour* on a run from
last week, say — every run-scoped stat and table looks empty or stale before you have even
started. Click **whole run**, top right of any run-scoped dashboard. It sets the range to
the run's recorded start through its recorded end (a minute either side), the same range
`./obs link` prints. A live run has no end yet, so its whole-run range runs to "now" and
keeps moving.

## Level 3: the Nodes table

Back on the Network overview, the Nodes table's **state** column says which node, and how:

- **advancing** — its best slot moved within a minute of the net's newest move, as of the
  run's end (or now, for a live run);
- **stopped during the run** (red) — it went quiet more than a minute before the rest of
  the net's did, while the run was still on;
- **net stalled** (grey) — no node in the net was still advancing.

`last advanced` says when its best slot last moved; `stopped`, `why` and `exit code` come
from the lifecycle service's Docker events, and are empty for a node that never stopped in
the range (or that is not a Docker container at all — lifecycle only watches Docker).
Click a row, or a node's line on any graph (Chain health, lasair's, jamswap's), for **Node
detail**.

## Level 4: Node detail

One node, one page:

- **State**: the same three values as the table, for this node alone.
- **Why it stopped**: the container's last stop in the range, from Docker (via
  lifecycle): *teardown* (the net was shut down), *killed* (a signal, no stop first, such
  as `docker kill`), *oom* (the kernel killed it for memory), *crashed* (exited non-zero
  by itself, with its code), *stopped* (`docker stop`) or *exited* (0, by itself). Red
  when it was killed, OOM-killed or crashed while its run was still on. Orange, *running,
  stalled since*, means the container is still up but its best slot has not moved for over
  a minute — hung, or cut off (check *Scraped* and *Last scrape* beside it). A node
  registered with `./obs register` rather than run in Docker has no lifecycle record at
  all, so this stays "not stopped" even after it goes quiet: only the state and the
  metrics can tell you it stopped.
- **Last words**: its last 20 log lines in the range — what it said just before, if
  anything.
- **Lifecycle**: every Docker event for its container (create, start, restart, kill, oom,
  stop, die with its exit code, destroy), newest first, from the same source as the *Why
  it stopped* stat.
- Below: its chain, work-packages, resources, then its full logs.

## Level 5: Explore

For anything the dashboards do not already show — a metric by hand, a search across every
node's logs at once, a query you are still shaping — open Grafana's **Explore** with the
Prometheus or Loki datasource. Every per-node line and table row links to it, scoped to
that node and the run's time range.

## Worked example 1: a PASS run, six nodes "stopped"

Run `lasair6-20260928T173608Z`: a one-hour soak of six lasair validators. Opened on the
Network overview with its own link:

- **Run**: grey, *run ended 20:41:43 — net shut down* (CEST; 18:41:43 UTC). `./dex down`
  recorded the end and then removed the network.
- **Nodes advancing 6, Nodes stopped during the run 0**, both green.
- **Nodes table**: lm0 through lm5 all read *advancing*, `last advanced` 20:41:26 for
  every one of them — sixteen seconds before the run's own end, and the same instant for
  all six. Nodes that fail rarely fail within the same second; a network being switched
  off does.

Nothing here needed the lower levels: the Run tile alone answers it. If you saw only "6
nodes stopped" without the Run tile's context, the annotations confirm the same story —
green marks ending with `VERDICT (orders + chain): PASS` at 20:41:13, then the blue
`lasair6 down` mark at 20:41:42 — a soak that passed, then a network taken down on
purpose. **whole run** shows the same picture over the full hour: best and finalized slots
climbing together from 0 to about 1047, and two purple lifecycle marks, `run_begin` at
the start and `run_end` at 20:41:43, and nothing red in between.

(This run predates the lifecycle service, so its six node containers left no Docker
events — they were long gone before the service existed to watch them. The `run_begin`
and `run_end` marks exist anyway, because the lifecycle service found this run's record
on disk and read it once it started, whenever that was. What it could not do is back-date
*Prometheus* samples for `obs_run_*` on its own: a live gauge only speaks from now on. That
gap is what `./obs backfill` closes (lesson 1.4), writing those series over the run's own
historical range. Without it, the Run tile would say "no run record" and the Nodes table
would read *net stalled*, since with no recorded end the stats would describe the range's
end — a minute after the network had already gone.)

## Worked example 2: a kill test, one node red

Run `lasair-pj-20260928T202556Z`, a mixed lasair/PolkaJam net, deliberately killed to
check the stack notices: `docker kill` was sent to node `lm1` at **22:27:44 CEST**
(20:27:44 UTC).

- The Nodes table shows `lm1` **stopped during the run** (red), `last advanced` 22:28:28,
  `stopped` 22:27:44, `why` **killed**, `exit code` **137**. The other five nodes (lm0,
  lm2, and three PolkaJam nodes) read *advancing*: `lm1` was the only one that fell
  behind, by about seven minutes, while the rest of the net kept going.
- A **red annotation** marks 22:27:44 on every graph: `reason=killed` while its run was
  still active matches the lifecycle service's rule for a red mark (a graceful stop or a
  clean exit does not).
- Node detail for `lm1`: **State** *stopped during the run*; **Why it stopped** *killed* at
  22:27:44, red; **Exit code** 137 (SIGKILL — a `docker kill`, an OOM kill, or a stop that
  timed out); **Lifecycle** lists the `die` event with its signal and exit code; **Last
  words** shows whatever `lm1` logged in the seconds before.

The net was later torn down normally, so its own Run tile ends up grey, *run ended
22:35:34 — net shut down* — the run as a whole finished cleanly. That does not undo
`lm1`'s red: the run's own ending and one node falling behind partway through it are two
different facts, and the dashboards keep them separate. This is the case the first worked
example is not: one line stops while the others go on, a reason and an exit code are on
record, and the annotation is red, not blue.

## What a real failure looks like, in one line

**one** node red in the Nodes table (not all of them, not none), a red annotation instead
of a blue `down` mark at that moment, and Node detail's *Why it stopped* giving a reason
(`killed`, `oom`, `crashed`) with an exit code, or *Last words* showing an error before it
went quiet.

Next: [PromQL basics](07-promql-basics.md)
