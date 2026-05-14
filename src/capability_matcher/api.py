"""
FastAPI REST API for capability matching.

Endpoints:
    POST /match          - match a single step
    POST /match/plan     - match all steps in a plan
    POST /outcome        - record execution outcome
    GET  /steps          - list all process steps
    GET  /health         - health check
"""
from __future__ import annotations
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from .loader import load_process_steps
from .matcher import CapabilityMatcher
from .models import MatchRequest, MatchResult, RobotRuntimeState, WorkcellRuntimeState

app = FastAPI(title="Capability Matcher", version="0.1.0")
_matcher = CapabilityMatcher()


class OutcomePayload(BaseModel):
    step_index: int
    robot_id: int
    tool_id: int
    op_index: int
    call_id: int | None = None
    success: bool


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/steps")
async def list_steps() -> list[dict[str, Any]]:
    return [s.model_dump() for s in load_process_steps()]


@app.post("/match", response_model=MatchResult)
async def match_step(request: MatchRequest) -> MatchResult:
    result = _matcher.match(request)
    if not result.ranked_options:
        raise HTTPException(status_code=409, detail="no feasible robot found for step")
    return result


@app.post("/match/plan")
async def match_plan(requests: list[MatchRequest]) -> list[dict[str, Any]]:
    results = _matcher.match_plan(requests)
    return [r.model_dump() for r in results]


@app.post("/outcome", status_code=204, response_class=Response)
async def record_outcome(payload: OutcomePayload) -> Response:
    _matcher.record_outcome(
        step_index=payload.step_index,
        robot_id=payload.robot_id,
        tool_id=payload.tool_id,
        op_index=payload.op_index,
        call_id=payload.call_id,
        success=payload.success,
    )
    return Response(status_code=204)
