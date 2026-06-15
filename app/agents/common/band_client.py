"""Band REST client (Python) — mirrors the exact contract verified in P1.

Auth = `X-API-Key: <agent_key>` plain REST (no SDK). Every method maps 1:1 to a
Contract-Net primitive and to a path that returned 2xx in
spikes/p1-band-connectivity.evidence.json:

  peers()                  GET  /api/v1/agent/peers            discovery
  create_chat(title)       POST /api/v1/agent/chats            muster room
  add_participant(...)     POST .../chats/{id}/participants    recruit
  remove_participant(...)  DELETE .../participants/{pid}       de-muster
  create_event(...)        POST .../chats/{id}/events          task/tool_call/result
  create_message(...)      POST .../chats/{id}/messages        @mention handoff/bid
  messages_next(id)        GET  .../chats/{id}/messages/next   responder work queue
  mark_processing/processed                                    ack lifecycle
  read_messages(id)        GET  .../chats/{id}/messages        bid collection

stdlib only (urllib) so responders have no third-party HTTP dependency.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ENV = Path(os.environ.get("BAND_ENV", r"C:\Users\hokut\.credentials\band.env"))


def load_env(path: Path = DEFAULT_ENV) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


@dataclass
class Agent:
    """One Band agent identity (key + id + handle)."""
    key: str
    id: str
    handle: str

    @classmethod
    def from_env(cls, prefix: str, env: dict[str, str]) -> "Agent":
        return cls(
            key=env[f"BAND_{prefix}_API_KEY"],
            id=env[f"BAND_{prefix}_AGENT_ID"],
            handle=env[f"BAND_{prefix}_HANDLE"].lstrip("@"),
        )


class BandClient:
    def __init__(self, agent: Agent, base: str | None = None,
                 env: dict[str, str] | None = None):
        env = env or load_env()
        self.agent = agent
        self.base = (base or env.get("THENVOI_REST_URL", "https://app.band.ai/")).rstrip("/")
        self.calls: list[dict[str, Any]] = []  # in-memory evidence trail

    # ---------------------------------------------------------------- transport
    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.agent.key)
        req.add_header("Content-Type", "application/json")
        # Band sits behind Cloudflare; the default urllib UA trips bot rule 1010.
        req.add_header("User-Agent", "muster-agent/1.0 (+https://app.band.ai)")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
            text = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        self.calls.append({"who": self.agent.handle, "method": method,
                           "path": path, "status": status})
        if status >= 300:
            raise RuntimeError(f"Band {method} {path} -> {status}: {str(parsed)[:300]}")
        return parsed

    @staticmethod
    def _unwrap(resp: Any) -> Any:
        if isinstance(resp, dict) and "data" in resp:
            return resp["data"]
        return resp

    # ---------------------------------------------------------------- primitives
    def me(self) -> dict:
        return self._unwrap(self._call("GET", "/api/v1/agent/me"))

    def peers(self, page_size: int = 50) -> list[dict]:
        resp = self._call("GET", f"/api/v1/agent/peers?page_size={page_size}")
        peers = self._unwrap(resp)
        return peers if isinstance(peers, list) else []

    def read_chats(self, page_size: int = 100) -> list[dict]:
        resp = self._unwrap(self._call("GET",
                            f"/api/v1/agent/chats?page_size={page_size}"))
        return resp if isinstance(resp, list) else []

    def create_chat(self, title: str) -> str:
        resp = self._unwrap(self._call("POST", "/api/v1/agent/chats",
                                       {"chat": {"title": title}}))
        return resp["id"]

    def add_participant(self, chat_id: str, participant_id: str, role: str = "member"):
        return self._call("POST", f"/api/v1/agent/chats/{chat_id}/participants",
                          {"participant": {"participant_id": participant_id, "role": role}})

    def remove_participant(self, chat_id: str, participant_id: str):
        return self._call("DELETE",
                          f"/api/v1/agent/chats/{chat_id}/participants/{participant_id}")

    def create_event(self, chat_id: str, message_type: str, content: str,
                     metadata: dict | None = None):
        """message_type in {task, tool_call, tool_result, thought}."""
        event = {"message_type": message_type, "content": content}
        if metadata is not None:
            event["metadata"] = metadata
        return self._call("POST", f"/api/v1/agent/chats/{chat_id}/events",
                          {"event": event})

    def create_message(self, chat_id: str, content: str,
                       mentions: list[Agent] | None = None):
        msg: dict[str, Any] = {"content": content}
        if mentions:
            msg["mentions"] = [{"id": m.id, "handle": m.handle,
                                "name": m.handle.split("/")[-1]} for m in mentions]
        return self._call("POST", f"/api/v1/agent/chats/{chat_id}/messages",
                          {"message": msg})

    def messages_next(self, chat_id: str) -> dict | None:
        resp = self._unwrap(self._call("GET",
                            f"/api/v1/agent/chats/{chat_id}/messages/next"))
        return resp or None

    def mark_processing(self, chat_id: str, message_id: str):
        return self._call("POST",
                          f"/api/v1/agent/chats/{chat_id}/messages/{message_id}/processing")

    def mark_processed(self, chat_id: str, message_id: str):
        return self._call("POST",
                          f"/api/v1/agent/chats/{chat_id}/messages/{message_id}/processed")

    def read_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        resp = self._unwrap(self._call("GET",
                            f"/api/v1/agent/chats/{chat_id}/messages?limit={limit}"))
        return resp if isinstance(resp, list) else []
