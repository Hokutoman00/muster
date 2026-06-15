"""P3 cross-framework adapter smoke test (offline, no Band, no cluster).

Runs each domain's bid through its REAL framework runtime (LangGraph graph,
CrewAI flow, Pydantic AI agent) and asserts the verdict matches the
dependency-free NativeAdapter baseline. Proves the frameworks genuinely import
and execute, and that heterogeneous runtimes reach an identical, auditable
verdict (the point: substrate-level coordination, framework-agnostic).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP / "agents"))

from common import tools as T                       # noqa: E402
from common.contract_net import Incident            # noqa: E402
from common.responder import NativeAdapter          # noqa: E402
from adapters import get_adapter                     # noqa: E402

SIGS = {
    "workload": Incident("INC-WL", symptom=["CrashLoopBackOff", "rollout"],
                         scope=["workload"], capability_required=["kubectl.rollout.undo"]),
    "network": Incident("INC-NW", symptom=["endpoints", "blackhole", "service"],
                        scope=["network"], capability_required=["service.selector"]),
    "data": Incident("INC-DA", symptom=["readiness", "configmap", "marker"],
                     scope=["data"], capability_required=["configmap.restore"]),
}
HANDLES = {"workload": "wl", "network": "nw", "data": "da"}


def main() -> int:
    results = {}
    ok_all = True
    for domain, incident in SIGS.items():
        tools = T.DOMAIN_TOOLS[domain]
        native = NativeAdapter(HANDLES[domain]).decide(incident, tools)
        fw_adapter = get_adapter(domain, HANDLES[domain])
        fw = fw_adapter.decide(incident, tools)
        match = (fw.can_handle == native.can_handle
                 and abs(fw.fit - native.fit) < 1e-9
                 and fw.planned_actions == native.planned_actions
                 and abs(fw.confidence - native.confidence) < 1e-9)
        ok_all &= match
        rec = {"domain": domain, "framework": fw_adapter.framework,
               "fit": round(fw.fit, 4), "action": fw.planned_actions,
               "confidence": round(fw.confidence, 4),
               "matches_native": match, "note": fw.note}
        results[domain] = rec
        print(f"{domain:9s} {fw_adapter.framework:12s} fit={fw.fit:.2f} "
              f"action={fw.planned_actions} match={match}")
    out = Path(__file__).with_name("p3-adapters-smoke.evidence.json")
    out.write_text(json.dumps({"pass": ok_all, "results": results}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print("\n===== P3 ADAPTERS", "PASS" if ok_all else "FAIL", "=====")
    print("evidence ->", out)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
