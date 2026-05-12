# /research — Automated Research Pipeline

Conduct deep research on `$ARGUMENTS` using parallel subagents.

## Execution Plan

### Step 1 — Spawn Parallel Research Subagents

Launch two subagents simultaneously using the Task tool:

**Subagent A — Web Research:**
```
Search for recent (last 10 years) articles, blog posts, and documentation on: $ARGUMENTS
Use the brave-search MCP tool (brave_web_search) with at least 3 different query variants.
For each result collect: title, URL, date, 2-sentence summary, relevance score (1-10).
Return structured JSON array.
```

**Subagent B — Academic Research:**
```
Search arXiv for papers on: $ARGUMENTS
Use research-mcp tool fetch_arxiv with query derived from the topic.
For each paper: extract title, authors, abstract, arXiv ID, published date.
Use summarize_paper tool to get a 3-sentence summary of each.
Return top-5 papers ranked by citation potential and recency.
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

## Notes
- If brave-search MCP is unavailable, fall back to WebSearch tool
- Minimum: 3 web sources + 2 papers before declaring complete
- Always include full citations — no citation → not counted
