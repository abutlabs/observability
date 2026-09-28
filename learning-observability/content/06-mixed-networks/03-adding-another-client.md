# Adding another client

lasair and PolkaJam are two of many JAM implementations. jamswap already defines networks
with others: `pj-pbnjam` (five PolkaJam, one pbnjam), `pj-javajam` (three PolkaJam, three
JavaJAM), `nolasair` (two each of PolkaJam, JavaJAM and pbnjam); `./dex nets` lists them.
This lesson is the checklist for bringing a third client into a mixed network and trusting
what the dashboards then say.

## Get it in (recap of lesson 2.5)

1. Run it in a container on the network's Docker network.
2. Label it: `org.abutlabs.obs.net`, `.run_id`, `.client` (its name in lower case), `.logs`.
3. Choose its paths: `/metrics` with `jam_*` names if it has them (`scrape`, `port`),
   JIP-3 if it implements it (`--telemetry obs-jip3:9910`), JIP-2 if it serves the RPC
   (`org.abutlabs.obs.jip2`). **On a mixed network, add JIP-2 whenever the client serves
   it**, even if it has richer paths: it is what puts the node into the hash comparison.
4. `./obs status`, then the Network overview.

## Verify it, before trusting a single panel

| Check | Where | Expect |
|---|---|---|
| Every node is there | Network overview, *Nodes reporting* | the number you started |
| The client is named | *Clients*, and the `client` column | one more client, spelled consistently |
| Each node is seen the way you meant | the *paths* column | 1, 2 or 3, matching the paths you set up |
| Identity is filled | *version*, *GP* | filled for nodes with `/metrics` or JIP-3; empty for JIP-2-only nodes (JIP-2 cannot say) |
| JIP-3 matched its container | `net` of its series | your net, not `jip3`; a name like `<client>-1a2b3c4d` means rule 4 applied (lesson 2.3) |
| It is in the hash comparison | Chain health, *One head* and *Head agreement* | `jam_net_one_head` = 1 with the new nodes up; `jam_net_nodes` counts them |
| Its logs arrive | Logs, pick the node | its container output |

## What you can learn about it at once

With nothing more than the steps above, the platform dashboards answer, for the new client
as for the others:

- does it keep up (best slot, head spread)?
- does it finalize with the others (finalized slot, finality lag)?
- does it hold the same blocks (hash agreement)?
- does it author its share (blocks authored, by client)?

If it pushes JIP-3, also: import time, the guarantor pipeline, refine time and gas, and
every event it reports, including its complaints about peers (`peer_misbehaved`).

## What you cannot learn without its own metrics

Anything inside it: queues, memory, why a package was refused, where refine time goes. For
that the client needs its own metrics and dashboards, as lasair has. Until then, a problem
inside it shows on the platform dashboards only as a symptom (a node behind, a finality
lag, a missing guarantee) and in its logs.

## Watch both directions

When a new client joins, run the lesson 6.2 query in both directions:

```logql
{net="<net>", source="jip3"} | json | event="peer_misbehaved"
```

grouped by the reporting node's client. Complaints *from* the new client about the others,
and *about* the new client from the others, are the fastest map of where the
implementations disagree. Treat each as a lead to check against the specification, as in
lesson 6.2.

## A last word on fairness

The dashboards group by `client` so that you can compare implementations. Keep the
comparisons honest (lesson 6.1): slots, finality and hashes mean the same for every client;
timings and client-specific counters mean what each client decided they mean. When one
client looks slow or noisy, first check that you are reading the same measurement on both
sides.

You have reached the end of the lessons. The labs put them to work:
[Labs and exercises](../../exercises/README.md).
