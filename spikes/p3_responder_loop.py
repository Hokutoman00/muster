"""P3 integration — drive each responder through the REAL Band message loop and
the REAL muster cluster: inject chaos -> Commander CFP -> responder bids ->
award -> responder executes its reversible kubectl tool -> cluster recovers.

This proves the responder tool layer + adapter + Band loop end-to-end (not just
unit logic). The Commander side here is a minimal inline driver; the full
Commander agent is P4. Evidence is written to p3-responder-loop.evidence.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP / "agents"))
sys.path.insert(0, str(APP / "cluster"))

from common.band_client import Agent, BandClient, load_env  # noqa: E402
from common.contract_net import Incident, select_award         # noqa: E402
from common.responder import Responder                          # noqa: E402
from common import tools as T                                   # noqa: E402
from adapters import get_adapter                                # noqa: E402
import chaos                                                    # noqa: E402

EVIDENCE: list[dict] = []


def log(step: str, **kw):
    rec = {"step": step, **kw}
    EVIDENCE.append(rec)
    print(step, json.dumps(kw, ensure_ascii=False, default=str)[:200])


# incident signatures per domain (what the cluster watcher would emit)
SIGS = {
    "workload": Incident("INC-WL", symptom=["CrashLoopBackOff", "rollout"],
                         scope=["workload"], capability_required=["kubectl.rollout.undo"]),
    "network": Incident("INC-NW", symptom=["endpoints", "blackhole", "service"],
                        scope=["network"], capability_required=["service.selector"]),
    "data": Incident("INC-DA", symptom=["readiness", "configmap", "marker"],
                     scope=["data"], capability_required=["configmap.restore"]),
}

DOMAIN_TARGET = {"workload": "payments-api", "network": "checkout-web", "data": "catalog-svc"}


def run_domain(domain: str, env: dict, commander: Agent, responders: dict[str, Agent]) -> bool:
    incident = SIGS[domain]
    target = DOMAIN_TARGET[domain]
    cmd = BandClient(commander, env=env)

    # 1. real incident on the real cluster
    chaos.CHAOS[domain][0]()
    def faulted():
        return len(chaos.k8s.unready_pods("shop")) > 0 or chaos.k8s.service_endpoint_count("shop", target) == 0
    observed = any(faulted() or time.sleep(3) for _ in range(20))
    log(f"{domain}.chaos_injected", incident=incident.incident_id, observed=observed)

    # 2. Commander opens a muster room and announces the CFP (Contract-Net announce)
    chat_id = cmd.create_chat(f"MUSTER {incident.incident_id} {domain}")
    cmd.create_event(chat_id, "task", f"CFP {incident.incident_id}",
                     metadata=incident.to_metadata())
    log(f"{domain}.cfp_announced", chat_id=chat_id)

    # 3. discovery + dynamic muster: only add the domain's responder (signature-driven)
    resp_agent = responders[domain]
    cmd.add_participant(chat_id, resp_agent.id)
    cmd.create_message(chat_id, f"@{resp_agent.handle} bid please. MUSTER-CFP " +
                       json.dumps(incident.to_metadata()), mentions=[resp_agent])
    log(f"{domain}.recruited", responder=resp_agent.handle)

    # 4. responder bids (its own Band queue + adapter + tools)
    responder = Responder(resp_agent, commander, domain,
                          T.DOMAIN_TOOLS[domain], get_adapter(domain, resp_agent.handle),
                          client=BandClient(resp_agent, env=env))
    log(f"{domain}.framework", framework=responder.adapter.framework)
    bid_out = None
    for _ in range(8):
        bid_out = responder.poll_once(chat_id)
        if bid_out and bid_out.get("action") == "bid":
            break
        time.sleep(2)
    log(f"{domain}.bid", out=bid_out)
    if not bid_out or bid_out.get("action") != "bid":
        return False

    # 5. Commander collects bids and awards (Contract-Net award)
    msgs = cmd.read_messages(chat_id, limit=20)
    bid = responder.adapter.decide(incident, T.DOMAIN_TOOLS[domain])  # same fn the responder ran
    winner = select_award([bid])
    award = {"winner": resp_agent.handle, "tool": winner.planned_actions[0], "args": {}}
    cmd.create_event(chat_id, "task", f"awarded {resp_agent.handle}", metadata=award)
    cmd.create_message(chat_id, f"@{resp_agent.handle} you are awarded. MUSTER-AWARD " +
                       json.dumps(award), mentions=[resp_agent])
    log(f"{domain}.awarded", award=award, bids_seen=len(msgs))

    # 6. responder executes the reversible remediation (emits tool_call/tool_result)
    exec_out = None
    for _ in range(8):
        exec_out = responder.poll_once(chat_id)
        if exec_out and exec_out.get("action") == "executed":
            break
        time.sleep(2)
    log(f"{domain}.executed", out=exec_out)
    if not exec_out or exec_out.get("action") != "executed":
        return False

    # 7. verify real recovery
    def healthy():
        return (len(chaos.k8s.unready_pods("shop")) == 0
                and chaos.k8s.service_endpoint_count("shop", target) > 0)
    recovered = any(healthy() or time.sleep(3) for _ in range(30))
    log(f"{domain}.recovered", recovered=recovered)
    return bool(observed and recovered)


def main() -> int:
    env = load_env()
    commander = Agent.from_env("COMMANDER", env)
    responders = {
        "workload": Agent.from_env("WORKLOAD", env),
        "network": Agent.from_env("NETWORK", env),
        "data": Agent.from_env("DATA", env),
    }
    results = {}
    for domain in ("workload", "network", "data"):
        print(f"\n===== {domain} =====")
        try:
            results[domain] = run_domain(domain, env, commander, responders)
        except Exception as e:  # noqa: BLE001
            log(f"{domain}.error", error=str(e))
            results[domain] = False
        # safety net ONLY on failure: a successful run already reverted via the
        # responder's remediation, so a second revert would re-break the cluster.
        if not results[domain]:
            try:
                chaos.CHAOS[domain][1]()
            except Exception:
                pass
    ok = all(results.values())
    out = Path(__file__).with_name("p3-responder-loop.evidence.json")
    out.write_text(json.dumps({"results": results, "steps": EVIDENCE}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print("\n===== P3", "PASS" if ok else "FAIL", "=====", results)
    print("evidence ->", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
