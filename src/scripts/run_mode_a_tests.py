"""
Automatic Mode A integration test runner.

Implements all 7 use cases from docs/TESTING.md and asserts the exact expected outcomes.
Uses the Python API directly (not subprocess) so assertions are structured, not text-based.
Runs against a temporary SQLite database so results are deterministic regardless of prior
execution history.

Usage:
    PYTHONPATH=src python -m scripts.run_mode_a_tests
    PYTHONPATH=src python -m scripts.run_mode_a_tests --verbose
    PYTHONPATH=src python -m scripts.run_mode_a_tests --junit results/junit.xml

Exit: 0 = all pass, 1 = any failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Ensure src/ is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capability_matcher.filter import run_filter_verbose
from capability_matcher.loader import load_process_steps
from capability_matcher.matcher import CapabilityMatcher
from capability_matcher.models import (
    MatchRequest,
    MatchResult,
    RobotRuntimeState,
    WorkcellRuntimeState,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "data" / "test_states"

_NOMINAL_STATE = [
    {"robot_id": 1, "tool_id": 2,  "active_base_id": 11},
    {"robot_id": 2, "tool_id": 10, "active_base_id": 11},
    {"robot_id": 3, "tool_id": 2,  "active_base_id": 11},
]


def _load_state(filename: str | None) -> list[dict]:
    if filename is None:
        return _NOMINAL_STATE
    return json.loads((_FIXTURES_DIR / filename).read_text())


def _workcell(robots: list[dict]) -> WorkcellRuntimeState:
    return WorkcellRuntimeState(robots=[RobotRuntimeState(**r) for r in robots])


# ── assertion helpers ─────────────────────────────────────────────────────────

def _feasible_count(results: list[MatchResult]) -> int:
    return sum(1 for r in results if r.best is not None)


def _blocked_steps(results: list[MatchResult]) -> list[int]:
    return [r.step_index for r in results if r.best is None]


def _violations_for(step_index: int, fixture: str | None) -> list[str]:
    """Return all constraint names that fired for any robot on the given step."""
    steps = {s.step_index: s for s in load_process_steps()}
    step = steps[step_index]
    workcell = _workcell(_load_state(fixture))
    _, rejected = run_filter_verbose(MatchRequest(step=step, workcell=workcell))
    return [v.constraint for c in rejected for v in c.violations]


# ── test case definition ──────────────────────────────────────────────────────

@dataclass
class Check:
    description: str
    fn: Callable[[list[MatchResult]], bool]
    detail: Callable[[list[MatchResult]], str] = field(
        default_factory=lambda: (lambda _: "")
    )


@dataclass
class TestCase:
    id: str
    name: str
    fixture: str | None        # filename in data/test_states/ or None for nominal
    step: int | None           # None = all steps
    checks: list[Check]


def _all_steps_results(fixture: str | None) -> list[MatchResult]:
    matcher = CapabilityMatcher()
    workcell = _workcell(_load_state(fixture))
    return [
        matcher.match(MatchRequest(step=s, workcell=workcell))
        for s in load_process_steps()
    ]


def _single_step_result(step_index: int, fixture: str | None) -> list[MatchResult]:
    steps = {s.step_index: s for s in load_process_steps()}
    workcell = _workcell(_load_state(fixture))
    return [CapabilityMatcher().match(MatchRequest(step=steps[step_index], workcell=workcell))]


TEST_CASES: list[TestCase] = [
    # ── UC1: Nominal ──────────────────────────────────────────────────────────
    TestCase(
        id="UC1",
        name="Nominal — all robots healthy",
        fixture=None,
        step=None,
        checks=[
            Check(
                "18/18 steps feasible",
                lambda rs: _feasible_count(rs) == 18,
                lambda rs: f"feasible={_feasible_count(rs)}, expected=18",
            ),
            Check(
                "Steps 1-6 and 11-18 assigned to R1 with T2",
                lambda rs: all(
                    r.best is not None and r.best.robot_id == 1 and r.best.tool_id == 2
                    for r in rs
                    if r.step_index in set(range(1, 7)) | set(range(11, 19))
                ),
                lambda rs: str([
                    (r.step_index, r.best.robot_id, r.best.tool_id)
                    for r in rs
                    if r.step_index in set(range(1, 7)) | set(range(11, 19))
                    and r.best is not None
                    and not (r.best.robot_id == 1 and r.best.tool_id == 2)
                ]),
            ),
            Check(
                "Steps 7-10 assigned to R2 with T10",
                lambda rs: all(
                    r.best is not None and r.best.robot_id == 2 and r.best.tool_id == 10
                    for r in rs
                    if r.step_index in {7, 8, 9, 10}
                ),
                lambda rs: str([
                    (r.step_index, r.best.robot_id if r.best else None)
                    for r in rs if r.step_index in {7, 8, 9, 10}
                ]),
            ),
            Check(
                "All scores in range [0.0, 1.0]",
                lambda rs: all(0.0 <= r.best.score <= 1.0 for r in rs if r.best),
                lambda rs: str([(r.step_index, r.best.score) for r in rs if r.best and not 0 <= r.best.score <= 1]),
            ),
            Check(
                "R1 nominal score ≈ 0.70 (no history)",
                lambda rs: all(
                    abs(r.best.score - 0.70) < 0.05
                    for r in rs
                    if r.best and r.best.robot_id == 1 and r.step_index != 1
                ),
                lambda rs: str([(r.step_index, r.best.score) for r in rs if r.best and r.best.robot_id == 1]),
            ),
            Check(
                "R2 nominal score ≈ 0.68 (no history)",
                lambda rs: all(
                    abs(r.best.score - 0.68) < 0.05
                    for r in rs
                    if r.best and r.best.robot_id == 2
                ),
                lambda rs: str([(r.step_index, r.best.score) for r in rs if r.best and r.best.robot_id == 2]),
            ),
        ],
    ),

    # ── UC2: R1 busy ─────────────────────────────────────────────────────────
    TestCase(
        id="UC2",
        name="R1 busy — entire plan blocked",
        fixture="r1_busy.json",
        step=None,
        checks=[
            Check(
                "0/18 steps feasible",
                lambda rs: _feasible_count(rs) == 0,
                lambda rs: f"feasible={_feasible_count(rs)}, expected=0",
            ),
            Check(
                "R1 rejected on [busy] constraint for R1 positions",
                lambda _: "busy" in _violations_for(1, "r1_busy.json"),
                lambda _: "expected 'busy' in violations for step 1",
            ),
            Check(
                "R2/R3 rejected on [base_reachable] for R1 positions",
                lambda _: "base_reachable" in _violations_for(1, "r1_busy.json"),
                lambda _: "expected 'base_reachable' violation for step 1",
            ),
        ],
    ),

    # ── UC3: R1 error ─────────────────────────────────────────────────────────
    TestCase(
        id="UC3",
        name="R1 error_id=5 — entire plan blocked",
        fixture="r1_error.json",
        step=None,
        checks=[
            Check(
                "0/18 steps feasible",
                lambda rs: _feasible_count(rs) == 0,
                lambda rs: f"feasible={_feasible_count(rs)}, expected=0",
            ),
            Check(
                "R1 rejected on [error_id] constraint",
                lambda _: "error_id" in _violations_for(1, "r1_error.json"),
                lambda _: "expected 'error_id' violation for step 1",
            ),
        ],
    ),

    # ── UC4: wrong tool ───────────────────────────────────────────────────────
    TestCase(
        id="UC4",
        name="Wrong tool (T9) — all positions reject via tool_valid",
        fixture="wrong_tool.json",
        step=None,
        checks=[
            Check(
                "0/18 steps feasible",
                lambda rs: _feasible_count(rs) == 0,
                lambda rs: f"feasible={_feasible_count(rs)}, expected=0",
            ),
            Check(
                "All robots rejected on [tool_valid] for step 1",
                lambda _: "tool_valid" in _violations_for(1, "wrong_tool.json"),
                lambda _: "expected 'tool_valid' violation for step 1",
            ),
        ],
    ),

    # ── UC5: no tool ──────────────────────────────────────────────────────────
    TestCase(
        id="UC5",
        name="No tool attached — short-circuits before positional checks",
        fixture="no_tool.json",
        step=None,
        checks=[
            Check(
                "0/18 steps feasible",
                lambda rs: _feasible_count(rs) == 0,
                lambda rs: f"feasible={_feasible_count(rs)}, expected=0",
            ),
            Check(
                "Robots rejected on [tool_attached] before [tool_valid]",
                lambda _: (
                    "tool_attached" in _violations_for(1, "no_tool.json")
                    and "tool_valid" not in _violations_for(1, "no_tool.json")
                ),
                lambda _: f"violations={_violations_for(1, 'no_tool.json')}",
            ),
        ],
    ),

    # ── UC6: degraded workcell ────────────────────────────────────────────────
    TestCase(
        id="UC6",
        name="Degraded (R2 error, R3 disabled) — glass sub-sequence blocked",
        fixture="degraded.json",
        step=None,
        checks=[
            Check(
                "14/18 steps feasible",
                lambda rs: _feasible_count(rs) == 14,
                lambda rs: f"feasible={_feasible_count(rs)}, expected=14",
            ),
            Check(
                "Steps 7-10 are blocked",
                lambda rs: set(_blocked_steps(rs)) == {7, 8, 9, 10},
                lambda rs: f"blocked={_blocked_steps(rs)}, expected=[7,8,9,10]",
            ),
            Check(
                "Steps 1-6 and 11-18 assigned to R1",
                lambda rs: all(
                    r.best is not None and r.best.robot_id == 1
                    for r in rs
                    if r.step_index in set(range(1, 7)) | set(range(11, 19))
                ),
                lambda rs: str([
                    (r.step_index, r.best.robot_id if r.best else None)
                    for r in rs
                    if r.step_index not in {7, 8, 9, 10}
                    and (r.best is None or r.best.robot_id != 1)
                ]),
            ),
            Check(
                "R2 rejected on [error_id] for step 7",
                lambda _: "error_id" in _violations_for(7, "degraded.json"),
                lambda _: f"violations step 7={_violations_for(7, 'degraded.json')}",
            ),
        ],
    ),

    # ── UC7: glass position isolated ─────────────────────────────────────────
    TestCase(
        id="UC7",
        name="Glass position isolated — R2+T10 resolves correctly",
        fixture="r2_glass.json",
        step=8,
        checks=[
            Check(
                "Step 8 is feasible",
                lambda rs: rs[0].best is not None,
                lambda rs: "step 8 returned no candidate",
            ),
            Check(
                "Best candidate is R2",
                lambda rs: rs[0].best is not None and rs[0].best.robot_id == 2,
                lambda rs: f"robot_id={rs[0].best.robot_id if rs[0].best else None}, expected=2",
            ),
            Check(
                "Best candidate uses T10",
                lambda rs: rs[0].best is not None and rs[0].best.tool_id == 10,
                lambda rs: f"tool_id={rs[0].best.tool_id if rs[0].best else None}, expected=10",
            ),
            Check(
                "Active base is 11",
                lambda rs: rs[0].best is not None and rs[0].best.active_base_id == 11,
                lambda rs: f"base={rs[0].best.active_base_id if rs[0].best else None}, expected=11",
            ),
            Check(
                "Score ≈ 0.68 (no history)",
                lambda rs: rs[0].best is not None and abs(rs[0].best.score - 0.68) < 0.05,
                lambda rs: f"score={rs[0].best.score if rs[0].best else None}, expected≈0.68",
            ),
        ],
    ),
]


# ── runner ────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    description: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    tc: TestCase
    checks: list[CheckResult]
    duration_s: float
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)


def _run_case(tc: TestCase) -> CaseResult:
    t0 = time.perf_counter()
    try:
        if tc.step is not None:
            results = _single_step_result(tc.step, tc.fixture)
        else:
            results = _all_steps_results(tc.fixture)
    except Exception as exc:
        return CaseResult(tc=tc, checks=[], duration_s=time.perf_counter() - t0, error=str(exc))

    check_results = []
    for chk in tc.checks:
        try:
            passed = chk.fn(results)
            detail = "" if passed else chk.detail(results)
        except Exception as exc:
            passed = False
            detail = f"check raised: {exc}"
        check_results.append(CheckResult(description=chk.description, passed=passed, detail=detail))

    return CaseResult(tc=tc, checks=check_results, duration_s=time.perf_counter() - t0)


def run_all(verbose: bool) -> list[CaseResult]:
    case_results = []
    for tc in TEST_CASES:
        result = _run_case(tc)
        case_results.append(result)
        _print_case(result, verbose)
    return case_results


# ── output ────────────────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _colour(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


def _print_case(r: CaseResult, verbose: bool) -> None:
    icon = _colour("PASS", _GREEN) if r.passed else _colour("FAIL", _RED)
    dur = f"{r.duration_s * 1000:.0f}ms"
    print(f"  [{icon}] {r.tc.id}: {r.tc.name}  ({dur})")

    if r.error:
        print(f"         ERROR: {r.error}")
        return

    if verbose or not r.passed:
        for chk in r.checks:
            sym = _colour("✓", _GREEN) if chk.passed else _colour("✗", _RED)
            print(f"         {sym}  {chk.description}")
            if not chk.passed and chk.detail:
                for line in textwrap.wrap(chk.detail, width=100, initial_indent="              "):
                    print(line)


def _print_summary(results: list[CaseResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    bar = "=" * 60
    print(f"\n{bar}")
    if passed == total:
        print(_colour(f"  ALL {total} TEST CASES PASSED", _GREEN + _BOLD))
    else:
        failed = [r.tc.id for r in results if not r.passed]
        print(_colour(f"  {total - passed}/{total} FAILED: {', '.join(failed)}", _RED + _BOLD))
    total_ms = sum(r.duration_s * 1000 for r in results)
    print(f"  Total time: {total_ms:.0f}ms")
    print(bar)


# ── JUnit XML export ──────────────────────────────────────────────────────────

def _write_junit(results: list[CaseResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite", name="mode-a-integration", tests=str(len(results)))
    for r in results:
        tc_el = ET.SubElement(suite, "testcase", name=f"{r.tc.id}: {r.tc.name}",
                              time=f"{r.duration_s:.3f}")
        if r.error:
            ET.SubElement(tc_el, "error", message=r.error)
        else:
            failed = [c for c in r.checks if not c.passed]
            if failed:
                msg = "; ".join(f"{c.description}: {c.detail}" for c in failed)
                ET.SubElement(tc_el, "failure", message=msg)

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    print(f"\n  JUnit XML: {path}")


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Mode A integration test runner (docs/TESTING.md)")
    parser.add_argument("--verbose", "-v", action="store_true", help="show all check details")
    parser.add_argument("--junit", metavar="FILE", type=Path, help="write JUnit XML to FILE")
    args = parser.parse_args()

    # Use an isolated temp DB so history from previous runs doesn't affect scores
    tmpdir = tempfile.mkdtemp(prefix="capability_matcher_test_")
    os.environ["DATA_DIR"] = tmpdir

    print(f"\n{_colour('Mode A Integration Tests', _BOLD)}")
    print(f"Fixtures: {_FIXTURES_DIR}")
    print(f"Temp DB:  {tmpdir}\n")

    results = run_all(verbose=args.verbose)
    _print_summary(results)

    if args.junit:
        _write_junit(results, args.junit)

    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
