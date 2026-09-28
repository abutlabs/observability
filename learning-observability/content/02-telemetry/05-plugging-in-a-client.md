# Plugging in a new client

You have a JAM client the stack has never seen. Five steps get it onto every platform
dashboard, beside lasair and PolkaJam.

## 1. Run it where the stack can see it

In a container on the network's Docker network (the usual case), or as a process on your
machine, registered with `./obs register` (lesson 1.4).

## 2. Label it

At least the client name, plus the network and the run, so its series land in the right
place:

```yaml
x-obs: &obs
  org.abutlabs.obs.net: mynet
  org.abutlabs.obs.run_id: ${OBS_RUN_ID:-}
  org.abutlabs.obs.logs: "true"

services:
  v0:
    image: example/newclient:1.0
    labels: { <<: *obs, org.abutlabs.obs.client: newclient }
```

`client` should be the implementation's name in lower case. It is how every
"by client" panel groups nodes.

## 3. Pick the richest path it supports

| If the client... | Add |
|---|---|
| serves `/metrics` with the standard `jam_*` names | `org.abutlabs.obs.scrape: "true"`, `org.abutlabs.obs.port: "<port>"` |
| implements JIP-3 | start it with `--telemetry obs-jip3:9910` (from a container) or `--telemetry 127.0.0.1:9910` (on your machine) |
| serves only a JIP-2 RPC | `org.abutlabs.obs.jip2: "<RPC port>"` |

More than one is fine and gives you more: the scrape for the client's own detail, JIP-3
for event logs, JIP-2 for the independent hash comparison.

A client that serves `/metrics` under its own names only (no `jam_*`) still gets scraped,
and its metrics are there for its own dashboards, but the platform dashboards will not show
it. Add JIP-3 or JIP-2 to get it onto them.

## 4. Check that it arrived

```sh
./obs status        # the run is listed, with its jobs and "jip3 nodes"/"jip2 nodes" counts
./obs status -v     # one line per scraped target, up or DOWN
```

Then in Grafana:

- **Network overview**: the node has a row in the Nodes table, with its client, version
  (from `jam_node_info`), best and finalized slot, and **paths**, the number of ingestion
  paths that describe it. A node on two paths shows `2`.
- **obs self-health**: the JIP-3 receiver panel shows connected nodes and events per
  second; the JIP-2 exporter panel shows nodes polled and answering.
- **Logs**: pick the net, run and node; its container output should be there.

If a JIP-3 node appears under `net="jip3"` with a generated name, the receiver could not
match it to a container (lesson 2.3, *Which node is it?*): check its labels.

## 5. Add its own dashboards (optional)

Client-specific metrics belong in the client's own dashboards, kept as code in its own
repository under `observability/dashboards/`. `./obs up` mounts that directory as a
Grafana folder named after the checkout. For the dashboards to work with the rest of the
stack:

- use the datasource uids `prometheus` and `loki`;
- give each dashboard a stable `uid`;
- give it the variables `net` and `run_id`, so `./obs link` can open it for a run.

[`dashgen/obsdash.py`](../../../dashgen/obsdash.py) has small helpers for writing
dashboards in plain Python (a text panel stating the question, pass/fail stats with the
threshold in their title, run annotations, links between dashboards). The platform
generator, [`dashgen/gen_platform.py`](../../../dashgen/gen_platform.py), and lasair's and
jamswap's generators use them.

## A checklist for a client's `/metrics`

If you are the one adding metrics to a client, these rules (from
[`docs/metrics.md`](../../../docs/metrics.md)) keep it compatible:

- [ ] `jam_node_info` with `client`, `client_version`, `gp_version`, `spec`, `chain_id`,
      `validator_index`, `peer_id`, value always 1
- [ ] counters end in `_total`; durations are histograms in seconds, named `_seconds`
- [ ] no `net`, `run_id`, `node` or `instance` labels (the collector adds them)
- [ ] label values from short fixed lists: never a hash or free text (the one exception is
      `jam_node_info`, a single series whose labels name the node)
- [ ] default port 9615, with an option to change it

Next: [How to read a dashboard](../03-dashboards/01-how-to-read-a-dashboard.md)
