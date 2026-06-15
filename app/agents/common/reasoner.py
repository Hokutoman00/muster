"""LLM-backed Reasoner — a drop-in for the Commander's `Reasoner` seam.

This is the *only* place a real LLM enters MUSTER, and it is deliberately
**narration-only**: it is handed the decisions that `score_fit` (shortlist) and
`select_award` (award) have *already* made and asked to put them into war-room
language for the observatory timeline. It never changes who is mustered or who
wins — remediation stays deterministic, reproducible and audit-stable. If the
model is slow, errors, or no key is configured, every method falls back to the
deterministic `NativeReasoner` string, so `make demo` still runs creds-free and
a flaky network never breaks an incident response.

Provider-agnostic by design: any OpenAI-compatible `/chat/completions` endpoint
works through one stdlib HTTP call (no SDK dependency). Configure via env:

    MUSTER_LLM_BASE_URL   e.g. https://api.aimlapi.com/v1
                               https://api.featherless.ai/v1
                               https://generativelanguage.googleapis.com/v1beta/openai
    MUSTER_LLM_API_KEY    the provider key (kept server-side; never sent to a browser)
    MUSTER_LLM_MODEL      e.g. gpt-4o-mini / gemini-2.0-flash / a Featherless model id
    MUSTER_LLM_TIMEOUT    seconds (default 12)

`make_reasoner()` returns an LLMReasoner when a key is present, else the native
one — so the same code path serves the no-creds reproducer and the live demo.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from common.contract_net import Incident

# NativeReasoner lives in commander.py; make_reasoner imports it lazily at call
# time to avoid an import cycle (commander imports make_reasoner, not vice-versa).


def _provider_label(base_url: str) -> str:
    b = (base_url or "").lower()
    if "aimlapi" in b:
        return "aimlapi"
    if "featherless" in b:
        return "featherless"
    if "googleapis" in b or "gemini" in b:
        return "gemini"
    return "llm"


class LLMReasoner:
    """Narrates Contract-Net decisions via an OpenAI-compatible chat endpoint.

    Wraps a deterministic fallback (`_native`) used whenever the call fails.
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 12.0, fallback=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._native = fallback
        # provider goes into the seam name so the timeline shows what reasoned
        self.name = f"llm:{_provider_label(base_url)}:{model}"

    # -- the OpenAI-compatible call (stdlib only) --------------------------------
    def _chat(self, system: str, user: str) -> str | None:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 160,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            txt = data["choices"][0]["message"]["content"].strip()
            return " ".join(txt.split())  # collapse to one timeline line
        except (urllib.error.URLError, KeyError, IndexError,
                json.JSONDecodeError, TimeoutError, OSError):
            return None

    _SYS = ("You narrate a Kubernetes incident war-room for a live operator "
            "timeline. The dispatch decision is ALREADY made deterministically; "
            "you only describe it in <=2 crisp sentences. Never invent actions, "
            "numbers, or alternatives not given. No markdown, no preamble.")

    def triage(self, incident: Incident, ranked) -> str:
        fit = ", ".join(f"{c.domain}={c.fit:.2f}" for c in ranked) or "none"
        native = self._native.triage(incident, ranked)
        user = (f"Incident {incident.incident_id} severity={incident.severity} "
                f"symptoms={incident.symptom} scope={incident.scope}. "
                f"Capability fit by responder domain: {fit}. "
                f"Narrate the triage and which domain the signature points to.")
        return self._chat(self._SYS, user) or native

    def rationale(self, incident: Incident, bids, winner) -> str:
        native = self._native.rationale(incident, bids, winner)
        bidlines = "; ".join(
            f"{b.responder_handle} can_handle={b.can_handle} "
            f"conf={b.confidence:.2f} blast~{b.estimated_blast}" for b in bids
        ) or "no bids"
        win = (f"{winner.responder_handle} (conf={winner.confidence:.2f}, "
               f"blast~{winner.estimated_blast})") if winner else "none -> escalate to human"
        user = (f"Incident {incident.incident_id}. Bids: {bidlines}. "
                f"Awarded: {win}. Narrate why this responder was awarded and that "
                f"the others are de-mustered.")
        return self._chat(self._SYS, user) or native


def make_reasoner(fallback=None):
    """Return an LLMReasoner if a key is configured, else the deterministic one.

    `fallback` should be a NativeReasoner instance (the Commander passes its own
    so the two stay in sync); if omitted we import and build one.
    """
    if fallback is None:
        from commander import NativeReasoner as _NR  # type: ignore
        fallback = _NR()
    key = os.environ.get("MUSTER_LLM_API_KEY", "").strip()
    base = os.environ.get("MUSTER_LLM_BASE_URL", "").strip()
    model = os.environ.get("MUSTER_LLM_MODEL", "").strip()
    if key and base and model:
        timeout = float(os.environ.get("MUSTER_LLM_TIMEOUT", "12") or 12)
        return LLMReasoner(base, key, model, timeout=timeout, fallback=fallback)
    return fallback
