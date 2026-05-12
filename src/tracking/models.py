from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    A = "A"  # R&D file-based, no OPC UA
    B = "B"  # Planning/dry-run, produces command plan
    C = "C"  # Real OPC UA execution


class StepStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class RobotState(BaseModel):
    robot_id: int
    enabled: bool = False
    busy: bool = False
    ready: bool = True
    error_id: int = 0
    manually_disabled: bool = False
    tool_id: int = 0
    tool_attached: bool = False
    base_frame_id: int = 0
    program_busy: bool = False
    program_done: bool = False
    program_error: bool = False
    program_ready: bool = True
    program_number: int = 0
    call_id: int = 0
    result_code: int = 0
    tcp_x: float = 0.0
    tcp_y: float = 0.0
    tcp_z: float = 0.0
    tcp_a: float = 0.0


class WorkcellState(BaseModel):
    estop_ok: bool = True
    conveyor_running: bool = True
    robots: list[RobotState] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanStep(BaseModel):
    step_index: int
    name: str
    op_id: int
    op_name: str
    robot_id: int
    tool_id: int
    base_id: int
    position_name: str
    position: dict[str, float]
    parameter_array: list[int] = Field(default_factory=lambda: [0] * 20)
    status: StepStatus = StepStatus.PENDING
    call_id: Optional[int] = None
    result_code: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None


class Plan(BaseModel):
    plan_id: str
    product: str
    mode: ExecutionMode
    status: PlanStatus = PlanStatus.IDLE
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class DigitalTwinSnapshot(BaseModel):
    workcell: WorkcellState
    plan: Optional[Plan] = None
    event: str = "state_update"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
