# The symptom

*A case in six parts. You follow a real investigation from 2026-09-28, in the order it
happened, with the queries that answered each question. On the machine that ran it you can
open every dashboard and run every query yourself; elsewhere, the lessons show you what
they returned.*

## The call

The `lasair6` network is six lasair validators on one genesis, with jamswap's DEX on top.
Earlier that day, a mixed network (three lasair, three PolkaJam) had passed a full
one-hour soak on lasair 2.1.2, and `lasair6` had passed 25 minutes but failed a full hour,
for a reason nobody could see yet. So the hour on `lasair6` was run again, this time with
every node reporting into the dashboards.

```sh
LASAIR_IMAGE=ghcr.io/abutlabs/lasair:2.1.2 LASAIR_DATA_DIR=/data soak/run lasair6 3600
```

The network came up at 15:39:15 UTC, so its run id is `lasair6-20260928T153915Z`. The load
started a minute later. An hour and three minutes after that, the verdict:

```text
VERDICT (orders + chain): FAIL
```

## What the report said

| Check | Result |
|---|---|
| offered load | **FAIL**: 1,440 offered, 519 refused |
| clearing SLO | **FAIL**: 0.6643 (572 cleared, 289 still open after 600 s) |
| SEALED zero-loss | **FAIL**: 33 sealed orders stuck |
| one head, liveness, finality, authoring, state parity | PASS, all five |
| clear latency | p50 160 s, p99 1,624 s |

And the reason for the refusals, from the load generator's log:

```text
op buy failed: HTTP Error 400: Bad Request: open-order limit reached (50 per market) — cancel or let some clear before placing more
```

## Sorting the evidence

First, the rule from lesson 4.3: sort the failures into chain and application.

- **Chain: all green.** One head on 630 of 630 samples, 659 finalized slots hash-checked, no
  conflict, every validator authoring, and byte-identical DEX state on all six nodes. The
  network agreed with itself the whole hour.
- **Application: all red.** And the three failures are one failure seen three ways. Orders
  stopped clearing, so each account's open orders piled up to the cap of 50, so new orders
  were refused. Nothing was *lost*: the 289 misses are orders still open, not expired or
  dropped. The sealed orders stuck for the same reason.

So the question is not "is the chain broken?" but **"why did orders stop clearing on a
healthy chain?"**

## What else we knew

Three earlier results from the same day, from jamswap's soak results:

| Run | Net | Duration | Result |
|---|---|---|---|
| `lasair-pj-20260928T102552Z` | 3 lasair + 3 PolkaJam, lasair 2.1.2 | 1 hour | PASS, 0 refused |
| `lasair6-20260928T133147Z` | 6 lasair, 2.1.2 | 1 hour | FAIL, 387 refused |
| `lasair6-20260928T143808Z` | 6 lasair, 2.1.2 | 25 minutes | PASS, 0 refused |

(These are the soaks' own folder names, which carry the time the load started.)

Two things follow. The same load passes when PolkaJam nodes share the work, so the problem
is something lasair does alone. And lasair alone passes 25 minutes but fails an hour, so it
is something that gets **worse with time**. Not a crash, not a wrong answer: a slowdown.

## Suspects

1. The DEX itself (its matching, its API).
2. The chain's consensus (heads, finality).
3. The validators' work on the DEX's rounds: guaranteeing, which includes refining each
   round's work-package.

Suspect 2 has an alibi: every chain check passed, on every sample. Suspect 1 is unlikely
since the same DEX passes on `lasair-pj`, but it is where the symptom showed, so it is
where to look first: *when* did it start, and what did the DEX see?

## Open the run on your machine

The run was recorded by jamswap's first monitoring setup, before this repository existed:
Prometheus and Grafana only. The stack you run now uses the same Prometheus volume
(`obs_prometheus`), so on the machine that ran it the metrics are in the Prometheus you
query, for 30 days after the run. Its container logs were never collected (there was no
Loki yet); lesson 5.4 deals with that.

Print its dashboard links (lesson 0.4 has what to do if `obs` does not know the run):

```sh
./obs link lasair6-20260928T153915Z -d dex
./obs link lasair6-20260928T153915Z -d obs-lasair
./obs link lasair6-20260928T153915Z -d chain
```

The first prints:

```text
http://localhost:3300/d/obs-dex?orgId=1&var-run_id=lasair6-20260928T153915Z&var-net=lasair6&from=1790609895389&to=1790613868842
```

In Grafana's Explore, set the time range to 2026-09-28 15:39 to 16:44 UTC for this run.
With the API, evaluate at `time=1790613808` (the run's end).

The run's annotations mark its story on every graph (times UTC):

| Time | Mark |
|---|---|
| 15:39:15 | blue: `lasair6 up: validators lasair,lasair,lasair,lasair,lasair,lasair; lasair ghcr.io/abutlabs/lasair:2.1.2` |
| 15:40:28 | blue: `soak: load on` |
| 16:40:39 | blue: `soak: load off, draining 180 s (offered 1440, refused 519, busy 0)` |
| 16:43:25 to 16:43:28 | red and green: one mark per check, red for `clearing SLO`, `SEALED zero-loss`, `orders`, `offered load` and the verdict, green for the chain checks |
| 16:43:28 | blue: `lasair6 down` |

Next: [The DEX backlog](02-the-dex-backlog.md)
