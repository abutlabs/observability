# The fix and the proof

## The fix: a faster PVM

The target was clear: a full 48-order round, 253M gas, had to refine in far less than the
14 seconds it took. A profile of one such refine showed where lasair's PVM spent its time:
about half in per-byte memory-page lookups through a generic hash table, much of the rest
in decoding each instruction again at every step and building fresh records per step.

The change (in lasair's PVM, not in the DEX and not in the protocol):

- programs are decoded **once**, with each instruction's next address and flags, and
  reused;
- one interpreter loop runs with registers held unboxed and a small cache of the memory
  pages last read and written;
- the interpreter runs in batches of steps instead of one call per step;
- the gas model's simulation became linear per block instead of rescanning.

The result on the benchmark: a 48-order round refines in **0.53 seconds instead of 9.42**
when the program is already loaded (about **18 times faster**, the figure the change is
known by), and in **0.58 seconds instead of 14.0** from cold (about 24 times).

**And byte-exact.** A faster PVM that computes a different result is worse than a slow one:
the network would split. So the change was checked against the old interpreter: identical
gas, steps and outputs, and per-instruction traces of a real DEX refine that match line for
line. On a network, the soak's *state parity* check is the final word: every node must hold
byte-identical DEX state at one finalized block.

## The proof: the same soak again

A fix is proven by the measurement that showed the failure, repeated with one thing
changed. Same network (`lasair6`), same load, same hour; the lasair build is the new one:

```sh
LASAIR_IMAGE=<a lasair build with the new PVM> soak/run lasair6 3600
```

The network came up at 17:36:08 UTC: run `lasair6-20260928T173608Z`. Its first annotation
records exactly what ran: `lasair6 up: validators lasair,lasair,lasair,lasair,lasair,lasair;
lasair lasair:perf-8c62dd5`, a local build of the new PVM. (Annotations like this one are
how you find out, weeks later, what a run was.) One setting was not recorded: whether the
nodes used durable storage (`LASAIR_DATA_DIR=/data`) as the failing run did. Strictly, then,
the two runs are known to differ in one thing and may differ in a second.

```text
VERDICT (orders + chain): PASS
```

## Side by side

The same queries, on both runs (evaluate each at its run's end: `time=1790613808` for the
failing run, `time=1790620903` for the passing one).

| Measure | Failing `…T153915Z` | Passing `…T173608Z` |
|---|---|---|
| Clearing SLO (verdict) | 0.6643 | **1.000000** |
| Orders refused (verdict) | 519 of 1,440 | **0** of 1,440 |
| Sealed orders stuck | 33 | **0** |
| Clear latency p50 / p99 | 160 s / 1,624 s | 44 s / 473 s |
| Refine, average per package, whole run | 2.28 s | **0.186 s** |
| Refine, worst five-minute average on any node | 13.2 s | **0.60 s** |
| Work-items guaranteed | 140 | **267** (266 during the load) |
| Packages expired | 17 | 13 (12 during the load) |
| Market-1 mempool, peak | 344 orders | 183 orders |
| One head, finality, authoring, state parity | PASS | PASS |

The queries behind the lasair and DEX rows (replace the run id):

```promql
# refine, average per package, whole run
sum(max_over_time(lasair_guarantor_refine_seconds_sum{run_id="<run>"}[70m]))
  / sum(max_over_time(lasair_guarantor_refine_seconds_count{run_id="<run>"}[70m]))

# refine, worst five-minute average on any node
max_over_time((max(rate(lasair_guarantor_refine_seconds_sum{run_id="<run>"}[5m])
  / rate(lasair_guarantor_refine_seconds_count{run_id="<run>"}[5m])))[70m:15s])

# work-items guaranteed, packages expired
sum(max_over_time(lasair_ce133_guaranteed_total{run_id="<run>"}[70m]))
sum(max_over_time(lasair_ce133_expired_total{run_id="<run>"}[70m]))

# market-1 mempool peak
max_over_time(jamswap_mempool_orders{run_id="<run>", market="1"}[70m])
```

On the **lasair validator duties** dashboard the difference shows at a glance: the
passing run's *Refine seconds* line stays under 0.6 seconds for the whole hour (mind the
axis: Grafana scales it to the data, so read the numbers, not the height), and *CE-133
guaranteed* holds at about 2.6 to 5.7 work-items a minute throughout the load, where the
failing run fell to about one.

The refine average, 0.186 s, is lower than the benchmark's 0.53 to 0.58 s per full round
mostly because most rounds were not full: in most five-minute windows of the passing run,
market-1 rounds averaged between 8 and 25 orders. With packages refined in a fraction of a
second the backlog stays bounded, so rounds stay small, which keeps refines cheap. The loop
that fed itself in the failing run now runs the other way.

## What is left

Thirteen packages still expired (12 during the load, most on one node, `lm0`). The
investigation's look at them: each refined in at most 1.7 seconds, but they arrived about 3
slots old and waited for the busy core 0. That is the other two levers of lesson 5.5:
anchor freshness and core contention.

This time the evidence is on the dashboard, because the passing build exports the
anchor-age metric added during the investigation:

```promql
histogram_quantile(0.5, sum by (le) (increase(lasair_ce133_anchor_age_slots_bucket{run_id="lasair6-20260928T173608Z"}[65m])))
```

returns 2.5 slots, and the same with 0.9 returns 2.9. Packages typically arrived with their
anchor between 2 and 3 slots old: 2 to 3 of the window's 8 blocks already gone before the
guarantor saw them. The investigation noted the next lead with them: lasair's JIP-2
reader answers `bestBlock` with the head's parent, so a builder that anchors at *that*
block's parent starts two blocks behind.

The soak passes, so the question is no longer urgent, but it is measurable, and a fix to it
will be proven the same way.

## The method, in one list

1. **Symptom**: the verdict. Sort failures into chain and application.
2. **When**: the service's dashboard (DEX): when did it start, what moved first?
3. **Where**: the client's dashboard (lasair validator duties): which part of the work got
   slower?
4. **Why this one**: the logs, for one individual case, end to end.
5. **The rule**: what constraint turns slowness into failure (the anchor window), and the
   budget it sets.
6. **The fix**: the biggest term inside your control.
7. **The proof**: the same run with one change, compared on the same queries, with the
   safety checks (state parity, one head) still green.

Every step used the stack: labels to find the right series, runs to keep the two hours
apart, dashboards to see the shape, PromQL to get the numbers, logs for the single case.

Next: [lasair and PolkaJam on one chain](../06-mixed-networks/01-lasair-and-polkajam.md)
