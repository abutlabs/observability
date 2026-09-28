# Running the stack

All of this runs from the top folder of your observability checkout.

## Start it: `./obs up`

```sh
./obs up
```

`up` does four things:

1. It looks for dashboard folders: every sibling checkout with an
   `observability/dashboards/` directory, plus any `<NAME>_DIR` or `OBS_PROJECT_DIRS` you
   set.
2. It writes two generated files into its state directory (`~/.cache/abutlabs-obs`): the
   Grafana folder list, and a compose *override* (an extra compose file) that adds each
   project's dashboards folder to Grafana.
3. It runs `docker compose -p obs up -d --remove-orphans` with `compose.yml` and that
   override. The first time, Docker pulls the images.
4. It waits (up to 180 seconds) until Prometheus, Grafana, Alloy, Loki and the Pushgateway
   all answer their readiness checks (a URL each service answers only once it is ready to
   work), then prints where everything is.

The printout has this shape (the last line depends on your checkouts):

```text
grafana      http://localhost:3300
prometheus   http://localhost:9390
loki         http://localhost:3100
pushgateway  http://localhost:9091
alloy        http://localhost:12345
jip3         localhost:9910  (--telemetry; containers on an observed net: obs-jip3:9910)
dashboards   platform, jamswap (/…/jamswap/observability/dashboards), lasair (/…/lasair/observability/dashboards)
```

Open http://localhost:3300. You land on the **Network overview**. It is empty until a
network reports in.

If a service does not come up in time, `up` says which and how to see why:
`started, but loki do not answer: docker compose -p obs logs loki`.

If the `dashboards` line shows only `platform`, the lasair and jamswap folders were not
found: they are not next to this checkout, or their checkouts have no
`observability/dashboards/` yet. Point at them and run `up` again:

```sh
LASAIR_DIR=~/src/lasair JAMSWAP_DIR=~/src/jamswap ./obs up
```

`up` is safe to repeat: it updates the stack in place and keeps the data.

## Check it: `./obs status`

```sh
./obs status
```

This is real output, captured on 2026-09-28 from a second stack running beside the first
(so its ports differ from the defaults; see *A second stack* below):

```text
grafana      http://localhost:3400        up
prometheus   http://localhost:9490        up
loki         http://localhost:3110        up
pushgateway  http://localhost:9491        up
alloy        http://localhost:12345       up
lasair-pj      lasair-pj-20260928T184439Z       jip3 nodes 3
netjoin      alloy on 0 network(s), jip2-exporter on 0 network(s), jip3-receiver on 0 network(s)
```

How to read it:

- **One line per service**, with its URL and `up` or `DOWN` (from its readiness endpoint).
- **One line per run that Prometheus holds data for**: the net, the run id, then what
  reports. A scraped job shows as `<job> <up>/<total> up` (for example `lasair 6/6 up`);
  a path that describes nodes for others shows as `jip3 nodes 3` or `jip2 nodes 6`.
- **netjoin**: how many networks each collector is attached to right now.

This capture was taken just after the `lasair-pj` network had stopped. That is why
netjoin is on 0 networks and no scraped job is listed, yet `jip3 nodes 3` remains: the
JIP-3 receiver keeps a node's series for 6 hours after it disconnects
(`JIP3_FORGET_SECS`), so the three PolkaJam nodes that pushed telemetry are still
described. `status` reports what Prometheus holds now, which trails a network that just
stopped.

`./obs status -v` adds one line per target: net, job, node, client, up or DOWN, and the
address Alloy scrapes. Use it when a node is missing.

With no network reporting, the run lines are replaced by `no run is reporting`.

## Other small commands

```sh
./obs ping          # exit status 0 if Prometheus, Grafana and Alloy answer, 1 if not
./obs url grafana   # one service's URL: grafana prometheus loki pushgateway alloy jip3
```

With the default settings:

```text
$ for n in grafana prometheus loki pushgateway alloy jip3; do ./obs url $n; done
http://localhost:3300
http://localhost:9390
http://localhost:3100
http://localhost:9091
http://localhost:12345
localhost:9910
```

(`jip3` is `host:port` with no scheme, because that is what a node's `--telemetry` option
takes.) Scripts use `ping` to check for the stack: jamswap's `soak/run` starts the stack
only when `ping` fails.

## Stop it: `./obs down`

```sh
./obs down          # stop the stack; every volume (the data) is kept
./obs down --wipe   # stop it and delete the data volumes
```

Your runs, dashboards' annotations and soak results survive a plain `down`.

## Settings

Everything is an environment variable, read by `./obs`:

| Variable | Default | What |
|---|---|---|
| `OBS_GRAFANA_PORT` | 3300 | Grafana |
| `OBS_PROMETHEUS_PORT` | 9390 | Prometheus |
| `OBS_LOKI_PORT` | 3100 | Loki |
| `OBS_PUSHGATEWAY_PORT` | 9091 | Pushgateway |
| `OBS_ALLOY_PORT` | 12345 | Alloy's UI |
| `OBS_JIP3_PORT` | 9910 | JIP-3 ingest |
| `OBS_BIND` | 127.0.0.1 | the address every port binds to, except JIP-3's |
| `OBS_JIP3_BIND` | 127.0.0.1 | the address the JIP-3 port binds to (a node elsewhere needs it opened) |
| `OBS_GRAFANA_PASSWORD` | obs | admin password; set before the first `up` |
| `OBS_STATE` | `~/.cache/abutlabs-obs` | targets, run records, generated files |
| `OBS_PROJECT` | obs | the compose project name |
| `OBS_RETENTION_TIME` / `OBS_RETENTION_SIZE` | 30d / 5GB | Prometheus retention |
| `OBS_LOG_RETENTION` | 720h | Loki retention |

## Disk

Prometheus stops at 5 GB. Loki has no size cap: a validator that logs a few lines per slot
writes about 25 KB a minute, so a six-node network running for a whole month is about 7 GB
of text, one to two GB on disk after Loki's compression. Shorten `OBS_LOG_RETENTION` if
your disk is small. The stack's own container logs rotate at 3 × 10 MB.

## A second stack

Every name derives from `OBS_PROJECT`, so you can run a second stack beside the first (to
try a change while the first keeps recording) with its own project, ports and state:

```sh
OBS_PROJECT=obs2 OBS_STATE=~/.cache/abutlabs-obs2 OBS_GRAFANA_PORT=3400 \
OBS_PROMETHEUS_PORT=9490 OBS_LOKI_PORT=3110 OBS_PUSHGATEWAY_PORT=9491 \
OBS_ALLOY_PORT=12346 OBS_JIP3_PORT=9920 ./obs up
```

Every later `./obs` command for that stack needs the same variables. The status output
above came from such a stack (one that moved Grafana, Prometheus, Loki and the Pushgateway
but kept Alloy and JIP-3 on their default ports, which only works while no first stack
uses them).

Next: [Runs, annotations and links](04-runs-annotations-links.md)
