/**
 * The builder's RPC recorder (`ci/record.ts`): every public door of
 * `OwnerDO`, called over RPC with a spread of bindings and request shapes, as the owner and
 * as a live run, each answer one `RPC` line, ids taken out (`clean`), so base and head print
 * the same lines for the same behaviour.
 */
import { describe, it } from "vitest";

import { OwnerDO } from "../../src/owner-do";
import { rpcReserved } from "../../../contract/src/doors";
import { gated } from "../gateway-support";
import { DOT, MCP, MUSE, rig } from "../ledger-support";
import { ALL_SCOPES, ownerRun, runBinding, runnerBinding } from "../runs-support";
import { OPEN_RECORD } from "../support";
import { asked } from "./support";

const METHODS = Object.getOwnPropertyNames(OwnerDO.prototype)
  .filter((name) => !rpcReserved().includes(name)).sort();

function clean(text: string): string {
  return text
    .replace(/owner-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<owner>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<uuid>")
    .replace(/[A-Za-z0-9_-]{40,}/g, "<tok>")
    .replace(/[0-9a-f]{16,}/g, "<hex>")
    .replace(/\b\d{9,}(?:\.\d+)?\b/g, "<n>");
}

/** Every field name a door's request names anywhere in owner-do, by flavour. */
const NAMES = ["path", "dir", "limit", "after", "collection", "row_id", "body", "expected_etag",
  "private", "message", "client_send_id", "agent", "input", "run_id", "schedule_id", "change",
  "request_id", "through", "room_id", "client_message_id", "parent_id", "content", "query",
  "id", "intent_id", "chunks", "size", "version", "expected_generation", "policy", "client",
  "agent_id", "reason", "script", "lease_id", "epoch", "ops", "engine", "code", "conversation_id",
  "subscription_id", "export_id", "cursor", "days", "confirm", "binding_id", "clients",
  "key_id", "name", "scopes", "card_id", "digest", "frames", "signins", "mode", "from", "to",
  "replace", "recursive", "old_text", "new_text", "writer", "reservation"];

function fill(flavour: number): Record<string, unknown> {
  return Object.fromEntries(NAMES.map((name) => [name, flavour === 0 ? "nope"
    : flavour === 1 ? 1 : name === "path" || name === "dir" ? "/workspace/x.md" : null]));
}

const REQUESTS: [string, unknown][] = [["none", undefined], ["empty", {}], ["extra", { __x: 1 }],
  ["array", [1]], ["fill0", fill(0)], ["fill1", fill(1)], ["fill2", fill(2)]];

/** `method` called by each of `bindings` with each request shape, each answer one line. */
async function record(stub: DurableObjectStub<OwnerDO>, method: string,
                      bindings: [string, unknown][]): Promise<void> {
  for (const [b, binding] of bindings) {
    for (const [q, request] of REQUESTS) {
      let line: string;
      try {
        const value = await asked(stub, method, binding, request);
        line = `OK\t${clean(JSON.stringify(value) ?? "undefined").slice(0, 400)}`;
      } catch (error) {
        const e = error as Error & { code?: unknown };
        line = `THROW\t${String(e?.name)}\t${String(e?.code)}\t${clean(String(e?.message))
          .slice(0, 300)}`;
      }
      console.log(`RPC\t${method}\t${b}\t${q}\t${line}`);
    }
  }
}

describe("rpc: every door", () => {
  for (const method of METHODS) {
    it(`door ${method}`, async () => {
      const r = await rig();
      const visitor = { owner: r.owner, caller: `owner-visitor`, origin: null,
                        control: OPEN_RECORD };
      await record(r.stub, method, [
        ["own", r.bind(null)],
        ["session", r.session(null)],
        ["visitor", visitor],
        ["mcp", r.bind(MUSE)],
        ["mcp-session", r.bind(DOT, { session: r.cookie, source: MCP })],
        ["platform", { owner: r.owner, caller: "platform", origin: null }],
        ["noorigin", { owner: r.owner, caller: r.owner }],
        ["bad", { owner: "", caller: "x", origin: null }],
        ["egress", { owner: r.owner, caller: r.owner, origin: null,
                     sandbox: { kind: "egress", secret: "0".repeat(64) } }],
        ["sync", { owner: r.owner, caller: r.owner, origin: null,
                   sandbox: { kind: "sync", token: "0".repeat(64), egress: "0".repeat(64) } }],
        ["undefined", undefined],
      ]);
    });
  }
});

describe("rpc: every door, as a live run", () => {
  for (const method of METHODS) {
    it(`door ${method} (run)`, async () => {
      const r = await gated();
      const connection = await r.connect("sk-good");
      const { start } = await ownerRun(r.stub, r.bind(), {
        engine: "claude-code", credential: "api_key", connection, scopes: ALL_SCOPES });
      const narrow = await ownerRun(r.stub, r.bind(), {
        engine: "claude-code", credential: "api_key", connection, scopes: ["read_files"] },
                                    "narrow");
      await record(r.stub, method, [
        ["run", runBinding(r, start)],
        ["runnarrow", runBinding(r, narrow.start)],
        ["runner", runnerBinding(r, start)],
      ]);
    });
  }
});
