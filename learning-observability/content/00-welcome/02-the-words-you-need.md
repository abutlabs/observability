# The words you need

Observability has its own vocabulary. This lesson explains the words the rest of the course
uses, each with a picture or an example. Come back to it whenever a word stops making
sense.

## Metric, label, series, sample

A **metric** is a named measurement that a program keeps up to date. A JAM node keeps one
called `jam_best_slot`: the slot number of the block it currently considers the head of
the chain.

Six nodes each have their own `jam_best_slot`. To tell them apart, every value carries
**labels**: `name="value"` pairs that say *which one* it is. Three of the six, at 18:41:30
UTC on 2026-09-28 (real values from the run you will meet in the next lesson):

```text
 metric name     labels: which one exactly                          value
 ─────────────   ─────────────────────────────────────────────────  ─────
 jam_best_slot  {net="lasair6", node="lm0", client="lasair"}         1044
 jam_best_slot  {net="lasair6", node="lm1", client="lasair"}         1044
 jam_best_slot  {net="lasair6", node="lm2", client="lasair"}         1044
```

One metric name with one exact set of labels is a **series** (short for *time series*).
Above are three series. On a graph, **one series is one line**.

Each time the value is recorded, that is a **sample**: one number at one moment. A series
is a list of samples:

```text
 jam_best_slot{node="lm0"}:   18:41:20 → 1040   18:41:25 → 1043   18:41:30 → 1044   ...
```

The labels you will see everywhere in this course are `net` (which network), `run_id`
(which run of it), `node` (which node) and `client` (which JAM implementation).

## Three kinds of metric

| Kind | Behaves like | Example | How you read it |
|---|---|---|---|
| **Gauge** | a speedometer: goes up and down | `jam_best_slot`, `jam_peers` | as is: "the node is at slot 1044" |
| **Counter** | an odometer: only goes up, from 0 when the program starts | `jam_blocks_imported_total` | by how fast it grows: "blocks imported per minute" |
| **Histogram** | a tally of how long things took, in buckets | `jam_block_import_seconds` | as percentiles: p50 (the median) is the time half the imports stayed under; p99, the time 99 in 100 stayed under |

Counters end in `_total`, and durations are in seconds and end in `_seconds`.

## Scrape

Programs do not send their metrics anywhere by default. They show them on a web page,
usually `/metrics`, and a collector comes and reads that page every few seconds. Reading
it is called a **scrape**:

```text
  every 5 seconds
  Alloy ──── GET http://lm0:9615/metrics ───▶ node lm0
  Alloy ◀─── jam_best_slot 1044
             jam_peers{role="validator"} 5
             ...
  Alloy ──── adds net, run_id, node, client ───▶ Prometheus stores one sample per series
```

Alloy is the stack's collector; Prometheus is the database that stores the samples.

## Time range

Every dashboard shows a window of time: the **time range**, picked at the top right. It can
be relative ("Last 1 hour", moving with the clock) or fixed (from 17:35 to 18:42). Nearly
every "why is this empty?" moment is a time range that does not cover what you want to see.

```text
            time range: 17:35 ────────────────────────────── 18:42
 the run:          17:36 ████████████████████████████████████ 18:41
 a graph:          ▁▂▃▄▅▅▆▆▇▇█  (one line per series, across the range)
```

## Instant and range queries

You ask Prometheus questions in its language, **PromQL**. A question can be asked two ways:

- An **instant query** asks for the value *at one moment*. The answer is one number per
  series. Big-number panels and tables use instant answers.
- A **range query** asks for the values *across the time range*. The answer is a line per
  series. Graphs use range queries.

```text
 instant query, at 18:26:       range query, 17:36 to 18:41:
   lm0   775                      lm0   ▁▂▃▄▅▆▇█
   lm1   775                      lm1   ▁▂▃▄▅▆▇█
```

## Log line and log stream

A **log line** is one line of text a program writes about something that happened:

```text
[ce133] package 0x3f1c9a02.. EXPIRED (anchor_too_old): its context can no longer be reported; the builder must resubmit
```

Metrics count things; log lines tell you about *one* thing, with details like a package's
hash.

A **log stream** is every line from one source, labelled like a series:
`{net="lasair6", node="lm0", source="docker"}` is everything node `lm0` printed. Loki, the
stack's log database, stores streams, and you ask it questions in **LogQL**.

## Run

The same network is started many times. Each start is a **run**, named by a **run id**:
the network's name and the UTC time the run began.

```text
 lasair6-20260928T173608Z   =   network lasair6, started 2026-09-28 at 17:36:08 UTC
```

Every series and every log line carries its run id, so two runs of the same network never
mix, and you can put them side by side.

## Dashboard, panel, annotation

A **dashboard** is a page of **panels**. The panels you will meet:

```text
 ┌──────────────┐  ┌──────────────────────────────┐  ┌────────────────────┐
 │  stat        │  │  time series                 │  │ table              │
 │              │  │        ╱╲    ╱               │  │ node  client  slot │
 │     0        │  │   ╱╲  ╱  ╲  ╱                │  │ lm0   lasair  1044 │
 │  (green)     │  │  ╱  ╲╱    ╲╱                 │  │ lm1   lasair  1044 │
 └──────────────┘  └──────────────────────────────┘  └────────────────────┘
  one big number,    lines over the time range         values per node, now
  green or red if
  it has a threshold
```

and **logs** panels, which list log lines.

An **annotation** is a vertical mark across every graph at the moment something happened,
with a note: "load on", "drain", "clearing SLO 0.66". Blue marks events, green a PASS,
red a FAIL.

```text
            load on                        FAIL: clearing SLO 0.66
               │                                    │
   ▁▁▁▂▂▃▃▄▅▆▇▇│▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇│
```

## A few JAM and test words the course leans on

| Word | In one line |
|---|---|
| slot | a numbered time step of the chain; one block at most per slot |
| best block, head | the block a node currently builds on; its slot is the *best slot* |
| finalized | agreed for good by the validators (by GRANDPA); cannot be undone |
| work-package | a unit of work for a service, sent to validators by a *builder*; they *refine* it (run the service's code on it) |
| guarantor | a validator that refines a work-package and signs a *guarantee* for it |
| anchor | a recent block a work-package names; it must stay among the last 8 blocks until the package is reported |
| CE-133, CE-134, CE-135 | numbered network protocols: submitting a package, sharing it with co-guarantors, distributing the guarantee |
| soak (test) | running a real network under steady load for a long time, then judging it (track 4) |
| DEX | jamswap's decentralised exchange, the application the soaks run; it batches orders into *rounds*, one work-package each |
| clearing SLO | an SLO is a *service-level objective*, a target a service promises; the DEX's is the share of orders that could trade and did, with a target of 0.9999 |

Next: [Reading your first dashboard](03-reading-your-first-dashboard.md)
