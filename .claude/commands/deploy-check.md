# /deploy-check — Pre-Deployment Validation Pipeline

Run all pre-flight checks before deploying. No arguments needed — validates the current repo.

## Execution Plan

### Step 1 — Spawn 3 Parallel Validation Subagents

All three run simultaneously. Deployment is blocked if ANY fails.

---

**Subagent 1 — Test Runner & Coverage:**
```
Run the full test suite for this project. Detect test framework automatically:
- Python: pytest with --json-report --cov=src --cov-fail-under=80
- Node: npm test / pnpm test
- Other: look for Makefile targets (make test)

Report:
- Total tests, passed, failed, skipped
- Coverage percentage
- Any test that takes > 5s (performance issue)
- PASS if: 0 failures AND coverage >= 80%
- FAIL otherwise with details
```

**Subagent 2 — Security & Dependency Scanner:**
```
Run security checks:
1. pip-audit or npm audit — check for vulnerable dependencies
2. git log --oneline -20 — check for secrets accidentally committed (look for: key, token, password, secret in diff)
3. Check .env files are in .gitignore
4. Verify no debug flags are enabled in production config
5. Check Dockerfile (if exists): no root user, no exposed secrets

Report:
- CVE list with severity (critical/high/medium/low)
- Any secrets found
- PASS if: no critical/high CVEs AND no secrets found
- FAIL otherwise
```

**Subagent 3 — Environment & Config Validator:**
```
Validate deployment configuration:
1. Check all required env vars are documented (in .env.example or README)
2. Verify docker-compose.yml or k8s manifests are valid YAML
3. Check resource limits are set (memory, CPU)
4. Verify health check endpoints exist and respond
5. Confirm migrations are up to date (if ORM detected)
6. Validate API version compatibility (semver check)

Report:
- Missing env vars
- Config file issues
- PASS / FAIL with details
```

---

### Step 2 — Aggregate Results

Wait for all 3 subagents to complete. Collect their PASS/FAIL status.

### Step 3 — Generate Deploy Report

```markdown
# 🚀 Deploy-Check Report
**Date:** <timestamp>
**Branch:** <git branch>
**Commit:** <git rev-parse --short HEAD>

## Status Overview
| Check              | Status | Details |
|--------------------|--------|---------|
| Tests & Coverage   | ✅ PASS / ❌ FAIL | N tests, N% coverage |
| Security & Deps    | ✅ PASS / ❌ FAIL | N CVEs found |
| Environment/Config | ✅ PASS / ❌ FAIL | N issues |

## Final Verdict
### ✅ READY TO DEPLOY
or
### ❌ BLOCKED — Fix the following before deploying:
1. <issue>
2. <issue>
```

### Step 4 — Enforce Gate

If ANY subagent returned FAIL:
- Exit with code 1 (blocks CI/CD)
- Print BLOCKED message

If all PASS:
- Exit with code 0
- Print READY TO DEPLOY
- Log result to `.claude/deploy-history.log`
