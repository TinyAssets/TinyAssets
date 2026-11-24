/**
 * The owner app's own routes on the owner surface, each one door: the
 * account (tier, the control record's numbers, file usage), notices, policy (code mode's
 * switch and the owner's custom tools), a client's profile (`grants/set`, never a revoke,
 * and a disconnected app's revoke lifted), an agent's files as text and their kept versions
 * removed, a main agent turned off or deleted, a mail chain, the source records' public facts,
 * the tool table's names, and the owner calling a table tool as itself. Each takes only the
 * owner's first-party token, and each widening the owner's session.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it } from "vitest";

import { until } from "../../ci/workerd";
import { RUN_CAPABILITIES } from "../../contract/src/capabilities";
import { waitingPage } from "../../contract/src/identity";
import { NOT_AN_AGENT } from "../../contract/src/refusal";
import { wakeKinds } from "../../contract/src/wake-records";
import { mcpOrigin } from "../src/binder";
import { bundledSources } from "../../sources/src/catalog";
import { profileNames, toolNames } from "../../contract/src/doors";
import {
  answerOf, appPost, call, connect, doorsEnv, legacyTool, OPEN, OPEN_RECORD, ownDoor,
  registerClient, serveFirstPartyDocument, setControl, signInApp, TIERS, type Tokens, url,
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

describe("a new owner's starter seed, through the owner Worker's own bindings", () => {
  it("lands main and the starter's files, with no failure told: a missing or broken files " +
     "binding fails here", async () => {
    const who = owner();
    await signInApp({ owner: who });
    // The seed is the owner's own lane's work once the sign-in admits it, on its own alarm.
    await until(async () => (await ownDoor(who, "starterReceipts"))
      .version > 0, "the starter's seed");
    const { notices } = await ownDoor(who, "noticeList");
    expect(notices.filter((notice) => notice.type === "starter_failed")
      .map((notice) => notice.detail)).toEqual([]);
    const { owner_agents: agents } = await ownDoor(who, "outsideAgents", {});
    expect(agents.map((agent) => agent.agent)).toContain("main");
    for (const path of ["/workspace/USER.md", "/workspace/index.md",
                        "/workspace/agents/main/agent.json"]) {
      expect((await ownDoor(who, "fileStat", { path })).entry, path)
        .not.toBeNull();
    }
  });
});

describe("the owner app's account route", () => {
  it("answers the owner's tier beside every tier's numbers and its file usage", async () => {
    const app = await signInApp({ owner: owner() });
    const account = await answered(await appPost("account", app.access_token, null, {}));
    expect(account["tier"]).toBe("free");
    expect(account["tiers"]).toEqual(TIERS);
    // A new owner holds its starter files, as many as its seed has written.
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
                     policy: { ...policy, codemode: { on: false },
                               tools: { custom: ["agents_list"] } } };
    expect(await answered(await appPost("policy/write", app.access_token, null, change), 403))
      .toEqual({ error: "interactive owner session required" });
    const written = await answered(await appPost("policy/write", app.access_token, app.session,
                                                 change));
    expect(written["policy"]).toMatchObject({ codemode: { on: false },
                                              tools: { custom: ["agents_list"] } });
    expect((await answered(await appPost("policy", app.access_token, null, {})))["generation"])
      .toBe(written["generation"]);
  });

  it("name a client's profile, and take no revoke but grants/revoke's", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const client = await registerClient("Listed");
    const tokens = await connect(client, { owner: who });
    // Custom, code withheld: code mode is off by default.
    expect(await listed(tokens)).toEqual(profileNames("custom").filter((n) => n !== "code"));
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

  it("let a disconnected app connect again only once the owner sets its grant", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const client = await registerClient("Back");
    await listed(await connect(client, { owner: who }));
    const origin = await mcpOrigin(client.clientId, client.redirectUri);
    await answered(await appPost("grants/revoke", app.access_token, null,
                                 { client: origin.client }));
    // Its client id is the same on every connect, so a new sign-in alone stays refused.
    const signedIn = await connect(client, { owner: who });
    await expect(legacyTool(signedIn.access_token, "agents_list", {}))
      .rejects.toThrow('/mcp answered 403: {"error":"outside client revoked"}');
    const { clients } = await answered(await appPost("grants", app.access_token, null, {})) as
      { clients: Record<string, unknown>[] };
    const row = clients.find((c) => c["client"] === origin.client)!;
    expect(row["mode"]).toBe("revoked");
    expect(await answered(await appPost("grants/set", app.access_token, app.session, {
      client: origin.client, source: "mcp", mode: "full", scopes: [],
      expected_revision: row["revision"], name: row["name"], profile: row["profile"],
      agent: row["agent"] }))).toMatchObject({ client: origin.client, mode: "full" });
    // Its revoke is lifted, and its fence stands: only a sign-in made after the revoke
    // connects it (`grants.test.ts`), never this one from before.
    await expect(legacyTool(signedIn.access_token, "agents_list", {})).rejects.toThrow(
      '/mcp answered 403: {"error":"a fresh sign-in is required after a revoke"}');
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
  it("are never sniffed or framed, refusals and connect routes alike", async () => {
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
    // The agent form shows a run's capabilities whole.
    expect(capabilities.map((c) => c.id)).toEqual([...RUN_CAPABILITIES]);
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

  it("keep every version until the owner removes an older one's content or every older one",
     async () => {
    const { access_token: token, session } = await signInApp({ owner: owner() });
    const path = "/workspace/notes.md";
    const first = await answered(await appPost("files/write", token, session,
                                               { path, expected_etag: "", text: "key=secret" }));
    await answered(await appPost("files/write", token, session, {
      path, expected_etag: (first["entry"] as { etag: string }).etag, text: "key=<rotated>" }));
    const kept = async () => (await answered(await appPost("tools/file_history", token, null,
      { path, limit: 10 })))["changes"] as { version: number; content_id: string | null }[];
    const [current, older] = await kept();
    expect(await answered(await appPost("files/redact", token, null,
                                        { path, version: current!.version }), 403))
      .toEqual({ error: "the current version is never redacted; write a new one first" });
    expect(await answered(await appPost("files/redact", token, null,
                                        { path, version: older!.version })))
      .toEqual({ redacted: older!.version });
    expect((await kept()).map((c) => c.content_id === null)).toEqual([false, true]);
    expect(await answered(await appPost("files/purge", token, null, { path })))
      .toEqual({ purged: 1 });
    expect((await kept()).map((c) => c.version)).toEqual([current!.version]);
    expect((await answered(await appPost("files/read", token, null, { path })))["text"])
      .toBe("key=<rotated>");
  });

  it("take no change to an agent's definition from the token alone", async () => {
    const app = await signInApp({ owner: owner() });
    const { access_token: token, session } = app;
    await answered(await appPost("tools/file_mkdir", token, session,
                                 { path: "/workspace/agents/helper", expected_etag: null }));
    const path = "/workspace/agents/helper/agent.json";
    const made = await answered(await appPost("files/write", token, session, {
      path, expected_etag: "", text: JSON.stringify({ engine: "auto", scopes: ["read_files"] }) }));
    const etag = (made["entry"] as { etag: string }).etag;
    // A leaked bearer widens no scope, plants no instruction and removes nothing there.
    const SESSION = { error: "interactive owner session required" };
    expect(await answered(await appPost("files/write", token, null, { path, expected_etag: etag,
      text: JSON.stringify({ engine: "auto", scopes: ["read_files", "run_graph", "converse"] }) }),
                          403)).toEqual(SESSION);
    expect(await answered(await appPost("files/write", token, null, {
      path: "/workspace/agents/helper/AGENTS.md", expected_etag: "", text: "planted" }), 403))
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
    await doorsEnv.OWNER.getByName(who).fileWrite(
      { owner: who, caller: who, origin: null, control: OPEN_RECORD },
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
                                                 "suggested"]);
    }
    const claude = sources.find((s) => s["id"] === "claude-code")!;
    expect(claude["credentials"]).toEqual(["vendor_login_in_sandbox", "api_key"]);
    expect(JSON.stringify(sources)).not.toMatch(/authorization_endpoint|argv|client_id/);
  });

  it("call a table tool as the owner itself, and no name the table lacks", async () => {
    const app = await signInApp({ owner: owner() });
    // The list waits for the new owner's seed, so the starter's main is there.
    const listed = await answered(await appPost("tools/agents_list", app.access_token, null, {}));
    expect(listed).toEqual({ agents: [], owner_agents: expect.any(Array), apps: expect.any(Array) });
    expect((listed["owner_agents"] as { agent: string }[]).map((a) => a.agent)).toEqual(["main"]);
    expect(await answered(await appPost("tools/inbox_digest", app.access_token, null,
                                        { limit: 10 })))
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

describe("an app's agent registration, answered in the owner app", () => {
  /** A card as the client files it and as the owner is shown it. */
  type Filed = { card_id: string; digest: string; approve_url: string };
  type Shown = { card_id: string; digest: string; request: Record<string, unknown> };

  /** `tokens`' client files its agent's card, naming no place. */
  async function file(tokens: Tokens, display_name = "Muse"): Promise<Filed> {
    return answerOf(await legacyTool(tokens.access_token, "agent_register", {
      display_name, delivery_method: "pull", recipe_id: null })) as unknown as Filed;
  }

  /** The pending cards, as the app shows them. */
  async function cards(token: string): Promise<Shown[]> {
    return (await answered(await appPost("registrations", token, null, {})))["cards"] as Shown[];
  }

  /** Whether the client's mailbox answers: its digest and its read, each refused or not. */
  async function mailbox(tokens: Tokens): Promise<(string | null)[]> {
    return Promise.all([["inbox_digest", { limit: 5 }], ["mailbox_read", { limit: 5 }]]
      .map(async ([name, args]) => {
        const result = await legacyTool(tokens.access_token, name as string, args);
        return result.isError === true ? result.content[0]!.text.split("\n")[0]! : null;
      }));
  }

  it("is filed with its page, which the owner is told of, and once applied there in session " +
     "the connection has its mailbox", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    expect(await mailbox(tokens)).toEqual([NOT_AN_AGENT, NOT_AN_AGENT]);
    const filed = await file(tokens);
    expect(filed.approve_url).toBe(waitingPage(filed.card_id));
    const { notices } = await answered(await appPost("notices", token, null, {})) as
      { notices: { type: string; detail: string }[] };
    expect(notices.filter((n) => n.type === "agent_registration")
      .map((n) => n.detail.endsWith(` ${filed.approve_url}`))).toEqual([true]);
    // A full grant's agent registers in main: its client names no place.
    expect(await cards(token)).toMatchObject([{ card_id: filed.card_id, digest: filed.digest,
      request: { command_center_id: "main", display_name: "Muse", delivery_method: "pull",
                 recipe_id: null } }]);
    const answer = { card_id: filed.card_id, digest: filed.digest };
    expect(await answered(await appPost("registrations/apply", token, null, answer), 403))
      .toEqual({ error: "interactive owner session required" });
    expect(await answered(await appPost("registrations/apply", token, session, answer)))
      .toMatchObject({ display_name: "Muse", command_center_id: "main", disabled_reason: null });
    expect([await cards(token), await mailbox(tokens)]).toEqual([[], [null, null]]);
  });

  it("is declined there, and the connection stays without a mailbox", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    const { card_id, digest } = await file(tokens);
    expect(await answered(await appPost("registrations/decline", token, null,
                                        { card_id, digest })))
      .toEqual({ card_id, declined: true });
    expect(await cards(token)).toEqual([]);
    expect(await answered(await appPost("registrations/apply", token, session,
                                        { card_id, digest }), 403))
      .toEqual({ error: "registration is not the pending card shown" });
    expect(await mailbox(tokens)).toEqual([NOT_AN_AGENT, NOT_AN_AGENT]);
  });

  it("refuses a page whose card no longer waits, or never did", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    const first = await file(tokens);
    const again = await file(tokens, "Muse Two");  // filing again replaces the card
    expect((await cards(token)).map((card) => card.card_id)).toEqual([again.card_id]);
    for (const [path, stale] of [
      ["registrations/apply", { card_id: first.card_id, digest: first.digest }],
      ["registrations/decline", { card_id: first.card_id, digest: first.digest }],
      ["registrations/apply", { card_id: again.card_id, digest: first.digest }],
      ["registrations/apply", { card_id: "card_" + "0".repeat(32), digest: again.digest }],
    ] as const) {
      expect(await answered(await appPost(path, token, session, stale), 403), path)
        .toEqual({ error: "registration is not the pending card shown" });
    }
    expect(await answered(await appPost("registrations/apply", token, session,
                                        { card_id: "nope" }), 400))
      .toEqual({ error: "invalid ledger request" });
    expect(await mailbox(tokens)).toEqual([NOT_AN_AGENT, NOT_AN_AGENT]);
  });

  it("lands a narrowed grant's agent in the space its scopes name, where it then reads its " +
     "mail", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const client = await registerClient("Muse");
    const tokens = await connect(client, { owner: who });
    const origin = await mcpOrigin(client.clientId, client.redirectUri);
    // Its first call records its grant, full, for the owner to narrow.
    expect(await mailbox(tokens)).toEqual([NOT_AN_AGENT, NOT_AN_AGENT]);
    const { clients } = await answered(await appPost("grants", token, null, {})) as
      { clients: Record<string, unknown>[] };
    const row = clients.find((c) => c["client"] === origin.client)!;
    await answered(await appPost("grants/set", token, session, {
      client: origin.client, source: "mcp", mode: "scoped", expected_revision: row["revision"],
      name: "Muse", scopes: ["read_graph", "write_graph"].map((capability) =>
        ({ universe: "work", agent: "main", capability })) }));
    const filed = await file(tokens);
    expect((await cards(token))[0]!.request["command_center_id"]).toBe("work");
    expect(await answered(await appPost("registrations/apply", token, session,
                                        { card_id: filed.card_id, digest: filed.digest })))
      .toMatchObject({ command_center_id: "work" });
    expect(await mailbox(tokens)).toEqual([null, null]);
  });

  it("is turned off and deleted there in session, and the owner reads its mail's chain",
     async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    const apply = async () => {
      const { card_id, digest } = await file(tokens);
      return answered(await appPost("registrations/apply", token, session, { card_id, digest }));
    };
    const { agent_id } = await apply() as { agent_id: string };
    const off = { agent_id, reason: "turned off by the owner" };
    expect(await answered(await appPost("agent/disable", token, null, off), 403))
      .toEqual({ error: "interactive owner session required" });
    expect(await answered(await appPost("agent/disable", token, session, off)))
      .toMatchObject({ agent_id, disabled_reason: "turned off by the owner" });
    expect(await mailbox(tokens)).toEqual([NOT_AN_AGENT, NOT_AN_AGENT]);
    // Its app asks to join again, and once applied it is back.
    await apply();
    expect(await mailbox(tokens)).toEqual([null, null]);
    // The owner's mail to it, read back whole from the message.
    const { agents } = await answered(await appPost("tools/agents_list", token, null, {})) as
      { agents: { agent_id: string; room_id: string }[] };
    const posted = await answered(await appPost("tools/room_post", token, null, {
      room_id: agents[0]!.room_id, client_message_id: "c1", body: "good morning",
      parent_id: null }));
    const read = { message_id: posted["message_id"], limit: 10 };
    expect(await answered(await appPost("mail/chain", token, null, read))).toMatchObject({
      messages: [{ message_id: posted["message_id"], body: "good morning" }], more: false });
    const gone = { agent_id };
    expect(await answered(await appPost("agent/delete", token, null, gone), 403))
      .toEqual({ error: "interactive owner session required" });
    expect(await answered(await appPost("agent/delete", token, session, gone)))
      .toEqual({ agent_id, messages: 1 });
    expect(await answered(await appPost("mail/chain", token, null, read), 403))
      .toEqual({ error: "no such message held" });
    expect(await answered(await appPost("tools/agents_list", token, null, {})))
      .toMatchObject({ agents: [] });
    expect(await mailbox(tokens)).toEqual([NOT_AN_AGENT, NOT_AN_AGENT]);
  });
});

describe("an agent's held call, answered in the owner app", () => {
  /** A card as the owner is shown it. */
  type Shown = { card_id: string; digest: string; act: string; door: string };

  /** The owner's rule, written in session: every call of `workspace.files` asks. */
  async function asking(token: string, session: string): Promise<void> {
    const read = await answered(await appPost("policy", token, null, {}));
    await answered(await appPost("policy/write", token, session, {
      expected_generation: read["generation"],
      policy: { ...read["policy"] as object, actions: [{ act: "workspace.files", do: "ask" }] } }));
  }

  /** `tokens`' client makes a directory: null once it is made, else its refusal's first line. */
  async function mkdir(tokens: Tokens, path: string): Promise<string | null> {
    const result = await legacyTool(tokens.access_token, "file_mkdir",
                                    { path, expected_etag: null });
    return result.isError === true ? result.content[0]!.text.split("\n")[0]! : null;
  }

  /** The card a refusal names. */
  function cardOf(refused: string | null): string {
    return /card (act_[0-9a-f]{32})/.exec(refused ?? "")![1]!;
  }

  /** The pending held calls, as the app shows them. */
  async function cards(token: string): Promise<Shown[]> {
    return (await answered(await appPost("actions", token, null, {})))["cards"] as Shown[];
  }

  it("names its page to the agent and the owner, and once allowed there in session the " +
     "call goes through", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    await asking(token, session);
    const refused = await mkdir(tokens, "/workspace/asked");
    const card = cardOf(refused);
    expect(refused).toBe(`held for your owner's answer: card ${card}. Your owner answers it ` +
      `at ${waitingPage(card)}: give them that link. Once they say yes, make this same call ` +
      "again");
    const { notices } = await answered(await appPost("notices", token, null, {})) as
      { notices: { type: string; detail: string }[] };
    expect(notices.filter((n) => n.type === "action_request")
      .map((n) => n.detail.endsWith(` ${waitingPage(card)}`))).toEqual([true]);
    const [shown, ...more] = await cards(token);
    expect([shown, more]).toMatchObject([{ card_id: card, act: "workspace.files",
                                           door: "fileMkdir" }, []]);
    const answer = { card_id: card, digest: shown!.digest };
    expect(await answered(await appPost("actions/approve", token, null, answer), 403))
      .toEqual({ error: "interactive owner session required" });
    expect(await answered(await appPost("actions/approve", token, session, answer)))
      .toEqual({ card_id: card, approved: true, run: null });
    expect(await cards(token)).toEqual([]);
    expect(await mkdir(tokens, "/workspace/asked")).toBeNull();
  });

  it("is denied there with no session, only as the card shown, and the call stays held",
     async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    await asking(token, session);
    const card = cardOf(await mkdir(tokens, "/workspace/denied"));
    const [{ digest }] = await cards(token) as [Shown];
    expect(await answered(await appPost("actions/refuse", token, null,
                                        { card_id: card, digest: "0".repeat(64) }), 403))
      .toEqual({ error: "action request is not the pending card shown" });
    expect(await answered(await appPost("actions/refuse", token, null, { card_id: card, digest })))
      .toEqual({ card_id: card, approved: false });
    expect(await cards(token)).toEqual([]);
    expect(await answered(await appPost("actions/approve", token, session,
                                        { card_id: card, digest }), 403))
      .toEqual({ error: "action request is not the pending card shown" });
    expect(cardOf(await mkdir(tokens, "/workspace/denied"))).not.toBe(card);
  });
});

describe("an agent's policy proposal, answered in the owner app", () => {
  /** A card as the owner is shown it. */
  type Shown = { card_id: string; digest: string; client: string | null; change: unknown };

  /** The pending proposals, as the app shows them. */
  async function cards(token: string): Promise<Shown[]> {
    return (await answered(await appPost("policy/proposals", token, null, {})))["cards"] as
      Shown[];
  }

  /** `tokens`' client proposes `change`: the answer it is given. */
  async function propose(tokens: Tokens, change: unknown): Promise<Record<string, unknown>> {
    return answerOf(await legacyTool(tokens.access_token, "policy_propose", { change }));
  }

  it("names its page to the agent and the owner, and once applied there in session the " +
     "owner's settings have its change", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    const read = await answered(await appPost("policy", token, null, {}));
    const filed = await propose(tokens, { set: { codemode: { on: true } } });
    const card = filed["card_id"] as string;
    expect(Object.keys(filed).sort()).toEqual(["approve_url", "card_id", "request_digest"]);
    expect(filed["approve_url"]).toBe(waitingPage(card));
    const { notices } = await answered(await appPost("notices", token, null, {})) as
      { notices: { type: string; detail: string }[] };
    expect(notices.filter((n) => n.type === "policy_proposal")
      .map((n) => n.detail.endsWith(` ${waitingPage(card)}`))).toEqual([true]);
    const [shown, ...more] = await cards(token);
    expect([shown, more]).toMatchObject([{ card_id: card, client: expect.any(String) }, []]);
    const answer = { card_id: card, digest: shown!.digest };
    expect(await answered(await appPost("policy/apply", token, null, answer), 403))
      .toEqual({ error: "interactive owner session required" });
    expect(await answered(await appPost("policy/apply", token, session, answer)))
      .toMatchObject({ generation: (read["generation"] as number) + 1, codemode: { on: true } });
    expect(await cards(token)).toEqual([]);
  });

  it("is declined there with no session, only as the card shown", async () => {
    const who = owner();
    const { access_token: token, session } = await signInApp({ owner: who });
    const tokens = await connect(await registerClient("Muse"), { owner: who });
    const card = (await propose(tokens, { add: { egress: { destinations: [
      { host: "github.com", port: 443 }] } } }))["card_id"] as string;
    const [{ digest }] = await cards(token) as [Shown];
    expect(await answered(await appPost("policy/decline", token, null,
                                        { card_id: card, digest: "0".repeat(64) }), 403))
      .toEqual({ error: "proposal is not the pending card shown" });
    expect(await answered(await appPost("policy/decline", token, null, { card_id: card, digest })))
      .toEqual({ card_id: card, declined: true });
    expect(await cards(token)).toEqual([]);
    expect(await answered(await appPost("policy/apply", token, session,
                                        { card_id: card, digest }), 403))
      .toEqual({ error: "proposal is not the pending card shown" });
  });
});
