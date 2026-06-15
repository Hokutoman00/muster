"""Shared, deterministic bidding policy.

The cross-framework story is about the *runtime*, not the decision rule: every
responder must reach the same, auditable verdict for a given incident, but each
runs that verdict through a different agent framework (LangGraph graph / CrewAI
flow / Pydantic AI typed agent). Keeping the policy deterministic is deliberate —
remediation must be reproducible and free of LLM nondeterminism. The *Commander*
(P4) is where real LLM reasoning lives.

These are pure functions: no Band I/O, no kubectl, no framework imports.
"""
from __future__ import annotations

from .contract_net import Bid, Incident, score_fit
from .tools import Tool


def rank(incident: Incident, tools: list[Tool]) -> list[tuple[float, Tool]]:
    scored = [(score_fit(incident.tags, t.capability_tags), t) for t in tools]
    return sorted(scored, key=lambda x: -x[0])


def choose(incident: Incident, tools: list[Tool]) -> Tool | None:
    """Highest capability-overlap, non-destructive tool with positive fit."""
    for fit, t in rank(incident, tools):
        if fit > 0 and not t.destructive:
            return t
    return None


def best_fit(incident: Incident, tools: list[Tool]) -> float:
    ranked = rank(incident, tools)
    return ranked[0][0] if ranked else 0.0


def make_bid(handle: str, incident: Incident, tools: list[Tool], *,
             framework: str) -> Bid:
    fit = best_fit(incident, tools)
    action = choose(incident, tools)
    if action is None or fit <= 0:
        return Bid(handle, can_handle=False, fit=fit, planned_actions=[],
                   confidence=0.0,
                   note=f"[{framework}] no in-scope reversible tool for this signature")
    return Bid(handle, can_handle=True, fit=fit, planned_actions=[action.name],
               confidence=min(0.5 + fit, 0.99), estimated_blast=0,
               note=f"[{framework}] {action.description}")
