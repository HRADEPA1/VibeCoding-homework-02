"""Unit tests for CapabilityMatcher (filter + ranker integration)."""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from capability_matcher.matcher import CapabilityMatcher
from capability_matcher.models import (
    MatchRequest,
    ProcessStep,
    RobotRuntimeState,
    WorkcellRuntimeState,
)


def _workcell_all_idle():
    return WorkcellRuntimeState(
        robots=[
            RobotRuntimeState(robot_id=1, tool_id=2, active_base_id=11),
            RobotRuntimeState(robot_id=2, tool_id=10, active_base_id=11),
            RobotRuntimeState(robot_id=3, tool_id=2, active_base_id=11),
        ]
    )


def _step1():
    return ProcessStep(
        step_index=1,
        step_name="Pick START Insert",
        op_index=21,
        component="START-INSERT_106",
        position_name="maze_106_pick_insert_start",
    )


def test_match_returns_result():
    matcher = CapabilityMatcher()
    result = matcher.match(MatchRequest(step=_step1(), workcell=_workcell_all_idle()))
    assert result.step_index == 1
    assert len(result.ranked_options) >= 1


def test_match_best_is_highest_score():
    matcher = CapabilityMatcher()
    result = matcher.match(MatchRequest(step=_step1(), workcell=_workcell_all_idle()))
    scores = [c.score for c in result.ranked_options]
    assert scores == sorted(scores, reverse=True)


def test_record_outcome_persists():
    matcher = CapabilityMatcher()
    matcher.record_outcome(1, 1, 2, 21, call_id=42, success=True)
    import sqlite3
    from pathlib import Path as P
    db = P(os.environ["DATA_DIR"]) / "research.db"
    con = sqlite3.connect(db)
    row = con.execute("SELECT COUNT(*) FROM execution_history WHERE call_id=42").fetchone()
    con.close()
    assert row[0] == 1


def test_match_plan_full_assembly():
    from capability_matcher.loader import load_process_steps
    matcher = CapabilityMatcher()
    steps = load_process_steps()
    workcell = _workcell_all_idle()
    requests = [MatchRequest(step=s, workcell=workcell) for s in steps]
    results = matcher.match_plan(requests)
    assert len(results) == len(steps)
    # Every step should have at least one feasible candidate
    no_match = [r for r in results if not r.ranked_options]
    assert no_match == [], f"steps with no match: {[r.step_name for r in no_match]}"
