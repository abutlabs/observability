# Lab 4: Replay the case study

**Needs:** the machine that recorded the two runs of track 5, within 30 days of
2026-09-28, with Prometheus answering on port 9390. **Time:** 30 minutes.

You answer the case study's questions yourself, with PromQL, against the recorded data.
Every answer below is what the query returned on 2026-09-28.

| Run | Net came up (UTC) | Evaluate "at the end" with |
|---|---|---|
| failing: `lasair6-20260928T153915Z` | 15:39:15 | `time=1790613808` |
| passing: `lasair6-20260928T173608Z` | 17:36:08 | `time=1790620903` |

## Two ways to run a query

**Grafana Explore**: open the DEX dashboard link from lesson 5.1, then *Explore* from any
panel's menu: the time range comes along. Paste a query, switch between table and graph.

**The API**, with a small shell helper (paste it into your terminal):

```sh
q() { curl -s -G http://localhost:9390/api/v1/query \
        --data-urlencode "query=$1" --data-urlencode "time=$2" \
      | python3 -c 'import json,sys; [print(r["metric"], r["value"][1]) for r in json.load(sys.stdin)["data"]["result"]]'; }
FAIL=lasair6-20260928T153915Z; FAIL_END=1790613808
PASS=lasair6-20260928T173608Z; PASS_END=1790620903
```

Try it:

```sh
q "sum(max_over_time(lasair_ce133_guaranteed_total{run_id=\"$FAIL\"}[70m]))" $FAIL_END
```

```text
{} 140
```

## The questions

### 1. How many orders did the load generator offer, and how many were refused?

Use `loadgen_ops_total` and `loadgen_op_errors_total` on the failing run.

<details>
<summary>Answer</summary>

```sh
q "sum(max_over_time(loadgen_ops_total{run_id=\"$FAIL\"}[70m]))" $FAIL_END          # 1444
q "sum(max_over_time(loadgen_op_errors_total{run_id=\"$FAIL\"}[70m]))" $FAIL_END    # 523
```

1,444 offered and 523 refused in Prometheus. The soak's own counts are 1,440 and 519: it
read the load generator's counters when the load stopped, and the generator made a few
more calls before it was stopped. Two instruments, two moments; both are right.

</details>

### 2. When did the refusals start?

Graph `sum(loadgen_op_errors_total{run_id="lasair6-20260928T153915Z"})` over the run. At
what time does it leave zero?

<details>
<summary>Answer</summary>

About 31 minutes after the network came up, around 16:10 UTC. The series does not exist
before the first refusal; at minute 31 it reads 10, at 32 it reads 24, at 33 it reads 43.

</details>

### 3. When did market-1 rounds reach the 48-order cap?

<details>
<summary>Answer</summary>

```promql
sum by (market) (rate(jamswap_round_orders_sum{run_id="lasair6-20260928T153915Z"}[5m]))
  / sum by (market) (rate(jamswap_round_orders_count{run_id="lasair6-20260928T153915Z"}[5m]))
```

It reaches 48 at minute 21 (about 16:00 UTC) and stays there to the end: ten minutes
before the refusals began.

</details>

### 4. How slow did refine get, and how fast is it after the fix?

Find the worst five-minute average refine time per package, on any node, for each run.

<details>
<summary>Answer</summary>

```sh
q "max_over_time((max(rate(lasair_guarantor_refine_seconds_sum{run_id=\"$FAIL\"}[5m]) / rate(lasair_guarantor_refine_seconds_count{run_id=\"$FAIL\"}[5m])))[70m:15s])" $FAIL_END
q "max_over_time((max(rate(lasair_guarantor_refine_seconds_sum{run_id=\"$PASS\"}[5m]) / rate(lasair_guarantor_refine_seconds_count{run_id=\"$PASS\"}[5m])))[70m:15s])" $PASS_END
```

13.2 seconds on the failing run, 0.60 seconds on the passing run.

</details>

### 5. And the average over the whole run?

<details>
<summary>Answer</summary>

```sh
q "sum(max_over_time(lasair_guarantor_refine_seconds_sum{run_id=\"$FAIL\"}[70m])) / sum(max_over_time(lasair_guarantor_refine_seconds_count{run_id=\"$FAIL\"}[70m]))" $FAIL_END
q "sum(max_over_time(lasair_guarantor_refine_seconds_sum{run_id=\"$PASS\"}[70m])) / sum(max_over_time(lasair_guarantor_refine_seconds_count{run_id=\"$PASS\"}[70m]))" $PASS_END
```

2.28 seconds and 0.186 seconds. The failing run's average is low next to its peak because
the first 20 minutes, with small rounds, refined in about 2 seconds.

</details>

### 6. Which node lost the most packages to expiry?

<details>
<summary>Answer</summary>

```sh
q "max by (node) (max_over_time(lasair_ce133_expired_total{run_id=\"$FAIL\"}[70m]))" $FAIL_END
```

`lm0` with 10 of 17 (then `lm1` 4, `lm2` 2, `lm3` 1). On the passing run, `lm0` again: 12
of 13.

</details>

### 7. How old were packages when they arrived?

Answer for the passing run. Then try the failing run and explain the result.

<details>
<summary>Answer</summary>

```sh
q "histogram_quantile(0.5, sum by (le) (increase(lasair_ce133_anchor_age_slots_bucket{run_id=\"$PASS\"}[65m])))" $PASS_END   # 2.5
q "histogram_quantile(0.9, sum by (le) (increase(lasair_ce133_anchor_age_slots_bucket{run_id=\"$PASS\"}[65m])))" $PASS_END   # 2.9
```

Median 2.5 slots, 90th percentile 2.9 slots. For the failing run the query returns nothing:
its nodes ran lasair 2.1.2, which did not export `lasair_ce133_anchor_age_slots`. The
metric was added during the investigation.

</details>

### 8. What did the DEX's own clearing-SLO gauge say on the failing run?

<details>
<summary>Answer</summary>

```sh
q "min_over_time(min(jamswap_order_clearing_slo{run_id=\"$FAIL\"})[70m:1m])" $FAIL_END
```

1. The gauge counts only orders that ended; the 289 orders stuck open never ended, so it
never saw them as missed. The soak's verdict (0.6643) did.

</details>

### 9. How fast did slots advance?

<details>
<summary>Answer</summary>

```sh
q "deriv(max(jam_best_slot{run_id=\"$FAIL\"})[10m:])" 1790612000
```

About 0.29 slots per second at that moment, and between 0.26 and 0.30 across the run: one
slot every 3.3 to 3.8 seconds. Lesson 5.5 explains why this matters.

</details>

## Going further

Pick any panel of the DEX or lasair validator duties dashboard, open *Explore* from its
menu, and change its query: per node instead of summed, a different window, the other run.
Every panel is just a query, and now you can write them.
