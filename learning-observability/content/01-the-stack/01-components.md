# The nine services

## In one breath

Think of the stack as a small newsroom for your network:

| Service | Its job, in plain words |
|---|---|
| **Alloy** | the reporter: goes to every node, collects its numbers and its log lines, delivers them |
| **Prometheus** | the archive of numbers (metrics) |
| **Loki** | the archive of text (log lines) |
| **Grafana** | the screen: dashboards drawn from both archives |
| **Pushgateway** | a mailbox where short jobs, like a soak test, leave their results |
| **JIP-3 receiver** | a translator: listens to JAM nodes that *send* telemetry, and turns it into numbers and log lines |
| **JIP-2 exporter** | a translator the other way round: *asks* JAM nodes about their chain and turns the answers into numbers |
| **netjoin** | the plumber: connects the reporter and the translators to each network's private Docker network |
| **lifecycle** | the obituary desk: watches Docker events and the run records, and says why a container stopped |

The rest of this lesson takes them one at a time. If a word is new, it is probably in
[The words you need](../00-welcome/02-the-words-you-need.md).

## What runs

The stack is one Docker Compose project (named `obs`, from [`compose.yml`](../../../compose.yml))
with nine services: nine containers that start and stop together. Nothing is built:
every service is a stock image, and the four JAM-specific ones are small
standard-library Python programs on `python:3.12-alpine`.

| Service | Image | Port on your machine | Role |
|---|---|---|---|
| grafana | `grafana/grafana:11.6.0` | 3300 | Dashboards |
| prometheus | `prom/prometheus:v3.4.1` | 9390 | Metrics storage and queries |
| loki | `grafana/loki:3.7.8` | 3100 | Log storage and queries |
| alloy | `grafana/alloy:v1.20.1` | 12345 | The one collector: finds targets, scrapes, ships logs |
| pushgateway | `prom/pushgateway:v1.11.3` | 9091 | Results of batch jobs, such as a soak's verdict |
| jip3-receiver | `python:3.12-alpine` | 9910 | JIP-3 telemetry server for JAM nodes |
| jip2-exporter | `python:3.12-alpine` | none | Polls JAM nodes' JIP-2 RPC |
| netjoin | `python:3.12-alpine` | none | Attaches the collectors to each network's Docker network |
| lifecycle | `python:3.12-alpine` | none | Watches Docker events and run records; exports why a container stopped |

Every published port binds to `127.0.0.1` (`OBS_BIND`; the JIP-3 port has its own,
`OBS_JIP3_BIND`), so nothing is reachable from other machines unless you change that.

## Prometheus: the metrics database

Prometheus stores series: a metric name, a set of labels, and a value sampled every few
seconds. You query it in PromQL (lesson 3.7). Prometheus is usually its own collector, but
in this stack it **scrapes nothing itself**: Alloy does the collecting and pushes every
sample into Prometheus over HTTP (Prometheus calls this *remote write*). It keeps 30 days of data, capped at 5 GB, whichever comes first
(`OBS_RETENTION_TIME`, `OBS_RETENTION_SIZE`); the oldest data goes first.

## Grafana: the dashboards

Grafana draws the dashboards and lets you explore both databases. Anyone who can reach the
port may look and explore without logging in (anonymous Viewer). Only the `admin` account
writes; the CLI uses it to add annotations. Its password is `obs` unless you set
`OBS_GRAFANA_PASSWORD` **before the first** `./obs up` (it is applied when Grafana's
*volume*, the Docker storage that outlives its container, is created).

Grafana reads from *datasources*, its name for the databases it can query. Its two have
fixed ids, `prometheus` and `loki`, so a dashboard written on one machine works on
another. The home page is the **Network overview**. Dashboards come in
folders: `platform` (this repository's own) and one folder per project that brings its
own (lesson 3.1).

## Loki: the log database

Loki stores log lines in *streams*, each identified by a set of labels, and you query it
in LogQL (lesson 3.8). Lines arrive from two places: Alloy ships the output of every
labelled container, and the JIP-3 receiver writes each telemetry event as one JSON line.
It keeps 30 days (`OBS_LOG_RETENTION`, default `720h`). It has no size cap; ingestion limits
(8 MB/s overall, 2 MB/s per stream) bound how fast a noisy node can fill the disk.

## Grafana Alloy: the collector

Alloy is the only process that collects. It:

- watches Docker for containers carrying `org.abutlabs.obs.*` labels, and scrapes the ones
  that ask for it every 5 seconds;
- reads *file targets* for processes running directly on your machine (written by
  `./obs register`);
- scrapes the Pushgateway, the two JAM collectors and the stack's own services;
- remote-writes every sample to Prometheus;
- ships the stdout and stderr of labelled containers to Loki.

Its web UI, http://localhost:12345, shows every component and every target with its last
scrape. It is the first place to look when a target is missing.

## Pushgateway: results of batch jobs

Prometheus is built to scrape things that stay up. A soak test or a benchmark runs, ends,
and has results. It pushes them to the Pushgateway, which holds them (persisted to disk)
until they are deleted, and Alloy scrapes them from there like any other target. The soak
runner pushes its configuration, progress and verdict there (lesson 3.5).

## The JIP-3 receiver

[JIP-3](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-3.md) is the JAM telemetry
standard: a node started with `--telemetry HOST:PORT` opens a TCP connection and streams
one message per event. The receiver ([`jip3/`](../../../jip3/README.md)) accepts those
connections on port 9910, decodes every event the JIP defines, turns them into the standard
`jam_*` metrics and writes each event to Loki as a JSON line. It serves its metrics on port
9911 inside the stack, where Alloy scrapes them.

## The JIP-2 exporter

[JIP-2](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-2.md) is the JAM node RPC.
The exporter ([`jip2/`](../../../jip2/README.md)) polls each node that has one, every 3
seconds, for its best and finalized block, and compares block hashes across the network:
it is the stack's independent check that all nodes agree on one chain. Its metrics are on
port 9912 inside the stack.

## netjoin

A JAM network in Docker lives on its own Docker network, where its containers reach each
other by name. The collectors must be on that network too. netjoin watches Docker and
attaches Alloy, the JIP-2 exporter and the JIP-3 receiver to every network that has a
labelled container, as soon as its first container is created, and detaches them when the
last one stops. On each network they get an alias: `obs-alloy`, `obs-jip2` and `obs-jip3`.
That is why a containerised node can report to `obs-jip3:9910`.

## lifecycle: why a container stopped

A dashboard can say a node stopped reporting; lifecycle says why. It watches Docker events
for every container carrying an `org.abutlabs.obs.*` label (create, start, restart, kill,
oom, stop, die with its exit code, destroy) and the run records `obs begin`/`obs end`
write, and turns both into Loki lines (`source="lifecycle"`) and metrics:
`obs_container_running`, `obs_container_last_exit_code`,
`obs_container_stopped_timestamp_seconds{reason, during_run}`, and, for runs,
`obs_run_info`, `obs_run_start_timestamp_seconds`, `obs_run_end_timestamp_seconds`. It
also polls each running container's CPU and memory from the Docker stats API.

`reason` is one of: `oom` (the kernel killed it for memory), `teardown` (stopped and
removed, or stopped after its run had already ended: the net was shut down on purpose),
`stopped` (`docker stop` while its run was still active), `killed` (a signal with no
stop, such as `docker kill`), `crashed` (exited non-zero by itself) or `exited` (exited 0
by itself). `during_run` is `true` only when the stop was not a teardown and the
container's run had not yet ended. Node detail and the Network overview's Nodes table
read this (lesson 3.6). Its own metrics are on port 9914 inside the stack.

## Security in one paragraph

The stack is for a development machine. Alloy, netjoin, lifecycle and the JAM collectors
mount the Docker socket (netjoin to attach networks, the others only to read). Ports bind
to `127.0.0.1` by default. Anonymous Grafana users can view and explore; only admin
writes.

Next: [How the pieces fit together](02-how-it-fits-together.md)
