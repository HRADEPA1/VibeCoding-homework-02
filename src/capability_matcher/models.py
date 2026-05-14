from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """Static specification of a gripper/tool loaded from tools.json."""

    tool_id: int
    label: str
    model: str
    type: str
    can_grip: bool
    can_reference: bool
    grip_range_um: list[int]
    payload_kg: float


class ToolchangerSlot(BaseModel):
    """One slot in a robot's tool rack."""

    slot: int
    tool_id: int


class RobotSpec(BaseModel):
    """Static specification of a robot loaded from robots.json."""

    robot_id: int
    label: str
    model: str
    supported_ops: list[int]
    toolchangers: list[ToolchangerSlot]


class PositionValidTool(BaseModel):
    """Tool constraint for a specific position: which tool and required open position (µm)."""

    tool_id: int
    open_position: int


class PositionValidBase(BaseModel):
    """Valid base frame IDs for a specific robot at a position."""

    robot_id: int
    base_id_list: list[int]


class PositionSpec(BaseModel):
    """Full specification of a named maze position loaded from maze-positions/{name}.json."""

    position_name: str
    description: str = ""
    valid_bases: list[PositionValidBase]
    valid_tools: list[PositionValidTool]
    position: dict[str, float]


class ProcessStep(BaseModel):
    """One step in an assembly or disassembly sequence."""

    step_index: int
    step_name: str
    op_index: int
    component: str
    position_name: str
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)


# ── Runtime state (Mode A: loaded from JSON; Mode C: from OPC UA) ──────────

class RobotRuntimeState(BaseModel):
    """Current OPC UA state of one robot at the time of a matching request."""

    robot_id: int
    enabled: bool = True
    busy: bool = False
    ready: bool = True
    error_id: int = 0
    manually_disabled: bool = False
    tool_id: int = 0
    tool_attached: bool = True
    active_base_id: int = 11


class WorkcellRuntimeState(BaseModel):
    """Snapshot of the full workcell: safety flags + per-robot state."""

    estop_ok: bool = True
    conveyor_running: bool = True
    robots: list[RobotRuntimeState] = Field(default_factory=list)


# ── Matching I/O ──────────────────────────────────────────────────────────────

class MatchRequest(BaseModel):
    """Input to the capability matcher: one process step + current workcell state."""

    step: ProcessStep
    workcell: WorkcellRuntimeState


class ConstraintViolation(BaseModel):
    """A single hard constraint that a robot failed during Phase 1 filtering."""

    constraint: str
    detail: str


class RobotCandidate(BaseModel):
    """One feasible (robot, tool) assignment scored by the ranker."""

    robot_id: int
    tool_id: int
    active_base_id: int
    score: float
    violations: list[ConstraintViolation] = Field(default_factory=list)


class MatchResult(BaseModel):
    """Output of one matching call: ranked feasible assignments for a process step."""

    step_index: int
    step_name: str
    position_name: str
    ranked_options: list[RobotCandidate]
    fallback_used: bool = False

    @property
    def best(self) -> Optional[RobotCandidate]:
        """Highest-scored candidate, or None if no feasible robot was found."""
        return self.ranked_options[0] if self.ranked_options else None
