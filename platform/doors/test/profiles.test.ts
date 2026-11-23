/**
 * The MCP door's tool profiles and code mode, over the real MCP client SDK and
 * the real codemode Worker: each client is served exactly its own grant's profile (`custom`,
 * the owner's own choice, until the owner names `directory`), `code` only while the owner's
 * policy has code mode on (off by default); one `code` call runs a script of several tool
 * calls, and its calls stop once outside access is switched off; a script is stopped once its
 * caller stops waiting (an `/api` caller's request aborted, a 2025-11-25 client past
 * `LEGACY_WAIT_MS` on the fake clock, where a 2026-07-28 client's runs on), each while its
 * call is held at the owner gate, so the stop the doors hand is closed before the object reads
 * it; a revoked client is
 * refused before any tool is listed; an API key is held to its profile at `/api` and `/mcp`
 * as a client is at `/mcp`, and its `code` call at `/api` refused, saying why, while code mode
 * is off.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { until } from "../../ci/workerd";
import { LEGACY_WAIT_MS } from "../src/mcp";

import { apiKeyOrigin } from "../../contract/src/binding";
import type { Binding } from "../../contract/src/binding";
import { mcpOrigin } from "../src/binder";
import { profileNames } from "../../contract/src/doors";
import { APP_RESOURCE } from "../../contract/src/identity";
import { INSTRUCTIONS } from "../src/instructions";
import {
  answerOf, appPost, call, connect, doorsEnv, grantsOf, legacyPost, OPEN, OPEN_RECORD, ownDoor,
  registerClient, serveFirstPartyDocument, setControl, signInApp, type Tokens, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

async function mcpClient(tokens: Pick<Tokens, "access_token">): Promise<Client> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  const transport = new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
  });
  await client.connect(transport);
  return client;
}

/** The custom profile while code mode is off, its default: every tool but code. */
const CUSTOM_OFF = profileNames("custom").filter((name) => name !== "code");

async function listed(tokens: Pick<Tokens, "access_token">): Promise<string[]> {
  const client = await mcpClient(tokens);
  const { tools } = await client.listTools();
  await client.close();
  return tools.map((tool) => tool.name);
}

/** A script of tool calls that ends with its value unless it is stopped first. */
const FIFTY_CALLS = "for (let n = 0; n < 50; n++) { await tools.agents_list({}); } return 'ran';";

/** The held `codeRun` call at the owner gate, once it is there. */
async function heldCode(): Promise<number> {
  let id: number | undefined;
  await until(async () => {
    id = (await env.OWNER_GATE.held()).find((held) => held.method === "codeRun")?.id;
    return id !== undefined;
  }, "the code call at the gate");
  return id!;
}

/** A signed-in owner, and what it changes in its own session. */
async function signedIn() {
  const who = owner();
  const app = await signInApp({ owner: who });
  const stub = doorsEnv.OWNER.getByName(who);
  const own: Binding = { owner: who, caller: who, origin: null };
  const session: Binding = { ...own, session: app.session };
  return {
    who, app, stub, own,
    policy: async (codemode: boolean, custom?: string[]) => {
      const { generation, policy } = await ownDoor(who, "policyRead");
      const { tools: _tools, ...rest } = policy;
      await stub.policyWrite(session, { expected_generation: generation, policy: {
        ...rest, codemode: { on: codemode },
        ...(custom === undefined ? {} : { tools: { custom } }) } });
    },
    profile: async (client: string, profile: string) => {
      const row = (await grantsOf(who)).find((c) => c.client === client)!;
      await stub.grantSet(session, { client, source: row.source, mode: row.mode,
                                     scopes: row.scopes as never,
                                     expected_revision: row.revision, name: "", profile });
    },
  };
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore = serveFirstPartyDocument();
});

afterEach(() => {
  restore();
});

describe("the MCP door's tool profiles", () => {
  it("serves each client exactly its own grant's profile", async () => {
    const o = await signedIn();
    const listedClient = await registerClient("Listed");
    const customClient = await registerClient("Custom");
    const listedTokens = await connect(listedClient, { owner: o.who });
    const customTokens = await connect(customClient, { owner: o.who });
    // Every grant here is a custom connector's: every tool but code, code mode off by
    // default.
    expect(await listed(listedTokens)).toEqual(CUSTOM_OFF);
    expect(await listed(customTokens)).toEqual(CUSTOM_OFF);
    const listedOrigin = await mcpOrigin(listedClient.clientId, listedClient.redirectUri);
    await o.profile(listedOrigin.client, "directory");
    expect(await listed(listedTokens)).toEqual(profileNames("directory"));
    // Code mode on: every custom tool.
    await o.policy(true);
    expect(await listed(customTokens)).toEqual(profileNames("custom"));
    expect(await listed(listedTokens)).toEqual(profileNames("directory"));
    // The owner's own choice of custom tools.
    await o.policy(true, ["agents_list", "code"]);
    expect(await listed(customTokens)).toEqual(["agents_list", "code"]);
    const client = await mcpClient(customTokens);
    const outside = await client.callTool({ name: "file_usage", arguments: {} })
      .then((result) => JSON.stringify(result), (error: Error) => error.message);
    expect(outside).toMatch(/not found/i);
    await client.close();
  });

  it("tells each client its profile's instructions, the agent prompt named to the owner's " +
     "own connector alone, and serves each the agent's definition", async () => {
    const o = await signedIn();
    const listedClient = await registerClient("Listed");
    const listedTokens = await connect(listedClient, { owner: o.who });
    const customTokens = await connect(await registerClient("Custom"), { owner: o.who });
    await listed(listedTokens);  // its grant's row, made by its first call
    await o.profile((await mcpOrigin(listedClient.clientId, listedClient.redirectUri)).client,
                    "directory");
    for (const [tokens, profile] of [[customTokens, "custom"],
                                     [listedTokens, "directory"]] as const) {
      const client = await mcpClient(tokens);
      expect(client.getInstructions()).toBe(INSTRUCTIONS[profile]);
      expect((await client.getPrompt({ name: "agent" })).messages[0]!.content).toMatchObject({
        type: "resource", resource: { uri: "tinyassets://agent/main/AGENTS.md" } });
      await client.close();
    }
  });

  it("serves search, fetch and file_read in ChatGPT's shape to a directory client", async () => {
    const o = await signedIn();
    // The owner's own write, as its app binds it: with the tier's numbers (STORE's wall).
    await o.stub.fileWrite({ ...o.own, control: OPEN_RECORD }, { path: "/workspace/plumber.md",
                                    expected_etag: null,
                                    content: new TextEncoder().encode(
                                      "---\ntype: Person\ntitle: Bo\n---\nBo fixes boilers.\n") });
    const registered = await registerClient("Listed");
    const tokens = await connect(registered, { owner: o.who });
    const client = await mcpClient(tokens);
    expect((await client.listTools()).tools.map((tool) => tool.name))
      .toEqual(expect.arrayContaining(["file_read", "search", "fetch"]));
    await o.profile((await mcpOrigin(registered.clientId, registered.redirectUri)).client,
                    "directory");
    const found = await client.callTool({ name: "search", arguments: { query: "boilers" } });
    expect(answerOf(found)).toMatchObject({ next_cursor: null, results: [{
      id: "/workspace/plumber.md", title: "Bo",
      url: `${APP_RESOURCE}#data/${encodeURIComponent("/workspace")}` }] });
    const fetched = await client.callTool({ name: "fetch",
                                            arguments: { id: "/workspace/plumber.md" } });
    expect(answerOf(fetched)).toMatchObject({ id: "/workspace/plumber.md", title: "Bo",
                                                      metadata: { type: "Person" } });
    expect((answerOf(fetched) as { text: string }).text).toContain("Bo fixes boilers.");
    const read = await client.callTool({ name: "file_read", arguments: {
      path: "/workspace/plumber.md", offset: 4, max_bytes: 12 } });
    expect(answerOf(read)).toMatchObject({ offset: 4, text: "type: Person",
                                                   next_offset: 16 });
    await client.close();
  });

  it("runs one script of several tool calls in one code call", async () => {
    const o = await signedIn();
    await o.policy(true);
    const registered = await registerClient();
    const tokens = await connect(registered, { owner: o.who });
    const client = await mcpClient(tokens);
    const ran = await client.callTool({ name: "code", arguments: { script: [
      "await tools.collection_write({ collection: 'board', row_id: 't', body: 'todo',",
      "  expected_etag: '', private: false });",
      "const digest = await tools.board_digest({ limit: 5, after: 0 });",
      "return digest.rows.map((row) => row.head);",
    ].join("\n") } });
    expect(ran.isError).toBeFalsy();
    expect(answerOf(ran)).toMatchObject({ ok: true, value: ["todo"],
      calls: { ok: 2, error: 0 }, landed: [{ name: "collection_write", status: "ok", count: 1 }] });
    // Outside access switched off: the door refuses before any script runs.
    await setControl({ ...OPEN, outside: "off" });
    const off = await legacyPost(tokens.access_token,
                                 { jsonrpc: "2.0", id: 1, method: "tools/list" });
    expect(off.status).toBe(503);
    await setControl(OPEN);
    // With code mode off again, the call is refused with its reason.
    await o.policy(false);
    const refused = await client.callTool({ name: "code", arguments: { script: "return 1;" } })
      .then((result) => JSON.stringify(result), (error: Error) => error.message);
    expect(refused).toMatch(/not found|code_mode_off/);
    await client.close();
  });

  // A 2025-11-25 client's script is stopped once its wait is past, its cancel unable to reach;
  // a 2026-07-28 client's runs on past it, its leaving its own.
  it.each([
    ["legacy" as const, { ok: false, error: { kind: "stopped" }, calls: { ok: 0 } }],
    [{ pin: "2026-07-28" as const }, { ok: true, value: "ran", calls: { ok: 50 } }],
  ])("answers a %j client's script held past LEGACY_WAIT_MS", async (mode, answer) => {
    const o = await signedIn();
    await o.policy(true);
    const tokens = await connect(await registerClient(), { owner: o.who });
    const client = new Client({ name: "test-agent", version: "1.0.0" },
                              { versionNegotiation: { mode } });
    await client.connect(new StreamableHTTPClientTransport(new URL(url("/mcp")), {
      fetch: (input, init) => call(new Request(input, init)),
      requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
    }));
    await env.OWNER_GATE.arm({ method: "codeRun", name: o.who, hold: "before" });
    // The doors' clock is faked: the legacy caller's wait passes while its call is held.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const asked = client.callTool({ name: "code", arguments: { script: FIFTY_CALLS } },
                                    { timeout: 2 * LEGACY_WAIT_MS });
      const id = await heldCode();
      vi.advanceTimersByTime(LEGACY_WAIT_MS);
      vi.useRealTimers();
      await env.OWNER_GATE.release(id);
      expect(answerOf(await asked)).toMatchObject(answer);
    } finally {
      vi.useRealTimers();
      await client.close();
    }
  });

  it("refuses a revoked client before any tool is listed", async () => {
    const o = await signedIn();
    const registered = await registerClient();
    const tokens = await connect(registered, { owner: o.who });
    expect(await listed(tokens)).toEqual(CUSTOM_OFF);
    await o.stub.grantRevoke(o.own, {
      client: (await mcpOrigin(registered.clientId, registered.redirectUri)).client });
    const response = await legacyPost(tokens.access_token,
                                      { jsonrpc: "2.0", id: 1, method: "tools/list" });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "outside client revoked" });
  });
});

describe("a key's tool profiles", () => {
  it("holds a key to its grant's profile", async () => {
    const o = await signedIn();
    const minted = await appPost("keys", o.app.access_token, o.app.session, {
      name: "laptop", scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] });
    const { key, key_id } = await minted.json<{ key: string; key_id: string }>();
    const api = (tool: string, body: unknown) => call(new Request(url(`/api/tools/${tool}`), {
      method: "POST", body: JSON.stringify(body),
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" } }));
    // A key's grant names custom too: code is withheld while code mode is off, and its call
    // refused saying why; once the owner turns it on, served.
    const off = await api("code", { script: "return 1;" });
    expect([off.status, await off.json()])
      .toEqual([403, { error: expect.stringMatching(/^code_mode_off: /) }]);
    await o.policy(true);
    const ran = await api("code", { script: "return (await tools.agents_list({})).agents;" });
    expect([ran.status, await ran.json()]).toEqual([200, expect.objectContaining({
      ok: true, value: [] })]);
    expect(await listed({ access_token: key })).toEqual(profileNames("custom"));
    await o.profile(apiKeyOrigin(key_id).client, "directory");
    expect((await api("code", { script: "return 1;" })).status).toBe(404);
    expect((await api("agents_list", {})).status).toBe(200);
    expect(await listed({ access_token: key })).toEqual(profileNames("directory"));
    await o.profile(apiKeyOrigin(key_id).client, "custom");
    await o.policy(true, ["code"]);
    expect((await api("agents_list", {})).status).toBe(404);
    expect(await listed({ access_token: key })).toEqual(["code"]);
  });

  it("stops a key's script once its request is aborted", async () => {
    const o = await signedIn();
    await o.policy(true);
    const minted = await appPost("keys", o.app.access_token, o.app.session, {
      name: "laptop", scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] });
    const { key } = await minted.json<{ key: string }>();
    const ran = (signal: AbortSignal) => call(new Request(url("/api/tools/code"), {
      method: "POST", body: JSON.stringify({ script: FIFTY_CALLS }), signal,
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" } }));
    // A caller that waits: its script runs to its value.
    const waited = await ran(new AbortController().signal);
    expect(await waited.json()).toMatchObject({ ok: true, value: "ran" });
    // One that leaves while its call is held on its way to the object: stopped before it
    // makes a call, answered so (the doors answer once the object does).
    await env.OWNER_GATE.arm({ method: "codeRun", name: o.who, hold: "before" });
    const leave = new AbortController();
    const answered = ran(leave.signal);
    const id = await heldCode();
    leave.abort();
    await env.OWNER_GATE.release(id);
    expect(await (await answered).json()).toMatchObject({ ok: false, error: {
      kind: "stopped", message: "the script's caller stopped waiting, so it was stopped" },
      calls: { ok: 0 } });
  });
});
