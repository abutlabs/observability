# Refine time

*The question: how long did the validators take to handle a full round?*

A DEX round is a work-package. The guarantors of the core it is sent to must **refine** it
(run the service's refine code in the PVM over the package's orders), sign a work-report,
collect a co-guarantor's signature, and distribute the guarantee so a block can include it.
Refining is the heavy part, and its cost grows with the round.

Open **lasair validator duties** for the run. It reads lasair's own metrics, which the
lasair 2.1.2 nodes exported.

## Clue 5: refine time climbed sixfold

The panel **Refine seconds (average per package)** is the guarantor worker's average time to
refine one package, per node:

```promql
rate(lasair_guarantor_refine_seconds_sum{run_id="lasair6-20260928T153915Z"}[5m])
  / rate(lasair_guarantor_refine_seconds_count{run_id="lasair6-20260928T153915Z"}[5m])
```

Taking the slowest node at each minute (wrap the query in `max(...)`):

| Minute | 5 | 10 | 20 | 25 | 27 | 35 | 40 | 50 | 55 | 63 |
|---|---|---|---|---|---|---|---|---|---|---|
| Seconds per package | 1.8 | 1.4 | 1.7 | 4.6 | 5.7 | **13.2** | 9.8 | 11.8 | 12.5 | 12.1 |

About 2 seconds per package while rounds were small; from minute 24, as full rounds reached
the guarantors, it climbs; from minute 35 on it stays between roughly 6 and 13 seconds, and
in the last quarter hour between 10.5 and 12.5. The investigation summed it up as
"Refine seconds climbs from 2 s to 12–15 s". Individual packages took longer than these
five-minute averages: a full round took lasair **14 to 27 seconds** to refine.

Why so slow? A full 48-order round is a work-package of **253M gas**, and lasair's PVM ran
at about 18M gas per second. At that speed, 253M gas is 14 seconds before anything else
happens.

## Clue 6: fewer guarantees

The panel **CE-133 guaranteed and CE-135 included per minute** shows the result. Summed
over the six nodes:

```promql
sum(60 * rate(lasair_ce133_guaranteed_total{run_id="lasair6-20260928T153915Z"}[5m]))
```

| Minute | 5 | 15 | 25 | 30 | 35 | 45 | 50 | 55 | 60 |
|---|---|---|---|---|---|---|---|---|---|
| Work-items guaranteed per minute | 4.3 | 5.7 | 4.1 | 3.5 | 1.4 | 1.2 | 0 | 1.2 | 0 |

From minute 35, guarantees fall to about one a minute, some five-minute windows none at
all. Over the whole run the six nodes guaranteed **140** work-items.

## Clue 7: packages expired

The panel **Packages expired (anchor too old), per 10 min**:

```promql
sum(increase(lasair_ce133_expired_total{run_id="lasair6-20260928T153915Z"}[10m]))
```

reads between 2 and 4 in every ten-minute window after the first, **17** over the run, most of them on one
node (`lm0`: 10). An expired package is a round that was accepted, worked on, and then could
not be reported anymore. Its orders go back in line, into the next round, which is full.

The panel's title names the reason: *anchor too old*. That is the lead to follow.

## What the panels cannot say

The panels show averages per five minutes. They cannot say what happened to one package:
when it arrived, when it was refined, when it expired, and why "too old" applied to it.
For the individual case you need the logs.

Two more panels on this dashboard would help, and this run does not have them:

- **Package anchor age on arrival** reads `lasair_ce133_anchor_age_slots`, a metric the
  investigation added *because of this run*. The 2.1.2 nodes did not export it.
- The **work-package lifecycle** dashboard's stage timings and outcomes by `last_stage`
  came later still, with lasair's `jam_*` telemetry.

This is normal. An investigation often ends by adding the measurement that would have made
it shorter.

## What we know now

- Full rounds took lasair 14 to 27 seconds to refine; the five-minute averages rose from
  about 2 to over 12 seconds.
- Guarantees fell to about one a minute.
- Packages expired with the reason "anchor too old".

**Next question: what happened to one package that expired?**

Next: [The logs](04-the-logs.md)
