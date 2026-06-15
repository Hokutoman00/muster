"""MUSTER Live Observatory — FastAPI backend.

Bridges the real Contract-Net incident cycle (Band + kind cluster) to the browser
over a single SSE stream. The browser never holds a Band token: the backend owns
the agent keys, runs the cycle server-side, and pushes derived events.

Endpoints
  GET  /api/stream            text/event-stream — cluster / phase / band / incident
  GET  /api/state             current derived snapshot (cluster, graph, incident)
  POST /api/incident/{domain} inject a fault + run the muster cycle (workload|network|data)
  POST /api/approve           human-in-the-loop key for a destructive remediation
  GET  /api/health            liveness

Run:
  cd app/observatory/backend
  ../../.venv/Scripts/python.exe -m uvicorn server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import json
import queue
import sys
from pathlib import Path

# make the shared agent/cluster code importable (same convention as the spikes)
APP = Path(__file__).resolve().parents[2]
for sub in ("agents", "cluster"):
    p = str(APP / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from runner import Observatory  # noqa: E402

app = FastAPI(title="MUSTER Live Observatory")
obs = Observatory()


@app.on_event("startup")
def _startup() -> None:
    obs.start_cluster_poll(interval=4.0)


@app.on_event("shutdown")
def _shutdown() -> None:
    obs.stop()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "busy": obs.busy}


@app.get("/api/state")
def state() -> dict:
    return obs.bus.snapshot()


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    q = obs.bus.subscribe()

    async def gen():
        try:
            # prime the client with the current derived snapshot
            yield _sse("state", obs.bus.snapshot())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.to_thread(q.get, True, 15.0)
                    yield _sse(evt["kind"], evt)
                except queue.Empty:
                    yield ": keep-alive\n\n"  # comment frame to hold the connection
        finally:
            obs.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/incident/{domain}")
async def incident(domain: str, mode: str = "hardened") -> JSONResponse:
    if obs.busy:
        raise HTTPException(409, "an incident is already in progress")
    if domain not in ("workload", "network", "data"):
        raise HTTPException(404, f"unknown domain {domain!r}")
    if mode not in ("hardened", "naive"):
        raise HTTPException(400, f"unknown mode {mode!r}")
    # naive control is the workload-only contrast (P6 / 受賞関数): a single
    # unscoped, un-gated operator on the SAME real fault — the judge toggles it.
    if mode == "naive":
        if domain != "workload":
            raise HTTPException(400, "naive control runs on the workload incident")
        asyncio.get_event_loop().run_in_executor(None, obs.run_naive)
    else:
        # run the synchronous cycle off the event loop so SSE keeps flowing
        asyncio.get_event_loop().run_in_executor(None, obs.run, domain)
    return JSONResponse({"started": True, "domain": domain, "mode": mode})


@app.post("/api/approve")
async def approve(request: Request) -> dict:
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — empty body == approve
        pass
    decision = bool(body.get("approve", True))
    resolved = obs.gate.resolve(decision)
    if not resolved:
        raise HTTPException(409, "no destructive remediation is awaiting approval")
    return {"resolved": True, "approved": decision}


def _sse(event: str, data: dict | None) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# serve the built frontend (P5 frontend build output) if present, so the whole
# observatory is reachable from one public URL.
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
