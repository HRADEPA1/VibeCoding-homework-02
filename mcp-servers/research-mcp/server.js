/**
 * research-mcp — Custom MCP Server
 *
 * Provides tools for academic research pipelines:
 *   - fetch_arxiv    : query arXiv API and return structured paper metadata
 *   - summarize_paper: extract key contribution, methods, results from abstract
 *   - store_finding  : persist a structured finding to SQLite
 *   - list_findings  : retrieve stored findings with optional filters
 *
 * Run: node server.js
 * Communicates over stdio (MCP transport).
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fetch from "node-fetch";
import { XMLParser } from "fast-xml-parser";
import Database from "better-sqlite3";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Config ────────────────────────────────────────────────────────────────────
const ARXIV_BASE = process.env.ARXIV_BASE_URL ?? "https://export.arxiv.org/api/query";
const MAX_RESULTS = parseInt(process.env.MAX_RESULTS ?? "10", 10);
const DB_PATH = process.env.DB_PATH ?? path.join(__dirname, "../../data/research.db");
const LOG_FILE = process.env.LOG_FILE ?? path.join(__dirname, "../../data/logs/research-mcp.log");
const LOG_LEVEL = (process.env.LOG_LEVEL ?? "info").toLowerCase();
// Compression: cap abstract length to save tokens sent back to the model
const ABSTRACT_MAX_CHARS = parseInt(process.env.ABSTRACT_MAX_CHARS ?? "800", 10);
// Fields returned by list_findings in compact mode (omits full summary text)
const COMPACT_FIELDS = ["id", "topic", "source_type", "title", "url", "relevance", "fetched_at"];

// ── Logger ────────────────────────────────────────────────────────────────────
const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const currentLevel = LEVELS[LOG_LEVEL] ?? 1;

fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
const logStream = fs.createWriteStream(LOG_FILE, { flags: "a" });

function log(level, message, data = {}) {
  if ((LEVELS[level] ?? 1) < currentLevel) return;
  const entry = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    message,
    ...data,
  });
  logStream.write(entry + "\n");
  // also emit to stderr so MCP host can capture it
  process.stderr.write(`[research-mcp][${level.toUpperCase()}] ${message}\n`);
}

// ── Database setup ────────────────────────────────────────────────────────────
fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
const db = new Database(DB_PATH);

db.exec(`
  CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT    NOT NULL,
    source_type TEXT    NOT NULL DEFAULT 'arxiv',
    title       TEXT    NOT NULL,
    url         TEXT,
    authors     TEXT,
    summary     TEXT,
    relevance   REAL    DEFAULT 0.0,
    tags        TEXT,
    fetched_at  TEXT    DEFAULT (datetime('now'))
  );
  CREATE INDEX IF NOT EXISTS idx_findings_topic ON findings(topic);
`);

log("info", "Database initialized", { path: DB_PATH });

const insertFinding = db.prepare(`
  INSERT INTO findings (topic, source_type, title, url, authors, summary, relevance, tags)
  VALUES (@topic, @source_type, @title, @url, @authors, @summary, @relevance, @tags)
`);

const queryFindings = db.prepare(`
  SELECT * FROM findings WHERE topic LIKE ? ORDER BY relevance DESC, fetched_at DESC LIMIT ?
`);

// ── arXiv helpers ─────────────────────────────────────────────────────────────
const xmlParser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "@_" });

async function fetchArxiv(query, maxResults = MAX_RESULTS) {
  const params = new URLSearchParams({
    search_query: `all:${query}`,
    start: "0",
    max_results: String(maxResults),
    sortBy: "relevance",
    sortOrder: "descending",
  });

  const url = `${ARXIV_BASE}?${params}`;
  log("debug", "Fetching arXiv", { query, maxResults, url });

  const res = await fetch(url);
  if (!res.ok) throw new Error(`arXiv API error: ${res.status} ${res.statusText}`);

  const xml = await res.text();
  const parsed = xmlParser.parse(xml);
  const feed = parsed?.feed;
  const entries = feed?.entry ? (Array.isArray(feed.entry) ? feed.entry : [feed.entry]) : [];

  log("info", "arXiv results fetched", { query, count: entries.length });

  return entries.map((e) => {
    const rawAbstract = e.summary?.replace(/\s+/g, " ").trim() ?? "";
    const abstract = rawAbstract.length > ABSTRACT_MAX_CHARS
      ? rawAbstract.slice(0, ABSTRACT_MAX_CHARS) + "…"
      : rawAbstract;
    return {
      id: e.id?.split("/abs/")[1] ?? e.id,
      title: e.title?.replace(/\s+/g, " ").trim() ?? "Unknown",
      // Limit authors to first 3 to avoid long author lists burning tokens
      authors: (() => {
        const list = Array.isArray(e.author)
          ? e.author.map((a) => a.name)
          : [e.author?.name ?? "Unknown"];
        return list.length > 3 ? list.slice(0, 3).join(", ") + " et al." : list.join(", ");
      })(),
      abstract,
      published: e.published?.slice(0, 10) ?? "",
      url: `https://arxiv.org/abs/${e.id?.split("/abs/")[1] ?? e.id}`,
    };
  });
}

function summarizePaper(paper) {
  const { title, abstract, authors, published } = paper;
  const sentences = abstract.split(/(?<=[.!?])\s+/);

  // Heuristic extraction: contribution, methods, results
  const contribution = sentences.find((s) =>
    /propose|present|introduce|novel|new approach|we show|we demonstrate/i.test(s)
  ) ?? sentences[0] ?? "";

  const methods = sentences.find((s) =>
    /method|model|algorithm|architecture|framework|approach|technique/i.test(s)
  ) ?? sentences[1] ?? "";

  const results = sentences.find((s) =>
    /result|achiev|outperform|state.of.the.art|benchmark|evaluat|improve|accuracy|score/i.test(s)
  ) ?? sentences[sentences.length - 1] ?? "";

  return {
    title,
    authors,
    published,
    contribution: contribution.trim(),
    methods: methods.trim(),
    results: results.trim(),
    oneLineSummary: `${contribution.trim()} ${methods.trim()}`.slice(0, 200),
  };
}

// ── Tool definitions ──────────────────────────────────────────────────────────
const TOOLS = [
  {
    name: "fetch_arxiv",
    description:
      "Search arXiv for academic papers on a given topic. Returns structured paper metadata. Abstracts are capped at ABSTRACT_MAX_CHARS to conserve tokens. Set compact=true to omit abstracts entirely.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Search query for arXiv (e.g. 'transformer attention mechanism')",
        },
        max_results: {
          type: "number",
          description: "Maximum number of papers to return (default: 10, max: 50)",
          default: 10,
        },
        compact: {
          type: "boolean",
          description: "If true, omit abstracts from response to save tokens. Use summarize_paper separately for papers of interest.",
          default: false,
        },
      },
      required: ["query"],
    },
  },
  {
    name: "summarize_paper",
    description:
      "Extract key contribution, methods, and results from a paper's abstract. Provide the paper object returned by fetch_arxiv.",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string" },
        abstract: { type: "string" },
        authors: { type: "string" },
        published: { type: "string" },
        url: { type: "string" },
      },
      required: ["title", "abstract"],
    },
  },
  {
    name: "store_finding",
    description:
      "Persist a research finding to the local SQLite database for future retrieval.",
    inputSchema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Research topic / search query used" },
        source_type: {
          type: "string",
          enum: ["arxiv", "web", "book", "other"],
          default: "arxiv",
        },
        title: { type: "string" },
        url: { type: "string" },
        authors: { type: "string" },
        summary: { type: "string", description: "2-3 sentence summary" },
        relevance: {
          type: "number",
          description: "Relevance score 0.0–10.0",
          minimum: 0,
          maximum: 10,
        },
        tags: { type: "string", description: "Comma-separated tags" },
      },
      required: ["topic", "title", "summary"],
    },
  },
  {
    name: "list_findings",
    description: "Retrieve stored research findings from the database, optionally filtered by topic. Use compact=true (default) to return only key fields without full summary text, saving tokens.",
    inputSchema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Filter by topic (supports % wildcard)" },
        limit: { type: "number", default: 10 },
        compact: {
          type: "boolean",
          description: "If true (default), return only id/topic/source_type/title/url/relevance/fetched_at. Set false to include full summary.",
          default: true,
        },
      },
    },
  },
];

// ── Tool handlers ─────────────────────────────────────────────────────────────
async function handleTool(name, args) {
  const startMs = Date.now();
  log("info", `Tool invoked: ${name}`, { args });

  try {
    let result;

    switch (name) {
      case "fetch_arxiv": {
        const papers = await fetchArxiv(args.query, Math.min(args.max_results ?? MAX_RESULTS, 50));
        const payload = args.compact
          ? papers.map(({ abstract: _a, ...rest }) => rest)  // drop abstracts entirely
          : papers;
        result = {
          content: [
            {
              type: "text",
              text: JSON.stringify({ query: args.query, count: payload.length, papers: payload }),
            },
          ],
        };
        break;
      }

      case "summarize_paper": {
        const summary = summarizePaper(args);
        // Always return compact form — full fields available in DB after store_finding
        result = {
          content: [{ type: "text", text: JSON.stringify(summary) }],
        };
        break;
      }

      case "store_finding": {
        const row = insertFinding.run({
          topic: args.topic,
          source_type: args.source_type ?? "arxiv",
          title: args.title,
          url: args.url ?? null,
          authors: args.authors ?? null,
          summary: args.summary,
          relevance: args.relevance ?? 5.0,
          tags: args.tags ?? null,
        });
        log("info", "Finding stored", { id: row.lastInsertRowid, topic: args.topic });
        result = {
          content: [
            {
              type: "text",
              text: JSON.stringify({ success: true, id: row.lastInsertRowid }),
            },
          ],
        };
        break;
      }

      case "list_findings": {
        const topic = args.topic ? `%${args.topic}%` : "%";
        const rows = queryFindings.all(topic, args.limit ?? 10);
        // compact=true by default — strip heavy text fields to save tokens
        const compact = args.compact !== false;
        const findings = compact
          ? rows.map((r) => Object.fromEntries(COMPACT_FIELDS.map((f) => [f, r[f]])))
          : rows;
        log("info", "Findings listed", { topic: args.topic, count: findings.length, compact });
        result = {
          content: [
            {
              type: "text",
              text: JSON.stringify({ count: findings.length, findings }),
            },
          ],
        };
        break;
      }

      default:
        throw new Error(`Unknown tool: ${name}`);
    }

    log("info", `Tool completed: ${name}`, { durationMs: Date.now() - startMs });
    return result;

  } catch (err) {
    log("error", `Tool failed: ${name}`, { error: err.message, stack: err.stack, durationMs: Date.now() - startMs });
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true,
    };
  }
}

// ── Server setup ──────────────────────────────────────────────────────────────
const server = new Server(
  { name: "research-mcp", version: "1.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  return handleTool(name, args ?? {});
});

// ── Start ─────────────────────────────────────────────────────────────────────
const transport = new StdioServerTransport();
await server.connect(transport);
log("info", "Server started", { transport: "stdio", version: "1.1.0", logFile: LOG_FILE });
