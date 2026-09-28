# Labs and exercises

Hands-on work for the course. Each lab says what it needs. Lab 2 and the two exercise sets
need no Docker at all.

| | What you do | Needs | Goes with |
|---|---|---|---|
| [Lab 1: Start the stack](lab-1-start-the-stack.md) | start the stack, check every service, find your way around Grafana and Alloy | Docker | track 1 |
| [Lab 2: JIP-3 without Docker](lab-2-jip3-without-docker.md) | run the JIP-3 receiver as a plain process, feed it from the synthetic sender, read what it derives | Python only | lessons 2.1 and 2.3 |
| [Lab 3: Register a process](lab-3-register-a-process.md) | serve a tiny fake `/metrics`, register it, watch it appear on the dashboards | Docker, the stack | lessons 1.4 and 2.2 |
| [Lab 4: Replay the case study](lab-4-replay-the-case-study.md) | answer the case study's questions yourself, with PromQL against the recorded runs | the machine that recorded them | track 5 |
| [PromQL exercises](promql.md) | read and write queries; answers included | nothing, or Prometheus to check | lesson 3.7 |
| [LogQL exercises](logql.md) | read and write log queries; answers included | nothing, or Loki to check | lesson 3.8 |

## How the answers work

Every exercise has its answer in a collapsed block under it. Try first, then open it:

<details>
<summary>Answer</summary>

Like this. On GitHub and on the course site, click the line above to open or close it.

</details>

Where an answer is a number from a real run, it is the number that query returned on
2026-09-28, and the exercise says which run.

## A note on outputs

Output shown in a lab was captured by running the command, unless the lab says it shows
the *expected* output. Parts of labs 1 and 3 need a stack and a scraped target, and their
expected output there is worked out from the code (`obs`, `alloy/config.alloy`), not
captured: if yours differs, the difference is worth understanding, and worth reporting.
