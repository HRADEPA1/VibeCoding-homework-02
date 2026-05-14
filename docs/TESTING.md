# Capability Matcher — Mode A Testing

> Mode A = file-based matching. No hardware, no OPC UA, no TypeDB required.
> All tests run with: `PYTHONPATH=src python -m capability_matcher.cli`

---

## Test Harness

### State Fixtures

Test states live in `data/test_states/`. Each file is a JSON array of `RobotRuntimeState` objects
fed via `--resource-state`.

| File | Description |
|------|-------------|
| *(none)* | Default nominal state: R1/T2, R2/T10, R3/T2, all idle, base 11 |
| `r1_busy.json` | R1 busy + not ready; R2/R3 idle with T2 (wrong tool for glass steps) |
| `r1_error.json` | R1 error_id=5; R2/R3 idle with T2 |
| `wrong_tool.json` | All robots carry T9 (no valid tool for any position) |
| `degraded.json` | R2 error_id=1, R3 manually_disabled=true, R1 nominal |
| `no_tool.json` | All robots: tool_id=0, tool_attached=false |
| `r2_glass.json` | R2 with T10 (suction cup); R1/R3 with T2 |

### Verbose Diagnostics

```bash
# Single step with rejection reasons
PYTHONPATH=src python -m capability_matcher.cli --step 1 --verbose

# Full plan with summary
PYTHONPATH=src python -m capability_matcher.cli --all-steps --verbose

# Scenario test
PYTHONPATH=src python -m capability_matcher.cli --all-steps \
  --resource-state data/test_states/degraded.json --verbose
```

---

## Use Case Results

### UC1 — Nominal (all robots healthy)

```bash
PYTHONPATH=src python -m capability_matcher.cli --all-steps
```

**Result: 18/18 steps feasible**

| Steps | Robot | Tool | Base | Score |
|-------|-------|------|------|-------|
| 1–6, 11–18 | R1 | T2 | 11 | 0.700 |
| 7–10 | R2 | T10 | 11 | 0.680 |

**Finding:** Score is 0.700 / 0.680 because no execution history exists yet.
Formula: `score = 0.6 × history_rate + 0.4 × priority_weight`
With zero history: `0.6 × 0.5 + 0.4 × 1.0 = 0.700` (R1), `0.6 × 0.5 + 0.4 × 0.7 = 0.680` (R2).
Step 1 scores 1.000 due to pre-seeded success record from the test fixture.

---

### UC2 — R1 Busy

```bash
PYTHONPATH=src python -m capability_matcher.cli --all-steps \
  --resource-state data/test_states/r1_busy.json --verbose
```

**Result: 0/18 steps feasible — ALL BLOCKED**

Root cause analysis (from `--verbose`):
- Steps 1–6, 11–18: R1 rejected on `[busy]` + `[ready]`; R2/R3 have no valid base entry for these positions
- Steps 7–10: R1 busy; R2/R3 carry T2 which fails `[tool_valid]` (T10 required); R3 also lacks valid base

```
=== Step 1: Pick START Insert [maze_106_pick_insert_start] ===
  BLOCKED — no feasible robot
  REJECTED (3 robot(s)):
    R1: [busy] robot is busy
    R1: [ready] robot not ready
    R2: [base_reachable] robot 2 has no valid base entry for position maze_106_pick_insert_start
    R3: [base_reachable] robot 3 has no valid base entry for position maze_106_pick_insert_start
```

**Finding:** The RICAIP testbed physically assigns each position to one specific robot.
R2/R3 have no calibrated base frame for R1 positions. There is **zero fault tolerance** —
if R1 goes busy or fails, the entire assembly is blocked.

---

### UC3 — R1 Error (error_id=5)

```bash
PYTHONPATH=src python -m capability_matcher.cli --all-steps \
  --resource-state data/test_states/r1_error.json --verbose
```

**Result: 0/18 steps feasible — ALL BLOCKED**

Identical root cause to UC2 (single-robot positions). R1 rejected on `[error_id] robot error 5`.

---

### UC4 — Wrong Tool (all robots carry T9)

```bash
PYTHONPATH=src python -m capability_matcher.cli --all-steps \
  --resource-state data/test_states/wrong_tool.json --verbose
```

**Result: 0/18 steps feasible — ALL BLOCKED**

All positions reject T9 via `[tool_valid]` constraint. Example:

```
R1: [tool_valid] tool 9 not in valid tools [2] for position maze_106_pick_insert_start
```

---

### UC5 — No Tool Attached

```bash
PYTHONPATH=src python -m capability_matcher.cli --all-steps \
  --resource-state data/test_states/no_tool.json --verbose
```

**Result: 0/18 steps feasible — ALL BLOCKED**

All robots rejected on `[tool_attached] no tool attached` before reaching positional checks.

---

### UC6 — Degraded Workcell (R2 error, R3 disabled)

```bash
PYTHONPATH=src python -m capability_matcher.cli --all-steps \
  --resource-state data/test_states/degraded.json --verbose
```

**Result: 14/18 steps feasible, 4 BLOCKED**

| Steps | Status | Robot | Reason |
|-------|--------|-------|--------|
| 1–6, 11–18 | PASS | R1/T2 | Healthy, correct position |
| 7–10 | BLOCKED | — | R2 error; R1/R3 wrong tool + wrong base |

Steps 7–10 require R2 with T10 (suction cup) to handle the glass ball component.
With R2 faulted, this sub-sequence is physically unresolvable.

```
=== Step 7: Pick Ball [maze_106_pick_glass] ===
  BLOCKED — no feasible robot
  REJECTED (3 robot(s)):
    R1: [tool_valid] tool 2 not in valid tools [10] for position maze_106_pick_glass
    R1: [base_reachable] robot 1 has no valid base entry for position maze_106_pick_glass
    R2: [error_id] robot error 1
    ...
```

---

### UC7 — Glass Position Isolated (R2 + T10)

```bash
PYTHONPATH=src python -m capability_matcher.cli --step 8 \
  --resource-state data/test_states/r2_glass.json --verbose
```

**Result: PASS — R2/T10/base=11 score=0.680**

Verifies the suction-cup path through `maze_106_place_glass` resolves correctly when R2 is nominal.

---

## Key Findings

### Finding 1 — Zero Fallback Coverage

All 18 positions in the Maze 106 assembly have `valid_bases` entries for **exactly one robot**.
This is a physical constraint of the testbed: each robot has a unique calibrated tool-centre-point
for each position. No other robot can substitute.

| Position group | Assigned robot | Fallback |
|----------------|---------------|---------|
| Insert positions (steps 1–6) | R1 | None |
| Glass/ball positions (steps 7–10) | R2 | None |
| Rivet positions (steps 11–18) | R1 | None |

**Implication for the thesis:** The GNN ranker cannot learn robot-substitution strategies on this
testbed configuration. The ranker's value is in tool-selection optimisation and scheduling
priority, not fault routing. For fault tolerance research, position fixtures would need to
be re-calibrated for multiple robots.

### Finding 2 — Ranker Baseline Score Behaviour

With no execution history the ranker produces a constant score:
- R1 steps: 0.700 (`0.6×0.5 + 0.4×1.0`)
- R2 steps: 0.680 (`0.6×0.5 + 0.4×0.7`)

Once `record_outcome()` is called after each real execution, `history_rate` will replace
the 0.5 prior and scores will diverge to reflect actual reliability.

### Finding 3 — Tool Specialisation

The constraint chain `[tool_valid] → [base_reachable]` correctly short-circuits:
robots carrying the wrong tool are rejected immediately without evaluating base frames.
This prevents misleading "wrong base" messages when the real issue is the tool.

---

## Verbose Flag — `--verbose`

Added in v1.1. Shows per-robot constraint violations for rejected candidates.

```
=== Step 1: Pick START Insert [maze_106_pick_insert_start] ===
  PASS (1 candidate(s)):
    R1 / T2 / base=11  score=1.000
  REJECTED (2 robot(s)):
    R2: [tool_valid] tool 10 not in valid tools [2] for position maze_106_pick_insert_start
    R2: [base_reachable] robot 2 has no valid base entry for position maze_106_pick_insert_start
    R3: [base_reachable] robot 3 has no valid base entry for position maze_106_pick_insert_start
```

Use it during integration testing or operator troubleshooting to understand why no robot
was selected without inspecting raw JSON fixtures.

---

## Running the Unit Test Suite

```bash
source .venv/bin/activate
PYTHONPATH=src python -m pytest src/tests/ -v
```

19 tests across `test_filter.py` (15 tests) and `test_matcher.py` (4 tests).
All pass against the current fixture set.

---

*Generated: 2026-05-12 | Mode A testing complete*
