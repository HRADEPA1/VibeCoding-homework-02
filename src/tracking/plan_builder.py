"""
Builds structured assembly plans from maze position JSON files.
Encodes parameters into Parameter_Array[20] per V4 operation spec.

Units:
  Linear positions: mm → µm  (*1000)
  Angular positions: degrees → millidegrees  (*1000)
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone

from models import Plan, PlanStep, ExecutionMode

POSITIONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "montrac", "maze-positions"
)

OP_NAMES: dict[int, str] = {
    10: "MOVE TO HOME",
    11: "MOVE LIN CART",
    12: "MOVE PTP CART",
    13: "MOVE PTP AXIS",
    20: "PICK",
    21: "PICK VERTICAL",
    30: "PLACE",
    31: "PLACE VERTICAL",
    40: "APPROACH",
    41: "APPROACH VERTICAL",
    50: "BRAKE TEST",
    51: "RUN TOOL CMD",
    200: "PICK PTP",
    201: "PICK PTP RSI",
}

_MM_TO_UM = 1000
_DEG_TO_MDEG = 1000
_DEFAULT_SPEED = 30
_DEFAULT_RETURN = 1
_DEFAULT_PICK_OFFSET = 150_000   # 150 mm approach offset
_DEFAULT_APPROACH_OFFSET = 50_000  # 50 mm
_DEFAULT_GRIPPER_OPEN = 7_000    # µm


def _load_pos(name: str) -> dict:
    path = os.path.join(POSITIONS_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _arr20() -> list[int]:
    return [0] * 20


def _encode_pick_vertical(
    pos: dict,
    speed: int = _DEFAULT_SPEED,
    base_id: int = 11,
    ret: int = _DEFAULT_RETURN,
    offset: int = _DEFAULT_PICK_OFFSET,
    gripper_open: int = _DEFAULT_GRIPPER_OPEN,
) -> list[int]:
    """Op 21 / 20: [X, Y, Z, A, Speed, BaseID, Return, Offset, GripperOpen, ...]"""
    p = pos["position"]
    arr = _arr20()
    arr[0] = int(p["X"] * _MM_TO_UM)
    arr[1] = int(p["Y"] * _MM_TO_UM)
    arr[2] = int(p["Z"] * _MM_TO_UM)
    arr[3] = int(p["A"] * _DEG_TO_MDEG)
    arr[4] = speed
    arr[5] = base_id
    arr[6] = ret
    arr[7] = offset
    arr[8] = gripper_open
    return arr


def _encode_place_vertical(
    pos: dict,
    speed: int = _DEFAULT_SPEED,
    base_id: int = 11,
    ret: int = _DEFAULT_RETURN,
    offset: int = _DEFAULT_PICK_OFFSET,
    gripper_open: int = _DEFAULT_GRIPPER_OPEN,
) -> list[int]:
    """Op 31 / 30: same layout as pick."""
    return _encode_pick_vertical(pos, speed, base_id, ret, offset, gripper_open)


def _encode_approach_vertical(
    pos: dict,
    speed: int = _DEFAULT_SPEED,
    base_id: int = 11,
    ret: int = _DEFAULT_RETURN,
    offset: int = _DEFAULT_APPROACH_OFFSET,
) -> list[int]:
    """Op 41 / 40: [X, Y, Z, A, Speed, BaseID, Return, Offset, ...]"""
    p = pos["position"]
    arr = _arr20()
    arr[0] = int(p["X"] * _MM_TO_UM)
    arr[1] = int(p["Y"] * _MM_TO_UM)
    arr[2] = int(p["Z"] * _MM_TO_UM)
    arr[3] = int(p["A"] * _DEG_TO_MDEG)
    arr[4] = speed
    arr[5] = base_id
    arr[6] = ret
    arr[7] = offset
    return arr


def _get_base_id(pos_data: dict, robot_id: int) -> int:
    for entry in pos_data.get("valid_bases", []):
        if entry["robot_id"] == robot_id:
            bases = entry.get("base_id_list", [])
            return bases[0] if bases else 11
    return 11


def _get_gripper_open(pos_data: dict) -> int:
    tools = pos_data.get("valid_tools", [])
    if tools:
        return tools[0].get("open_position", _DEFAULT_GRIPPER_OPEN)
    return _DEFAULT_GRIPPER_OPEN


# ── Assembly step definition ──────────────────────────────────────────────────
# (step_index, label, op_id, position_name, robot_id, tool_id)

_MAZE_106_STEPS: list[tuple[int, str, int, str, int, int]] = [
    (1,  "Pick START Insert",    21, "maze_106_pick_insert_start",   1, 2),
    (2,  "Place START Insert",   31, "maze_106_place_insert_start",  1, 2),
    (3,  "Pick FINISH Insert",   21, "maze_106_pick_insert_finish",  1, 2),
    (4,  "Place FINISH Insert",  31, "maze_106_place_insert_finish", 1, 2),
    (5,  "Pick LOGO Insert",     21, "maze_106_pick_insert_logo",    1, 2),
    (6,  "Place LOGO Insert",    31, "maze_106_place_insert_logo",   1, 2),
    (7,  "Pick Ball (Glass)",    21, "maze_106_pick_glass",          1, 2),
    (8,  "Place Ball",           41, "maze_106_place_glass",         1, 2),
    (9,  "Pick Cover",           21, "maze_106_storage_glass",       1, 2),
    (10, "Place Cover",          31, "maze_106_place_glass",         1, 2),
    (11, "Place Rivet 1",        41, "maze_106_place_rivet_1",       1, 2),
    (12, "Push Rivet 1",         41, "maze_106_push_in_rivet_1",     1, 2),
    (13, "Place Rivet 2",        41, "maze_106_place_rivet_2",       1, 2),
    (14, "Push Rivet 2",         41, "maze_106_push_in_rivet_2",     1, 2),
    (15, "Place Rivet 3",        41, "maze_106_place_rivet_3",       1, 2),
    (16, "Push Rivet 3",         41, "maze_106_push_in_rivet_3",     1, 2),
    (17, "Place Rivet 4",        41, "maze_106_place_rivet_4",       1, 2),
    (18, "Push Rivet 4",         41, "maze_106_push_in_rivet_4",     1, 2),
]


def build_maze_106_plan(mode: ExecutionMode) -> Plan:
    """Build a complete Maze 106 assembly plan from position JSON files."""
    steps: list[PlanStep] = []
    for step_idx, label, op_id, pos_name, robot_id, tool_id in _MAZE_106_STEPS:
        pos_data = _load_pos(pos_name)
        base_id = _get_base_id(pos_data, robot_id)
        gripper_open = _get_gripper_open(pos_data)

        if op_id in (20, 21):
            params = _encode_pick_vertical(pos_data, base_id=base_id, gripper_open=gripper_open)
        elif op_id in (30, 31):
            params = _encode_place_vertical(pos_data, base_id=base_id, gripper_open=gripper_open)
        else:  # 40, 41
            params = _encode_approach_vertical(pos_data, base_id=base_id)

        steps.append(
            PlanStep(
                step_index=step_idx,
                name=label,
                op_id=op_id,
                op_name=OP_NAMES[op_id],
                robot_id=robot_id,
                tool_id=tool_id,
                base_id=base_id,
                position_name=pos_name,
                position=pos_data["position"],
                parameter_array=params,
            )
        )

    return Plan(
        plan_id=f"MAZE_106_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        product="MAZE_106",
        mode=mode,
        steps=steps,
    )
