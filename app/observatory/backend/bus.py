"""Thread-safe event bus + observatory state store.

The incident cycle (Commander + responders) runs synchronously in a worker
thread; the FastAPI/SSE layer is async. This bus bridges the two: the worker
calls `publish()` from its thread, every SSE subscriber drains its own queue,
and a bounded ring buffer lets a late-joining browser replay recent history.

It also keeps a small derived snapshot (latest cluster status, current incident
phase, per-node muster-graph state) so `GET /api/state` answers without waiting
for the next event.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from typing import Any

# muster-graph nodes the frontend draws (commander + the 3 specialist responders)
NODES = ("commander", "workload", "network", "data")


class EventBus:
    def __init__(self, history: int = 400):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._buffer: deque[dict] = deque(maxlen=history)
        self._seq = itertools.count(1)
        self.state = StateStore()

    # ------------------------------------------------------------- publish
    def publish(self, kind: str, **payload: Any) -> dict:
        evt = {"id": next(self._seq), "ts": time.time(), "kind": kind, **payload}
        with self._lock:
            self._buffer.append(evt)
            self.state.apply(evt)
            dead: list[queue.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)
        return evt

    # ------------------------------------------------------------- subscribe
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            # replay recent history so a fresh browser is immediately coherent
            for evt in self._buffer:
                q.put_nowait(evt)
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def snapshot(self) -> dict:
        with self._lock:
            return self.state.as_dict()


class StateStore:
    """Derived, always-current view assembled from the event stream."""

    def __init__(self) -> None:
        self.cluster: dict | None = None
        self.incident: dict = {"status": "idle"}
        self.graph: dict[str, dict] = {n: {"state": "idle", "detail": ""} for n in NODES}
        self.pending_approval: dict | None = None

    def _set_node(self, node: str, state: str, detail: str = "") -> None:
        if node in self.graph:
            self.graph[node] = {"state": state, "detail": detail}

    def apply(self, evt: dict) -> None:
        kind = evt.get("kind")
        if kind == "cluster":
            self.cluster = evt.get("status")
        elif kind == "incident":
            self.incident = {k: v for k, v in evt.items()
                             if k not in ("kind", "id", "ts")}
            if evt.get("phase") == "start":
                # new incident: reset graph to idle
                for n in NODES:
                    self.graph[n] = {"state": "idle", "detail": ""}
        elif kind == "approval":
            self.pending_approval = (None if evt.get("resolved")
                                     else {k: v for k, v in evt.items()
                                           if k not in ("kind", "id", "ts")})
        elif kind == "phase":
            self._apply_phase(evt)

    def _apply_phase(self, evt: dict) -> None:
        step = evt.get("step")
        if step == "discovered":
            for d in evt.get("domains", []):
                self._set_node(d, "discovered")
            self._set_node("commander", "triaging")
        elif step == "recruited":
            for d in evt.get("domains", []):
                self._set_node(d, "recruited", "CFP sent")
        elif step == "bid":
            self._set_node(evt.get("domain", ""),
                           "bid_yes" if evt.get("can_handle") else "bid_no",
                           evt.get("summary", ""))
        elif step == "awarded":
            self._set_node("commander", "awarded", evt.get("winner", ""))
            self._set_node(evt.get("domain", ""), "awarded", evt.get("tool", ""))
        elif step == "demustered":
            for d in evt.get("domains", []):
                self._set_node(d, "demustered", "released")
        elif step == "executing":
            self._set_node(evt.get("domain", ""), "executing", evt.get("tool", ""))
        elif step == "executed":
            self._set_node(evt.get("domain", ""), "executed", evt.get("result", ""))

    def as_dict(self) -> dict:
        return {"cluster": self.cluster, "incident": self.incident,
                "graph": self.graph, "pending_approval": self.pending_approval}
