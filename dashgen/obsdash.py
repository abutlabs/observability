"""obsdash: small helpers for writing Grafana dashboards as code, in plain Python.

The platform dashboards (dashgen/platform.py) are made with it, and a project may use it
for its own (import it from $OBS_HOME/dashgen). The conventions it encodes:

  * every dashboard is scoped by two variables, net and run_id, which every series
    carries (README.md, "Labels"); a run's link sets both and the time range;
  * runs are first-class: hidden variables read the run's record (obs_run_*, exported
    by the lifecycle service) so that
      - the "whole run" link at the top sets the time range to the run's start → end;
      - run-scoped stats and tables describe the run at $at, the run's end when it
        ended before the range does: a finished run shows its last figures, not
        "no data" at now;
  * it opens with a text panel saying what question it answers;
  * a pass/fail stat is colored by its threshold, and its title states the threshold;
  * annotations: run events (obs annotate) blue, PASS green, FAIL red, and the lifecycle
    of the run and its containers (Loki, source="lifecycle"): starts, restarts, stops
    purple, a node that stopped while its run was active red;
  * every per-node series and table row links to Node detail (obs-node) for that node
    and to its logs in Explore;
  * the dashboards link to each other by the tag "obs", keeping variables and time.

Stdlib only. A dashboard is a dict; write it with json.dumps(d, indent=1).
"""
import json
import os
import re
import urllib.parse

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
SEL = 'net="$net",run_id="$run_id"'
GREEN, RED, BLUE, GREY, PURPLE = "green", "red", "#5794F2", "text", "#B877D9"
ABNORMAL = "killed|oom|crashed"   # lifecycle reasons that are a failure while the run is on;
#                                  a graceful stop or a clean exit 0 (an init container) is not
STOP_GRACE = 60           # a node that stopped this long before the rest of its net stopped early
NODE_DETAIL = "obs-node"


# ---- runs ---------------------------------------------------------------------------
def at_end(expr):
    """expr's last value in the dashboard range, ending at $at (the run's end when it
    ended before the range does). As an instant query it shows a finished run's figures
    whatever the range; a live run's at the range's end."""
    return "last_over_time((%s)[$__range:$__interval] @ $at)" % expr


def last_advanced(metric="jam_best_slot", sel=SEL, by="node, client"):
    """Per node, the last time (unix s, to within a minute) in the range, up to $at, that its
    best slot moved. Progress, not the arrival of samples: a collector may keep exporting a
    gone node's last value (the JIP-3 receiver keeps a disconnected node's series)."""
    return ("max by (%s) (max_over_time((timestamp(%s{%s}) and changes(%s{%s}[1m]) > 0)[$__range:15s] @ $at))"
            % (by, metric, sel, metric, sel))


def stopped_early(ls):
    """The nodes (of last_advanced() `ls`) that stopped more than STOP_GRACE seconds before
    the rest of the net: while the run went on. A net shut down at the end of its run stops
    all together, whatever order the run's end and the teardown came in."""
    return "%s < on() group_left() (max(%s) - %d)" % (ls, ls, STOP_GRACE)


def run_variables(ds=PROM):
    """Hidden variables of a run-scoped dashboard (after net and run_id):
      at        unix seconds the stats describe: the run's end if it ended before the
                range's end, else the range's end
      run_from  the range that shows the whole run (unix ms, 1 min margins; run_to "now"
      run_to    while it runs), from obs_run_info, for the "whole run" link"""
    to = "${__to:date:seconds}"

    def var(name, query, regex):
        return {"name": name, "label": name, "type": "query", "datasource": ds, "query": query,
                "definition": query, "regex": regex, "refresh": 2, "sort": 0, "includeAll": False,
                "multi": False, "current": {}, "options": [], "hide": 2}
    run = 'net="$net",run_id="$run_id"'
    return [var("at", "query_result(clamp_max(max(obs_run_end_timestamp_seconds{%s}), %s) or vector(%s))"
                % (run, to, to), r"/\}\s+(\S+)\s/"),
            var("run_from", "query_result(max by (link_from) (obs_run_info{%s}))" % run, '/link_from="([^"]+)"/'),
            var("run_to", "query_result(max by (link_to) (obs_run_info{%s}))" % run, '/link_to="([^"]+)"/')]


def whole_run_link(uid):
    """The link at the top of a run-scoped dashboard: this dashboard over the whole run."""
    return {"type": "link", "title": "whole run", "icon": "dashboard", "includeVars": True,
            "keepTime": False, "targetBlank": False,
            "tooltip": "Set the time range to this run's start → end (1 min either side)",
            "url": "/d/%s?from=${run_from}&to=${run_to}" % uid}


# ---- links to a node ----------------------------------------------------------------
def _url_keep_vars(raw):
    """Percent-encode raw text, keeping Grafana's $var and ${...} placeholders."""
    return "".join(p if p.startswith("$") else urllib.parse.quote(p, safe="")
                   for p in re.split(r"(\$\{[^}]+\}|\$[A-Za-z_]+)", raw))


def explore_logs_url(node, frm, to):
    """Grafana Explore on Loki: that node's lines of the run, over [frm, to]."""
    pane = {"datasource": "loki", "range": {"from": frm, "to": to},
            "queries": [{"refId": "A", "datasource": LOKI,
                         "expr": '{net="$net",run_id="$run_id",node="%s"}' % node}]}
    return "/explore?schemaVersion=1&orgId=1&panes=" + _url_keep_vars(json.dumps({"a": pane},
                                                                                separators=(",", ":")))


def node_links(node="${__field.labels.node}", logs_from="${__from}", logs_to="${__value.time}"):
    """Data links of a per-node field: Node detail over the same range, and the node's
    logs in Explore (by default from the range's start to the clicked moment, newest
    first: what it said leading up to it)."""
    return [{"title": "Node detail: %s" % node, "targetBlank": False,
             "url": "/d/%s?orgId=1&var-net=${net}&var-run_id=${run_id}&var-node=%s&${__url_time_range}"
                    % (NODE_DETAIL, node)},
            {"title": "Logs of %s up to here (Explore)" % node, "targetBlank": True,
             "url": explore_logs_url(node, logs_from, logs_to)}]


class Layout:
    """Panels placed left to right, wrapping at 24 columns; ids in order."""

    def __init__(self):
        self.panels, self.x, self.y, self.row_h, self.id = [], 0, 0, 0, 0

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        self.id += 1
        panel.update(id=self.id, gridPos={"x": self.x, "y": self.y, "w": w, "h": h})
        self.panels.append(panel)
        self.x += w
        self.row_h = max(self.row_h, h)

    def newline(self):
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0

    def row(self, title):
        self.newline()
        self.add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)
        self.newline()


def text(md, title=""):
    return {"type": "text", "title": title, "transparent": not title,
            "options": {"mode": "markdown", "content": md}}


def _steps(pass_if, threshold):
    """Threshold steps coloring a value green when it passes, red when it fails."""
    if pass_if in ("<=", "=="):
        return [{"color": GREEN, "value": None}, {"color": RED, "value": threshold + 1e-9}]
    if pass_if == ">=":
        return [{"color": RED, "value": None}, {"color": GREEN, "value": threshold}]
    if pass_if == ">":
        return [{"color": RED, "value": None}, {"color": GREEN, "value": threshold + 1e-9}]
    raise ValueError(pass_if)


def stat(title, expr, desc, pass_if=None, threshold=None, unit=None, decimals=0,
         calc="lastNotNull", mappings=None, ds=PROM, instant=False, legend=None, text_mode="value"):
    """A stat of the dashboard's range. A run-scoped one (its expr names $run_id) shows
    expr's last value up to $at (at_end): a finished run shows its figures at its end.
    instant=True: exactly at the range's end instead. pass_if/threshold color it
    green/red; the title states the threshold."""
    if not instant and "$run_id" in expr and ds is PROM:
        expr, instant = at_end(expr), True
    fc = {"decimals": decimals, "noValue": "no data"}
    if pass_if:
        fc["color"] = {"mode": "thresholds"}
        fc["thresholds"] = {"mode": "absolute", "steps": _steps(pass_if, threshold)}
    else:
        fc["color"] = {"mode": "fixed", "fixedColor": GREY}
    if unit:
        fc["unit"] = unit
    if mappings:
        fc["mappings"] = mappings
    t = {"expr": expr, "refId": "A", "range": not instant, "instant": instant}
    if legend:
        t["legendFormat"] = legend
    return {"type": "stat", "title": title, "description": desc, "datasource": ds,
            "targets": [t],
            "fieldConfig": {"defaults": fc, "overrides": []},
            "options": {"graphMode": "none" if instant else "area", "colorMode": "background",
                        "textMode": text_mode, "justifyMode": "center",
                        "reduceOptions": {"calcs": [calc], "fields": "", "values": False}}}


PASS_FAIL = [{"type": "value", "options": {"0": {"text": "FAIL", "color": RED},
                                           "1": {"text": "PASS", "color": GREEN}}}]


def ts(title, targets, desc="", unit=None, threshold=None, decimals=None, stack=False,
       overrides=None, min0=True, max=None, ds=PROM):
    """targets: [(expr, legend), ...]. threshold: a dashed red line at that value."""
    d = {"custom": {"lineWidth": 2, "fillOpacity": 10 if stack else 0, "showPoints": "never",
                    "spanNulls": False,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"}},
         "color": {"mode": "palette-classic"}}
    if unit:
        d["unit"] = unit
    if decimals is not None:
        d["decimals"] = decimals
    if min0:
        d["min"] = 0
    if max is not None:
        d["max"] = max
    if threshold is not None:
        d["custom"]["thresholdsStyle"] = {"mode": "dashed"}
        d["thresholds"] = {"mode": "absolute", "steps": [{"color": "transparent", "value": None},
                                                         {"color": RED, "value": threshold}]}
    if any("{{node}}" in lg for _, lg in targets):
        d["links"] = node_links()
    return {"type": "timeseries", "title": title, "description": desc, "datasource": ds,
            "targets": [{"expr": e, "legendFormat": lg, "refId": chr(65 + i), "range": True}
                        for i, (e, lg) in enumerate(targets)],
            "fieldConfig": {"defaults": d, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def table(title, targets, desc="", overrides=None, transformations=None, sort=None, ds=PROM,
          node_field=None, logs_range=("${__from}", "${__to}")):
    """An instant-query table. targets: [(expr, refId), ...], each format=table; a
    run-scoped one (naming $run_id, not $at) shows its last values up to $at (at_end).
    node_field: the column naming the node: every cell links to that node (node_links;
    its logs over logs_range)."""
    def fix(e):
        return at_end(e) if "$run_id" in e and "$at" not in e and ds is PROM else e
    defaults = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}}
    if node_field:
        defaults["links"] = node_links("${__data.fields.%s}" % node_field, *logs_range)
    p = {"type": "table", "title": title, "description": desc, "datasource": ds,
         "targets": [{"expr": fix(e), "refId": r, "format": "table", "instant": True, "range": False}
                     for e, r in targets],
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "transformations": transformations or [],
         "options": {"showHeader": True, "cellHeight": "sm"}}
    if sort:
        p["options"]["sortBy"] = [{"displayName": sort[0], "desc": sort[1]}]
    return p


def logs(title, expr, desc="", max_lines=None, order="Descending"):
    t = {"expr": expr, "refId": "A", "queryType": "range"}
    if max_lines:
        t["maxLines"] = max_lines
    return {"type": "logs", "title": title, "description": desc, "datasource": LOKI,
            "targets": [t],
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": order,
                        "enableLogDetails": True, "dedupStrategy": "none",
                        "prettifyLogMessage": False, "showLabels": False, "showCommonLabels": False}}


def right_axis(regex, unit=None):
    props = [{"id": "custom.axisPlacement", "value": "right"},
             {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}]
    if unit:
        props.append({"id": "unit", "value": unit})
    return {"matcher": {"id": "byRegexp", "options": regex}, "properties": props}


def per_min(metric, extra="", sel=SEL):
    return "60 * rate(%s{%s%s}[2m])" % (metric, sel, extra)


def variables(source='{__name__=~"up|jam_best_slot|obs_run_info",run_id!=""}', ds=PROM):
    """net, then run_id of that net (newest first: run ids end in their UTC start time),
    then the run's hidden variables (run_variables). `source` is the series selector whose
    labels list the runs; obs_run_info lists every recorded run of the last 31 days,
    whether or not it has data in the range."""
    def var(name, query, sort):
        return {"name": name, "label": name, "type": "query", "datasource": ds,
                "query": query, "definition": query, "refresh": 2, "sort": sort,
                "includeAll": False, "multi": False, "current": {}, "options": [], "hide": 0}
    run_src = source.replace('run_id!=""', 'net="$net"')
    return [var("net", "label_values(%s, net)" % source, 1),
            var("run_id", "label_values(%s, run_id)" % run_src, 2)] + run_variables(ds)


def annotations(lifecycle=True):
    """Run events, PASS, FAIL (obs annotate) and, on a run-scoped dashboard, the lifecycle."""
    def ann(name, tags, color):
        return {"name": name, "enable": True, "hide": False, "iconColor": color,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "target": {"type": "tags", "tags": tags, "matchAny": False, "limit": 500}}
    return {"list": [
        {"builtIn": 1, "name": "Annotations & Alerts", "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "type": "dashboard",
         "datasource": {"type": "grafana", "uid": "-- Grafana --"}},
        ann("run events", ["$run_id", "event"], BLUE),
        ann("PASS", ["$run_id", "pass"], "green"),
        ann("FAIL", ["$run_id", "fail"], "red"),
    ] + (lifecycle_annotations() if lifecycle else [])}


def lifecycle_annotations():
    """The run's and its containers' lives, from the lifecycle service's Loki lines: run
    begin and end, starts, restarts and stops (purple), and red for a container killed,
    OOM-killed or crashed while its run was active."""
    base = '{source="lifecycle", net="$net", run_id="$run_id"} | json'

    def ann(name, expr, color):
        return {"name": name, "enable": True, "hide": False, "iconColor": color, "datasource": LOKI,
                "expr": expr, "target": {"expr": expr, "refId": "Anno", "queryType": "range"},
                "instant": False, "titleFormat": "{{node}} {{event}}", "textFormat": "{{msg}}",
                "tagKeys": "node,event,reason"}
    return [ann("lifecycle", base + ' | event=~"run_begin|run_end|start|restart|die" '
                '| during_run!="true" or reason!~"%s"' % ABNORMAL, PURPLE),
            ann("node stopped during the run", base + ' | event="die" | during_run="true" | reason=~"%s"'
                % ABNORMAL, RED)]


def dashboard(uid, title, layout, tags=("obs",), variables_=None, refresh="10s",
              time_from="now-1h", links=True):
    tvars = variables() if variables_ is None else variables_
    lk = [{"type": "dashboards", "tags": ["obs"], "asDropdown": False,
           "includeVars": True, "keepTime": True, "title": "obs"}] if links else []
    if "run_to" in [v["name"] for v in tvars]:
        lk.insert(0, whole_run_link(uid))
    d = {"uid": uid, "title": title, "tags": list(tags), "timezone": "browser",
         "refresh": refresh, "time": {"from": time_from, "to": "now"}, "editable": False,
         "graphTooltip": 1, "schemaVersion": 39, "version": 1,
         "templating": {"list": tvars},
         "annotations": annotations(lifecycle="run_id" in [v["name"] for v in tvars]),
         "links": lk,
         "panels": layout.panels}
    return d


def render(d):
    return json.dumps(d, indent=1) + "\n"


def write_all(out_dir, dashboards, argv):
    """Write {file name: dashboard} into out_dir; with --check exit 1 if any is stale."""
    out = {k: render(v) for k, v in dashboards.items()}
    if "--check" in argv:
        stale = [k for k, v in out.items() if not os.path.exists(os.path.join(out_dir, k))
                 or open(os.path.join(out_dir, k)).read() != v]
        if stale:
            raise SystemExit("stale dashboards: %s (regenerate them)" % ", ".join(stale))
        return
    os.makedirs(out_dir, exist_ok=True)
    for k, v in out.items():
        with open(os.path.join(out_dir, k), "w") as fh:
            fh.write(v)
    print("wrote %s" % ", ".join(sorted(out)))
