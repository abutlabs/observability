# How the pieces fit together

## The picture

Two flows, one for numbers and one for text.

```text
 METRICS (numbers)

   a node's own /metrics ───────────────────────────────┐
   JIP-3 node ──pushes──▶ jip3-receiver, its /metrics ──┤
   JIP-2 node ◀──polls─── jip2-exporter, its /metrics ──┤
   a soak ──pushes──▶ Pushgateway ──────────────────────┼──▶ Alloy ──▶ Prometheus ──▶ Grafana
   a process on your machine (obs register) ────────────┘    scrapes every one
                                                              of these every 5 s

 LOGS (text)

   a container's output (stdout, stderr) ──▶ Alloy ─────────────────▶ Loki ──▶ Grafana
   JIP-3 node ──pushes──▶ jip3-receiver ── one JSON line per event ──▶ Loki
```

And one helper behind the scenes: **netjoin** puts Alloy and the two JAM collectors on the
Docker network of every JAM network that has a labelled container, so they can reach its
containers by name.

A JAM node gets in through whichever of three paths its client supports (the first three
lines of the metrics flow):

1. It **serves `/metrics`**: Alloy scrapes it directly.
2. It **pushes JIP-3 telemetry**: the JIP-3 receiver decodes the events into metrics (which
   Alloy scrapes from the receiver) and into log lines (which the receiver writes to Loki).
3. It **serves a JIP-2 RPC**: the JIP-2 exporter polls it and exports metrics, which Alloy
   scrapes from the exporter.

More than one path per node is fine. Whatever the path, the metrics end in Prometheus with
the same names and labels, so the same dashboards show every node.

## Labels are the whole interface

Nothing is registered by hand for a container. A network opts in by putting labels on its
containers in its compose file:

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

Alloy re-reads Docker every 5 seconds. As soon as `node0` exists, it scrapes
`http://node0:9615/metrics` and ships its output to Loki, and every series and log line
carries the labels `net="mynet"`, `run_id=...`, `node="node0"`, `client="myclient"`.
Without the stack running, the labels do nothing and the network runs as before. Lesson
2.2 lists every label.

## Where each signal goes

| It starts as | It goes through | It ends in |
|---|---|---|
| a line of a node's `/metrics` | Alloy (scrape) | Prometheus |
| a JIP-3 event | JIP-3 receiver → Alloy (scrape of the receiver) | Prometheus, as `jam_*` series with `source="jip3"` |
| the same JIP-3 event | JIP-3 receiver | Loki, as one JSON line with `source="jip3"` |
| a JIP-2 answer (best block, finalized block) | JIP-2 exporter → Alloy | Prometheus, with `source="jip2"` |
| a line a container prints | Alloy (Docker logs) | Loki, with `source="docker"` |
| a soak's verdict | Pushgateway → Alloy | Prometheus, grouped by `job` and `run_id` |
| an annotation (`./obs annotate`) | Grafana's API | Grafana, drawn on every dashboard of that run |

## Processes on your machine

A node running directly on your machine, not in a container, has no labels to be found by.
You register it instead:

```sh
./obs register localnet "$run" myclient v0@host.docker.internal:9615
```

That writes a *file target* (a small JSON file in `~/.cache/abutlabs-obs/targets/`) that
Alloy reads. `host.docker.internal` is how a container, here Alloy, reaches your machine.
Lesson 1.4 shows the file it writes.

## Why netjoin exists

Docker networks are isolated: the containers of the `lasair6` network reach each other by
name (`lm0`, `lm1`, ...) on their own Docker network, but Alloy, sitting on the stack's own
network, cannot resolve those names. netjoin fixes that without the
network knowing about the stack. When a labelled container is *created* (before it
starts), netjoin attaches Alloy, the JIP-2 exporter and the JIP-3 receiver to that
container's networks. So when a node starts with `--telemetry obs-jip3:9910`, the name
already resolves. When the last labelled container on a network stops, netjoin detaches
them again.

One side effect you may see: `docker compose down` removes a network right after its
containers, often before the collectors have left. Compose then prints *"Resource is still
in use"* and exits 0; netjoin removes the empty network a moment later. It is harmless.

## Where your dashboards come from

`./obs up` looks at every folder next to the observability checkout. Each one that has an
`observability/dashboards/` directory becomes a Grafana folder of the same name, mounted
read-only. Grafana re-reads them every 10 seconds, so a regenerated dashboard appears
without restarting anything. The platform dashboards come from this repository's
[`dashboards/`](../../../dashboards/) folder.

Next: [Running the stack](03-running-the-stack.md)
