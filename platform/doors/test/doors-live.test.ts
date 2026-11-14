/**
 * Slice L's doors, in workerd through the real owner object: the platform's explicit admit
 * at sign-up and the erasure's outside record (an erased owner's name resolving nothing
 * again); the key store; `/hooks/inbound`'s
 * two shapes (a bad HMAC, a replay, the gate, the breaker); no caller counted or capped by
 * the doors (slice L's review, N1); and the file door's bytes, streamed to `FILES` by the
 * binder.
 */
import { createHash, createHmac } from "node:crypto";

import {
  env, evictDurableObject, runDurableObjectAlarm, runInDurableObject,
} from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { OWNER_ERASED, OWNER_UNADMITTED } from "../../owner/src/admission";
import { ERASING } from "../../contract/src/refusal";
import { BREAKER_FAILURES, INSTALL_PROOF } from "../../owner/src/inbound";
import { NO_ERASURE } from "../../owner/src/erasure";
import { LEDGER_LIMITS } from "../../owner/src/ledger";
import { defineAgent } from "../../owner/test/runs-support";
import { failure } from "../../owner/test/support";
import { ERASURE_QUEUE, MCP_RESOURCE, now } from "../src/config";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { readOwner, readRoute } from "../src/directory";
import { eraseOutside, erasureQueue } from "../src/erase";
import { contentDigest } from "../src/file-bytes";
import { sharedApps } from "../src/inbound";
import { type OwnerDO, refuseOnce } from "./entry";
import { writeControl } from "../../ci/fixtures";
import { slotBusy } from "../../contract/src/slots";
import { FAKE_UPSTREAM, FORGOTTEN } from "./fake-upstream";
import {
 appCallback, appPost, call, connect, doorsEnv, NAVIGATE, OPEN, refresh, registerClient,
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
  return { owner: who, caller: who, origin: null, tiers: OPEN.tiers };
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
    await env.OWNER.getByName(who).admissionSet({ owner: who, caller: PLATFORM_CALLER, origin: null },
                                                { reason: "billing", suspended: true });
    await env.DIRECTORY.delete(`o:${who}`);  // as if the record were lost
    await appCallback({ owner: who });
    expect(await sqlIn(who, "SELECT state, reasons FROM owner_admission"))
      .toEqual([{ state: "suspended", reasons: "[\"billing\"]" }]);
  });

  it("is refused for an admit the platform's reserved caller sends at any other door", async () => {
    const who = owner();
    await signInApp({ owner: who });
    expect((await failure(() => env.OWNER.getByName(who).grantList({ owner: who, caller: PLATFORM_CALLER,
                                                      origin: null }))).message).toMatch(/reaches no owner door/);
  });
});

describe("the erasure", () => {
  /** The object's erasure queue as the doors would consume it: each message sent, run. */
  async function consume(who: string): Promise<string[]> {
    const sent = await runInDurableObject(env.OWNER.getByName(who), (instance: OwnerDO) =>
      instance.erasures.sent.splice(0).map((message) => message.owner));
    const done: string[] = [];
    for (const named of sent) {
      done.push(await eraseOutside(doorsEnv, named, () => FAKE_UPSTREAM));
    }
    return done;
  }

  /** The erasure's job, once its retry is due (the test object's 500 ms). */
  async function finish(who: string): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await runDurableObjectAlarm(env.OWNER.getByName(who));
  }

  it("marks the object first; its job's outside record then leaves the name resolving nothing",
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
    expect(await erased.json()).toEqual({ erasing: true });
    expect(erased.headers.getSetCookie().join()).toMatch(/__Host-ta-owner=;/);
    // The mark first: every door refuses from the answer on.
    expect((await failure(() => env.OWNER.getByName(who).grantList(ownerBinding(who)))).message)
      .toBe(ERASING);
    expect(await sqlIn(who, "SELECT outside FROM owner_erasure")).toEqual([{ outside: 0 }]);
    // The queue's message, consumed: the outside record, then the confirmation (the job may
    // send it more than once; each after the first finds the work done).
    expect(await consume(who)).toContain("done");
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
    expect(new URL(again.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    expect(FORGOTTEN).toEqual([who, who]);  // the identity's deletion tried again
    // The job finishes the wipe (the confirmation in), leaving the tombstone alone.
    await finish(who);
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "erased" }]);
    expect((await failure(() => env.OWNER.getByName(who).grantList(ownerBinding(who)))).message)
      .toBe(OWNER_ERASED);
  });

  it("finishes a half-done erasure: a lost message is sent again until the outside record lands",
     async () => {
    const { who, app } = await owned();
    await appPost("account/erase", app.access_token, app.session, { confirm: "erase my account" });
    // The request is gone and its message lost: the object's own job sends again.
    await runInDurableObject(env.OWNER.getByName(who), (instance: OwnerDO) => {
      instance.erasures.sent.splice(0);
    });
    await finish(who);
    // Not wiped without the outside record: the wipe waits for the doors' confirmation.
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "admitted" }]);
    // Locked out meanwhile: the mark refuses sign-in, though no outside record exists yet.
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    const during = await appCallback({ owner: who });
    expect(new URL(during.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    expect(await consume(who)).toContain("done");
    await finish(who);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "erased" });
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "erased" }]);
    // A message after the wipe is done already.
    expect(await eraseOutside(doorsEnv, who, () => FAKE_UPSTREAM)).toBe("wiped");
  });

  it("writes no outside record for an owner no erasure has marked", async () => {
    const { who } = await owned();
    expect((await failure(() => eraseOutside(doorsEnv, who, () => FAKE_UPSTREAM))).message)
      .toBe(NO_ERASURE);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    // The queue's consumer drops it, and a message out of shape, without retrying.
    const acked: string[] = [];
    const retried: string[] = [];
    const message = (body: unknown, id: string) => ({
      id, timestamp: new Date(), attempts: 1, body,
      ack: () => { acked.push(id); }, retry: () => { retried.push(id); } });
    await erasureQueue({ queue: ERASURE_QUEUE, messages: [
      message({ owner: who }, "m1"), message({ owner: who, extra: 1 }, "m2")],
      retryAll: () => undefined, ackAll: () => undefined } as unknown as MessageBatch<unknown>,
                       doorsEnv, () => FAKE_UPSTREAM);
    expect([acked, retried]).toEqual([["m1", "m2"], []]);
  });

  it("retries an erasure message the object refuses for the moment, never drops it",
     async () => {
    const { who } = await owned();
    refuseOnce("erasureOutside", who, slotBusy());
    const acked: string[] = [];
    const retried: string[] = [];
    await erasureQueue({ queue: ERASURE_QUEUE, messages: [{
      id: "m1", timestamp: new Date(), attempts: 1, body: { owner: who },
      ack: () => { acked.push("m1"); }, retry: () => { retried.push("m1"); } }],
      retryAll: () => undefined, ackAll: () => undefined } as unknown as MessageBatch<unknown>,
                       doorsEnv, () => FAKE_UPSTREAM);
    expect([acked, retried]).toEqual([[], ["m1"]]);
  });

  it("retries an outside record whose identity deletion fails, and finishes on the next try",
     async () => {
    const { who, app } = await owned();
    await appPost("account/erase", app.access_token, app.session, { confirm: "erase my account" });
    const sent = await runInDurableObject(env.OWNER.getByName(who), (instance: OwnerDO) =>
      instance.erasures.sent.splice(0));
    const failing = { ...FAKE_UPSTREAM, forget: () => Promise.reject(new Error("WorkOS down")) };
    const retried: string[] = [];
    await erasureQueue({ queue: ERASURE_QUEUE, messages: [{
      id: "m1", timestamp: new Date(), attempts: 1, body: sent[0],
      ack: () => undefined, retry: () => { retried.push("m1"); } }],
      retryAll: () => undefined, ackAll: () => undefined } as unknown as MessageBatch<unknown>,
                       doorsEnv, () => failing);
    expect(retried).toEqual(["m1"]);
    expect(await sqlIn(who, "SELECT outside FROM owner_erasure")).toEqual([{ outside: 0 }]);
    expect(await eraseOutside(doorsEnv, who, () => FAKE_UPSTREAM)).toBe("done");
    await finish(who);
    expect(await sqlIn(who, "SELECT state FROM owner_admission")).toEqual([{ state: "erased" }]);
  });

  it("closes the crash window: a wiped object that lost its tombstone is pending, never admitted",
     async () => {
    const { who, app } = await owned();
    await appPost("account/erase", app.access_token, app.session, { confirm: "erase my account" });
    await consume(who);
    // The crash the window names: the wipe durable, the tombstone lost.
    await runInDurableObject(env.OWNER.getByName(who), async (_instance: OwnerDO, state) => {
      await state.storage.deleteAll();
    });
    await evictDurableObject(env.OWNER.getByName(who));
    const remade = env.OWNER.get(env.OWNER.idFromName(who));
    expect((await failure(() => remade.grantList(ownerBinding(who)))).message)
      .toMatch(OWNER_UNADMITTED);
    // And no sign-in admits the name again: the directory's record refuses it first.
    const again = await appCallback({ owner: who });
    expect(new URL(again.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    expect((await failure(() => remade.grantList(ownerBinding(who)))).message)
      .toMatch(OWNER_UNADMITTED);
  });

  it("erases nothing without the session or the owner's words", async () => {
    const { who, app } = await owned();
    for (const [session, body] of [[null, { confirm: "erase my account" }],
                                   [app.session, { confirm: "yes" }],
                                   [app.session, {}]] as const) {
      const refused = await appPost("account/erase", app.access_token, session, body);
      expect(refused.status).toBeGreaterThanOrEqual(400);
    }
    expect(await sqlIn(who, "SELECT COUNT(*) AS n FROM owner_erasure")).toEqual([{ n: 0 }]);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
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

  it("turns a key off by its grant row, as the app does, its directory entry gone too",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const minted = await json<{ key: string; key_id: string }>(
      await appPost("keys", app.access_token, app.session, { name: "laptop", scopes: SCOPES }));
    const [row] = (await env.OWNER.getByName(who).grantList(ownerBinding(who))).clients;
    expect(row!.source).toBe("api_key");
    expect(await env.DIRECTORY.get(`k:${minted.key_id}`)).not.toBeNull();
    const off = await appPost("grants/revoke", app.access_token, null, { client: row!.client });
    expect(off.status).toBe(200);
    expect(await off.json()).toMatchObject({ client: row!.client, revoked: true });
    expect(await env.DIRECTORY.get(`k:${minted.key_id}`)).toBeNull();
    const [key] = (await json<{ keys: { revoked_at: number | null }[] }>(
      await appPost("keys/list", app.access_token, null, {}))).keys;
    expect(key!.revoked_at).toEqual(expect.any(Number));
    const refused = await call(new Request(url("/api/tools/agents_list"), {
      method: "POST", headers: { Authorization: `Bearer ${minted.key}`,
                                 "Content-Type": "application/json" }, body: "{}" }));
    expect(refused.status).toBe(401);
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

/**
 * A shared app's install for `who`, as its owed install flow will write it: the object's
 * row (no door makes one before that flow's proof) and the directory's route.
 */
async function slackInstall(who: string, team: string): Promise<string> {
  const route = `i:slack:workspace:${createHash("sha256").update(team).digest("hex")}`;
  const id = crypto.randomUUID().replace(/-/g, "");
  await sqlIn(who, "INSERT INTO inbound_bindings (binding_id, kind, agent, enabled, revision, " +
              "route, created_at, changed_at) VALUES (?, 'shared', 'main', 1, 1, ?, ?, ?)",
              id, route, now(), now());
  await env.DIRECTORY.put(route, JSON.stringify({ o: who, b: id, r: 1 }));
  return id;
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

  /** An event to a capability URL: its id, its bytes, and its own time (by default, now). */
  function event(hookUrl: string, id: string, body = "{\"x\":1}",
                 headers: Record<string, string> = {}, at: number = now()): Request {
    return new Request(hookUrl, { method: "POST", body, headers: {
      "Idempotency-Key": id, "X-Event-Timestamp": String(at), ...headers } });
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
    // A replay is the same event: its id, its bytes and its own time. (Stamped afresh, a
    // second's tick between the two calls made it another event under the same id, so
    // `event_conflict`, 409: the envelope the object dedupes on carries the event's time.)
    const at = now();
    const first = await json<{ receipt: string }>(await call(event(hookUrl, "evt-r",
                                                                   undefined, {}, at)));
    const replay = await call(event(hookUrl, "evt-r", undefined, {}, at));
    expect(replay.status).toBe(202);
    expect(await replay.json()).toMatchObject({ receipt: first.receipt, deduped: true });
    expect(await mail(who)).toHaveLength(1);
    const conflict = await call(event(hookUrl, "evt-r", "{\"other\":true}", {}, at));
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
    // A gate that cannot be read, or the owner's maintenance gate: `unavailable`, a minute.
    const closed = async (response: Response) =>
      [response.status, response.headers.get("Retry-After"), await response.json()];
    await writeControl(env.CONTROL, JSON.stringify({ outside: "on" }));
    expect(await closed(await call(event(hookUrl, "c3"))))
      .toEqual([503, "60", { error: "unavailable" }]);
    await setControl({ ...OPEN, inbound: true, ordinary: "none" });
    expect(await closed(await call(event(hookUrl, "c4"))))
      .toEqual([503, "60", { error: "unavailable" }]);
  });

  it("takes a body up to the platform's one request ceiling, and refuses one past it",
     async () => {
    const { url: hookUrl } = await hooked();
    const over = await call(event(hookUrl, "big", "x".repeat(EGRESS_BODY_MAX + 1)));
    expect(over.status).toBe(413);
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

  it("is never claimed by an owner: no door makes a shared binding before its install flow",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const route = `i:slack:workspace:${createHash("sha256").update("TOTHER").digest("hex")}`;
    expect((await failure(() => env.OWNER.getByName(who).inboundCreate(
      { owner: who, caller: who, origin: null, session: app.session },
      { kind: "shared", agent: "main", route }))).message).toBe(INSTALL_PROOF);
    expect(await sqlIn(who, "SELECT COUNT(*) AS n FROM inbound_bindings")).toEqual([{ n: 0 }]);
  });

  it("dedupes a replayed event inside the skew, and no-ops an install no owner made", async () => {
    const who = owner();
    await signInApp({ owner: who });
    await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    const retried = slackEvent(team, "EvR");  // a retry carries the same envelope
    expect((await call(slackRequest(retried))).status).toBe(202);
    const replay = await call(slackRequest(retried));
    expect(await replay.json()).toMatchObject({ state: "admitted", deduped: true });
    expect(await mail(who)).toHaveLength(1);
    const stranger = await call(slackRequest(slackEvent("TNOBODY", "EvS")));
    expect(stranger.status).toBe(200);
    expect(await stranger.json()).toEqual({ state: "no_op" });
    const shared = await call(slackRequest({ ...slackEvent(team, "EvX"),
                                             is_ext_shared_channel: true }));
    expect(await shared.json()).toEqual({ state: "no_op" });
  });

  it("is told to send an event again at the hot ceiling, whether it clears by itself or is " +
     "held by a full storage (review E-2)", async () => {
    const who = owner();
    await signInApp({ owner: who });
    await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    for (const blocked of [0, ...Array<number>(BREAKER_FAILURES + 1).fill(1)]) {
      // The hot ceiling; blocked, the archive's moves wait for the owner to free space.
      await sqlIn(who, "UPDATE ledger_usage SET messages = ?, archive_blocked = ?",
                  LEDGER_LIMITS.maxMessages, blocked);
      const refused = await call(slackRequest(slackEvent(team, `EvC${crypto.randomUUID()}`)));
      expect([refused.status, refused.headers.get("Retry-After"), await refused.json()])
        .toEqual([429, "5", { state: "refused", error: "mailbox_full", retry: true }]);
    }
    // Never the breaker's: more refusals than trip it, and the events still told to retry.
    const { notices } = await env.OWNER.getByName(who).noticeList(ownerBinding(who));
    expect(notices.map((notice) => notice.type)).not.toContain("inbound");
  });

  it("trips its breaker after the agent's mailbox refuses it, then no-ops until re-armed",
     async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    await defineAgent(env.OWNER.getByName(who), ownerBinding(who), "main", SIGNED_IN);
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    const bindingId = await slackInstall(who, team);
    expect((await call(slackRequest(slackEvent(team, "EvB0")))).status).toBe(202);
    // Too much mail unread, owner-wide (slice STORE item 2: one ceiling, no mailbox's own).
    await sqlIn(who, "UPDATE ledger_usage SET unacked = 1000000");
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
    await sqlIn(who, "UPDATE ledger_usage SET unacked = (SELECT COUNT(*) FROM " +
                     "ledger_messages WHERE unacked > 0)");
    const set = await appPost("hooks/set", app.access_token, app.session,
                              { binding_id: bindingId, expected_revision: 1, enabled: true });
    expect(set.status).toBe(200);
    expect((await call(slackRequest(slackEvent(team, "EvA")))).status).toBe(202);
  });
});

describe("no caller is counted or capped by the doors (slice L's review)", () => {
  let fetched: string[] = [];
  let spy: { mockRestore(): void } | undefined;

  beforeEach(() => {
    fetched = [];
    const real = globalThis.fetch;
    spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const request = new Request(input as RequestInfo, init);
      if (/attacker-\d+\.example/i.test(request.url)) {
        fetched.push(request.url);
        return new Response("nope", { status: 404 });
      }
      return real(input as RequestInfo, init);
    });
  });
  afterEach(() => spy?.mockRestore());

  /** More failures from one address than the removed failure bound (115) allowed. */
  const MANY = 117;
  /** A credential of the right shape that no grant holds. */
  const stale = () => `${owner()}:${"a".repeat(16)}:${"b".repeat(32)}`;

  it("serves a valid key from an address that sent many bad MCP bearers (review N1, attack 1)",
     async () => {
    const app = await signInApp({ owner: owner() });
    const minted = await json<{ key: string }>(
      await appPost("keys", app.access_token, app.session, { name: "k", scopes: SCOPES }));
    const backend = `198.51.100.250-${crypto.randomUUID()}`;
    for (let i = 0; i < MANY; i++) {
      const answer = await call(new Request(url("/mcp"), {
        method: "POST", body: "{}", headers: { "CF-Connecting-IP": backend,
          "Content-Type": "application/json", Accept: "application/json, text/event-stream",
          Authorization: `Bearer ${stale()}` } }));
      expect(answer.status).toBe(401);
    }
    const valid = await call(new Request(url("/api/tools/agents_list"), {
      method: "POST", body: "{}", headers: { "CF-Connecting-IP": backend,
        Authorization: `Bearer ${minted.key}`, "Content-Type": "application/json" } }));
    expect(valid.status).toBe(200);
  });

  it("serves a live refresh from an address that sent many failed refreshes (review N1, " +
     "attack 2)", async () => {
    const client = await registerClient();
    const backend = `198.51.100.251-${crypto.randomUUID()}`;
    const live = await connect(client, { owner: owner() });
    for (let i = 0; i < MANY; i++) {
      const failed = await call(new Request(url("/oauth/token"), {
        method: "POST", headers: { "CF-Connecting-IP": backend },
        body: new URLSearchParams({ grant_type: "refresh_token", client_id: client.clientId,
                                    refresh_token: stale(), resource: MCP_RESOURCE }) }));
      expect(failed.status).toBeGreaterThanOrEqual(400);
      expect(failed.status).not.toBe(429);
    }
    const refreshed = await call(new Request(url("/oauth/token"), {
      method: "POST", headers: { "CF-Connecting-IP": backend },
      body: new URLSearchParams({ grant_type: "refresh_token", client_id: client.clientId,
                                  refresh_token: live.refresh_token }) }));
    expect(refreshed.status).toBe(200);
  });

  it("refuses no sign-in start and no bad key by count, from one address", async () => {
    const ip = `198.51.100.9-${crypto.randomUUID()}`;
    for (let i = 0; i < 40; i++) {
      const start = await call(new Request(url("/authorize?response_type=code"), {
        headers: { ...NAVIGATE, "CF-Connecting-IP": ip } }));
      expect(start.status).not.toBe(429);
      const key = await call(new Request(url("/api/tools/agents_list"), {
        method: "POST", body: "{}", headers: { "Content-Type": "application/json",
          "CF-Connecting-IP": ip,
          Authorization: `Bearer ta_key_${i.toString(16).padStart(16, "0")}_${"a".repeat(43)}` } }));
      expect(key.status).toBe(401);
    }
  });

  it("sends at most one metadata fetch out for each /authorize in (the provider's bound)",
     async () => {
    const ip = `203.0.113.7-${crypto.randomUUID()}`;
    const sent = 30;
    for (let i = 0; i < sent; i++) {
      const params = new URLSearchParams({
        response_type: "code", client_id: `https://attacker-${i}.example/client.json`,
        redirect_uri: "https://attacker.example/cb", state: "s", code_challenge: "c".repeat(43),
        code_challenge_method: "S256", resource: MCP_RESOURCE });
      const answer = await call(new Request(url(`/authorize?${params}`), {
        headers: { ...NAVIGATE, "CF-Connecting-IP": ip } }));
      expect(answer.status).not.toBe(429);
    }
    expect(fetched.length).toBeLessThanOrEqual(sent);
  });

  it("answers an overloaded owner object 429 with Retry-After, never retried", async () => {
    const app = await signInApp({ owner: owner() });
    const busy = Object.assign(new Error("Durable Object is overloaded"), { overloaded: true });
    const stub = vi.spyOn(doorsEnv.OWNER, "getByName").mockImplementation(() => ({
      keyList: () => Promise.reject(busy) }) as never);
    const answer = await appPost("keys/list", app.access_token, null, {});
    stub.mockRestore();
    expect(answer.status).toBe(429);
    expect(answer.headers.get("Retry-After")).toBe("1");
  });
});

describe("the file door's bytes", () => {
  const CHUNK = 4 * 1024 * 1024;
  const FILE = new Uint8Array(CHUNK + 4096).map((_byte, i) => (i * 131 + 7) % 251);
  const pieces = [FILE.subarray(0, CHUNK), FILE.subarray(CHUNK)];

  async function begun() {
    const { who, app } = await owned();
    const chunks = pieces.map((piece) => createHash("sha256").update(piece).digest("hex"));
    const begin = await appPost("files/upload/begin", app.access_token, null,
                                { size: FILE.byteLength, chunks });
    expect(begin.status).toBe(200);
    const { intent_id } = await json<{ intent_id: string }>(begin);
    return { who, app, intent_id };
  }

  function put(token: string, intent: string, index: number, body: Uint8Array,
               headers: Record<string, string> = {}): Promise<Response> {
    return call(new Request(url(`/app/api/files/upload/${intent}/${index}`), {
      method: "PUT", body, headers: { Authorization: `Bearer ${token}`,
                                      "Content-Type": "application/octet-stream", ...headers } }));
  }

  it("reach the files Worker's upload entrypoint alone: no read and no key call (review M5-1)",
     async () => {
    const files = (env as unknown as {
      FILES: Record<string, (...args: unknown[]) => Promise<unknown>> }).FILES;
    for (const method of ["getChunks", "getManifestRoot", "getManifestPart", "getManifestSegment",
                          "rewrapKey", "mintKey", "deleteNamespace"]) {
      const refused = await Promise.resolve().then(() => files[method]!("0".repeat(32), "k"))
        .then(() => null, (error: unknown) => String(error));
      expect([method, refused]).toEqual([method, expect.stringMatching(/does not implement/)]);
    }
  });

  it("takes one chunk a PUT, in any order, resumes, serves each Content-Digest, and commits",
     async () => {
    const { who, app, intent_id } = await begun();
    const second = await put(app.access_token, intent_id, 1, pieces[1]!);
    expect(second.status).toBe(200);
    expect(await second.json()).toEqual({ intent_id, index: 1, stored: true, pending: 1 });
    const digest = createHash("sha256").update(pieces[0]!).digest();
    const first = await put(app.access_token, intent_id, 0, pieces[0]!,
                            { "Content-Digest": contentDigest(digest) });
    expect(first.headers.get("Content-Digest")).toBe(contentDigest(digest));
    expect(await first.json()).toEqual({ intent_id, index: 0, stored: true, pending: 0 });
    // Sent again after a failure: answered, never put twice.
    expect(await (await put(app.access_token, intent_id, 0, pieces[0]!)).json())
      .toMatchObject({ stored: false, pending: 0 });
    const committed = await appPost("files/upload/commit", app.access_token, null, {
      intent_id, path: "/workspace/big.bin", expected_etag: null });
    expect(committed.status).toBe(200);
    const read = await env.OWNER.getByName(who).fileRead(ownerBinding(who),
                                                        { path: "/workspace/big.bin" });
    expect(read.entry.size).toBe(FILE.byteLength);
  });

  it("refuses bytes that are not the declared chunk, a short or long chunk, and no such index",
     async () => {
    const { app, intent_id } = await begun();
    const wrong = pieces[0]!.slice();
    wrong[10] = wrong[10]! ^ 0xff;
    const refused = await put(app.access_token, intent_id, 0, wrong);
    expect(refused.status).toBe(400);
    expect(await refused.json()).toEqual({
      error: "the bytes of chunk 0 are not the chunk the upload declared" });
    expect((await put(app.access_token, intent_id, 1, pieces[1]!.subarray(0, 10))).status)
      .toBe(400);
    const longer = new Uint8Array(pieces[1]!.byteLength + 1);
    longer.set(pieces[1]!);
    expect((await put(app.access_token, intent_id, 1, longer)).status).toBe(400);
    expect((await put(app.access_token, intent_id, 2, pieces[1]!)).status).toBe(404);
  });

  it("answers a refusal mid-upload with its fixed code, and refuses an outside credential",
     async () => {
    const { app, intent_id } = await begun();
    await appPost("files/upload/abort", app.access_token, null, { intent_id });
    const gone = await put(app.access_token, intent_id, 0, pieces[0]!);
    expect(gone.status).toBe(403);
    expect(await gone.json()).toMatchObject({ error: expect.stringMatching(/no such upload/) });
    const other = contentDigest(createHash("sha256").update("other").digest());
    const { app: second, intent_id: next } = await begun();
    expect((await put(second.access_token, next, 0, pieces[0]!, { "Content-Digest": other }))
      .status).toBe(400);
    expect((await put("ta_key_0000000000000000_" + "a".repeat(43), next, 0, pieces[0]!))
      .status).toBe(403);
  });
});
