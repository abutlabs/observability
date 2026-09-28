"""lib/obslib.py (the label model, the metrics registry, file targets), netjoin's
planning, and the dashboards. Hermetic: no Docker.

    python3 -m unittest discover -s tests
"""
import glob
import json
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.join(REPO, "netjoin"))
import obslib  # noqa: E402
import netjoin  # noqa: E402

P = obslib.PREFIX
PROJ, SVC = obslib.COMPOSE_PROJECT, obslib.COMPOSE_SERVICE


def load_platform():
    import importlib.util
    spec = importlib.util.spec_from_file_location("obs_platform", os.path.join(REPO, "dashgen", "gen_platform.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Labels(unittest.TestCase):
    def test_defaults_match_the_alloy_rules(self):
        self.assertEqual(obslib.obs_labels({PROJ: "lasair-pj", SVC: "lm0"}, "/lasair-pj-lm0-1"),
                         {"net": "lasair-pj", "run_id": "lasair-pj-adhoc", "node": "lm0",
                          "client": "lm0", "job": "lm0"})
        self.assertEqual(obslib.obs_labels({}, "/solo"),
                         {"net": "docker", "run_id": "docker-adhoc", "node": "solo",
                          "client": "solo", "job": "solo"})
        full = {P + "net": "n", P + "run_id": "n-1", P + "node": "v0", P + "job": "lasair",
                P + "client": "lasair", PROJ: "p", SVC: "s"}
        self.assertEqual(obslib.obs_labels(full, "c"),
                         {"net": "n", "run_id": "n-1", "node": "v0", "client": "lasair", "job": "lasair"})
        # an empty run id label (a net started without the stack) falls back like Alloy
        self.assertEqual(obslib.obs_labels({P + "net": "n", P + "run_id": ""}, "c")["run_id"], "n-adhoc")

    def test_the_alloy_config_names_every_label_it_defaults(self):
        cfg = open(os.path.join(REPO, "alloy", "config.alloy")).read()
        for key in ("net", "run_id", "node", "job", "client", "scrape", "port", "path", "logs"):
            self.assertIn("__meta_docker_container_label_org_abutlabs_obs_" + key, cfg)
        for target in ("net", "run_id", "node", "job", "client", "instance"):
            self.assertIn('target_label  = "%s"' % target, cfg)

    def test_observed(self):
        self.assertTrue(obslib.observed({P + "logs": "true"}))
        self.assertTrue(obslib.observed({P + "jip2": "19800"}))
        self.assertFalse(obslib.observed({PROJ: "x"}))
        self.assertFalse(obslib.observed({P + "scrape": "true", P + "enable": "false"}))


class Metrics(unittest.TestCase):
    def test_render(self):
        m = obslib.Metrics()
        c = m.counter("x_total", "things", ["node"])
        c.labels(node='a"b').inc(2)
        m.gauge("g", "a gauge").set(1.5)
        h = m.histogram("h_seconds", "time", ["node"], buckets=(1, 5))
        h.labels(node="n").observe(0.5)
        h.labels(node="n").observe(3)
        text = m.render()
        self.assertIn('x_total{node="a\\"b"} 2', text)
        self.assertIn("g 1.5", text)
        self.assertIn('h_seconds_bucket{node="n",le="1"} 1', text)
        self.assertIn('h_seconds_bucket{node="n",le="5"} 2', text)
        self.assertIn('h_seconds_bucket{node="n",le="+Inf"} 2', text)
        self.assertIn('h_seconds_count{node="n"} 2', text)
        c.remove(node='a"b')
        self.assertNotIn("x_total{", m.render())

    def test_read_targets_skips_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.json"), "w") as fh:
                json.dump([{"targets": ["h:1", "h:2"], "labels": {"net": "n"}}], fh)
            with open(os.path.join(d, "b.json"), "w") as fh:
                fh.write("{half")
            self.assertEqual(obslib.read_targets(os.path.join(d, "*.json")),
                             [("h:1", {"net": "n"}), ("h:2", {"net": "n"})])


def container(name, project, state="running", mode="lasair-pj_net", nets=("lasair-pj_net",), **labels):
    lb = {P + k.replace("_", "."): v for k, v in labels.items()}
    lb[PROJ] = project
    return {"Names": ["/" + name], "State": state, "Labels": lb,
            "HostConfig": {"NetworkMode": mode},
            "NetworkSettings": {"Networks": {n: {"IPAddress": ""} for n in nets}}}


class NetJoin(unittest.TestCase):
    def test_wanted_networks(self):
        cs = [container("lm0", "lasair-pj", scrape="true"),
              container("pj3", "lasair-pj", state="created", logs="true"),
              container("old", "gone", state="exited", scrape="true", nets=("gone_default",)),
              container("plain", "other", nets=("other_default",)),
              container("hostnet", "h", mode="host", nets=("host",), scrape="true"),
              container("alloy", "obs", scrape="true", nets=("obs_default",))]
        self.assertEqual(netjoin.wanted_networks(cs, "obs"), {"lasair-pj_net"})

    def test_plan_keeps_own_networks(self):
        join, leave = netjoin.plan({"a", "b"}, {"obs_default", "b", "c"}, {"obs_default"})
        self.assertEqual((join, leave), (["a"], ["c"]))

    def test_pick_ip_takes_the_top_free_address(self):
        self.assertEqual(netjoin.pick_ip("10.231.6.0/24", "10.231.6.1", []), "10.231.6.254")
        self.assertEqual(netjoin.pick_ip("10.231.6.0/24", "10.231.6.1",
                                         ["10.231.6.254/24", "10.231.6.253/24"]), "10.231.6.252")
        self.assertIsNone(netjoin.pick_ip("fd00::/64", "", []))
        self.assertIsNone(netjoin.pick_ip("10.0.0.0/31", "", []))

    def test_relevant_events(self):
        netjoin.PROJECT = "obs"
        ev = lambda action, **attrs: {"Action": action, "Actor": {"Attributes": attrs}}  # noqa: E731
        self.assertTrue(netjoin.relevant(ev("die", **{P + "scrape": "true"})))
        self.assertFalse(netjoin.relevant(ev("die", **{PROJ: "x"})))
        self.assertTrue(netjoin.relevant(ev("start", **{PROJ: "obs", SVC: "alloy"})))
        self.assertFalse(netjoin.relevant(ev("start", **{PROJ: "obs", SVC: "grafana"})))


class Dashboards(unittest.TestCase):
    def setUp(self):
        self.platform = load_platform()

    def test_generated_json_is_current(self):
        for name, d in self.platform.render_all().items():
            with open(os.path.join(REPO, "dashboards", name)) as fh:
                self.assertEqual(fh.read(), json.dumps(d, indent=1) + "\n",
                                 "%s is stale: python3 dashgen/gen_platform.py" % name)

    FUNCS = {"by", "on", "or", "and", "unless", "bool", "without", "vector", "max", "min", "sum",
             "count", "rate", "increase", "time", "group", "max_over_time", "min_over_time",
             "last_over_time", "histogram_quantile", "label_replace", "deriv", "abs", "offset"}

    @classmethod
    def metric_names(cls, expr):
        """The metric names a PromQL expression reads (selectors, groupings, strings and
        ranges removed first)."""
        e = re.sub(r'"[^"]*"', "", expr)
        e = re.sub(r"\{[^}]*\}", "", e)
        e = re.sub(r"\b(by|on|without|ignoring|group_left|group_right)\s*\([^)]*\)", "", e)
        e = re.sub(r"\[[^\]]*\]", "", e)
        return {t for t in re.findall(r"(?<![0-9.])\b[a-zA-Z_][a-zA-Z0-9_]*", e) if t not in cls.FUNCS}

    def test_metric_names_parser(self):
        self.assertEqual(self.metric_names('count(max by (node, client) (jam_best_slot{net="$net"})) - '
                                           '(count(x_total[5m]) or vector(0)) + 1e-9'),
                         {"jam_best_slot", "x_total"})

    def test_platform_dashboards_use_only_standard_and_stack_metrics(self):
        allowed = re.compile(r"^(jam_|up$|obs_|jip2_|jip3_|prometheus_|loki_|scrape_|push_time_seconds$)")
        for name, d in self.platform.render_all().items():
            for p in d["panels"]:
                if (p.get("datasource") or {}).get("uid") == "loki":
                    continue
                for t in p.get("targets", []):
                    for m in self.metric_names(t["expr"]):
                        self.assertRegex(m, allowed, "%s / %s: %s" % (name, p["title"], t["expr"]))

    def test_run_scoped_dashboards_filter_every_query_by_run(self):
        for name, d in self.platform.render_all().items():
            names = [v["name"] for v in d["templating"]["list"]]
            if "run_id" not in names:
                continue
            self.assertEqual(d["panels"][0]["type"], "text", name)
            for p in d["panels"]:
                for t in p.get("targets", []):
                    self.assertIn("$run_id", t["expr"], (name, p["title"]))

    def test_every_dashboard_json_in_the_repo_has_a_unique_uid_and_known_datasources(self):
        seen = set()
        for f in glob.glob(os.path.join(REPO, "dashboards", "*.json")):
            d = json.load(open(f))
            self.assertNotIn(d["uid"], seen)
            seen.add(d["uid"])
            for p in d["panels"]:
                ds = p.get("datasource")
                if ds:
                    self.assertIn(ds["uid"], ("prometheus", "loki"), f)


if __name__ == "__main__":
    unittest.main()
