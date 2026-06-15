"""DataResponder reasoning on Pydantic AI.

A real pydantic_ai.Agent runs with a FunctionModel (deterministic, no external
LLM): the model emits a tool call for the agent's structured-output tool, and
pydantic-ai validates the arguments against the `_BidOut` schema. That
typed-output + validation pass is Pydantic AI's distinctive machinery; the bid
values themselves come from the shared deterministic policy.
"""
from __future__ import annotations

from pydantic import BaseModel

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from common import policy
from common.contract_net import Bid, Incident
from common.tools import Tool


class _BidOut(BaseModel):
    can_handle: bool
    fit: float
    planned_actions: list[str]
    confidence: float
    note: str


class PydanticAdapter:
    framework = "pydantic-ai"

    def __init__(self, handle: str):
        self.handle = handle

    def choose_action(self, incident: Incident, tools: list[Tool]) -> Tool | None:
        return policy.choose(incident, tools)

    def decide(self, incident: Incident, tools: list[Tool]) -> Bid:
        verdict = policy.make_bid(self.handle, incident, tools, framework=self.framework)

        def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            out_tool = info.output_tools[0]
            args = {
                "can_handle": verdict.can_handle,
                "fit": verdict.fit,
                "planned_actions": list(verdict.planned_actions),
                "confidence": verdict.confidence,
                "note": verdict.note,
            }
            return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args=args)])

        agent = Agent(FunctionModel(model_fn), output_type=_BidOut)
        result = agent.run_sync("Produce the Contract-Net bid for this incident.")
        o = result.output  # validated _BidOut
        return Bid(self.handle, can_handle=o.can_handle, fit=o.fit,
                   planned_actions=list(o.planned_actions), confidence=o.confidence,
                   estimated_blast=verdict.estimated_blast, note=o.note)
