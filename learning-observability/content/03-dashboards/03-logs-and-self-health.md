# Logs and obs self-health

## Logs (`obs-logs`)

**What did the nodes say?** Every labelled container's output (`source="docker"`), every
JIP-3 event a node pushed (`source="jip3"`, one JSON object per event), and the
containers' lifecycle (`source="lifecycle"`: start, stop, die with its exit code), for the
run and nodes you pick.

### Its controls

This dashboard reads its variables from Loki, not Prometheus:

- **net** and **run_id**, as everywhere;
- **node**: one, several or All;
- **search (regex)**: a regular expression every line must match. Empty shows everything.

### Its panels

| Panel | What it shows |
|---|---|
| Log lines per node | lines per interval, stacked by node. A node that goes silent, or suddenly floods, stands out. |
| JIP-3 events by type | telemetry events per interval, stacked by event name (`status`, `best_block_changed`, `block_announced`, ...) |
| Lines | the lines themselves, newest first; expand one for its labels |

The *Lines* panel runs `{net="$net", run_id="$run_id", node=~"$node"} |~ "$search"`. The
search box is the fastest way to follow one thing through every node: type a package hash
prefix, a slot number, or a word like `EXPIRED`, and you get every line that mentions it,
from every node, in time order.

### Good searches

| Search | Finds |
|---|---|
| `EXPIRED` | lasair work-packages that expired (with their reason) |
| `refused` | anything refused: packages, shares, guarantees |
| `peer_misbehaved` | JIP-3 events in which a node reports that a peer misbehaved (track 6) |
| `(?i)error\|panic` | errors, any case |
| `0x3f1c9a02` | every line naming that package (use the prefix the logs print) |

For anything the search box cannot do (parsing JSON fields, counting), go to Grafana's
**Explore** with the Loki datasource and write LogQL (lesson 3.8).

### Which logs are there

A container's output is shipped when it carries `org.abutlabs.obs.logs=true`, or
`org.abutlabs.obs.scrape=true` without `logs=false`. Loki keeps 30 days. Processes on
your machine registered with `./obs register` are scraped for metrics, but their logs are
not collected: only containers' are.

## obs self-health (`obs-self`)

**Is the obs stack itself collecting?** Open it when another dashboard looks empty or
wrong: before you suspect the network, rule out the thing watching it. It has no net or
run selector; it is about the stack.

### The top row

| Stat | Passes when | Means |
|---|---|---|
| obs services down | 0 | every stack service Alloy scrapes answers |
| Scrape targets down (info) | (none) | net targets that do not answer; a stopped load generator counts too |
| Samples failed to write (5 min) | 0 | Alloy could not remote-write to Prometheus |
| Log entries dropped (5 min) | 0 | Alloy, the JIP-3 receiver or the lifecycle service dropped log lines instead of writing them to Loki |
| Prometheus storage (info) | (none) | blocks plus write-ahead log, against the 5 GB cap |
| netjoin errors (1 h) | 0 | Docker calls netjoin could not make |

### The panels

- **Targets up, by job** and **Scrape duration, worst target per job**: a slow scrape delays
  every panel that reads it.
- **Samples written per second** and **Log lines per second**: ingestion, and anything lost
  on the way (should be 0).
- **Storage**: Prometheus against its size cap.
- **JIP-3 receiver**: connected nodes, events per second, decode errors per second.
- **JIP-2 exporter**: nodes polled, nodes answering, poll errors per second.
- **netjoin**: networks each collector is attached to, joins and leaves.
- **lifecycle**: containers tracked, runs exported and Docker API errors, from the
  lifecycle service.
- **Pushgateway groups**: result groups batch jobs pushed (one per job and run).
- **Prometheus series**: active series; a sudden jump usually means a label with too many
  values.

### Two more places to look

- **Alloy's own UI**, http://localhost:12345: every component, every discovered target,
  its labels, its last scrape and its last error. If a container is not being scraped,
  this says why (not discovered, wrong port, scrape failing).
- **`./obs status -v`**: every target in one screen, up or DOWN.

Next: [The lasair dashboards](04-lasair-dashboards.md)
