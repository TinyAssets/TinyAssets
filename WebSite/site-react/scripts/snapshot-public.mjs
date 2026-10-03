#!/usr/bin/env node
// snapshot-public.mjs — refresh lib/mcp-snapshot.json, the checked-in public
// fallback the site shows when a browser cannot reach the endpoint.
//
// It reads the public universe list through the shared projection contract
// (read_graph target=graphs) as the named canary service principal, sanitizes
// it to public scalars, and fails closed if completeness cannot be proven.
// Nothing else is baked: no goals, no pages, no operator status.
//
//   node scripts/snapshot-public.mjs                 # refresh against tinyassets.io/mcp
//   SNAPSHOT_REQUIRED=1 node scripts/snapshot-public.mjs   # a refused refresh exits 1
//
// Untrusted error detail from the endpoint is never printed: a failed refresh
// says that it failed and which step, nothing more.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertSnapshotEndpoint,
  publicGraphCall,
  requireCompleteCollection,
  sanitizePublicUniverse,
} from "../../shared/mcp/public-read-contract.js";

const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, "../lib/mcp-snapshot.json");
const ENDPOINT = assertSnapshotEndpoint(
  process.env.TINY_SNAPSHOT_URL || "https://tinyassets.io/mcp",
);
const TOKEN = (process.env.TINYASSETS_WIKI_CANARY_TOKEN || "").trim();
const REQUIRED = process.env.SNAPSHOT_REQUIRED === "1";
const LIMIT = 100;

let sessionId = null;
let nextId = 1;

async function rpc(method, params = {}) {
  if (!TOKEN) {
    throw new Error("TINYASSETS_WIKI_CANARY_TOKEN is required");
  }
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    Authorization: `Bearer ${TOKEN}`,
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: nextId++, method, params }),
  });
  const sid = res.headers.get("Mcp-Session-Id");
  if (sid && !sessionId) sessionId = sid;
  if (!res.ok) throw new Error(`public endpoint answered HTTP ${res.status}`);
  let text = await res.text();
  if ((res.headers.get("Content-Type") ?? "").includes("text/event-stream")) {
    const line = text.split("\n").find((l) => l.startsWith("data:"));
    if (!line) throw new Error("SSE response missing data line");
    text = line.replace(/^data:\s*/, "");
  }
  const json = JSON.parse(text);
  if (json.error) throw new Error("public endpoint returned a JSON-RPC error");
  return json.result;
}

async function callTool(name, args) {
  const result = await rpc("tools/call", { name, arguments: args });
  if (result?.structuredContent && typeof result.structuredContent === "object") {
    return result.structuredContent;
  }
  const item = result?.content?.find((c) => c?.type === "text");
  if (!item?.text) return null;
  const parsed = JSON.parse(item.text);
  if (parsed && typeof parsed.result === "string") {
    try {
      return JSON.parse(parsed.result);
    } catch {
      return parsed.result;
    }
  }
  return parsed;
}

async function refresh() {
  await rpc("initialize", {
    protocolVersion: "2025-06-18",
    clientInfo: { name: "tinyassets-site-snapshot", version: "0.2.0" },
    capabilities: {},
  });
  const call = publicGraphCall("graphs", LIMIT);
  const payload = await callTool(call.name, call.args);
  const universes = requireCompleteCollection(payload, "command_centers", "read_graph graphs", LIMIT)
    .map(sanitizePublicUniverse)
    .map((u) => ({
      id: u.id,
      visibility: u.visibility,
      phase: u.phase_human ?? u.phase ?? "unknown",
      word_count: u.word_count ?? 0,
      last_activity_at: u.last_activity_at ?? null,
    }))
    .sort(
      (a, b) =>
        (Date.parse(b.last_activity_at ?? "") || 0) - (Date.parse(a.last_activity_at ?? "") || 0),
    );
  return {
    fetched_at: new Date().toISOString(),
    source: "tinyassets.io/mcp · read_graph target=graphs · public projection only",
    command_centers: universes,
  };
}

async function main() {
  let snapshot;
  try {
    snapshot = await refresh();
  } catch {
    const previous = JSON.parse(readFileSync(OUT, "utf8"));
    if (REQUIRED) {
      console.error("Required public snapshot refresh failed; keeping the checked-in snapshot.");
      process.exit(1);
    }
    console.error(
      `Public snapshot refresh failed; keeping the checked-in snapshot from ${previous.fetched_at}.`,
    );
    return;
  }
  writeFileSync(OUT, JSON.stringify(snapshot, null, 2) + "\n", "utf8");
  console.log(`wrote ${OUT}: ${snapshot.command_centers.length} public universes at ${snapshot.fetched_at}`);
}

main();
