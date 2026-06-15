"""WorkloadResponder reasoning on LangGraph.

A real compiled StateGraph drives the bid: assess -> select -> (conditional)
-> bid | nobid. The conditional edge is genuine LangGraph control flow, not a
wrapper around a single function. The node bodies call the shared deterministic
policy so the verdict is reproducible.
"""
from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from common import policy
from common.contract_net import Bid, Incident
from common.tools import Tool


class _State(TypedDict, total=False):
    incident: Incident
    tools: list
    ranked: list
    action: Optional[Tool]
    bid: Bid


class LangGraphAdapter:
    framework = "langgraph"

    def __init__(self, handle: str):
        self.handle = handle
        self._graph = self._build()

    def _build(self):
        g = StateGraph(_State)

        def assess(s: _State) -> _State:
            return {"ranked": policy.rank(s["incident"], s["tools"])}

        def select(s: _State) -> _State:
            return {"action": policy.choose(s["incident"], s["tools"])}

        def bid(s: _State) -> _State:
            return {"bid": policy.make_bid(self.handle, s["incident"], s["tools"],
                                           framework=self.framework)}

        def nobid(s: _State) -> _State:
            fit = s["ranked"][0][0] if s.get("ranked") else 0.0
            return {"bid": Bid(self.handle, can_handle=False, fit=fit,
                               planned_actions=[], confidence=0.0,
                               note=f"[{self.framework}] no in-scope reversible tool")}

        g.add_node("assess", assess)
        g.add_node("select", select)
        g.add_node("bid", bid)
        g.add_node("nobid", nobid)
        g.set_entry_point("assess")
        g.add_edge("assess", "select")
        g.add_conditional_edges(
            "select",
            lambda s: "bid" if s.get("action") is not None else "nobid",
            {"bid": "bid", "nobid": "nobid"},
        )
        g.add_edge("bid", END)
        g.add_edge("nobid", END)
        return g.compile()

    def choose_action(self, incident: Incident, tools: list[Tool]) -> Tool | None:
        return policy.choose(incident, tools)

    def decide(self, incident: Incident, tools: list[Tool]) -> Bid:
        out = self._graph.invoke({"incident": incident, "tools": tools})
        return out["bid"]
