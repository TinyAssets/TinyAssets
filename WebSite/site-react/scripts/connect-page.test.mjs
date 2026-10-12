// /connect is the power-user door: one hosted MCP address, copy-paste configs,
// a Cursor install link, and a single mailbox. No price.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  API_KEYS_PR,
  CLAUDE_CODE_JSON,
  CODEX_COMMANDS,
  CONTACT,
  CURSOR_DEEPLINK,
  GENERIC_JSON,
  MCP_URL,
} from "../app/connect/snippets.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(here, "..");

function source(path) {
  return readFileSync(resolve(siteRoot, path), "utf8");
}

test("connect snippets use the hosted MCP address and one mailbox", () => {
  assert.equal(MCP_URL, "https://tinyassets.io/mcp");
  assert.equal(CONTACT, "info@tinyassets.io");
  assert.equal(API_KEYS_PR, "https://github.com/TinyAssets/TinyAssets/pull/4602");
  assert.match(CLAUDE_CODE_JSON, new RegExp(MCP_URL.replaceAll(".", "\\.")));
  assert.match(CODEX_COMMANDS, /codex mcp add TinyAssets --url https:\/\/tinyassets\.io\/mcp/);
  assert.match(CODEX_COMMANDS, /codex mcp login TinyAssets/);
  assert.match(GENERIC_JSON, /streamable-http/);

  const config = new URL(CURSOR_DEEPLINK).searchParams.get("config");
  assert.equal(new URL(CURSOR_DEEPLINK).searchParams.get("name"), "TinyAssets");
  assert.deepEqual(JSON.parse(Buffer.from(config, "base64").toString("utf8")), { url: MCP_URL });
});

test("the connect page is a real route with no price and no other mailbox", () => {
  const page = source("app/connect/page.tsx");
  const snippets = source("app/connect/snippets.mjs");
  const body = `${page}\n${snippets}`;

  assert.match(page, /Add TinyAssets to the tool you already use/);
  assert.match(page, /Add to Cursor/);
  assert.match(page, /pull request 4602/);
  assert.doesNotMatch(page, /robots:\s*\{\s*index:\s*false/);

  const emails = body.match(/[A-Za-z0-9._%+-]+@tinyassets\.io/g) ?? [];
  assert.deepEqual([...new Set(emails)], ["info@tinyassets.io"]);
  assert.doesNotMatch(body, /\$20\b|premium|pricing|\/fine-print/i);
  assert.doesNotMatch(body, /ops@|security@|legal@|dmca@/);
});
