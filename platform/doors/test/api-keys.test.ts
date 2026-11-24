/**
 * `/api` keys (research §5; checklist 13, 14, 15): the key's format and hash, mint
 * in session as grant-then-entry, use bound to the key's origin and mint time, a wrong
 * key never reaching an object, and the revoke and delete orders.
 */
import { NOT_AN_AGENT, Refusal, refusalOf } from "../../contract/src/refusal";
import { BUSY, slotBusy } from "../../contract/src/slots";

import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { apiKeyOrigin, type Binding } from "../../contract/src/binding";
import { newKey, parseKey } from "../src/api-keys";
import { sha256Hex } from "../../contract/src/digest";
import { now } from "../src/config";
import type { DoorsEnv } from "../src/env";
import { DEFAULT_PROFILE, profileNames } from "../../contract/src/doors";
import { ORIGIN } from "../../contract/src/identity";
import {
  appPost, call, connect, doorsEnv, grantsOf, legacyPost, OPEN, ownDoor, reached, refuseOnce,
  registerClient, serveFirstPartyDocument, setControl, signInApp, TIERS, url,
} from "./support";

const SCOPES = [{ universe: "cc", agent: "main", capability: "read_graph" }];

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

function api(key: string, tool: string, body: unknown, environment?: DoorsEnv):
    Promise<Response> {
  return call(new Request(url(`/api/tools/${tool}`), {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }), environment);
}

interface Owner {
  readonly owner: string;
  readonly token: string;
  readonly session: string;
}

async function signedIn(): Promise<Owner> {
  const who = owner();
  const app = await signInApp({ owner: who });
  return { owner: who, token: app.access_token, session: app.session };
}

async function mint(o: Owner, environment?: DoorsEnv):
    Promise<{ key: string; key_id: string; created_at: number }> {
  const response = environment === undefined
    ? await appPost("keys", o.token, o.session, { name: "laptop", scopes: SCOPES })
    : await call(new Request(url("/app/api/keys"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}`,
                                 Cookie: `__Host-ta-owner=${o.session}` },
      body: JSON.stringify({ name: "laptop", scopes: SCOPES }) }), environment);
  if (response.status !== 200) {
    throw new Error(`mint: ${response.status} ${await response.text()}`);
  }
  return await response.json();
}

/** The directory with one operation made to fail, or observed. */
function directoryWith(overrides: Partial<KVNamespace>): DoorsEnv {
  const real = env.DIRECTORY;
  const wrapped = {
    get: real.get.bind(real), put: real.put.bind(real), delete: real.delete.bind(real),
    list: real.list.bind(real), getWithMetadata: real.getWithMetadata.bind(real),
    ...overrides,
  } as unknown as KVNamespace;
  return { ...doorsEnv, DIRECTORY: wrapped };
}

/** The key's grant, as the owner's `grantList` door answers it, or none. */
async function grantRow(o: string, keyId: string) {
  return (await grantsOf(o)).filter((row) => row.client === apiKeyOrigin(keyId).client)
    .map(({ source, mode, revoked_at }) => ({ source, mode, revoked_at }));
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

/**
 * A refusal thrown in the owner's object crosses its RPC (workerd's error serialization:
 * the name, the message and the own fields, never the class) and is answered by its code.
 */
describe("a refusal across the object's RPC", () => {
  const FACTS = Object.freeze({ used_bytes: 10, needed_bytes: 2, held_bytes: 0,
                                limit_bytes: 10, free: ["delete old mail"] });

  it("reaches the caller with its code, its Retry-After and its facts", async () => {
    const o = await signedIn();
    await refuseOnce("outsideAgents", o.owner, new Refusal("storage_busy",
                                                           "storage_busy: catching up",
                                                           { retryAfterS: 7, facts: FACTS }));
    const error = await ownDoor(o.owner, "outsideAgents", {})
      .then(() => null, (caught: unknown) => caught);
    expect(error).not.toBeInstanceOf(Refusal);  // the class never crosses
    const refusal = refusalOf(error)!;
    expect([refusal.code, refusal.message, refusal.retryAfterS, refusal.facts])
      .toEqual(["storage_busy", "storage_busy: catching up", 7, FACTS]);
    expect([refusal.status, refusal.retryAfter]).toEqual([503, 7]);
  });

  // Its code's wait, or its own, as it crossed (the codes' statuses are the contract's table).
  it.each([
    ["storage_busy", "storage_busy: catching up", {}, 503, "30"],
    ["storage_busy", "storage_busy: the archive moved this mail", { retryAfterS: 1 }, 503, "1"],
  ] as const)("answers %s on the API as its code says", async (code, message, more, status,
                                                              retry) => {
    const o = await signedIn();
    const { key } = await mint(o);
    await refuseOnce("outsideAgents", o.owner, new Refusal(code, message, more));
    const refused = await api(key, "agents_list", {});
    expect([refused.status, refused.headers.get("Retry-After"), await refused.json()])
      .toEqual([status, retry, { error: message }]);
  });

  // The doors' one answer on every surface that answers as JSON: a busy
  // refusal's 503 and its Retry-After on the app's tools, MCP's profile and an upload's
  // bytes, as on the API above.
  it("answers busy 503 with Retry-After on the app's tools", async () => {
    const o = await signedIn();
    await refuseOnce("outsideAgents", o.owner, slotBusy());
    const busy = await appPost("tools/agents_list", o.token, o.session, {});
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { error: BUSY }]);
  });

  it("answers busy 503 with Retry-After on MCP's profile", async () => {
    const who = owner();
    const tokens = await connect(await registerClient(), { owner: who });
    await refuseOnce("toolProfile", who, slotBusy());
    const busy = await legacyPost(tokens.access_token, { jsonrpc: "2.0", id: 1,
                                                         method: "tools/list" });
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { error: BUSY }]);
  });

  it("answers a refusal's Retry-After on an upload's bytes", async () => {
    const o = await signedIn();
    await refuseOnce("fileUploadTarget", o.owner, slotBusy());
    const busy = await call(new Request(url(`/app/api/files/upload/${"a".repeat(32)}/0`), {
      method: "PUT", body: new Uint8Array(1),
      headers: { "Content-Type": "application/octet-stream", Origin: ORIGIN,
                 "Sec-Fetch-Site": "same-origin", Authorization: `Bearer ${o.token}`,
                 Cookie: `__Host-ta-owner=${o.session}` } }));
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { error: BUSY }]);
  });

  it("is a bug, loudly, when its code is no code of the list", async () => {
    const o = await signedIn();
    const forged = new Refusal("refused", "from another version");
    (forged as unknown as { code: string }).code = "not_a_code";
    await refuseOnce("outsideAgents", o.owner, forged);
    const error = await ownDoor(o.owner, "outsideAgents", {})
      .then(() => null, (caught: unknown) => caught);
    expect(refusalOf(error)).toBeNull();
  });
});

describe("the key format", () => {
  it("answers a call the owner's slots refuse busy 503 with Retry-After, and the next lands",
     async () => {
    const o = await signedIn();
    const { key } = await mint(o);
    await refuseOnce("outsideAgents", o.owner, slotBusy());
    const busy = await api(key, "agents_list", {});
    expect([busy.status, busy.headers.get("Retry-After")]).toEqual([503, "1"]);
    expect(await busy.json()).toEqual({ error: BUSY });
    expect([slotBusy().code, slotBusy().status, slotBusy().retryAfter, slotBusy().message])
      .toEqual(["busy", 503, 1, BUSY]);
    expect((await api(key, "agents_list", {})).status).toBe(200);
  });

  it("is ta_key_<16 hex>_<43 urlsafe>", () => {
    for (let i = 0; i < 20; i++) {
      const { key, keyId, secret } = newKey();
      expect(key).toMatch(/^ta_key_[0-9a-f]{16}_[A-Za-z0-9_-]{43}$/);
      expect(parseKey(key)).toEqual({ keyId, secret });
    }
    for (const bad of [`ta_key_${"0".repeat(15)}_${"a".repeat(43)}`,
                       `ta_key_${"0".repeat(16)}_${"a".repeat(42)}`,
                       `ta_key_${"G".repeat(16)}_${"a".repeat(43)}`,
                       `ta_key_${"0".repeat(16)}_${"a".repeat(42)}=`,
                       ` ta_key_${"0".repeat(16)}_${"a".repeat(43)}`]) {
      expect(parseKey(bad)).toBeNull();
    }
  });

  it("takes a key whose entry holds its secret's SHA-256 hex, whoever wrote the entry",
     async () => {
    const o = await signedIn();
    const { key_id } = await mint(o);
    // An entry the doors did not mint: its digest is the SHA-256 hex of the secret's UTF-8.
    const imported = `ta_key_${key_id}_${"Z".repeat(43)}`;
    await env.DIRECTORY.put(`k:${key_id}`, JSON.stringify({
      o: o.owner, h: "4587a2535bc507ba17c302e7fd1e46a8028d13c6528ed84d89b9e2794072d632",
      c: now() - 86_400 }));
    const response = await api(imported, "agents_list", {});
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      agents: [expect.objectContaining({ source_ref: key_id })], owner_agents: [],
      apps: expect.any(Array) });
  });
});

describe("a mint", () => {
  it("needs the owner's session", async () => {
    const o = await signedIn();
    const response = await appPost("keys", o.token, null, { name: "laptop", scopes: SCOPES });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "interactive owner session required" });
    expect(await grantsOf(o.owner)).toEqual([]);
  });

  it("writes the grant, then the directory entry, then returns the secret once", async () => {
    const o = await signedIn();
    const { key, key_id, created_at } = await mint(o);
    expect(await grantRow(o.owner, key_id)).toEqual([
      { source: "api_key", mode: "scoped", revoked_at: null }]);
    expect(await env.DIRECTORY.get(`k:${key_id}`, { type: "json" })).toEqual({
      o: o.owner, h: sha256Hex(parseKey(key)!.secret), c: created_at });
  });

  it("naming no scopes, makes a full key, as an MCP client's: it writes and reads the " +
     "owner's files", async () => {
    const o = await signedIn();
    const { key, key_id } = await (await appPost("keys", o.token, o.session,
                                                 { name: "canary", scopes: [] })).json<
      { key: string; key_id: string }>();
    expect(await grantRow(o.owner, key_id)).toEqual([
      { source: "api_key", mode: "full", revoked_at: null }]);
    const path = "/workspace/beat.txt";
    expect((await api(key, "file_write", { path, expected_etag: "", content: "beat" })).status)
      .toBe(200);
    expect(await (await api(key, "file_read", { path })).json()).toMatchObject({ text: "beat" });
  });

  it("naming scopes, makes a key whose agent is registered at its mint, in the space its " +
     "scopes name: it writes and reads its mailbox at once, and goes with its revoke",
     async () => {
    const o = await signedIn();
    const { key, key_id } = await (await appPost("keys", o.token, o.session, { name: "laptop",
      scopes: ["read_files", "write_files", "read_graph"].map((capability) =>
        ({ universe: "cc", agent: "main", capability })) })).json<
      { key: string; key_id: string }>();
    expect((await api(key, "file_write", { path: "/workspace/beat.txt", expected_etag: "",
                                           content: "beat" })).status).toBe(200);
    expect((await api(key, "mailbox_read", { limit: 5 })).status).toBe(200);
    const agents = async () => (await (await appPost("tools/agents_list", o.token, o.session,
                                                     {})).json<{ agents: {
      outside_client: string; command_center_id: string; display_name: string;
      source: string; delivery_method: string }[] }>()).agents
      .filter((agent) => agent.outside_client === apiKeyOrigin(key_id).client);
    expect(await agents()).toEqual([expect.objectContaining({
      command_center_id: "cc", display_name: "laptop", source: "api_key",
      delivery_method: "pull" })]);
    expect((await appPost("grants/revoke", o.token, null,
                          { client: apiKeyOrigin(key_id).client })).status).toBe(200);
    expect(await agents()).toEqual([]);
  });

  it("refuses a scoped mint whose agent cannot register, keeping nothing of it", async () => {
    const o = await signedIn();
    let puts = 0;
    const counted = directoryWith({ put: async () => { puts++; } });
    const response = await call(new Request(url("/app/api/keys"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}`,
                                 Cookie: `__Host-ta-owner=${o.session}` },
      body: JSON.stringify({ name: "laptop", scopes: ["cc", "dd"].map((universe) =>
        ({ universe, agent: "main", capability: "read_graph" })) }) }), counted);
    expect(response.status).toBe(400);
    expect(puts).toBe(0);
    expect((await grantsOf(o.owner)).filter((row) => row.source === "api_key")).toEqual([]);
  });

  it("writes no entry when the grant is refused", async () => {
    const o = await signedIn();
    let puts = 0;
    const counted = directoryWith({ put: async () => { puts++; } });
    const response = await call(new Request(url("/app/api/keys"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}`,
                                 Cookie: `__Host-ta-owner=${o.session}` },
      body: JSON.stringify({ name: "laptop", scopes: [{ universe: "*", agent: "main",
                                                        capability: "read_graph" }] }) }), counted);
    expect(response.status).toBe(400);
    expect(puts).toBe(0);
  });

  it("leaves a revocable grant when the entry fails to write", async () => {
    const o = await signedIn();
    const failing = directoryWith({ put: async () => { throw new Error("KV down"); } });
    await expect(mint(o, failing)).rejects.toThrow("KV down");
    expect((await grantsOf(o.owner)).map(({ source, mode }) => ({ source, mode })))
      .toEqual([{ source: "api_key", mode: "scoped" }]);
  });

  it("refuses a name that is blank or over 80 characters", async () => {
    const o = await signedIn();
    for (const name of ["  ", "x".repeat(81), 7]) {
      const response = await appPost("keys", o.token, o.session, { name, scopes: SCOPES });
      expect(response.status).toBe(400);
    }
  });

  // That the owner refuses a mint past MAX_KEYS live keys, counted in the mint's own unit, is
  // the owner's test (`owner/test/admit.test.ts`); here, the doors' answer to its refusal.
  it("answers a mint the owner refuses with its refusal, and writes no entry", async () => {
    const o = await signedIn();
    await mint(o);
    const before = await grantsOf(o.owner);
    let puts = 0;
    const counted = directoryWith({ put: async () => { puts++; } });
    await refuseOnce("keyMint", o.owner, new Refusal("refused", "no more keys for this owner"));
    const over = await call(new Request(url("/app/api/keys"), {
      method: "POST", headers: { "Content-Type": "application/json",
                                 Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}`,
                                 Cookie: `__Host-ta-owner=${o.session}` },
      body: JSON.stringify({ name: "one more", scopes: SCOPES }) }), counted);
    expect(over.status).toBe(403);
    expect(await over.json()).toEqual({ error: "no more keys for this owner" });
    expect(puts).toBe(0);
    expect(await grantsOf(o.owner)).toEqual(before);
  });
});

describe("a key's use", () => {
  it("binds the key's own origin and source", async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const response = await api(key, "agents_list", {});
    expect(response.status).toBe(200);
    expect((await grantsOf(o.owner)).map(({ client, source }) => ({ client, source })))
      .toEqual([{ client: apiKeyOrigin(key_id).client, source: "api_key" }]);
  });

  // The owner's revoke fence against that sign-in time is the owner's test
  // (`owner/test/grants.test.ts`); here, the time the doors hand it, and its refusal answered.
  it("carries the mint time as its sign-in, and answers the owner's revoke fence 403",
     async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const minted = now() - 1_000;
    const entry = await env.DIRECTORY.get<{ o: string; h: string }>(`k:${key_id}`, { type: "json" });
    await env.DIRECTORY.put(`k:${key_id}`, JSON.stringify({ ...entry, c: minted }));
    await env.OWNER_GATE.seen(o.owner);
    expect((await api(key, "agents_list", {})).status).toBe(200);
    const [sent] = await env.OWNER_GATE.seen(o.owner);
    expect((sent!.args as [Binding])[0]).toMatchObject({
      owner: o.owner, origin: apiKeyOrigin(key_id), source: { kind: "api_key", ref: key_id },
      authenticated_at: minted });
    await refuseOnce("outsideAgents", o.owner,
                     new Refusal("refused", "a fresh sign-in is required after a revoke"));
    const refused = await api(key, "agents_list", {});
    expect(refused.status).toBe(403);
    expect(await refused.json()).toEqual({ error: "a fresh sign-in is required after a revoke" });
  });

  it.each([
    ["a wrong secret", (key: string) => `${key.slice(0, -1)}${key.endsWith("A") ? "B" : "A"}`],
    ["an unknown id", () => `ta_key_${"0".repeat(16)}_${"a".repeat(43)}`],
    ["no key shape", () => "not-a-key"],
  ])("is 401 for %s, and no object is reached", async (_what, alter) => {
    const victim = owner();
    const { key, keyId, secret } = newKey();
    await env.DIRECTORY.put(`k:${keyId}`, JSON.stringify({ o: victim, h: sha256Hex(secret),
                                                           c: now() }));
    const response = await api(alter(key), "agents_list", {});
    expect(response.status).toBe(401);
    expect(await reached(victim)).toBe(false);
  });

  it("is 401 with no credential", async () => {
    const response = await call(new Request(url("/api/tools/agents_list"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }));
    expect(response.status).toBe(401);
  });

  it("names only a tool of the table", async () => {
    const o = await signedIn();
    const { key } = await mint(o);
    expect((await api(key, "bash", {})).status).toBe(404);
    expect((await api(key, "ta", {})).status).toBe(404);
  });

  it("answers a GET 405 for each tool the key's profile serves and 404 for any other, " +
     "which the handles canary reads its new side by", async () => {
    // deploy/canary.ts: one read-only GET a name, never a tool's call.
    const o = await signedIn();
    const { key } = await mint(o);
    const get = (tool: string) => call(new Request(url(`/api/tools/${tool}`), {
      headers: { Authorization: `Bearer ${key}` } }));
    for (const tool of profileNames(DEFAULT_PROFILE)) {
      expect([tool, (await get(tool)).status]).toEqual([tool, 405]);
    }
    for (const tool of ["read_graph", "write_page", "get_status", "no_such_tool"]) {
      expect([tool, (await get(tool)).status]).toEqual([tool, 404]);
    }
  });

  it("passes the probe owner's GET under the window's record, admitting it alone, and " +
     "refuses it with the outside gate off", async () => {
    // The window's step 2 (platform/deploy/checklist.md): both gates, the probe owner alone.
    const probe = await signedIn();
    const { key } = await mint(probe);
    const other = await signedIn();
    const { key: otherKey } = await mint(other);
    const get = (with_: string) => call(new Request(url("/api/tools/file_read"), {
      headers: { Authorization: `Bearer ${with_}` } }));
    const record = { outside: "on", ordinary: "canary", canary: [probe.owner], inbound: false,
                     tiers: TIERS };
    await setControl(record);
    expect((await get(key)).status).toBe(405);
    expect((await get(otherKey)).status).toBe(503);
    await setControl({ ...record, outside: "off" });
    expect((await get(key)).status).toBe(503);
  });
});

describe("a revoke", () => {
  it("refuses the key at once in the object, even when the entry cannot be deleted",
     async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const stuck = directoryWith({ delete: async () => { throw new Error("KV down"); } });
    await expect(call(new Request(url("/app/api/keys/revoke"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}` },
      body: JSON.stringify({ key_id }) }), stuck)).rejects.toThrow("KV down");
    expect(await env.DIRECTORY.get(`k:${key_id}`)).not.toBeNull();
    const refused = await api(key, "agents_list", {});
    expect(refused.status).toBe(403);
    expect(await refused.json()).toEqual({ error: "outside client revoked" });
  });

  it("needs no session, and deletes the entry after", async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const response = await appPost("keys/revoke", o.token, null, { key_id });
    expect(await response.json()).toEqual({ key_id, revoked: true });
    expect(await env.DIRECTORY.get(`k:${key_id}`)).toBeNull();
    expect((await api(key, "agents_list", {})).status).toBe(401);
    expect(await grantRow(o.owner, key_id)).toEqual([
      { source: "api_key", mode: "revoked", revoked_at: expect.any(Number) }]);
  });

  it("does not touch another owner's key", async () => {
    const alice = await signedIn();
    const bob = await signedIn();
    const { key, key_id } = await mint(bob);
    const response = await appPost("keys/revoke", alice.token, null, { key_id });
    expect(response.status).toBe(404);
    expect(await env.DIRECTORY.get(`k:${key_id}`)).not.toBeNull();
    expect((await api(key, "agents_list", {})).status).toBe(200);
  });

  it("is not found for a key that never was", async () => {
    const o = await signedIn();
    const response = await appPost("keys/revoke", o.token, null, { key_id: "0".repeat(16) });
    expect(response.status).toBe(404);
  });
});

describe("a delete", () => {
  it("revokes, deletes the entry, then forgets the grant", async () => {
    const o = await signedIn();
    const { key_id } = await mint(o);
    const seen: unknown[] = [];
    const observed = directoryWith({ delete: async (name: string) => {
      seen.push(await grantRow(o.owner, key_id));
      await env.DIRECTORY.delete(name);
    } });
    const response = await call(new Request(url("/app/api/keys/delete"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}` },
      body: JSON.stringify({ key_id }) }), observed);
    expect(await response.json()).toEqual({ key_id, deleted: true });
    // At the directory delete the grant was revoked and not yet forgotten.
    expect(seen).toEqual([[{ source: "api_key", mode: "revoked", revoked_at: expect.any(Number) }]]);
    expect(await grantRow(o.owner, key_id)).toEqual([]);
    expect(await env.DIRECTORY.get(`k:${key_id}`)).toBeNull();
  });

  it("forgets nothing when the entry cannot be deleted", async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const stuck = directoryWith({ delete: async () => { throw new Error("KV down"); } });
    await expect(call(new Request(url("/app/api/keys/delete"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: ORIGIN,
                                 Authorization: `Bearer ${o.token}` },
      body: JSON.stringify({ key_id }) }), stuck)).rejects.toThrow("KV down");
    expect(await grantRow(o.owner, key_id)).toEqual([
      { source: "api_key", mode: "revoked", revoked_at: expect.any(Number) }]);
    expect((await api(key, "agents_list", {})).status).toBe(403);
  });
});

describe("a key's own agent", () => {
  const AGENT = { display_name: "Muse", delivery_method: "pull", recipe_id: null };

  /** The key's card filed on its own call, then the owner's apply of it on Home's route. */
  async function filedAndApplied(o: Owner, key: string, request: object) {
    const filed = await api(key, "agent_register", request);
    expect(filed.status).toBe(200);
    const { card_id } = await filed.json<{ card_id: string }>();
    const { cards } = await (await appPost("registrations", o.token, o.session, {}))
      .json<{ cards: { card_id: string; digest: string }[] }>();
    const card = cards.find((each) => each.card_id === card_id)!;
    const applied = await appPost("registrations/apply", o.token, o.session,
                                  { card_id, digest: card.digest });
    expect(applied.status).toBe(200);
    return applied.json<{ agent_id: string; delivery_method: string; revision: number;
                          source: string; command_center_id: string }>();
  }

  it("is the card a full key files, which the owner applies on Home: its mailbox opens, " +
     "filing again changes its delivery, and it goes with the key's revoke", async () => {
    const o = await signedIn();
    const { key, key_id } = await (await appPost("keys", o.token, o.session,
                                                 { name: "muse", scopes: [] })).json<
      { key: string; key_id: string }>();
    const refused = await api(key, "mailbox_read", { limit: 5 });
    expect([refused.status, await refused.json()]).toEqual([403, { error: NOT_AN_AGENT }]);
    const agent = await filedAndApplied(o, key, AGENT);
    expect(agent).toMatchObject({ source: "api_key", command_center_id: "main",
                                  delivery_method: "pull", revision: 1 });
    expect((await api(key, "mailbox_read", { limit: 5 })).status).toBe(200);
    expect(await filedAndApplied(o, key, { ...AGENT, delivery_method: "mcp_event" }))
      .toMatchObject({ agent_id: agent.agent_id, delivery_method: "mcp_event", revision: 2 });
    expect((await appPost("grants/revoke", o.token, null,
                          { client: apiKeyOrigin(key_id).client })).status).toBe(200);
    const { agents } = await (await appPost("tools/agents_list", o.token, o.session, {}))
      .json<{ agents: { agent_id: string }[] }>();
    expect(agents.map((each) => each.agent_id)).not.toContain(agent.agent_id);
  });
});
