"""
RICAIP Digital Twin — FastAPI backend
Serves the frontend and exposes:
  GET  /         → index.html
  GET  /state    → current snapshot (JSON)
  POST /plan/load  {mode: "A"|"B"|"C", debug_mode: bool}
  POST /plan/start
  POST /plan/pause
  POST /plan/reset
  POST /plan/ack  → resume debug step
  POST /workcell/estop   {ok: bool}
  POST /workcell/conveyor {running: bool}
  WS   /ws       → push DigitalTwinSnapshot on every state change
  POST /seed     → SSE stream seeding TypeDB
  POST /clear    → clear TypeDB database
  GET  /capability-states        → list capability states
  POST /capability-states        → save capability state
  GET  /capability-states/{sid}  → get capability state
  PUT  /capability-states/{sid}  → update capability state
  DELETE /capability-states/{sid} → delete capability state
  POST /capability-states/{sid}/apply → apply state to live twin
  POST /plans/export   → export current plan
  GET  /plans          → list saved plans
  GET  /plans/{pid}    → get plan
  DELETE /plans/{pid}  → delete plan
  POST /plans/{pid}/train → copy plan to training data
  GET  /viz/stats    → aggregate stats
  GET  /viz/history  → execution history
  GET  /viz/diagram  → mermaid diagram
  GET  /viz/execution-logs               → list saved execution logs
  GET  /viz/execution-logs/{id}          → full log detail
  GET  /viz/execution-logs/{id}/download → download log as JSON attachment
  GET  /debug/status → debug mode status
"""
from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin import DigitalTwin
from models import ExecutionMode, PlanStatus, StepStatus
from opcua_client import MockOpcUaClient
from plan_builder import build_maze_106_plan

# Optional seed imports — may fail if scripts/ not on path yet; import lazily in endpoint
try:
    from scripts.seed_kg import build_typeql as _build_typeql, run as _seed_run
    _SEED_KG_AVAILABLE = True
except ImportError:
    _SEED_KG_AVAILABLE = False

# ── Globals ───────────────────────────────────────────────────────────────────
twin = DigitalTwin()
opcua: Optional[MockOpcUaClient] = None
_runner: Optional[asyncio.Task] = None
_debug_mode: bool = False
_debug_ack: Optional[asyncio.Event] = None

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Data directories
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_STATES_DIR   = _PROJECT_ROOT / "data" / "states"
_DATA_PLANS_DIR    = _PROJECT_ROOT / "data" / "plans"
_DATA_TRAINING_DIR = _PROJECT_ROOT / "data" / "training"
_DATA_LOGS_DIR     = _PROJECT_ROOT / "data" / "logs"


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
    debug_mode: bool = False


@app.post("/plan/load")
async def load_plan(req: LoadPlanRequest):
    global _debug_mode
    plan = build_maze_106_plan(req.mode)
    await twin.set_plan(plan)
    _debug_mode = req.debug_mode
    return {"plan_id": plan.plan_id, "steps": len(plan.steps), "mode": req.mode, "debug_mode": _debug_mode}


@app.post("/plan/load-saved")
async def load_saved_plan(body: dict = Body(...)):
    """Load a saved plan JSON from data/plans/ into the twin executor."""
    global _debug_mode
    plan_id  = body.get("plan_id", "")
    debug    = bool(body.get("debug_mode", False))
    mode_str = body.get("mode", "A")

    path = _DATA_PLANS_DIR / f"{plan_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Plan '{plan_id}' not found")

    raw = json.loads(path.read_text())

    # Normalise both execution-plan and generated-plan formats → PlanStep list
    from models import Plan, PlanStep, ExecutionMode, PlanStatus as PS
    try:
        exec_mode = ExecutionMode(mode_str)
    except ValueError:
        exec_mode = ExecutionMode.A

    steps: list[PlanStep] = []
    raw_steps = raw.get("steps", [])
    for s in raw_steps:
        # Generated plan steps have assignment{robot_id,tool_id,base_id} nested
        # Execution plan steps have robot_id/tool_id/base_id at top level
        assignment = s.get("assignment") or {}
        robot_id = s.get("robot_id") or assignment.get("robot_id") or 1
        tool_id  = s.get("tool_id")  or assignment.get("tool_id")  or 0
        base_id  = s.get("base_id")  or assignment.get("base_id")  or 11
        op_id    = s.get("op_id")    or s.get("op_index")          or 21
        op_name  = s.get("op_name",  f"op{op_id}")
        name     = s.get("name") or s.get("step_name", f"Step {s.get('step_index',0)}")

        # Reconstruct position dict if missing
        position = s.get("position") or {}
        if not position:
            try:
                from plan_builder import _load_pos
                pos_data = _load_pos(s.get("position_name", ""))
                position = pos_data.get("position", {})
            except Exception:
                position = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}

        steps.append(PlanStep(
            step_index=s.get("step_index", len(steps) + 1),
            name=name,
            op_id=op_id,
            op_name=op_name,
            robot_id=robot_id,
            tool_id=tool_id,
            base_id=base_id,
            position_name=s.get("position_name", ""),
            position=position,
            parameter_array=s.get("parameter_array", [0] * 20),
        ))

    plan = Plan(
        plan_id=raw.get("plan_id", plan_id),
        product=raw.get("product", "MAZE_106"),
        mode=exec_mode,
        steps=steps,
    )
    await twin.set_plan(plan)
    _debug_mode = debug
    return {"plan_id": plan.plan_id, "steps": len(plan.steps), "mode": exec_mode, "debug_mode": debug}


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
    _runner = asyncio.create_task(_run_plan(debug=_debug_mode))
    return {"status": "started"}


@app.post("/plan/pause")
async def pause_plan():
    await twin.set_plan_status(PlanStatus.PAUSED)
    return {"status": "paused"}


@app.post("/plan/reset")
async def reset_plan():
    global _runner, _debug_mode, _debug_ack
    if _runner and not _runner.done():
        _runner.cancel()
    await twin.reset_plan()
    _debug_mode = False
    _debug_ack = None
    return {"status": "reset"}


@app.post("/plan/ack")
async def ack_debug_step():
    global _debug_ack
    if _debug_ack is not None:
        _debug_ack.set()
        return {"status": "ack"}
    return {"status": "no_pause"}


@app.get("/debug/status")
async def debug_status():
    global _debug_mode, _debug_ack
    snap = twin.get_snapshot()
    paused_step = None
    if _debug_ack is not None and not _debug_ack.is_set():
        # Find current active step
        if snap.plan:
            for step in snap.plan.steps:
                if step.status == StepStatus.ACTIVE:
                    paused_step = step.step_index
                    break
    return {"debug_mode": _debug_mode, "paused_step": paused_step}


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

async def _run_plan(debug: bool = False) -> None:
    global _debug_ack

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
            await _save_execution_log()
            return

        # Mark active
        started = datetime.now(timezone.utc)
        await twin.update_step(
            step.step_index,
            status=StepStatus.ACTIVE,
            started_at=started,
        )

        # Debug pause: wait for ACK before proceeding
        if debug:
            _debug_ack = asyncio.Event()
            # Broadcast current snapshot so frontend can poll /debug/status
            await twin._broadcast()
            try:
                await asyncio.wait_for(_debug_ack.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                pass
            _debug_ack = None

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
            await _save_execution_log()
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
            await _save_execution_log()
            return

    await twin.set_plan_status(
        PlanStatus.COMPLETED, completed_at=datetime.now(timezone.utc)
    )
    await _save_execution_log()


async def _save_execution_log() -> None:
    """Persist the current plan's execution log to data/logs/."""
    snap = twin.get_snapshot()
    plan = snap.plan
    if not plan:
        return
    _DATA_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_id = f"{plan.plan_id}_exec_{ts}"
    steps_out = []
    for s in plan.steps:
        steps_out.append({
            "step_index": s.step_index,
            "name": s.name,
            "robot_id": s.robot_id,
            "tool_id": s.tool_id,
            "op_id": s.op_id,
            "status": s.status,
            "call_id": s.call_id,
            "result_code": s.result_code,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "duration_ms": s.duration_ms,
            "error_message": s.error_message,
        })
    done = sum(1 for s in plan.steps if s.status == "done")
    failed = sum(1 for s in plan.steps if s.status == "failed")
    total = len(steps_out)
    log_data = {
        "log_id": log_id,
        "plan_id": plan.plan_id,
        "product": plan.product,
        "mode": plan.mode,
        "status": plan.status,
        "started_at": plan.started_at.isoformat() if plan.started_at else None,
        "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        "steps": steps_out,
        "summary": {
            "total_steps": total,
            "completed": done,
            "failed": failed,
            "success_rate": round(done / total, 3) if total else 0,
        },
    }
    (_DATA_LOGS_DIR / f"{log_id}.json").write_text(json.dumps(log_data))


# ── Seed / Clear ──────────────────────────────────────────────────────────────

@app.post("/seed")
async def seed_typedb():
    async def event_stream():
        try:
            # Lazy import in case sys.path wasn't ready at module load
            if _SEED_KG_AVAILABLE:
                build_typeql_fn = _build_typeql
                seed_run_fn = _seed_run
            else:
                try:
                    from scripts.seed_kg import build_typeql as build_typeql_fn, run as seed_run_fn
                except ImportError as e:
                    yield f'data: {json.dumps({"error": f"seed_kg not importable: {e}"})}\n\n'
                    return

            try:
                from typedb.driver import TypeDB, Credentials, DriverOptions
            except ImportError:
                yield 'data: {"error": "typedb-driver not installed"}\n\n'
                return

            host = os.environ.get("TYPEDB_HOST", "localhost")
            port = int(os.environ.get("TYPEDB_PORT", 1729))
            db = os.environ.get("TYPEDB_DB", "capability_kg")
            username = os.environ.get("TYPEDB_USERNAME", "admin")
            password = os.environ.get("TYPEDB_PASSWORD", "password")

            # Test connectivity before running full seed
            try:
                creds = Credentials(username, password)
                driver_opts = DriverOptions(is_tls_enabled=False)
                test_driver = TypeDB.driver(f"{host}:{port}", creds, driver_opts)
                test_driver.close()
            except Exception as e:
                yield f'data: {json.dumps({"error": "TypeDB unreachable", "detail": str(e)})}\n\n'
                return

            statements = await asyncio.to_thread(build_typeql_fn)

            # Emit progress by entity type
            robots = [s for s in statements if "isa robot" in s and s.startswith("insert")]
            tools = [s for s in statements if "isa tool" in s and s.startswith("insert")]
            positions = [s for s in statements if "isa maze-position" in s and s.startswith("insert")]
            steps_stmts = [s for s in statements if "isa process-step" in s and s.startswith("insert")]
            relations = [s for s in statements if s.startswith("match")]

            yield f'data: {json.dumps({"phase": "robots", "count": len(robots)})}\n\n'
            await asyncio.sleep(0.05)
            yield f'data: {json.dumps({"phase": "tools", "count": len(tools)})}\n\n'
            await asyncio.sleep(0.05)
            yield f'data: {json.dumps({"phase": "positions", "count": len(positions)})}\n\n'
            await asyncio.sleep(0.05)
            yield f'data: {json.dumps({"phase": "steps", "count": len(steps_stmts)})}\n\n'
            await asyncio.sleep(0.05)
            yield f'data: {json.dumps({"phase": "relations", "count": len(relations)})}\n\n'
            await asyncio.sleep(0.05)

            # Run the full seed
            try:
                await asyncio.to_thread(seed_run_fn, host, port, db, False)
                total = len([s for s in statements if not s.startswith("#")])
                yield f'data: {json.dumps({"phase": "done", "total": total})}\n\n'
            except Exception as e:
                yield f'data: {json.dumps({"phase": "error", "detail": str(e)})}\n\n'

        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/clear")
async def clear_typedb():
    try:
        from typedb.driver import TypeDB, Credentials, DriverOptions
    except ImportError:
        raise HTTPException(503, "typedb-driver not installed")

    host = os.environ.get("TYPEDB_HOST", "localhost")
    port = int(os.environ.get("TYPEDB_PORT", 1729))
    db = os.environ.get("TYPEDB_DB", "capability_kg")
    username = os.environ.get("TYPEDB_USERNAME", "admin")
    password = os.environ.get("TYPEDB_PASSWORD", "password")

    try:
        creds = Credentials(username, password)
        driver_opts = DriverOptions(is_tls_enabled=False)
        with TypeDB.driver(f"{host}:{port}", creds, driver_opts) as driver:
            if driver.databases.contains(db):
                driver.databases.get(db).delete()
            driver.databases.create(db)
        return {"status": "cleared"}
    except Exception as e:
        raise HTTPException(503, f"TypeDB unreachable: {e}")


# ── Capability State Management ───────────────────────────────────────────────

@app.get("/capability-states")
async def list_capability_states():
    _DATA_STATES_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for f in sorted(_DATA_STATES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            result.append({
                "state_id": f.stem,
                "description": data.get("description", ""),
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            pass
    return result


@app.post("/capability-states")
async def create_capability_state(body: dict = Body(...)):
    _DATA_STATES_DIR.mkdir(parents=True, exist_ok=True)
    state_id = body.get("state_id") or f"state_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    body["state_id"] = state_id
    path = _DATA_STATES_DIR / f"{state_id}.json"
    path.write_text(json.dumps(body))
    return {"state_id": state_id}


@app.get("/capability-states/{sid}")
async def get_capability_state(sid: str):
    path = _DATA_STATES_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "State not found")
    return json.loads(path.read_text())


@app.put("/capability-states/{sid}")
async def update_capability_state(sid: str, body: dict = Body(...)):
    _DATA_STATES_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATA_STATES_DIR / f"{sid}.json"
    body["state_id"] = sid
    path.write_text(json.dumps(body))
    return {"state_id": sid}


@app.delete("/capability-states/{sid}", status_code=204)
async def delete_capability_state(sid: str):
    path = _DATA_STATES_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "State not found")
    path.unlink()


@app.post("/capability-states/{sid}/apply")
async def apply_capability_state(sid: str):
    """Apply a saved capability state to the live digital twin (resets robot states)."""
    path = _DATA_STATES_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, f"State '{sid}' not found")
    state_data = json.loads(path.read_text())
    for r in state_data.get("robots", []):
        robot_id = r.get("robot_id")
        if robot_id is None:
            continue
        tool_id = r.get("tool_id", 0)
        await twin.update_robot_state(
            robot_id,
            enabled=r.get("enabled", True),
            tool_id=tool_id,
            tool_attached=tool_id > 0,
            base_frame_id=r.get("base_frame_id", 11),
            busy=False,
            ready=True,
            manually_disabled=False,
            error_id=0,
        )
    return {"applied": sid, "robots_updated": len(state_data.get("robots", []))}


# ── Plan Export / Persistence ─────────────────────────────────────────────────

@app.post("/plans/export")
async def export_plan():
    _DATA_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    snap = twin.get_snapshot()
    if not snap.plan:
        raise HTTPException(400, "No plan loaded")
    plan_data = snap.plan.model_dump(mode="json")
    plan_id = plan_data["plan_id"]
    path = _DATA_PLANS_DIR / f"{plan_id}.json"
    plan_data["exported_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(plan_data))
    return {"plan_id": plan_id, "path": str(path)}


@app.get("/plans")
async def list_plans():
    _DATA_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for f in sorted(_DATA_PLANS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            result.append({
                "plan_id": data.get("plan_id", f.stem),
                "product": data.get("product", ""),
                "mode": data.get("mode", ""),
                "status": data.get("status", ""),
                "step_count": len(data.get("steps", [])),
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            pass
    return result


@app.get("/plans/{pid}")
async def get_plan(pid: str):
    path = _DATA_PLANS_DIR / f"{pid}.json"
    if not path.exists():
        raise HTTPException(404, "Plan not found")
    return json.loads(path.read_text())


@app.delete("/plans/{pid}", status_code=204)
async def delete_plan(pid: str):
    path = _DATA_PLANS_DIR / f"{pid}.json"
    if not path.exists():
        raise HTTPException(404, "Plan not found")
    path.unlink()


@app.post("/plans/save-generated")
async def save_generated_plan(body: dict = Body(...)):
    """Save a generated plan (arbitrary JSON) to data/plans/."""
    _DATA_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan_id = body.get("plan_id") or f"gen_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    body["plan_id"] = plan_id
    (_DATA_PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(body, indent=2))
    return {"plan_id": plan_id}


@app.post("/plans/{pid}/train")
async def queue_for_training(pid: str):
    _DATA_TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    src = _DATA_PLANS_DIR / f"{pid}.json"
    if not src.exists():
        raise HTTPException(404, "Plan not found")
    dst = _DATA_TRAINING_DIR / f"{pid}.json"
    dst.write_text(src.read_text())
    return {"status": "queued", "plan_id": pid}


# ── Plan Generation ──────────────────────────────────────────────────────────

class GeneratePlanRequest(BaseModel):
    method: str = "typedb_gnn"   # "typedb" | "typedb_gnn" | "gnn"
    state_id: Optional[str] = None   # saved capability state; None = use current twin state


def _workcell_from_state(state_data: dict):
    """Convert a saved capability-state dict to WorkcellRuntimeState."""
    from capability_matcher.models import WorkcellRuntimeState, RobotRuntimeState
    robots = []
    for r in state_data.get("robots", []):
        tid = r.get("tool_id", 0)
        robots.append(RobotRuntimeState(
            robot_id=r["robot_id"],
            enabled=r.get("enabled", True),
            error_id=r.get("error_id", 0),
            busy=False,
            ready=True,
            manually_disabled=False,
            tool_id=tid,
            tool_attached=tid > 0,
            active_base_id=r.get("base_frame_id", 11),
        ))
    return WorkcellRuntimeState(estop_ok=True, conveyor_running=True, robots=robots)


def _workcell_from_twin():
    """Convert live DigitalTwin workcell state to WorkcellRuntimeState."""
    from capability_matcher.models import WorkcellRuntimeState, RobotRuntimeState
    snap = twin.get_snapshot()
    robots = [
        RobotRuntimeState(
            robot_id=r.robot_id,
            enabled=r.enabled,
            error_id=r.error_id,
            busy=r.busy,
            ready=r.ready,
            manually_disabled=r.manually_disabled,
            tool_id=r.tool_id,
            tool_attached=r.tool_attached,
            active_base_id=r.base_frame_id,
        )
        for r in snap.workcell.robots
    ]
    return WorkcellRuntimeState(
        estop_ok=snap.workcell.estop_ok,
        conveyor_running=snap.workcell.conveyor_running,
        robots=robots,
    )


@app.post("/plan/generate")
async def generate_plan(req: GeneratePlanRequest):
    try:
        from capability_matcher.loader import load_process_steps, load_robots, load_all_positions
        from capability_matcher.filter import run_filter, run_filter_verbose
        from capability_matcher.ranker import rank_candidates
        from capability_matcher.models import (
            MatchRequest, ProcessStep as CmStep,
            WorkcellRuntimeState, RobotRuntimeState, RobotCandidate,
        )
        from plan_builder import _load_pos, _get_base_id, _get_gripper_open, \
            _encode_pick_vertical, _encode_place_vertical, _encode_approach_vertical, OP_NAMES
    except ImportError as e:
        raise HTTPException(503, f"Matching engine unavailable: {e}")

    # Build workcell state
    if req.state_id:
        path = _DATA_STATES_DIR / f"{req.state_id}.json"
        if not path.exists():
            raise HTTPException(404, f"State '{req.state_id}' not found")
        state_data = json.loads(path.read_text())
        workcell = _workcell_from_state(state_data)
    else:
        workcell = _workcell_from_twin()

    all_robots = list(load_robots().values())
    steps = load_process_steps()
    result_steps = []

    def _score_gnn_only(robot_id: int, op_index: int) -> float:
        """GNN-only: score without constraint verification (history + priority)."""
        import sqlite3 as _sq, os as _os
        from pathlib import Path as _P
        db = _P(_os.environ.get("DATA_DIR", "./data")) / "research.db"
        priority = {1: 1.0, 2: 0.95, 3: 0.90}.get(robot_id, 0.5)
        if not db.exists():
            return round(0.4 * priority, 4)
        try:
            con = _sq.connect(db)
            row = con.execute(
                "SELECT AVG(CAST(success AS REAL)) FROM execution_history "
                "WHERE robot_id=? AND op_index=?", (robot_id, op_index)
            ).fetchone()
            con.close()
            hist = float(row[0]) if row and row[0] is not None else 0.5
        except Exception:
            hist = 0.5
        return round(0.6 * hist + 0.4 * priority, 4)

    for s in steps:
        cm_step = CmStep(
            step_index=s.step_index,
            step_name=s.step_name,
            op_index=s.op_index,
            component=s.component,
            position_name=s.position_name,
            preconditions=s.preconditions,
            postconditions=s.postconditions,
        )
        match_req = MatchRequest(step=cm_step, workcell=workcell)

        if req.method == "typedb":
            # Phase 1 only — take first passing robot, no ranking
            passing, rejected = run_filter_verbose(match_req)
            ranked = passing  # unsorted / unscored, just pick first
            for c in ranked:
                c.score = 1.0  # fixed score (rule-based selection)
            filtered_out = [
                {"robot_id": r.robot_id, "violations": [v.detail for v in r.violations]}
                for r in rejected
            ]

        elif req.method == "typedb_gnn":
            # Phase 1 + Phase 2
            passing, rejected = run_filter_verbose(match_req)
            ranked = rank_candidates(passing, match_req)
            filtered_out = [
                {"robot_id": r.robot_id, "violations": [v.detail for v in r.violations]}
                for r in rejected
            ]

        else:  # gnn
            # Skip Phase 1 — score all robots unconditionally
            _, all_violations = run_filter_verbose(match_req)
            all_candidates = []
            for r in workcell.robots:
                viols = [v for v in all_violations if v.robot_id == r.robot_id] if False else []
                score = _score_gnn_only(r.robot_id, s.op_index)
                all_candidates.append(RobotCandidate(
                    robot_id=r.robot_id,
                    tool_id=r.tool_id,
                    active_base_id=r.active_base_id,
                    score=score,
                    violations=[],
                ))
            all_candidates.sort(key=lambda c: c.score, reverse=True)
            ranked = all_candidates
            filtered_out = []

        # Build parameter_array for the best assignment
        best = ranked[0] if ranked else None
        param_array = [0] * 20
        if best:
            try:
                pos_data = _load_pos(s.position_name)
                base_id = _get_base_id(pos_data, best.robot_id)
                gripper_open = _get_gripper_open(pos_data)
                if s.op_index in (20, 21):
                    param_array = _encode_pick_vertical(pos_data, base_id=base_id, gripper_open=gripper_open)
                elif s.op_index in (30, 31):
                    param_array = _encode_place_vertical(pos_data, base_id=base_id, gripper_open=gripper_open)
                else:
                    param_array = _encode_approach_vertical(pos_data, base_id=base_id)
                best.active_base_id = base_id
            except Exception:
                pass

        result_steps.append({
            "step_index": s.step_index,
            "step_name": s.step_name,
            "op_index": s.op_index,
            "op_name": OP_NAMES.get(s.op_index, f"op{s.op_index}"),
            "component": s.component,
            "position_name": s.position_name,
            "assignment": {
                "robot_id": best.robot_id if best else None,
                "tool_id": best.tool_id if best else None,
                "base_id": best.active_base_id if best else None,
                "score": best.score if best else None,
            } if best else None,
            "alternatives": [
                {"robot_id": c.robot_id, "tool_id": c.tool_id, "score": c.score}
                for c in ranked[1:3]
            ],
            "filtered_out": filtered_out,
            "parameter_array": param_array,
            "edited": False,
        })

    plan_id = f"gen_{req.method}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    result = {
        "plan_id": plan_id,
        "product": "MAZE_106",
        "generated_by": req.method,
        "state_id": req.state_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "steps": result_steps,
    }
    return result


# ── Visualization Endpoints ───────────────────────────────────────────────────

@app.get("/viz/stats")
async def viz_stats():
    _DATA_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    total_plans = 0
    total_steps = 0
    completed_steps = 0
    failed_steps = 0
    robot_scores: dict[str, list[float]] = {}
    robot_utilization: dict[str, int] = {}

    for f in _DATA_PLANS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            total_plans += 1
            steps = data.get("steps", [])
            total_steps += len(steps)
            for s in steps:
                if s.get("status") == "done":
                    completed_steps += 1
                elif s.get("status") == "failed":
                    failed_steps += 1
                robot_key = f"R{s.get('robot_id', '?')}"
                robot_utilization[robot_key] = robot_utilization.get(robot_key, 0) + 1
                rc = s.get("result_code")
                if rc is not None:
                    if robot_key not in robot_scores:
                        robot_scores[robot_key] = []
                    robot_scores[robot_key].append(1.0 if rc == 0 else 0.0)
        except Exception:
            pass

    avg_score_by_robot = {
        k: round(sum(v) / len(v), 3) for k, v in robot_scores.items() if v
    }

    return {
        "total_plans": total_plans,
        "total_steps": total_steps,
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "avg_score_by_robot": avg_score_by_robot,
        "robot_utilization": robot_utilization,
    }


@app.get("/viz/history")
async def viz_history():
    db_path = _PROJECT_ROOT / "data" / "research.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row["name"] for row in cur.fetchall()]
        if "execution_history" in tables:
            cur.execute(
                "SELECT * FROM execution_history ORDER BY id DESC LIMIT 50"
            )
        elif tables:
            cur.execute(f"SELECT * FROM {tables[0]} LIMIT 50")
        else:
            conn.close()
            return []
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return []


@app.get("/viz/diagram")
async def viz_diagram():
    snap = twin.get_snapshot()
    if not snap.plan:
        return {"diagram": ""}

    plan = snap.plan
    lines = ["sequenceDiagram"]

    # Collect unique robots
    robot_ids = sorted({s.robot_id for s in plan.steps})
    lines.append("  participant Planner")
    for rid in robot_ids:
        lines.append(f"  participant R{rid}")

    for step in plan.steps:
        status_note = ""
        if step.status == "done":
            status_note = " [DONE]"
        elif step.status == "failed":
            status_note = " [FAILED]"
        elif step.status == "active":
            status_note = " [ACTIVE]"
        lines.append(
            f"  Planner->>R{step.robot_id}: step {step.step_index} {step.name}{status_note}"
        )

    return {"diagram": "\n".join(lines)}


@app.get("/viz/execution-logs")
async def viz_execution_logs():
    """List all saved execution log files, newest first."""
    _DATA_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logs = []
    for f in sorted(_DATA_LOGS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            logs.append({
                "log_id": data.get("log_id", f.stem),
                "plan_id": data.get("plan_id", ""),
                "product": data.get("product", ""),
                "mode": data.get("mode", ""),
                "status": data.get("status", ""),
                "started_at": data.get("started_at"),
                "completed_at": data.get("completed_at"),
                "summary": data.get("summary", {}),
            })
        except Exception:
            pass
    return logs


@app.get("/viz/execution-logs/{log_id}")
async def viz_execution_log_detail(log_id: str):
    """Return full execution log detail including all steps."""
    f = _DATA_LOGS_DIR / f"{log_id}.json"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    return json.loads(f.read_text())


@app.get("/viz/execution-logs/{log_id}/download")
async def viz_execution_log_download(log_id: str):
    """Download execution log as a JSON file attachment."""
    f = _DATA_LOGS_DIR / f"{log_id}.json"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    from fastapi.responses import Response
    content = f.read_text()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{log_id}.json"'},
    )


@app.get("/resource-data")
async def resource_data():
    """Return tools, robots (with toolchanger slots), valid base IDs, components, and positions
    for building dropdowns in the State panel editor."""
    try:
        from capability_matcher.loader import load_robots, load_tools, load_all_positions, load_process_steps
    except ImportError:
        return {"tools": [], "robots": [], "base_ids": [], "components": [], "positions": [], "product": ""}

    robots_map = load_robots()
    tools_map = load_tools()
    positions_map = load_all_positions()
    steps = load_process_steps()

    # Build tool list: [{tool_id, label, model, type}]
    tools_out = []
    for t in sorted(tools_map.values(), key=lambda x: x.tool_id):
        tools_out.append({
            "tool_id": t.tool_id,
            "label": t.label,
            "model": t.model,
            "type": t.type,
        })

    # Build robots with their toolchanger-available tool IDs
    robots_out = []
    for r in sorted(robots_map.values(), key=lambda x: x.robot_id):
        slot_tools = [tc.tool_id if hasattr(tc, "tool_id") else tc["tool_id"] for tc in r.toolchangers] if hasattr(r, "toolchangers") and r.toolchangers else []
        robots_out.append({
            "robot_id": r.robot_id,
            "label": r.label,
            "model": r.model,
            "slot_tool_ids": slot_tools,
        })

    # Collect all valid base IDs across all positions (deduplicated + sorted)
    base_ids: set[int] = set()
    for p in positions_map.values():
        for vb in (p.valid_bases or []):
            for bid in (vb.base_id_list if hasattr(vb, "base_id_list") else []):
                base_ids.add(bid)

    # BOM quantities per component (product-specific constants for MAZE_106)
    BOM_QTY: dict[str, int] = {
        "BALL_106":       2,
        "SNAP-RIVET_106": 4,
    }

    # Components from process steps (unique, preserving insertion order)
    components_seen: dict[str, str] = {}  # component → first pick position_name
    product_name = ""
    for s in steps:
        if hasattr(s, "component") and s.component:
            if s.component not in components_seen:
                components_seen[s.component] = getattr(s, "position_name", "")
        if not product_name and hasattr(s, "step_name"):
            product_name = "MAZE_106"

    all_positions = sorted(positions_map.keys())

    # Position groups for filtered dropdowns
    def _num_sort(name: str) -> int:
        import re
        m = re.search(r'(\d+)$', name)
        return int(m.group(1)) if m else 0

    rivet_storage  = sorted((p for p in all_positions if "storage_rivet" in p), key=_num_sort)
    rivet_place    = sorted(p for p in all_positions if "place_rivet"     in p or "push_in_rivet" in p)
    ball_positions = sorted(p for p in all_positions if "glass" in p or "ball" in p)

    position_groups = {
        "rivet_storage":  rivet_storage,
        "rivet_place":    rivet_place,
        "ball_positions": ball_positions,
        "all":            all_positions,
    }

    # Default storage positions per consumable instance
    def _default_positions(comp: str) -> list[str]:
        if comp == "SNAP-RIVET_106":
            return rivet_storage[:4] if len(rivet_storage) >= 4 else rivet_storage
        if comp == "BALL_106":
            return [p for p in ball_positions if "storage" in p][:2]
        return []

    components_out = []
    for comp, first_pos in components_seen.items():
        qty = BOM_QTY.get(comp, 1)
        defaults = _default_positions(comp)
        components_out.append({
            "component":      comp,
            "qty":            qty,
            "default_position": first_pos,
            "instance_defaults": [
                defaults[i] if i < len(defaults) else first_pos
                for i in range(qty)
            ],
        })

    return {
        "product":         product_name,
        "tools":           tools_out,
        "robots":          robots_out,
        "base_ids":        sorted(base_ids),
        "components":      components_out,
        "positions":       all_positions,
        "position_groups": position_groups,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=True)
