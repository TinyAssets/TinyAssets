/**
 * The operator channel's consumer (`src/operator.ts`), in workerd through the doors' own entry
 * and the operator bucket: a message the kit signs (`signOperator`) answered once, and each
 * gate dropping its message loudly with nothing written (no ring, its shape, its MAC, an
 * unknown kid, a stale or a future time, a replay after its answer, another op under an ask
 * already accepted); an erased owner answered refused; a redelivery of a message accepted
 * but not yet answered going on, past the window too; and a delivery racing another held to
 * the acceptance record and the answer the other made first. Then each op on a real owner
 * object: `owner.state` read and changing nothing, the `owner.*` acts, a key rotation's
 * `owner.rewrap` and `owner.reseal`, and the restore's; and the platform's own
 * `restore.locate`, through the real files Worker.
 */
import { createHash, createHmac, randomBytes } from "node:crypto";

import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HELD_WAIT_MS, keyring } from "../../ci/fixtures";
import { PLATFORM_CALLER } from "../../contract/src/binding";
import { parseRing, type Ring } from "../../contract/src/envelope";
import {
  acceptanceKey, acceptanceOf, answerKey, argsText, LOCATE_PAGE, locateArgs,
  type OperatorAnswer, operatorAnswer, type OperatorArgs, type OperatorMessage, OPERATOR_WINDOW_S,
  type Refused, signOperator,
} from "../../contract/src/operator-message";
import { Refusal } from "../../contract/src/refusal";
import { now } from "../src/config";
import { putKey, putOwner, readKey, readOwner } from "../src/directory";
import type { DoorsEnv } from "../src/env";
import {
  admissionOf, appCallback, appPost, deliver, doorsEnv, held, OPEN, refuseOnce,
  serveFirstPartyDocument, setControl, signInApp,
} from "./support";

const RING = parseRing(doorsEnv.OPERATOR_KEYRING)!;
const OTHER = parseRing(keyring())!;
const fresh = () => `user_${randomBytes(12).toString("hex")}`;

/** What the consumer logs from here on, each line's first part. */
function logs() {
  const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
  return () => logged.mock.calls.map((call) => String(call[0]));
}

afterEach(() => {
  vi.restoreAllMocks();
});

const sign = (owner: string, op = "owner.state", args = {}, ring: Ring = RING, at = now()) =>
  signOperator(ring, op, owner, args, at);

/**
 * `message` MAC'd again as its fields now stand, by the format's own words (the contract
 * module's header) rather than its code: what a key holder could send under an ask of its
 * choosing.
 */
function remac(message: OperatorMessage): OperatorMessage {
  const digest = createHash("sha256").update(argsText(message.args)).digest("hex");
  const mac = createHmac("sha256", RING.keys.get(message.kid)!)
    .update(["tinyassets-operator-v1", message.op, message.owner, digest, message.ask,
             String(message.at)].join(String.fromCharCode(0))).digest("base64url");
  return { ...message, mac };
}

/**
 * The doors' env as a delivery racing another sees the operator bucket: its first read of
 * `hidden` finds nothing, as if the other delivery had not yet written it.
 */
function racing(hidden: string): DoorsEnv {
  const bucket = doorsEnv.OPERATOR_LOG;
  let blind = true;
  const read = <T>(key: string, real: () => Promise<T>): Promise<T | null> => {
    if (key === hidden && blind) {
      blind = false;
      return Promise.resolve(null);
    }
    return real();
  };
  return { ...doorsEnv, OPERATOR_LOG: {
    head: (key: string) => read(key, () => bucket.head(key)),
    get: (key: string) => read(key, () => bucket.get(key)),
    put: (key: string, value: string, options: R2PutOptions) => bucket.put(key, value, options),
  } as unknown as R2Bucket };
}

/** An answer's refusal. */
function refusedOf(answer: OperatorAnswer): Refused {
  expect(answer).toHaveProperty("refused");
  return (answer as { refused: Refused }).refused;
}

describe("the operator channel", () => {
  it("answers a signed message once, refusing an op it does not take, and drops its replay",
     async () => {
    const owner = fresh();
    const message = await sign(owner, "owner.rename", { part: "all" });
    const logged = logs();
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    const first = await held(message.ask);
    expect(first.accepted).toMatchObject({ op: "owner.rename", owner, at: message.at,
                                           kid: RING.active });
    const answer = operatorAnswer(first.answer, message);
    expect(answer).toMatchObject({ ask: message.ask, op: "owner.rename", owner,
                                   args: { part: "all" } });
    expect(refusedOf(answer)).toEqual({
      code: "invalid", message: "the operator channel takes no op owner.rename" });
    expect(answer.done).toBeGreaterThanOrEqual(message.at);
    const etag = (await doorsEnv.OPERATOR_LOG.head(answerKey(message.ask)))!.etag;
    // The same message again, as a redelivery or a capture: dropped, the answer as it was.
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    expect(logged()).toEqual(["an operator message is answered already: a replay; dropped"]);
    expect((await doorsEnv.OPERATOR_LOG.head(answerKey(message.ask)))!.etag).toBe(etag);
  });

  it("answers an erased owner's message refused", async () => {
    const owner = fresh();
    await putOwner(doorsEnv, owner, { s: "erased", t: now() });
    const message = await sign(owner);
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    expect(refusedOf(operatorAnswer((await held(message.ask)).answer, message)))
      .toEqual({ code: "owner_erased", message: "the owner is erased" });
  });

  const changed = async (change: (message: OperatorMessage) => unknown) =>
    change(await sign(fresh()));

  it.each([
    ["no ring", async () => sign(fresh()), { ...doorsEnv, OPERATOR_KEYRING: "" },
     "finds OPERATOR_KEYRING unset or no keyring"],
    ["a field more", () => changed((m) => ({ ...m, cursor: 1 })), doorsEnv, "is out of shape"],
    ["no args", () => changed(({ args: _args, ...m }) => m), doorsEnv, "is out of shape"],
    ["nested args", () => changed((m) => ({ ...m, args: { tier: { name: "paid" } } })),
     doorsEnv, "is out of shape"],
    ["another version", () => changed((m) => ({ ...m, v: 2 })), doorsEnv, "is out of shape"],
    ["another ring's MAC", async () => sign(fresh(), "owner.state", {}, OTHER), doorsEnv,
     "has a MAC that does not hold under its kid"],
    ["an unknown kid", () => changed((m) => ({ ...m, kid: "k9" })), doorsEnv,
     "has a MAC that does not hold under its kid"],
    ["args the MAC is not over", () => changed((m) => ({ ...m, args: { tier: "paid" } })),
     doorsEnv, "has a MAC that does not hold under its kid"],
    ["another owner", () => changed((m) => ({ ...m, owner: fresh() })), doorsEnv,
     "has a MAC that does not hold under its kid"],
    ["a stale time", async () => sign(fresh(), "owner.state", {}, RING,
                                      now() - OPERATOR_WINDOW_S - 5), doorsEnv,
     `was signed more than ${OPERATOR_WINDOW_S} s from now`],
    ["a time ahead", async () => sign(fresh(), "owner.state", {}, RING,
                                      now() + OPERATOR_WINDOW_S + 5), doorsEnv,
     `was signed more than ${OPERATOR_WINDOW_S} s from now`],
  ] as const)("drops a message with %s, nothing written", async (_what, made, env, why) => {
    const body = await made() as { ask: string };
    const logged = logs();
    expect(await deliver(body, env as DoorsEnv)).toEqual({ acked: 1, retried: 0 });
    expect(logged()).toEqual([`an operator message ${why}; dropped`]);
    expect(await held(body.ask)).toEqual({ accepted: null, answer: null });
  });

  it("takes the kit's MAC as the format words it, and no other", async () => {
    const message = await sign(fresh(), "owner.tier", { tier: "paid", reason: "x" });
    expect(remac(message).mac).toBe(message.mac);
    // The args' canonical text sorts their keys.
    expect(remac({ ...message, args: { reason: "x", tier: "paid" } }).mac).toBe(message.mac);
    expect(remac({ ...message, at: message.at + 1 }).mac).not.toBe(message.mac);
  });

  it("goes on with a redelivery of a message accepted but not answered, and drops another op " +
     "or other args under its ask", async () => {
    const owner = fresh();
    const message = await sign(owner, "owner.tier", { tier: "paid" });
    // The directory's record of the owner unreadable: the delivery fails after its acceptance.
    await doorsEnv.DIRECTORY.put(`o:${owner}`, "{}");
    const logged = logs();
    expect(await deliver(message)).toEqual({ acked: 0, retried: 1 });
    expect(await held(message.ask)).toMatchObject({ accepted: { op: "owner.tier" },
                                                    answer: null });
    // The same ask MAC'd for another op, or other args: dropped, nothing more written.
    for (const other of [remac({ ...message, op: "owner.admit", args: {} }),
                         remac({ ...message, args: { tier: "free" } })]) {
      expect(await deliver(other)).toEqual({ acked: 1, retried: 0 });
    }
    expect(logged().slice(1)).toEqual(Array(2).fill(
      "an operator message names another op, owner or args under an ask already accepted; " +
      "dropped"));
    expect((await held(message.ask)).answer).toBeNull();
    // The record readable again, the first message delivered again: answered.
    await putOwner(doorsEnv, owner, { s: "admitted", t: now() });
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    expect(operatorAnswer((await held(message.ask)).answer, message))
      .toMatchObject({ result: { tier: "paid" } });
  });

  it("makes the acceptance record and the answer once, a racing delivery held to them",
     async () => {
    const message = await sign(fresh(), "owner.tier", { tier: "paid" });
    const first = await acceptanceOf(message);
    await doorsEnv.OPERATOR_LOG.put(acceptanceKey(message.ask), JSON.stringify(first));
    // Other args under the ask, by a delivery that read before the record was made: dropped.
    const logged = logs();
    expect(await deliver(remac({ ...message, args: { tier: "free" } }),
                         racing(acceptanceKey(message.ask)))).toEqual({ acked: 1, retried: 0 });
    expect(logged()).toEqual(["an operator message names another op, owner or args under an " +
                              "ask already accepted; dropped"]);
    expect(await held(message.ask)).toEqual({ accepted: first, answer: null });
    // Answered, then answered again by a delivery that read before the answer was made.
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    const version = (await doorsEnv.OPERATOR_LOG.head(answerKey(message.ask)))!.version;
    expect(await deliver(message, racing(answerKey(message.ask))))
      .toEqual({ acked: 1, retried: 0 });
    expect((await doorsEnv.OPERATOR_LOG.head(answerKey(message.ask)))!.version).toBe(version);
  });

  it("goes on with a message accepted before, past the window a new one is dropped in",
     async () => {
    const message = await sign(fresh(), "owner.state", {}, RING, now() - OPERATOR_WINDOW_S - 60);
    await doorsEnv.OPERATOR_LOG.put(acceptanceKey(message.ask),
                                    JSON.stringify(await acceptanceOf(message)));
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    expect(operatorAnswer((await held(message.ask)).answer, message))
      .toMatchObject({ result: { state: "pending" } });
  });
});


/** `op` with `args` for `owner` delivered once, and its answer. */
async function asked(owner: string, op: string, args: OperatorArgs = {}) {
  const message = await sign(owner, op, args);
  expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
  return operatorAnswer((await held(message.ask)).answer, message);
}

/** The doors' env with the operator bucket failing `ask`'s answer once: its op has run. */
function answerFailingOnce(ask: string): DoorsEnv {
  let failed = false;
  const bucket = doorsEnv.OPERATOR_LOG;
  return { ...doorsEnv, OPERATOR_LOG: {
    head: (key: string) => bucket.head(key), get: (key: string) => bucket.get(key),
    put: (key: string, value: string, options: R2PutOptions) => {
      if (key === answerKey(ask) && !failed) {
        failed = true;
        return Promise.reject(new Error("the delivery is killed"));
      }
      return bucket.put(key, value, options);
    } } as unknown as R2Bucket };
}

/**
 * The doors the operator's deliveries called on `owner`'s object since the last look (the
 * owner gate's record): that the object's own read changes nothing is the owner's test
 * (`owner/test/admit.test.ts`).
 */
async function doorsCalled(owner: string): Promise<string[]> {
  return (await env.OWNER_GATE.seen(owner)).map((call) => call.method);
}

describe("the operator's owner ops, on a real owner object", () => {
  it("admits an owner, sets its tier, and suspends and lifts it, each answered with the " +
     "owner's own state", async () => {
    const owner = fresh();
    expect(await asked(owner, "owner.state")).toMatchObject({
      result: { state: "pending", reasons: [], tier: "free" } });
    expect(await asked(owner, "owner.admit")).toMatchObject({
      result: { state: "admitted", reasons: [] } });
    expect((await readOwner(doorsEnv, owner))?.s).toBe("admitted");
    expect(await asked(owner, "owner.tier", { tier: "paid" })).toMatchObject({
      result: { tier: "paid" } });
    expect(await asked(owner, "owner.suspend", { reason: "abuse" })).toMatchObject({
      result: { state: "suspended", reasons: ["abuse"] } });
    expect(await admissionOf(owner)).toEqual({ state: "suspended", reasons: ["abuse"],
                                             tier: "paid" });
    // An admit leaves a suspended owner as it stands.
    expect(await asked(owner, "owner.admit")).toMatchObject({
      result: { state: "suspended", reasons: ["abuse"] } });
    expect(await asked(owner, "owner.lift", { reason: "abuse" })).toMatchObject({
      result: { state: "admitted", reasons: [] } });
    expect(await admissionOf(owner)).toEqual({ state: "admitted", reasons: [], tier: "paid" });
  });

  it.each([
    ["a suspension of an owner never admitted", "owner.suspend", { reason: "billing" },
     { code: "refused", message: "an owner the platform never admitted is not suspended" }],
    ["a tier the owner has not", "owner.tier", { tier: "gold" }, { code: "invalid" }],
    ["a reason the owner has not", "owner.lift", { reason: "fraud" }, { code: "invalid" }],
    ["an arg more", "owner.tier", { tier: "paid", reason: "abuse" },
     { code: "invalid", message: "owner.tier takes exactly tier" }],
    ["an arg missing", "owner.suspend", {},
     { code: "invalid", message: "owner.suspend takes exactly reason" }],
    ["an arg an admit has not", "owner.admit", { now: true },
     { code: "invalid", message: "owner.admit takes exactly no args" }],
  ] as const)("answers %s refused, the owner as it was", async (_what, op, args, refusal) => {
    const owner = fresh();
    expect(refusedOf(await asked(owner, op, args))).toMatchObject(refusal);
    expect(await admissionOf(owner)).toEqual({ state: "pending", reasons: [], tier: "free" });
    expect(await readOwner(doorsEnv, owner)).toBeNull();
  });

  it("changes nothing more on a redelivery of a message it acted on but did not answer",
     async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    const message = await sign(owner, "owner.suspend", { reason: "billing" });
    // The answer's put fails once, after the op ran: the queue tries again.
    const flaky = answerFailingOnce(message.ask);
    const logged = logs();
    expect(await deliver(message, flaky)).toEqual({ acked: 0, retried: 1 });
    expect(logged()).toEqual(["an operator message failed; the queue tries again"]);
    expect(await admissionOf(owner)).toEqual({ state: "suspended", reasons: ["billing"],
                                             tier: "free" });
    expect(await deliver(message, flaky)).toEqual({ acked: 1, retried: 0 });
    expect(operatorAnswer((await held(message.ask)).answer, message)).toMatchObject({
      result: { state: "suspended", reasons: ["billing"] } });
    expect(await admissionOf(owner)).toEqual({ state: "suspended", reasons: ["billing"],
                                             tier: "free" });
  });
});

describe("the operator's owner.state, on a real owner object", () => {
  it("answers the owner's state, its names, codes and counts alone", async () => {
    const owner = fresh();
    expect(await asked(owner, "owner.state")).toMatchObject({ result: {
      state: "pending", reasons: [], tier: "free", restore: null, runs: 0, stored_bytes: 0 } });
    await asked(owner, "owner.admit");
    await asked(owner, "owner.tier", { tier: "paid" });
    await asked(owner, "owner.suspend", { reason: "billing" });
    const { result } = await asked(owner, "owner.state") as { result: unknown };
    expect(result).toEqual({ state: "suspended", reasons: ["billing"], tier: "paid",
                             restore: null, runs: 0, stored_bytes: 0 });
  });

  it("changes nothing: asked again, and delivered again after its read but before its " +
     "answer, it answers the same over the same rows", async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    await asked(owner, "owner.suspend", { reason: "abuse" });
    await doorsCalled(owner);
    const message = await sign(owner, "owner.state");
    const logged = logs();
    expect(await deliver(message, answerFailingOnce(message.ask)))
      .toEqual({ acked: 0, retried: 1 });
    expect(logged()).toEqual(["an operator message failed; the queue tries again"]);
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    const state = { state: "suspended", reasons: ["abuse"], tier: "free", restore: null,
                    runs: 0, stored_bytes: 0 };
    expect(operatorAnswer((await held(message.ask)).answer, message))
      .toMatchObject({ result: state });
    expect(await asked(owner, "owner.state")).toMatchObject({ result: state });
    // Each delivery called the object's read alone, its every time.
    expect(await doorsCalled(owner)).toEqual(["ownerState", "ownerState", "ownerState"]);
  });

  it("answers only a message its ring signed: another ring's, or none, is dropped with " +
     "nothing written", async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    await doorsCalled(owner);
    const forged = await sign(owner, "owner.state", {}, OTHER);
    const { mac: _mac, ...unsigned } = await sign(owner, "owner.state");
    const logged = logs();
    for (const body of [forged, unsigned]) {
      expect(await deliver(body)).toEqual({ acked: 1, retried: 0 });
      expect(await held(body.ask)).toEqual({ accepted: null, answer: null });
    }
    expect(logged()).toEqual([
      "an operator message has a MAC that does not hold under its kid; dropped",
      "an operator message is out of shape; dropped"]);
    expect(await doorsCalled(owner)).toEqual([]);
  });
});

describe("a key rotation's owner.rewrap and owner.reseal, on a real owner object", () => {
  /** Nothing but names, codes and counts: each answer's keys. */
  const keysOf = (answer: OperatorAnswer) =>
    Object.keys((answer as { result: object }).result).sort();

  it("answers each sweep's counts and kid alone; a restore's hold is counted left, never " +
     "refused, so it never stops the sweep", async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    const rewrap = await asked(owner, "owner.rewrap");
    expect(keysOf(rewrap)).toEqual(["kid", "left", "rewrapped", "roots"]);
    expect(rewrap).toMatchObject({ result: { left: 0 } });
    expect(await asked(owner, "owner.reseal"))
      .toMatchObject({ result: { resealed: 0, left: 0, kid: null } });
    // Held for a restore: answered at once, its generation left, acknowledged, nothing retried
    // (that a hold's generations count left is the owner's test, `owner/test/restore.test.ts`).
    const holding = { rewrapped: false, roots: 0, left: 1, kid: null };
    await env.OWNER_GATE.arm({ method: "dataKeyRewrap", name: owner, answer: holding });
    expect(await asked(owner, "owner.rewrap")).toMatchObject({ result: holding });
  });

  // That a sweep asked again changes nothing more is the owner's test
  // (`owner/test/export.test.ts`, `connections.test.ts`).
  it.each(["owner.rewrap", "owner.reseal"])(
    "%s: delivered again after its sweep but before its answer, answers as it stands; a " +
    "forged or unsigned one is dropped with nothing written", async (op) => {
      const owner = fresh();
      await asked(owner, "owner.admit");
      const message = await sign(owner, op);
      const logged = logs();
      expect(await deliver(message, answerFailingOnce(message.ask)))
        .toEqual({ acked: 0, retried: 1 });
      expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
      const { result } = operatorAnswer((await held(message.ask)).answer, message) as
        { result: unknown };
      expect(await asked(owner, op)).toMatchObject({ result });
      await doorsCalled(owner);
      const forged = await sign(owner, op, {}, OTHER);
      const { mac: _mac, ...unsigned } = await sign(owner, op);
      for (const body of [forged, unsigned]) {
        expect(await deliver(body)).toEqual({ acked: 1, retried: 0 });
        expect(await held(body.ask)).toEqual({ accepted: null, answer: null });
      }
      expect(logged()).toEqual([
        "an operator message failed; the queue tries again",
        "an operator message has a MAC that does not hold under its kid; dropped",
        "an operator message is out of shape; dropped"]);
      expect(await doorsCalled(owner)).toEqual([]);
    });
});

describe("the operator's restore ops, on a real owner object", () => {
  const GEN = { namespace: "a".repeat(32), gen: "b".repeat(16) };
  /** Nothing of a restore in the owner's object, as its restore source answers it. */
  const untouched = async (owner: string) =>
    expect(await asked(owner, "restore.source")).toMatchObject({
      result: { namespace: null, generations: [], restore: null } });

  it("answers a pending owner's source as its object holds it: no namespace, no generation",
     async () => {
    const owner = fresh();
    expect(await asked(owner, "restore.source")).toMatchObject({
      result: { namespace: null, generations: [], state: "pending", begins: true,
                restore: null } });
  });

  it("refuses a restore into an admitted owner, and a step with none begun", async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    expect(refusedOf(await asked(owner, "restore.begin", GEN))).toEqual({
      code: "refused", message: "the owner is admitted: a restore takes only a pending owner" });
    expect(refusedOf(await asked(owner, "restore.step"))).toEqual({
      code: "refused", message: "no restore of this owner was begun" });
    await untouched(owner);
  });

  it("refuses an erased owner whatever generation is named: its erasure record, the " +
     "directory's, beats it, though its object is pending and empty", async () => {
    const owner = fresh();
    await putOwner(doorsEnv, owner, { s: "erased", t: now() });
    expect(refusedOf(await asked(owner, "restore.begin", GEN))).toEqual({
      code: "owner_erased", message: "the owner is erased" });
    expect(await doorsCalled(owner)).toEqual([]);
  });

  it("takes a begin's namespace and generation exactly", async () => {
    const owner = fresh();
    expect(refusedOf(await asked(owner, "restore.begin", { namespace: GEN.namespace })))
      .toEqual({ code: "invalid", message: "restore.begin takes exactly namespace, gen" });
    expect(refusedOf(await asked(owner, "restore.begin", { ...GEN, gen: "x" })).code)
      .toBe("invalid");
    await untouched(owner);
  });

  // That a finished restore's step answers the restored owner's keys is the owner's test
  // (`owner/test/restore.test.ts`); here, the doors' work with that answer.
  it("once a restore is done, deletes the restored owner's API keys from the directory, its " +
     "answer naming them and no row", async () => {
    const owner = fresh();
    const key = "0123456789abcdef";
    await env.OWNER_GATE.arm({ method: "restoreStep", name: owner,
                               answer: { step: "done", done: true, keys: [key] } });
    await putKey(doorsEnv, key, { o: owner, h: "h".repeat(64), c: 1 });
    expect(await asked(owner, "restore.step")).toMatchObject({
      result: { step: "done", done: true, keys: [key] } });
    expect(await readKey(doorsEnv, key)).toBeNull();
  });
});

describe("an owner a restore holds, on a real owner object", () => {
  const GEN = { namespace: "a".repeat(32), gen: "b".repeat(16) };
  const FRESH = "c".repeat(16);
  /** The error a sign-in's redirect back to the app carries, or null for a code. */
  const signInError = async (owner: string) =>
    new URL((await appCallback({ owner })).back.headers.get("Location")!).searchParams
      .get("error");

  // That a restore under way or failed refuses every door but the restore's, `restoring`, is
  // the owner's test (`owner/test/restore.test.ts`); here, the sign-in's answer to it.
  it("answers a sign-in during a restore, and one after a failed restore, " +
     "temporarily_unavailable: never a thrown bug", async () => {
    serveFirstPartyDocument();
    const restoring = () => new Refusal("restoring", "restoring: this owner is held for a restore");
    // An owner the directory holds no record of (R8's case, its account lost): its sign-in's
    // admit meets the restore under way, and the directory is left without it.
    const lost = fresh();
    await refuseOnce("admissionSet", lost, restoring());
    expect(await signInError(lost)).toBe("temporarily_unavailable");
    expect(await readOwner(doorsEnv, lost)).toBeNull();
    // An owner signed up before, whose restore failed: its session's open meets it.
    const known = fresh();
    expect(await signInError(known)).toBeNull();
    await refuseOnce("sessionOpen", known, restoring());
    expect(await signInError(known)).toBe("temporarily_unavailable");
  });

  it("refuses a pin of a pending owner and a wipe with none pinned, and releases none as " +
     "released; an erased owner's pin, wipe and release are refused", async () => {
    const owner = fresh();
    expect(refusedOf(await asked(owner, "restore.pin", { gen: GEN.gen }))).toEqual({
      code: "refused", message: "the owner is pending: a pin holds a live owner, and a " +
                                "pending one is restored into with none" });
    expect(refusedOf(await asked(owner, "restore.wipe", { confirm: "x" }))).toEqual({
      code: "refused", message: "no generation of this owner is pinned: restore.pin first" });
    expect(await asked(owner, "restore.release")).toMatchObject({
      result: { released: true } });
    const erased = fresh();
    await putOwner(doorsEnv, erased, { s: "erased", t: now() });
    for (const [op, args] of [["restore.pin", { gen: GEN.gen }], ["restore.wipe",
                              { confirm: "x" }], ["restore.release", {}]] as const) {
      expect(refusedOf(await asked(erased, op, args))).toEqual({
        code: "owner_erased", message: "the owner is erased" });
    }
  });

  // That the wipe resets the object and leaves its record, which a redelivery's wipe answers,
  // is the owner's test (`owner/test/restore.test.ts`); here, the doors' redelivery.
  it("answers a confirmed wipe on its redelivery: the wipe resets the object, and the " +
     "record it left answers", async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    const message = await sign(owner, "restore.wipe", { confirm: `wipe-${owner}-${FRESH}` });
    const logged = logs();
    // The first delivery's wipe never answers: the reset dropped its connection.
    await env.OWNER_GATE.arm({ method: "restoreWipe", name: owner, hold: "before" });
    const first = deliver(message);
    const [wiping] = await env.OWNER_GATE.held(HELD_WAIT_MS);
    await env.OWNER_GATE.lose(wiping!.id);
    expect(await first).toEqual({ acked: 0, retried: 1 });
    expect(logged()).toEqual(["an operator message failed; the queue tries again"]);
    await env.OWNER_GATE.arm({ method: "restoreWipe", name: owner, answer: {
      step: "wiped", namespace: GEN.namespace, gen: GEN.gen, fresh: FRESH } });
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    expect(operatorAnswer((await held(message.ask)).answer, message)).toMatchObject({
      result: { step: "wiped", namespace: GEN.namespace, gen: GEN.gen, fresh: FRESH } });
  });
});

describe("the platform's restore.locate, through the files Worker", () => {
  const space = () => crypto.randomUUID().replaceAll("-", "");

  it("answers each namespace's owner and newest generation as the bucket holds it, and null " +
     "for one it holds no root of", async () => {
    // An owner's export writes its generation, as its nightly does.
    const restore = serveFirstPartyDocument();
    await setControl(OPEN);
    const owner = fresh();
    const app = await signInApp({ owner });
    let ask: Record<string, unknown> = {};
    for (let tries = 0; tries < 400; tries++) {
      const last = (await (await appPost("export", app.access_token, app.session, ask)).text())
        .trimEnd().split("\n").map((line) => JSON.parse(line) as Record<string, unknown>).at(-1)!;
      if (last["t"] !== "cursor") {
        break;
      }
      ask = { cursor: last["cursor"], export_id: last["export_id"] };
      await new Promise((done) => setTimeout(done, 25));
    }
    restore();
    const source = (await asked(owner, "restore.source") as { result: {
      namespace: string; generations: { gen: string }[] } }).result;
    const none = space();
    expect(await asked(PLATFORM_CALLER, "restore.locate",
                       locateArgs([source.namespace, none]))).toMatchObject({
      result: { located: { [source.namespace]: { owner, gen: source.generations.at(-1)!.gen },
                           [none]: null } } });
  });

  it("is the platform's alone, and an owner's op is no platform's", async () => {
    expect(refusedOf(await asked(fresh(), "restore.locate", locateArgs([space()])))).toEqual({
      code: "invalid", message: "restore.locate is the platform's own: it is addressed to the " +
                                "platform's reserved principal" });
    expect(refusedOf(await asked(PLATFORM_CALLER, "owner.state"))).toEqual({
      code: "invalid", message: "owner.state is addressed to an owner, not the platform's " +
                                "reserved principal" });
  });

  it.each([
    ["no namespace", ""],
    ["a namespace out of shape", "not-a-namespace"],
    ["one twice", ["a".repeat(32), "a".repeat(32)].join(",")],
    ["more than a page", Array.from({ length: LOCATE_PAGE + 1 },
                                    (_, at) => at.toString(16).padStart(32, "0")).join(",")],
  ])("refuses %s", async (_what, namespaces) => {
    expect(refusedOf(await asked(PLATFORM_CALLER, "restore.locate", { namespaces })).code)
      .toBe("invalid");
  });
});
