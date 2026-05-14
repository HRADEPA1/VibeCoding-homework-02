"""
Synthetic training data generator for the GNN ranker.

Produces labelled (graph_snapshot, step, robot, label) tuples by running the
constraint filter over a parameter grid of resource states across all scenarios.

Output: JSONL files in DATA_DIR/training/{scenario}/
Labels:
  1.0  — robot passes all constraints, highest-priority in feasible set
  0.x  — robot passes all constraints but is secondary (score = rank / n_feasible)
  0.0  — robot fails at least one hard constraint

Usage:
    python -m scripts.generate_training_data
    python -m scripts.generate_training_data --scenario MAZE_106_Assembly --samples 1000
"""
from __future__ import annotations
import argparse
import itertools
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capability_matcher.filter import run_filter
from capability_matcher.loader import load_process_steps
from capability_matcher.models import (
    MatchRequest,
    ProcessStep,
    RobotRuntimeState,
    WorkcellRuntimeState,
)

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
TRAINING_DIR = DATA_DIR / "training"

ROBOT_IDS = [1, 2, 3]
ALL_TOOL_IDS = [2, 3, 7, 8, 9, 10, 12, 16]
ERROR_IDS = [0, 0, 0, 0, 1, 5, 99]     # weighted towards healthy (0)
BASE_IDS = [11, 12, 13, 21, 22, 23, 99]  # 99 = invalid base

SCENARIOS = {
    "MAZE_106_Assembly": {
        "product": "MAZE_106",
        "description": "Normal assembly, all resources available",
    },
    "MAZE_106_R2_Error": {
        "product": "MAZE_106",
        "description": "R2 in ERROR state throughout",
        "force_error": {2: 5},
    },
    "MAZE_106_Wrong_Tool": {
        "product": "MAZE_106",
        "description": "R1 has wrong tool mounted (tool_id=9 instead of 2)",
        "force_tool": {1: 9},
    },
    "MAZE_106_R1_Busy": {
        "product": "MAZE_106",
        "description": "R1 busy — should route to R2/R3",
        "force_busy": {1: True},
    },
    "MAZE_106_Degraded": {
        "product": "MAZE_106",
        "description": "R2 error + R3 manually disabled, only R1 available",
        "force_error": {2: 1},
        "force_disabled": {3: True},
    },
}


def _sample_robot_state(
    robot_id: int,
    scenario: dict,
    rng: random.Random,
) -> RobotRuntimeState:
    tool_id = rng.choice(ALL_TOOL_IDS)
    error_id = rng.choice(ERROR_IDS)
    base_id = rng.choice(BASE_IDS)
    busy = rng.random() < 0.1
    manually_disabled = False

    if "force_error" in scenario and robot_id in scenario["force_error"]:
        error_id = scenario["force_error"][robot_id]
    if "force_tool" in scenario and robot_id in scenario["force_tool"]:
        tool_id = scenario["force_tool"][robot_id]
    if "force_busy" in scenario and robot_id in scenario["force_busy"]:
        busy = scenario["force_busy"][robot_id]
    if "force_disabled" in scenario and robot_id in scenario["force_disabled"]:
        manually_disabled = scenario["force_disabled"][robot_id]

    return RobotRuntimeState(
        robot_id=robot_id,
        enabled=True,
        busy=busy,
        ready=not busy,
        error_id=error_id,
        manually_disabled=manually_disabled,
        tool_id=tool_id,
        tool_attached=True,
        active_base_id=base_id,
    )


def _build_node_features(state: RobotRuntimeState) -> list[float]:
    """Simple feature vector for each robot node."""
    return [
        float(state.robot_id - 1) / 2,        # normalised robot id
        float(state.enabled),
        float(not state.busy),
        float(not state.manually_disabled),
        float(state.error_id == 0),
        float(state.tool_id) / 16,             # normalised tool id
        float(state.active_base_id) / 30,      # normalised base id
    ]


def _build_step_features(step: ProcessStep) -> list[float]:
    op_ids = [10, 11, 12, 13, 20, 21, 30, 31, 40, 41, 50, 51, 200, 201]
    one_hot = [float(step.op_index == op) for op in op_ids]
    return one_hot


def _label_candidates(
    feasible_ids: list[int],
    all_robot_ids: list[int],
) -> dict[int, float]:
    """
    feasible_ids are sorted by priority (ascending robot_id = higher priority).
    Label: 1.0 for first, descending for others, 0.0 for non-feasible.
    """
    labels: dict[int, float] = {rid: 0.0 for rid in all_robot_ids}
    if not feasible_ids:
        return labels
    n = len(feasible_ids)
    for rank, rid in enumerate(feasible_ids):
        labels[rid] = round(1.0 - rank * (0.15 / max(n - 1, 1)), 4)
    return labels


def generate(scenario_name: str, n_samples: int, seed: int = 42) -> Path:
    scenario = SCENARIOS[scenario_name]
    steps = load_process_steps()
    rng = random.Random(seed)

    out_dir = TRAINING_DIR / scenario_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "samples.jsonl"

    samples_written = 0
    with open(out_path, "w") as f:
        for _ in range(n_samples):
            step = rng.choice(steps)
            robot_states = [
                _sample_robot_state(rid, scenario, rng) for rid in ROBOT_IDS
            ]
            workcell = WorkcellRuntimeState(robots=robot_states)
            request = MatchRequest(step=step, workcell=workcell)

            feasible = run_filter(request)
            feasible_ids = [c.robot_id for c in feasible]
            labels = _label_candidates(feasible_ids, ROBOT_IDS)

            sample = {
                "scenario": scenario_name,
                "step_index": step.step_index,
                "position_name": step.position_name,
                "op_index": step.op_index,
                "graph": {
                    "step_features": _build_step_features(step),
                    "nodes": [
                        {
                            "robot_id": s.robot_id,
                            "features": _build_node_features(s),
                        }
                        for s in robot_states
                    ],
                    "edges": [
                        {"src": "step", "dst": f"robot_{rid}", "type": "requires"}
                        for rid in ROBOT_IDS
                    ],
                },
                "labels": labels,
                "feasible_count": len(feasible_ids),
            }
            f.write(json.dumps(sample) + "\n")
            samples_written += 1

    print(f"{scenario_name}: {samples_written} samples → {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default=None, help="single scenario name (default: all)")
    parser.add_argument("--samples", type=int, default=1000, help="samples per scenario")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    targets = [args.scenario] if args.scenario else list(SCENARIOS)
    for name in targets:
        if name not in SCENARIOS:
            print(f"unknown scenario: {name}. valid: {list(SCENARIOS)}", file=sys.stderr)
            sys.exit(1)
        generate(name, args.samples, args.seed)

    total = args.samples * len(targets)
    print(f"\ntotal samples: {total} across {len(targets)} scenario(s)")


if __name__ == "__main__":
    main()
