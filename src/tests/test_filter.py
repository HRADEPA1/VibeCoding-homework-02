"""
Unit tests for Phase 1 constraint filter.
No TypeDB or OPC UA required.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_matcher.filter import run_filter
from capability_matcher.models import (
    MatchRequest,
    ProcessStep,
    RobotRuntimeState,
    WorkcellRuntimeState,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _step(step_index=1, op_index=21, position_name="maze_106_pick_insert_start") -> ProcessStep:
    return ProcessStep(
        step_index=step_index,
        step_name="Test Step",
        op_index=op_index,
        component="TEST",
        position_name=position_name,
    )


def _robot(robot_id=1, tool_id=2, base_id=11, tool_attached=True, **kwargs) -> RobotRuntimeState:
    return RobotRuntimeState(
        robot_id=robot_id,
        tool_id=tool_id,
        tool_attached=tool_attached,
        active_base_id=base_id,
        **kwargs,
    )


def _workcell(*robots: RobotRuntimeState, estop_ok=True) -> WorkcellRuntimeState:
    return WorkcellRuntimeState(estop_ok=estop_ok, robots=list(robots))


def _request(step=None, workcell=None) -> MatchRequest:
    return MatchRequest(step=step or _step(), workcell=workcell or _workcell(_robot()))


# ── Tests: basic filter ───────────────────────────────────────────────────────

def test_nominal_robot1_passes():
    """R1 with T2 on base 11 should pass for pick_insert_start (op 21)."""
    result = run_filter(_request())
    assert len(result) == 1
    assert result[0].robot_id == 1
    assert result[0].tool_id == 2


def test_wrong_tool_excluded():
    """R1 with tool 9 (pneumatic, not valid at this position) should be excluded."""
    r = _robot(tool_id=9)
    result = run_filter(_request(workcell=_workcell(r)))
    assert len(result) == 0


def test_wrong_base_excluded():
    """R1 with base 99 (not in valid_bases) should be excluded."""
    r = _robot(base_id=99)
    result = run_filter(_request(workcell=_workcell(r)))
    assert len(result) == 0


def test_busy_robot_excluded():
    """Busy robot should be excluded regardless of tool/base."""
    r = _robot(busy=True, ready=False)
    result = run_filter(_request(workcell=_workcell(r)))
    assert len(result) == 0


def test_error_robot_excluded():
    """Robot with error_id != 0 should be excluded."""
    r = _robot(error_id=5)
    result = run_filter(_request(workcell=_workcell(r)))
    assert len(result) == 0


def test_manually_disabled_excluded():
    r = _robot(manually_disabled=True)
    result = run_filter(_request(workcell=_workcell(r)))
    assert len(result) == 0


def test_estop_not_ok_excludes_all():
    r = _robot()
    result = run_filter(_request(workcell=_workcell(r, estop_ok=False)))
    assert len(result) == 0


def test_no_tool_attached_excluded():
    r = _robot(tool_attached=False, tool_id=0)
    result = run_filter(_request(workcell=_workcell(r)))
    assert len(result) == 0


def test_result_sorted_ascending_by_robot_id():
    """Filter output is always sorted ascending by robot_id (positions are robot-specific)."""
    # maze_106_place_glass accepts robot 2 / tool 10; everything else fails
    step = _step(op_index=41, position_name="maze_106_place_glass")
    r1 = _robot(robot_id=1, tool_id=10, base_id=11)   # fails: no base entry for R1
    r2 = _robot(robot_id=2, tool_id=10, base_id=11)   # passes
    result = run_filter(_request(step=step, workcell=_workcell(r1, r2)))
    robot_ids = [c.robot_id for c in result]
    assert robot_ids == sorted(robot_ids)
    assert result[0].robot_id == 2


def test_only_feasible_robot_returned_when_others_fail():
    r1 = _robot(robot_id=1, tool_id=2, base_id=11)
    r2 = _robot(robot_id=2, tool_id=9, base_id=11)  # wrong tool
    r3 = _robot(robot_id=3, tool_id=2, base_id=99)  # wrong base
    result = run_filter(_request(workcell=_workcell(r1, r2, r3)))
    assert len(result) == 1
    assert result[0].robot_id == 1


def test_all_robots_fail_returns_empty():
    r1 = _robot(error_id=1)
    r2 = _robot(robot_id=2, busy=True, ready=False)
    r3 = _robot(robot_id=3, manually_disabled=True)
    result = run_filter(_request(workcell=_workcell(r1, r2, r3)))
    assert result == []


# ── Tests: op_index constraint ────────────────────────────────────────────────

def test_unsupported_op_excluded():
    """op 999 is not in any robot's supported_ops."""
    step = _step(op_index=999)
    r = _robot()
    result = run_filter(_request(step=step, workcell=_workcell(r)))
    assert result == []


def test_approach_vertical_op41_passes():
    """Op 41 is supported; use a position that accepts T2."""
    step = _step(op_index=41, position_name="maze_106_place_rivet_1")
    r = _robot(tool_id=2, base_id=11)
    result = run_filter(_request(step=step, workcell=_workcell(r)))
    assert len(result) == 1


# ── Tests: position with robot 2 / tool 10 ───────────────────────────────────

def test_place_glass_accepts_robot2_tool10():
    """maze_106_place_glass requires robot 2 and tool 10."""
    step = _step(op_index=41, position_name="maze_106_place_glass")
    r = _robot(robot_id=2, tool_id=10, base_id=11)
    result = run_filter(_request(step=step, workcell=_workcell(r)))
    assert len(result) == 1
    assert result[0].robot_id == 2


def test_place_glass_rejects_robot1():
    """maze_106_place_glass has no valid_bases entry for robot 1."""
    step = _step(op_index=41, position_name="maze_106_place_glass")
    r = _robot(robot_id=1, tool_id=10, base_id=11)
    result = run_filter(_request(step=step, workcell=_workcell(r)))
    assert result == []
