# Agent Workflows

All agents are spawned via the **Task tool** from the main Claude orchestrator. Each runs in an isolated context and returns a structured result.

---

## Agent Roster

| Agent | Trigger | Tools Used | Output |
|-------|---------|-----------|--------|
| `research-agent` | `/research <topic>` | brave-search, research-mcp, sqlite | Ranked findings + report |
| `review-agent` | `/review <path>` | Bash, Read, rg | JSON severity report |
| `test-agent` | `/analyze <path>` | Bash, Read | Coverage + failure report |
| `doc-agent` | `/document <path>` | Read, Write, Bash | docs/API.md + README update |
| `security-agent` | `/deploy-check` | Bash (npm audit, bandit) | CVE list + secrets scan |
| `env-agent` | `/deploy-check` | Read, Bash | Config validation report |

---

## Research Pipeline (`/research`)

```mermaid
sequenceDiagram
    actor User
    participant ORC as Orchestrator
    participant WEB as research-agent (web)
    participant ACA as research-agent (arxiv)
    participant RM as research-mcp
    participant SQ as sqlite MCP
    participant LOG as agent.log

    User->>ORC: /research <topic>
    ORC->>LOG: RESEARCH_START topic=<topic>

    par Parallel fetch
        ORC->>WEB: brave_web_search × 3 queries
        ORC->>ACA: fetch_arxiv query=<topic>
        ACA->>RM: fetch_arxiv(query, max=20)
        RM-->>ACA: papers[]
        ACA->>RM: summarize_paper(paper) × N
        RM-->>ACA: summaries[]
    end

    WEB-->>ORC: web_results[]
    ACA-->>ORC: academic_results[]

    ORC->>ORC: Merge + deduplicate + rank
    ORC->>SQ: INSERT INTO findings × N

    ORC->>LOG: RESEARCH_COMPLETE results=N duration=Xs
    ORC-->>User: Research Report (Markdown)
```

---

## Code Review Pipeline (`/review`)

```mermaid
sequenceDiagram
    actor User
    participant ORC as Orchestrator
    participant STATIC as Static Analysis (sync)
    participant LOGIC as review-agent (logic)
    participant ARCH as review-agent (architecture)
    participant LOG as agent.log

    User->>ORC: /review <path>
    ORC->>STATIC: lint + type-check + security-scan
    alt Critical errors found
        STATIC-->>ORC: FAIL
        ORC-->>User: ❌ Blocked (static analysis)
    else Clean
        STATIC-->>ORC: OK

        par Parallel review
            ORC->>LOGIC: logic/correctness/edge-cases
            ORC->>ARCH: SOLID/DRY/naming/complexity
        end

        LOGIC-->>ORC: issues[] (JSON)
        ARCH-->>ORC: issues[] (JSON)

        ORC->>ORC: Merge, group by severity
        ORC->>LOG: REVIEW_<VERDICT> target=<path> critical=N warnings=N
        ORC-->>User: Review Report (Markdown)
    end
```

---

## Deploy-Check Pipeline (`/deploy-check`)

```mermaid
sequenceDiagram
    actor User
    participant ORC as Orchestrator
    participant TEST as test-agent
    participant SEC as security-agent
    participant ENV as env-agent
    participant LOG as deploy-history.log

    User->>ORC: /deploy-check

    par 3 parallel gates
        ORC->>TEST: run full test suite
        ORC->>SEC: npm audit + secret scan + .gitignore check
        ORC->>ENV: validate env vars + config files + health checks
    end

    TEST-->>ORC: PASS/FAIL (coverage%)
    SEC-->>ORC: PASS/FAIL (CVE list)
    ENV-->>ORC: PASS/FAIL (issues list)

    ORC->>ORC: Aggregate all results

    alt All PASS
        ORC->>LOG: DEPLOY_PASS branch=X commit=Y
        ORC-->>User: ✅ READY TO DEPLOY
    else Any FAIL
        ORC->>LOG: DEPLOY_FAIL failed=<checks>
        ORC-->>User: ❌ BLOCKED — fix required
    end
```

---

## Analyze Pipeline (`/analyze`)

```mermaid
flowchart TD
    IN[$ARGUMENTS] --> DETECT{Auto-detect input type}

    DETECT -->|*.csv / *.json / *.parquet| DATA[Data Analysis Mode]
    DETECT -->|directory / *.py / *.js / *.ts| CODE[Code Analysis Mode]
    DETECT -->|natural language question| ROOT[Root-Cause Mode]

    CODE --> CA[Spawn: code-intelligence subagent]
    CA --> CA1[Structure analysis]
    CA --> CA2[Complexity metrics]
    CA --> CA3[Git hotspots]
    CA --> CA4[Dependency health]

    DATA --> DA1[Profile dataset — pandas describe]
    DA1 --> DA[Spawn: data-analysis subagent]
    DA --> DA2[Distribution + outliers]
    DA --> DA3[Correlation analysis]
    DA --> DA4[Anomaly detection]

    ROOT --> RA[Spawn: root-cause subagent]
    RA --> RA1[Reproduce issue]
    RA --> RA2[Trace execution]
    RA --> RA3[Form + validate hypothesis]
    RA --> RA4[Propose fix]

    CA4 & DA4 & RA4 --> OUT[Structured JSON + Markdown summary]

    style CODE fill:#1a472a,color:#fff
    style DATA fill:#1e3a5f,color:#fff
    style ROOT fill:#4a235a,color:#fff
```
