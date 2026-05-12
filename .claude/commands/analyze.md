# /analyze — Deep Codebase & Data Analysis

Run comprehensive analysis on `$ARGUMENTS` (path, dataset, or module name).

## Execution Plan

Auto-detect input type:
- If path ends in `.csv`, `.json`, `.parquet` → **Data Analysis mode**
- If path is a directory or `.py`/`.js`/`.ts` → **Code Analysis mode**
- If `$ARGUMENTS` contains a question (e.g., "why is X slow") → **Root-cause mode**

---

## Mode A: Code Analysis

### Spawn Subagent — Code Intelligence

```
Analyze the codebase at $ARGUMENTS:

1. STRUCTURE ANALYSIS
   - Count files, lines, functions, classes per module
   - Identify the top-level architecture pattern (MVC, layered, hexagonal, etc.)
   - Map import/dependency graph (which modules depend on what)

2. COMPLEXITY METRICS
   - Find top-10 most complex functions (cyclomatic complexity)
   - Find top-10 largest files
   - Identify dead code (unreachable functions/imports)

3. HOTSPOTS
   - Files changed most often in git log (last 90 days): git log --since=90.days --name-only --pretty=format: | sort | uniq -c | sort -rn | head -20
   - Files with most TODO/FIXME comments

4. DEPENDENCY HEALTH
   - List all external dependencies with versions
   - Flag any with known vulnerabilities (check npm audit / pip-audit)

Output: structured JSON + markdown summary
```

---

## Mode B: Data Analysis

### Step 1 — Profile the Dataset

```bash
# Load and profile
python3 -c "
import json, sys
try:
    import pandas as pd
    df = pd.read_csv('$ARGUMENTS') if '$ARGUMENTS'.endswith('.csv') else pd.read_json('$ARGUMENTS')
    print(df.describe().to_json())
    print('SHAPE:', df.shape)
    print('NULLS:', df.isnull().sum().to_json())
    print('DTYPES:', df.dtypes.to_json())
except Exception as e:
    print('ERROR:', e)
"
```

### Step 2 — Spawn Analysis Subagent

```
Given this dataset profile: <profile from step 1>
File: $ARGUMENTS

Perform:
1. Distribution analysis — identify skewed columns, outliers (IQR method)
2. Correlation analysis — top correlated feature pairs
3. Data quality issues — duplicates, missing values, type mismatches
4. Anomaly detection — flag rows that are statistical outliers
5. Recommend 3 most useful visualizations for this data

Output: JSON report + Python code snippets for recommended analyses
```

---

## Mode C: Root-Cause Analysis

When the argument is a natural-language question about a problem:

```
Task for subagent:
Context: $ARGUMENTS
1. Reproduce the issue: identify code path that triggers it
2. Trace execution: instrument relevant functions with logging
3. Form hypothesis: what is the most likely cause?
4. Validate hypothesis: find evidence in code/logs/tests
5. Propose fix with minimal blast radius

Output: Root-cause report with evidence and fix PR description
```

---

## Final Output

Always conclude with:
```
Analysis complete. Key findings:
1. <most important finding>
2. <second most important>
3. <third>

Next recommended action: <specific action>
```
