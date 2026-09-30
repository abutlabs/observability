#!/usr/bin/env python3
"""A fake JAM node that serves a few jam_* metrics on port 19615: the smallest thing the
stack can scrape. Lab 3 of Learning Observability registers it
(https://abutlabs.github.io/jam-learning/observability/lesson.html?lesson=exercises/lab-3-register-a-process).

    python3 examples/fake_node.py      # Ctrl-C to stop

Its best slot starts at 100 and grows by one every 6 seconds; its finalized slot trails by
two. It listens on all interfaces so that a container (Alloy) can reach it through
host.docker.internal. Stdlib only.
"""
import http.server
import time

PORT = 19615
START = time.time()


class Node(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        slot = 100 + int((time.time() - START) // 6)
        body = ("# TYPE jam_best_slot gauge\n"
                "jam_best_slot %d\n"
                "# TYPE jam_finalized_slot gauge\n"
                "jam_finalized_slot %d\n"
                "# TYPE jam_node_info gauge\n"
                'jam_node_info{client="fakeclient",client_version="0.0.1",gp_version="0.8.0",spec="tiny"} 1\n'
                % (slot, slot - 2)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("fake node: http://0.0.0.0:%d/metrics (Ctrl-C to stop)" % PORT, flush=True)
    try:
        http.server.HTTPServer(("0.0.0.0", PORT), Node).serve_forever()
    except KeyboardInterrupt:
        pass
