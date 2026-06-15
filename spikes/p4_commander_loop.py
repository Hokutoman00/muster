"""P4 integration — the Commander runs a full Contract-Net cycle on REAL Band
against the REAL muster cluster:

  inject a workload incident -> Commander discovers peers -> signature shortlist
  (top-k) -> opens a muster room + announces CFP -> recruits the shortlist ->
  responders post COMPETING bids (the workload match bids YES, the next-best
  NO-BIDs) -> Commander collects bids from the Band stream -> select_award ->
  @mention award -> DE-MUSTER the loser (remove_participant) -> the winner
  executes its reversible kubectl tool -> cluster recovers.

This proves the orchestrator end-to-end: discovery, dynamic muster (recruit +
prune), real competing bids, deterministic award, real remediation. The bid_driver
pumps the recruited responders' poll loops so their bids land on real Band (in
production each responder runs as its own process). Evidence ->
p4-commander.evidence.json.
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
from common.contract_net import Incident                     # noqa: E402
from common.responder import Responder                       # noqa: E402
from common import tools as T                                 # noqa: E402
from adapters import get_adapter                              # noqa: E402
from commander import Commander, domain_of                    # noqa: E402
import chaos                                                  # noqa: E402

EVIDENCE: list[dict] = []

ENV_PREFIX = {"workload": "WORKLOAD", "network": "NETWORK", "data": "DATA"}

# drive a workload incident: the strong match (workload) bids YES, and the
# next-best by capability (fit=0) is recruited too so a real contest forms.
INCIDENT = Incident("INC-WL-P4", symptom=["CrashLoopBackOff", "rollout"],
                    scope=["workload"], capability_required=["kubectl.rollout.undo"])
TARGET = "payments-api"
# Reuse a stable muster channel: Band caps owned rooms (10) and exposes no
# delete/archive, so per-incident channels are reused (also a stable public URL
# for the observatory). Bid collection is scoped to messages after this CFP.
MUSTER_CHANNEL = "MUSTER INC-WL workload"


def log(step: str, **kw):
    rec = {"step": step, **kw}
    EVIDENCE.append(rec)
    print(step, json.dumps(kw, ensure_ascii=False, default=str)[:240])


def make_bid_driver(env: dict, commander_agent: Agent):
    """Pump each recruited responder's poll loop so its bid lands on real Band."""
    def driver(chat_id: str, invited):
        for c in invited:
            dom = c.domain
            resp_agent = Agent.from_env(ENV_PREFIX[dom], env)
            responder = Responder(
                resp_agent, commander_agent, dom, T.DOMAIN_TOOLS[dom],
                get_adapter(dom, resp_agent.handle),
                client=BandClient(resp_agent, env=env))
            framework = responder.adapter.framework
            out = None
            for _ in range(8):
                out = responder.poll_once(chat_id)
                if out and out.get("action") == "bid":
                    break
                time.sleep(2)
            log("driver.bid", responder=resp_agent.handle, framework=framework, out=out)
    return driver


def execute_winner(env: dict, commander_agent: Agent, chat_id: str,
                   winner_handle: str) -> dict | None:
    """Drive the winning responder to process the award and run its remediation."""
    dom = domain_of(winner_handle)
    resp_agent = Agent.from_env(ENV_PREFIX[dom], env)
    responder = Responder(
        resp_agent, commander_agent, dom, T.DOMAIN_TOOLS[dom],
        get_adapter(dom, resp_agent.handle), client=BandClient(resp_agent, env=env))
    out = None
    for _ in range(8):
        out = responder.poll_once(chat_id)
        if out and out.get("action") == "executed":
            break
        time.sleep(2)
    log("winner.executed", responder=resp_agent.handle, out=out)
    return out


def main() -> int:
    env = load_env()
    commander_agent = Agent.from_env("COMMANDER", env)
    commander = Commander(commander_agent, client=BandClient(commander_agent, env=env),
                          shortlist_k=2)

    ok = False
    chat_id = None
    try:
        # 1. real incident on the real cluster
        chaos.CHAOS["workload"][0]()

        def faulted():
            return (len(chaos.k8s.blast_report().unready) > 0
                    or chaos.k8s.service_endpoint_count("shop", TARGET) == 0)
        observed = any(faulted() or time.sleep(3) for _ in range(20))
        log("chaos_injected", incident=INCIDENT.incident_id, observed=observed)

        # 2. Commander runs the full Contract-Net cycle (discover -> shortlist ->
        #    announce -> recruit -> collect competing bids -> award -> de-muster)
        result = commander.run_incident(
            INCIDENT, chat_title=MUSTER_CHANNEL,
            bid_driver=make_bid_driver(env, commander_agent),
            collect_timeout=30.0, log=log)
        chat_id = result.get("chat_id")
        log("commander.result", result=result)
        if result.get("status") != "awarded":
            raise RuntimeError(f"commander did not award: {result}")

        # this incident exercises discovery -> recruit -> COLLECT competing bids ->
        # prune the loser: >=2 bids land, the workload match bids YES and the
        # next-best NO-BIDs, then the loser is de-mustered. (The distinct case where
        # >=2 responders both bid YES and select_award must DISCRIMINATE between real
        # competitors is proven separately + deterministically in
        # spikes/p8-contested-award.evidence.json — kept out of this live run so it
        # never perturbs the public observatory cluster.)
        bids = result.get("bids", {})
        yes_bids = sum(1 for v in bids.values() if v)
        bids_collected = len(bids) >= 2
        demustered = len(result.get("removed", [])) >= 1
        log("contest_check", bids=bids, bids_collected=bids_collected,
            yes_bids=yes_bids, demustered=demustered)

        # 3. the winner executes the reversible remediation
        exec_out = execute_winner(env, commander_agent, chat_id, result["winner"])
        if not exec_out or exec_out.get("action") != "executed":
            raise RuntimeError(f"winner did not execute: {exec_out}")

        # 4. verify real recovery
        def healthy():
            return (len(chaos.k8s.blast_report().unready) == 0
                    and chaos.k8s.service_endpoint_count("shop", TARGET) > 0)
        recovered = any(healthy() or time.sleep(3) for _ in range(30))
        log("recovered", recovered=recovered)

        ok = bool(observed and bids_collected and demustered and recovered)
    except Exception as e:  # noqa: BLE001
        log("error", error=str(e))
        ok = False
    finally:
        # safety net ONLY on failure (a successful run already reverted via the
        # winner's remediation; a second revert would re-break the cluster).
        if not ok:
            try:
                chaos.CHAOS["workload"][1]()
                log("safety_revert", done=True)
            except Exception:
                pass

    out = Path(__file__).with_name("p4-commander.evidence.json")
    out.write_text(json.dumps({"pass": ok, "chat_id": chat_id, "steps": EVIDENCE},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n===== P4", "PASS" if ok else "FAIL", "=====")
    print("evidence ->", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
