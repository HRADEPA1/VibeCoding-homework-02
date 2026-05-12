# /review — Deep Code Review with Parallel Subagents

Perform a comprehensive code review of `$ARGUMENTS` (file or directory path).

## Execution Plan

### Step 1 — Static Analysis (fast fail)

Run these checks synchronously first. If critical errors → stop and report:

```bash
# Linting
rg --type py "$ARGUMENTS" -l | xargs pylint --output-format=json 2>/dev/null || \
npx eslint "$ARGUMENTS" --format=json 2>/dev/null

# Type checking
mypy "$ARGUMENTS" --json-report /tmp/mypy-report 2>/dev/null || \
npx tsc --noEmit 2>/dev/null

# Security scan
pip show bandit &>/dev/null && bandit -r "$ARGUMENTS" -f json 2>/dev/null || \
npx semgrep --config=auto "$ARGUMENTS" --json 2>/dev/null
```

### Step 2 — Spawn Parallel Review Subagents

**Subagent A — Logic & Correctness Review:**
```
TOKEN BUDGET: Output ONLY a JSON array. No prose before or after.

Review $ARGUMENTS for: correctness, edge cases (null/empty/overflow),
error handling, async races, resource leaks.

Each issue: {"s":"critical|warning|info","f":"file","l":line,"d":"≤80-char description","fix":"≤80-char fix"}
Omit info-level if total issues > 20. Cap array at 30 entries.
```

**Subagent B — Architecture & Style Review:**
```
TOKEN BUDGET: Output ONLY a JSON array. No prose before or after.

Review $ARGUMENTS for: SOLID violations, DRY, naming, cyclomatic complexity,
test coverage, public API documentation.

Each issue: {"s":"critical|warning|info","f":"file","l":line,"d":"≤80-char description","fix":"≤80-char fix"}
Omit info-level if total issues > 20. Cap array at 30 entries.
```

### Step 3 — Merge Reports

Combine outputs from both subagents + static analysis.
Group by severity. Sort critical → warning → info.

### Step 4 — Output Structured Report

```markdown
# Code Review Report
**Target:** $ARGUMENTS
**Date:** <date>
**Reviewer:** Claude Code Review Agent

## Summary
- 🔴 Critical: N issues
- 🟡 Warnings: N issues
- 🔵 Info: N issues

## Critical Issues (must fix before merge)
...

## Warnings
...

## Suggestions
...

## Verdict
PASS | FAIL | CONDITIONAL PASS
```

### Step 5 — Log Review Session

```bash
printf '%s\tREVIEW_%s\ttarget=%s\tcritical=%d\twarnings=%d\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$VERDICT" "$ARGUMENTS" "$CRITICAL_COUNT" "$WARNING_COUNT" \
  >> .claude/logs/agent.log
```

Write full report to `docs/reviews/<basename>-<date>.md`.

## Fail Criteria
- Any security vulnerability → FAIL
- Any unhandled exception path in public API → FAIL
- Cyclomatic complexity > 15 in any function → WARNING
