/**
 * The MCP door's tool profiles and code mode (slice M), over the real MCP client SDK and
 * the real hooks Worker: each client is served exactly its own grant's profile (`custom`,
 * the owner's own choice, until the owner names `directory`), `code` while the owner's
 * policy has code mode on, its default; one `code` call runs a script of several tool
 * calls, and its calls stop once outside access is switched off; a revoked client is
 * refused before any tool is listed; an API key is held to its profile at `/api` as a
 * client is at `/mcp`.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { apiKeyOrigin } from "../../owner/src/ledger";
import type { Binding } from "../../owner/src/owner-do";
import { mcpOrigin } from "../src/binder";
import { profileNames } from "../src/tools";
import {
  appPost, call, connect, legacyPost, OPEN, registerClient, serveFirstPartyDocument,
  setControl, signInApp, type Tokens, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

async function mcpClient(tokens: Tokens): Promise<Client> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  const transport = new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
  });
  await client.connect(transport);
  return client;
}

async function listed(tokens: Tokens): Promise<string[]> {
  const client = await mcpClient(tokens);
  const { tools } = await client.listTools();
  await client.close();
  return tools.map((tool) => tool.name);
}

/** A signed-in owner, and what it changes in its own session. */
async function signedIn() {
  const who = owner();
  const app = await signInApp({ owner: who });
  const stub = env.OWNER.getByName(who);
  const own: Binding = { owner: who, caller: who, origin: null };
  const session: Binding = { ...own, session: app.session };
  return {
    who, app, stub, own,
    policy: async (codemode: boolean, custom?: string[]) => {
      const { generation, policy } = await stub.policyRead(own);
      const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
      await stub.policyWrite(session, { expected_generation: generation, policy: {
        ...rest, hooks: { codemode }, ...(custom === undefined ? {} : { tools: { custom } }) } });
    },
    profile: async (client: string, profile: string) => {
      const row = (await stub.grantList(own)).clients.find((c) => c.client === client)!;
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
    // Every grant here is a custom connector's: every tool, code mode on by default.
    expect(await listed(listedTokens)).toEqual(profileNames("custom"));
    expect(await listed(customTokens)).toEqual(profileNames("custom"));
    const listedOrigin = await mcpOrigin(listedClient.clientId, listedClient.redirectUri);
    await o.profile(listedOrigin.client, "directory");
    expect(await listed(listedTokens)).toEqual(profileNames("directory"));
    expect(await listed(customTokens)).toEqual(profileNames("custom"));
    // Code mode off: every custom tool but code.
    await o.policy(false);
    expect(await listed(customTokens))
      .toEqual(profileNames("custom").filter((name) => name !== "code"));
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

  it("serves search, fetch and file_read in ChatGPT's shape to a directory client", async () => {
    const o = await signedIn();
    await o.stub.fileWrite(o.own, { path: "/workspace/plumber.md", expected_etag: null,
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
    expect(found.structuredContent).toMatchObject({ next_cursor: null, results: [{
      id: "/workspace/plumber.md", title: "Bo",
      url: `https://tinyassets.io/app#data/${encodeURIComponent("/workspace")}` }] });
    // The text copy is the same JSON, as ChatGPT's connectors read either.
    expect(JSON.parse((found.content as { text: string }[])[0]!.text))
      .toEqual(found.structuredContent);
    const fetched = await client.callTool({ name: "fetch",
                                            arguments: { id: "/workspace/plumber.md" } });
    expect(fetched.structuredContent).toMatchObject({ id: "/workspace/plumber.md", title: "Bo",
                                                      metadata: { type: "Person" } });
    expect((fetched.structuredContent as { text: string }).text).toContain("Bo fixes boilers.");
    const read = await client.callTool({ name: "file_read", arguments: {
      path: "/workspace/plumber.md", offset: 4, max_bytes: 12 } });
    expect(read.structuredContent).toMatchObject({ offset: 4, text: "type: Person",
                                                   next_offset: 16 });
    await client.close();
  });

  it("runs one script of several tool calls in one code call", async () => {
    const o = await signedIn();
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
    expect(ran.structuredContent).toMatchObject({ ok: true, value: ["todo"],
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

  it("refuses a revoked client before any tool is listed", async () => {
    const o = await signedIn();
    const registered = await registerClient();
    const tokens = await connect(registered, { owner: o.who });
    expect(await listed(tokens)).toEqual(profileNames("custom"));
    await o.stub.grantRevoke(o.own, {
      client: (await mcpOrigin(registered.clientId, registered.redirectUri)).client });
    const response = await legacyPost(tokens.access_token,
                                      { jsonrpc: "2.0", id: 1, method: "tools/list" });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "outside client revoked" });
  });
});

describe("the API door's tool profiles", () => {
  it("holds a key to its grant's profile", async () => {
    const o = await signedIn();
    const minted = await appPost("keys", o.app.access_token, o.app.session, {
      name: "laptop", scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] });
    const { key, key_id } = await minted.json<{ key: string; key_id: string }>();
    const api = (tool: string, body: unknown) => call(new Request(url(`/api/tools/${tool}`), {
      method: "POST", body: JSON.stringify(body),
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" } }));
    // A key's grant names custom too: code is served.
    const ran = await api("code", { script: "return (await tools.agents_list({})).agents;" });
    expect([ran.status, await ran.json()]).toEqual([200, expect.objectContaining({
      ok: true, value: [] })]);
    await o.profile(apiKeyOrigin(key_id).client, "directory");
    expect((await api("code", { script: "return 1;" })).status).toBe(404);
    expect((await api("agents_list", {})).status).toBe(200);
    await o.profile(apiKeyOrigin(key_id).client, "custom");
    await o.policy(true, ["code"]);
    expect((await api("agents_list", {})).status).toBe(404);
  });
});
