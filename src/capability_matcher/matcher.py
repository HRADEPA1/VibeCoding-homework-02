from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .filter import run_filter
from .models import MatchRequest, MatchResult, RobotCandidate
from .ranker import rank_candidates

def _db_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")) / "research.db"


def _ensure_history_table() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            step_index  INTEGER NOT NULL,
            robot_id    INTEGER NOT NULL,
            tool_id     INTEGER NOT NULL,
            op_index    INTEGER NOT NULL,
            call_id     INTEGER,
            success     INTEGER NOT NULL DEFAULT 1,
            timestamp   TEXT NOT NULL
        )
        """
    )
    con.commit()
    con.close()


class CapabilityMatcher:
    """Main capability matching orchestrator. Runs Phase 1 filter then Phase 2 ranker."""

    def __init__(self) -> None:
        _ensure_history_table()

    def match(self, request: MatchRequest) -> MatchResult:
        """Run Phase 1 + Phase 2 for one step. Returns empty ranked_options if no robot is feasible."""
        feasible = run_filter(request)
        ranked = rank_candidates(feasible, request)
        return MatchResult(
            step_index=request.step.step_index,
            step_name=request.step.step_name,
            position_name=request.step.position_name,
            ranked_options=ranked,
            fallback_used=True,  # always true until GNN is trained
        )

    def record_outcome(
        self,
        step_index: int,
        robot_id: int,
        tool_id: int,
        op_index: int,
        call_id: int | None,
        success: bool,
    ) -> None:
        """Persist execution outcome to SQLite execution_history for ranker learning."""
        _ensure_history_table()
        con = sqlite3.connect(_db_path())
        con.execute(
            "INSERT INTO execution_history "
            "(step_index, robot_id, tool_id, op_index, call_id, success, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                step_index,
                robot_id,
                tool_id,
                op_index,
                call_id,
                int(success),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
        con.close()

    def match_plan(self, requests: list[MatchRequest]) -> list[MatchResult]:
        """Match a sequence of steps. Returns one MatchResult per request in the same order."""
        return [self.match(r) for r in requests]
