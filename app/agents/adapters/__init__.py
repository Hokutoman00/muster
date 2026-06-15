"""Cross-framework responder adapters.

Each responder runs on a *different* agent framework but implements the same
`Adapter` protocol (decide / choose_action), so the Commander coordinates them
over Band without knowing or caring which framework each uses. That heterogeneity
— discovery + Contract-Net across frameworks on one substrate — is the keystone
of MUSTER's differentiation.

  workload -> LangGraph    (StateGraph pregel graph w/ conditional routing)
  network  -> CrewAI       (crewai.flow Flow: @start -> @router -> @listen)
  data     -> Pydantic AI  (Agent + FunctionModel + validated structured output)

`get_adapter(domain, handle)` returns the framework adapter for a domain, falling
back to the dependency-free NativeAdapter if a framework fails to import (so the
loop still runs in a minimal environment). The fallback is logged, never silent.
"""
from __future__ import annotations

import sys

# domain -> (module, class) of the framework adapter
_REGISTRY = {
    "workload": ("adapters.langgraph_adapter", "LangGraphAdapter"),
    "network": ("adapters.crewai_adapter", "CrewAIAdapter"),
    "data": ("adapters.pydantic_adapter", "PydanticAdapter"),
}


def get_adapter(domain: str, handle: str):
    spec = _REGISTRY.get(domain)
    if spec is None:
        from common.responder import NativeAdapter
        return NativeAdapter(handle)
    module_name, cls_name = spec
    try:
        import importlib
        mod = importlib.import_module(module_name)
        return getattr(mod, cls_name)(handle)
    except Exception as e:  # noqa: BLE001 — degrade loudly, never silently
        from common.responder import NativeAdapter
        print(f"[adapters] {domain}: framework adapter unavailable ({e!r}); "
              f"falling back to NativeAdapter", file=sys.stderr)
        return NativeAdapter(handle)
