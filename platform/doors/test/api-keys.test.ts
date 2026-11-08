/**
 * `/api` keys (research §5; checklist 13, 14, 15): the Python's format and hash, mint
 * in session as grant-then-entry, use bound to the key's origin and mint time, a wrong
 * key never reaching an object, and the revoke and delete orders.
 */
import { Refusal, refusalOf } from "../../contract/src/refusal";
import { BUSY, slotBusy } from "../../contract/src/slots";
import { refuseOnce } from "./entry";
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { GRANT_LIMITS } from "../../owner/src/grants";
import { apiKeyOrigin } from "../../owner/src/ledger";
import {
  MAX_KEYS_PER_OWNER, newKey, parseKey, secretHash,
} from "../src/api-keys";
import { type DoorsEnv, now } from "../src/config";
import { DEFAULT_PROFILE, profileNames } from "../src/tools";
import {
  appPost, call, connect, doorsEnv, legacyPost, OPEN, reached, registerClient,
  serveFirstPartyDocument, setControl, signInApp, sqlIn, TIERS, url,
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
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
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

function grantRow(o: string, keyId: string) {
  return sqlIn(o, "SELECT source, mode, revoked_at FROM outside_clients WHERE client = ?",
               apiKeyOrigin(keyId).client);
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
    refuseOnce("outsideAgents", o.owner, new Refusal("storage_busy", "storage_busy: catching up",
                                         { retryAfterS: 7, facts: FACTS }));
    const error = await env.OWNER.getByName(o.owner).outsideAgents({ owner: o.owner } as never)
      .then(() => null, (caught: unknown) => caught);
    expect(error).not.toBeInstanceOf(Refusal);  // the class never crosses
    const refusal = refusalOf(error)!;
    expect([refusal.code, refusal.message, refusal.retryAfterS, refusal.facts])
      .toEqual(["storage_busy", "storage_busy: catching up", 7, FACTS]);
    expect([refusal.status, refusal.retryAfter]).toEqual([503, 7]);
  });

  it.each([
    ["storage_full", "storage_full: your storage is full", {}, 507, null],
    ["uploads_open", "uploads_open: held by uploads still open", {}, 409, null],
    ["storage_busy", "storage_busy: catching up", {}, 503, "30"],
    ["storage_busy", "storage_busy: the archive moved this mail", { retryAfterS: 1 }, 503, "1"],
    ["owner_suspended", "owner_suspended: the owner's account is suspended", {}, 403, null],
    ["invalid", "invalid ledger request", {}, 400, null],
  ] as const)("answers %s on the API as its code says", async (code, message, more, status,
                                                              retry) => {
    const o = await signedIn();
    const { key } = await mint(o);
    refuseOnce("outsideAgents", o.owner, new Refusal(code, message, more));
    const refused = await api(key, "agents_list", {});
    expect([refused.status, refused.headers.get("Retry-After"), await refused.json()])
      .toEqual([status, retry, { error: message }]);
  });

  // The doors' one answer on every surface that answers as JSON (review N1): a busy
  // refusal's 503 and its Retry-After on the app's tools, MCP's profile and an upload's
  // bytes, as on the API above.
  it("answers busy 503 with Retry-After on the app's tools", async () => {
    const o = await signedIn();
    refuseOnce("outsideAgents", o.owner, slotBusy());
    const busy = await appPost("tools/agents_list", o.token, o.session, {});
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { error: BUSY }]);
  });

  it("answers busy 503 with Retry-After on MCP's profile", async () => {
    const who = owner();
    const tokens = await connect(await registerClient(), { owner: who });
    refuseOnce("toolProfile", who, slotBusy());
    const busy = await legacyPost(tokens.access_token, { jsonrpc: "2.0", id: 1,
                                                         method: "tools/list" });
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { error: BUSY }]);
  });

  it("answers a refusal's Retry-After on an upload's bytes", async () => {
    const o = await signedIn();
    refuseOnce("fileUploadTarget", o.owner, slotBusy());
    const busy = await call(new Request(url(`/app/api/files/upload/${"a".repeat(32)}/0`), {
      method: "PUT", body: new Uint8Array(1),
      headers: { "Content-Type": "application/octet-stream", Origin: "https://tinyassets.io",
                 "Sec-Fetch-Site": "same-origin", Authorization: `Bearer ${o.token}`,
                 Cookie: `__Host-ta-owner=${o.session}` } }));
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { error: BUSY }]);
  });

  it("is a bug, loudly, when its code is no code of the list", async () => {
    const o = await signedIn();
    const forged = new Refusal("refused", "from another version");
    (forged as unknown as { code: string }).code = "not_a_code";
    refuseOnce("outsideAgents", o.owner, forged);
    const error = await env.OWNER.getByName(o.owner).outsideAgents({ owner: o.owner } as never)
      .then(() => null, (caught: unknown) => caught);
    expect(refusalOf(error)).toBeNull();
  });
});

describe("the key format", () => {
  it("answers a call the owner's slots refuse busy 503 with Retry-After, and the next lands",
     async () => {
    const o = await signedIn();
    const { key } = await mint(o);
    refuseOnce("outsideAgents", o.owner, slotBusy());
    const busy = await api(key, "agents_list", {});
    expect([busy.status, busy.headers.get("Retry-After")]).toEqual([503, "1"]);
    expect(await busy.json()).toEqual({ error: BUSY });
    expect([slotBusy().code, slotBusy().status, slotBusy().retryAfter, slotBusy().message])
      .toEqual(["busy", 503, 1, BUSY]);
    expect((await api(key, "agents_list", {})).status).toBe(200);
  });

  it("is the Python's: ta_key_<16 hex>_<43 urlsafe>", () => {
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

  it("stores the secret's SHA-256 hex, as hashlib.sha256(secret.encode())", async () => {
    const { secret } = newKey();
    expect(await secretHash(secret)).toBe(createHash("sha256").update(secret, "utf8").digest("hex"));
    expect(await secretHash("abc"))
      .toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("keeps a cutover-imported key working", async () => {
    const o = await signedIn();
    const { key_id } = await mint(o);
    // The Python minted this key: its entry is the same shape, its secret its own.
    const imported = `ta_key_${key_id}_${"Z".repeat(43)}`;
    await env.DIRECTORY.put(`k:${key_id}`, JSON.stringify({
      o: o.owner, h: await secretHash("Z".repeat(43)), c: now() - 86_400 }));
    const response = await api(imported, "agents_list", {});
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ agents: [], owner_agents: [],
                                            apps: expect.any(Array) });
  });
});

describe("a mint", () => {
  it("needs the owner's session", async () => {
    const o = await signedIn();
    const response = await appPost("keys", o.token, null, { name: "laptop", scopes: SCOPES });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "interactive owner session required" });
    expect(await sqlIn(o.owner, "SELECT client FROM outside_clients")).toEqual([]);
  });

  it("writes the grant, then the directory entry, then returns the secret once", async () => {
    const o = await signedIn();
    const { key, key_id, created_at } = await mint(o);
    expect(await grantRow(o.owner, key_id)).toEqual([
      { source: "api_key", mode: "scoped", revoked_at: null }]);
    expect(await env.DIRECTORY.get(`k:${key_id}`, { type: "json" })).toEqual({
      o: o.owner, h: await secretHash(parseKey(key)!.secret), c: created_at });
  });

  it("writes no entry when the grant is refused", async () => {
    const o = await signedIn();
    let puts = 0;
    const counted = directoryWith({ put: async () => { puts++; } });
    const response = await call(new Request(url("/app/api/keys"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
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
    const rows = await sqlIn(o.owner, "SELECT source, mode FROM outside_clients");
    expect(rows).toEqual([{ source: "api_key", mode: "scoped" }]);
  });

  it("refuses a name that is blank or over 80 characters", async () => {
    const o = await signedIn();
    for (const name of ["  ", "x".repeat(81), 7]) {
      const response = await appPost("keys", o.token, o.session, { name, scopes: SCOPES });
      expect(response.status).toBe(400);
    }
  });

  it("refuses past MAX_KEYS live keys, counted in the mint's own unit, and writes nothing",
     async () => {
    const o = await signedIn();
    await mint(o);
    // The rest of the live keys, as earlier mints left them (a revoked one does not count).
    await sqlIn(o.owner, "INSERT INTO owner_keys (key_id, client, name, created_at, revoked_at) " +
                "SELECT printf('%016x', x), printf('%064x', x), 'k', 0, " +
                "CASE WHEN x = 1 THEN 1 ELSE NULL END FROM (WITH RECURSIVE c(x) AS " +
                "(SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < ?) SELECT x FROM c)",
                MAX_KEYS_PER_OWNER);
    const before = await sqlIn(o.owner, "SELECT COUNT(*) AS n FROM outside_clients");
    const over = await appPost("keys", o.token, o.session, { name: "one more", scopes: SCOPES });
    expect(over.status).toBe(403);
    expect(await over.json()).toEqual({ error: "too many live API keys; revoke one first" });
    expect(await sqlIn(o.owner, "SELECT COUNT(*) AS n FROM outside_clients")).toEqual(before);
    expect(MAX_KEYS_PER_OWNER).toBe(GRANT_LIMITS.maxOwnerClients);
  });
});

describe("a key's use", () => {
  it("binds the key's own origin and source", async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const response = await api(key, "agents_list", {});
    expect(response.status).toBe(200);
    expect(await sqlIn(o.owner, "SELECT client, source FROM outside_clients")).toEqual([
      { client: apiKeyOrigin(key_id).client, source: "api_key" }]);
  });

  it("carries the mint time as its sign-in: a revoke before it passes, one after does not",
     async () => {
    const o = await signedIn();
    const { key, key_id } = await mint(o);
    const minted = now() - 1_000;
    const entry = await env.DIRECTORY.get<{ o: string; h: string }>(`k:${key_id}`, { type: "json" });
    await env.DIRECTORY.put(`k:${key_id}`, JSON.stringify({ ...entry, c: minted }));
    const client = apiKeyOrigin(key_id).client;
    await sqlIn(o.owner, "UPDATE outside_clients SET revoked_at = ? WHERE client = ?",
                minted - 100, client);
    expect((await api(key, "agents_list", {})).status).toBe(200);
    await sqlIn(o.owner, "UPDATE outside_clients SET revoked_at = ? WHERE client = ?",
                now() - 10, client);
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
    await env.DIRECTORY.put(`k:${keyId}`, JSON.stringify({ o: victim, h: await secretHash(secret),
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
    // scripts/cutover_handles_canary.py: one read-only GET a name, never a tool's call.
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
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
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
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
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
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
                                 Authorization: `Bearer ${o.token}` },
      body: JSON.stringify({ key_id }) }), stuck)).rejects.toThrow("KV down");
    expect(await grantRow(o.owner, key_id)).toEqual([
      { source: "api_key", mode: "revoked", revoked_at: expect.any(Number) }]);
    expect((await api(key, "agents_list", {})).status).toBe(403);
  });
});

describe("the registering route", () => {
  const AGENT = { command_center_id: "cc", display_name: "Laptop", delivery_method: "pull",
                  recipe_id: null };

  it("registers the key's agent with its origin, in session", async () => {
    const o = await signedIn();
    const { key_id } = await mint(o);
    const response = await appPost("outside-agents", o.token, o.session, { key_id, ...AGENT });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ outside_client: apiKeyOrigin(key_id).client });
  });

  it("needs the session", async () => {
    const o = await signedIn();
    const { key_id } = await mint(o);
    const response = await appPost("outside-agents", o.token, null, { key_id, ...AGENT });
    expect(response.status).toBe(403);
  });

  it("refuses another owner's key", async () => {
    const alice = await signedIn();
    const bob = await signedIn();
    const { key_id } = await mint(bob);
    const response = await appPost("outside-agents", alice.token, alice.session,
                                   { key_id, ...AGENT });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "the API key is not this owner's live key" });
  });
});
