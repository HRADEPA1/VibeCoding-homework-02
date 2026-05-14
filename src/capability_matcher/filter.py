"""
Phase 1 — Hard constraint filter.

Pure Python implementation (no TypeDB required).
Returns only (robot, tool, base_id) triples that satisfy every hard constraint
for a given process step and current workcell state.
"""
from __future__ import annotations

from .loader import load_robots, load_position
from .models import (
    ConstraintViolation,
    MatchRequest,
    PositionSpec,
    RobotCandidate,
    RobotRuntimeState,
)


def _robot_available(state: RobotRuntimeState, workcell_estop_ok: bool) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    if not state.enabled:
        violations.append(ConstraintViolation(constraint="enabled", detail="robot is disabled"))
    if state.manually_disabled:
        violations.append(ConstraintViolation(constraint="manually_disabled", detail="robot manually disabled"))
    if state.error_id != 0:
        violations.append(ConstraintViolation(constraint="error_id", detail=f"robot error {state.error_id}"))
    if state.busy:
        violations.append(ConstraintViolation(constraint="busy", detail="robot is busy"))
    if not state.ready:
        violations.append(ConstraintViolation(constraint="ready", detail="robot not ready"))
    if not workcell_estop_ok:
        violations.append(ConstraintViolation(constraint="estop", detail="e-stop not OK"))
    return violations


def _op_supported(robot_id: int, op_index: int) -> list[ConstraintViolation]:
    robots = load_robots()
    spec = robots.get(robot_id)
    if spec is None:
        return [ConstraintViolation(constraint="robot_exists", detail=f"robot {robot_id} not in spec")]
    if op_index not in spec.supported_ops:
        return [ConstraintViolation(constraint="op_supported", detail=f"op {op_index} not supported by robot {robot_id}")]
    return []


def _tool_valid(tool_id: int, open_position_required: int, pos: PositionSpec) -> list[ConstraintViolation]:
    valid_tool_ids = {vt.tool_id for vt in pos.valid_tools}
    if tool_id not in valid_tool_ids:
        return [ConstraintViolation(
            constraint="tool_valid",
            detail=f"tool {tool_id} not in valid tools {sorted(valid_tool_ids)} for position {pos.position_name}",
        )]
    return []


def _base_reachable(robot_id: int, active_base_id: int, pos: PositionSpec) -> list[ConstraintViolation]:
    for vb in pos.valid_bases:
        if vb.robot_id == robot_id:
            if active_base_id in vb.base_id_list:
                return []
            return [ConstraintViolation(
                constraint="base_reachable",
                detail=f"base {active_base_id} not in valid bases {vb.base_id_list} for robot {robot_id} at {pos.position_name}",
            )]
    return [ConstraintViolation(
        constraint="base_reachable",
        detail=f"robot {robot_id} has no valid base entry for position {pos.position_name}",
    )]


def _required_open_position(tool_id: int, pos: PositionSpec) -> int:
    for vt in pos.valid_tools:
        if vt.tool_id == tool_id:
            return vt.open_position
    return 0


def _evaluate_robot(
    state: RobotRuntimeState,
    estop_ok: bool,
    op_index: int,
    pos: PositionSpec,
) -> tuple[RobotCandidate | None, list[ConstraintViolation]]:
    """Evaluate one robot against all hard constraints. Returns (candidate, violations)."""
    violations: list[ConstraintViolation] = []

    violations += _robot_available(state, estop_ok)
    violations += _op_supported(state.robot_id, op_index)

    if not state.tool_attached or state.tool_id == 0:
        violations.append(ConstraintViolation(constraint="tool_attached", detail="no tool attached"))
    else:
        violations += _tool_valid(state.tool_id, 0, pos)
        violations += _base_reachable(state.robot_id, state.active_base_id, pos)

    if violations:
        return None, violations

    return RobotCandidate(
        robot_id=state.robot_id,
        tool_id=state.tool_id,
        active_base_id=state.active_base_id,
        score=0.0,
        violations=[],
    ), []


def run_filter(request: MatchRequest) -> list[RobotCandidate]:
    """Returns feasible (robot, tool, base) triples sorted by robot_id."""
    pos = load_position(request.step.position_name)
    passing, _ = _run_filter_all(request, pos)
    return passing


def run_filter_verbose(
    request: MatchRequest,
) -> tuple[list[RobotCandidate], list[RobotCandidate]]:
    """
    Returns (passing, rejected).
    rejected candidates have violations populated explaining why they failed.
    """
    pos = load_position(request.step.position_name)
    return _run_filter_all(request, pos)


def _run_filter_all(
    request: MatchRequest,
    pos: PositionSpec,
) -> tuple[list[RobotCandidate], list[RobotCandidate]]:
    op_index = request.step.op_index
    estop_ok = request.workcell.estop_ok
    passing: list[RobotCandidate] = []
    rejected: list[RobotCandidate] = []

    for state in request.workcell.robots:
        candidate, violations = _evaluate_robot(state, estop_ok, op_index, pos)
        if candidate is not None:
            passing.append(candidate)
        else:
            rejected.append(RobotCandidate(
                robot_id=state.robot_id,
                tool_id=state.tool_id,
                active_base_id=state.active_base_id,
                score=0.0,
                violations=violations,
            ))

    passing.sort(key=lambda c: c.robot_id)
    return passing, rejected
