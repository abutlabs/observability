# The DEX and Soak runs dashboards

jamswap is a decentralised exchange on JAM: its off-chain server batches orders into
*rounds*, and each round is a work-package the validators guarantee, make available, audit
and accumulate. It is the application the soaks run, and its two dashboards are in the
`jamswap` folder.

## DEX (`obs-dex`)

**Is the DEX turning offered load into cleared trades?** The load generator offers
operations; the DEX should place them as orders and clear them. Errors and "busy" replies
are load it turned away.

### The top row

| Stat | Passes when | Reads |
|---|---|---|
| Clearing SLO (soak verdict) | ≥ 0.9999 | the soak's pushed verdict (`soak_check_value{check="clearing_slo"}`): cleared ÷ (cleared + missed + stuck open) over orders that could trade. Empty until a soak of this run has judged it. |
| Cleared of settled orders (live, info) | | `jamswap_order_clearing_slo`, the DEX's running tally: filled ÷ (filled + expired, lost, rejected, partially cancelled) over orders that **reached an end**. A backlog of still-open orders does not lower it. |
| Orders open too long | = 0 | orders with no event for 10 minutes (resting ones, 10 minutes past their own expiry): what the verdict counts as stuck open, as it builds |
| Load turned away | ≤ 0.01% | the load generator's errors plus busy replies, over its offered operations |
| Settle timeouts | 0 | rounds that timed out waiting to settle |
| Settlements reverted | 0 | settlements undone |
| Clear latency p99, 10 min (info) | | placement to durable fill |

The headline is the soak verdict, not the live gauge. The live gauge only ever sees orders
that finished, so it reads 1.0 while a backlog silently builds; watch **Orders open too
long** beside it for that.

### The panels

- **Offered load (ops/s, by op)**: `buy`, `sell`, `sealed_sell`. Flat and steady during a
  soak.
- **Turned away (ops/s, by op)**: should be empty. When it is not, the DEX is refusing
  orders.
- **Orders placed and terminal (per s)**: placed against how they ended (`filled`,
  `expired`, ...).
- **Cleared of settled orders (live)** over time, and **Orders open, and open too long**:
  live orders by phase, those silent past the grace period, and how long the oldest has
  gone without an event.
- **Clearing latency**: p50 and p99 from placement to fill, and settle latency by op.
- **Refused (per min, by op)**, and API errors (5xx) by route.
- **Round sizes**: the average number of orders per round, by market, with orders in
  flight and orders waiting in the mempool.
- **Rounds abandoned and late-landed (per 10 min).**
- **Treasury reserve.**

### Round sizes: the panel to watch under load

A round carries up to 48 orders. When the network settles rounds as fast as orders arrive,
rounds stay small. When it falls behind, orders pile up in the mempool, the next round is
full, and a full round is the DEX's most expensive work-package. A *Round sizes* line
pinned at 48 with a growing mempool is a system falling behind.

### A gauge that only counts what has ended

**Cleared of settled orders (live)** is a gauge the DEX computes: cleared over
(cleared + missed), among marketable orders **that reached an end state**. An order stuck
open is not yet counted as missed, so it can read `1` while a backlog builds. The soak's
verdict, which is now the dashboard's headline stat, counts it: at the end, any order
still open past its grace period is a miss.

On the case study's failing run, the live gauge read `1` on every sample, while the soak's
verdict was 0.6643 (572 cleared, 289 stuck open). The live tile was right about the orders
that had ended and blind to the ones that never did. **The soak's verdict is the source of
truth; the live gauge is an early indicator.** When the live gauge is 1.0 but *Orders open
too long* is not 0 and the mempool keeps growing, believe the mempool.

## Soak runs (`obs-soak-runs`)

A soak pushes its configuration, progress and results to the Pushgateway, grouped by its
run id. This dashboard reads them. It has two halves.

### The selected run

Pick a net and a run:

- **Verdict** (PASS or FAIL), **Phase** (`starting`, `load`, `drain`, `parity`, `verdict`,
  `done`), **Load left** in seconds, **Duration**, **Last report** (seconds since the soak
  last pushed; a big number while the phase is not `done` means the soak died), **Orders
  offered**.
- **Configuration**: clients, lasair image, data directory, DEX backend, load profile, rate,
  sealed ratio, jamswap commit, duration and drain.
- **One tile per check**, green passed, red failed, each showing what was measured and what
  it had to reach: offered load, clearing SLO, SEALED zero-loss, one head, liveness,
  finality, authoring, state parity. Then clear latency p50 and p99, refused or busy, and
  finalized slots checked.
- **Links** to Chain health, Network overview, lasair validator duties, DEX, Memory and
  Logs for the same run and time range.

### Every soak so far

A table of every soak, newest first: run id, net, clients, lasair image, start, duration,
clearing SLO, refused, sealed stuck, latency p50 and p99, PASS or FAIL. Click a run id to
open it above with its time range. This is where you compare builds: the same net, the same
load, two lasair images, side by side.

The soak's metrics are named `soak_*` (`soak_pass`, `soak_check{check, measured,
threshold}`, `soak_orders_refused`, `soak_clear_latency_seconds{quantile}`, ...), all
labelled with `job="soak"`, the `run_id` and the `net`. Runs from before the soak pushed to
the Pushgateway, such as the two in the case study, do not appear here; their verdicts are
in their `REPORT.md` files (track 4).

Next: [When something looks wrong: the drill-down path](06-the-drill-down-path.md)
