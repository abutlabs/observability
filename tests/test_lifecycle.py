"""lifecycle/lifecycle.py: why a container stopped, its lines and metrics, and the runs as
metrics. Hermetic: Docker events are plain dicts, run records a temporary directory.

    python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.join(REPO, "lifecycle"))
import obslib  # noqa: E402
import lifecycle  # noqa: E402

P = obslib.PREFIX
T0 = 1790620000.0
LABELS = {P + "net": "lasair-pj", P + "run_id": "lasair-pj-r1", P + "client": "lasair",
          P + "scrape": "true", "com.docker.compose.project": "lasair-pj",
          "com.docker.compose.service": "lm1", "name": "lasair-pj-lm1-1", "image": "lasair:x"}


def ev(action, t, cid="c1", **attrs):
    a = dict(LABELS)
    a.update({k: str(v) for k, v in attrs.items()})
    return {"Type": "container", "Action": action, "timeNano": int(t * 1e9),
            "Actor": {"ID": cid, "Attributes": a}}


class Harness:
    def __init__(self, runs=None):
        self.lines, self.runs = [], dict(runs or {})
        self.metrics = obslib.Metrics()
        self.t = lifecycle.Tracker(self.metrics, self.emit, self.runs.get)

    def emit(self, stream, ns, line):
        self.lines.append((dict(stream), ns, json.loads(line)))

    def feed(self, *events):
        for e in events:
            self.t.event(e)

    def text(self):
        return self.metrics.render()

    def dies(self):
        return [(s, b) for s, _, b in self.lines if b["event"] == "die"]


class Classify(unittest.TestCase):
    def test_reasons(self):
        c = lifecycle.classify
        self.assertEqual(c(137, ["9"], True, False, False, False), "oom")
        self.assertEqual(c(0, ["15"], False, True, True, False), "teardown")
        self.assertEqual(c(0, ["15"], False, True, False, True), "teardown")
        self.assertEqual(c(0, ["15"], False, True, False, False), "stopped")
        self.assertEqual(c(137, ["9"], False, False, True, False), "killed")
        self.assertEqual(c(3, [], False, False, False, False), "crashed")
        self.assertEqual(c(0, [], False, False, False, False), "exited")

    def test_the_sentences_name_the_signal_and_the_exit_code(self):
        self.assertEqual(lifecycle.describe("killed", 137, ["9"], True),
                         "killed by SIGKILL (docker kill), exit code 137, while its run was active")
        self.assertIn("SIGKILL after the stop timed out",
                      lifecycle.describe("teardown", 137, ["15", "9"], False))


class Events(unittest.TestCase):
    def test_docker_kill_mid_run_is_a_red_stop_with_its_exit_code(self):
        h = Harness({"lasair-pj-r1": {"run_id": "lasair-pj-r1", "net": "lasair-pj", "start": T0 - 600}})
        h.feed(ev("create", T0), ev("start", T0 + 1), ev("kill", T0 + 100, signal=9),
               ev("die", T0 + 100.1, exitCode=137))
        self.assertIn('obs_container_running{net="lasair-pj",run_id="lasair-pj-r1",node="lm1",client="lasair",'
                      'job="lm1",container="lasair-pj-lm1-1"} 0', h.text())
        self.assertEqual(h.dies(), [])                  # settled later: a stop may still come
        h.t.tick(T0 + 100.1 + lifecycle.SETTLE_SECS)
        (stream, body), = h.dies()
        self.assertEqual(stream, {"source": "lifecycle", "kind": "container", "net": "lasair-pj",
                                  "run_id": "lasair-pj-r1", "node": "lm1", "client": "lasair", "job": "lm1",
                                  "container": "lasair-pj-lm1-1"})
        self.assertEqual((body["reason"], body["exit_code"], body["signal"], body["during_run"]),
                         ("killed", 137, "SIGKILL", True))
        self.assertIn("lm1 killed by SIGKILL", body["msg"])
        self.assertIn('reason="killed",during_run="true"} %d' % (T0 + 100.1), h.text())
        self.assertIn('obs_container_last_exit_code{', h.text())

    def test_compose_down_is_a_teardown_in_either_event_order(self):
        for order in (("kill", "stop", "die"), ("kill", "die", "stop")):   # Docker 29, older engines
            h = Harness()
            h.feed(ev("start", T0))
            for i, a in enumerate(order):
                h.feed(ev(a, T0 + 50 + i * 0.1, signal=15, exitCode=0))
            h.feed(ev("destroy", T0 + 51))
            (_, body), = h.dies()
            self.assertEqual((body["reason"], body["during_run"]), ("teardown", False), order)
            self.assertEqual([b["event"] for _, _, b in h.lines][-1], "destroy")

    def test_a_run_ended_just_after_the_die_makes_it_a_teardown(self):
        run = {"run_id": "lasair-pj-r1", "net": "lasair-pj", "start": T0, "end": T0 + 115}
        h = Harness({"lasair-pj-r1": run})
        h.feed(ev("start", T0), ev("kill", T0 + 100, signal=15), ev("stop", T0 + 100),
               ev("die", T0 + 100.2, exitCode=143))
        h.t.tick(T0 + 200)
        (_, body), = h.dies()
        self.assertEqual((body["reason"], body["during_run"]), ("teardown", False))

    def test_a_crash_restarted_by_policy_counts_a_restart_and_clears_the_stop(self):
        h = Harness()
        h.feed(ev("start", T0), ev("die", T0 + 10, exitCode=3), ev("start", T0 + 11))
        (_, body), = h.dies()
        self.assertEqual((body["reason"], body["exit_code"], body["during_run"]), ("crashed", 3, True))
        self.assertIn("obs_container_restarts_total{", h.text())
        self.assertNotIn("obs_container_stopped_timestamp_seconds{", h.text())
        self.assertEqual([b["msg"] for _, _, b in h.lines][-1], "lm1 restarted")

    def test_oom(self):
        h = Harness()
        h.feed(ev("start", T0), ev("oom", T0 + 5), ev("kill", T0 + 5, signal=9),
               ev("die", T0 + 5, exitCode=137), ev("destroy", T0 + 6))
        self.assertEqual(h.dies()[0][1]["reason"], "oom")

    def test_unobserved_containers_are_ignored(self):
        h = Harness()
        h.t.event({"Action": "die", "timeNano": 1, "Actor": {"ID": "x", "Attributes": {"name": "redis"}}})
        self.assertEqual((h.lines, h.t.c), ([], {}))

    def test_a_removed_containers_series_go_unless_its_name_lives_on(self):
        h = Harness()
        h.feed(ev("start", T0), ev("die", T0 + 1, exitCode=0), ev("destroy", T0 + 2))
        h.t.tick(T0 + 2 + lifecycle.DROP_AFTER)
        self.assertNotIn("lasair-pj-lm1-1", h.text())
        h.feed(ev("start", T0 + 3000, cid="old"), ev("die", T0 + 3001, cid="old", exitCode=0),
               ev("destroy", T0 + 3002, cid="old"), ev("create", T0 + 3002, cid="new"))
        h.t.tick(T0 + 3002 + lifecycle.DROP_AFTER)
        self.assertIn("lasair-pj-lm1-1", h.text())      # recreated under the same name

    def test_stats(self):
        h = Harness()
        h.feed(ev("start", T0))
        h.t.stats(h.t.c["c1"], {"cpu_stats": {"cpu_usage": {"total_usage": 2500000000}},
                                "memory_stats": {"usage": 1000, "stats": {"inactive_file": 400}}})
        self.assertIn('obs_container_cpu_seconds_total{net="lasair-pj"', h.text())
        self.assertIn('container="lasair-pj-lm1-1"} 2.5', h.text())
        self.assertIn('container="lasair-pj-lm1-1"} 600', h.text())


class Runs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lines = []
        self.metrics = obslib.Metrics()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rec):
        with open(os.path.join(self.tmp.name, rec["run_id"] + ".json"), "w") as fh:
            json.dump(rec, fh)

    def runs(self, loki=None):
        return lifecycle.Runs(self.metrics, lambda s, ns, l: self.lines.append((s, ns, json.loads(l))),
                              self.tmp.name, loki)

    def test_records_become_metrics_with_the_whole_run_link(self):
        self.write({"run_id": "n-r1", "net": "n", "kind": "docker", "start": T0, "end": T0 + 3600})
        self.write({"run_id": "n-r2", "net": "n", "kind": "native", "start": T0 + 4000, "end": None})
        r = self.runs()
        r.sync(T0 + 5000)
        text = self.metrics.render()
        self.assertIn('obs_run_info{net="n",run_id="n-r1",kind="docker",link_from="%d",link_to="%d"} 1'
                      % (T0 * 1000 - 60000, (T0 + 3600) * 1000 + 60000), text)
        self.assertIn('obs_run_info{net="n",run_id="n-r2",kind="native",link_from="%d",link_to="now"} 1'
                      % ((T0 + 4000) * 1000 - 60000), text)
        self.assertIn('obs_run_end_timestamp_seconds{net="n",run_id="n-r1"} %d' % (T0 + 3600), text)
        self.assertNotIn('obs_run_end_timestamp_seconds{net="n",run_id="n-r2"}', text)
        self.assertEqual(sorted((s["run_id"], b["event"], ns) for s, ns, b in self.lines),
                         [("n-r1", "run_begin", int(T0 * 1e9)), ("n-r1", "run_end", int((T0 + 3600) * 1e9)),
                          ("n-r2", "run_begin", int((T0 + 4000) * 1e9))])
        r.sync(T0 + 5005)                               # announced once
        self.assertEqual(len(self.lines), 3)

    def test_old_runs_leave_and_lines_already_in_loki_are_not_pushed_again(self):
        self.write({"run_id": "n-old", "net": "n", "start": T0 - 40 * 86400, "end": T0 - 39 * 86400})
        self.write({"run_id": "n-r1", "net": "n", "start": T0, "end": None})
        answers = iter([None, True])                    # Loki down, then it has the line
        r = self.runs(lambda stream, ns, event: next(answers))
        r.sync(T0 + 10)
        self.assertNotIn("n-old", self.metrics.render())
        self.assertEqual(self.lines, [])
        r.sync(T0 + 15)
        self.assertEqual(self.lines, [])
        self.assertIn(("n-r1", "run_begin"), r.announced)


if __name__ == "__main__":
    unittest.main()
