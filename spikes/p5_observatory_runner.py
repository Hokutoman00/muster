"""P5 integration — drive the Observatory incident runner end-to-end on REAL
Band + the REAL muster cluster, capturing the event stream a browser would see.

This proves the observatory backend's hard part (the live bridge) before any
frontend exists: inject -> Commander muster on real Band -> human-gate (auto for
reversible) -> winner remediates the real cluster -> recovery, with cluster /
phase / incident / approval events flowing through the EventBus exactly as the
SSE endpoint serves them. Evidence -> p5-observatory.evidence.json.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP / "agents"))
sys.path.insert(0, str(APP / "cluster"))
sys.path.insert(0, str(APP / "observatory" / "backend"))

from bus import EventBus            # noqa: E402
from runner import Observatory      # noqa: E402

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "workload"


def main() -> int:
    bus = EventBus()
    captured: list[dict] = []

    q = bus.subscribe()

    def drain():
        while True:
            try:
                evt = q.get(timeout=1.0)
            except Exception:  # noqa: BLE001
                if stop.is_set():
                    return
                continue
            captured.append(evt)
            if evt.get("kind") in ("phase", "incident", "approval"):
                tag = evt.get("step") or evt.get("phase") or evt.get("decision")
                print(evt["kind"], tag,
                      json.dumps({k: v for k, v in evt.items()
                                  if k not in ("kind", "id", "ts")},
                                 ensure_ascii=False, default=str)[:200])

    stop = threading.Event()
    obs = Observatory(bus=bus)
    t = threading.Thread(target=drain, daemon=True)
    t.start()

    result = obs.run(DOMAIN)
    time.sleep(1.5)
    stop.set()
    t.join(timeout=3)

    snap = bus.snapshot()
    # success criteria, all observed live:
    phases = [e for e in captured if e.get("kind") == "phase"]
    steps = {e.get("step") for e in phases}
    incidents = [e for e in captured if e.get("kind") == "incident"]
    bid_yes = any(e.get("step") == "bid" and e.get("can_handle") for e in phases)
    demustered = any(e.get("step") == "demustered" and e.get("domains") for e in phases)
    awarded = any(e.get("step") == "awarded" for e in phases)
    executed = any(e.get("step") == "executed" and e.get("ok") for e in phases)
    resolved = any(e.get("phase") == "resolved" and e.get("status") == "resolved"
                   for e in incidents)
    approval_seen = any(e.get("kind") == "approval" for e in captured)

    ok = bool(result.get("ok") and bid_yes and awarded and demustered
              and executed and resolved and approval_seen)

    out = Path(__file__).with_name("p5-observatory.evidence.json")
    out.write_text(json.dumps({
        "pass": ok, "domain": DOMAIN, "result": result,
        "checks": {"bid_yes": bid_yes, "awarded": awarded,
                   "demustered": demustered, "executed": executed,
                   "resolved": resolved, "approval_seen": approval_seen,
                   "phase_steps": sorted(s for s in steps if s)},
        "final_snapshot": snap,
        "events": captured,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("\n===== P5", "PASS" if ok else "FAIL", "=====")
    print("evidence ->", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
