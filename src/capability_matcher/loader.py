from __future__ import annotations
import json
import os
from functools import lru_cache

from .models import RobotSpec, ToolSpec, PositionSpec, ProcessStep

_RESOURCES = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "montrac"
)


def _load_json(path: str) -> object:
    with open(path) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_robots() -> dict[int, RobotSpec]:
    """Return all robots keyed by robot_id, cached after first load."""
    data = _load_json(os.path.join(_RESOURCES, "robots.json"))
    return {r["robot_id"]: RobotSpec(**r) for r in data}


@lru_cache(maxsize=1)
def load_tools() -> dict[int, ToolSpec]:
    """Return all tools keyed by tool_id, cached after first load."""
    data = _load_json(os.path.join(_RESOURCES, "tools.json"))
    return {t["tool_id"]: ToolSpec(**t) for t in data}


@lru_cache(maxsize=1)
def load_process_steps(product: str = "MAZE_106") -> list[ProcessStep]:
    """Return ordered list of process steps for the given product."""
    data = _load_json(os.path.join(_RESOURCES, "process_steps.json"))
    return [ProcessStep(**s) for s in data["steps"]]


@lru_cache(maxsize=64)
def load_position(position_name: str) -> PositionSpec:
    """Load and cache a single position file by name."""
    path = os.path.join(_RESOURCES, "maze-positions", f"{position_name}.json")
    return PositionSpec(**_load_json(path))


def load_all_positions() -> dict[str, PositionSpec]:
    """Load all position files from maze-positions/. Not cached — use sparingly."""
    pos_dir = os.path.join(_RESOURCES, "maze-positions")
    result: dict[str, PositionSpec] = {}
    for fname in os.listdir(pos_dir):
        if fname.endswith(".json"):
            name = fname[:-5]
            result[name] = load_position(name)
    return result
