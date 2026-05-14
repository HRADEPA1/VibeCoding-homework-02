"""
CLI entry point for Mode A (file-based) capability matching.

Usage:
    python -m capability_matcher.cli --step 1
    python -m capability_matcher.cli --input data/test_steps/step_01.json --output /tmp/result.json
    python -m capability_matcher.cli --all-steps
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .filter import run_filter_verbose
from .loader import load_process_steps
from .matcher import CapabilityMatcher
from .models import MatchRequest, RobotRuntimeState, WorkcellRuntimeState


def _default_workcell(robots_state: list[dict] | None = None) -> WorkcellRuntimeState:
    if robots_state:
        return WorkcellRuntimeState(
            robots=[RobotRuntimeState(**r) for r in robots_state]
        )
    return WorkcellRuntimeState(
        robots=[
            RobotRuntimeState(robot_id=1, tool_id=2, active_base_id=11),
            RobotRuntimeState(robot_id=2, tool_id=10, active_base_id=11),
            RobotRuntimeState(robot_id=3, tool_id=2, active_base_id=11),
        ]
    )


def _run_from_file(input_path: Path, output_path: Path | None) -> None:
    payload = json.loads(input_path.read_text())
    from .models import ProcessStep
    step = ProcessStep(**payload["step"])
    workcell = _default_workcell(payload.get("resource_state"))
    request = MatchRequest(step=step, workcell=workcell)
    result = CapabilityMatcher().match(request)
    out = result.model_dump()
    if output_path:
        output_path.write_text(json.dumps(out, indent=2))
        print(f"result → {output_path}", file=sys.stderr)
    else:
        print(json.dumps(out, indent=2))


def _print_verbose_step(request: MatchRequest, passing: list, rejected: list) -> None:
    step = request.step
    print(f"\n=== Step {step.step_index}: {step.step_name} [{step.position_name}] ===")
    if passing:
        print(f"  PASS ({len(passing)} candidate(s)):")
        for c in passing:
            print(f"    R{c.robot_id} / T{c.tool_id} / base={c.active_base_id}  score={c.score:.3f}")
    else:
        print("  BLOCKED — no feasible robot")
    if rejected:
        print(f"  REJECTED ({len(rejected)} robot(s)):")
        for c in rejected:
            for v in c.violations:
                print(f"    R{c.robot_id}: [{v.constraint}] {v.detail}")


def _run_step(step_index: int, workcell: WorkcellRuntimeState, verbose: bool = False) -> None:
    steps = {s.step_index: s for s in load_process_steps()}
    if step_index not in steps:
        print(f"step {step_index} not found. valid: {sorted(steps)}", file=sys.stderr)
        sys.exit(1)
    request = MatchRequest(step=steps[step_index], workcell=workcell)
    if verbose:
        passing, rejected = run_filter_verbose(request)
        from .ranker import rank_candidates
        ranked = rank_candidates(passing, request)
        _print_verbose_step(request, ranked, rejected)
    else:
        result = CapabilityMatcher().match(request)
        print(json.dumps(result.model_dump(), indent=2))


def _run_all_steps(workcell: WorkcellRuntimeState, verbose: bool = False) -> None:
    matcher = CapabilityMatcher()
    steps = load_process_steps()
    if verbose:
        from .ranker import rank_candidates
        total_blocked = 0
        for step in steps:
            request = MatchRequest(step=step, workcell=workcell)
            passing, rejected = run_filter_verbose(request)
            ranked = rank_candidates(passing, request)
            _print_verbose_step(request, ranked, rejected)
            if not passing:
                total_blocked += 1
        print(f"\nSummary: {len(steps)-total_blocked}/{len(steps)} steps feasible, {total_blocked} BLOCKED")
    else:
        results = []
        for step in steps:
            request = MatchRequest(step=step, workcell=workcell)
            results.append(matcher.match(request).model_dump())
        print(json.dumps(results, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Capability Matcher CLI (Mode A)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--step", type=int, metavar="N", help="match a single step by index")
    group.add_argument("--input", type=Path, metavar="FILE", help="JSON file with step + resource_state")
    group.add_argument("--all-steps", action="store_true", help="match all process steps")
    parser.add_argument("--output", type=Path, default=None, help="write result to FILE (default: stdout)")
    parser.add_argument("--resource-state", type=Path, default=None, help="JSON file with robot runtime states")
    parser.add_argument("--verbose", action="store_true", help="show constraint violations for rejected robots")
    args = parser.parse_args()

    robots_state = None
    if args.resource_state:
        robots_state = json.loads(args.resource_state.read_text())

    workcell = _default_workcell(robots_state)

    if args.input:
        _run_from_file(args.input, args.output)
    elif args.step:
        _run_step(args.step, workcell, verbose=args.verbose)
    else:
        _run_all_steps(workcell, verbose=args.verbose)


if __name__ == "__main__":
    main()
