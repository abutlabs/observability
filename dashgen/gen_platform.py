#!/usr/bin/env python3
"""Generate the platform dashboards (dashboards/*.json, Grafana folder "platform").

    python3 dashgen/gen_platform.py          # write them
    python3 dashgen/gen_platform.py --check  # exit 1 if the JSON is stale

  obs-overview  Network overview: every node, its client and version, whether it keeps up
  obs-chain     Chain health: one chain, growing and finalizing, on every node
  obs-logs      Logs: every labelled container's output and every JIP-3 event
  obs-self      obs self-health: is the stack itself collecting?

The node dashboards use only the standard jam_* metrics (docs/proposals/
jam-node-metrics.md), so they work for any client, whichever path supplies a node's
series: its own /metrics, the JIP-2 exporter (source="jip2") or the JIP-3 receiver
(source="jip3"). Several paths can describe one node at once, so every per-node query
takes `max by (node, client)` across them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obsdash import (LOKI, PASS_FAIL, PROM, SEL, Layout, dashboard, logs, per_min,  # noqa: E402
                     right_axis, stat, table, text, ts, variables, write_all)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboards")

EPOCH_SLOTS = 12          # tiny's epoch: the one-epoch finality-stall bound
MAX_LAG_SLOTS = 3         # a best block this far behind the newest still counts as one head


def node(metric, extra=""):
    """One series per node, whichever paths report it."""
    return "max by (node, client) (%s{%s%s})" % (metric, SEL, extra)


BEST = node("jam_best_slot")
FINAL = node("jam_finalized_slot")
# finality lag, once a node has finalized past genesis (before, the "lag" is the slot)
FIN_LAG = "%s - (%s > 0)" % (BEST, FINAL)
SOURCES = ('label_replace(jam_best_slot{%s}, "source", "metrics", "source", "")' % SEL)


# ---- Network overview ---------------------------------------------------------------
def overview():
    L = Layout()
    L.add(text(
        "**Which nodes make up this net, what client runs each, and is every node keeping "
        "up?** One row per node, whatever its client. A node's figures come from its own "
        "`/metrics` if it serves the standard `jam_*` set, else from the JIP-2 exporter "
        "polling its RPC, else from the JIP-3 events it pushes; the *paths* column says how "
        "many of those describe it."), 24, 3)
    L.add(stat("Nodes reporting", "count(%s)" % BEST, "Nodes with a best block from any path.",
               instant=True), 4, 4)
    L.add(stat("Clients", "count(count by (client) (%s))" % BEST,
               "Distinct client implementations among them.", instant=True), 4, 4)
    L.add(stat("Newest best slot", "max(%s)" % BEST, "The highest best slot any node reports."), 4, 4)
    L.add(stat("Newest finalized slot", "max(%s)" % FINAL, "The highest finalized slot."), 4, 4)
    L.add(stat("Head spread · PASS ≤ %d slots" % MAX_LAG_SLOTS, "max(%s) - min(%s)" % (BEST, BEST),
               "Newest best slot minus the oldest: how far the slowest node is behind.",
               "<=", MAX_LAG_SLOTS), 4, 4)
    L.add(stat("Nodes stopped reporting (15 min) · PASS = 0",
               "count(max by (node) (last_over_time(jam_best_slot{%s}[15m]))) - (count(%s) or vector(0))"
               % (SEL, BEST),
               "Nodes that reported a best block in the last 15 minutes but report none now.",
               "==", 0), 4, 4)

    L.add(table("Nodes", [
        (BEST, "A"), (FINAL, "B"), (FIN_LAG, "C"),
        ("sum by (node, client) (max by (node, client, role) (jam_peers{%s}))" % SEL, "D"),
        # the version from whichever path knows it (JIP-2 cannot tell)
        ('max by (node, client, client_version, gp_version) (jam_node_info{%s,client_version!=""})' % SEL, "E"),
        ("count by (node, client) (group by (node, client, source) (%s))" % SOURCES, "F")],
        "Instant values per node. version and GP come from jam_node_info (the node's own "
        "metrics or its JIP-3 node information).",
        transformations=[
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value #E": True},
                "indexByName": {"node": 0, "client": 1, "client_version": 2, "gp_version": 3,
                                "Value #A": 4, "Value #B": 5, "Value #C": 6, "Value #D": 7,
                                "Value #F": 8},
                "renameByName": {"client_version": "version", "gp_version": "GP",
                                 "Value #A": "best slot", "Value #B": "finalized slot",
                                 "Value #C": "finality lag", "Value #D": "peers",
                                 "Value #F": "paths"}}}],
        overrides=[{"matcher": {"id": "byName", "options": "finality lag"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                {"color": "green", "value": None}, {"color": "red", "value": EPOCH_SLOTS + 1}]}}]}],
        sort=("node", False)), 24, 10)

    L.add(ts("Best slot per node", [("%s > 0" % BEST, "{{node}} · {{client}}")],
             "Each node's best slot. The lines climb together; one that flattens stopped "
             "importing.", decimals=0, min0=False), 12, 8)
    L.add(ts("Finalized slot per node", [("%s > 0" % FINAL, "{{node}} · {{client}}")],
             "Each node's latest finalized slot, from its first finality on.", decimals=0,
             min0=False), 12, 8)
    L.add(ts("Blocks authored per minute, by client",
             [("sum by (client) (60 * rate(jam_blocks_authored_total{%s}[5m]))" % SEL, "{{client}}")],
             "Blocks each client's nodes authored (jam_blocks_authored_total)."), 8, 8)
    L.add(ts("Block import time, by client",
             [("histogram_quantile(0.5, sum by (client, le) (rate(jam_block_import_seconds_bucket{%s}[5m])))"
               % SEL, "{{client}} p50"),
              ("histogram_quantile(0.99, sum by (client, le) (rate(jam_block_import_seconds_bucket{%s}[5m])))"
               % SEL, "{{client}} p99")],
             "From the start of an import to the end of its execution (jam_block_import_seconds).",
             unit="s"), 8, 8)
    L.add(ts("Peers per node", [("sum by (node, client) (max by (node, client, role) (jam_peers{%s}))" % SEL,
                                 "{{node}} · {{client}}")],
             "Connected JAMNP-S peers each node reports.", decimals=0), 8, 8)
    L.add(ts("Work-packages received per minute, by client",
             [("sum by (client) (60 * rate(jam_wp_received_total{%s}[5m]))" % SEL, "{{client}} received"),
              ("sum by (client) (60 * rate(jam_wp_refused_total{%s}[5m]))" % SEL, "{{client}} refused")],
             "Work-packages guarantors received, and those they refused or failed."), 8, 8)
    L.add(ts("Guarantor pipeline, p50 per stage",
             [("histogram_quantile(0.5, sum by (stage, le) (rate(jam_wp_stage_seconds_bucket{%s}[10m])))"
               % SEL, "{{stage}}")],
             "Time to reach each stage of the guarantor pipeline from the previous one "
             "(jam_wp_stage_seconds): receive, authorize, refine, report, guarantee, "
             "distribute.", unit="s", stack=True), 8, 8)
    L.add(ts("Refine time, by client",
             [("histogram_quantile(0.5, sum by (client, le) (rate(jam_refine_seconds_bucket{%s}[10m])))"
               % SEL, "{{client}} p50"),
              ("histogram_quantile(0.99, sum by (client, le) (rate(jam_refine_seconds_bucket{%s}[10m])))"
               % SEL, "{{client}} p99")],
             "Wall-clock refine time of one work-package (jam_refine_seconds).", unit="s"), 8, 8)
    L.add(ts("Which path reports each node",
             [("count by (source) (group by (node, source) (%s))" % SOURCES, "{{source}}")],
             "Nodes described by each ingestion path: metrics (the node's /metrics), jip2 "
             "(the JIP-2 exporter), jip3 (the JIP-3 receiver).", decimals=0), 12, 6)
    L.add(ts("Reorgs per 10 min",
             [("sum by (node, client) (increase(jam_reorgs_total{%s}[10m]))" % SEL, "{{node}} · {{client}}")],
             "Best-block changes to a block off the previous best chain (jam_reorgs_total).",
             decimals=0), 12, 6)
    return dashboard("obs-overview", "Network overview", L)


# ---- Chain health --------------------------------------------------------------------
def chain():
    L = Layout()
    L.add(text(
        "**Is the net one chain that keeps growing and finalizing, on every node?** Each "
        "line is one node: its best and finalized slots should climb together, finality lag "
        "should stay under one epoch (%d slots on tiny), and head agreement should read one "
        "head. Any client works: the standard `jam_*` series come from the node's own "
        "metrics, the JIP-2 exporter or the JIP-3 receiver." % EPOCH_SLOTS), 24, 3)
    one_head = ("min(jam_net_one_head{%s}) or on() ((max(%s) - min(%s)) <= bool %d)"
                % (SEL, BEST, BEST, MAX_LAG_SLOTS))
    moved = ("min((max_over_time((%s)[5m:15s]) - min_over_time((%s > 0)[5m:15s])) or (%s * 0))"
             % (FINAL, FINAL, FINAL))
    L.add(stat("Nodes stopped reporting (15 min) · PASS = 0",
               "count(max by (node) (last_over_time(jam_best_slot{%s}[15m]))) - (count(%s) or vector(0))"
               % (SEL, BEST),
               "Nodes that reported a best block in the last 15 minutes but report none now.",
               "==", 0), 5, 4)
    L.add(stat("One head · PASS = all nodes on one block (≤ %d slots behind)" % MAX_LAG_SLOTS, one_head,
               "jam_net_one_head (the JIP-2 exporter compares block hashes): every node up, one "
               "block at the common slot, none more than %d slots behind. Without it, the "
               "spread of best slots stands in." % MAX_LAG_SLOTS, ">=", 1, mappings=PASS_FAIL), 5, 4)
    L.add(stat("Finality lag, worst node · PASS ≤ %d slots" % EPOCH_SLOTS, "max(%s)" % FIN_LAG,
               "Best slot minus finalized slot on the node that lags most.", "<=", EPOCH_SLOTS), 5, 4)
    L.add(stat("Finalized in 5 min, slowest node · PASS > 0 slots", moved,
               "How far the slowest node's finalized block moved in the last 5 minutes. "
               "0 = finality stalled on some node.", ">", 0), 5, 4)
    L.add(stat("Finality conflicts · PASS = 0", "max(jam_finality_conflicts_total{%s})" % SEL,
               "Finalized slots seen with two different hashes (a safety failure; needs a "
               "collector that compares hashes, such as the JIP-2 exporter).", "==", 0), 4, 4)

    L.newline()
    L.add(ts("Best slot per node", [("%s > 0" % BEST, "{{node}} · {{client}}")],
             "Slot of each node's best block. Lines climb together; one that flattens stopped "
             "importing.", decimals=0, min0=False), 12, 8)
    L.add(ts("Finalized slot per node", [("%s > 0" % FINAL, "{{node}} · {{client}}")],
             "Slot of each node's finalized block (from its first finality on). Lines climb in "
             "steps; one that flattens stopped finalizing.", decimals=0, min0=False), 12, 8)
    L.add(ts("Finality lag per node (slots) · PASS ≤ %d" % EPOCH_SLOTS, [(FIN_LAG, "{{node}} · {{client}}")],
             "Best slot minus finalized slot per node; the dashed line is one epoch.",
             threshold=EPOCH_SLOTS, decimals=0), 12, 8)
    L.add(ts("Head agreement",
             [("min(jam_net_heads{%s})" % SEL, "distinct heads at the common slot (1 = one head)"),
              ("min(jam_net_final_heads{%s})" % SEL, "distinct finalized blocks (1 = agree)"),
              ("max(%s) - min(%s)" % (BEST, BEST), "best-slot spread")],
             "The JIP-2 exporter compares block hashes at the common slot across every node; "
             "the spread of best slots works from any path.", threshold=MAX_LAG_SLOTS,
             decimals=0), 12, 8)
    L.add(ts("Height per node (best solid, finalized dashed)",
             [(node("jam_best_height"), "{{node}} best"), (node("jam_finalized_height"), "{{node}} finalized")],
             "Blocks since genesis, for nodes that report heights in their own metrics (JIP-2 "
             "and JIP-3 carry slots only).", decimals=0,
             overrides=[{"matcher": {"id": "byRegexp", "options": ".* finalized$"},
                         "properties": [{"id": "custom.lineStyle",
                                         "value": {"fill": "dash", "dash": [6, 4]}}]}]), 12, 8)
    L.add(ts("Peers per node",
             [("sum by (node, client) (max by (node, client, role) (jam_peers{%s}))" % SEL, "{{node}} · {{client}}"),
              ("sum(max by (node) (jam_node_up{%s}))" % SEL, "nodes answering JIP-2")],
             "Connected peers each node reports, and how many nodes answer the JIP-2 exporter.",
             decimals=0), 12, 8)
    return dashboard("obs-chain", "Chain health", L)


# ---- Logs ----------------------------------------------------------------------------
def logs_dashboard():
    def var(name, query, sort=1, multi=False):
        return {"name": name, "label": name, "type": "query", "datasource": LOKI,
                "query": query, "definition": query, "refresh": 2, "sort": sort,
                "includeAll": multi, "multi": multi, "current": {}, "options": [], "hide": 0,
                **({"allValue": ".+"} if multi else {})}
    variables_ = [var("net", "label_values(net)"),
                  var("run_id", 'label_values({net="$net"}, run_id)', 2),
                  var("node", 'label_values({net="$net", run_id="$run_id"}, node)', 1, True),
                  {"name": "search", "label": "search (regex)", "type": "textbox", "query": "",
                   "current": {"text": "", "value": ""}, "hide": 0}]
    sel = 'net="$net", run_id="$run_id", node=~"$node"'
    L = Layout()
    L.add(text(
        "**What did the nodes say?** Every labelled container's output (`source=\"docker\"`) "
        "and every JIP-3 event a node pushed (`source=\"jip3\"`, one JSON object per event), "
        "for the run and nodes picked above. Narrow it with *search* (a regex)."), 24, 2)
    L.add(ts("Log lines per node",
             [('sum by (node) (count_over_time({%s} |~ "$search" [$__auto]))' % sel, "{{node}}")],
             "Lines per interval, by node.", stack=True, ds=LOKI), 12, 7)
    L.add(ts("JIP-3 events by type",
             [('sum by (event) (count_over_time({%s, source="jip3"} | json event="event" [$__auto]))' % sel,
               "{{event}}")],
             "Telemetry events the nodes pushed, by event type.", stack=True, ds=LOKI), 12, 7)
    L.add(logs("Lines", '{%s} |~ "$search"' % sel, "Newest first. Expand a line for its labels."), 24, 18)
    return dashboard("obs-logs", "Logs", L, variables_=variables_)


# ---- obs self-health -----------------------------------------------------------------
def self_health():
    L = Layout()
    L.add(text(
        "**Is the obs stack itself collecting?** The stack's services answer, Alloy reaches "
        "its targets and writes to Prometheus and Loki without loss, netjoin keeps the "
        "collectors on the nets' networks, and the disk stays under its caps."), 24, 2)
    L.add(stat("obs services down · PASS = 0", 'sum(1 - up{job=~"obs-.*"})',
               "Stack services Alloy scrapes that do not answer.", "==", 0), 4, 4)
    L.add(stat("Scrape targets down (info)", 'sum(1 - up{job!~"obs-.*"}) or vector(0)',
               "Net targets that do not answer: a stopped load generator counts too."), 4, 4)
    L.add(stat("Samples failed to write (5 min) · PASS = 0",
               "sum(increase(prometheus_remote_storage_samples_failed_total[5m])) or vector(0)",
               "Samples Alloy could not remote-write to Prometheus.", "==", 0), 4, 4)
    L.add(stat("Log entries dropped (5 min) · PASS = 0",
               "(sum(increase(loki_write_dropped_entries_total[5m])) or vector(0)) + "
               "(sum(increase(jip3_loki_dropped_total[5m])) or vector(0))",
               "Log entries Alloy or the JIP-3 receiver dropped instead of writing to Loki.",
               "==", 0), 4, 4)
    L.add(stat("Prometheus storage (info)",
               "sum(prometheus_tsdb_storage_blocks_bytes) + sum(prometheus_tsdb_wal_storage_size_bytes)",
               "Blocks plus write-ahead log, against the retention size cap.", unit="bytes",
               decimals=1), 4, 4)
    L.add(stat("netjoin errors (1 h) · PASS = 0",
               "sum(increase(obs_netjoin_errors_total[1h])) or vector(0)",
               "Docker API calls netjoin could not make (joining or leaving a net's network).",
               "==", 0), 4, 4)

    L.newline()
    L.add(ts("Targets up, by job", [("sum by (job) (up)", "{{job}}")],
             "Every target Alloy scrapes, by job.", decimals=0, stack=True), 12, 8)
    L.add(ts("Scrape duration, worst target per job", [("max by (job) (scrape_duration_seconds)", "{{job}}")],
             "Slow scrapes delay every panel.", unit="s"), 12, 8)
    L.add(ts("Samples written per second",
             [("sum(rate(prometheus_tsdb_head_samples_appended_total[5m]))", "Prometheus appended"),
              ("sum(rate(prometheus_remote_storage_samples_failed_total[5m]))", "Alloy failed to send")],
             "Ingestion rate, and remote-write failures (should be 0)."), 8, 8)
    L.add(ts("Log lines per second",
             [("sum(rate(loki_distributor_lines_received_total[5m]))", "Loki received"),
              ("sum(rate(loki_write_dropped_entries_total[5m]))", "Alloy dropped"),
              ("sum(rate(jip3_loki_dropped_total[5m]))", "JIP-3 receiver dropped")],
             "Log ingestion, and entries dropped on the way (should be 0)."), 8, 8)
    L.add(ts("Storage",
             [("sum(prometheus_tsdb_storage_blocks_bytes) + sum(prometheus_tsdb_wal_storage_size_bytes)",
               "Prometheus"),
              ("max(prometheus_tsdb_retention_limit_bytes)", "Prometheus size cap")],
             "Prometheus drops its oldest blocks at the cap (OBS_RETENTION_SIZE). Loki keeps "
             "OBS_LOG_RETENTION of logs.", unit="bytes"), 8, 8)
    L.add(ts("JIP-3 receiver",
             [("sum(jip3_connected)", "connected nodes"),
              ("sum(rate(jip3_events_total[5m]))", "events per second"),
              ("sum(rate(jip3_decode_errors_total[5m]))", "decode errors per second")],
             "Telemetry connections and events."), 8, 8)
    L.add(ts("JIP-2 exporter",
             [('count(jam_node_up{source="jip2"})', "nodes polled"),
              ('sum(jam_node_up{source="jip2"})', "nodes answering"),
              ("sum(rate(jip2_poll_errors_total[5m]))", "poll errors per second")],
             "Nodes whose JIP-2 RPC is polled."), 8, 8)
    L.add(ts("netjoin",
             [("sum by (service) (obs_netjoin_networks)", "{{service}}: networks"),
              ("sum by (action) (increase(obs_netjoin_actions_total[10m]))", "{{action}}s per 10 min")],
             "Nets' networks each collector is attached to, and joins and leaves.", decimals=0), 8, 8)
    L.add(ts("Pushgateway groups", [("count(push_time_seconds)", "groups")],
             "Result groups batch jobs pushed (one per job and run).", decimals=0), 12, 6)
    L.add(ts("Prometheus series", [("sum(prometheus_tsdb_head_series)", "head series")],
             "Active series in Prometheus's head block.", decimals=0), 12, 6)
    return dashboard("obs-self", "obs self-health", L, variables_=[], refresh="30s")


def render_all():
    return {"obs-overview.json": overview(), "obs-chain.json": chain(),
            "obs-logs.json": logs_dashboard(), "obs-self.json": self_health()}


if __name__ == "__main__":
    write_all(OUT, render_all(), sys.argv)
