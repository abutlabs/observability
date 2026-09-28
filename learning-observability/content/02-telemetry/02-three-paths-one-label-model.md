# Three paths, one label model

## The three ways in

| Path | The node must | Opt in with | You get |
|---|---|---|---|
| **Scrape** | serve `/metrics` | labels `org.abutlabs.obs.scrape=true` and `org.abutlabs.obs.port=<port>` | Everything the node exports: the `jam_*` set and its own metrics |
| **JIP-3** | push JIP-3 telemetry | start it with `--telemetry obs-jip3:9910` (from a container) or `127.0.0.1:9910` (on your machine) | Chain, peers, authoring, importing, the guarantor pipeline, refine time and gas, assurances, tickets; and every event as a log line |
| **JIP-2** | serve a JIP-2 RPC | label `org.abutlabs.obs.jip2=<RPC port>` (or a `ws://` URL) | Best and finalized slot, whether the node answers, and whether all nodes hold the same blocks |

Pick the richest path the client supports. More than one is fine: PolkaJam nodes, which
serve no `/metrics`, typically report through JIP-3 *and* JIP-2.

## The label model

Every series and every log stream carries the same labels, whatever path it came through.
This is the contract that lets one dashboard show any client.

| Label | Meaning | When the container does not say |
|---|---|---|
| `net` | the network | its compose project, else `docker` |
| `run_id` | one life of the network | `<net>-adhoc` |
| `node` | the node, or the service | its compose service, else its container name |
| `client` | the implementation (`lasair`, `polkajam`...) or the kind of service | the job |
| `job` | what kind of thing it is | its compose service, else its container name |

A series a collector *derives* also carries **`source`**: `jip3` or `jip2`. A series
scraped from the node itself has none. So `jam_best_slot{node="pj3", source="jip3"}` is
what the JIP-3 receiver learned from `pj3`'s events.

A node that reports through two paths therefore has two series for the same fact. The
dashboards take `max by (node, client)` across them, so it shows once:

```promql
max by (node, client) (jam_best_slot{net="$net",run_id="$run_id"})
```

## Every container label

| Label | Effect |
|---|---|
| `org.abutlabs.obs.scrape=true` + `org.abutlabs.obs.port=<port>` | Alloy scrapes `http://<container>:<port>/metrics` every 5 s |
| `org.abutlabs.obs.path=/stats` | a metrics path other than `/metrics` |
| `org.abutlabs.obs.jip2=<port>` or `=ws://host:port` | the JIP-2 exporter polls this RPC |
| `org.abutlabs.obs.jip2.node`, `org.abutlabs.obs.jip2.client` | a proxy that serves another node's RPC names the node it describes |
| `org.abutlabs.obs.logs=true` | ship its logs to Loki (a scraped container's logs are shipped unless `logs=false`) |
| `org.abutlabs.obs.net`, `.run_id`, `.node`, `.client`, `.job` | override the defaults above |
| `org.abutlabs.obs.enable=false` | ignore the container entirely |

Any `org.abutlabs.obs.*` label also makes netjoin attach the collectors to the container's
networks.

## A real example: jamswap's lasair6 network

jamswap's `docker-compose.lasair6.yml` (six lasair validators and a DEX) opts in like
this. A shared anchor for the whole network:

```yaml
x-obs: &obs
  org.abutlabs.obs.net: lasair6
  org.abutlabs.obs.run_id: ${OBS_RUN_ID:-}
  org.abutlabs.obs.logs: "true"
```

Each lasair validator (`lm0` to `lm5`) adds a scrape of its metrics port:

```yaml
    org.abutlabs.obs.job: lasair
    org.abutlabs.obs.client: lasair
    org.abutlabs.obs.scrape: "true"
    org.abutlabs.obs.port: "9615"
```

And the JIP-2 readers, small proxies that serve a validator's chain over JIP-2, name the
validator they describe, so their series land on `lm0`, not on the proxy:

```yaml
      org.abutlabs.obs.job: reader
      org.abutlabs.obs.client: lasair
      org.abutlabs.obs.jip2: "19990"
      org.abutlabs.obs.jip2.node: lm0
      org.abutlabs.obs.jip2.client: lasair
```

`./dex up` sets `OBS_RUN_ID`, and the node label defaults to the compose service name.
Nothing else is needed: the stack finds the whole network by these labels.

## How Alloy turns labels into targets

For the curious, [`alloy/config.alloy`](../../../alloy/config.alloy) does it in two steps:

1. `discovery.docker` lists every container every 5 seconds, with its labels as
   `__meta_docker_container_label_*`.
2. `discovery.relabel` rules apply the defaults above (compose project → `net`, compose
   service → `node` and `job`, `<net>-adhoc` → `run_id`), keep only containers with
   `scrape=true` and a numeric `port`, and build the address from the container name and
   port.

The same defaults are written once more in Python, in
[`lib/obslib.py`](../../../lib/obslib.py), for the two JAM collectors, so a metric Alloy
scrapes and a series the JIP-3 receiver derives for the same container carry identical
labels.

## `honor_labels`: whose labels win

Some exporters describe other nodes. jamswap's netwatch, for one, polls every node's RPC
and exports one series per node, each already labelled with the `node` and `client` it
describes; but netwatch's own scrape target is labelled `node="netwatch"`. Alloy scrapes
with `honor_labels = true`, which means *when a series and its target disagree on a
label, the series' own label wins*. The same setting keeps the `job="soak"` and `run_id`
of the results a soak pushes to the Pushgateway, which Alloy scrapes as the target
`job="obs-pushgateway"`.

## A PolkaJam node, for comparison

PolkaJam serves no `/metrics`. On jamswap's `lasair-pj` network each PolkaJam node gets
`TELEMETRY: "${OBS_JIP3:-}"` in its environment (its entrypoint turns that into
`--telemetry`) and these labels:

```yaml
      org.abutlabs.obs.net: "lasair-pj"
      org.abutlabs.obs.run_id: "${OBS_RUN_ID:-}"
      org.abutlabs.obs.job: "polkajam"
      org.abutlabs.obs.client: "polkajam"
      org.abutlabs.obs.logs: "true"
      org.abutlabs.obs.jip2: "42603"
```

So it reports through JIP-3 (events and derived metrics), JIP-2 (the exporter polls port
42603) and its container logs. No scrape labels, because there is nothing to scrape.

Next: [JIP-3 and JIP-2](03-jip3-and-jip2.md)
