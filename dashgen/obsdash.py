"""obsdash: small helpers for writing Grafana dashboards as code, in plain Python.

The platform dashboards (dashgen/platform.py) are made with it, and a project may use it
for its own (import it from $OBS_HOME/dashgen). The conventions it encodes:

  * every dashboard is scoped by two variables, net and run_id, which every series
    carries (README.md, "Labels"); a run's link sets both and the time range;
  * it opens with a text panel saying what question it answers;
  * a pass/fail stat is colored by its threshold, and its title states the threshold;
  * run annotations (obs annotate) are drawn: blue events, green PASS, red FAIL;
  * the dashboards link to each other by the tag "obs", keeping variables and time.

Stdlib only. A dashboard is a dict; write it with json.dumps(d, indent=1).
"""
import json
import os

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
SEL = 'net="$net",run_id="$run_id"'
GREEN, RED, BLUE, GREY = "green", "red", "#5794F2", "text"


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
    """A stat over the dashboard's range (a finished run still shows its last value).
    pass_if/threshold color it green/red; the title states the threshold."""
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
    return {"type": "timeseries", "title": title, "description": desc, "datasource": ds,
            "targets": [{"expr": e, "legendFormat": lg, "refId": chr(65 + i), "range": True}
                        for i, (e, lg) in enumerate(targets)],
            "fieldConfig": {"defaults": d, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def table(title, targets, desc="", overrides=None, transformations=None, sort=None, ds=PROM):
    """An instant-query table. targets: [(expr, refId), ...], each format=table."""
    p = {"type": "table", "title": title, "description": desc, "datasource": ds,
         "targets": [{"expr": e, "refId": r, "format": "table", "instant": True, "range": False}
                     for e, r in targets],
         "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                         "overrides": overrides or []},
         "transformations": transformations or [],
         "options": {"showHeader": True, "cellHeight": "sm"}}
    if sort:
        p["options"]["sortBy"] = [{"displayName": sort[0], "desc": sort[1]}]
    return p


def logs(title, expr, desc=""):
    return {"type": "logs", "title": title, "description": desc, "datasource": LOKI,
            "targets": [{"expr": expr, "refId": "A", "queryType": "range"}],
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
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


def variables(source='{__name__=~"up|jam_best_slot",run_id!=""}', ds=PROM):
    """net, then run_id of that net (newest first: run ids end in their UTC start time).
    `source` is the series selector whose labels list the runs."""
    def var(name, query, sort):
        return {"name": name, "label": name, "type": "query", "datasource": ds,
                "query": query, "definition": query, "refresh": 2, "sort": sort,
                "includeAll": False, "multi": False, "current": {}, "options": [], "hide": 0}
    run_src = source.replace('run_id!=""', 'net="$net"')
    return [var("net", "label_values(%s, net)" % source, 1),
            var("run_id", "label_values(%s, run_id)" % run_src, 2)]


def annotations():
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
    ]}


def dashboard(uid, title, layout, tags=("obs",), variables_=None, refresh="10s",
              time_from="now-1h", links=True):
    d = {"uid": uid, "title": title, "tags": list(tags), "timezone": "browser",
         "refresh": refresh, "time": {"from": time_from, "to": "now"}, "editable": False,
         "graphTooltip": 1, "schemaVersion": 39, "version": 1,
         "templating": {"list": variables() if variables_ is None else variables_},
         "annotations": annotations(),
         "links": [{"type": "dashboards", "tags": ["obs"], "asDropdown": False,
                    "includeVars": True, "keepTime": True, "title": "obs"}] if links else [],
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
