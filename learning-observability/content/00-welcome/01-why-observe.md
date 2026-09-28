# Why a JAM network needs observability

A JAM network is a set of processes that each make decisions you cannot see from the
outside. Every validator picks what it thinks is the best block, decides when a block is
final, refines work-packages, refuses some of them, signs guarantees and assurances. Next
to the validators run the things that use the chain: builders, services, a DEX, a load
generator. When something goes wrong, the question is always the same: *which process
decided what, and when?*

Observability is the ability to answer that question from the outside, from what the
processes emit, without stopping them or adding print statements. A system is observable
when you can ask it a question you did not plan for and get an answer.

## Three kinds of signal

| Signal | What it answers | Where it lives in this stack |
|---|---|---|
| **Metrics**: numbers sampled over time | How many, how fast, how far behind? Is it getting worse? | Prometheus |
| **Logs**: lines a process writes | What exactly happened to *this* package, block or peer? | Loki |
| **Telemetry events** (JIP-3): a structured stream a node pushes | Both: each event is kept as a log line, and counted and timed as metrics | The JIP-3 receiver, then Prometheus and Loki |

Metrics are cheap and good at trends: a line on a graph that climbs, flattens or spikes.
Logs are detailed and good at the individual case: the one package that expired, with its
hash and the reason. You usually start with a metric that looks wrong and end in the logs
that explain it.

(Traces, the fourth classic signal, are not collected yet. The stack's README says where
they would go.)

## Why JAM needs it more than most systems

**The chain can be healthy while the thing on top of it fails.** In the case study of this
course, a six-validator network ran a decentralised exchange for an hour. Every consensus
check passed: all nodes agreed on one head at every one of 630 samples, 659 finalized
slots were checked by hash, and every node held byte-identical exchange state. Yet the exchange refused
519 of 1,440 orders. Only by watching the whole system, from the order book down to how
long one validator took to refine one work-package, did the cause show.

**Time windows turn slowness into failure.** A work-package must be reported while its
anchor block is still among the last 8 blocks: 48 seconds at 6-second slots. A guarantor
that is correct but slow does not produce a wrong answer; it produces no answer in time,
and the package expires. You cannot see that from a pass/fail test. You see it from
timings.

**Networks mix clients.** A JAM network may run lasair, PolkaJam and other
implementations side by side. Each client reports in its own way, or not at all. To
compare them you need a common vocabulary (the `jam_*` metrics), a common label model
(`net`, `run_id`, `node`, `client`), and collectors for the clients that offer only the
JAM standards (JIP-3 telemetry, JIP-2 RPC). This stack provides all three.

**Problems appear late.** Memory that grows by a few kilobytes per block, a queue that
fills after twenty minutes of load, a round size that only reaches its cap under
sustained traffic: none of these show in a five-minute test. They show in a *soak*, a
long run under steady load, and only if something is recording the whole time.

## What this stack is

One Docker Compose stack on your machine that any JAM network on the same machine reports
into: Grafana, Prometheus, Loki, Grafana Alloy, a Pushgateway, and two small JAM
collectors (a JIP-3 receiver and a JIP-2 exporter). A network opts in with labels on its
containers; nothing is registered by hand. Projects such as lasair (a JAM client) and
jamswap (a DEX on JAM) keep their own dashboards in their own repositories and plug them
in as Grafana folders.

By the end of this course you will be able to start the stack, get any client's nodes
into it, read every dashboard, run a soak and read its verdict, and follow a failure from
its first symptom to its cause.

Next: [The words you need](02-the-words-you-need.md)
