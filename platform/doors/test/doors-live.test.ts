/**
 * Slice L's doors, in workerd through the real owner object: the platform's explicit admit
 * at sign-up and the erasure's outside record (an erased owner's name resolving nothing
 * again); a set to `revoked` through the provider revoke; the key store; `/hooks/inbound`'s
 * two shapes (a bad HMAC, a replay, the gate, the breaker); the security bounds on
 * unauthenticated callers; and the file door's bytes, streamed to `FILES` by the binder.
 */
import { createHash, createHmac } from "node:crypto";

import { env, evictDurableObject, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import { ADMIN_CALLER } from "../../admin/src/caller";
import { OWNER_ERASED, OWNER_UNADMITTED } from "../../owner/src/admission";
import { BREAKER_FAILURES } from "../../owner/src/inbound";
import { nativeId } from "../../owner/src/ledger";
import type { Binding } from "../../owner/src/owner-do";
import { defineAgent } from "../../owner/test/runs-support";
import { failure } from "../../owner/test/support";
import { API_BODY_MAX, INBOUND_BODY_MAX, MCP_RESOURCE, now } from "../src/config";
import { readOwner, readRoute } from "../src/directory";
import { reprDigest } from "../src/file-bytes";
import { sharedApps } from "../src/inbound";
import { CIMD_CACHE } from "../src/throttle";
import type { OwnerDO } from "./entry";
import { FORGOTTEN } from "./fake-upstream";
import {
  appCallback, appPost, call, connect, doorsEnv, legacyTool, NAVIGATE, OPEN, refresh,
  registerClient,
  serveFirstPartyDocument, setControl, signInApp, sqlIn, url,
} from "./support";

const SCOPES = [{ universe: "cc", agent: "main", capability: "read_graph" }];
const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: ["read_files", "read_graph"] });
const SLACK = sharedApps().find((app) => app.id === "slack")!;
const SLACK_SECRET = (env as unknown as Record<string, string>)["INBOUND_SLACK_SIGNING_SECRET"]!;

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

function ownerBinding(who: string): Binding {
  return { owner: who, caller: who, origin: null };
}

/** An owner signed in to its app, with its `main` agent defined. */
async function owned() {
  const who = owner();
  const app = await signInApp({ owner: who });
  await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
  return { who, app };
}

async function json<T = Record<string, unknown>>(response: Response): Promise<T> {
  return await response.json() as T;
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl({ ...OPEN, inbound: true });
  restore();
  restore = serveFirstPartyDocument();
});

describe("sign-up", () => {
  it("is the platform's explicit admit: a fresh object is pending until the callback admits it",
     async () => {
    const who = owner();
    // Any getByName makes an object; made is never admitted.
    const fresh = env.OWNER.get(env.OWNER.idFromName(who));
    expect((await failure(() => fresh.grantList(ownerBinding(who)))).message).toMatch(OWNER_UNADMITTED);
    expect(await readOwner(doorsEnv, who)).toBeNull();
    const app = await signInApp({ owner: who });
    expect(await readOwner(doorsEnv, who)).toEqual({ s: "admitted", t: expect.any(Number) });
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "admitted" }]);
    expect((await appPost("keys/list", app.access_token, null, {})).status).toBe(200);
  });

  it("admits an owner who first signs in through an MCP client", async () => {
    const who = owner();
    await connect(await registerClient(), { owner: who });
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "admitted" }]);
  });

  it("never lifts a suspension when the owner signs in again", async () => {
    const who = owner();
    await signInApp({ owner: who });
    await env.OWNER.getByName(who).admissionSet({ owner: who, caller: ADMIN_CALLER, origin: null },
                                                { reason: "billing", suspended: true });
    await env.DIRECTORY.delete(`o:${who}`);  // as if the record were lost
    await appCallback({ owner: who });
    expect(await sqlIn(who, "SELECT state, reasons FROM owner_admission"))
      .toEqual([{ state: "suspended", reasons: "[\"billing\"]" }]);
  });

  it("is refused for an admit the platform's reserved caller sends at any other door", async () => {
    const who = owner();
    await signInApp({ owner: who });
    expect((await failure(() => env.OWNER.getByName(who).grantList({ owner: who, caller: ADMIN_CALLER,
                                                      origin: null }))).message).toMatch(/reaches no owner door/);
  });
});

describe("the erasure", () => {
  it("writes the outside record first, so an erased owner's name resolves nothing again",
     async () => {
    const { who, app } = await owned();
    // A key, an inbound URL and an MCP client, each a way back to the owner.
    const minted = await json<{ key: string; key_id: string }>(
      await appPost("keys", app.access_token, app.session, { name: "laptop", scopes: SCOPES }));
    const hook = await json<{ url: string; route: string }>(
      await appPost("hooks/create", app.access_token, app.session, { agent: "main" }));
    const client = await registerClient();
    const tokens = await connect(client, { owner: who });
    FORGOTTEN.length = 0;
    const erased = await appPost("account/erase", app.access_token, app.session,
                                 { confirm: "erase my account" });
    expect(erased.status).toBe(200);
    expect(await erased.json()).toMatchObject({ erasing: true, erased: true,
                                               identity_deleted: true, keys: 1, routes: 1 });
    expect(erased.headers.getSetCookie().join()).toMatch(/__Host-ta-owner=;/);
    // The outside record, the directory's entries gone, the identity deleted.
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "erased" });
    expect(await env.DIRECTORY.get(`k:${minted.key_id}`)).toBeNull();
    expect(await readRoute(doorsEnv, hook.route)).toBeNull();
    expect(FORGOTTEN).toEqual([who]);
    // No key, no inbound route, no token, no refresh, no sign-in resolves the name again.
    const key = await call(new Request(url("/api/tools/agents_list"), {
      method: "POST", headers: { Authorization: `Bearer ${minted.key}`,
                                 "Content-Type": "application/json" }, body: "{}" }));
    expect(key.status).toBe(401);
    const event = await call(new Request(hook.url, { method: "POST", body: "{}", headers: {
      "Idempotency-Key": "e1", "X-Event-Timestamp": String(now()) } }));
    expect(event.status).toBe(404);
    expect((await refresh(client, tokens.refresh_token)).status).toBe(400);
    const again = await appCallback({ owner: who });
    expect(again.back.status).toBe(302);
    expect(new URL(again.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    expect(FORGOTTEN).toEqual([who, who]);  // the identity's deletion tried again
    // The object holds the tombstone alone, and a stale getByName is refused, not remade.
    expect((await failure(() => env.OWNER.getByName(who).grantList(ownerBinding(who)))).message).toMatch(OWNER_ERASED);
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "erased" }]);
  });

  it("closes the crash window: a wiped object that lost its tombstone is pending, never admitted",
     async () => {
    const { who, app } = await owned();
    await appPost("account/erase", app.access_token, app.session, { confirm: "erase my account" });
    // The crash the window names: the wipe durable, the tombstone lost.
    await runInDurableObject(env.OWNER.getByName(who), async (_instance: OwnerDO, state) => {
      await state.storage.deleteAll();
    });
    await evictDurableObject(env.OWNER.getByName(who));
    const remade = env.OWNER.get(env.OWNER.idFromName(who));
    expect((await failure(() => remade.grantList(ownerBinding(who)))).message).toMatch(OWNER_UNADMITTED);
    // And no sign-in admits the name again: the directory's record refuses it first.
    const again = await appCallback({ owner: who });
    expect(new URL(again.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    expect((await failure(() => remade.grantList(ownerBinding(who)))).message).toMatch(OWNER_UNADMITTED);
  });

  it("erases nothing without the session or the owner's words", async () => {
    const { who, app } = await owned();
    for (const [session, body] of [[null, { confirm: "erase my account" }],
                                   [app.session, { confirm: "yes" }],
                                   [app.session, {}]] as const) {
      const refused = await appPost("account/erase", app.access_token, session, body);
      expect(refused.status).toBeGreaterThanOrEqual(400);
    }
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "admitted" }]);
  });
});

describe("a grant set to revoked", () => {
  it("revokes the client at the provider too, as grants/revoke does", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const client = await registerClient();
    const tokens = await connect(client, { owner: who });
    await legacyTool(tokens.access_token, "agents_list", {});  // its grant row, recorded
    const [row] = (await env.OWNER.getByName(who).grantList(ownerBinding(who))).clients;
    const set = await appPost("grants/set", app.access_token, app.session, {
      client: row!.client, source: "mcp", mode: "revoked", scopes: [],
      expected_revision: row!.revision, name: "" });
    expect(set.status).toBe(200);
    expect(await set.json()).toMatchObject({ mode: "revoked", grants: 1 });
    expect((await refresh(client, tokens.refresh_token)).status).toBe(400);
  });

  it("deletes a key's directory entry, as a key's revoke does", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const minted = await json<{ key_id: string }>(
      await appPost("keys", app.access_token, app.session, { name: "laptop", scopes: SCOPES }));
    const [row] = (await env.OWNER.getByName(who).grantList(ownerBinding(who))).clients;
    const set = await appPost("grants/set", app.access_token, app.session, {
      client: row!.client, source: "api_key", mode: "revoked", scopes: [],
      expected_revision: row!.revision, name: "" });
    expect(set.status).toBe(200);
    expect(await env.DIRECTORY.get(`k:${minted.key_id}`)).toBeNull();
    const [key] = (await json<{ keys: { revoked_at: number | null }[] }>(
      await appPost("keys/list", app.access_token, null, {}))).keys;
    expect(key!.revoked_at).toEqual(expect.any(Number));
  });
});

describe("the key store", () => {
  it("lists each key's id, name, mint time and last use, and follows its revoke and forget",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const minted = await json<{ key: string; key_id: string; created_at: number }>(
      await appPost("keys", app.access_token, app.session, { name: "laptop", scopes: SCOPES }));
    const list = async () => (await json<{ keys: Record<string, unknown>[] }>(
      await appPost("keys/list", app.access_token, null, {}))).keys;
    expect(await list()).toEqual([{ key_id: minted.key_id, name: "laptop",
                                    created_at: minted.created_at, last_used_at: null,
                                    revoked_at: null }]);
    const used = await call(new Request(url("/api/tools/agents_list"), {
      method: "POST", headers: { Authorization: `Bearer ${minted.key}`,
                                 "Content-Type": "application/json" }, body: "{}" }));
    expect(used.status).toBe(200);
    expect((await list())[0]!["last_used_at"]).toEqual(expect.any(Number));
    await appPost("keys/revoke", app.access_token, null, { key_id: minted.key_id });
    expect((await list())[0]!["revoked_at"]).toEqual(expect.any(Number));
    await appPost("keys/delete", app.access_token, null, { key_id: minted.key_id });
    expect(await list()).toEqual([]);
  });

  it("applies an agent's held key request with its grant, once", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const stub = env.OWNER.getByName(who);
    const change = { type: "api_key_request", scopes: SCOPES };
    await stub.policyPropose(ownerBinding(who), { change, request_id: "card-key-1" });
    const { digest } = await stub.policyProposal(ownerBinding(who), { request_id: "card-key-1" });
    const proposal = { request_id: "card-key-1", digest };
    const first = await appPost("keys", app.access_token, app.session,
                                { name: "agent key", scopes: SCOPES, proposal });
    expect(first.status).toBe(200);
    const again = await appPost("keys", app.access_token, app.session,
                                { name: "agent key", scopes: SCOPES, proposal });
    expect(again.status).toBe(403);
    expect(await again.json()).toEqual({ error: "proposal is not the pending card shown" });
    const other = [{ universe: "cc", agent: "main", capability: "write_graph" }];
    await stub.policyPropose(ownerBinding(who), { change, request_id: "card-key-2" });
    const held = await stub.policyProposal(ownerBinding(who), { request_id: "card-key-2" });
    const widened = await appPost("keys", app.access_token, app.session, {
      name: "agent key", scopes: other,
      proposal: { request_id: "card-key-2", digest: held.digest } });
    expect(widened.status).toBe(403);
    expect((await stub.keyList(ownerBinding(who))).keys).toHaveLength(1);
  });
});

/** A Slack request signed as Slack signs it: `v0:<timestamp>:<body>`. */
function slackRequest(body: unknown, at: number = now(), secret: string = SLACK_SECRET): Request {
  const text = JSON.stringify(body);
  const signature = createHmac("sha256", secret).update(`v0:${at}:${text}`).digest("hex");
  return new Request(url(`/hooks/inbound/${SLACK.endpoint}`), {
    method: "POST", body: text,
    headers: { "Content-Type": "application/json", "X-Slack-Signature": `v0=${signature}`,
               "X-Slack-Request-Timestamp": String(at) } });
}

function slackEvent(team: string, eventId: string): Record<string, unknown> {
  return { type: "event_callback", is_ext_shared_channel: false, event_id: eventId,
           event_time: now(), event: { type: "message", text: "hi" },
           authorizations: [{ is_enterprise_install: false, team_id: team }] };
}

/** A shared app's install for `who`, as its owed install flow will write it. */
async function slackInstall(who: string, team: string): Promise<string> {
  const route = `i:slack:workspace:${createHash("sha256").update(team).digest("hex")}`;
  const stub = env.OWNER.getByName(who);
  // The owed install flow's two writes: the object's binding (in session), the route.
  const app = await signInApp({ owner: who });
  const session = app.session;
  const made = await stub.inboundCreate({ owner: who, caller: who, origin: null, session },
                                        { kind: "shared", agent: "main", route });
  await env.DIRECTORY.put(route, JSON.stringify({ o: who, b: made.binding_id, r: made.revision }));
  return made.binding_id;
}

function mail(who: string): Promise<Record<string, unknown>[]> {
  return sqlIn(who, "SELECT message_id, client_message_id FROM ledger_messages " +
                    "WHERE client_message_id LIKE 'inb_%' ORDER BY seq");
}

describe("/hooks/inbound, a capability URL", () => {
  async function hooked() {
    const { who, app } = await owned();
    const made = await json<{ url: string; binding_id: string; route: string; revision: number }>(
      await appPost("hooks/create", app.access_token, app.session, { agent: "main" }));
    return { who, app, ...made };
  }

  function event(hookUrl: string, id: string, body = "{\"x\":1}",
                 headers: Record<string, string> = {}): Request {
    return new Request(hookUrl, { method: "POST", body, headers: {
      "Idempotency-Key": id, "X-Event-Timestamp": String(now()), ...headers } });
  }

  it("is shown once, routed by its token's hash, and mails the binding's agent verbatim",
     async () => {
    const { who, url: hookUrl, route, binding_id } = await hooked();
    const token = hookUrl.split("/").at(-1)!;
    expect(route).toBe(`h:${createHash("sha256").update(token).digest("hex")}`);
    expect(await readRoute(doorsEnv, route)).toEqual({ o: who, b: binding_id, r: 1 });
    const bytes = "{\"raw\": \"é exact bytes\"}";
    const accepted = await call(event(hookUrl, "evt-1", bytes));
    expect(accepted.status).toBe(202);
    const answer = await json<{ receipt: string; deduped: boolean }>(accepted);
    expect(answer.deduped).toBe(false);
    const [body] = await sqlIn(who, "SELECT body FROM message_bodies WHERE message_id = ?",
                               answer.receipt);
    const envelope = JSON.parse(String(body!["body"])) as { inbound: { raw_base64: string } };
    expect(Buffer.from(envelope.inbound.raw_base64, "base64").toString("utf8")).toBe(bytes);
  });

  it("answers a replayed event deduped, with its receipt, and mails once", async () => {
    const { who, url: hookUrl } = await hooked();
    const first = await json<{ receipt: string }>(await call(event(hookUrl, "evt-r")));
    const replay = await call(event(hookUrl, "evt-r"));
    expect(replay.status).toBe(202);
    expect(await replay.json()).toMatchObject({ receipt: first.receipt, deduped: true });
    expect(await mail(who)).toHaveLength(1);
    const conflict = await call(event(hookUrl, "evt-r", "{\"other\":true}"));
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toMatchObject({ error: "event_conflict" });
  });

  it("refuses an event with no identity, an unknown token, and every method but POST", async () => {
    const { url: hookUrl } = await hooked();
    expect((await call(new Request(hookUrl, { method: "POST", body: "{}" }))).status).toBe(400);
    expect((await call(event(`${url("/hooks/inbound/")}${"A".repeat(43)}`, "e"))).status)
      .toBe(404);
    expect((await call(new Request(hookUrl))).status).toBe(405);
    expect((await call(event(`${hookUrl}/deeper`, "e"))).status).toBe(404);
  });

  it("checks an owner-held HMAC proof on the raw bytes", async () => {
    const { app } = await owned();
    const secret = "whsec_0123456789abcdef";
    const made = await json<{ url: string }>(await appPost("hooks/create", app.access_token,
      app.session, { agent: "main", secret, profile: {
        method: "hmac-sha256", signature_header: "x-hub-signature-256", encoding: "hex",
        prefix: "sha256=" } }));
    const body = "{\"action\":\"opened\"}";
    const good = createHmac("sha256", secret).update(body).digest("hex");
    expect((await call(event(made.url, "g1", body, { "X-Hub-Signature-256": `sha256=${good}` })))
      .status).toBe(202);
    const bad = await call(event(made.url, "g2", body, { "X-Hub-Signature-256": "sha256=00" }));
    expect(bad.status).toBe(401);
    expect(await bad.json()).toEqual({ error: "inbound_proof_invalid" });
    expect((await call(event(made.url, "g3", body))).status).toBe(401);
  });

  it("refuses an entry whose binding has since changed, and serves none once revoked", async () => {
    const { app, url: hookUrl, binding_id, route } = await hooked();
    // A stale entry, as KV's lag may serve it: the revision before the owner's change.
    const stale = await readRoute(doorsEnv, route);
    const changed = await appPost("hooks/set", app.access_token, app.session,
                                  { binding_id, expected_revision: 1, enabled: true });
    expect(await changed.json()).toMatchObject({ revision: 2 });
    await env.DIRECTORY.put(route, JSON.stringify(stale));
    const withdrawn = await call(event(hookUrl, "s1"));
    expect(withdrawn.status).toBe(404);
    expect(await withdrawn.json()).toEqual({ error: "binding_withdrawn" });
    await appPost("hooks/revoke", app.access_token, null, { binding_id });
    expect(await readRoute(doorsEnv, route)).toBeNull();
    expect((await call(event(hookUrl, "s2"))).status).toBe(404);
  });

  it("does not exist while the control record's inbound gate is off, and is 503 unread",
     async () => {
    const { url: hookUrl } = await hooked();
    await setControl({ ...OPEN, inbound: false });
    expect((await call(event(hookUrl, "c1"))).status).toBe(404);
    await setControl(null);
    expect((await call(event(hookUrl, "c2"))).status).toBe(404);
    await env.CONTROL.put("c:control", JSON.stringify({ outside: "on" }));
    expect((await call(event(hookUrl, "c3"))).status).toBe(503);
    await setControl({ ...OPEN, inbound: true, ordinary: "none" });
    expect((await call(event(hookUrl, "c4"))).status).toBe(503);
  });

  it("takes a body up to the platform's one request ceiling, and refuses one past it",
     async () => {
    const { url: hookUrl } = await hooked();
    const over = await call(event(hookUrl, "big", "x".repeat(INBOUND_BODY_MAX + 1)));
    expect(over.status).toBe(413);
    expect(INBOUND_BODY_MAX).toBe(API_BODY_MAX);
  });

  it("is refused at every door but inboundAccept, and inboundAccept refuses any other binding",
     async () => {
    const { who, binding_id } = await hooked();
    const stub = env.OWNER.getByName(who);
    const inbound = { owner: who, caller: who, origin: null,
                      inbound: { binding: binding_id, revision: 1 } };
    expect((await failure(() => stub.grantList(inbound))).message).toMatch(/inbound door alone/);
    expect((await failure(() => stub.inboundAccept(ownerBinding(who), { event_id: "x", event_at: now(),
                                                         body: new Uint8Array(1), headers: {} }))).message).toMatch(/inbound event's route alone/);
  });
});

describe("/hooks/inbound, a shared app (Slack's HMAC)", () => {
  it("answers its URL challenge, and delivers a signed event to the installing owner", async () => {
    const who = owner();
    await signInApp({ owner: who });
    await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    const challenge = await call(slackRequest({ type: "url_verification", challenge: "c-1" }));
    expect(await challenge.json()).toEqual({ challenge: "c-1" });
    const delivered = await call(slackRequest(slackEvent(team, "Ev1")));
    expect(delivered.status).toBe(202);
    expect(await mail(who)).toHaveLength(1);
  });

  it("refuses a bad HMAC, another secret's, and a replay past the five minutes' skew", async () => {
    const team = "TBAD";
    const tampered = slackRequest(slackEvent(team, "Ev2"));
    const forged = new Request(tampered, { body: JSON.stringify(slackEvent(team, "Ev3")) });
    expect((await call(forged)).status).toBe(401);
    expect((await call(slackRequest(slackEvent(team, "Ev4"), now(), "another-secret"))).status)
      .toBe(401);
    expect((await call(slackRequest(slackEvent(team, "Ev5"), now() - 301))).status).toBe(401);
    const unsigned = new Request(url(`/hooks/inbound/${SLACK.endpoint}`),
                                 { method: "POST", body: "{}" });
    expect((await call(unsigned)).status).toBe(401);
  });

  it("dedupes a replayed event inside the skew, and no-ops an install no owner made", async () => {
    const who = owner();
    await signInApp({ owner: who });
    await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    expect((await call(slackRequest(slackEvent(team, "EvR")))).status).toBe(202);
    const replay = await call(slackRequest(slackEvent(team, "EvR")));
    expect(await replay.json()).toMatchObject({ state: "admitted", deduped: true });
    expect(await mail(who)).toHaveLength(1);
    const stranger = await call(slackRequest(slackEvent("TNOBODY", "EvS")));
    expect(stranger.status).toBe(200);
    expect(await stranger.json()).toEqual({ state: "no_op" });
    const shared = await call(slackRequest({ ...slackEvent(team, "EvX"),
                                             is_ext_shared_channel: true }));
    expect(await shared.json()).toEqual({ state: "no_op" });
  });

  it("trips its breaker after the agent's mailbox refuses it, then no-ops until re-armed",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    const bindingId = await slackInstall(who, team);
    expect((await call(slackRequest(slackEvent(team, "EvB0")))).status).toBe(202);
    // The agent's mailbox, full.
    await sqlIn(who, "UPDATE ledger_readers SET unacked = 1000000 WHERE reader = ?",
                nativeId("main"));
    for (let i = 1; i <= BREAKER_FAILURES; i++) {
      const refused = await call(slackRequest(slackEvent(team, `EvB${i}`)));
      expect(refused.status).toBe(429);
      expect(refused.headers.get("Retry-After")).toBe("5");
      expect(await refused.json()).toMatchObject({ state: "refused", error: "mailbox_full" });
    }
    const tripped = await call(slackRequest(slackEvent(team, "EvT")));
    expect(tripped.status).toBe(200);
    expect(await tripped.json()).toEqual({ state: "no_op", error: "breaker_tripped" });
    const { notices } = await env.OWNER.getByName(who).noticeList(ownerBinding(who));
    expect(notices.map((notice) => notice.type)).toContain("inbound");
    // The owner's change re-arms it, at a new revision its route now carries.
    await sqlIn(who, "UPDATE ledger_readers SET unacked = 0 WHERE reader = ?", nativeId("main"));
    const set = await appPost("hooks/set", app.access_token, app.session,
                              { binding_id: bindingId, expected_revision: 1, enabled: true });
    expect(set.status).toBe(200);
    expect((await call(slackRequest(slackEvent(team, "EvA")))).status).toBe(202);
  });
});

describe("unauthenticated callers' bounds", () => {
  it("bound the CIMD fetches an unknown client_id makes, per caller IP, before any fetch",
     async () => {
    const ip = `203.0.113.${crypto.getRandomValues(new Uint8Array(1))[0]}-cimd-${crypto.randomUUID()}`;
    const statuses: number[] = [];
    for (let i = 0; i < 21; i++) {
      const params = new URLSearchParams({
        response_type: "code", client_id: `https://unknown-${i}.example/client.json`,
        redirect_uri: "https://unknown.example/cb", state: "s", code_challenge: "c".repeat(43),
        code_challenge_method: "S256", resource: MCP_RESOURCE });
      statuses.push((await call(new Request(url(`/authorize?${params}`), {
        headers: { ...NAVIGATE, "CF-Connecting-IP": ip } }))).status);
    }
    expect(statuses.slice(0, 20).every((status) => status !== 429)).toBe(true);
    expect(statuses[20]).toBe(429);
    // A client the provider's cache holds costs no fetch, and is never refused.
    const cached = "https://known.example/client.json";
    await (await caches.open(CIMD_CACHE)).put(cached, Response.json({ client_id: cached }, {
      headers: { "Cache-Control": "public, max-age=3600" } }));
    const known = await call(new Request(url(`/authorize?${new URLSearchParams({
      response_type: "code", client_id: cached, redirect_uri: "https://known.example/cb",
      state: "s", code_challenge: "c".repeat(43), code_challenge_method: "S256",
      resource: MCP_RESOURCE })}`), { headers: { ...NAVIGATE, "CF-Connecting-IP": ip } }));
    expect(known.status).not.toBe(429);
  });

  it("bound an unknown client_id's fetches at the token endpoint too", async () => {
    const ip = `cimd-token-${crypto.randomUUID()}`;
    const statuses: number[] = [];
    for (let i = 0; i < 21; i++) {
      statuses.push((await call(new Request(url("/oauth/token"), {
        method: "POST", headers: { "CF-Connecting-IP": ip },
        body: new URLSearchParams({ grant_type: "authorization_code", code: "x",
                                    client_id: `https://unknown-t${i}.example/c.json` }) }))).status);
    }
    expect(statuses[20]).toBe(429);
    expect(statuses.slice(0, 20).every((status) => status !== 429)).toBe(true);
  });

  it("bound sign-in attempts per caller IP, and never an authenticated caller's calls",
     async () => {
    const ip = `signin-${crypto.randomUUID()}`;
    const statuses: number[] = [];
    for (let i = 0; i < 61; i++) {
      statuses.push((await call(new Request(url("/authorize"), {
        headers: { "CF-Connecting-IP": ip } }))).status);
    }
    expect(statuses.slice(0, 60).every((status) => status === 403)).toBe(true);
    expect(statuses[60]).toBe(429);
    // An API key's calls from that same address are bounded by nothing here.
    const app = await signInApp({ owner: owner() });
    const minted = await json<{ key: string }>(
      await appPost("keys", app.access_token, app.session, { name: "k", scopes: SCOPES }));
    for (let i = 0; i < 70; i++) {
      const used = await call(new Request(url("/api/tools/agents_list"), {
        method: "POST", headers: { Authorization: `Bearer ${minted.key}`, "CF-Connecting-IP": ip,
                                   "Content-Type": "application/json" }, body: "{}" }));
      expect(used.status).toBe(200);
    }
  });
});

describe("the file door's bytes", () => {
  async function begun(bytes: Uint8Array) {
    const { who, app } = await owned();
    const chunks: string[] = [];
    for (let at = 0; at < bytes.byteLength; at += 4 * 1024 * 1024) {
      chunks.push(createHash("sha256").update(bytes.subarray(at, at + 4 * 1024 * 1024))
        .digest("hex"));
    }
    const begin = await appPost("files/upload/begin", app.access_token, null,
                                { size: bytes.byteLength, chunks });
    expect(begin.status).toBe(200);
    const { intent_id } = await json<{ intent_id: string }>(begin);
    return { who, app, intent_id };
  }

  function put(token: string, intent: string, body: Uint8Array,
               headers: Record<string, string> = {}): Promise<Response> {
    return call(new Request(url(`/app/api/files/upload/${intent}`), {
      method: "PUT", body, headers: { Authorization: `Bearer ${token}`,
                                      "Content-Type": "application/octet-stream", ...headers } }));
  }

  const FILE = new Uint8Array(4 * 1024 * 1024 + 4096).map((_byte, i) => (i * 131 + 7) % 251);

  it("streams the chunks to FILES itself, serves the body's Repr-Digest, and commits", async () => {
    const { who, app, intent_id } = await begun(FILE);
    const digest = createHash("sha256").update(FILE).digest();
    const sent = await put(app.access_token, intent_id, FILE,
                           { "Repr-Digest": reprDigest(digest) });
    expect(sent.status).toBe(200);
    expect(sent.headers.get("Repr-Digest")).toBe(reprDigest(digest));
    expect(await sent.json()).toEqual({ intent_id, chunks: 2, stored: 2 });
    const committed = await appPost("files/upload/commit", app.access_token, null, {
      intent_id, path: "/workspace/big.bin", expected_etag: null });
    expect(committed.status).toBe(200);
    const read = await env.OWNER.getByName(who).fileRead(ownerBinding(who),
                                                        { path: "/workspace/big.bin" });
    expect(read.entry.size).toBe(FILE.byteLength);
    expect(read.chunks).toHaveLength(2);
  });

  it("refuses bytes that are not the declared chunk, a short body and a long one", async () => {
    const { app, intent_id } = await begun(FILE);
    const wrong = FILE.slice();
    wrong[10] = wrong[10]! ^ 0xff;
    const refused = await put(app.access_token, intent_id, wrong);
    expect(refused.status).toBe(400);
    expect(await refused.json()).toEqual({
      error: "the bytes of chunk 0 are not the chunk the upload declared" });
    expect((await put(app.access_token, intent_id, FILE.subarray(0, 1000))).status).toBe(400);
    const longer = new Uint8Array(FILE.byteLength + 1);
    longer.set(FILE);
    expect((await put(app.access_token, intent_id, longer)).status).toBe(400);
  });

  it("refuses a body whose Repr-Digest it does not match, and an outside credential", async () => {
    const { app, intent_id } = await begun(FILE);
    const other = reprDigest(createHash("sha256").update("other").digest());
    expect((await put(app.access_token, intent_id, FILE, { "Repr-Digest": other })).status)
      .toBe(400);
    expect((await put("ta_key_0000000000000000_" + "a".repeat(43), intent_id, FILE)).status)
      .toBe(403);
  });
});
