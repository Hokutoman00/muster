"""Observatory incident runner — drives the REAL Contract-Net cycle and turns it
into a live event stream for the browser.

It reuses the exact machinery proven in P4 (Commander.run_incident + the bid_driver
pattern + execute_winner), but:
  - emits structured `phase` events (discovered / recruited / bid / awarded /
    demustered / executing / executed) instead of printing,
  - runs a real human-approval gate before any destructive remediation (reversible
    tools auto-pass; destructive tools block until POST /api/approve),
  - keeps the Band agent keys server-side only — the browser never sees a token.

One incident runs at a time (a lock guards the shared cluster). The cycle injects
a real fault, lets the Commander muster + award + de-muster on real Band, the
winner runs its reversible kubectl tool on the real cluster, then verifies
recovery. On any failure a safety-net revert restores the cluster.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from common import tools as T
from common.band_client import Agent, BandClient, load_env
from common.contract_net import Incident
from common.responder import Responder
from adapters import get_adapter
from commander import Commander, domain_of

import chaos

from bus import EventBus

ENV_PREFIX = {"workload": "WORKLOAD", "network": "NETWORK", "data": "DATA"}

# namespaces a naive operator still leaves alone (cluster infra, not business scope)
INFRA_NS_PREFIXES = ("kube-", "local-path-storage")
PAYMENTS_REPLICAS = 2  # baseline from cluster/manifests/10-payments.yaml

# one stable, reusable muster channel per domain (Band caps owned rooms and has no
# delete; reuse also gives the observatory a stable public URL per incident type).
# Band caps owned rooms at 10 and exposes no delete/archive API, so the muster
# channels are REUSED by exact title (this also gives each channel a stable public
# URL). These titles match the rooms already provisioned during P1-P4 so no new
# room is ever created at runtime — see Commander.get_or_create_chat.
CHANNELS = {
    "workload": "MUSTER INC-WL workload",
    "network": "MUSTER INC-NW network",
    "data": "MUSTER INC-DA data",
}

# each incident's signature is tuned so score_fit shortlists the matching domain
# (strong YES) plus a next-best (NO-BID) — a genuine contest, not a walkover.
INCIDENTS = {
    "workload": Incident("INC-WL", symptom=["CrashLoopBackOff", "rollout"],
                         scope=["workload"], capability_required=["kubectl.rollout.undo"]),
    "network": Incident("INC-NET", symptom=["blackhole", "endpoints"],
                        scope=["network"], capability_required=["selector"]),
    # CrashLoopBackOff is a genuinely ambiguous symptom: a corrupted ConfigMap
    # crashes the pod, so the workload generalist can also bid (a blunt rollout)
    # while the data specialist offers the targeted config restore. The Commander
    # must discriminate — a real Contract-Net contest, not a single self-selection.
    "data": Incident("INC-DATA", symptom=["readiness", "marker", "CrashLoopBackOff"],
                     scope=["data"], capability_required=["config"]),
}


class ApprovalGate:
    """A real human-in-the-loop key. Reversible tools pass automatically; a
    destructive tool blocks here until a human POSTs /api/approve (or denies)."""

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._event = threading.Event()
        self._approved = False
        self._pending: dict | None = None

    def request(self, incident_id: str, domain: str, tool_name: str,
                destructive: bool, timeout: float = 120.0) -> bool:
        if not destructive:
            self.bus.publish("approval", incident_id=incident_id, domain=domain,
                             tool=tool_name, destructive=False, auto=True,
                             resolved=True, decision="auto (reversible)")
            return True
        # destructive: surface a pending request and wait for a human decision
        self._event.clear()
        self._approved = False
        self._pending = {"incident_id": incident_id, "domain": domain,
                         "tool": tool_name}
        self.bus.publish("approval", incident_id=incident_id, domain=domain,
                         tool=tool_name, destructive=True, auto=False,
                         resolved=False, decision="awaiting human key")
        granted = self._event.wait(timeout) and self._approved
        self.bus.publish("approval", incident_id=incident_id, domain=domain,
                         tool=tool_name, destructive=True, resolved=True,
                         decision="approved" if granted else "denied/timeout")
        self._pending = None
        return granted

    def resolve(self, approve: bool) -> bool:
        if self._pending is None:
            return False
        self._approved = approve
        self._event.set()
        return True


class Observatory:
    def __init__(self, bus: EventBus | None = None):
        self.bus = bus or EventBus()
        self.env = load_env()
        self.commander_agent = Agent.from_env("COMMANDER", self.env)
        self.gate = ApprovalGate(self.bus)
        self._lock = threading.Lock()
        self._running = False
        self._stop_cluster = threading.Event()

    # ------------------------------------------------------------- cluster poll
    def start_cluster_poll(self, interval: float = 4.0) -> None:
        def loop():
            while not self._stop_cluster.is_set():
                try:
                    self.bus.publish("cluster", status=chaos.status())
                except Exception as e:  # noqa: BLE001 — keep the stream alive
                    self.bus.publish("cluster", error=str(e))
                self._stop_cluster.wait(interval)
        threading.Thread(target=loop, name="cluster-poll", daemon=True).start()

    def stop(self) -> None:
        self._stop_cluster.set()

    # ------------------------------------------------------------- one cycle
    def run(self, domain: str) -> dict:
        if domain not in INCIDENTS:
            raise ValueError(f"unknown domain {domain!r}")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("an incident is already in progress")
        try:
            self._running = True
            return self._run_locked(domain)
        finally:
            self._running = False
            self._lock.release()

    @property
    def busy(self) -> bool:
        return self._running

    # ------------------------------------------------------------- naive control
    # The anti-pattern MUSTER is measured against (P6 / 受賞関数). A single
    # full-cluster-access operator with NO RBAC scope and NO human gate, run on the
    # SAME real workload fault and measured by the SAME blast machinery. The judge
    # toggles this from the public URL and watches the blast radius diverge live.
    def run_naive(self) -> dict:
        """Run the naive single-agent remediation on the workload incident.

        Naive mode is workload-only: that is the canonical contrast where an
        unscoped operator mutates the off-limits `billing` namespace (blast > 0)
        and runs a destructive op with no human key — exactly what the hardened
        muster prevents. The cluster is always restored to baseline afterward.
        """
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("an incident is already in progress")
        try:
            self._running = True
            return self._run_naive_locked()
        finally:
            self._running = False
            self._lock.release()

    def _app_namespaces(self) -> list[str]:
        """Every namespace a naive agent treats as fair game (skips k8s infra)."""
        data = chaos.k8s.kubectl_json("get", "ns")
        return [it["metadata"]["name"] for it in data.get("items", [])
                if not any(it["metadata"]["name"].startswith(p)
                           for p in INFRA_NS_PREFIXES)]

    def _restore_baseline(self) -> bool:
        """Deterministic restore (no dependence on rollout history): pin the
        known-good image + replica count for payments-api, then wait for health."""
        k8s = chaos.k8s
        ns = k8s.REMEDIABLE_NS
        k8s.kubectl("set", "image", "deploy/payments-api", f"web={chaos.GOOD_IMAGE}",
                    "-n", ns, check=False)
        k8s.kubectl("scale", "deploy/payments-api", f"--replicas={PAYMENTS_REPLICAS}",
                    "-n", ns, check=False)
        k8s.kubectl("rollout", "status", "deploy/payments-api", "-n", ns,
                    "--timeout=120s", check=False)
        return any(self._healthy() or time.sleep(3) for _ in range(20))

    def _healthy(self) -> bool:
        k8s = chaos.k8s
        rep = k8s.blast_report()
        eps = all(k8s.service_endpoint_count(k8s.REMEDIABLE_NS, s) > 0
                  for s in ("payments-api", "checkout-web", "catalog-svc"))
        return len(rep.unready) == 0 and eps

    def _run_naive_locked(self) -> dict:
        k8s = chaos.k8s
        domain, target = "workload", "payments-api"
        incident = INCIDENTS[domain]
        self.bus.publish("incident", phase="start", domain=domain, mode="naive",
                         incident_id=incident.incident_id, target=target,
                         status="injecting (naive control)")
        # reset the muster graph: there is no muster in naive mode — show the lone
        # operator so the judge sees the structural difference, not just numbers.
        self.bus.publish("phase", step="discovered", domains=[], n=0)

        offlimits_before = k8s.namespace_fingerprint(k8s.OFFLIMITS_NS)
        ok = False
        try:
            if not self._healthy():
                self._restore_baseline()

            # 1. the SAME real fault the hardened path handles
            chaos.inject_workload()

            def faulted():
                return len(k8s.blast_report().unready) > 0
            observed = any(faulted() or time.sleep(3) for _ in range(20))
            self.bus.publish("incident", phase="injected", domain=domain, mode="naive",
                             incident_id=incident.incident_id, observed=observed,
                             status="naive operator: full cluster access, no human gate")

            self.bus.publish("phase", step="triage",
                             note="naive single agent — no muster, no RBAC scope, "
                                  "no human approval")

            # 2a. "restart everything to clear the bad state": every app deployment
            # in every namespace, incl. the off-limits billing/ledger canary.
            touched = []
            for ns in self._app_namespaces():
                if not k8s.kubectl_json("get", "deploy", "-n", ns).get("items", []):
                    continue
                k8s.kubectl("rollout", "restart", "deployment", "-n", ns)
                touched.append(ns)
            self.bus.publish("phase", step="executing", domain=domain,
                             tool=f"rollout restart deployment in {touched} "
                                  f"(no scope → includes off-limits billing)",
                             destructive=False)

            # 2b. destructive scale-to-zero with NO human key (the gate the hardened
            # path enforces is simply absent here).
            self.bus.publish("phase", step="executing", domain=domain,
                             tool="scale deploy/payments-api --replicas=0",
                             destructive=True)
            k8s.kubectl("scale", "deploy/payments-api", "--replicas=0",
                        "-n", k8s.REMEDIABLE_NS, check=False)
            self.bus.publish("phase", step="executed", domain=domain,
                             tool="scale --replicas=0",
                             result="DESTRUCTIVE op ran with NO human approval", ok=False)

            # 3. measure with the SAME blast machinery the hardened path uses
            rep = k8s.blast_report(offlimits_before)
            recovered = any(self._healthy() or time.sleep(3) for _ in range(10))
            self.bus.publish("incident", phase="resolved", domain=domain, mode="naive",
                             incident_id=incident.incident_id, recovered=recovered,
                             blast=rep.blast, unready=len(rep.unready),
                             offlimits_touched=", ".join(rep.offlimits_touched) or "none",
                             destructive_unapproved=1,
                             status="naive: off-limits touched · destructive ran "
                                    "unapproved · incident NOT recovered")
        except Exception as e:  # noqa: BLE001
            self.bus.publish("incident", phase="error", domain=domain, mode="naive",
                             incident_id=incident.incident_id, error=str(e),
                             status="failed")
        finally:
            # cluster invariant: always reversible — restore baseline
            restored = self._restore_baseline()
            self.bus.publish("incident", phase="restored", domain=domain, mode="naive",
                             incident_id=incident.incident_id, restored=restored,
                             status="restored to baseline")
        return {"ok": ok, "mode": "naive", "domain": domain}

    # ------------------------------------------------------------- internals
    def _client(self, agent: Agent) -> BandClient:
        return BandClient(agent, env=self.env)

    def _bid_driver(self, chat_id: str, invited):
        for c in invited:
            dom = c.domain
            resp_agent = Agent.from_env(ENV_PREFIX[dom], self.env)
            responder = Responder(
                resp_agent, self.commander_agent, dom, T.DOMAIN_TOOLS[dom],
                get_adapter(dom, resp_agent.handle), client=self._client(resp_agent))
            for _ in range(8):
                out = responder.poll_once(chat_id)
                if out and out.get("action") == "bid":
                    can = "NO-BID" not in (out.get("bid") or "")
                    self.bus.publish("phase", step="bid", domain=dom,
                                     can_handle=can, summary=out.get("bid", "")[:160])
                    break
                time.sleep(2)

    def _execute_winner(self, chat_id: str, incident: Incident, winner_handle: str,
                        tool_name: str) -> dict | None:
        dom = domain_of(winner_handle)
        tool = next((t for t in T.DOMAIN_TOOLS[dom] if t.name == tool_name), None)
        destructive = bool(tool and tool.destructive)
        self.bus.publish("phase", step="executing", domain=dom, tool=tool_name,
                         destructive=destructive)
        # real human-in-the-loop gate before the remediation touches the cluster
        ack = self.gate.request(incident.incident_id, dom, tool_name, destructive)
        if destructive and not ack:
            self.bus.publish("phase", step="executed", domain=dom, tool=tool_name,
                             result="blocked: human approval denied", ok=False)
            return {"action": "blocked"}
        resp_agent = Agent.from_env(ENV_PREFIX[dom], self.env)
        responder = Responder(
            resp_agent, self.commander_agent, dom, T.DOMAIN_TOOLS[dom],
            get_adapter(dom, resp_agent.handle), client=self._client(resp_agent))
        out = None
        # the remediation runs under the responder's namespaced ServiceAccount —
        # real cluster RBAC, so an out-of-scope call is rejected by the API server.
        for _ in range(8):
            with T.k8s.scoped():
                out = responder.poll_once(chat_id, human_ack=ack)
            if out and out.get("action") == "executed":
                break
            time.sleep(2)
        if out and out.get("action") == "executed":
            self.bus.publish("phase", step="executed", domain=dom, tool=tool_name,
                             result=out.get("result", ""), ok=True)
        return out

    def _run_locked(self, domain: str) -> dict:
        incident = INCIDENTS[domain]
        target = chaos.CHAOS[domain][2]
        self.bus.publish("incident", phase="start", domain=domain,
                         incident_id=incident.incident_id, target=target,
                         status="injecting")

        commander = Commander(self.commander_agent,
                              client=self._client(self.commander_agent), shortlist_k=2)
        ok = False
        chat_id = None
        try:
            # 1. real fault on the real cluster
            chaos.CHAOS[domain][0]()

            def faulted():
                return (len(chaos.k8s.blast_report().unready) > 0
                        or chaos.k8s.service_endpoint_count("shop", target) == 0)
            observed = any(faulted() or time.sleep(3) for _ in range(20))
            self.bus.publish("incident", phase="injected", domain=domain,
                             incident_id=incident.incident_id, observed=observed,
                             status="mustering")

            # 2. Commander runs the full Contract-Net cycle on real Band
            def log(step, **kw):
                self._on_phase(domain, step, kw)
            result = commander.run_incident(
                incident, chat_title=CHANNELS[domain],
                bid_driver=self._bid_driver, collect_timeout=30.0, log=log)
            chat_id = result.get("chat_id")
            if result.get("status") != "awarded":
                raise RuntimeError(f"commander did not award: {result}")

            bids = result.get("bids", {})
            # a genuine Contract-Net contest = at least two responders that can
            # actually handle the incident competing for the award (not one bid +
            # a crowd of NO-BIDs). The Commander then has a real discrimination to
            # make. This is the honest meaning of the "contested" badge in the UI.
            contested = sum(1 for v in bids.values() if v) >= 2
            self.bus.publish("incident", phase="awarded", domain=domain,
                             incident_id=incident.incident_id,
                             winner=result["winner"], tool=result["tool"],
                             contested=contested, status="remediating")

            # 3. winner executes (through the human-approval gate)
            exec_out = self._execute_winner(chat_id, incident, result["winner"],
                                            result["tool"])
            if not exec_out or exec_out.get("action") != "executed":
                raise RuntimeError(f"winner did not execute: {exec_out}")

            # 4. verify real recovery
            def healthy():
                return (len(chaos.k8s.blast_report().unready) == 0
                        and chaos.k8s.service_endpoint_count("shop", target) > 0)
            recovered = any(healthy() or time.sleep(3) for _ in range(30))
            # success = we saw the real fault and the awarded responder really
            # recovered the cluster. "contested" is a property of the auction
            # (reported separately), not a precondition for a correct remediation:
            # a clean single-specialist match is still a success.
            ok = bool(observed and recovered)
            self.bus.publish("incident", phase="resolved", domain=domain,
                             incident_id=incident.incident_id, recovered=recovered,
                             contested=contested, chat_id=chat_id,
                             status="resolved" if ok else "failed")
        except Exception as e:  # noqa: BLE001
            self.bus.publish("incident", phase="error", domain=domain,
                             incident_id=incident.incident_id, error=str(e),
                             status="failed")
        finally:
            if not ok:
                try:
                    chaos.CHAOS[domain][1]()
                    self.bus.publish("incident", phase="safety_revert", domain=domain,
                                     incident_id=incident.incident_id, status="reverted")
                except Exception:
                    pass
        return {"ok": ok, "chat_id": chat_id, "domain": domain}

    def _on_phase(self, domain: str, step: str, kw: dict) -> None:
        """Translate Commander log steps into observatory phase events."""
        if step == "discovered":
            doms = [domain_of(h) for h in kw.get("handles", [])]
            self.bus.publish("phase", step="discovered",
                             domains=[d for d in doms if d], n=kw.get("n", 0))
        elif step == "triage":
            self.bus.publish("phase", step="triage", note=kw.get("note", ""))
        elif step == "muster_room":
            self.bus.publish("phase", step="muster_room", chat_id=kw.get("chat_id"),
                             title=kw.get("title"), created=kw.get("created"))
        elif step == "recruited":
            doms = [domain_of(h) for h in kw.get("invited", [])]
            self.bus.publish("phase", step="recruited",
                             domains=[d for d in doms if d])
        elif step == "bids":
            self.bus.publish("phase", step="bids", lines=kw.get("lines", []))
        elif step == "awarded":
            award = kw.get("award", {})
            self.bus.publish("phase", step="awarded",
                             winner=award.get("winner", ""),
                             domain=domain_of(award.get("winner", "")),
                             tool=award.get("tool", ""))
        elif step == "demustered":
            doms = [domain_of(h) for h in kw.get("removed", [])]
            self.bus.publish("phase", step="demustered",
                             domains=[d for d in doms if d])
        elif step == "escalated":
            self.bus.publish("phase", step="escalated", reason=kw.get("reason", ""))
