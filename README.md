# observability

Metrics, logs and dashboards for JAM networks with any mix of clients.

One Docker Compose stack (Grafana, Prometheus, Loki, Grafana Alloy, a Pushgateway, and
two small JAM collectors) that any JAM network on the same machine reports into. A node
is observed through whichever of three paths its client supports: its own Prometheus
endpoint, the JIP-3 telemetry stream it pushes, or the JIP-2 RPC it serves. All three land
in one label model (`net`, `run_id`, `node`, `client`), so one set of dashboards shows a
network whatever clients run it.

Projects keep their own dashboards as code and plug them in as Grafana folders; this repo
owns only the infrastructure and the cross-client ("platform") dashboards.

**New to it? Take the course:** https://abutlabs.github.io/observability/ (see
[Learn it](#learn-it)).

```sh
./obs up                 # start the stack (Docker Compose, project "obs")
open http://localhost:3300
```

Needs Docker with Compose v2 and Python 3.8+ for the CLI. Nothing is built: every service
is a stock image, and the JAM collectors are standard-library Python on `python:3.12-alpine`.

## Contents

- [Learn it](#learn-it)
- [Architecture](#architecture)
- [Services and ports](#services-and-ports)
- [Three ingestion paths](#three-ingestion-paths)
- [Labels](#labels)
- [Plugging a project in](#plugging-a-project-in)
- [Plugging in a new JAM client, in five steps](#plugging-in-a-new-jam-client-in-five-steps)
- [Dashboards](#dashboards)
- [Runs and the CLI](#runs-and-the-cli)
- [Retention and disk](#retention-and-disk)
- [A second stack beside the first](#a-second-stack-beside-the-first)
- [Traces](#traces)
- [Security](#security)
- [Repository layout](#repository-layout)

## Learn it

[**Learning Observability**](learning-observability/README.md), online at
https://abutlabs.github.io/observability/, is a course on this stack for someone new to
observability: the words it uses, each service, how JAM nodes of any client report into
it, every dashboard and the question it answers, PromQL and LogQL, soak tests, and a real
investigation of a failing lasair soak from symptom to fix. Its labs use the commands in
this README. The lessons are markdown in
[`learning-observability/content/`](learning-observability/content/), readable on GitHub
as well.

## Architecture

```
  a JAM network (any compose project, containers labelled org.abutlabs.obs.*)
  ┌───────────────────────────────────────────────────────────────────────────┐
  │  node A (/metrics)     node B (JIP-3 sender)     node C (JIP-2 RPC only)   │
  │  dex, load generator, other services with /metrics                        │
  └──────┬──────────────────────┬──────────────────────────┬──────────────────┘
         │ scrape (by name)     │ TCP push :9910           │ WebSocket poll
         │                      │ (--telemetry obs-jip3)   │
  ┌──────┴──────────────────────┴──────────────────────────┴──────────────────┐
  │ obs (this repo)             ┌──────────────┐      ┌───────────────┐        │
  │  ┌─────────────────┐        │ jip3-receiver│      │ jip2-exporter │        │
  │  │  Grafana Alloy  │◀───────│  /metrics    │      │  /metrics     │        │
  │  │ Docker discovery│◀───────┤──────────────┘      └───────┬───────┘        │
  │  │ file targets    │◀──────────────────────────────────────┘               │
  │  │ docker logs     │──────▶ Loki ◀── JIP-3 events as JSON lines            │
  │  └───────┬─────────┘                                                        │
  │          │ remote write            Pushgateway ◀── batch results (soaks)    │
  │          ▼                              │ (scraped by Alloy)                │
  │     Prometheus ◀────────────────────────┘                                   │
  │          ▲                                                                  │
  │     Grafana: folder "platform" (this repo) + one folder per project         │
  │                                                                             │
  │  netjoin: attaches Alloy and the two collectors to every network that has   │
  │  a labelled container, so they reach its containers by name                 │
  └─────────────────────────────────────────────────────────────────────────────┘
       processes on the host: `obs register` writes file targets Alloy reads
```

- **Grafana Alloy is the only collector.** It discovers containers through the Docker
  socket, scrapes the ones labelled for it, reads file targets for processes on the host,
  scrapes the Pushgateway and the obs services, remote-writes every sample to Prometheus,
  and ships labelled containers' logs to Loki.
- **netjoin** keeps Alloy, the JIP-2 exporter and the JIP-3 receiver on every Docker
  network that has a labelled container. They join when the network's first labelled
  container is created, before its nodes start, and leave when the last one stops.
  `docker compose down` removes the network right after its containers, usually before
  the collectors have left: Compose then prints "Resource is still in use" (and exits 0),
  and netjoin removes the empty network a moment later, once no container of that
  project is left. On a network with a configured subnet each
  takes the highest free address, never a dynamic one, so it cannot take a static address
  a node has not claimed yet. Each gets an alias there: `obs-alloy`, `obs-jip2`,
  `obs-jip3`.
- **Nothing is registered by hand for containers.** Labels on the containers are the whole
  interface. Processes on the host (no container to label) use `obs register`.

## Services and ports

Every published port binds to `OBS_BIND` (default `127.0.0.1`).

| Service | Image | Host port | What it does |
|---|---|---|---|
| grafana | grafana/grafana:11.6.0 | 3300 (`OBS_GRAFANA_PORT`) | Dashboards. Anonymous viewers; `admin` / `OBS_GRAFANA_PASSWORD` (default `obs`) for writes. |
| prometheus | prom/prometheus:v3.4.1 | 9390 (`OBS_PROMETHEUS_PORT`) | Metrics storage and queries (remote-write receiver; it scrapes nothing). |
| loki | grafana/loki:3.7.8 | 3100 (`OBS_LOKI_PORT`) | Logs. |
| pushgateway | prom/pushgateway:v1.11.3 | 9091 (`OBS_PUSHGATEWAY_PORT`) | Results of batch jobs. Persisted. |
| alloy | grafana/alloy:v1.20.1 | 12345 (`OBS_ALLOY_PORT`) | The collector; its UI shows every component and target. |
| jip3-receiver | python:3.12-alpine | 9910 (`OBS_JIP3_PORT`, bind `OBS_JIP3_BIND`) | JIP-3 telemetry ingest. Metrics on 9911 inside the stack. |
| jip2-exporter | python:3.12-alpine | none | Polls nodes' JIP-2 RPC. Metrics on 9912 inside the stack. |
| netjoin | python:3.12-alpine | none | Network attachment. Metrics on 9913 inside the stack. |

## Three ingestion paths

All three produce the standard `jam_*` series of
[docs/metrics.md](docs/metrics.md), labelled with
`net`, `run_id`, `node` and `client`. A series a collector derives also carries `source`
(`jip2` or `jip3`). The platform dashboards read `jam_*` only and take
`max by (node, client)` across paths, so a node shows once whichever paths describe it.

1. **Prometheus scrape**, for clients that serve `/metrics`. Label the container
   `org.abutlabs.obs.scrape=true` and `org.abutlabs.obs.port=<port>`; Alloy scrapes it every
   5 s. A client that exports the standard `jam_*` names appears on the platform dashboards
   directly; its own metrics sit beside them for its own dashboards.
2. **JIP-3 telemetry**, for clients that implement
   [JIP-3](https://github.com/polkadot-fellows/JIPs/blob/main/JIP-3.md) (`--telemetry
   HOST:PORT`). The receiver ([jip3/](jip3/README.md)) decodes every event the JIP defines,
   turns them into `jam_*` series (best and finalized slot, peers, blocks authored and
   imported, import time, the guarantor pipeline's stages, refine time and gas, assurances,
   tickets) and per-event counters (`jip3_events_total{event}`), and writes each event to
   Loki as a JSON line (`source="jip3"`). It labels a connection by the container it comes
   from (the TCP peer, or the address the node reports), else by a file target, else by the
   node's own name.
3. **JIP-2 RPC**, for clients with neither. Label the container `org.abutlabs.obs.jip2=<RPC
   port>` (or a `ws://` URL). The exporter ([jip2/](jip2/README.md)) polls `bestBlock`,
   `finalizedBlock` and `parameters` every 3 s and exports each node's best and finalized
   slot and lag, and, per net, head and finality agreement by block hash
   (`jam_net_one_head`, `jam_net_heads`, `jam_finality_conflicts_total`).

## Labels

Every series and every log stream carries:

| Label | Meaning | Default when the container does not say |
|---|---|---|
| `net` | the network | its compose project, else `docker` |
| `run_id` | one life of the network (`<net>-<UTC start>`) | `<net>-adhoc` |
| `node` | the node, or the service | its compose service, else its container name |
| `client` | the implementation (`lasair`, `polkajam`...) or the service kind | the job |
| `job` | what kind of thing it is | its compose service, else its container name |

Container labels (all optional except as noted):

| Label | Effect |
|---|---|
| `org.abutlabs.obs.scrape=true` + `org.abutlabs.obs.port=<port>` | Alloy scrapes `http://<container>:<port>/metrics`. |
| `org.abutlabs.obs.path=/stats` | a metrics path other than `/metrics` |
| `org.abutlabs.obs.jip2=<port>` or `=ws://host:port` | the JIP-2 exporter polls this RPC |
| `org.abutlabs.obs.jip2.node`, `.jip2.client` | a proxy that serves another node's RPC names that node |
| `org.abutlabs.obs.logs=true` | ship its logs to Loki (a scraped container's are shipped unless `logs=false`) |
| `org.abutlabs.obs.net`, `.run_id`, `.node`, `.client`, `.job` | override the defaults above |
| `org.abutlabs.obs.enable=false` | ignore the container entirely |

Any `org.abutlabs.obs.*` label makes netjoin attach the collectors to the container's
networks. Metrics exporters that describe other nodes (like the JIP-2 exporter) label each
series with the node it describes: Alloy keeps a series' own labels (`honor_labels`).

## Plugging a project in

**Containers.** Put the labels on the services in your compose file. A run id from your
tooling goes in through an environment variable, so each run of the net is told apart:

```yaml
x-obs: &obs
  org.abutlabs.obs.net: mynet
  org.abutlabs.obs.run_id: ${OBS_RUN_ID:-}
  org.abutlabs.obs.logs: "true"

services:
  node0:
    labels: { <<: *obs, org.abutlabs.obs.client: myclient, org.abutlabs.obs.scrape: "true",
              org.abutlabs.obs.port: "9615" }
```

```sh
export OBS_RUN_ID=$(./obs begin mynet)      # records the run; prints mynet-20260928T120000Z
docker compose up -d
./obs annotate "$OBS_RUN_ID" "load on" --tags load
./obs link "$OBS_RUN_ID" --all              # dashboard links with the run's time range
./obs end mynet                             # before docker compose down
```

Without the stack the labels do nothing and the net runs as before. Keep the run id of a
running net when you re-run `docker compose up`: a changed label makes Compose recreate
the container.

**Processes on the host.** Register their endpoints; a container reaches the host at
`host.docker.internal`:

```sh
run=$(./obs new-run localnet)
./obs register localnet "$run" myclient v0@host.docker.internal:9615 v1@host.docker.internal:9616
./obs register localnet "$run" myclient v0@host.docker.internal:19800 --jip2   # JIP-2 RPC
./obs register localnet "$run" myclient v0@127.0.0.1:40000 --jip3             # JIP-3: its JAMNP-S address
./obs unregister localnet
```

The targets are file_sd JSON files in `~/.cache/abutlabs-obs/targets/` (`OBS_STATE`).

**Dashboards.** Put Grafana dashboard JSON in `<project>/observability/dashboards/`. `obs
up` mounts every sibling checkout that has that directory as a Grafana folder named after
it, and skips the ones that do not. Point a name elsewhere with `<NAME>_DIR` (for example
`LASAIR_DIR=/path/to/lasair`), or add projects with `OBS_PROJECT_DIRS=/a:/b`. Use the
datasource uids `prometheus` and `loki`, give each dashboard a stable `uid`, and add the
variables `net` and `run_id` so `obs link` can open it for a run. Grafana reloads a folder
within 10 s. [dashgen/obsdash.py](dashgen/obsdash.py) has small helpers for writing
dashboards as Python; [dashgen/gen_platform.py](dashgen/gen_platform.py) uses them.

**Batch results (the Pushgateway contract).** A job that ends with results (a soak, a
benchmark) pushes Prometheus text grouped by `job` and `run_id` (and `net`), so every
result series carries the run id and dashboards can compare runs:

```sh
./obs push soak --group run_id=$OBS_RUN_ID --group net=mynet <<EOF
# TYPE soak_pass gauge
soak_pass 1
EOF
```

A push replaces the same-named metrics of that group only, so a job can push progress
while it runs and results at the end. Push an info metric (`<job>_info{...} 1`) for the
configuration. Groups persist until deleted (`curl -X DELETE
http://localhost:9091/metrics/job/soak/run_id/<id>/net/<net>`).

**Logs.** Anything a labelled container writes to stdout or stderr is in Loki, labelled
like its metrics plus `container` and `source="docker"`. The **Logs** dashboard browses
them; LogQL: `{net="mynet", run_id="...", node="node0"} |= "error"`.

## Plugging in a new JAM client, in five steps

1. **Run it in a container** on the net's Docker network, or natively with `obs register`.
2. **Label it** `org.abutlabs.obs.client=<client name>` (and `net`, `run_id` as above).
3. **Pick the richest path it supports:** `/metrics` with the standard `jam_*` names
   (label `scrape=true`, `port`); else JIP-3 (start it with `--telemetry obs-jip3:9910`
   from a container, `127.0.0.1:9910` natively); else JIP-2 (label `jip2=<RPC port>`).
   More than one path is fine.
4. **Check it arrived:** `./obs status` lists the run and its targets; the **obs
   self-health** dashboard shows JIP-3 connections and JIP-2 nodes polled; the **Network
   overview** lists the node with its client and how many paths describe it.
5. **Add its own dashboards** (optional): client-specific metrics go in the client
   project's `observability/dashboards/`, which becomes its Grafana folder.

## Dashboards

Folder **platform** (this repo, generated by `python3 dashgen/gen_platform.py`):

| Dashboard | Answers |
|---|---|
| Network overview (`obs-overview`, the home page) | Which nodes make up this net, what client runs each, is every node keeping up? A node table (client, version, best and finalized slot, lag, peers, paths) and per-client authoring, import, guaranteeing and refine panels. |
| Chain health (`obs-chain`) | Is the net one chain that keeps growing and finalizing on every node? One head, finality lag, finality progress, conflicts. |
| Logs (`obs-logs`) | What did the nodes say? Container output and JIP-3 events, by run and node, with a search box. |
| obs self-health (`obs-self`) | Is the stack itself collecting? Services up, targets, write failures, dropped logs, storage, the JAM collectors, netjoin. |

Each project's folder holds its own. Pick the net and the run at the top of a dashboard;
the link `obs link` prints sets both and the time range. Annotations mark run events
(blue) and PASS and FAIL verdicts (green, red).

## Runs and the CLI

`./obs -h` has every command. The stack: `up`, `down [--wipe]`, `status [-v]`, `ping`,
`url NAME`. Runs: `new-run`, `begin`, `end`, `current`, `runs`. Host processes: `register`,
`unregister`. Grafana: `annotate`, `link`. Batch results: `push`. Run records (start, end,
net) are kept in `~/.cache/abutlabs-obs/runs/` so a report can link a finished run with
its exact time range.

## Retention and disk

- Prometheus keeps 30 days, capped at 5 GB, whichever comes first (`OBS_RETENTION_TIME`,
  `OBS_RETENTION_SIZE`).
- Loki keeps 30 days (`OBS_LOG_RETENTION`, default `720h`); the compactor deletes older
  chunks. Loki has no size cap: ingestion limits (8 MB/s, 2 MB/s per stream) bound how fast
  a noisy node can grow it. A validator logging a few lines per slot writes about 25 KB a
  minute: a six-node net running for a whole month is about 7 GB of text, one to two GB
  on disk after Loki's compression. Shorten `OBS_LOG_RETENTION` if the disk is small.
- The stack's own container logs rotate at 3 × 10 MB.
- `./obs down` keeps every volume; `./obs down --wipe` deletes them.

## A second stack beside the first

Every name derives from `OBS_PROJECT`, so a second stack (for example to try a change
while the first keeps running) needs only its own project name, ports and state:

```sh
OBS_PROJECT=obs2 OBS_STATE=~/.cache/abutlabs-obs2 OBS_GRAFANA_PORT=3400 \
OBS_PROMETHEUS_PORT=9490 OBS_LOKI_PORT=3110 OBS_PUSHGATEWAY_PORT=9491 \
OBS_ALLOY_PORT=12346 OBS_JIP3_PORT=9920 ./obs up
```

The obs services reach each other by `<project>-<service>` aliases, so a net's own
`prometheus` or `loki` never shadows them.

## Traces

Not collected yet. The place for them: a Grafana Tempo service in `compose.yml`, an
`otelcol.receiver.otlp` in `alloy/config.alloy` forwarding to it, and a Tempo datasource
next to the other two.

## Security

The stack is for a development machine. Alloy, netjoin and the collectors mount the Docker
socket (netjoin needs it to attach containers to networks; the others only read). Every
port binds to 127.0.0.1 by default. Anonymous Grafana users may view and explore; only the
admin account writes (the CLI uses it for annotations). Set `OBS_GRAFANA_PASSWORD` before
the first `up`.

## Repository layout

| Path | What |
|---|---|
| `obs` | the CLI (Python, standard library) |
| `compose.yml` | the stack |
| `alloy/config.alloy` | the collector: discovery, relabeling, scrape, logs |
| `prometheus/`, `loki/`, `grafana/provisioning/` | their configuration |
| `netjoin/` | attaches the collectors to the nets' networks |
| `jip3/` | the JIP-3 telemetry receiver, and a synthetic sender |
| `jip2/` | the JIP-2 RPC exporter |
| `lib/obslib.py` | shared by the services: Docker API, the label model, metrics |
| `dashboards/` | the platform dashboards (JSON, generated) |
| `dashgen/` | dashboard helpers and the platform generator |
| `docs/metrics.md` | the `jam_*` metrics the dashboards read |
| `tests/` | `python3 -m unittest discover -s tests` |
| `learning-observability/` | the course ([Learn it](#learn-it)); `.github/workflows/pages.yml` publishes it |

## License

MIT, see [LICENSE](LICENSE).
