# /research — Automated Research Pipeline

Conduct deep research on `$ARGUMENTS` using parallel subagents.

## Execution Plan

### Step 1 — Spawn Parallel Research Subagents

Launch two subagents simultaneously using the Task tool:

**Subagent A — Web Research:**
```
TOKEN BUDGET: Return only JSON. No prose. No markdown outside the JSON block.

Search for recent (last 10 years) articles on: $ARGUMENTS
Use brave_web_search MCP with 3 query variants. Collect top-5 results only.
Return a JSON array — each object: {title, url, date, summary (≤120 chars), relevance (1-10)}.
Omit any field you cannot populate. No explanation text outside the array.
```

**Subagent B — Academic Research:**
```
TOKEN BUDGET: Return only JSON. No prose. No markdown outside the JSON block.

Search arXiv for: $ARGUMENTS
Use fetch_arxiv(query, compact=true) — this omits abstracts to save tokens.
For the top-5 results by relevance, call summarize_paper() to get oneLineSummary.
Return a JSON array: [{id, title, authors, published, url, oneLineSummary, relevance_estimate}]
No other text.
```

### Step 2 — Merge & Deduplicate

After both subagents return, merge results. Remove duplicates by URL/DOI.
Rank unified list: academic papers (weight 1.5x) over web articles.

### Step 3 — Persist to SQLite

Use the `sqlite` MCP to store findings:
```sql
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT,
  source_type TEXT,  -- 'web' | 'arxiv'
  title TEXT,
  url TEXT,
  summary TEXT,
  relevance REAL,
  fetched_at TEXT DEFAULT (datetime('now'))
);
```
Insert all results.

### Step 4 — Output

Return a formatted research report:
```markdown
# Research Report: $ARGUMENTS
Date: <today>

## Key Findings (Top 5)
1. **<title>** — <summary> [<url>]
...

## Academic Papers
...

## Raw Data
Stored in ./data/research.db → table: findings
```

### Step 5 — Log Session

Append a structured entry to `.claude/logs/agent.log`:
```
<timestamp>  RESEARCH_COMPLETE  topic="$ARGUMENTS"  results=<count>  duration=<seconds>s
```

Also append a human-readable summary to `.claude/session-notes.md`:
```
## Research: $ARGUMENTS — <date>
- Sources: N web, N arXiv
- Top finding: <title>
- DB: ./data/research.db → findings table
```

## Notes
- If brave-search MCP is unavailable, fall back to WebSearch tool
- Minimum: 3 web sources + 2 papers before declaring complete
- Always include full citations — no citation → not counted
- Log every session regardless of success/failure
