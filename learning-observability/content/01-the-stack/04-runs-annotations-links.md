# Runs, annotations and links

## A run is one life of a network

You will start the same network many times: to test a fix, to repeat a failure, to compare
two builds. If every start wrote into the same series, the runs would blur into each
other. So each start gets a **run id**, and every series, log line and annotation from that
start carries it:

```text
lasair6-20260928T153915Z
└─net─┘ └── UTC start ──┘
```

Dashboards pick a net and then a run. Queries select `run_id="..."`. Two runs of the same
network sit side by side and never mix.

## The run commands

```sh
./obs new-run NET          # print a fresh run id for NET (records nothing)
./obs begin NET            # record the start of NET's run, print its id
./obs end NET              # record its end, drop NET's file targets
./obs current NET          # NET's current run id (exit status 1 if none)
./obs runs [--net NET]     # the recorded runs, newest first (--json for scripts)
```

`begin` also ends the net's previous run, if one is open. A Docker network passes the id
`begin` printed to its containers through the `org.abutlabs.obs.run_id` label:

```sh
export OBS_RUN_ID=$(./obs begin mynet)      # prints mynet-<UTC time>
docker compose up -d                        # labels read ${OBS_RUN_ID}
./obs end mynet                             # before docker compose down
```

Keep the same `OBS_RUN_ID` while the network runs: a changed label makes Compose recreate
the container. A container with no run id label gets `<net>-adhoc`.

Run records (net, start, end) are small JSON files in `~/.cache/abutlabs-obs/runs/`.
They are what lets a link open a finished run with its exact time range.

## Processes on your machine: `register`

A process outside Docker is registered with its net, run id, job and targets. Here is a
real session, run against a scratch state directory so it touched nothing else
(`OBS_STATE` pointed there):

```text
$ ./obs new-run localnet
localnet-20260928T191647Z
$ ./obs register localnet localnet-20260928T120000Z myclient \
      v0@host.docker.internal:9615 v1@host.docker.internal:9616 -v
obs: localnet localnet-20260928T120000Z: myclient v0 v1 (scrape)
$ ./obs register localnet localnet-20260928T120000Z myclient v0@127.0.0.1:40000 --jip3 -v
obs: localnet localnet-20260928T120000Z: myclient v0 (jip3)
$ ./obs current localnet
localnet-20260928T120000Z
$ ./obs runs
localnet-20260928T120000Z          localnet     2026-09-28 19:16 -> running
```

The first `register` wrote `targets/localnet--myclient.json`, which Alloy reads:

```json
[
 {
  "targets": [
   "host.docker.internal:9615"
  ],
  "labels": {
   "net": "localnet",
   "run_id": "localnet-20260928T120000Z",
   "job": "myclient",
   "node": "v0",
   "client": "myclient"
  }
 },
 {
  "targets": [
   "host.docker.internal:9616"
  ],
  "labels": {
   "net": "localnet",
   "run_id": "localnet-20260928T120000Z",
   "job": "myclient",
   "node": "v1",
   "client": "myclient"
  }
 }
]
```

A target is `[node@]host:port[/path]`. The node name defaults to the host, the path to
`/metrics`, and the client to the job (add `--label client=...` to override). `--jip2`
registers a JIP-2 RPC for the JIP-2 exporter to poll instead; `--jip3` registers a node's
JAMNP-S address so the JIP-3 receiver can name its telemetry connection. Registering a new
run id for a net drops that net's old targets. Ending the run removes them:

```text
$ ./obs end localnet -v
obs: localnet ended (run localnet-20260928T120000Z)
$ ./obs runs
localnet-20260928T120000Z          localnet     2026-09-28 19:16 -> 2026-09-28 19:16
$ ./obs current localnet; echo "exit $?"
exit 1
```

## Annotations: marking what happened

An annotation is a mark on the time axis of every dashboard showing that run:

```sh
./obs annotate "$OBS_RUN_ID" "load on" --tags load            # a point, now
./obs annotate "$OBS_RUN_ID" "soak" --start start --end now   # a region over the run
./obs annotate "$OBS_RUN_ID" "clearing SLO 0.66" --tags fail  # drawn red
```

The tags `pass` and `fail` draw it green or red; anything else draws a blue *run event*.
`--start` and `--end` take `now`, `start` (the run's recorded start), or a Unix time in
seconds or milliseconds. The CLI writes annotations as Grafana's admin.

## Links: opening a run where it happened

A dashboard link needs the net, the run id and the time range. `obs link` builds it from
the run record:

```sh
./obs link RUN_ID                 # the Chain health dashboard
./obs link RUN_ID -d dex          # another dashboard, by uid with or without "obs-"
./obs link RUN_ID --all           # every dashboard that has a run selector
```

Real output for the failing run of the case study:

```text
$ ./obs link lasair6-20260928T153915Z -d dex
http://localhost:3300/d/obs-dex?orgId=1&var-run_id=lasair6-20260928T153915Z&var-net=lasair6&from=1790609895389&to=1790613868842
```

The range runs from a minute before the recorded start to a minute after the recorded end.
For a run still going, the link ends at `now` and refreshes every 10 seconds. For a run
this machine never recorded, `link` finds its net in Prometheus and falls back to the last
six hours.

`--all` prints one line per dashboard (folder/title, then the link):

```text
platform/Chain health      http://localhost:3300/d/obs-chain?...
platform/Logs              http://localhost:3300/d/obs-logs?...
platform/Network overview  http://localhost:3300/d/obs-overview?...
jamswap/DEX                http://localhost:3300/d/obs-dex?...
jamswap/Soak runs          http://localhost:3300/d/obs-soak-runs?...
lasair/lasair networking   http://localhost:3300/d/lasair-net?...
lasair/lasair node overview http://localhost:3300/d/lasair-node?...
lasair/lasair PVM          http://localhost:3300/d/lasair-pvm?...
lasair/work-package lifecycle http://localhost:3300/d/lasair-wp?...
```

(Links shortened here. Which lines you get depends on the dashboard folders you have.)

## Batch results: `obs push`

A job that ends with results pushes them to the Pushgateway, grouped by job and run id, so
every result series carries the run id:

```sh
./obs push soak --group run_id=$OBS_RUN_ID --group net=mynet <<EOF
# TYPE soak_pass gauge
soak_pass 1
EOF
```

A push replaces only the metrics it names within that group, so a job can push progress
while it runs and results at the end. Groups stay until deleted:
`curl -X DELETE http://localhost:9091/metrics/job/soak/run_id/<id>/net/<net>`.

## jamswap does all of this for you

For jamswap's networks, `./dex up NET=<net>` begins a run, passes its id to every
container's labels, annotates the start and prints the dashboard links; `load`, `noload`,
`soak` and `down` annotate the run as they go; the soak pushes its results. You rarely
type these commands for a jamswap net. You will for your own networks.

Next: [The jam_* metrics](../02-telemetry/01-the-jam-metrics.md)
