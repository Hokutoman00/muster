"""Responder runtime — the loop every specialist responder shares.

A responder:
  1. polls its Band work queue (messages_next),
  2. parses a CFP (Commander embeds `MUSTER-CFP <json>` in the @mention),
  3. asks its framework adapter to produce a Bid (decide),
  4. posts the Bid back to the Commander (@mention),
  5. on award (`MUSTER-AWARD <json>` addressed to it), executes the chosen
     reversible tool, emitting tool_call / tool_result events to the timeline,
  6. reports resolution (or hands off via @mention if out of its domain).

The *reasoning* (decide / choose_action) is delegated to a framework Adapter so
each responder can run on a different framework (LangGraph / CrewAI / Pydantic AI)
while sharing this Band + kubectl machinery. Cross-framework coordination is the
point: Band is the substrate, the frameworks are heterogeneous.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol

from .band_client import Agent, BandClient
from .contract_net import Bid, Incident, score_fit
from .tools import Tool, ToolResult

CFP_RE = re.compile(r"MUSTER-CFP\s+(\{.*\})", re.DOTALL)
AWARD_RE = re.compile(r"MUSTER-AWARD\s+(\{.*\})", re.DOTALL)


def parse_cfp(content: str) -> Incident | None:
    m = CFP_RE.search(content or "")
    if not m:
        return None
    d = json.loads(m.group(1))
    return Incident(
        incident_id=d.get("incident_id", "INC"),
        symptom=d.get("symptom", []),
        scope=d.get("scope", []),
        severity=d.get("severity", "high"),
        capability_required=d.get("capability_required", []),
    )


def parse_award(content: str) -> dict | None:
    m = AWARD_RE.search(content or "")
    return json.loads(m.group(1)) if m else None


class Adapter(Protocol):
    """A framework-specific reasoning unit. Pure decision; no Band/k8s I/O."""
    framework: str

    def decide(self, incident: Incident, tools: list[Tool]) -> Bid: ...
    def choose_action(self, incident: Incident, tools: list[Tool]) -> Tool | None: ...


@dataclass
class NativeAdapter:
    """Dependency-free reasoning: fit = best tool capability overlap, pick the
    highest-overlap non-destructive tool. Used as the baseline and as the
    fallback when a heavyweight framework is unavailable."""
    handle: str
    framework: str = "native"

    def _ranked(self, incident: Incident, tools: list[Tool]) -> list[tuple[float, Tool]]:
        scored = [(score_fit(incident.tags, t.capability_tags), t) for t in tools]
        return sorted(scored, key=lambda x: -x[0])

    def choose_action(self, incident: Incident, tools: list[Tool]) -> Tool | None:
        for fit, t in self._ranked(incident, tools):
            if fit > 0 and not t.destructive:
                return t
        return None

    def decide(self, incident: Incident, tools: list[Tool]) -> Bid:
        ranked = self._ranked(incident, tools)
        best_fit = ranked[0][0] if ranked else 0.0
        action = self.choose_action(incident, tools)
        if action is None or best_fit <= 0:
            return Bid(self.handle, can_handle=False, fit=best_fit,
                       planned_actions=[], confidence=0.0,
                       note="no in-scope reversible tool for this signature")
        return Bid(self.handle, can_handle=True, fit=best_fit,
                   planned_actions=[action.name], confidence=min(0.5 + best_fit, 0.99),
                   estimated_blast=0, note=action.description)


class Responder:
    def __init__(self, agent: Agent, commander: Agent, domain: str,
                 tools: list[Tool], adapter: Adapter, client: BandClient | None = None):
        self.agent = agent
        self.commander = commander
        self.domain = domain
        self.tools = tools
        self.adapter = adapter
        self.client = client or BandClient(agent)

    def _tool_by_name(self, name: str) -> Tool | None:
        return next((t for t in self.tools if t.name == name), None)

    def handle_cfp(self, chat_id: str, incident: Incident) -> Bid:
        bid = self.adapter.decide(incident, self.tools)
        self.client.create_message(chat_id, bid.to_line(self.commander.handle),
                                   mentions=[self.commander])
        return bid

    def execute_award(self, chat_id: str, award: dict, human_ack: bool = False) -> ToolResult:
        tool = self._tool_by_name(award.get("tool", ""))
        if tool is None:
            raise RuntimeError(f"awarded unknown tool {award.get('tool')!r}")
        self.client.create_event(chat_id, "tool_call",
                                 f"{self.domain}: kubectl {tool.name}",
                                 metadata={"tool": tool.name, "domain": self.domain,
                                           "destructive": tool.destructive})
        result = tool.guarded_run(human_ack=human_ack, **award.get("args", {}))
        self.client.create_event(chat_id, "tool_result",
                                 f"{tool.name}: {result.detail}",
                                 metadata={"ok": result.ok, "snapshot": result.snapshot_ref})
        return result

    def poll_once(self, chat_id: str, human_ack: bool = False) -> dict | None:
        """Process one queued message: bid on a CFP, or act on an award."""
        msg = self.client.messages_next(chat_id)
        if not msg:
            return None
        mid = msg.get("id")
        content = msg.get("content", "")
        if mid:
            self.client.mark_processing(chat_id, mid)
        out: dict = {"message_id": mid}
        try:
            incident = parse_cfp(content)
            award = parse_award(content)
            if incident is not None:
                bid = self.handle_cfp(chat_id, incident)
                out["action"] = "bid"
                out["bid"] = bid.to_line(self.commander.handle)
            elif award is not None and award.get("winner") == self.agent.handle:
                result = self.execute_award(chat_id, award, human_ack=human_ack)
                out["action"] = "executed"
                out["result"] = result.detail
            else:
                out["action"] = "ignored"
        finally:
            if mid:
                self.client.mark_processed(chat_id, mid)
        return out

    def run(self, chat_id: str, max_polls: int = 30, interval: float = 2.0,
            human_ack: bool = False):
        for _ in range(max_polls):
            res = self.poll_once(chat_id, human_ack=human_ack)
            if res and res.get("action") == "executed":
                return res
            time.sleep(interval)
        return None
