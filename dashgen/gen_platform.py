#!/usr/bin/env python3
"""Generate the platform dashboards (dashboards/*.json, Grafana folder "platform").

    python3 dashgen/gen_platform.py          # write them
    python3 dashgen/gen_platform.py --check  # exit 1 if the JSON is stale

  obs-overview  Network overview: the run, every node, its client and version, whether
                it keeps up, and the net's runs
  obs-chain     Chain health: one chain, growing and finalizing, on every node
  obs-node      Node detail: one node's chain, work, resources, lifecycle and logs; why
                it stopped, and its last words
  obs-logs      Logs: every labelled container's output and every JIP-3 event
  obs-self      obs self-health: is the stack itself collecting?

The node dashboards use only the standard jam_* metrics (docs/
metrics.md), so they work for any client, whichever path supplies a node's
series: its own /metrics, the JIP-2 exporter (source="jip2") or the JIP-3 receiver
(source="jip3"). Several paths can describe one node at once, so every per-node query
takes `max by (node, client)` across them. Node detail also reads the lifecycle service's
obs_container_* series and, for lasair nodes, lasair_* where no jam_* name exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obsdash import (ABNORMAL, GREEN, GREY, LOKI, PASS_FAIL, PROM, RED, SEL, STOP_GRACE, Layout,  # noqa: E402
                     dashboard, last_advanced, logs, per_min, right_axis, run_variables, stat,
                     stopped_early, table, text, ts, variables, write_all)

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
LS = last_advanced()      # per node: when its best slot last moved in the range, up to $at
DIED = "obs_container_stopped_timestamp_seconds"    # the lifecycle service's stop record
# the run's JAM nodes (not its DEX, load generator or init containers)
JAM_NODES = "group by (node) (last_over_time(jam_best_slot{%s}[$__range] @ $at))" % SEL

# a node's state at $at: 0 stopped while the rest of the net went on, 1 advancing (at the
# run's end, for a finished run), 2 the whole net had stopped advancing
STATE = ("(%s >= bool on() group_left() (max(%s) - %d)) * on() group_left() (1 + (max(%s) < bool ($at - %d)))"
         % (LS, LS, STOP_GRACE, LS, STOP_GRACE))
NEUTRAL = {"mode": "absolute", "steps": [{"color": "#8e8e8e", "value": None}]}
STATE_MAP = [{"type": "value", "options": {
    "0": {"text": "stopped during the run", "color": RED, "index": 0},
    "1": {"text": "advancing", "color": GREEN, "index": 1},
    "2": {"text": "net stalled", "color": "#8e8e8e", "index": 2}}}]


def stopped_tile(desc_extra=""):
    return stat("Nodes stopped during the run · PASS = 0",
                "count(%s) or (max(%s) * 0)" % (stopped_early(LS), LS),
                "Nodes whose best slot stopped advancing more than %d s before the rest of the net's "
                "did, within the range (up to the run's end). A net shut down at the end of its "
                "run stops all together and is not counted.%s" % (STOP_GRACE, desc_extra),
                "==", 0, instant=True)


def run_tile():
    """The run: active, ended (grey, with its end time), or its net stalled while the
    run is active (red). Evaluated at the range's end: what the record said then."""
    end = "max(obs_run_end_timestamp_seconds{%s})" % SEL
    stalled = "(max(%s) < ($at - %d))" % (LS, STOP_GRACE)
    targets = [
        ("A", "%s * 1000" % end, "time:[run ended ]HH:mm:ss[ — net shut down]", GREY),
        ("B", "max(obs_run_start_timestamp_seconds{%s}) * 1000 unless on() %s unless on() %s" % (SEL, end, stalled),
         "time:[run active since ]HH:mm:ss", "#5794F2"),
        ("C", "%s * 1000 unless on() %s and on() max(obs_run_start_timestamp_seconds{%s})" % (stalled, end, SEL),
         "time:[net stalled since ]HH:mm:ss[ — run not ended]", RED)]
    p = stat("Run", targets[0][1], "The run's state from its record (obs begin, obs end): active, "
             "ended normally (grey) or active with no node advancing for over %d s (red). A net run "
             "without the obs CLI has no record. Click for the whole run." % STOP_GRACE, instant=True)
    p["targets"] = [{"expr": e, "refId": r, "range": False, "instant": True} for r, e, _, _ in targets]
    p["fieldConfig"]["defaults"]["noValue"] = "no run record"
    p["fieldConfig"]["defaults"]["links"] = [{
        "title": "Show the whole run",
        "url": "/d/${__dashboard.uid}?orgId=1&var-net=${net}&var-run_id=${run_id}&from=${run_from}&to=${run_to}"}]
    p["fieldConfig"]["overrides"] = [
        {"matcher": {"id": "byFrameRefID", "options": r},
         "properties": [{"id": "unit", "value": unit},
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}
        for r, _, unit, color in targets]
    return p


# ---- Network overview ---------------------------------------------------------------
def overview():
    L = Layout()
    L.add(text(
        "**Which nodes make up this net, what client runs each, and is every node keeping "
        "up?** One row per node, whatever its client. A node's figures come from its own "
        "`/metrics` if it serves the standard `jam_*` set, else from the JIP-2 exporter "
        "polling its RPC, else from the JIP-3 events it pushes; the *paths* column says how "
        "many of those describe it."), 24, 3)
    L.add(run_tile(), 6, 4)
    L.add(stat("Nodes advancing", "count(%s >= on() group_left() (max(%s) - %d))" % (LS, LS, STOP_GRACE),
               "Nodes whose best slot moved within %d s of the net's newest move, at the range's "
               "end or, for a finished run, at its end." % STOP_GRACE, instant=True), 3, 4)
    L.add(stat("Clients", "count(count by (client) (%s))" % BEST,
               "Distinct client implementations among them."), 3, 4)
    L.add(stat("Newest best slot", "max(%s)" % BEST, "The highest best slot any node reports."), 3, 4)
    L.add(stat("Newest finalized slot", "max(%s)" % FINAL, "The highest finalized slot."), 3, 4)
    L.add(stat("Head spread · PASS ≤ %d slots" % MAX_LAG_SLOTS, "max(%s) - min(%s)" % (BEST, BEST),
               "Newest best slot minus the oldest: how far the slowest node is behind.",
               "<=", MAX_LAG_SLOTS), 3, 4)
    L.add(stopped_tile(), 3, 4)

    L.add(table("Nodes", [
        (BEST, "A"), (FINAL, "B"), (FIN_LAG, "C"),
        ("sum by (node, client) (max by (node, client, role) (jam_peers{%s}))" % SEL, "D"),
        # the version from whichever path knows it (JIP-2 cannot tell)
        ('max by (node, client, client_version, gp_version) (jam_node_info{%s,client_version!=""})' % SEL, "E"),
        ("count by (node, client) (group by (node, client, source) (%s))" % SOURCES, "F"),
        (STATE, "G"), ("%s * 1000" % LS, "H"),
        ("max by (node, client, reason) (last_over_time(%s{%s}[$__range] @ $at)) * 1000 and on(node) %s"
         % (DIED, SEL, JAM_NODES), "I"),
        ("max by (node, client) (last_over_time(obs_container_last_exit_code{%s}[$__range] @ $at)) and on(node) %s"
         % (SEL, JAM_NODES), "J"),
        ("(%s - 300) * 1000" % LS, "K"), ("(%s + 60) * 1000" % LS, "L")],
        "One row per node, at the range's end or, for a finished run, at its end. state: "
        "advancing, stopped during the run (its best slot stopped over %d s before the rest "
        "of the net's), or net stalled. last advanced: when its best slot last moved (to "
        "within a minute). stopped and why: the container's last stop in the range (the lifecycle "
        "service). Click a node for Node detail, or for its last minutes of logs." % STOP_GRACE,
        transformations=[
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value #E": True},
                "indexByName": {"node": 0, "client": 1, "Value #G": 2, "client_version": 3, "gp_version": 4,
                                "Value #A": 5, "Value #B": 6, "Value #C": 7, "Value #D": 8,
                                "Value #F": 9, "Value #H": 10, "Value #I": 11, "reason": 12,
                                "Value #J": 13, "Value #K": 14, "Value #L": 15},
                "renameByName": {"client_version": "version", "gp_version": "GP", "Value #G": "state",
                                 "Value #A": "best slot", "Value #B": "finalized slot",
                                 "Value #C": "finality lag", "Value #D": "peers",
                                 "Value #F": "paths", "Value #H": "last advanced", "Value #I": "stopped",
                                 "reason": "why", "Value #J": "exit code", "Value #K": "logs_from",
                                 "Value #L": "logs_to"}}}],
        overrides=[{"matcher": {"id": "byName", "options": "finality lag"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                {"color": "green", "value": None}, {"color": "red", "value": EPOCH_SLOTS + 1}]}}]},
            {"matcher": {"id": "byName", "options": "state"}, "properties": [
                {"id": "mappings", "value": STATE_MAP},
                {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
            {"matcher": {"id": "byRegexp", "options": "^(last advanced|stopped)$"}, "properties": [
                {"id": "unit", "value": "dateTimeAsLocalNoDateIfToday"}]},
            {"matcher": {"id": "byRegexp", "options": "^logs_(from|to)$"}, "properties": [
                {"id": "custom.hidden", "value": True}]}],
        sort=("node", False), node_field="node",
        logs_range=("${__data.fields.logs_from}", "${__data.fields.logs_to}")), 24, 10)

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
    L.add(runs_table(), 24, 7)
    return dashboard("obs-overview", "Network overview", L)


def runs_table():
    """Every recorded run of the net (the lifecycle service exports the last 31 days')."""
    net = 'net="$net"'
    return table("Runs of this net, newest first", [
        ("max by (run_id, kind, link_from, link_to) (obs_run_info{%s})" % net, "A"),
        ("max by (run_id) (obs_run_start_timestamp_seconds{%s}) * 1000" % net, "B"),
        ("max by (run_id) (obs_run_end_timestamp_seconds{%s}) * 1000" % net, "C"),
        ("max by (run_id) (obs_run_end_timestamp_seconds{%s} - on(run_id) obs_run_start_timestamp_seconds{%s})"
         % (net, net), "D")],
        "The net's recorded runs (obs begin, obs end). Click a run id to open it here over its "
        "whole time range.",
        transformations=[
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value #A": True},
                "indexByName": {"run_id": 0, "kind": 1, "Value #B": 2, "Value #C": 3, "Value #D": 4},
                "renameByName": {"Value #B": "started", "Value #C": "ended", "Value #D": "duration"}}}],
        overrides=[
            {"matcher": {"id": "byRegexp", "options": "^(started|ended)$"},
             "properties": [{"id": "unit", "value": "dateTimeAsIso"}]},
            {"matcher": {"id": "byName", "options": "duration"}, "properties": [{"id": "unit", "value": "s"}]},
            {"matcher": {"id": "byRegexp", "options": "^link_(from|to)$"},
             "properties": [{"id": "custom.hidden", "value": True}]},
            {"matcher": {"id": "byName", "options": "run_id"}, "properties": [{"id": "links", "value": [{
                "title": "Open this run over its whole time range",
                "url": "/d/obs-overview?orgId=1&var-net=${net}&var-run_id=${__value.raw}"
                       "&from=${__data.fields.link_from}&to=${__data.fields.link_to}"}]}]}],
        sort=("started", True))


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
    L.add(stopped_tile(" Node detail (click a node) says why."), 5, 4)
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


# ---- Node detail ---------------------------------------------------------------------
NSEL = SEL + ',node="$node"'
NLOKI = '{net="$net", run_id="$run_id", node="$node"'
WARN_RE = r'(?i)\\b(warn|warning)\\b|"level":"warn'
ERR_RE = r'(?i)\\b(error|err|panic|fatal|crit)\\b|"level":"error'


def either(jam, lasair):
    """The standard jam_* expression, else lasair's own name for it (lasair images that
    predate the jam_* set)."""
    return "(%s) or (%s)" % (jam, lasair)


def node_detail():
    node_var = {"name": "node", "label": "node", "type": "query", "datasource": PROM,
                "query": 'label_values({__name__=~"jam_best_slot|up|obs_container_running",%s}, node)' % SEL,
                "definition": 'label_values({__name__=~"jam_best_slot|up|obs_container_running",%s}, node)' % SEL,
                "refresh": 2, "sort": 1, "includeAll": False, "multi": False, "current": {}, "options": [],
                "hide": 0}
    vs = variables()
    vs = vs[:2] + [node_var] + vs[2:]
    ls_node = last_advanced(sel=NSEL)
    L = Layout()
    L.add(text(
        "**What happened to this node?** Its state and, if it stopped, why (the container's "
        "lifecycle: teardown, killed, crashed with an exit code, OOM-killed, or running but "
        "stalled), its last words, then its chain, work-packages, resources and every log line. "
        "Standard `jam_*` series first; for a lasair node, lasair's own names where a `jam_*` "
        "one is missing. Figures are at the range's end or, for a finished run, at its end; "
        "**whole run** (top right) shows the run start to end. "
        "[All nodes](/d/obs-overview?orgId=1&var-net=${net}&var-run_id=${run_id}&${__url_time_range}) · "
        "[Logs](/d/obs-logs?orgId=1&var-net=${net}&var-run_id=${run_id}&var-node=${node}&${__url_time_range})"),
        24, 3)

    state = stat("State", STATE.replace(LS + " >=", ls_node + " >=", 1),
                 "advancing, stopped during the run (its best slot stopped over %d s before the rest "
                 "of the net's), or net stalled (no node advancing)." % STOP_GRACE, instant=True)
    state["fieldConfig"]["defaults"]["mappings"] = STATE_MAP
    state["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"}
    state["fieldConfig"]["defaults"]["thresholds"] = NEUTRAL        # no data: grey
    state["fieldConfig"]["defaults"]["noValue"] = "no chain data"
    L.add(state, 4, 4)

    why = stat("Why it stopped", "x", "The container's last stop in the range, from Docker "
               "(the lifecycle service): teardown (the net was shut down), stopped, killed, oom, "
               "crashed or exited, and when. Red: killed, OOM-killed or crashed while its run was "
               "active. Orange: the "
               "container runs but its best slot has not moved for over a minute (hung, or cut off).", instant=True,
               legend="{{reason}}", text_mode="value_and_name")
    bad = 'max by (reason) (last_over_time(%s{%s,during_run="true",reason=~"%s"}[$__range]))' % (DIED, NSEL, ABNORMAL)
    why_q = [("A", "%s * 1000" % bad, RED),
             ("B", "max by (reason) (last_over_time(%s{%s}[$__range])) * 1000 unless %s" % (DIED, NSEL, bad),
              "#8e8e8e"),
             ("C", "max(%s) * 1000 and on() (max(last_over_time(obs_container_running{%s}[1m] @ $at)) == 1) "
                   "and on() (max(%s) < ($at - %d))" % (ls_node, NSEL, ls_node, STOP_GRACE),
              "orange")]
    why["targets"] = [{"expr": e, "refId": r, "range": False, "instant": True,
                       "legendFormat": "{{reason}}" if r != "C" else "running, stalled since"}
                      for r, e, _ in why_q]
    why["fieldConfig"]["defaults"].update(unit="dateTimeAsLocalNoDateIfToday", noValue="not stopped")
    why["fieldConfig"]["overrides"] = [
        {"matcher": {"id": "byFrameRefID", "options": r},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]} for r, _, c in why_q]
    L.add(why, 6, 4)
    ex = stat("Exit code", "max(last_over_time(obs_container_last_exit_code{%s}[$__range]))" % NSEL,
              "The exit code of its last stop in the range. 137: SIGKILL (docker kill, OOM, a stop "
              "that timed out); 143: SIGTERM.", instant=True)
    ex["fieldConfig"]["defaults"]["noValue"] = "–"
    L.add(ex, 2, 4)
    L.add(stat("Restarts", "max(last_over_time(obs_container_restarts_total{%s}[$__range])) or "
               "(max(last_over_time(obs_container_running{%s}[$__range])) * 0)" % (NSEL, NSEL),
               "Starts after it had stopped (restart policy, docker restart).", instant=True), 2, 4)
    seen = stat("Last advanced", "max(%s) * 1000" % ls_node, "When its best slot last moved in the "
                "range, up to the run's end (to within a minute).", unit="dateTimeAsLocalNoDateIfToday",
                instant=True)
    L.add(seen, 3, 4)
    scr = stat("Scraped", "max(last_over_time(up{%s}[1m] @ $at))" % NSEL,
               "up: 1 while Alloy scrapes its /metrics. Nodes seen only through JIP-2 or JIP-3 "
               "have no scrape target.", instant=True,
               mappings=[{"type": "value", "options": {"0": {"text": "target down", "color": RED},
                                                       "1": {"text": "up", "color": GREEN}}}])
    scr["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"}
    scr["fieldConfig"]["defaults"]["thresholds"] = NEUTRAL
    scr["fieldConfig"]["defaults"]["noValue"] = "no scrape target"
    L.add(scr, 3, 4)
    L.add(stat("Last scrape", "max(max_over_time(timestamp(up{%s})[$__range:15s] @ $at)) * 1000" % NSEL,
               "When Alloy last scraped it.", unit="dateTimeAsLocalNoDateIfToday", instant=True), 4, 4)

    L.add(logs("Lifecycle", '{source="lifecycle", net="$net", run_id="$run_id", node="$node"} | json '
               '| line_format `{{.msg}}`',
               "The container's Docker events (create, start, kill, oom, stop, die with its exit "
               "code and why, destroy), newest first. Expand a line for its fields (event, "
               "reason, exit_code, signal, during_run)."), 12, 9)
    L.add(logs("Last words", NLOKI + ', source!="lifecycle"}',
               "Its last 20 lines in the range: if it stopped, what it said just before.",
               max_lines=20), 12, 9)

    L.row("Chain")
    L.add(ts("Best and finalized slot",
             [("max(jam_best_slot{%s})" % NSEL, "best"),
              (either("max(jam_finalized_slot{%s})" % NSEL, "max(lasair_finalized_slot{%s})" % NSEL), "finalized")],
             "Slots of its best and finalized blocks.", decimals=0, min0=False), 8, 8)
    L.add(ts("Height",
             [(either("max(jam_best_height{%s})" % NSEL, "max(lasair_block_height{%s})" % NSEL), "best"),
              (either("max(jam_finalized_height{%s})" % NSEL, "max(lasair_finalized_height{%s})" % NSEL),
               "finalized")],
             "Blocks from genesis to its best and finalized blocks.", decimals=0, min0=False), 8, 8)
    L.add(ts("Lag (slots)",
             [("max(jam_best_slot{%s}) - on() max(jam_best_slot{%s})" % (SEL, NSEL), "behind the newest head"),
              ("max(jam_best_slot{%s}) - on() max(jam_finalized_slot{%s} > 0)" % (NSEL, NSEL), "finality lag")],
             "How far its best block trails the net's newest, and its best slot minus its "
             "finalized slot; the dashed line is one epoch.", threshold=EPOCH_SLOTS, decimals=0), 8, 8)
    L.add(ts("Blocks per minute",
             [(either("60 * sum(rate(jam_blocks_authored_total{%s}[2m]))" % NSEL,
                      "60 * sum(rate(lasair_blocks_authored_total{%s}[2m]))" % NSEL), "authored"),
              (either("60 * sum(rate(jam_blocks_imported_total{%s}[2m]))" % NSEL,
                      "60 * sum(rate(lasair_blocks_imported_total{%s}[2m]))" % NSEL), "imported")],
             "Blocks it authored, and blocks of other authors it imported."), 8, 8)
    L.add(ts("Block import time",
             [("histogram_quantile(%s, sum by (le) (rate(jam_block_import_seconds_bucket{%s}[5m])))" % (q, NSEL),
               "p%d" % int(float(q) * 100)) for q in ("0.5", "0.99")],
             "From the start of an import to the end of its execution.", unit="s"), 8, 8)
    L.add(ts("Peers", [("sum(max by (role) (jam_peers{%s}))" % NSEL, "peers")],
             "Connected JAMNP-S peers.", decimals=0), 8, 8)

    L.row("Work-packages (guaranteeing)")
    L.add(ts("Work-packages per minute",
             [(either("60 * sum(rate(jam_wp_received_total{%s}[2m]))" % NSEL,
                      "60 * sum(rate(lasair_ce133_queued_total{%s}[2m]))" % NSEL), "received"),
              (either('60 * sum(rate(jam_wp_stage_seconds_count{%s,stage="guarantee"}[2m]))' % NSEL,
                      "60 * sum(rate(lasair_ce133_guaranteed_total{%s}[2m]))" % NSEL), "guaranteed"),
              (either("60 * sum(rate(jam_wp_refused_total{%s}[2m]))" % NSEL,
                      "60 * sum(rate(lasair_ce133_refused_total{%s}[2m]))" % NSEL), "refused"),
              (either("60 * sum(rate(jam_wp_expired_total{%s}[2m]))" % NSEL,
                      "60 * sum(rate(lasair_ce133_expired_total{%s}[2m]))" % NSEL), "expired")],
             "Work-packages it received as a guarantor, guaranteed, refused, and let expire "
             "before guaranteeing."), 8, 8)
    L.add(ts("Guarantor pipeline, p50 per stage",
             [("histogram_quantile(0.5, sum by (stage, le) (rate(jam_wp_stage_seconds_bucket{%s}[10m])))" % NSEL,
               "{{stage}}")],
             "Time to reach each stage from the previous one (jam_wp_stage_seconds).", unit="s", stack=True), 8, 8)
    L.add(ts("Refine time and queue",
             [(either("histogram_quantile(%s, sum by (le) (rate(jam_refine_seconds_bucket{%s}[10m])))" % (q, NSEL),
                      "histogram_quantile(%s, sum by (le) (rate(lasair_guarantor_refine_seconds_bucket{%s}[10m])))"
                      % (q, NSEL)), "refine p%d" % int(float(q) * 100)) for q in ("0.5", "0.99")] +
             [("max(lasair_ce133_queue_depth{%s})" % NSEL, "lasair CE-133 queue (packages)")],
             "Wall-clock refine time of one work-package, and (lasair) packages waiting in its "
             "builder queue.", unit="s", overrides=[right_axis("lasair CE-133.*", "short")]), 8, 8)

    L.row("Resources")
    L.add(ts("CPU (cores)", [("sum(rate(obs_container_cpu_seconds_total{%s}[1m]))" % NSEL, "container")],
             "CPU time per second its container used (Docker stats, every 10 s).", decimals=2), 12, 7)
    L.add(ts("Memory",
             [("max(obs_container_memory_bytes{%s})" % NSEL, "container (working set)"),
              ("max(lasair_mem_rss_bytes{%s})" % NSEL, "lasair RSS"),
              ("max(lasair_mem_ocaml_heap_bytes{%s})" % NSEL, "lasair OCaml heap")],
             "Its container's memory less inactive page cache (Docker stats), and lasair's own "
             "view of its memory.", unit="bytes"), 12, 7)

    L.row("Logs")
    L.add(ts("Warnings and errors per minute",
             [('sum(count_over_time(%s, source!="lifecycle"} |~ `%s` [1m]))' % (NLOKI, WARN_RE), "warnings"),
              ('sum(count_over_time(%s, source!="lifecycle"} |~ `%s` [1m]))' % (NLOKI, ERR_RE), "errors")],
             "Lines that look like warnings or errors (plain text or JSON levels).", ds=LOKI,
             overrides=[{"matcher": {"id": "byName", "options": "errors"},
                         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": RED}}]},
                        {"matcher": {"id": "byName", "options": "warnings"},
                         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]}]),
          24, 6)
    L.add(logs("Warnings and errors", '%s, source!="lifecycle"} |~ `%s|%s`' % (NLOKI, WARN_RE, ERR_RE),
               "Only its warning and error lines, newest first."), 24, 9)
    L.add(logs("Every line", '%s, source!="lifecycle"}' % NLOKI,
               "Everything it wrote, and its JIP-3 events, newest first; the level colours each "
               "line."), 24, 14)
    return dashboard("obs-node", "Node detail", L, variables_=vs)


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
                   "current": {"text": "", "value": ""}, "hide": 0}] + run_variables()
    sel = 'net="$net", run_id="$run_id", node=~"$node"'
    L = Layout()
    L.add(text(
        "**What did the nodes say?** Every labelled container's output (`source=\"docker\"`), "
        "every JIP-3 event a node pushed (`source=\"jip3\"`, one JSON object per event) and "
        "the containers' lifecycle (`source=\"lifecycle\"`: start, stop, die with exit code), "
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
        "collectors on the nets' networks, lifecycle follows the containers and runs, and "
        "the disk stays under its caps."), 24, 2)
    L.add(stat("obs services down · PASS = 0", 'sum(1 - up{job=~"obs-.*"})',
               "Stack services Alloy scrapes that do not answer.", "==", 0), 4, 4)
    L.add(stat("Scrape targets down (info)", 'sum(1 - up{job!~"obs-.*"}) or vector(0)',
               "Net targets that do not answer: a stopped load generator counts too."), 4, 4)
    L.add(stat("Samples failed to write (5 min) · PASS = 0",
               "sum(increase(prometheus_remote_storage_samples_failed_total[5m])) or vector(0)",
               "Samples Alloy could not remote-write to Prometheus.", "==", 0), 4, 4)
    L.add(stat("Log entries dropped (5 min) · PASS = 0",
               "(sum(increase(loki_write_dropped_entries_total[5m])) or vector(0)) + "
               "(sum(increase(jip3_loki_dropped_total[5m])) or vector(0)) + "
               "(sum(increase(obs_lifecycle_loki_dropped_total[5m])) or vector(0))",
               "Log entries Alloy, the JIP-3 receiver or lifecycle dropped instead of writing to Loki.",
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
    L.add(ts("lifecycle",
             [("sum(obs_lifecycle_containers)", "containers tracked"),
              ("count(obs_run_info)", "runs exported"),
              ("sum(increase(obs_lifecycle_errors_total[10m]))", "Docker API errors per 10 min")],
             "Labelled containers whose events, CPU and memory the lifecycle service records, "
             "and the run records it exports.", decimals=0), 8, 6)
    L.add(ts("Pushgateway groups", [("count(push_time_seconds)", "groups")],
             "Result groups batch jobs pushed (one per job and run).", decimals=0), 8, 6)
    L.add(ts("Prometheus series", [("sum(prometheus_tsdb_head_series)", "head series")],
             "Active series in Prometheus's head block.", decimals=0), 8, 6)
    return dashboard("obs-self", "obs self-health", L, variables_=[], refresh="30s")


def render_all():
    return {"obs-overview.json": overview(), "obs-chain.json": chain(), "obs-node.json": node_detail(),
            "obs-logs.json": logs_dashboard(), "obs-self.json": self_health()}


if __name__ == "__main__":
    write_all(OUT, render_all(), sys.argv)
