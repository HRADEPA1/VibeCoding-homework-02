# System Architecture

## Overview

This project is a **Senior R&D Agent** platform built on Claude Code. It orchestrates parallel subagents, MCP servers, and a local SQLite store to automate research, code review, analysis, documentation, and deployment validation.

---

## High-Level Architecture

```mermaid
graph TB
    subgraph USER["User Interface"]
        CLI[Claude Code CLI]
        CMDS[Slash Commands<br/>/research /review /analyze /document /deploy-check]
    end

    subgraph CLAUDE["Claude Orchestrator"]
        ORC[Main Agent<br/>claude-sonnet-4-6]
        HOOKS[Hook System<br/>PreToolUse / PostToolUse / Stop]
        LOG_SYS[Log Router]
    end

    subgraph AGENTS["Parallel Subagents (Task tool)"]
        RA[research-agent]
        RVA[review-agent]
        TA[test-agent]
        DA[doc-agent]
        SEC[security-agent]
        ENV[env-agent]
    end

    subgraph MCP["MCP Servers"]
        FS[filesystem<br/>@modelcontextprotocol/server-filesystem]
        GH[github<br/>@modelcontextprotocol/server-github]
        BS[brave-search<br/>@modelcontextprotocol/server-brave-search]
        SQ[sqlite<br/>@modelcontextprotocol/server-sqlite]
        RM[research-mcp<br/>custom node server]
    end

    subgraph STORAGE["Persistent Storage"]
        DB[(research.db<br/>SQLite)]
        LOGS[.claude/logs/<br/>agent.log / errors.log<br/>deploy-history.log]
        NOTES[.claude/session-notes.md]
        DOCS[docs/]
    end

    CLI --> ORC
    CMDS --> ORC
    ORC --> HOOKS
    HOOKS --> LOG_SYS
    LOG_SYS --> LOGS
    ORC --> AGENTS
    AGENTS --> MCP
    MCP --> STORAGE
    RM --> DB
    SQ --> DB

    style USER fill:#1e3a5f,color:#fff
    style CLAUDE fill:#1a472a,color:#fff
    style AGENTS fill:#4a235a,color:#fff
    style MCP fill:#7b3f00,color:#fff
    style STORAGE fill:#2c3e50,color:#fff
```

---

## MCP Server Registry

```mermaid
flowchart LR
    subgraph MCP_SERVERS["MCP Server Layer"]
        direction TB
        FS["filesystem<br/>npx @modelcontextprotocol/server-filesystem<br/>Scoped: . / ./data / ./src / ./docs"]
        GH["github<br/>npx @modelcontextprotocol/server-github<br/>Auth: GITHUB_TOKEN env"]
        BS["brave-search<br/>npx @modelcontextprotocol/server-brave-search<br/>Auth: BRAVE_API_KEY env"]
        SQ["sqlite<br/>npx @modelcontextprotocol/server-sqlite<br/>DB: ./data/research.db"]
        RM["research-mcp (custom)<br/>node ./mcp-servers/research-mcp/server.js<br/>v1.1.0 — with structured logging"]
    end

    AGENT[Claude Agent] --> FS
    AGENT --> GH
    AGENT --> BS
    AGENT --> SQ
    AGENT --> RM
    RM --> ARXIV[arXiv API]
    RM --> DB[(research.db)]
    RM --> LOGF[data/logs/research-mcp.log]

    style MCP_SERVERS fill:#7b3f00,color:#fff
    style ARXIV fill:#555,color:#fff
```

---

## Permission Model

```mermaid
flowchart TD
    TOOL[Tool Call] --> CHECK{Permission Check}
    CHECK -->|Allowed| EXEC[Execute]
    CHECK -->|Denied| BLOCK[Block + Warn]

    EXEC --> LOG_OK[Log to agent.log]
    BLOCK --> LOG_ERR[Log to errors.log]

    subgraph ALLOW["Allowed Patterns"]
        A1["Bash: git, npm, pnpm, npx, node, python3, uv, rg, fd, jq, curl, mkdir, echo, date"]
        A2["Read: ** (all files)"]
        A3["Write/Edit: src/**, docs/**, data/**, .claude/**"]
        A4["MCP: all registered servers"]
        A5["WebFetch/WebSearch: *"]
    end

    subgraph DENY["Denied Patterns"]
        D1["Bash: rm -rf /"]
        D2["Bash: sudo:*"]
        D3["Bash: curl * | bash (pipe to shell)"]
        D4["Write: /etc/**, /usr/**"]
    end

    style ALLOW fill:#1a472a,color:#fff
    style DENY fill:#7b1c1c,color:#fff
```
