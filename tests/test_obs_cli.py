"""The obs CLI: runs, file targets, links, annotations, pushes and dashboard folders.
Hermetic: a temporary OBS_STATE and sibling tree, fake Grafana and Pushgateway servers.

    python3 -m unittest discover -s tests
"""
import contextlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cli(env):
    """A fresh import of the CLI under this environment."""
    os.environ.update(env)
    loader = importlib.machinery.SourceFileLoader("obs_cli", os.path.join(REPO, "obs"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("obs_cli", loader))
    loader.exec_module(mod)
    return mod


def run(cli, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(list(argv))
    return rc, out.getvalue()


class Recorder(http.server.BaseHTTPRequestHandler):
    """Records every POST (path, auth header, body) and answers 200."""
    posted = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Recorder.posted.append((self.path, self.headers.get("Authorization"), body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id": 7}')

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def recorder():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Recorder.posted.clear()
    try:
        yield srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        self.env = dict(os.environ)
        for k in [k for k in os.environ if k.endswith("_DIR") or k.startswith("OBS_")]:
            del os.environ[k]
        self.cli = load_cli({"OBS_STATE": self.state, "OBS_GRAFANA_PORT": "1",
                             "OBS_PROMETHEUS_PORT": "1", "OBS_PUSHGATEWAY_PORT": "1"})

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def targets(self, sub, net, job):
        with open(os.path.join(self.state, "targets", sub, "%s--%s.json" % (net, job))) as fh:
            return json.load(fh)


class Runs(Base):
    def test_parse_target(self):
        self.assertEqual(self.cli.parse_target("v0@host.docker.internal:41100/stats"),
                         {"node": "v0", "addr": "host.docker.internal:41100", "path": "/stats"})
        self.assertEqual(self.cli.parse_target("lm0:9615"),
                         {"node": "lm0", "addr": "lm0:9615", "path": "/metrics"})

    def test_register_writes_file_sd_with_every_label(self):
        rc, _ = run(self.cli, "register", "n1", "n1-r1", "lasair", "lm0:9615", "v1@h:1/x",
                    "--label", "client=lasair", "--label", "extra=1")
        self.assertEqual(rc, 0)
        g = self.targets("", "n1", "lasair")
        self.assertEqual([x["targets"] for x in g], [["lm0:9615"], ["h:1"]])
        self.assertEqual(g[0]["labels"], {"net": "n1", "run_id": "n1-r1", "job": "lasair",
                                          "node": "lm0", "client": "lasair", "extra": "1"})
        self.assertEqual(g[1]["labels"]["__metrics_path__"], "/x")
        self.assertEqual(run(self.cli, "current", "n1"), (0, "n1-r1\n"))

    def test_jip2_and_jip3_targets_go_to_their_own_directories(self):
        run(self.cli, "register", "n1", "n1-r1", "polkajam", "pj0@host.docker.internal:19800", "--jip2")
        run(self.cli, "register", "n1", "n1-r1", "lasair", "v0@127.0.0.1:40000", "--jip3")
        self.assertEqual(self.targets("jip2", "n1", "polkajam")[0]["targets"], ["host.docker.internal:19800"])
        self.assertEqual(self.targets("jip3", "n1", "lasair")[0]["labels"]["node"], "v0")
        self.assertFalse(os.path.exists(os.path.join(self.state, "targets", "n1--polkajam.json")))

    def test_a_new_run_replaces_the_nets_targets_and_ends_the_old_run(self):
        run(self.cli, "register", "n1", "n1-r1", "lasair", "lm0:9615")
        run(self.cli, "register", "n1", "n1-r1", "dex", "dex:8080", "--jip2")
        run(self.cli, "register", "n1", "n1-r2", "dex", "dex:8080")
        self.assertEqual(sorted(f for f in os.listdir(os.path.join(self.state, "targets"))
                                if f.endswith(".json")), ["n1--dex.json"])
        self.assertEqual(os.listdir(os.path.join(self.state, "targets", "jip2")), [])
        self.assertIsNotNone(self.cli._read(self.cli.run_file("n1-r1"))["end"])
        self.assertIsNone(self.cli._read(self.cli.run_file("n1-r2"))["end"])

    def test_begin_and_end_a_docker_run(self):
        rc, out = run(self.cli, "begin", "lasair-pj", "--meta", "describe=3 lasair : 3 PolkaJam")
        rid = out.strip()
        self.assertTrue(rid.startswith("lasair-pj-") and rid.endswith("Z"), rid)
        rec = self.cli._read(self.cli.run_file(rid))
        self.assertEqual((rec["net"], rec["kind"], rec["meta"]["describe"]),
                         ("lasair-pj", "docker", "3 lasair : 3 PolkaJam"))
        self.assertEqual(run(self.cli, "current", "lasair-pj"), (0, rid + "\n"))
        run(self.cli, "begin", "lasair-pj", "--run-id", "lasair-pj-x")
        self.assertIsNotNone(self.cli._read(self.cli.run_file(rid))["end"])
        run(self.cli, "end", "lasair-pj")
        self.assertEqual(run(self.cli, "current", "lasair-pj")[0], 1)
        self.assertIsNotNone(self.cli._read(self.cli.run_file("lasair-pj-x"))["end"])
        _, out = run(self.cli, "runs", "--json")
        self.assertEqual({r["run_id"] for r in json.loads(out)}, {rid, "lasair-pj-x"})

    def test_bad_names_are_refused(self):
        for argv in (["register", "../x", "r", "lasair", "lm0:9615"],
                     ["register", "n", "r", "lasair", "lm0"],
                     ["register", "n", "r", "lasair", "lm0:1", "--label", "net=x"],
                     ["begin", "a b"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                run(self.cli, *argv)


class Grafana(Base):
    def test_link_carries_the_runs_variables_and_range(self):
        run(self.cli, "register", "n1", "n1-r1", "lasair", "lm0:9615")
        start = self.cli._read(self.cli.run_file("n1-r1"))["start"]
        _, out = run(self.cli, "link", "n1-r1", "-d", "chain")
        self.assertIn("/d/obs-chain?", out)
        for part in ("var-net=n1", "var-run_id=n1-r1", "to=now", "from=%d" % (int(start * 1000) - 60000)):
            self.assertIn(part, out)
        run(self.cli, "end", "n1")
        _, out = run(self.cli, "link", "n1-r1", "-d", "obs-overview")
        self.assertIn("/d/obs-overview?", out)
        self.assertNotIn("to=now", out)

    def test_link_all_lists_every_run_scoped_dashboard(self):
        self.cli.projects = lambda: {}                 # the platform's own, not a sibling's
        _, out = run(self.cli, "link", "n1-r1", "--all")
        uids = [ln.split("/d/")[1].split("?")[0] for ln in out.splitlines()]
        # the platform's run-scoped dashboards (obs-self has no run selector)
        self.assertEqual(sorted(uids), ["obs-chain", "obs-logs", "obs-node", "obs-overview"])

    def test_link_range_is_the_one_the_dashboards_read(self):
        """obs link and the lifecycle service's obs_run_info{link_from, link_to} (which
        the dashboards' "whole run" link reads) share obslib.run_range."""
        rec = {"run_id": "n1-r1", "net": "n1", "start": 1790616968.8, "end": 1790620903.0}
        self.assertEqual(self.cli.obslib.run_range(rec), (1790616908800, 1790620963000))
        self.assertEqual(self.cli.obslib.run_range(dict(rec, end=None)), (1790616908800, "now"))
        self.assertIsNone(self.cli.obslib.run_range(dict(rec, start=None)))

    def test_annotate_tags_the_run_and_its_net(self):
        with recorder() as port:
            cli = load_cli({"OBS_GRAFANA_PORT": str(port)})
            run(cli, "register", "n1", "n1-r1", "lasair", "lm0:9615")
            self.assertEqual(run(cli, "annotate", "n1-r1", "soak start", "--tags", "soak")[0], 0)
            self.assertEqual(run(cli, "annotate", "n1-r1", "verdict", "--tags", "verdict,fail",
                                 "--start", "start", "--end", "1790000000")[0], 0)
        (path, auth, point), (_, _, region) = [(p, a, json.loads(b)) for p, a, b in Recorder.posted]
        self.assertEqual(path, "/api/annotations")
        self.assertTrue(auth.startswith("Basic "))
        self.assertEqual(point["tags"], ["obs", "n1-r1", "n1", "soak", "event"])
        self.assertEqual(region["tags"], ["obs", "n1-r1", "n1", "verdict", "fail"])
        self.assertEqual(region["timeEnd"], 1790000000000)


class Push(Base):
    def test_push_groups_by_job_and_labels(self):
        text = "# TYPE soak_pass gauge\nsoak_pass 1\n"
        with recorder() as port:
            cli = load_cli({"OBS_PUSHGATEWAY_PORT": str(port)})
            path = os.path.join(self.tmp.name, "m.txt")
            with open(path, "w") as fh:
                fh.write(text)
            self.assertEqual(run(cli, "push", "soak", path, "--group", "run_id=lasair-pj-20260928T200000Z",
                                 "--group", "net=lasair-pj")[0], 0)
        (p, _, body), = Recorder.posted
        self.assertEqual(p, "/metrics/job/soak/run_id/lasair-pj-20260928T200000Z/net/lasair-pj")
        self.assertEqual(body.decode(), text)


class Folders(Base):
    def make(self, *parts):
        d = os.path.join(self.tmp.name, *parts)
        os.makedirs(d, exist_ok=True)
        return d

    def test_projects_are_discovered_overridden_and_skipped(self):
        parent = self.make("tree")
        home = self.make("tree", "observability")
        self.make("tree", "lasair", "observability", "dashboards")
        self.make("tree", "graypaper")                       # no dashboards: skipped
        other = self.make("elsewhere", "wt-jamswap", "observability", "dashboards")
        cli = load_cli({"JAMSWAP_DIR": os.path.dirname(os.path.dirname(other))})
        cli.HOME = home
        got = cli.projects()
        self.assertEqual(got, {"lasair": os.path.join(parent, "lasair", "observability", "dashboards"),
                               "jamswap": other})
        # an override to a checkout without dashboards drops that project
        os.environ["LASAIR_DIR"] = self.make("elsewhere", "old-lasair")
        self.assertEqual(sorted(cli.projects()), ["jamswap"])

    def test_generated_providers_and_override(self):
        d = self.make("x", "observability", "dashboards")
        self.cli.write_generated({"lasair": d})
        prov = open(os.path.join(self.state, "grafana", "providers.yml")).read()
        self.assertIn("folder: platform", prov)
        self.assertIn("folder: lasair", prov)
        self.assertIn("path: /etc/grafana/dashboards/lasair", prov)
        override = open(os.path.join(self.state, "compose.projects.yml")).read()
        self.assertIn('source: %s, target: /etc/grafana/dashboards/lasair' % json.dumps(d), override)
        self.cli.write_generated({})
        self.assertIn("volumes: []", open(os.path.join(self.state, "compose.projects.yml")).read())


if __name__ == "__main__":
    unittest.main()
