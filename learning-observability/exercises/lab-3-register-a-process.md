# Lab 3: Register a process

**Needs:** the stack running (lab 1). **Time:** 15 minutes.

You run a fake JAM node on your machine, register it with the stack, and watch it appear
on the Network overview like a real one. Then you stop it and see how the dashboards
report a node that goes away.

The fake node and the `register` step were run while the course was written; the steps
that need the stack show the **expected** output, worked out from the code.

## 1. Start the fake node

[`fake_node.py`](fake_node.py) serves three `jam_*` metrics on port 19615: a best slot that
starts at 100 and grows by one every 6 seconds, a finalized slot two behind, and
`jam_node_info`. In a first terminal, from the observability checkout:

```sh
python3 learning-observability/exercises/fake_node.py
```

```text
fake node: http://0.0.0.0:19615/metrics (Ctrl-C to stop)
```

Check it in a second terminal:

```sh
curl -s localhost:19615/metrics
```

```text
# TYPE jam_best_slot gauge
jam_best_slot 100
# TYPE jam_finalized_slot gauge
jam_finalized_slot 98
# TYPE jam_node_info gauge
jam_node_info{client="fakeclient",client_version="0.0.1",gp_version="0.8.0",spec="tiny"} 1
```

It listens on all interfaces, because Alloy runs in a container and reaches your machine
through `host.docker.internal`.

## 2. Register it

```sh
run=$(./obs new-run localnet)
echo "$run"
./obs register localnet "$run" fakeclient v0@host.docker.internal:19615 -v
```

```text
localnet-20260928T195134Z
obs: localnet localnet-20260928T195134Z: fakeclient v0 (scrape)
```

(Your run id carries your time.) The CLI wrote a file target, which Alloy reads within 5
seconds:

```sh
cat ~/.cache/abutlabs-obs/targets/localnet--fakeclient.json
```

```json
[
 {
  "targets": [
   "host.docker.internal:19615"
  ],
  "labels": {
   "net": "localnet",
   "run_id": "localnet-20260928T195134Z",
   "job": "fakeclient",
   "node": "v0",
   "client": "fakeclient"
  }
 }
]
```

## 3. See it arrive

Wait 10 seconds, then:

```sh
./obs status -v
```

Expected, among the lines:

```text
localnet       localnet-20260928T195134Z        fakeclient 1/1 up
    localnet     fakeclient v0         fakeclient up   host.docker.internal:19615
```

Now open the Network overview for the run:

```sh
./obs link "$run" -d overview
```

Expected on the dashboard:

- *Nodes advancing* 1, *Clients* 1;
- in the Nodes table, one row: `v0`, `fakeclient`, state `advancing`, version `0.0.1`, GP
  `0.8.0`, a best slot counting up from 100, a finalized slot two behind, *finality lag* 2,
  *paths* 1 (`metrics`: its own endpoint); `stopped`, `why` and `exit code` are empty, since
  lifecycle only tracks Docker containers and this process runs directly on your machine;
- *Best slot per node*: one line climbing a step every 6 seconds.

Mark the moment:

```sh
./obs annotate "$run" "lab 3: fake node registered"
```

A blue mark appears on the graphs (refresh within 10 seconds).

## 4. Stop it, and read what the dashboards say

Stop the fake node with Ctrl-C in the first terminal. Do not run `./obs end` yet. Within
about a minute (`STOP_GRACE`, 60 s):

- `./obs status -v` shows `v0` as `DOWN`;
- on the Network overview, the best-slot line ends, the **Run** tile turns red: *net
  stalled since HH:MM:SS — run not ended* (there is no end record, since you have not run
  `./obs end`), and the Nodes table's `v0` row turns state `net stalled`, not *stopped
  during the run*.

*Stopped during the run* (lesson 3.6) needs another node still advancing to fall behind;
with one node, there is no "rest of the net" to compare against, so a lone node that goes
quiet always reads *net stalled*, never red-for-that-node. Open **Node detail** for `v0`:
*State* also says `net stalled`, but *Why it stopped* stays `not stopped` — lifecycle has
no Docker event for this process, so it cannot say why, only the metrics can say that it
went quiet. Walk what is left of the drill-down path: which node, since when (`last
advanced`), what else happened then (your own annotation), and why (you pressed Ctrl-C).

## 5. Clean up

```sh
./obs end localnet
```

The run is recorded as ended and its file target removed.

## Check yourself

1. Why `host.docker.internal` and not `localhost` in the target?
2. The table shows version `0.0.1`. Which metric does it come from, and why would a node
   described only by JIP-2 show no version?
3. After step 5, does the run disappear from Grafana?

<details>
<summary>Answers</summary>

1. The address is used by Alloy, which runs in a container. Inside a container,
   `localhost` is the container itself; `host.docker.internal` is your machine (the stack
   maps it for Alloy with `extra_hosts`).
2. From `jam_node_info`'s `client_version` label. JIP-2's RPC does not report the client's
   name or version, so the JIP-2 exporter leaves those labels empty.
3. No. `obs end` records the end and stops the scraping; the data already stored stays for
   the retention period (30 days), and the run stays in the run selector.

</details>
