# Running a soak

A soak is one command, run from your **jamswap** checkout. It needs Docker with a few GB of
disk free; the first run pulls the images.

```sh
soak/run lasair-pj 600        # 10 minutes of load
soak/run lasair6 3600         # the hour-long check
```

`soak/run NET [SECS]` takes the network (`lasair6`, `lasair-pj`, `pj6`; `./dex nets` lists
every one) and the seconds of load (default 600).

## What it does, in order

1. **Makes sure the stack is up.** If `./dex obs ping` fails it runs `./dex obs up`
   (`./dex obs ...` passes any command to the observability checkout's `obs`, which it
   expects next to jamswap or at `OBS_HOME`). Without the stack the soak still runs,
   without dashboards.
2. **Brings the network up**: `./dex up NET=<net>`. This begins a run in the stack, gives
   every container the run id through its labels, annotates the start and prints the
   dashboard links.
3. **Runs the soak**: `./dex soak NET=<net> <secs>`. It records the configuration, pushes
   the configuration and its progress to the Pushgateway (the Soak runs dashboard), runs
   the load, drains for 180 seconds, checks state parity, computes the verdict, and pushes
   the verdict.
4. **Tears the network down**: `./dex down NET=<net>`, which annotates the end and ends the
   run.
5. **Writes the report**: `REPORT.md` next to the soak's own files, and prints its path and
   the Soak runs dashboard link.

The exit status is the soak's: 0 on PASS.

A one-hour soak takes a little over an hour: 3,600 seconds of load, 180 of drain, plus
bringing the network up and down.

## Settings

Pass them as environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `LASAIR_IMAGE` | the pin in `nets/profiles.py` | which lasair build runs |
| `LASAIR_DATA_DIR` | unset (memory only) | `/data`: durable storage, as a real node runs |
| `RATE` / `PROFILE` / `SEALED_RATIO` | 12 / trading / 0.2 | the load |
| `KEEP_UP` | 0 | 1 leaves the network running afterwards, to look around |

For example, the exact commands behind the soak results of 2026-09-28:

```sh
LASAIR_IMAGE=ghcr.io/abutlabs/lasair:2.1.2 LASAIR_DATA_DIR=/data soak/run lasair-pj 3600   # passes
LASAIR_IMAGE=ghcr.io/abutlabs/lasair:2.1.2 LASAIR_DATA_DIR=/data soak/run lasair6 3600     # fails at ~25 min
LASAIR_IMAGE=ghcr.io/abutlabs/lasair:2.1.2 LASAIR_DATA_DIR=/data soak/run lasair6 1500     # passes
```

To compare two lasair builds, run the same net and duration twice with a different
`LASAIR_IMAGE`, then put the two rows side by side in the Soak runs table.

## The step-by-step equivalent

`soak/run` is a convenience around three `./dex` verbs. Run them yourself to keep control:

```sh
./dex up NET=lasair6
./dex soak NET=lasair6 3600
./dex down NET=lasair6
```

## Watching it live

`./dex up` prints the dashboard links for the run. While the soak runs:

- **Soak runs**: its phase (`load`, `drain`, `parity`, `verdict`, `done`) and seconds of
  load left.
- **DEX**: offered against turned-away load, round sizes, the mempool.
- **Chain health**: one head, finality lag.
- **lasair validator duties**: refine time, guarantees, expiries.

A problem that builds up slowly is visible on the DEX dashboard long before the verdict:
a *Turned away* line that leaves zero, or a *Round sizes* line that pins at the cap.

## Where the files go

Every run gets a folder, `~/.cache/jamswap/soak/<net>-<UTC start>/`:

| File | What |
|---|---|
| `REPORT.md` | the report (next lesson) |
| `verdict.txt` | the verdict, one line per check |
| `DONE` | a JSON summary: load offered and refused, each step's exit status, pass |
| `config.json` | the configuration as it ran |
| `loadgen.log`, `dex.log` | the load generator's and the DEX's own logs |
| `loadgen.txt` | the load generator's metrics at the end of the load |
| `chain.jsonl`, `parity.json` | the chain samples and the state-parity evidence |

The folder's time is the soak's start, a minute or two after the run id's: the run begins
when the network comes up. The case study's failing soak is in
`lasair6-20260928T154026Z/`, while its run id is `lasair6-20260928T153915Z`.

Next: [Reading the results](03-reading-the-results.md)
