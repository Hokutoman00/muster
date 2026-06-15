"""Reversible chaos for the MUSTER demo cluster (3 domains).

Each chaos has inject() and revert(); both use kubectl through the shared layer.
The point is that *every* injected fault is fully reversible on the real cluster,
so the demo can be replayed indefinitely (design invariant: cluster is reversible).

CLI:
  python chaos.py status
  python chaos.py inject  workload|network|data
  python chaos.py revert  workload|network|data
  python chaos.py selftest          # inject->observe->revert->verify, all 3 domains
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
from common import k8s  # noqa: E402

NS = "shop"
GOOD_IMAGE = "nginx:1.27-alpine"
BAD_IMAGE = "nginx:doesnotexist-9z9z9z"
GOOD_CONFIG = "MARKER=HEALTHY\ncatalog service config v1 — ok\n"
BAD_CONFIG = "corrupted by chaos — marker removed\n"


def _wait(predicate, timeout=90, interval=3, desc=""):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------- workload
def inject_workload():
    k8s.kubectl("set", "image", "deploy/payments-api", f"web={BAD_IMAGE}", "-n", NS)
    return {"domain": "workload", "action": f"set image -> {BAD_IMAGE}",
            "deployment": "payments-api"}


def revert_workload():
    k8s.kubectl("rollout", "undo", "deploy/payments-api", "-n", NS)
    k8s.kubectl("rollout", "status", "deploy/payments-api", "-n", NS, "--timeout=90s")
    return {"domain": "workload", "action": "rollout undo", "deployment": "payments-api"}


# ---------------------------------------------------------------- network
def inject_network():
    patch = json.dumps({"spec": {"selector": {"app": "nonexistent-selector"}}})
    k8s.kubectl("patch", "svc", "checkout-web", "-n", NS, "-p", patch)
    return {"domain": "network", "action": "patch svc selector -> nonexistent",
            "service": "checkout-web"}


def revert_network():
    patch = json.dumps({"spec": {"selector": {"app": "checkout-web"}}})
    k8s.kubectl("patch", "svc", "checkout-web", "-n", NS, "-p", patch)
    return {"domain": "network", "action": "patch svc selector -> app=checkout-web",
            "service": "checkout-web"}


# ---------------------------------------------------------------- data
def _set_config(content: str):
    patch = json.dumps({"data": {"index.html": content}})
    k8s.kubectl("patch", "configmap", "catalog-config", "-n", NS, "-p", patch)
    k8s.kubectl("rollout", "restart", "deploy/catalog-svc", "-n", NS)


def inject_data():
    _set_config(BAD_CONFIG)
    return {"domain": "data", "action": "patch configmap (remove marker) + rollout restart",
            "deployment": "catalog-svc"}


def revert_data():
    _set_config(GOOD_CONFIG)
    k8s.kubectl("rollout", "status", "deploy/catalog-svc", "-n", NS, "--timeout=90s")
    return {"domain": "data", "action": "restore configmap + rollout restart",
            "deployment": "catalog-svc"}


CHAOS = {
    "workload": (inject_workload, revert_workload, "payments-api"),
    "network": (inject_network, revert_network, "checkout-web"),
    "data": (inject_data, revert_data, "catalog-svc"),
}


def status():
    rep = k8s.blast_report()
    eps = {svc: k8s.service_endpoint_count(NS, svc)
           for svc in ("payments-api", "checkout-web", "catalog-svc")}
    return {"unready_pods": rep.unready, "blast": rep.blast, "endpoints": eps}


def _healthy() -> bool:
    rep = k8s.blast_report()
    eps = all(k8s.service_endpoint_count(NS, s) > 0
              for s in ("payments-api", "checkout-web", "catalog-svc"))
    return len(rep.unready) == 0 and eps


def selftest():
    results = []
    assert _wait(_healthy, timeout=60), "cluster not healthy at baseline"
    print("baseline: healthy")
    for domain, (inject, revert, target) in CHAOS.items():
        print(f"\n=== {domain} ===")
        inj = inject()
        print("  injected:", inj["action"])
        # observe a real incident: either unready pods or a blackholed service
        def faulted():
            rep = k8s.blast_report()
            ep = k8s.service_endpoint_count(NS, target)
            return len(rep.unready) > 0 or ep == 0
        observed = _wait(faulted, timeout=60)
        rep = k8s.blast_report()
        ep = k8s.service_endpoint_count(NS, target)
        print(f"  incident observed={observed} unready={len(rep.unready)} {target}.endpoints={ep}")
        rev = revert()
        print("  reverted:", rev["action"])
        recovered = _wait(_healthy, timeout=90)
        print(f"  recovered={recovered}")
        results.append({"domain": domain, "incident_observed": observed,
                        "recovered": recovered})
    ok = all(r["incident_observed"] and r["recovered"] for r in results)
    print("\n=== SELFTEST", "PASS" if ok else "FAIL", "===")
    print(json.dumps(results, indent=2))
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif cmd == "selftest":
        sys.exit(0 if selftest() else 1)
    elif cmd in ("inject", "revert"):
        domain = sys.argv[2]
        fn = CHAOS[domain][0 if cmd == "inject" else 1]
        print(json.dumps(fn(), indent=2, ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(2)
