"""
Phase 2 — Ranking.

v1: rule-based ranker (baseline).
Scores candidates by: historical success rate from SQLite (if available),
then breaks ties by robot_id priority (lower = preferred).

GNN ranker will replace this module when training data is available.
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path

from .models import MatchRequest, RobotCandidate

_ROBOT_PRIORITY = {1: 1.0, 2: 0.95, 3: 0.90}


def _db_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")) / "research.db"


def _history_score(robot_id: int, op_index: int) -> float:
    """Returns success rate from execution_history, or 0.5 if no data."""
    p = _db_path()
    if not p.exists():
        return 0.5
    try:
        con = sqlite3.connect(p)
        row = con.execute(
            "SELECT AVG(CAST(success AS REAL)) FROM execution_history "
            "WHERE robot_id = ? AND op_index = ?",
            (robot_id, op_index),
        ).fetchone()
        con.close()
        rate = row[0] if row and row[0] is not None else 0.5
        return float(rate)
    except Exception:
        return 0.5


def rank_candidates(
    candidates: list[RobotCandidate],
    request: MatchRequest,
) -> list[RobotCandidate]:
    """
    Assigns scores and returns candidates sorted descending by score.
    Score = 0.6 * history_rate + 0.4 * priority_weight.
    """
    if not candidates:
        return []

    op_index = request.step.op_index
    for c in candidates:
        history = _history_score(c.robot_id, op_index)
        priority = _ROBOT_PRIORITY.get(c.robot_id, 0.5)
        c.score = round(0.6 * history + 0.4 * priority, 4)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
