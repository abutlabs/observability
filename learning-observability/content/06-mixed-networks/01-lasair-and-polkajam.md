# lasair and PolkaJam on one chain

A mixed network runs validators of different clients on one genesis. They must agree on
every block, every state root, every finalized head, while each is written independently.
It is the strongest test a client can face, and the hardest network to observe, because
the clients do not report the same way.

## The network

jamswap's `lasair-pj`: six validators, each holding only its own key.

| Nodes | Client | How it reports |
|---|---|---|
| `lm0`, `lm1`, `lm2` | lasair | its own `/metrics` (scraped), JIP-2 through a lasair reader beside each node, its container output |
| `pj3`, `pj4`, `pj5` | PolkaJam 0.1.29 | JIP-3 telemetry, its JIP-2 RPC, its container output |

PolkaJam serves no `/metrics`. Its only telemetry option is a JIP-3 push endpoint, so the
stack sees it through the JIP-3 receiver (events, and the metrics derived from them) and
the JIP-2 exporter (slots and block hashes). lasair is scraped directly, and each lasair
node has a reader, a small JIP-2 server labelled with `org.abutlabs.obs.jip2.node`, so
the JIP-2 exporter compares all six nodes. On top runs the DEX, with finality by GRANDPA
across both clients.

## What the platform dashboards show

On the **Network overview**, six rows and 2 clients. The *paths* column tells you how each
node is seen: 2 for a PolkaJam node (JIP-3 and JIP-2), and 2 for a lasair node that exports
the `jam_*` set (its metrics and JIP-2). An older lasair build shows 1: its metrics carry
only `lasair_*` names, so only JIP-2 describes it in `jam_*` terms. The *version* column comes from lasair's `jam_node_info` (on builds that export the
`jam_*` set) and from PolkaJam's JIP-3 node information message.

`./obs status` lists the run with its scraped jobs and its derived nodes. Here is the line
from the capture in lesson 1.3, taken just after a `lasair-pj` run stopped, when only the
JIP-3 series were still held:

```text
lasair-pj      lasair-pj-20260928T184439Z       jip3 nodes 3
```

On **Chain health**, the one-head check is the JIP-2 exporter comparing **block hashes**
across all six nodes at the common slot, whatever client each runs. This is the view no
single client can give you: lasair knows its own head, PolkaJam knows its own, and only a
collector that asks both can say they are the same block.

## What is fair to compare across clients

| Compare freely | Compare with care | Do not compare |
|---|---|---|
| best and finalized slot, finality lag | block import time (each client times its own import) | work-package stage times (different stages, lesson 2.1) |
| one head, finality conflicts (by hash) | refine time (lasair times each item; JIP-3's refined event carries per-item costs) | client-specific metrics |
| blocks authored per client | peers (definitions of "validator peer" can differ) | protocol labels (`CE-128` against `ce128`) |

When a comparison looks alarming, check first that both sides measure the same thing.

## What the soaks said

jamswap's soak results for 2026-09-28 tell the mixed network's story:

| Run | lasair | Result |
|---|---|---|
| `lasair-pj-20260928T051406Z` | 2.1.1, 10 min | PASS: 240 offered, 0 refused |
| `lasair-pj-20260928T091838Z` | 2.1.1, 1 hour | FAIL (load): 17 of 1,440 refused, cause unseen |
| `lasair-pj-20260928T102552Z` | 2.1.2, 1 hour | PASS: 0 refused, SLO 1.0000, p50 50 s / p99 523 s |

The failure on 2.1.1 had a cause nobody could see: the load generator logged only "Bad
Request", and its log went away with the network. That is itself an observability lesson.
Since then the soaks keep `loadgen.log` and `dex.log` in the run's folder, the load
generator prints the DEX's reason, and the stack's Loki keeps every container's output for
30 days.

In every run, one head held on every sample, finality had no conflict, and state parity
agreed on all six nodes: lasair and PolkaJam held byte-identical DEX state at the same
finalized block. And the same DEX load that the all-lasair network failed at the time
(track 5) passed here, because PolkaJam's co-guarantors refined fast enough.

## Why this matters for observing

A mixed network gives you something a single-client network cannot: **two independent
implementations watching each other**. Each client reports not only what it did, but, in
its telemetry, what it thought of its peers. The next lesson shows what that revealed.

Next: [What cross-client telemetry reveals](02-what-cross-client-telemetry-reveals.md)
