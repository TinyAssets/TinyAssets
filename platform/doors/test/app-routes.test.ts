/**
 * The owner app's own routes on the owner surface (the app slice), each one door: the
 * account (tier, the control record's numbers, file usage), notices, policy (code mode's
 * switch and the owner's custom tools), a client's profile (`grants/set`, never a revoke),
 * an agent's files as text, the source records' public facts, the tool table's names, and
 * the owner calling a table tool as itself. Each takes only the owner's first-party token,
 * and each widening the owner's session.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it } from "vitest";

import { RUN_CAPABILITIES } from "../../owner/src/agent-file";
import { CAPABILITIES } from "../../owner/src/grants";
import { wakeKinds } from "../../owner/src/wake-kinds";
import { mcpOrigin } from "../src/binder";
import { bundledSources } from "../src/sources";
import { profileNames, toolNames } from "../src/tools";
import {
  appPost, call, connect, OPEN, registerClient, serveFirstPartyDocument, setControl,
  signInApp, TIERS, type Tokens, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

async function listed(tokens: Tokens): Promise<string[]> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  await client.connect(new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } } }));
  const { tools } = await client.listTools();
  await client.close();
  return tools.map((tool) => tool.name);
}

async function answered(response: Response, status = 200): Promise<Record<string, unknown>> {
  const body = await response.json<Record<string, unknown>>();
  expect(response.status, JSON.stringify(body)).toBe(status);
  return body;
}

describe("the owner app's account route", () => {
  it("answers the owner's tier beside every tier's numbers and its file usage", async () => {
    const app = await signInApp({ owner: owner() });
    const account = await answered(await appPost("account", app.access_token, null, {}));
    expect(account["tier"]).toBe("free");
    expect(account["tiers"]).toEqual(TIERS);
    // A new owner holds its starter files (the starter slice), as many as its seed has written.
    expect(account["usage"]).toMatchObject({ entries: expect.any(Number),
                                             chunk_bytes: expect.any(Number),
                                             inline_bytes: expect.any(Number) });
    // A closed empty body.
    expect(await answered(await appPost("account", app.access_token, null, { tier: "paid" }), 400))
      .toEqual({ error: "this route takes an empty object" });
  });

  it("says when the control record holds no tier numbers, never a default", async () => {
    const app = await signInApp({ owner: owner() });
    const { tiers: _tiers, ...untiered } = OPEN;
    await setControl(untiered);
    expect((await answered(await appPost("account", app.access_token, null, {})))["tiers"])
      .toBeNull();
  });

  it("is the owner's alone: an outside credential never reaches it", async () => {
    const who = owner();
    const tokens = await connect(await registerClient(), { owner: who });
    expect(await answered(await appPost("account", tokens.access_token, null, {}), 403))
      .toEqual({ error: "an outside credential never reaches the owner's surface" });
  });
});

describe("the owner app's settings routes", () => {
  it("turn code mode off and name the owner's custom tools, in session only", async () => {
    const app = await signInApp({ owner: owner() });
    const read = await answered(await appPost("policy", app.access_token, null, {}));
    const policy = read["policy"] as Record<string, unknown>;
    const change = { expected_generation: read["generation"],
                     policy: { ...policy, hooks: { codemode: false },
                               tools: { custom: ["agents_list"] } } };
    expect(await answered(await appPost("policy/write", app.access_token, null, change), 403))
      .toEqual({ error: "interactive owner session required" });
    const written = await answered(await appPost("policy/write", app.access_token, app.session,
                                                 change));
    expect(written["policy"]).toMatchObject({ hooks: { codemode: false },
                                              tools: { custom: ["agents_list"] } });
    expect((await answered(await appPost("policy", app.access_token, null, {})))["generation"])
      .toBe(written["generation"]);
  });

  it("name a client's profile, and take no revoke but grants/revoke's", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const client = await registerClient("Listed");
    const tokens = await connect(client, { owner: who });
    expect(await listed(tokens)).toEqual(profileNames("custom"));
    const origin = await mcpOrigin(client.clientId, client.redirectUri);
    const { clients } = await answered(await appPost("grants", app.access_token, null, {})) as
      { clients: Record<string, unknown>[] };
    const row = clients.find((c) => c["client"] === origin.client)!;
    const set = { client: row["client"], source: row["source"], mode: row["mode"],
                  scopes: row["scopes"], expected_revision: row["revision"], name: row["name"] };
    expect(await answered(await appPost("grants/set", app.access_token, app.session,
                                        { ...set, mode: "revoked", scopes: [] }), 400))
      .toEqual({ error: "a revoke goes by grants/revoke, which also ends the client's tokens" });
    expect(await answered(await appPost("grants/set", app.access_token, null,
                                        { ...set, profile: "directory" }), 403))
      .toEqual({ error: "interactive owner session required" });
    expect(await answered(await appPost("grants/set", app.access_token, app.session,
                                        { ...set, profile: "directory" })))
      .toMatchObject({ client: origin.client, profile: "directory" });
    expect(await listed(tokens)).toEqual(profileNames("directory"));
  });

  it("list the owner's notices, and the tool table's names", async () => {
    const app = await signInApp({ owner: owner() });
    expect(await answered(await appPost("notices", app.access_token, null, {})))
      .toEqual({ notices: [] });
    const { tools } = await answered(await appPost("tools", app.access_token, null, {})) as
      { tools: { name: string; title: string }[] };
    expect(tools.map((tool) => tool.name)).toEqual(toolNames());
  });
});

describe("the owner surface's answers", () => {
  it("are never sniffed or framed, refusals and connect routes alike (review L7)", async () => {
    const app = await signInApp({ owner: owner() });
    for (const response of [await appPost("account", app.access_token, null, {}),
                            await appPost("no/such/route", app.access_token, null, {}),
                            await appPost("connections/list", app.access_token, null, {}),
                            await call(new Request(url("/app/api/account"), { method: "POST" }))]) {
      expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
      expect(response.headers.get("Content-Security-Policy"))
        .toBe("default-src 'none'; frame-ancestors 'none'");
    }
  });

  it("list the capabilities in plain words, from the object's own list", async () => {
    const app = await signInApp({ owner: owner() });
    const { capabilities } = await answered(await appPost("capabilities", app.access_token,
                                                          null, {})) as
      { capabilities: { id: string; words: string }[] };
    // A run's and a key's capabilities are one list: both forms show it whole.
    expect([...RUN_CAPABILITIES]).toEqual([...CAPABILITIES]);
    expect(capabilities.map((c) => c.id)).toEqual([...CAPABILITIES]);
    expect(capabilities.every((c) => c.words.length > 0)).toBe(true);
  });
});

describe("the owner app's wake-up kinds", () => {
  it("are the records whose owner pastes the URL and token, as data", async () => {
    const app = await signInApp({ owner: owner() });
    const { kinds } = await answered(await appPost("agent/doorbell/kinds", app.access_token,
                                                   null, {})) as
      { kinds: { id: string; vendor: string; agents: string }[] };
    const pasted = wakeKinds().filter((kind) => kind.push?.callback === "owner");
    expect(pasted.length).toBeGreaterThan(0);
    expect(kinds).toEqual(pasted.map(({ id, vendor, agents }) => ({ id, vendor, agents })));
    expect(kinds.map((kind) => kind.id)).not.toContain("chatgpt_mcp_events");
  });
});

describe("the owner app's file routes", () => {
  it("write and read an agent's files as text, against their etags", async () => {
    const app = await signInApp({ owner: owner() });
    const { access_token: token, session } = app;
    // An agent of the owner's own making: main is the starter's, seeded at sign-up.
    for (const path of ["/workspace/agents", "/workspace/agents/helper"]) {
      await answered(await appPost("tools/file_mkdir", token, session,
                                   { path, expected_etag: null }));
    }
    const path = "/workspace/agents/helper/AGENTS.md";
    const text = "# main\n\nBe brief. Ünïcode stays.\n";
    const written = await answered(await appPost("files/write", token, session,
                                                 { path, expected_etag: "", text }));
    const entry = written["entry"] as { etag: string; size: number };
    expect(entry.size).toBe(new TextEncoder().encode(text).byteLength);
    expect(await answered(await appPost("files/read", token, null, { path })))
      .toMatchObject({ entry: { path, etag: entry.etag }, text });
    // A create where a file is, or a stale etag, is the object's conflict, never an
    // overwrite.
    for (const expected of ["", "0".repeat(64)]) {
      const stale = await answered(await appPost("files/write", token, session,
                                                 { path, expected_etag: expected, text: "x" }));
      expect(stale).not.toHaveProperty("entry");
    }
    const changed = await answered(await appPost("files/write", token, session,
                                                 { path, expected_etag: entry.etag, text: "y" }));
    expect(changed).toHaveProperty("entry");
    await answered(await appPost("files/write", token, session,
                                 { path, expected_etag: (changed["entry"] as { etag: string }).etag,
                                   text }));
    expect((await answered(await appPost("files/read", token, null, { path })))["text"])
      .toBe(text);
  });

  it("take no change to an agent's definition from the token alone (review M1)", async () => {
    const app = await signInApp({ owner: owner() });
    const { access_token: token, session } = app;
    await answered(await appPost("tools/file_mkdir", token, session,
                                 { path: "/workspace/agents/main", expected_etag: null }));
    const path = "/workspace/agents/main/agent.json";
    const made = await answered(await appPost("files/write", token, session, {
      path, expected_etag: "", text: JSON.stringify({ engine: "auto", scopes: ["read_files"] }) }));
    const etag = (made["entry"] as { etag: string }).etag;
    // A leaked bearer widens no scope, plants no instruction and removes nothing there.
    const SESSION = { error: "interactive owner session required" };
    expect(await answered(await appPost("files/write", token, null, { path, expected_etag: etag,
      text: JSON.stringify({ engine: "auto", scopes: ["read_files", "run_graph", "converse"] }) }),
                          403)).toEqual(SESSION);
    expect(await answered(await appPost("files/write", token, null, {
      path: "/workspace/agents/main/AGENTS.md", expected_etag: "", text: "planted" }), 403))
      .toEqual(SESSION);
    expect(await answered(await appPost("tools/file_remove", token, null,
      { path, expected_etag: null, recursive: false }), 403)).toEqual(SESSION);
    expect(await answered(await appPost("tools/file_rename", token, null,
      { from: path, to: "/workspace/x.json", expected_etag: null, replace: false }), 403))
      .toEqual(SESSION);
    // A cookie that is not the owner's live session is no session either.
    expect(await answered(await appPost("tools/file_remove", token, "x".repeat(43),
      { path, expected_etag: null, recursive: false }), 403)).toEqual(SESSION);
    expect((await answered(await appPost("files/read", token, null, { path })))["entry"])
      .toMatchObject({ etag });
    // In its session, the owner's app changes it.
    expect(await answered(await appPost("tools/file_remove", token, session,
      { path, expected_etag: etag, recursive: false }))).toEqual({ removed: 1 });
  });

  it("show a file that is not UTF-8 as not text, and take only closed bodies", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const { env } = await import("cloudflare:workers");
    const stub = (env as unknown as { OWNER: DurableObjectNamespace<
      import("../../owner/src/owner-do").OwnerDO> }).OWNER.getByName(who);
    await stub.fileWrite({ owner: who, caller: who, origin: null, tiers: TIERS },
                         { path: "/workspace/blob.bin", expected_etag: null,
                           content: new Uint8Array([0xff, 0xfe, 0x00]) });
    expect(await answered(await appPost("files/read", app.access_token, null,
                                        { path: "/workspace/blob.bin" })))
      .toMatchObject({ text: null, why: "not UTF-8 text" });
    expect((await appPost("files/read", app.access_token, null, {})).status).toBe(400);
    expect((await appPost("files/write", app.access_token, null,
                          { path: "/workspace/x", expected_etag: null })).status).toBe(400);
    expect((await appPost("files/write", app.access_token, null,
                          { path: "/workspace/x", expected_etag: null, text: 1 })).status)
      .toBe(400);
  });
});

describe("the owner app's sources and tools routes", () => {
  it("list each record's public facts and nothing sealed or run", async () => {
    const app = await signInApp({ owner: owner() });
    const { sources } = await answered(await appPost("sources", app.access_token, null, {})) as
      { sources: Record<string, unknown>[] };
    const bundled = bundledSources();
    // The test entry serves the bundled records and one test source beside them.
    expect(sources.map((s) => s["id"])).toEqual(expect.arrayContaining([...bundled.ids()]));
    for (const shown of sources) {
      expect(Object.keys(shown).sort()).toEqual(["connect", "credentials", "display_name",
                                                 "engines", "help_url", "id", "signin",
                                                 "suggested", "unwired"]);
    }
    const claude = sources.find((s) => s["id"] === "claude-code")!;
    expect(claude["credentials"]).toEqual(["vendor_login_in_sandbox", "api_key"]);
    expect(JSON.stringify(sources)).not.toMatch(/authorization_endpoint|argv|client_id/);
  });

  it("call a table tool as the owner itself, and no name the table lacks", async () => {
    const app = await signInApp({ owner: owner() });
    expect(await answered(await appPost("tools/agents_list", app.access_token, null, {})))
      .toEqual({ agents: [], owner_agents: [] });
    expect(await answered(await appPost("tools/inbox_digest", app.access_token, null,
                                        { limit: 10, after: 0 })))
      .toMatchObject({ mail: { messages: [] }, rooms: { rows: [] } });
    expect(await answered(await appPost("tools/no_such_tool", app.access_token, null, {}), 404))
      .toEqual({ error: "no such tool" });
    // The object checks every field, as for an outside client.
    expect((await appPost("tools/file_list", app.access_token, null, { dir: 3 })).status)
      .toBe(400);
  });

  it("never reach a tool with an outside credential", async () => {
    const who = owner();
    const tokens = await connect(await registerClient(), { owner: who });
    expect((await appPost("tools/agents_list", tokens.access_token, null, {})).status).toBe(403);
    expect((await appPost("sources", tokens.access_token, null, {})).status).toBe(403);
  });
});
