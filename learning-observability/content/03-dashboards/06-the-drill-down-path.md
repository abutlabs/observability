# When something looks wrong: the drill-down path

A red stat, a missing node, a line that stops. The first reaction is to guess. The better
one is to walk down four levels, each answering one question, until the answer is in front
of you.

```text
  1. overview        what is wrong, on how many nodes, since when?
        │
  2. the node        what did this node (or these nodes) do just before?
        │
  3. its logs        what did it say, last?
        │
  4. its lifecycle   did its container stop, crash (with what exit code), run out of memory?
```

> **Changing soon.** Work in progress on the stack adds a **Node detail** dashboard (a
> node's metrics, its logs and its "last words" before it stopped), links from every node
> row of the Network overview to Node detail and to its logs, Docker lifecycle events
> (start, stop, die with exit code, out-of-memory) as annotations and log lines, and runs
> that snap the time range to themselves and show as *ended* instead of red once over.
> Names and screens in this lesson may change when that lands. The path stays the same.

## Level 1: the overview

Start at **Network overview** or **Chain health**. Answer three things before going
further:

- **How many nodes?** One node is that node's problem. All nodes at once is the network's,
  or the run's.
- **Since when?** Hover over the graphs to find the moment the lines change.
- **What else happened then?** Look for an annotation at that moment: `load on`, a
  verdict, `down`.

## Level 2: the node

Follow the node's own lines: its row in the Nodes table, its line in *Best slot per node*.
Did it fall behind slowly (a line drifting below the others) or stop dead (a line that
ends)? For a lasair node, the **lasair node overview** with that node picked shows its
peers, memory, import time and warnings per minute just before.

*Coming: Node detail puts all of this, for any client, on one page, one click from its row.*

## Level 3: its logs

Open **Logs**, pick the run and the node, and read the last lines before the moment you
found. Search for `(?i)error|panic|killed` if there are many. A node that crashed usually
says something first; a node that was stopped usually says nothing at all.

## Level 4: its lifecycle

Did the container stop because someone stopped it, or because it died? While the container
still exists, Docker can tell you:

```sh
docker ps -a --filter name=<container>
docker inspect <container> --format 'exit {{.State.ExitCode}}  oom-killed {{.State.OOMKilled}}  finished {{.State.FinishedAt}}'
```

Exit code 0 is a clean exit. 137 means it was killed (signal 9), often by the kernel for
running out of memory, in which case `OOMKilled` is `true`. Any other non-zero code is the
program's own error exit.

*Coming: these events as annotations on every dashboard and as log lines, kept after the
container is gone.*

## Worked example: six nodes stopped reporting

Aodh opened the **Network overview** for run `lasair6-20260928T173608Z` and saw, in red:

```text
 Nodes stopped reporting (15 min) · PASS = 0        6
```

Six of six. Down the path:

**Level 1, the overview.** All six nodes, not one. On *Best slot per node* the six lines
climb together for an hour and end together: the last samples are between 18:41:39 and
18:41:42 UTC, all within three seconds. Nodes that fail rarely fail within the same three
seconds; a network being switched off does.

**What else happened then?** The annotations say it. At 18:41:11 to 18:41:13 UTC, green
marks: the soak's checks, one by one, ending with `VERDICT (orders + chain): PASS`. At
18:41:42 UTC, a blue mark: `lasair6 down`. And the run's record agrees:

```text
$ ./obs runs --net lasair6
lasair6-20260928T173608Z           lasair6      2026-09-28 17:36 -> 2026-09-28 18:41
lasair6-20260928T153915Z           lasair6      2026-09-28 15:39 -> 2026-09-28 16:43
```

`./obs runs --json` gives the end to the second, as Unix time `1790620903`: 18:41:43 UTC,
which Aodh's Grafana, in central European summer time, shows as **20:41:43**. That is `./dex down` removing the network, as `soak/run` does once
the soak is over.

**Levels 2 to 4** are not needed here: the first level already shows a planned end. (For
this run they are not available either: it was recorded before the stack collected
container logs.)

**Answer: a normal end, not a failure.** The soak passed, then the network was taken down
on purpose.

### Why the stat was red anyway

The stat counts nodes that reported at some point in the last 15 minutes but do not report
now. That is exactly right *during* a run: a node that stops is news. After a run, every
node has stopped, and the stat stays at 6 for 15 minutes after the end. The link `obs link`
prints for a finished run reaches one minute past its end, so the stat's last value, the
one it shows, is 6.

When you see it on a finished run, check the end first: `./obs runs`, the `down`
annotation, the soak's verdict marks. (With the drill-down work above, a finished run shows
as *ended* instead of red.)

## What a real failure looks like

For contrast, the same stat when something did go wrong:

- **one** line stops while the others go on;
- no `down` or verdict annotation at that moment;
- the node's last log lines show an error, or the node's memory had been climbing
  (Memory dashboard) before it stopped;
- Docker says `exit 137, oom-killed true`, or another non-zero exit code.

Then the next level down is that client's own dashboards and code.

Next: [PromQL basics](07-promql-basics.md)
