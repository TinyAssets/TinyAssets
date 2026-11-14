/**
 * The operator channel's consumer (`src/operator.ts`), in workerd through the doors' own entry
 * and the operator bucket: a message the kit signs (`signOperator`) answered once, and each
 * gate dropping its message loudly with nothing written (no ring, its shape, its MAC, an
 * unknown kid, a stale or a future time, a replay after its answer, another op under an ask
 * already accepted); an erased owner answered refused; a redelivery of a message accepted
 * but not yet answered going on, past the window too; and a delivery racing another held to
 * the acceptance record and the answer the other made first.
 */
import { createHash, createHmac, randomBytes } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import { keyring } from "../../ci/fixtures";
import { parseRing, type Ring } from "../../contract/src/envelope";
import {
  acceptanceKey, acceptanceOf, answerKey, argsText, type OperatorAnswer, operatorAnswer,
  type OperatorArgs, type OperatorMessage, OPERATOR_WINDOW_S, type Refused, signOperator,
} from "../../contract/src/operator-message";
import { QUEUES } from "../../contract/src/queue-names";
import { now } from "../src/config";
import { putOwner, readOwner } from "../src/directory";
import type { DoorsEnv } from "../src/env";
import worker from "../src/index";
import { doorsEnv, sqlIn } from "./support";

const RING = parseRing(doorsEnv.OPERATOR_KEYRING)!;
const OTHER = parseRing(keyring())!;
const fresh = () => `user_${randomBytes(12).toString("hex")}`;

/** One delivery of `body` through the doors' queue handler, and how it ended. */
async function deliver(body: unknown, env: DoorsEnv = doorsEnv) {
  const acked: number[] = [];
  const retried: number[] = [];
  const batch = { queue: QUEUES.OPERATOR, ackAll: () => undefined, retryAll: () => undefined,
                  messages: [{ id: "0", timestamp: new Date(), attempts: 1, body,
                               ack: () => acked.push(0), retry: () => retried.push(0) }] };
  await worker.queue(batch as unknown as MessageBatch<unknown>, env);
  return { acked: acked.length, retried: retried.length };
}

/** What the bucket holds for `ask`: its acceptance record and its answer, or null. */
async function held(ask: string) {
  const read = async (key: string) => {
    const object = await doorsEnv.OPERATOR_LOG.get(key);
    return object === null ? null : await object.json() as Record<string, unknown>;
  };
  return { accepted: await read(acceptanceKey(ask)), answer: await read(answerKey(ask)) };
}

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
    const message = await sign(owner, "owner.state", { part: "all" });
    const logged = logs();
    expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
    const first = await held(message.ask);
    expect(first.accepted).toMatchObject({ op: "owner.state", owner, at: message.at,
                                           kid: RING.active });
    const answer = operatorAnswer(first.answer, message);
    expect(answer).toMatchObject({ ask: message.ask, op: "owner.state", owner,
                                   args: { part: "all" } });
    expect(refusedOf(answer)).toEqual({
      code: "invalid", message: "the operator channel takes no op owner.state" });
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
      .toEqual({ code: "refused", message: "the owner is erased" });
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
    expect(refusedOf(operatorAnswer((await held(message.ask)).answer, message)).code)
      .toBe("invalid");
  });
});


/** The owner's admission row as its own object holds it. */
async function admission(owner: string) {
  const [row] = await sqlIn(owner, "SELECT state, reasons, tier FROM owner_admission");
  return { ...row, reasons: JSON.parse(String(row!["reasons"])) as string[] };
}

/** `op` with `args` for `owner` delivered once, and its answer. */
async function asked(owner: string, op: string, args: OperatorArgs = {}) {
  const message = await sign(owner, op, args);
  expect(await deliver(message)).toEqual({ acked: 1, retried: 0 });
  return operatorAnswer((await held(message.ask)).answer, message);
}

describe("the operator's owner ops, on a real owner object", () => {
  it("admits an owner, sets its tier, and suspends and lifts it, each answered with the " +
     "owner's own state", async () => {
    const owner = fresh();
    expect(await admission(owner)).toEqual({ state: "pending", reasons: [], tier: "free" });
    expect(await asked(owner, "owner.admit")).toMatchObject({
      result: { state: "admitted", reasons: [] } });
    expect((await readOwner(doorsEnv, owner))?.s).toBe("admitted");
    expect(await asked(owner, "owner.tier", { tier: "paid" })).toMatchObject({
      result: { tier: "paid" } });
    expect(await asked(owner, "owner.suspend", { reason: "abuse" })).toMatchObject({
      result: { state: "suspended", reasons: ["abuse"] } });
    expect(await admission(owner)).toEqual({ state: "suspended", reasons: ["abuse"],
                                             tier: "paid" });
    // An admit leaves a suspended owner as it stands.
    expect(await asked(owner, "owner.admit")).toMatchObject({
      result: { state: "suspended", reasons: ["abuse"] } });
    expect(await asked(owner, "owner.lift", { reason: "abuse" })).toMatchObject({
      result: { state: "admitted", reasons: [] } });
    expect(await admission(owner)).toEqual({ state: "admitted", reasons: [], tier: "paid" });
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
    expect(await admission(owner)).toEqual({ state: "pending", reasons: [], tier: "free" });
    expect(await readOwner(doorsEnv, owner)).toBeNull();
  });

  it("changes nothing more on a redelivery of a message it acted on but did not answer",
     async () => {
    const owner = fresh();
    await asked(owner, "owner.admit");
    const message = await sign(owner, "owner.suspend", { reason: "billing" });
    // The answer's put fails once, after the op ran: the queue tries again.
    let failed = false;
    const bucket = doorsEnv.OPERATOR_LOG;
    const flaky = { ...doorsEnv, OPERATOR_LOG: {
      head: (key: string) => bucket.head(key), get: (key: string) => bucket.get(key),
      put: (key: string, value: string, options: R2PutOptions) => {
        if (key === answerKey(message.ask) && !failed) {
          failed = true;
          return Promise.reject(new Error("the delivery is killed"));
        }
        return bucket.put(key, value, options);
      } } as unknown as R2Bucket };
    const logged = logs();
    expect(await deliver(message, flaky)).toEqual({ acked: 0, retried: 1 });
    expect(logged()).toEqual(["an operator message failed; the queue tries again"]);
    expect(await admission(owner)).toEqual({ state: "suspended", reasons: ["billing"],
                                             tier: "free" });
    expect(await deliver(message, flaky)).toEqual({ acked: 1, retried: 0 });
    expect(operatorAnswer((await held(message.ask)).answer, message)).toMatchObject({
      result: { state: "suspended", reasons: ["billing"] } });
    expect(await admission(owner)).toEqual({ state: "suspended", reasons: ["billing"],
                                             tier: "free" });
  });
});
