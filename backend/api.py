"""Optional FastAPI backend.

The Streamlit dashboard already runs the engine in-process, so this server is
NOT required for the basic demo. It is included because the proposal lists
"Python (FastAPI) + paho-mqtt" as the backend, and it gives a clean REST /
WebSocket surface over the same simulation engine - useful if a teammate wants
to build an alternative frontend or call the line from another tool.

Run with:
    uvicorn backend.api:app --reload
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import config  # noqa: E402
from ai_assistant import AIAssistant  # noqa: E402
from engine import SimulationEngine  # noqa: E402

app = FastAPI(title="Beverage Line Digital Twin API")

engine = SimulationEngine(use_mqtt=os.environ.get("USE_MQTT", "0") == "1")
assistant = AIAssistant()


@app.on_event("startup")
def _startup() -> None:
    engine.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    engine.stop()


class FaultRequest(BaseModel):
    fault_inject_code: int


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "running": engine.is_running}


@app.get("/tags/latest")
def latest_tags() -> dict:
    return engine.latest()


@app.get("/tags/history")
def history(window_s: float = config.HISTORY_WINDOW_S) -> list:
    return engine.historian.recent(window_s=window_s)


@app.get("/alarms")
def alarms(limit: int = 20) -> list:
    return engine.historian.recent_alarms(limit=limit)


@app.post("/command/start")
def cmd_start() -> dict:
    engine.start_line()
    return {"ok": True}


@app.post("/command/stop")
def cmd_stop() -> dict:
    engine.stop_line()
    return {"ok": True}


@app.post("/command/fault")
def cmd_fault(req: FaultRequest) -> dict:
    engine.inject_fault(req.fault_inject_code)
    return {"ok": True, "fault": req.fault_inject_code}


@app.post("/command/reset_fault")
def cmd_reset() -> dict:
    engine.reset_fault()
    return {"ok": True}


@app.get("/ai/recommendation")
def ai_recommendation() -> dict:
    latest = engine.latest()
    alarm_code = int(latest.get("alarm_code", config.ALARM_NONE))
    return assistant.diagnose(latest, alarm_code, engine.historian.recent())


@app.websocket("/ws/tags")
async def ws_tags(websocket: WebSocket) -> None:
    """Stream the latest tag snapshot once per second."""
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(engine.latest())
            await asyncio.sleep(config.TICK_INTERVAL_S)
    except WebSocketDisconnect:
        return
