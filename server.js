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
import { fileURLToPath } from "url";

// ── Config ────────────────────────────────────────────────────────────────────
const ARXIV_BASE = process.env.ARXIV_BASE_URL ?? "https://export.arxiv.org/api/query";
const MAX_RESULTS = parseInt(process.env.MAX_RESULTS ?? "10", 10);
const DB_PATH = process.env.DB_PATH ?? path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../data/research.db"
);

// ── Database setup ────────────────────────────────────────────────────────────
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

  const res = await fetch(`${ARXIV_BASE}?${params}`);
  if (!res.ok) throw new Error(`arXiv API error: ${res.status} ${res.statusText}`);

  const xml = await res.text();
  const parsed = xmlParser.parse(xml);
  const feed = parsed?.feed;
  const entries = feed?.entry ? (Array.isArray(feed.entry) ? feed.entry : [feed.entry]) : [];

  return entries.map((e) => ({
    id: e.id?.split("/abs/")[1] ?? e.id,
    title: e.title?.replace(/\s+/g, " ").trim() ?? "Unknown",
    authors: Array.isArray(e.author)
      ? e.author.map((a) => a.name).join(", ")
      : (e.author?.name ?? "Unknown"),
    abstract: e.summary?.replace(/\s+/g, " ").trim() ?? "",
    published: e.published?.slice(0, 10) ?? "",
    url: `https://arxiv.org/abs/${e.id?.split("/abs/")[1] ?? e.id}`,
    pdfUrl: `https://arxiv.org/pdf/${e.id?.split("/abs/")[1] ?? e.id}`,
  }));
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
      "Search arXiv for academic papers on a given topic. Returns structured paper metadata including title, authors, abstract, and URLs.",
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
    description: "Retrieve stored research findings from the database, optionally filtered by topic.",
    inputSchema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Filter by topic (supports % wildcard)" },
        limit: { type: "number", default: 20 },
      },
    },
  },
];

// ── Tool handlers ─────────────────────────────────────────────────────────────
async function handleTool(name, args) {
  switch (name) {
    case "fetch_arxiv": {
      const papers = await fetchArxiv(args.query, Math.min(args.max_results ?? MAX_RESULTS, 50));
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ query: args.query, count: papers.length, papers }, null, 2),
          },
        ],
      };
    }

    case "summarize_paper": {
      const summary = summarizePaper(args);
      return {
        content: [{ type: "text", text: JSON.stringify(summary, null, 2) }],
      };
    }

    case "store_finding": {
      const result = insertFinding.run({
        topic: args.topic,
        source_type: args.source_type ?? "arxiv",
        title: args.title,
        url: args.url ?? null,
        authors: args.authors ?? null,
        summary: args.summary,
        relevance: args.relevance ?? 5.0,
        tags: args.tags ?? null,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ success: true, id: result.lastInsertRowid }),
          },
        ],
      };
    }

    case "list_findings": {
      const topic = args.topic ? `%${args.topic}%` : "%";
      const rows = queryFindings.all(topic, args.limit ?? 20);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ count: rows.length, findings: rows }, null, 2),
          },
        ],
      };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ── Server setup ──────────────────────────────────────────────────────────────
const server = new Server(
  { name: "research-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    return await handleTool(name, args ?? {});
  } catch (err) {
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true,
    };
  }
});

// ── Start ─────────────────────────────────────────────────────────────────────
const transport = new StdioServerTransport();
await server.connect(transport);
console.error("[research-mcp] Server running on stdio");
