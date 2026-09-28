# Where the window goes

*The question: where did the rest of the window go?*

## The budget

A work-package's life, from the guarantor's point of view, is a race against its anchor:

```text
 anchor block made                                          anchor leaves the last 8 blocks
 │◀────────────────── 8 blocks (about 48 s at 6 s per slot) ──────────────────▶│
 │                                                                              │
 │ builder picks the anchor, builds the package, sends it                       │
 │─────────── already gone on arrival ───────────▶│                             │
 │                                                │ accept, share (CE-134)      │
 │                                                │ refine                      │
 │                                                │ sign, collect co-signature  │
 │                                                │ distribute the guarantee    │
 │                                                │ wait for a block to include it
```

Everything the guarantor does must fit in what is left when the package arrives. Three
things decide whether it fits:

1. **How much of the window is gone on arrival** (the anchor's age when the package
   reaches the guarantor);
2. **how long the guarantor takes** (refine is most of it);
3. **how soon a block includes the guarantee** (the next slot, if the core is free).

## The traced package, on the budget

Put the times from lesson 5.4 on it:

```text
 anchor ~16:09:23                                                   window closes ~16:10:11
 │◀──────────────── ~25 s gone ─────────────▶│◀──── 14.4 s refine ────▶│◀─ 9 s ─▶│ ✗
                                        16:09:48                   16:10:02    16:10:11
                                        accepted                   refined     EXPIRED
```

The package arrived with about half of its window, about 25 of the 48 seconds, already
gone. The guarantor's own refine took 14.4 seconds of the 23 that were left. The last 9 were
not enough for the guarantee to be distributed and land in a block: the refine plus the
next slot used the rest.

(The anchor time is worked back from the window, not logged: 16:10:11 minus 48 seconds.)

Each of the three terms was against it:

| Term | In this package | Why |
|---|---|---|
| Gone on arrival | ~25 s | the builder's side: choosing an anchor, building, sending |
| Guarantor time | 14.4 s refine | a full 48-order round is 253M gas at about 18M gas/s |
| Waiting for a block | > 9 s | the guarantee still had to be distributed and carried by the next block |

Any one of them smaller, and the package would have made it. With rounds of about 10
orders, the refine took about 2 seconds and the same budget had room to spare. That is why
the first 20 minutes kept up: a few packages expired even then (as a few still do after the
fix, lesson 5.6), but rounds stayed small and nothing was refused.

## Why the mixed network passes

The same load passes on `lasair-pj` (three lasair and three PolkaJam validators). jamswap's
soak results put it plainly: on the mixed network, PolkaJam's co-guarantors are fast
enough, so the same load passes. The window is the same; the guarantor time is not.

## Seeing the budget directly

This run could not show the budget on a dashboard: its nodes exported neither anchor age
nor stage times. lasair builds since then do, and the **work-package lifecycle** dashboard
(`lasair-wp`) reads it for you:

- **Package anchor age on arrival (slots) · window = 8**: term 1, in slots (for seconds,
  multiply by the slot pace; see the loose end below).

  ```promql
  histogram_quantile(0.5, sum by (le) (rate(jam_wp_anchor_age_slots_bucket{net="$net",run_id="$run_id"}[10m])))
  ```

  (`lasair_ce133_anchor_age_slots` on the builds of this case study; the passing run has
  it, the failing run does not.)

- **Median and p90 time from acceptance to each stage**: term 2 and part of term 3. The
  bar for `refined` is the refine; the step from `refined` to `guaranteed` is signing,
  co-signing and distribution; `included` is when a block carried it.

  ```promql
  histogram_quantile(0.5, sum by (le) (increase(jam_wp_stage_seconds_bucket{net="$net",run_id="$run_id",stage="refined"}[$__range])))
  ```

- **How far expired packages got**: `expired` outcomes by `last_stage`. `last_stage =
  refined` is this case exactly: refined, never guaranteed in time.

  ```promql
  sum by (last_stage, reason) (increase(lasair_wp_outcomes_total{net="$net",run_id="$run_id",outcome="expired"}[$__range]))
  ```

The investigation's first action was the first of these: add the anchor-age metric and
its panel, so the next run would measure term 1 instead of inferring it.

## A loose end: how long is 8 blocks here?

The window is defined in **blocks**. The investigation converted it at 6 seconds per slot:
8 blocks, 48 seconds. Measure that instead of assuming it. The soak's own liveness check
says the best block advanced 1,055 slots in 3,775 seconds on the failing run (1,014 to
1,015 on the passing one): one slot every 3.6 to 3.7 seconds on average, not 6. So does
PromQL, in slots per second:

```promql
deriv(max(jam_best_slot{run_id="lasair6-20260928T153915Z"})[10m:])
```

which returns between 0.26 and 0.30 across the failing run. At that pace, 8 blocks is
closer to 30 seconds than to 48. Then the traced package, which lived 23 seconds after it
was accepted, would have arrived only about 7 seconds old, not with half its window gone.
Both cannot be right, and the record does not say which is.

This does not change the diagnosis: whatever the window is in seconds, a 14-second refine
spent most of what the package had left. But it is an open question in the record, and a
good habit to copy: **when a conclusion rests on a conversion, measure the thing you
converted.**

## The decision

Three levers, then:

1. **Fresher anchors**: have packages arrive with more of the window left.
2. **Faster refine**: spend less of it.
3. **Less waiting for a core**: get the guarantee into a block sooner.

Which to pull first? The numbers point at the second. Refine time is the term the
dashboards showed growing tenfold as the rounds grew (from about 2 s to over 12 s on average, 14 to 27 s for
a full round), and the one entirely inside lasair's own code. At about 18M gas per second a
253M-gas round takes 14 seconds, a large share of what is left of the window when the
package arrives, however fresh its anchor. The target: make a full round refine fast
enough to fit the window with room to spare.

**Next question: what was the fix, and how do you prove it worked?**

Next: [The fix and the proof](06-the-fix-and-the-proof.md)
