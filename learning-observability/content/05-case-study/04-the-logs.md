# The logs

*The question: what happened to one package that expired?*

Metrics count; logs tell. lasair writes a line in its `ce133` component (work-package intake
and the guarantor pipeline) at each decision about a package, and one when a package
expires:

```text
[ce133] package 0x3f1c9a02.. EXPIRED (anchor_too_old): its context can no longer be reported; the builder must resubmit
```

Every line carries the package's hash prefix, so one package can be followed through every
node's log.

## Finding the lines

On any run whose container logs reached Loki, one query finds every expiry on every node:

```logql
{net="lasair6", run_id="<run_id>", client="lasair"} |= "EXPIRED"
```

or, with `LASAIR_LOG_FORMAT=json` on the nodes, filtered on fields:

```logql
{net="lasair6", run_id="<run_id>"} |= "EXPIRED" | json | component="ce133"
```

Then take one package's prefix and search for it alone: in the **Logs** dashboard, type it
into the search box, and every line about that package, from every node, appears in time
order.

Two cautions apply here (the first is from lesson 3.8):

- Search for `EXPIRED`, not just `anchor_too_old`. The second also matches packages
  *refused at intake* with `lookup_anchor_too_old`, a different event.
- Search for a package by the first 8 hex digits of its hash. Lines print at least that
  many, so the short prefix matches them all.

**This run is the exception.** It was recorded before the stack collected container logs,
so its lines are not in Loki. The investigation read the nodes' logs directly and recorded
what it found, below. If you repeat this run today (lesson 5.6 shows how), the queries
above find the same kind of lines in Loki.

## One package, traced

The investigation followed one package on one guarantor. Times in UTC, about 30 minutes
into the run, when the backlog was building:

| Time | What the logs showed |
|---|---|
| 16:09:48 | the package is **accepted** and **shared** with the core's co-guarantors (CE-134) |
| 16:09:59 to 16:10:00 | **both co-guarantors signed**: they had refined it too, and agreed |
| 16:10:02 | **our own refine done**, after 14.4 seconds |
| 16:10:11 | **`EXPIRED (anchor_too_old)`** |

And on the whole node, `lm0` logged 5 such expiries in 20 minutes.

Read the table slowly, because everything is in it.

- The package was fine. Both co-guarantors refined it and signed the same report.
- The work was done: by 16:10:02, 14 seconds after acceptance, this guarantor had
  everything it needed to guarantee the package.
- Nine seconds later, it was too late.

So "too slow" is not quite the diagnosis. The package was refined, co-signed and ready,
and it still expired. Something was running out, and the clock for it had started before
16:09:48.

## What "anchor too old" means

Every work-package names an **anchor**: a recent block its work is based on. A guarantee
for it is only valid while that anchor is among the last **8 blocks**. With 6-second slots,
that is a window of about **48 seconds** (lesson 5.5 checks that number against the run),
and the clock starts when the anchor block was made, not when the guarantor receives the
package.

"Anchor too old" means the window closed before the guarantee could be included in a
block. The package must be resubmitted, with a fresh anchor, as a new package, and its
orders wait again.

## What we know now

- Expired packages were valid and fully refined; they ran out of time, not correctness.
- The time that ran out is the anchor window: 8 blocks from the anchor block.
- A guarantor spent 14.4 seconds of that window on its own refine.

**Next question: where did the rest of the window go?**

Next: [Where the window goes](05-where-the-window-goes.md)
