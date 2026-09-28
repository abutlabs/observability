#!/usr/bin/env python3
"""netjoin: keep the obs collectors on every Docker network that has an observed container.

A net's containers live on the net's own Docker network(s). Grafana Alloy scrapes them by
name, the JIP-2 exporter polls their RPC and the JIP-3 receiver takes their telemetry
connections, so those three must share a network with them. netjoin watches Docker and:

  * joins each collector (alloy, jip2-exporter, jip3-receiver of this obs project) to
    every network that has an observed container (any org.abutlabs.obs.* label) that is
    created, running or restarting;
  * leaves a network as soon as no such container is left on it (on the container's
    `die`, before `docker compose down` removes the network, which it could not do
    while a collector is still attached);
  * finishes a `docker compose down` that lost that race: a compose network the
    collectors left is removed once it is empty and its project has no container left;
  * never touches the obs project's own networks.

Joining happens at the net's first container `create`, before the nodes start, so a
node's first telemetry connection already finds the receiver. On a network with a
configured subnet a collector takes a fixed address from the top of the subnet (the
highest free ones), never a dynamic one: a dynamic address could take the static IP a
net assigns to a node that has not started yet. Each collector gets a network alias,
obs-alloy, obs-jip2 and obs-jip3, so a node reaches the JIP-3 receiver at
obs-jip3:9910 on its own network.

Metrics on :9913 (obs_netjoin_*). Stdlib only (lib/obslib.py).
"""
import ipaddress
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import obslib  # noqa: E402

PROJECT = os.environ.get("OBS_PROJECT", "obs")
# collector service -> the alias it takes on a joined network
JOINERS = {"alloy": "obs-alloy", "jip2-exporter": "obs-jip2", "jip3-receiver": "obs-jip3"}
LIVE_STATES = {"created", "running", "restarting"}
RESYNC_SECS = float(os.environ.get("NETJOIN_RESYNC", "30"))
COALESCE_SECS = 0.3
METRICS_PORT = int(os.environ.get("NETJOIN_METRICS_PORT", "9913"))


def log(msg):
    print("%s netjoin: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---- planning (pure: the tests drive these with plain dicts) ---------------------------
def attachable(mode):
    """Networks a container's NetworkMode can share: not host, none or another container's."""
    return mode not in ("host", "none") and not (mode or "").startswith("container:")


def wanted_networks(containers, project):
    """Networks with an observed, live container of another project."""
    want = set()
    for c in containers:
        labels = obslib.container_labels(c)
        if labels.get(obslib.COMPOSE_PROJECT) == project or not obslib.observed(labels):
            continue
        if (c.get("State") or "").lower() not in LIVE_STATES:
            continue
        if not attachable((c.get("HostConfig") or {}).get("NetworkMode", "")):
            continue
        want.update(obslib.container_networks(c))
    return want


def plan(want, joined, own):
    """(networks to join, networks to leave) for one collector."""
    joined = set(joined)
    return sorted(want - joined - own), sorted(joined - want - own)


def pick_ip(subnet, gateway, used):
    """The highest free host address of an IPv4 subnet, or None (IPv6, or full)."""
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return None
    if net.version != 4 or net.num_addresses < 4:
        return None
    taken = {str(ipaddress.ip_address(u.split("/")[0])) for u in used if u}
    if gateway:
        taken.add(gateway)
    top = int(net.broadcast_address) - 1
    for i in range(min(256, net.num_addresses - 2)):
        ip = str(ipaddress.ip_address(top - i))
        if ip not in taken:
            return ip
    return None


# ---- Docker ------------------------------------------------------------------------------
class NetJoin:
    def __init__(self, docker, metrics):
        self.docker = docker
        self.m_networks = metrics.gauge("obs_netjoin_networks", "Foreign networks a collector is attached to.",
                                        ["service"])
        self.m_actions = metrics.counter("obs_netjoin_actions_total", "Networks joined and left.",
                                         ["service", "action"])
        self.m_errors = metrics.counter("obs_netjoin_errors_total", "Docker API calls that failed.")
        self.m_last = metrics.gauge("obs_netjoin_last_reconcile_timestamp_seconds",
                                    "When netjoin last compared the networks with the containers.")
        self.left = {}                  # network id -> compose project, for cleanup()

    def joiners(self):
        """{service: container} for this project's collectors that exist."""
        out = {}
        for c in self.docker.containers(all=True, filters={"label": [
                "%s=%s" % (obslib.COMPOSE_PROJECT, PROJECT)]}):
            svc = obslib.container_labels(c).get(obslib.COMPOSE_SERVICE)
            if svc in JOINERS:
                out[svc] = c
        return out

    def own_networks(self):
        return {n["Name"] for n in self.docker.get("/networks", filters={
            "label": ["%s=%s" % (obslib.COMPOSE_PROJECT, PROJECT)]}) or []}

    def join(self, network, container, alias):
        info = self.docker.get("/networks/" + network)
        used = [e.get("IPv4Address", "") for e in (info.get("Containers") or {}).values()]
        body = {"Container": container["Id"], "EndpointConfig": {"Aliases": [alias]}}
        for cfg in (info.get("IPAM") or {}).get("Config") or []:
            ip = pick_ip(cfg.get("Subnet", ""), cfg.get("Gateway", ""), used)
            if ip:
                body["EndpointConfig"]["IPAMConfig"] = {"IPv4Address": ip}
                break
        try:
            self.docker.post("/networks/%s/connect" % network, body)
        except obslib.DockerError as e:
            if "IPAMConfig" in body["EndpointConfig"] and "user specified IP" in e.body:
                # an automatic subnet: no net pins addresses on it, a dynamic one is safe
                del body["EndpointConfig"]["IPAMConfig"]
                self.docker.post("/networks/%s/connect" % network, body)
            elif "already exists" in e.body:
                return None
            else:
                raise
        return (body["EndpointConfig"].get("IPAMConfig") or {}).get("IPv4Address", "dynamic")

    def reconcile(self):
        try:
            joiners = self.joiners()
            if not joiners:
                return
            want = wanted_networks(self.docker.containers(all=True), PROJECT)
            own = self.own_networks()
            for svc, c in sorted(joiners.items()):
                if (c.get("State") or "") != "running":
                    continue
                join, leave = plan(want, obslib.container_networks(c), own)
                for n in leave:
                    try:
                        info = self.docker.get("/networks/" + n)
                        project = (info.get("Labels") or {}).get(obslib.COMPOSE_PROJECT)
                        if project:
                            self.left[info["Id"]] = project
                        self.docker.post("/networks/%s/disconnect" % n, {"Container": c["Id"], "Force": True})
                        self.m_actions.labels(service=svc, action="leave").inc()
                        log("%s left %s" % (svc, n))
                    except obslib.DockerError as e:
                        self.m_errors.inc()
                        log("%s cannot leave %s: %s" % (svc, n, e))
                for n in join:
                    try:
                        ip = self.join(n, c, JOINERS[svc])
                        if ip:
                            self.m_actions.labels(service=svc, action="join").inc()
                            log("%s joined %s (%s, alias %s)" % (svc, n, ip, JOINERS[svc]))
                    except obslib.DockerError as e:
                        self.m_errors.inc()
                        log("%s cannot join %s: %s" % (svc, n, e))
                self.m_networks.labels(service=svc).set(len((set(obslib.container_networks(c)) | set(join))
                                                            - set(leave) - own))
            self.cleanup()
            self.m_last.set(time.time())
        except (OSError, obslib.DockerError) as e:
            self.m_errors.inc()
            log("reconcile failed: %s" % e)

    def cleanup(self):
        """Remove the compose networks the collectors left that compose could not remove
        (it tried while a collector was still attached): empty, and no container of their
        project left in any state. Anything else stays."""
        for nid, project in list(self.left.items()):
            try:
                info = self.docker.get("/networks/" + nid)
            except obslib.DockerError as e:
                if e.status == 404:                  # compose removed it: done
                    del self.left[nid]
                    continue
                raise
            if info.get("Containers"):
                continue
            if self.docker.containers(all=True, filters={"label": ["%s=%s" % (obslib.COMPOSE_PROJECT, project)]}):
                continue                             # the project is still there (stopped, not down)
            try:
                self.docker.request("DELETE", "/networks/" + nid)
                self.m_actions.labels(service="netjoin", action="remove").inc()
                log("removed %s, left behind by the down of %s" % (info.get("Name"), project))
            except obslib.DockerError as e:
                if e.status != 404:
                    raise
            del self.left[nid]


def relevant(ev):
    """A container event that can change which networks the collectors need."""
    attrs = (ev.get("Actor") or {}).get("Attributes") or {}
    if attrs.get(obslib.COMPOSE_PROJECT) == PROJECT:
        return attrs.get(obslib.COMPOSE_SERVICE) in JOINERS and ev.get("Action") == "start"
    return obslib.observed(attrs)


def watch(docker, q):
    """Feed relevant Docker events into q; reconnect with backoff when the stream ends."""
    backoff = 1
    while True:
        try:
            for ev in docker.events({"type": ["container"],
                                     "event": ["create", "start", "die", "destroy"]}):
                backoff = 1
                if relevant(ev):
                    q.put(ev)
        except (OSError, obslib.DockerError) as e:
            log("event stream: %s; retrying in %d s" % (e, backoff))
        time.sleep(backoff)
        backoff = min(30, backoff * 2)
        q.put(None)                      # after a gap, compare everything again


def main():
    metrics = obslib.Metrics()
    obslib.serve_http(metrics, METRICS_PORT)
    nj = NetJoin(obslib.Docker(), metrics)
    q = queue.Queue()
    threading.Thread(target=watch, args=(nj.docker, q), daemon=True).start()
    log("project %s, collectors %s" % (PROJECT, ", ".join(sorted(JOINERS))))
    nj.reconcile()
    next_sync = time.time() + RESYNC_SECS
    while True:
        try:
            ev = q.get(timeout=max(0.1, next_sync - time.time()))
            # a stop is urgent (leave before compose removes the network); a compose up
            # is a burst of events, taken together
            if not ev or ev.get("Action") != "die":
                time.sleep(COALESCE_SECS)
            while not q.empty():
                q.get_nowait()
        except queue.Empty:
            pass
        nj.reconcile()
        if time.time() >= next_sync:
            next_sync = time.time() + RESYNC_SECS


if __name__ == "__main__":
    main()
