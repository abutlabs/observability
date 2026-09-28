# Lab 2: JIP-3 without Docker

**Needs:** Python 3.8+ and an observability checkout. No Docker, no network, no stack.
**Time:** 10 minutes.

You run the JIP-3 receiver as a plain process, feed it from the synthetic sender (a
stand-in JAM node that plays scripted rounds of a busy validator), and read what the
receiver makes of it. Every output below was captured by running these exact commands on
2026-09-28.

The receiver uses ports 19910 (JIP-3) and 19911 (its `/metrics`) here, so it does not
collide with a stack's receiver on 9910. Its `/metrics` listens on all interfaces; stop it
when you are done.

## 1. Start the receiver

In a first terminal, from the observability checkout:

```sh
JIP3_LISTEN=127.0.0.1:19910 JIP3_METRICS_PORT=19911 JIP3_LOKI_URL= \
JIP3_TARGETS='/nonexistent/*.json' DOCKER_SOCK=/nonexistent \
python3 jip3/receiver.py
```

`JIP3_LOKI_URL=` (empty) turns log shipping off; the two `/nonexistent` paths turn off the
container and file-target lookups. It prints:

```text
2026-09-28T19:16:23Z jip3-receiver: JIP-3 on 127.0.0.1:19910, /metrics on :19911, Loki off, Docker labels off
```

## 2. Send three rounds

In a second terminal, same folder:

```sh
python3 jip3/sender.py --port 19910 --count 3 --rate 200
```

```text
sent node info + 99 events (next event id 99)
```

The first terminal logs the connection. Note **rule self**: with no Docker and no
registered target, the receiver names the node from its own description (lesson 2.3).

```text
2026-09-28T19:16:24Z jip3-sender-cd16df6b connected from 127.0.0.1 (rule self): jip3-sender 0.1.0, GP 0.8.0, params gp-0.8/122 bytes, cores 2, protocol 1
2026-09-28T19:16:25Z jip3-sender-cd16df6b disconnected after 99 events
```

## 3. Read the derived metrics

```sh
curl -s localhost:19911/metrics | grep -E '^jam_(node_info|best_slot|finalized_slot|peers|blocks_authored_total|wp_received_total|wp_refused_total|refine_gas_total|tickets_generated_total)'
```

```text
jam_best_slot{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 9148364
jam_blocks_authored_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 3
jam_finalized_slot{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 9148362
jam_node_info{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",client_version="0.1.0",gp_version="0.8.0",spec="tiny",chain_id="d4e07b5b5551d315",validator_index="",peer_id="cd16df6bbb54917c06abaed4a046b8be87872471e653753b34ac01640956d9e7",source="jip3"} 1
jam_peers{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3",role="other"} 1
jam_peers{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3",role="validator"} 4
jam_refine_gas_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 4500000
jam_tickets_generated_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 6
jam_wp_received_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3"} 3
jam_wp_refused_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3",reason="anchor"} 3
jam_wp_refused_total{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",source="jip3",reason="duplicate"} 3
```

The slot numbers are the current JAM slot (the sender computes it from the clock), so
yours will be larger. Everything else should match.

The pipeline's stage timings are there too, one observation per stage per round:

```sh
curl -s localhost:19911/metrics | grep '^jam_wp_stage_seconds_count'
```

```text
jam_wp_stage_seconds_count{...,stage="authorize"} 3
jam_wp_stage_seconds_count{...,stage="distribute"} 3
jam_wp_stage_seconds_count{...,stage="guarantee"} 3
jam_wp_stage_seconds_count{...,stage="receive"} 3
jam_wp_stage_seconds_count{...,stage="refine"} 3
jam_wp_stage_seconds_count{...,stage="report"} 3
```

(Labels shortened here.)

## 4. Watch a live connection

The receiver lists who is connected at `/connections`. Hold a connection open for a few
seconds and look:

```sh
python3 jip3/sender.py --port 19910 --count 1 --rate 200 --hold 4 &
sleep 2; curl -s localhost:19911/connections
```

```json
[
 {
  "labels": {
   "net": "jip3",
   "run_id": "jip3-adhoc",
   "node": "jip3-sender-cd16df6b",
   "client": "jip3-sender"
  },
  "rule": "self",
  "tcp_peer": "127.0.0.1",
  "events": 33,
  "impl": "jip3-sender",
  "impl_version": "0.1.0",
  "gp_version": "0.8.0",
  "params_layout": "gp-0.8",
  "cores": 2
 }
]
```

## 5. Every event, once

```sh
python3 jip3/sender.py --port 19910 --every-event --rate 0
```

```text
sent node info + 115 events (next event id 115)
```

That is one of each of the 115 events JIP-3 defines. Check that the receiver decoded them
all:

```sh
curl -s localhost:19911/metrics | grep -E '^jip3_decode_errors|unknown_' ; echo "lines: $?"
```

No output (`lines: 1`): no decode errors, no unknown events.

## 6. A second client

```sh
python3 jip3/sender.py --port 19910 --count 3 --rate 200 --impl DemoClient --node-name d0
curl -s localhost:19911/metrics | grep '^jam_node_info'
```

```text
jam_node_info{net="jip3",run_id="jip3-adhoc",node="democlient-2e8352bc",client="democlient",client_version="0.1.0",...} 1
jam_node_info{net="jip3",run_id="jip3-adhoc",node="jip3-sender-cd16df6b",client="jip3-sender",client_version="0.1.0",...} 1
```

Two nodes, two clients, one set of `jam_*` names: a tiny mixed network. Stop the receiver
with Ctrl-C when you are done.

## Check yourself

1. Why is every series labelled `net="jip3"` and `run_id="jip3-adhoc"`?
2. Where did `democlient` come from, and why is it lower case?
3. The sender scripted a failed work-package and a duplicate one in every round. Which
   series show them, and with what `reason`?
4. In a real stack, what would make the receiver use rule **container** instead of
   **self**?

<details>
<summary>Answers</summary>

1. The receiver could not match the connection to a container (Docker lookup off) or to a
   registered target (none), so rule 4, *self*, applied: `net="jip3"`, and the run id
   defaults to `<net>-adhoc`.
2. From `--impl DemoClient`, the implementation name in the node information message. Rule
   *self* uses the implementation name in lower case as `client`, and
   `<implementation>-<first 4 bytes of the peer id>` as `node`.
3. `jam_wp_refused_total{reason="anchor"}` (the failed package, event 92: the receiver maps
   the failure text to the fixed reason vocabulary) and
   `jam_wp_refused_total{reason="duplicate"}` (event 93), 3 each for 3 rounds.
4. The TCP connection coming from a running container's IP address, with the receiver able
   to read Docker. In the stack, a containerised node that sends to `obs-jip3:9910` on its
   own network gets rule *container*, and the container's `org.abutlabs.obs.*` labels name
   it.

</details>
