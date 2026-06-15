"""NetworkResponder reasoning on CrewAI.

Uses crewai.flow (the deterministic orchestration primitive — no LLM, no token
spend) rather than a Crew of LLM agents: @start -> @router -> @listen. The router
is genuine CrewAI control flow that branches to the bid or no-bid step. Node
bodies call the shared deterministic policy.

Telemetry is opted out so a remediation run makes no outbound calls of its own.
"""
from __future__ import annotations

import contextlib
import io
import os

# keep CrewAI from phoning home during a remediation run
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai.flow.flow import Flow, listen, router, start  # noqa: E402

from common import policy  # noqa: E402
from common.contract_net import Bid, Incident  # noqa: E402
from common.tools import Tool  # noqa: E402


class _BidFlow(Flow):
    """Deterministic CrewAI flow that yields a Bid into self.state['bid']."""

    @start()
    def assess(self):
        self.state["ranked"] = policy.rank(self._incident, self._tools)
        return "assessed"

    @router(assess)
    def route(self):
        self.state["action"] = policy.choose(self._incident, self._tools)
        return "bid" if self.state["action"] is not None else "nobid"

    @listen("bid")
    def make_bid(self):
        self.state["bid"] = policy.make_bid(
            self._handle, self._incident, self._tools, framework="crewai")

    @listen("nobid")
    def no_bid(self):
        ranked = self.state.get("ranked") or []
        self.state["bid"] = Bid(
            self._handle, can_handle=False,
            fit=ranked[0][0] if ranked else 0.0,
            planned_actions=[], confidence=0.0,
            note="[crewai] no in-scope reversible tool")


class CrewAIAdapter:
    framework = "crewai"

    def __init__(self, handle: str):
        self.handle = handle

    def choose_action(self, incident: Incident, tools: list[Tool]) -> Tool | None:
        return policy.choose(incident, tools)

    def decide(self, incident: Incident, tools: list[Tool]) -> Bid:
        flow = _BidFlow()
        # inputs carried as private attrs so they don't collide with flow state
        flow._handle = self.handle
        flow._incident = incident
        flow._tools = tools
        # CrewAI's event bus prints emoji banners that crash a cp932 console and
        # add noise; the bid is read from flow.state, not stdout, so silence it.
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            flow.kickoff()
        return flow.state["bid"]
