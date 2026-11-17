/**
 * Slice L's doors, in workerd through the real owner Worker: the platform's explicit admit
 * at sign-up and the erasure's outside record (an erased owner's name resolving nothing
 * again), whether the owner's own door or the operator's `owner.erase` began it; the key
 * store; `/hooks/inbound`'s two shapes (a bad HMAC, a replay, the gate, the breaker); no
 * caller counted or capped by the doors (slice L's review, N1); and the file door's bytes,
 * streamed to `FILES` by the binder.
 */
import { createHash, createHmac } from "node:crypto";

import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { parseRing } from "../../contract/src/envelope";
import {
  type OperatorAnswer, operatorAnswer, type OperatorArgs, signOperator,
} from "../../contract/src/operator-message";
import { ERASE_CONFIRM, eraseWords } from "../../contract/src/owner";
import { Refusal } from "../../contract/src/refusal";
import { QUEUES } from "../../contract/src/queue-names";
import { MCP_RESOURCE } from "../../contract/src/identity";
import { now } from "../src/config";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { readOwner, readRoute } from "../src/directory";
import { eraseOutside, erasureQueue } from "../src/erase";
import { contentDigest } from "../src/file-bytes";
import { sharedApps } from "../src/inbound";
import { keyring, writeControl } from "../../ci/fixtures";
import { slotBusy } from "../../contract/src/slots";
import { until } from "../../ci/workerd";
import {
 admissionOf, appCallback, appPost, call, connect, crossed, defineMain, deliver, doorsEnv,
 failNextForget, forgotten, held,
 NAVIGATE, OPEN,
 grantsOf, ownDoor, refresh, refuseOnce, registerClient, serveFirstPartyDocument, setControl,
 signInApp,
 url,
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
  await defineMain(who, app, SIGNED_IN);
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
    expect(await admissionOf(who)).toMatchObject({ state: "pending" });
    expect(await readOwner(doorsEnv, who)).toBeNull();
    const app = await signInApp({ owner: who });
    expect(await readOwner(doorsEnv, who)).toEqual({ s: "admitted", t: expect.any(Number) });
    expect(await admissionOf(who)).toMatchObject({ state: "admitted", reasons: [] });
    expect((await appPost("keys/list", app.access_token, null, {})).status).toBe(200);
  });

  it("admits an owner who first signs in through an MCP client", async () => {
    const who = owner();
    await connect(await registerClient(), { owner: who });
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    expect(await admissionOf(who)).toMatchObject({ state: "admitted", reasons: [] });
  });

  it("never lifts a suspension when the owner signs in again", async () => {
    const who = owner();
    await signInApp({ owner: who });
    await doorsEnv.OWNER.getByName(who).admissionSet({ owner: who, caller: PLATFORM_CALLER, origin: null },
                                                { reason: "billing", suspended: true });
    await env.DIRECTORY.delete(`o:${who}`);  // as if the record were lost
    await appCallback({ owner: who });
    expect(await admissionOf(who)).toMatchObject({ state: "suspended", reasons: ["billing"] });
  });

});

describe("the erasure", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /**
   * `who`'s outside record made: the erasure message the object sent, consumed by the doors'
   * own entry, until the consumer's confirmation (`erasureOutsideDone`) crosses the owner gate.
   */
  async function confirmed(who: string): Promise<void> {
    await until(async () => (await crossed(who, "erasureOutsideDone")).length > 0,
                `${who}'s outside record`);
  }

  /** `who`'s next erasure message held at the owner gate, before the consumer reads its names. */
  async function holdOutside(who: string): Promise<void> {
    await env.OWNER_GATE.arm({ method: "erasureOutside", name: who, hold: "before" });
  }

  /** The id of `who`'s held erasure message, once the consumer has brought it to the gate. */
  async function heldOutside(who: string): Promise<number> {
    let id: number | undefined;
    await until(async () => {
      id = (await env.OWNER_GATE.held()).find((call) => call.name === who)?.id;
      return id !== undefined;
    }, `${who}'s erasure message at the gate`);
    return id!;
  }

  /** `who`'s held erasure message let go, and its outside record made. */
  async function letGo(who: string): Promise<void> {
    await env.OWNER_GATE.release(await heldOutside(who));
    await confirmed(who);
  }

  // The doors' work ends with the outside record and its confirmation: the wipe after the
  // put grace, on the object's own clock, is the owner's test (`owner/test/erasure.test.ts`).

  const RING = parseRing(doorsEnv.OPERATOR_KEYRING)!;

  /** `op` with `args` for `who`, signed under `ring` and delivered once; the bucket's records. */
  async function operated(who: string, op: string, args: OperatorArgs, ring = RING) {
    const message = await signOperator(ring, op, who, args, now());
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    const records = await held(message.ask);
    return { ...records, answer: records.answer === null ? null
      : operatorAnswer(records.answer, message) as OperatorAnswer & Record<string, unknown> };
  }

  /** The two ways an erasure begins: the owner's own door, in its session, and the operator's. */
  const BEGINS = [
    ["the owner's own door", async (who: string, app: Awaited<ReturnType<typeof signInApp>>) => {
      const erased = await appPost("account/erase", app.access_token, app.session,
                                   { confirm: ERASE_CONFIRM });
      expect(erased.status).toBe(200);
      expect(await erased.json()).toEqual({ erasing: true });
      expect(erased.headers.getSetCookie().join()).toMatch(/__Host-ta-owner=;/);
    }],
    // The email route: an owner who cannot sign in, erased by the operator channel's signed
    // message and its words; the operator log keeps the ask and its answer.
    ["the operator's owner.erase", async (who: string) => {
      const { accepted, answer } = await operated(who, "owner.erase",
                                                  { confirm: eraseWords(who) });
      expect(accepted).toMatchObject({ op: "owner.erase", owner: who });
      expect(answer).toMatchObject({ op: "owner.erase", owner: who,
                                     args: { confirm: eraseWords(who) },
                                     result: { erasing: true } });
    }],
  ] as const;

  it.each(BEGINS)("begun at %s, marks the object first; its job's outside record then leaves " +
                  "the name resolving nothing", async (_how, begin) => {
    const { who, app } = await owned();
    // A key, an inbound URL and an MCP client, each a way back to the owner.
    const minted = await json<{ key: string; key_id: string }>(
      await appPost("keys", app.access_token, app.session, { name: "laptop", scopes: SCOPES }));
    const hook = await json<{ url: string; route: string }>(
      await appPost("hooks/create", app.access_token, app.session, { agent: "main" }));
    const client = await registerClient();
    const tokens = await connect(client, { owner: who });
    await holdOutside(who);
    await begin(who, app);
    // The mark first: every door refuses from the answer on, while no outside record is in.
    await expect(ownDoor(who, "grantList")).rejects.toMatchObject({ code: "erasing" });
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    expect(await forgotten(who)).toEqual([]);
    // The queue's message, consumed by the doors' own entry: the outside record, then the
    // confirmation.
    await letGo(who);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "erased" });
    expect(await env.DIRECTORY.get(`k:${minted.key_id}`)).toBeNull();
    expect(await readRoute(doorsEnv, hook.route)).toBeNull();
    expect(await forgotten(who)).toEqual([204]);
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
    expect(await forgotten(who)).toEqual([204, 204]);  // the identity's deletion tried again
  });

  // That the object's own job sends a lost message again, and wipes only once the outside
  // record is in, is the owner's test (`owner/test/erasure.test.ts`).
  it("finishes a half-done erasure: the mark locks the owner out until its message's outside " +
     "record lands, and one after the wipe finds it done", async () => {
    const { who, app } = await owned();
    await holdOutside(who);
    await appPost("account/erase", app.access_token, app.session, { confirm: ERASE_CONFIRM });
    // Locked out meanwhile: the mark refuses sign-in, though no outside record exists yet.
    await heldOutside(who);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    const during = await appCallback({ owner: who });
    expect(new URL(during.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    // The message consumed.
    await letGo(who);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "erased" });
    // A message after the wipe: the object names nothing left to erase, and nothing is done.
    await env.OWNER_GATE.arm({ method: "erasureOutside", name: who, answer: null });
    expect(await eraseOutside(doorsEnv, who)).toBe("wiped");
    expect(await crossed(who, "erasureOutsideDone")).toEqual([]);
  });

  it("writes no outside record for an owner no erasure has marked", async () => {
    const { who } = await owned();
    await expect(eraseOutside(doorsEnv, who)).rejects.toMatchObject({ code: "refused" });
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
    // The queue's consumer drops it, and a message out of shape, without retrying.
    const acked: string[] = [];
    const retried: string[] = [];
    const message = (body: unknown, id: string) => ({
      id, timestamp: new Date(), attempts: 1, body,
      ack: () => { acked.push(id); }, retry: () => { retried.push(id); } });
    await erasureQueue({ queue: QUEUES.ERASURES, messages: [
      message({ owner: who }, "m1"), message({ owner: who, extra: 1 }, "m2")],
      retryAll: () => undefined, ackAll: () => undefined } as unknown as MessageBatch<unknown>,
                       doorsEnv);
    expect([acked, retried]).toEqual([["m1", "m2"], []]);
  });

  it("retries an erasure message the object refuses for the moment, never drops it",
     async () => {
    const { who } = await owned();
    await refuseOnce("erasureOutside", who, slotBusy());
    const acked: string[] = [];
    const retried: string[] = [];
    await erasureQueue({ queue: QUEUES.ERASURES, messages: [{
      id: "m1", timestamp: new Date(), attempts: 1, body: { owner: who },
      ack: () => { acked.push("m1"); }, retry: () => { retried.push("m1"); } }],
      retryAll: () => undefined, ackAll: () => undefined } as unknown as MessageBatch<unknown>,
                       doorsEnv);
    expect([acked, retried]).toEqual([[], ["m1"]]);
  });

  it("retries an outside record whose identity deletion fails, and finishes on the next try",
     async () => {
    const { who, app } = await owned();
    // The object's own message waits at the gate while a delivery of it fails at the issuer.
    await holdOutside(who);
    await appPost("account/erase", app.access_token, app.session, { confirm: ERASE_CONFIRM });
    await heldOutside(who);
    await failNextForget(who);
    const retried: string[] = [];
    await erasureQueue({ queue: QUEUES.ERASURES, messages: [{
      id: "m1", timestamp: new Date(), attempts: 1, body: { owner: who },
      ack: () => undefined, retry: () => { retried.push("m1"); } }],
      retryAll: () => undefined, ackAll: () => undefined } as unknown as MessageBatch<unknown>,
                       doorsEnv);
    expect(retried).toEqual(["m1"]);
    expect(await forgotten(who)).toEqual([503]);
    expect(await crossed(who, "erasureOutsideDone")).toEqual([]);
    // The next delivery finishes it.
    await letGo(who);
    expect(await forgotten(who)).toEqual([503, 204]);
  });

  // That a wiped object that lost its tombstone starts pending, as any new object does, is
  // the owner's test (`owner/test/admit.test.ts`, `erasure.test.ts`); here, the doors' half.
  it("closes the crash window: no sign-in admits an erased name, the directory's record " +
     "refusing it before the object is asked", async () => {
    const { who, app } = await owned();
    await appPost("account/erase", app.access_token, app.session, { confirm: ERASE_CONFIRM });
    await confirmed(who);
    await env.OWNER_GATE.seen(who);
    const again = await appCallback({ owner: who });
    expect(new URL(again.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
    expect(await crossed(who, "admissionSet")).toEqual([]);
  });

  it("erases by the operator only an owner the platform signed up, by a message its ring " +
     "signed, with its exact words, at the platform's own door", async () => {
    const { who } = await owned();
    const other = owner();
    for (const [args, refused] of [
      [{}, { code: "invalid", message: "owner.erase takes exactly confirm" }],
      [{ confirm: ERASE_CONFIRM }, { code: "invalid" }],
      [{ confirm: eraseWords(other) }, { code: "invalid" }],
      [{ confirm: `${eraseWords(who)} ` }, { code: "invalid" }],
      [{ confirm: eraseWords(who).toUpperCase() }, { code: "invalid" }],
    ] as const) {
      const { answer } = await operated(who, "owner.erase", args);
      expect(answer?.["refused"], JSON.stringify(args)).toMatchObject(refused);
    }
    // Another ring's MAC, or none: dropped at the gate, nothing kept in the log.
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(await operated(who, "owner.erase", { confirm: eraseWords(who) },
                          parseRing(keyring())!)).toEqual({ accepted: null, answer: null });
    const signed = await signOperator(RING, "owner.erase", who, { confirm: eraseWords(who) },
                                      now());
    const { mac: _mac, ...unsigned } = signed;
    expect(await deliver(unsigned)).toEqual({ acked: 1, retried: 0 });
    expect(await held(signed.ask)).toEqual({ accepted: null, answer: null });
    expect(logged.mock.calls.map((each) => String(each[0]))).toEqual([
      "an operator message has a MAC that does not hold under its kid; dropped",
      "an operator message is out of shape; dropped"]);
    logged.mockRestore();
    // A name the platform never signed up, with its own words: never barred.
    expect((await operated(other, "owner.erase", { confirm: eraseWords(other) }))
      .answer?.["refused"]).toEqual({
        code: "refused",
        message: "the platform holds no owner of that name: only an owner who signed up is erased",
      });
    expect(await readOwner(doorsEnv, other)).toBeNull();
    // No erasure began: the owner's state is its own, and the other's object never asked.
    expect(await admissionOf(who)).toMatchObject({ state: "admitted", reasons: [] });
    expect(await crossed(other, "erasureBegin")).toEqual([]);
    expect(await readOwner(doorsEnv, who)).toMatchObject({ s: "admitted" });
  });

  it("answers the operator's erasure asked again by the state it left: erasing, then erased",
     async () => {
    const { who } = await owned();
    await holdOutside(who);
    await BEGINS[1][1](who);
    expect((await operated(who, "owner.erase", { confirm: eraseWords(who) }))
      .answer?.["refused"]).toMatchObject({ code: "erasing" });
    await letGo(who);
    expect((await operated(who, "owner.erase", { confirm: eraseWords(who) }))
      .answer?.["refused"]).toEqual({ code: "owner_erased", message: "the owner is erased" });
  });

  it("answers owner.state at the platform's door; for an owner being erased erasing, " +
     "and once erased owner_erased", async () => {
    const { who } = await owned();
    expect((await operated(who, "owner.state", {})).answer).toMatchObject({
      result: { state: "admitted", restore: null } });
    await holdOutside(who);
    await BEGINS[1][1](who);
    expect((await operated(who, "owner.state", {})).answer?.["refused"])
      .toMatchObject({ code: "erasing" });
    await letGo(who);
    expect((await operated(who, "owner.state", {})).answer?.["refused"])
      .toEqual({ code: "owner_erased", message: "the owner is erased" });
  });

  it("answers a key rotation's owner.rewrap and owner.reseal at the platform's doors",
     async () => {
    const { who } = await owned();
    for (const op of ["owner.rewrap", "owner.reseal"]) {
      expect((await operated(who, op, {})).answer, op).toMatchObject({ result: { left: 0 } });
    }
  });

  it("erases nothing without the session or the owner's words", async () => {
    const { who, app } = await owned();
    for (const [session, body] of [[null, { confirm: ERASE_CONFIRM }],
                                   [app.session, { confirm: "yes" }],
                                   [app.session, {}]] as const) {
      const refused = await appPost("account/erase", app.access_token, session, body);
      expect(refused.status).toBeGreaterThanOrEqual(400);
    }
    expect(await admissionOf(who)).toMatchObject({ state: "admitted", reasons: [] });
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
    const [row] = await grantsOf(who);
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
    const stub = doorsEnv.OWNER.getByName(who);
    const change = { type: "api_key_request", scopes: SCOPES };
    const proposed = (request_id: string) =>
      ownDoor(who, "policyProposal", { request_id });
    await ownDoor(who, "policyPropose", { change, request_id: "card-key-1" });
    const { digest } = await proposed("card-key-1");
    const proposal = { request_id: "card-key-1", digest };
    const first = await appPost("keys", app.access_token, app.session,
                                { name: "agent key", scopes: SCOPES, proposal });
    expect(first.status).toBe(200);
    const again = await appPost("keys", app.access_token, app.session,
                                { name: "agent key", scopes: SCOPES, proposal });
    expect(again.status).toBe(403);
    expect(await again.json()).toEqual({ error: "proposal is not the pending card shown" });
    const other = [{ universe: "cc", agent: "main", capability: "write_graph" }];
    await ownDoor(who, "policyPropose", { change, request_id: "card-key-2" });
    const held = await proposed("card-key-2");
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
 * A shared app's install for `who`, as its owed install flow will write it: the directory's
 * route to a binding of the object's. No door makes the object's row before that flow's proof
 * (`owner/test/admit.test.ts`), so here the object's answers to its events are the owner
 * gate's, armed by each test (`accepts`): what the object does with an event is the owner's
 * test (`admit.test.ts`, "an inbound event at the object's door").
 */
async function slackInstall(who: string, team: string): Promise<string> {
  const route = `i:slack:workspace:${createHash("sha256").update(team).digest("hex")}`;
  const id = crypto.randomUUID().replace(/-/g, "");
  await env.DIRECTORY.put(route, JSON.stringify({ o: who, b: id, r: 1 }));
  return id;
}

/** `who`'s object's next `times` answers to an inbound event, `answer`, at the owner gate. */
async function accepts(who: string, answer: unknown, times = 1): Promise<void> {
  await env.OWNER_GATE.arm({ method: "inboundAccept", name: who, answer, times });
}

/** The object's answer to an event it took: mailed, its receipt. */
const ADMITTED = Object.freeze({ state: "admitted", receipt: "m".repeat(32), deduped: false });

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
    // The object is handed the event's bytes as they came (that it mails them verbatim is the
    // owner's test, `owner/test/admit.test.ts`).
    const [taken] = await crossed(who, "inboundAccept");
    expect(taken!.binding).toMatchObject({ inbound: { binding: binding_id, revision: 1 } });
    expect(taken!.request).toMatchObject({
      event_id: "evt-1", body: { bytes: new TextEncoder().encode(bytes).byteLength } });
  });

  it("answers a replayed event deduped, with its receipt, and mails once", async () => {
    const { url: hookUrl } = await hooked();
    // A replay is the same event: its id, its bytes and its own time. (Stamped afresh, a
    // second's tick between the two calls made it another event under the same id, so
    // `event_conflict`, 409: the envelope the object dedupes on carries the event's time.)
    const at = now();
    const first = await json<{ receipt: string }>(await call(event(hookUrl, "evt-r",
                                                                   undefined, {}, at)));
    const replay = await call(event(hookUrl, "evt-r", undefined, {}, at));
    expect(replay.status).toBe(202);
    expect(await replay.json()).toMatchObject({ receipt: first.receipt, deduped: true });
    const conflict = await call(event(hookUrl, "evt-r", "{\"other\":true}", {}, at));
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toEqual({ error: "event_conflict" });
  });

  it("answers the object's refused answer as the door answers any refusal: its code's " +
     "status and wait, and {error}", async () => {
    const { who, url: hookUrl } = await hooked();
    await accepts(who, { state: "refused", error: "mailbox_full" });
    const full = await call(event(hookUrl, "evt-f"));
    expect([full.status, full.headers.get("Retry-After"), await full.json()])
      .toEqual([429, "5", { error: "mailbox_full" }]);
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
    // The change moved the directory's entry to its revision, which the object takes.
    expect(await readRoute(doorsEnv, route)).toMatchObject({ r: 2 });
    expect((await call(event(hookUrl, "s0"))).status).toBe(202);
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
});

describe("/hooks/inbound, a shared app (Slack's HMAC)", () => {
  it("answers its URL challenge, and delivers a signed event to the installing owner", async () => {
    const who = owner();
    await signInApp({ owner: who });
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    const binding = await slackInstall(who, team);
    const challenge = await call(slackRequest({ type: "url_verification", challenge: "c-1" }));
    expect(await challenge.json()).toEqual({ challenge: "c-1" });
    await accepts(who, ADMITTED);
    const delivered = await call(slackRequest(slackEvent(team, "Ev1")));
    expect([delivered.status, await delivered.json()]).toEqual([202, ADMITTED]);
    const [taken] = await crossed(who, "inboundAccept");
    expect(taken!.binding).toMatchObject({ inbound: { binding, revision: 1 } });
    expect(taken!.request).toMatchObject({ event_id: "Ev1" });
  });

  it("refuses a bad HMAC, another secret's, and a replay past the five minutes' skew", async () => {
    const team = "TBAD";
    const tampered = slackRequest(slackEvent(team, "Ev2"));
    const forged = new Request(tampered, { body: JSON.stringify(slackEvent(team, "Ev3")) });
    const refused = await call(forged);
    // The one refusal of a proof that fails, whoever holds its secret.
    expect([refused.status, await refused.json()])
      .toEqual([401, { error: "inbound_proof_invalid" }]);
    expect((await call(slackRequest(slackEvent(team, "Ev4"), now(), "another-secret"))).status)
      .toBe(401);
    expect((await call(slackRequest(slackEvent(team, "Ev5"), now() - 301))).status).toBe(401);
    const unsigned = new Request(url(`/hooks/inbound/${SLACK.endpoint}`),
                                 { method: "POST", body: "{}" });
    expect((await call(unsigned)).status).toBe(401);
  });

  // The object's dedupe of a replay is the owner's test (`owner/test/admit.test.ts`).
  it("hands a replayed event inside the skew the object as it came, and no-ops an install no " +
     "owner made", async () => {
    const who = owner();
    await signInApp({ owner: who });
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    const retried = slackEvent(team, "EvR");  // a retry carries the same envelope
    await accepts(who, ADMITTED);
    expect((await call(slackRequest(retried))).status).toBe(202);
    await accepts(who, { ...ADMITTED, deduped: true });
    const replay = await call(slackRequest(retried));
    expect(await replay.json()).toMatchObject({ state: "admitted", deduped: true });
    const [first, again] = await crossed(who, "inboundAccept");
    expect(again!.request).toEqual(first!.request);
    const stranger = await call(slackRequest(slackEvent("TNOBODY", "EvS")));
    expect(stranger.status).toBe(200);
    expect(await stranger.json()).toEqual({ state: "no_op" });
    const shared = await call(slackRequest({ ...slackEvent(team, "EvX"),
                                             is_ext_shared_channel: true }));
    expect(await shared.json()).toEqual({ state: "no_op" });
  });

  // That the object answers mailbox_full at the hot ceiling, never counting it to the breaker,
  // is the owner's test (`owner/test/admit.test.ts`); here, the doors' answer to it.
  it("is told to send an event again when the object's mailbox is full (review E-2)",
     async () => {
    const who = owner();
    await signInApp({ owner: who });
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    await accepts(who, { state: "refused", error: "mailbox_full" });
    const refused = await call(slackRequest(slackEvent(team, `EvC${crypto.randomUUID()}`)));
    expect([refused.status, refused.headers.get("Retry-After"), await refused.json()])
      .toEqual([429, "5", { state: "refused", error: "mailbox_full" }]);
  });

  it("is told to send an event again when the object or the owner's maintenance gate refuses " +
     "it for the moment, and no-ops the event's own refusal", async () => {
    const who = owner();
    await signInApp({ owner: who });
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    await refuseOnce("inboundAccept", who, slotBusy());
    const busy = await call(slackRequest(slackEvent(team, "EvP")));
    expect([busy.status, busy.headers.get("Retry-After"), await busy.json()])
      .toEqual([503, "1", { state: "refused", error: "busy" }]);
    await setControl({ ...OPEN, inbound: true, ordinary: "none" });
    const closed = await call(slackRequest(slackEvent(team, "EvP")));
    expect([closed.status, closed.headers.get("Retry-After"), await closed.json()])
      .toEqual([503, "60", { state: "refused", error: "unavailable" }]);
    await setControl({ ...OPEN, inbound: true });
    await refuseOnce("inboundAccept", who, new Refusal("binding_withdrawn"));
    const withdrawn = await call(slackRequest(slackEvent(team, "EvP")));
    expect([withdrawn.status, withdrawn.headers.get("Retry-After"), await withdrawn.json()])
      .toEqual([200, null, { state: "refused", error: "binding_withdrawn" }]);
    await accepts(who, ADMITTED);
    expect((await call(slackRequest(slackEvent(team, "EvP")))).status).toBe(202);
    // Each refused try reached the object, and the one it took; the closed gate's did not.
    expect((await crossed(who, "inboundAccept")).map((call) => call.request["event_id"]))
      .toEqual(["EvP", "EvP", "EvP"]);
  });

  // That a shared binding's breaker trips once the agent's mailbox refused it, telling the
  // owner, until the owner's change re-arms it, is the owner's test (`owner/test/admit.test.ts`).
  it("answers the object's no-op, a tripped breaker, 200 with its error", async () => {
    const who = owner();
    await signInApp({ owner: who });
    const team = `T${crypto.randomUUID().slice(0, 8)}`;
    await slackInstall(who, team);
    await accepts(who, { state: "no_op", error: "breaker_tripped" });
    const tripped = await call(slackRequest(slackEvent(team, "EvT")));
    expect([tripped.status, await tripped.json()])
      .toEqual([200, { state: "no_op", error: "breaker_tripped" }]);
  });
});

describe("no caller is counted or capped by the doors (slice L's review)", () => {
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
    // Each metadata fetch reached the fake outside world, which answers none of them.
    const log = await (await env.PROVIDERS.fetch("https://status.test/log"))
      .json<{ url: string }[]>();
    expect(log.filter((entry) => /attacker-[0-9]+[.]example/.test(entry.url)).length)
      .toBeLessThanOrEqual(sent);
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
                          "rewrapKey", "mintKey", "markNamespace", "deleteNamespace", "holdNamespace",
                          "rootNamespace",
                          "checkChunks"]) {
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
    const read = await ownDoor(who, "fileRead", { path: "/workspace/big.bin" });
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
