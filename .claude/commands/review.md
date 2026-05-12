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
Review the code at $ARGUMENTS for:
1. Logical correctness — are algorithms correct? Any off-by-one errors?
2. Edge cases — null/None handling, empty collections, integer overflow
3. Error handling — are exceptions caught and handled properly?
4. Async/concurrency issues — race conditions, deadlocks
5. Resource leaks — files, connections, memory not released

For each issue found: severity (critical|warning|info), file, line, description, suggested fix.
Output as JSON array: [{severity, file, line, description, suggestion}]
```

**Subagent B — Architecture & Style Review:**
```
Review the code at $ARGUMENTS for:
1. SOLID principles violations
2. Code duplication (DRY)
3. Naming clarity — variables, functions, classes
4. Function/method length and complexity (cyclomatic complexity)
5. Test coverage — are edge cases tested?
6. Documentation completeness — public APIs documented?

Output as JSON array: [{severity, file, line, description, suggestion}]
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

## Fail Criteria
- Any security vulnerability → FAIL
- Any unhandled exception path in public API → FAIL
- Cyclomatic complexity > 15 in any function → WARNING
