# The DEX backlog

*The question: when did orders stop clearing, and what did the DEX see?*

Open the **DEX** dashboard for the run (lesson 5.1 has the link). Times below are minutes
from the start of the run (15:39:15 UTC).

## Clue 1: the offered load never changed

*Offered load (ops/s, by op)* is flat for the whole hour: `buy`, `sell` and `sealed_sell`
at the soak's steady rate, 1,440 operations in an hour. Whatever went wrong, it was not a
burst of traffic.

```promql
sum(rate(loadgen_ops_total{run_id="lasair6-20260928T153915Z"}[5m]))
```

## Clue 2: refusals began half an hour in

*Turned away (ops/s, by op)* is empty for the first half hour, then fills. The
cumulative count shows exactly when:

```promql
sum(loadgen_op_errors_total{run_id="lasair6-20260928T153915Z"})
```

| Minute | 30 | 31 | 32 | 33 | 40 | 50 | 60 |
|---|---|---|---|---|---|---|---|
| Refused so far | 0 | 10 | 24 | 43 | 127 | 296 | 489 |

(Values sampled from the recorded series. The series only exists from the first refusal,
so before minute 31 the query returns nothing: 0.)

## Clue 3: the rounds hit their cap first

*Round sizes* shows the average number of orders per round. The DEX batches orders into
rounds of up to 48; small rounds mean it is keeping up.

```promql
sum by (market) (rate(jamswap_round_orders_sum{run_id="lasair6-20260928T153915Z"}[5m]))
  / sum by (market) (rate(jamswap_round_orders_count{run_id="lasair6-20260928T153915Z"}[5m]))
```

For market 1:

| Minute | 5 | 10 | 15 | 19 | 20 | 21 | 25 | 30 to 60 |
|---|---|---|---|---|---|---|---|---|
| Orders per round | 9.8 | 8.6 | 15.7 | 8.2 | 21.7 | **48** | 48 | 48 |

From minute 21 on, every market-1 round is full. The same panel's mempool line (orders
waiting for a round) climbs behind it: 135 at minute 25, 253 at minute 30, 344 at its peak
near minute 50.

So the order of events is: **rounds fill up at minute 21, refusals start at minute 31.**
The refusals are downstream. Something made rounds stop keeping up about ten minutes
before the DEX began turning orders away.

## Clue 4: the headline tile said everything was fine

The *Clearing SLO · PASS ≥ 0.9999* tile at the top is green, and its graph reads 1 on every
sample of this run:

```promql
min(jamswap_order_clearing_slo{run_id="lasair6-20260928T153915Z"})
```

The DEX computes that gauge from orders that have **ended**. The 289 orders that were never
settled never ended, so they never counted as missed. The soak's verdict, taken at the end
with a 600-second limit, found them. Note it and move on: the tile is an early warning for
orders that fail, not for orders that wait.

## Reading the clues together

A full round is the DEX's heaviest work-package: one 48-order round is a work-package of
253M gas. When rounds settle slowly, the next round has more orders waiting, so it is full,
so it is heavy, so it settles slowly. The backlog feeds itself. Once it builds, the DEX
holds more and more open orders per account until it hits the cap of 50 and refuses new
ones.

What turns an order into a settled one? Its round's work-package must be refined and
guaranteed by the core's guarantors, then made available, audited and accumulated, and its
block finalized. The chain checks already clear finality and consensus. Of the other
steps, the one whose cost grows with the size of the round is the refine, done by the
guarantors. That is the first place to look.

## A detour: Chain health

For completeness, open **Chain health** for the same run. Every stat is green for the
whole hour: `jam_net_one_head` is 1 on every sample, finality lag never exceeds 2 slots on
any node, and there are no finality conflicts. Suspect 2 keeps its alibi.

## What we know now

- The DEX was not overwhelmed by traffic; its rounds stopped settling fast enough.
- It started about 20 minutes in, when market-1 rounds reached 48 orders.
- The chain was fine.

**Next question: how long did the validators take to handle a full round?**

Next: [Refine time](03-refine-time.md)
