# Lab 1: Start the stack

**Needs:** Docker with Compose v2, Python 3.8+, an observability checkout. **Time:** 15
minutes, plus image downloads the first time.

Step 1 shows the **expected** output, worked out from the code (the course's author did
not start a stack for it). The output of step 2 is real, captured on 2026-09-28 from a
running stack with no network reporting.

## 1. Start it

From the observability checkout:

```sh
./obs up
```

Expected: one line per service with its URL, then the JIP-3 address and the dashboard
folders:

```text
grafana      http://localhost:3300
prometheus   http://localhost:9390
loki         http://localhost:3100
pushgateway  http://localhost:9091
alloy        http://localhost:12345
jip3         localhost:9910  (--telemetry; containers on an observed net: obs-jip3:9910)
dashboards   platform, ...
```

If `dashboards` shows only `platform`, see lesson 1.3 (point `LASAIR_DIR` and
`JAMSWAP_DIR` at your checkouts and run `./obs up` again).

> **If jamswap's first monitoring stack is running** (containers named `obs-prometheus`
> and `obs-grafana`, started with jamswap's older `monitor/obs/obs up`): it uses the same
> compose project name, `obs`, and the same volume names, so `./obs up` replaces its two
> containers with this stack and keeps its data volumes. Runs it recorded stay queryable.

## 2. Check it

```sh
./obs status
echo "exit $?"
```

With no network running:

```text
grafana      http://localhost:3300        up
prometheus   http://localhost:9390        up
loki         http://localhost:3100        up
pushgateway  http://localhost:9091        up
alloy        http://localhost:12345       up
no run is reporting
netjoin      alloy on 0 network(s), jip2-exporter on 0 network(s), jip3-receiver on 0 network(s)
exit 0
```

(The `netjoin` line appears once Prometheus has netjoin's metrics, a few seconds after
start.) Then:

```text
$ ./obs ping; echo "ping exit $?"
ping exit 0
```

and `docker compose -p obs ps` lists the eight services, all running.

## 3. Look around Grafana

Open http://localhost:3300.

1. You land on **Network overview**. It is empty: no network reports yet.
2. Open the dashboard list. Find the `platform` folder and its four dashboards, and the
   `lasair` and `jamswap` folders if your checkouts provided them.
3. Open **obs self-health**. *obs services down* should be green at 0; *Targets up, by
   job* should show the `obs-*` jobs.
4. Open **Explore**, pick *Prometheus*, and run `up`. Every target Alloy scrapes appears
   with value 1: the stack's own services and the collectors.

## 4. Look around Alloy

Open http://localhost:12345. Find the components `discovery.docker.containers`,
`prometheus.scrape.containers`, `prometheus.scrape.collectors` and
`loki.source.docker.containers`. Open `prometheus.scrape.collectors`: its targets are the
Pushgateway, the JIP-3 receiver (`:9911`) and the JIP-2 exporter (`:9912`), each with its
last scrape time and error, if any.

## 5. Stop it (optional)

```sh
./obs down        # keeps the data
```

## Check yourself

1. Which service would you look at first if a node's metrics were missing from Grafana, and
   why?
2. Why does `./obs status` list services even when no network is running, but no run?
3. Where is the admin password applied, and when must you set it?

<details>
<summary>Answers</summary>

1. **Alloy** (its UI, or `./obs status -v`): it is the only collector, so either it
   discovered the node and failed to scrape it (the UI shows the error), or it never
   discovered it (the labels or the network are wrong). Prometheus scrapes nothing itself.
2. The service lines come from each service's readiness endpoint. The run lines come from
   series in Prometheus that carry a `run_id` (`up{run_id!=""}` and the derived
   `jam_best_slot`): with no network, there are none.
3. `OBS_GRAFANA_PASSWORD` is applied when Grafana's volume is created, so it must be set
   before the first `./obs up`. After that, changing the variable does not change the
   password.

</details>
