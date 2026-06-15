"""Responder tool layer — reversible kubectl remediations, RBAC-scoped.

Each responder owns a small set of tools. Every tool:
  - declares capability tags (used by Contract-Net score_fit),
  - is RBAC-scoped: it may only touch resources in REMEDIABLE_NS that match its
    allowed (kind, name) allowlist — never the off-limits namespace,
  - is reversible by construction (it *restores* desired state), and
  - records a snapshot before mutating so a manual revert is always possible.

A DESTRUCTIVE tool (scale-to-zero, delete) does NOT execute without a human ack;
`pre_destroy_guard` raises PermissionError otherwise. The naive control agent
(P6) deliberately skips this guard so the blast-radius difference is real.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from . import k8s

NS = k8s.REMEDIABLE_NS  # "shop"


class ScopeViolation(PermissionError):
    pass


@dataclass
class ToolResult:
    tool: str
    ok: bool
    detail: str
    snapshot_ref: str = ""
    destructive: bool = False


@dataclass
class Tool:
    name: str
    domain: str
    capability_tags: list[str]
    # (kind, name) pairs this tool is allowed to touch in REMEDIABLE_NS
    allowed: list[tuple[str, str]]
    run: Callable[..., ToolResult]
    destructive: bool = False
    description: str = ""

    def guarded_run(self, *, human_ack: bool = False, **kwargs) -> ToolResult:
        if self.destructive and not human_ack:
            raise PermissionError(
                f"{self.name} is destructive and requires human approval (no ack present)")
        return self.run(**kwargs)


def pre_destroy_guard(tool: Tool, human_ack: bool) -> None:
    """Raise unless a destructive tool carries a human acknowledgement."""
    if tool.destructive and not human_ack:
        raise PermissionError(f"destructive tool {tool.name} blocked: needs human ack")


def _assert_scope(allowed: list[tuple[str, str]], kind: str, name: str) -> None:
    if (kind, name) not in allowed:
        raise ScopeViolation(
            f"out-of-scope: tool may not touch {kind}/{name} (allowed={allowed})")


# ------------------------------------------------------------------ workload
def _rollout_undo(deployment: str = "payments-api") -> ToolResult:
    _assert_scope(WORKLOAD_ALLOWED, "deploy", deployment)
    snap = k8s.snapshot("deploy", deployment, NS)
    k8s.kubectl("rollout", "undo", f"deploy/{deployment}", "-n", NS)
    k8s.kubectl("rollout", "status", f"deploy/{deployment}", "-n", NS, "--timeout=90s")
    return ToolResult("rollout_undo", True,
                      f"rolled deploy/{deployment} back to previous ReplicaSet",
                      snapshot_ref=f"deploy/{deployment}@pre-undo")


WORKLOAD_ALLOWED = [("deploy", "payments-api")]


# ------------------------------------------------------------------ network
def _restore_selector(service: str = "checkout-web") -> ToolResult:
    _assert_scope(NETWORK_ALLOWED, "svc", service)
    # correct selector is declared on the Service annotation (single source of truth)
    svc = k8s.kubectl_json("get", "svc", service, "-n", NS)
    ann = svc["metadata"].get("annotations", {}).get("muster.correct-selector", "")
    key, _, val = ann.partition("=")
    if not key:
        return ToolResult("restore_selector", False, "no correct-selector annotation")
    snap = k8s.snapshot("svc", service, NS)
    patch = json.dumps({"spec": {"selector": {key: val}}})
    k8s.kubectl("patch", "svc", service, "-n", NS, "-p", patch)
    return ToolResult("restore_selector", True,
                      f"restored svc/{service} selector -> {key}={val}",
                      snapshot_ref=f"svc/{service}@pre-patch")


NETWORK_ALLOWED = [("svc", "checkout-web")]


# ------------------------------------------------------------------ data
GOOD_CONFIG = "MARKER=HEALTHY\ncatalog service config v1 — ok\n"


def _restore_configmap(configmap: str = "catalog-config",
                       deployment: str = "catalog-svc") -> ToolResult:
    _assert_scope(DATA_ALLOWED, "configmap", configmap)
    snap = k8s.snapshot("configmap", configmap, NS)
    patch = json.dumps({"data": {"index.html": GOOD_CONFIG}})
    k8s.kubectl("patch", "configmap", configmap, "-n", NS, "-p", patch)
    k8s.kubectl("rollout", "restart", f"deploy/{deployment}", "-n", NS)
    k8s.kubectl("rollout", "status", f"deploy/{deployment}", "-n", NS, "--timeout=90s")
    return ToolResult("restore_configmap", True,
                      f"restored {configmap} marker + restarted deploy/{deployment}",
                      snapshot_ref=f"configmap/{configmap}@pre-restore")


DATA_ALLOWED = [("configmap", "catalog-config"), ("deploy", "catalog-svc")]


# ------------------------------------------------------------------ destructive (gated)
def _scale_zero(deployment: str) -> ToolResult:
    # intentionally NOT scope-limited to a single deploy: this is the dangerous
    # primitive the naive agent reaches for; hardened path only runs it post-ack.
    snap = k8s.snapshot("deploy", deployment, NS)
    k8s.kubectl("scale", f"deploy/{deployment}", "--replicas=0", "-n", NS)
    return ToolResult("scale_zero", True, f"scaled deploy/{deployment} to 0",
                      snapshot_ref=f"deploy/{deployment}@pre-scale", destructive=True)


# ------------------------------------------------------------------ registries
WORKLOAD_TOOLS = [
    Tool("rollout_undo", "workload",
         ["workload", "rollout", "crashloopbackoff", "kubectl.rollout.undo"],
         WORKLOAD_ALLOWED, _rollout_undo,
         description="revert a bad rollout to the previous healthy ReplicaSet"),
    Tool("scale_zero", "workload", ["workload", "scale", "destructive"],
         WORKLOAD_ALLOWED, _scale_zero, destructive=True,
         description="scale a deployment to zero (destructive, human-gated)"),
]

NETWORK_TOOLS = [
    Tool("restore_selector", "network",
         ["network", "service", "selector", "endpoints", "blackhole"],
         NETWORK_ALLOWED, _restore_selector,
         description="restore a Service's correct selector to re-attach endpoints"),
]

DATA_TOOLS = [
    Tool("restore_configmap", "data",
         ["data", "configmap", "config", "readiness", "marker"],
         DATA_ALLOWED, _restore_configmap,
         description="restore a corrupted ConfigMap and roll the deployment"),
]

DOMAIN_TOOLS: dict[str, list[Tool]] = {
    "workload": WORKLOAD_TOOLS,
    "network": NETWORK_TOOLS,
    "data": DATA_TOOLS,
}


def capability_tags(domain: str) -> list[str]:
    tags: list[str] = [domain]
    for t in DOMAIN_TOOLS.get(domain, []):
        tags.extend(t.capability_tags)
    return sorted(set(tags))
