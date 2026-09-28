# How to use this course

## The path

| Track | You learn | Lessons |
|---|---|---|
| 0 Welcome | Why observe, the words you need, reading your first dashboard, how the course works | 4 |
| 1 The stack | Each service, how they connect, running it, runs and links | 4 |
| 2 Telemetry from JAM nodes | The `jam_*` metrics, the three ingestion paths, JIP-3 and JIP-2, lasair's telemetry, plugging in a client | 5 |
| 3 Reading the dashboards | Every dashboard and the question it answers; the drill-down path when something looks wrong; PromQL and LogQL | 8 |
| 4 Soak tests | What a soak proves, running one, reading its results | 3 |
| 5 Case study | A real failing soak, from symptom to cause to proof of the fix | 6 |
| 6 Mixed networks | lasair and PolkaJam on one chain; what cross-client telemetry reveals; adding a client | 3 |
| Labs | Hands-on labs, and PromQL and LogQL exercises with answers | 7 |

Read the tracks in order the first time. Each lesson takes 5 to 10 minutes. Lessons are
numbered track.lesson: lesson 3.6 is the sixth lesson of track 3. Tracks 1 to 3 are the
reference you will come back to; track 5 is where it comes together. When a word stops
making sense, go back to [The words you need](02-the-words-you-need.md).

## Where to read it

- **On GitHub:** the lessons are markdown files in
  [`learning-observability/content/`](../) and the labs in
  [`learning-observability/exercises/`](../../exercises/README.md). Links between lessons
  work there.
- **On the course site:** https://abutlabs.github.io/observability/ renders the same files
  with a sidebar and progress marks.
- **Locally:** build and serve the site as the [course README](../../README.md) shows.

## What you need

- Docker with Compose v2, and Python 3.8 or newer. The stack's CLI, `obs`, is a single
  standard-library Python file.
- A checkout of [abutlabs/observability](https://github.com/abutlabs/observability).
  Commands in this course run from its top folder unless a lesson says otherwise.
- For the lasair and jamswap parts: checkouts of lasair and jamswap **next to it** (in the
  same parent folder). `./obs up` mounts each sibling checkout's
  `observability/dashboards/` folder as a Grafana folder named after it. If yours live
  elsewhere, point at them with `LASAIR_DIR=/path/to/lasair` and
  `JAMSWAP_DIR=/path/to/jamswap`.

Several lessons need no running network at all. The labs say what each one needs.

## Conventions

- `./obs` is the CLI in the observability checkout. `./obs -h` lists every command.
- Ports are the defaults: Grafana `3300`, Prometheus `9390`, Loki `3100`, Pushgateway
  `9091`, Alloy `12345`, JIP-3 `9910`. Every one of them can be moved with an environment
  variable (lesson 1.3).
- `<net>` and `<run_id>` are placeholders. A run id looks like
  `lasair6-20260928T153915Z`: the network's name and the UTC time its run began.
- Output shown in a lesson was captured on 2026-09-28 unless it says otherwise. Where a
  lesson shows the *shape* of an output instead (because producing it needs a running
  network), it says so.

## The recorded runs

Track 5 follows two real one-hour soak runs of the `lasair6` network (six lasair
validators running a DEX):

| Run id | Result |
|---|---|
| `lasair6-20260928T153915Z` | FAIL: clearing SLO 0.66, 519 of 1,440 orders refused, 33 sealed orders stuck |
| `lasair6-20260928T173608Z` | PASS: clearing SLO 1.000, 0 refused |

Their metrics live in the Prometheus of the machine that ran them, which keeps 30 days of
data (and at most 5 GB). On that machine you can open every dashboard and run every query
of track 5 yourself. Anywhere else, the lessons give you the numbers and the queries, so
you can follow the reasoning and repeat it on your own runs.

These two runs were recorded by jamswap's first monitoring setup (Prometheus and Grafana
only), before this repository existed. This stack uses the same compose project name
(`obs`) and the same Prometheus and Grafana volumes, so when it replaced the first setup it
took over their data and annotations. The first setup kept its run records in
`~/.cache/jamswap/obs`. If `./obs link` or `./obs runs` does not know these two runs (the
link then covers the last six hours instead of the run), put
`OBS_STATE=~/.cache/jamswap/obs` in front of the command, or copy the two files from
`~/.cache/jamswap/obs/runs/` into `~/.cache/abutlabs-obs/runs/`.

## Two naming generations

lasair adopted the common `jam_*` names after these runs. Where it had its own name for a
standard metric, a new build still exports the old name too (an alias), but the recorded
runs have only the old names, and lasair-specific metrics keep their `lasair_` prefix
either way. Where it matters, a lesson names both: for example `jam_wp_anchor_age_slots`,
which older builds export as `lasair_ce133_anchor_age_slots`.

## Checking yourself

Each track has labs or exercises. The PromQL and LogQL exercises have their answers in
collapsed blocks: try first, then open them.

Next: [The eight services](../01-the-stack/01-components.md)
