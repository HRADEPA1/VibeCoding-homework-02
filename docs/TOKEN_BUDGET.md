# Token Budget & Context Compression

## Why This Matters

Every token sent to and from the Claude API counts against:
- **Context window** — hitting the limit causes auto-compaction or errors
- **Plan quota** — tokens cost money / plan credits
- **Latency** — larger payloads = slower round-trips

This document describes every compression measure implemented in this project.

---

## Compression Layers

```mermaid
flowchart TD
    subgraph L1["Layer 1 — MCP Response Compression"]
        A1["fetch_arxiv: abstract capped at 800 chars\n(was: full abstract ~3000 chars)"]
        A2["fetch_arxiv compact=true: abstracts omitted entirely\n(metadata only)"]
        A3["authors: truncated to first 3 + 'et al.'\n(was: full list of up to 20)"]
        A4["list_findings compact=true (default): 7 fields only\n(was: all 10 fields incl. full summary)"]
        A5["JSON.stringify without pretty-print\n(saves ~30% whitespace tokens)"]
    end

    subgraph L2["Layer 2 — Subagent Output Contracts"]
        B1["TOKEN BUDGET header on all spawns\nForces JSON-only responses"]
        B2["Result caps: 5 web, 5 papers, 30 issues"]
        B3["Short field values: descriptions ≤80 chars"]
        B4["review issues use abbreviated keys: s/f/l/d/fix"]
    end

    subgraph L3["Layer 3 — Harness & Session"]
        C1["autoCompact: true in settings.json\nAuto-compacts conversation near limit"]
        C2["MAX_RESULTS: 10 (was 20)\nHalved default fetch size"]
        C3["list_findings default limit: 10 (was 20)"]
    end

    L1 --> L2 --> L3

    style L1 fill:#1a472a,color:#fff
    style L2 fill:#1e3a5f,color:#fff
    style L3 fill:#4a235a,color:#fff
```

---

## Token Savings Estimate

| Operation | Before | After | Saving |
|-----------|--------|-------|--------|
| `fetch_arxiv` (10 papers, full) | ~35,000 chars | ~10,000 chars | **~71%** |
| `fetch_arxiv` (compact=true) | ~35,000 chars | ~2,500 chars | **~93%** |
| `list_findings` (20 rows, full) | ~15,000 chars | ~3,000 chars | **~80%** |
| Subagent review response | ~8,000 chars prose | ~3,000 chars JSON | **~62%** |
| Subagent research response | ~12,000 chars prose | ~2,000 chars JSON | **~83%** |

Approximate total per `/research` call: **~60,000 → ~15,000 chars** (~75% reduction)

---

## Configuration Knobs

All tunable via `settings.json` → `mcpServers.research-mcp.env`:

| Env Var | Default | Effect |
|---------|---------|--------|
| `ABSTRACT_MAX_CHARS` | `800` | Hard cap on abstract length in `fetch_arxiv` response |
| `MAX_RESULTS` | `10` | Default number of arXiv papers fetched per query |
| `LOG_LEVEL` | `info` | Set to `warn` to reduce log I/O (minor) |

---

## Tool Usage Patterns

### Recommended: Two-Phase arXiv Search

Fetch compact first → summarize only papers of interest:

```
# Phase 1 — cheap: metadata only
fetch_arxiv(query="transformers", max_results=10, compact=true)
# → 2,500 chars, pick top 3 by title relevance

# Phase 2 — targeted: get summaries for selected papers only
summarize_paper(paper_1)
summarize_paper(paper_2)
summarize_paper(paper_3)
# → ~600 chars × 3 = 1,800 chars total
```

**vs. naive approach** (fetch all with abstracts + summarize all 10):
`35,000 + 6,000 = 41,000 chars` → **Two-phase saves ~90%**

### Recommended: list_findings Before Fetching New Data

Always check the DB first to avoid redundant API calls:

```
list_findings(topic="transformers", compact=true, limit=5)
# → check if we already have relevant findings
# Only fetch from arXiv if cache miss
```

---

## Auto-Compaction

`autoCompact: true` in `settings.json` instructs Claude Code to automatically compact the conversation context when it approaches the model's context window limit.

```mermaid
sequenceDiagram
    participant ORC as Orchestrator
    participant HARNESS as Claude Code Harness
    participant API as Claude API

    ORC->>HARNESS: Growing conversation context
    HARNESS->>HARNESS: Monitor token count
    alt Approaching limit (autoCompact=true)
        HARNESS->>API: Summarize conversation so far
        API-->>HARNESS: Compact summary
        HARNESS->>HARNESS: Replace full history with summary
        Note over HARNESS: Context window freed
    end
    HARNESS->>API: Continue with compacted context
```

**Do not fight auto-compaction** — it preserves task state in a summary. If you need a specific piece of context after compaction, re-read it from files (logs, DB) rather than relying on conversation history.

---

## Subagent Token Contract

Every subagent spawn must include this header:

```
TOKEN BUDGET: Return only JSON. No prose before or after.
Cap: [N results / N issues / N findings].
Field values ≤ [X] chars each.
```

This is enforced in all `.claude/commands/*.md` files. Do not remove it.
