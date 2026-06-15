"""P8 contested award — proves the part of Contract-Net most likely to be doubted
as decorative: that `select_award` actually DISCRIMINATES between two genuinely
handleable competitors (not a single bidder dressed up as a contest).

The incident is an *ambiguous* one — the exact reason a war room broadcasts a CFP
instead of paging one team: a pod is CrashLoopBackOff-ing, but the root cause is a
corrupted ConfigMap. Two responders can both legitimately act:

  WorkloadResponder (LangGraph)  sees "CrashLoopBackOff" -> offers a rollout undo
  DataResponder     (Pydantic AI) sees "configmap/readiness" -> offers configmap restore
  NetworkResponder  (CrewAI)      sees nothing it owns -> NO-BID

Both real bids flow through their *own* framework control-flow (not a shim) and
the *same* shared deterministic policy. `select_award` (highest confidence, then
lowest blast) must pick the DataResponder — because a rollout would NOT fix a
corrupted ConfigMap; the higher capability-overlap (Jaccard fit) is the correct
call. That is select_award choosing between two real competitors.

This exercises the live responder code paths; it is a pure decision-layer spike,
so it does not perturb the running cluster / public observatory.

Run:  python spikes/p8_contested_award.py
Evidence -> spikes/p8-contested-award.evidence.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP / "agents"))

from common.contract_net import Incident, select_award  # noqa: E402
from common import policy, tools as T                    # noqa: E402
from adapters import get_adapter                         # noqa: E402

HANDLES = {
    "workload": "hokutoman00/workload-responder",
    "network": "hokutoman00/network-responder",
    "data": "hokutoman00/data-responder",
}

# Ambiguous incident: a CrashLoopBackOff whose true cause is a corrupted ConfigMap.
# Tags overlap workload (crashloopbackoff) AND data (configmap/readiness); network
# owns none of them. This is the case that makes the CFP non-decorative.
INCIDENT = Incident(
    "INC-CONTESTED-P8",
    symptom=["CrashLoopBackOff", "readiness"],
    scope=["data"],
    capability_required=["configmap"],
)


def main() -> int:
    bids = []
    per_responder = []
    one_rule_ok = True
    for domain in ("workload", "network", "data"):
        handle = HANDLES[domain]
        domain_tools = T.DOMAIN_TOOLS[domain]
        adapter = get_adapter(domain, handle)
        framework = getattr(adapter, "framework", "native")

        # the bid as produced through the responder's OWN framework control-flow
        bid = adapter.decide(INCIDENT, domain_tools)
        bids.append(bid)

        # "3 runtimes, 1 rule": the framework path must not change the verdict the
        # shared deterministic policy would reach. Compare to policy.make_bid direct.
        ref = policy.make_bid(handle, INCIDENT, domain_tools, framework=framework)
        same = (bid.can_handle == ref.can_handle
                and abs(bid.fit - ref.fit) < 1e-9
                and abs(bid.confidence - ref.confidence) < 1e-9
                and bid.planned_actions == ref.planned_actions)
        one_rule_ok = one_rule_ok and same

        per_responder.append({
            "domain": domain,
            "responder": handle,
            "framework": framework,
            "framework_native": framework != "native",
            "can_handle": bid.can_handle,
            "fit": round(bid.fit, 4),
            "confidence": round(bid.confidence, 4),
            "estimated_blast": bid.estimated_blast,
            "planned_actions": bid.planned_actions,
            "note": bid.note,
            "matches_shared_policy": same,
        })

    winner = select_award(bids)
    yes = [b for b in bids if b.can_handle]
    no = [b for b in bids if not b.can_handle]

    contested = len(yes) >= 2                      # STRICT: >=2 handleable competitors
    winner_is_data = bool(winner and winner.responder_handle == HANDLES["data"])
    # the award genuinely discriminated: winner beat at least one OTHER handleable bid
    discriminated = bool(winner and len(yes) >= 2
                         and any(b.responder_handle != winner.responder_handle
                                 for b in yes))
    # winner is the correct call: highest confidence among the YES bids
    winner_is_highest_conf = bool(
        winner and winner.confidence == max(b.confidence for b in yes))
    # the runner-up could ALSO have acted (it bid YES) — so the choice was real
    runner_up = sorted(
        (b for b in yes if b.responder_handle != (winner.responder_handle if winner else "")),
        key=lambda b: -b.confidence)
    runner_up = runner_up[0] if runner_up else None

    evidence = {
        "spike": "p8-contested-award",
        "incident": INCIDENT.to_metadata(),
        "bids": per_responder,
        "yes_bids": len(yes),
        "no_bids": len(no),
        "award": {
            "winner": winner.responder_handle if winner else None,
            "winner_framework": next((r["framework"] for r in per_responder
                                      if r["responder"] == (winner.responder_handle if winner else "")), None),
            "winner_confidence": round(winner.confidence, 4) if winner else None,
            "winner_fit": round(winner.fit, 4) if winner else None,
            "runner_up": runner_up.responder_handle if runner_up else None,
            "runner_up_confidence": round(runner_up.confidence, 4) if runner_up else None,
            "rule": "highest confidence among handleable bids, tie-broken by lowest blast",
        },
        "checks": {
            "contested_two_handleable": contested,
            "award_discriminated_between_competitors": discriminated,
            "winner_is_data_correct_root_cause": winner_is_data,
            "winner_is_highest_confidence": winner_is_highest_conf,
            "frameworks_heterogeneous": len({r["framework"] for r in per_responder
                                             if r["framework_native"]}) >= 2,
            "one_rule_three_runtimes": one_rule_ok,
        },
    }
    evidence["pass"] = all(evidence["checks"].values())

    out = Path(__file__).with_name("p8-contested-award.evidence.json")
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(f"\n===== P8 {'PASS' if evidence['pass'] else 'FAIL'} =====")
    print("evidence ->", out)
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
