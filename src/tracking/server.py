"""
RICAIP Digital Twin — FastAPI backend
Serves the frontend and exposes:
  GET  /         → index.html
  GET  /state    → current snapshot (JSON)
  POST /plan/load  {mode: "A"|"B"|"C"}
  POST /plan/start
  POST /plan/pause
  POST /plan/reset
  POST /workcell/estop   {ok: bool}
  POST /workcell/conveyor {running: bool}
  WS   /ws       → push DigitalTwinSnapshot on every state change
"""
from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from digital_twin import DigitalTwin
from models import ExecutionMode, PlanStatus, StepStatus
from opcua_client import MockOpcUaClient
from plan_builder import build_maze_106_plan

# ── Globals ───────────────────────────────────────────────────────────────────
twin = DigitalTwin()
opcua: Optional[MockOpcUaClient] = None
_runner: Optional[asyncio.Task] = None

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


async def _on_robot_state(robot_id: int, **kwargs) -> None:
    await twin.update_robot_state(robot_id, **kwargs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global opcua
    opcua = MockOpcUaClient(on_state_change=_on_robot_state)
    await opcua.connect()
    yield
    await opcua.disconnect()


app = FastAPI(title="RICAIP Digital Twin", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/state")
async def get_state():
    return twin.get_snapshot()


class LoadPlanRequest(BaseModel):
    mode: ExecutionMode = ExecutionMode.A


@app.post("/plan/load")
async def load_plan(req: LoadPlanRequest):
    plan = build_maze_106_plan(req.mode)
    await twin.set_plan(plan)
    return {"plan_id": plan.plan_id, "steps": len(plan.steps), "mode": req.mode}


@app.post("/plan/start")
async def start_plan():
    global _runner
    snap = twin.get_snapshot()
    if not snap.plan:
        raise HTTPException(400, "No plan loaded — call /plan/load first")
    if snap.plan.status == PlanStatus.RUNNING:
        raise HTTPException(400, "Plan already running")
    if snap.plan.status == PlanStatus.PAUSED:
        await twin.set_plan_status(PlanStatus.RUNNING)
        return {"status": "resumed"}
    _runner = asyncio.create_task(_run_plan())
    return {"status": "started"}


@app.post("/plan/pause")
async def pause_plan():
    await twin.set_plan_status(PlanStatus.PAUSED)
    return {"status": "paused"}


@app.post("/plan/reset")
async def reset_plan():
    global _runner
    if _runner and not _runner.done():
        _runner.cancel()
    await twin.reset_plan()
    return {"status": "reset"}


class WorkcellToggle(BaseModel):
    ok: Optional[bool] = None
    running: Optional[bool] = None


@app.post("/workcell/estop")
async def set_estop(body: WorkcellToggle):
    await twin.toggle_estop(body.ok if body.ok is not None else True)
    return {"estop_ok": body.ok}


@app.post("/workcell/conveyor")
async def set_conveyor(body: WorkcellToggle):
    await twin.toggle_conveyor(body.running if body.running is not None else True)
    return {"conveyor_running": body.running}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    twin.subscribe(queue)
    try:
        # Push current state immediately on connect
        await ws.send_text(twin.get_snapshot().model_dump_json())
        while True:
            msg = await queue.get()
            await ws.send_text(msg)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        twin.unsubscribe(queue)


# ── Plan runner ───────────────────────────────────────────────────────────────

async def _run_plan() -> None:
    snap = twin.get_snapshot()
    plan = snap.plan
    if not plan:
        return

    await twin.set_plan_status(PlanStatus.RUNNING, started_at=datetime.now(timezone.utc))

    for step in plan.steps:
        if step.status == StepStatus.DONE:
            continue

        # Respect pause
        while True:
            current = twin.get_snapshot()
            if current.plan and current.plan.status == PlanStatus.PAUSED:
                await asyncio.sleep(0.5)
            else:
                break

        # Check workcell safety
        state = twin.get_snapshot()
        if not state.workcell.estop_ok:
            await twin.set_plan_status(
                PlanStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
            )
            await twin.update_step(
                step.step_index,
                status=StepStatus.FAILED,
                error_message="E-stop active",
            )
            return

        # Mark active
        started = datetime.now(timezone.utc)
        await twin.update_step(
            step.step_index,
            status=StepStatus.ACTIVE,
            started_at=started,
        )

        # Dispatch to OPC UA (mock or real)
        call_id, done_future = await opcua.start_robot_operation(
            step.robot_id, step.op_id, step.parameter_array
        )
        await twin.update_step(step.step_index, call_id=call_id)

        try:
            result_code = await asyncio.wait_for(done_future, timeout=30.0)
        except asyncio.TimeoutError:
            result_code = 99  # timeout error code
            await twin.update_step(
                step.step_index,
                status=StepStatus.FAILED,
                result_code=result_code,
                completed_at=datetime.now(timezone.utc),
                error_message="Operation timed out",
            )
            await twin.set_plan_status(
                PlanStatus.FAILED, completed_at=datetime.now(timezone.utc)
            )
            return

        completed = datetime.now(timezone.utc)
        duration_ms = (completed - started).total_seconds() * 1000
        success = result_code == 0
        status = StepStatus.DONE if success else StepStatus.FAILED

        await twin.update_step(
            step.step_index,
            status=status,
            result_code=result_code,
            completed_at=completed,
            duration_ms=round(duration_ms, 1),
            error_message=None if success else f"Result code {result_code}",
        )

        if not success:
            await twin.set_plan_status(
                PlanStatus.FAILED, completed_at=datetime.now(timezone.utc)
            )
            return

    await twin.set_plan_status(
        PlanStatus.COMPLETED, completed_at=datetime.now(timezone.utc)
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=True)
