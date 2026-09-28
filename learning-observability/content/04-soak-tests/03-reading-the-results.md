# Reading the results

A finished soak leaves three things to read: `REPORT.md`, the raw files next to it, and the
Soak runs dashboard. This lesson reads the case study's failing run with the first two
(it ran before soaks pushed their results to the dashboard).

## REPORT.md

`soak/run` writes it at the end. For an older run, or to rewrite one, run from the jamswap
checkout:

```sh
python3 soak/report.py ~/.cache/jamswap/soak/<net>-<time> > ~/.cache/jamswap/soak/<net>-<time>/REPORT.md
```

It has five parts: **what was tested** (net, validators, lasair image, storage, load,
duration, run id), **results** (every check with its threshold, result and what it means),
**why orders were refused** (counted from the load generator's log), **dashboard links**
for the run, and **how to reproduce** it.

Here are the results and the refusal reasons of the failing run, produced on 2026-09-28
with `python3 soak/report.py ~/.cache/jamswap/soak/lasair6-20260928T154026Z --run-id
lasair6-20260928T153915Z` (the "Detail" column trimmed):

| Check | Threshold | Result | Detail |
|---|---|---|---|
| offered load | ≤ 0.01% turned away | **FAIL** | 1440 offered, 519 refused, 0 busy |
| clearing SLO | ≥ 0.9999 | **FAIL** | 0.664344 |
| SEALED zero-loss | 0 stuck | **FAIL** | seen 101, terminal 62, stuck-open 33 |
| one head | every sample | PASS | 630/630 samples ok, max lag 1 slots |
| liveness | blocks advance | PASS | best advanced 1055 slots per node over 3775.2 s |
| finality | 0 conflicts, stall < 1 epoch | PASS | 0 conflicts, longest stall 2 slots vs 12, 659 finalized slots hash-checked |
| authoring (pi) | every validator | PASS | blocks per validator 181, 179, 166, 184, 186, 167 |
| state parity | all digests agree | PASS | all digests agree at final slot 1074 |
| clear latency | (information) | | p50 160.1 s, p99 1623.5 s |

**Why orders were refused** (from the load generator's log):

```text
- 523 × Bad Request: open-order limit reached (N per market) — cancel or let some clear before placing more
```

Read it the way you would read any verdict:

1. **Which group failed?** All three application checks. All five chain checks passed. So
   the chain is not the problem; something between the orders and the chain is.
2. **What does the failing check measure?** The refusals are the DEX refusing new orders
   because each account already had the maximum of open orders (50 per market). Orders
   were not clearing, so they piled up until the cap turned new ones away.
3. **What changed over time?** The report is a summary of the whole hour. For *when*, go
   to the dashboards (track 5).

This run predates the soak recording its configuration, so its "What was tested" table
says "not recorded" in places. The results table in jamswap's
`soak/reports/2026-09-28.md` has its configuration: lasair 2.1.2, durable storage
(`/data`), one hour.

## The raw files

**`verdict.txt`** has more than the report shows. From the same run:

```text
orders seen         : 921
clearing SLO        : 0.664344  (target 0.9999)  FAIL
  cleared           : 572
  missed            : 289  (expired/lost 0, stuck-open 289)
breakdown           : {'cleared': 572, 'resting': 5, 'expired-nonmarketable': 3, 'open': 341}
...
SEALED zero-loss    : FAIL  (seen 101, terminal 62, stuck-open 33)
...
clear latency       : p50 160.1s  p99 1623.5s
STUCK OPEN (>600s): 289 — e.g. [((1, 6, 1627), 2211.1), ((1, 1, 1628), 2206.2), ...]
```

The SLO is 572 / (572 + 289). Not one order was *lost* or *expired*: 289 were still open
more than 600 seconds after they were placed. The system did not drop orders; it fell
behind.

**`DONE`** is the machine-readable summary:

```json
{
  "net": "lasair6",
  "secs": 3600,
  "drain": 180,
  "started": "20260928T154026Z",
  "service": 100,
  "load": {
    "offered": 1440,
    "refused": 519,
    "busy": 0,
    "turned_away": 519,
    "pass": false
  },
  "poll": 0,
  "parity": 0,
  "verdict": 1,
  "pass": false,
  "finished": "20260928T164325Z"
}
```

`poll`, `parity` and `verdict` are the exit statuses of the soak's last three steps: the
verdict step returned 1, a FAIL verdict.

**`loadgen.txt`** is the load generator's counters at the end of the load:

```text
loadgen_op_errors_total{op="buy"} 265
loadgen_op_errors_total{op="sealed_sell"} 57
loadgen_op_errors_total{op="sell"} 197
loadgen_ops_total{op="buy"} 720
loadgen_ops_total{op="sealed_sell"} 158
loadgen_ops_total{op="sell"} 562
```

265 + 57 + 197 = 519 refused of 1,440 offered. (The log counts 523 refusals and so does
Prometheus: a few more calls failed after this snapshot, before the load generator
stopped.)

## The Soak runs dashboard

For soaks that push their results (every soak run with the current jamswap and the stack
up), the **Soak runs** dashboard shows the same verdict as tiles: one per check, green or
red, with the measured value and the threshold. Its table below lists every soak, newest
first, so a new run sits right above the last one. Its links open the other dashboards for
the same run and time range, which is where the diagnosis starts.

## Three rules for reading a verdict

1. **The verdict is the source of truth.** Live tiles can mislead: on this run the DEX
   dashboard's clearing-SLO tile read 1 the whole hour, because it counts only orders that
   ended (lesson 3.5).
2. **Chain checks and application checks fail for different reasons.** Sort the failures
   into the two groups before looking for a cause.
3. **A summary says what, not when.** Take the run id to the dashboards to see when it
   started and what moved first.

Next: [The symptom](../05-case-study/01-the-symptom.md)
