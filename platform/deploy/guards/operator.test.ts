/**
 * The kit's sending side of the operator channel (`deploy/operator.ts`), against a fake
 * Cloudflare: one message, MAC'd under the ring's active kid as the doors check it, sent to the
 * operator queue by its id; the answer read from the operator bucket until it is there, and
 * held to the message; no answer in time, an answer to another message, a failed read and an
 * account with no operator queue each a loud stop. The doors' half is
 * `doors/test/operator.test.ts`.
 */
import { describe, expect, it } from "vitest";

import { keyring } from "../../ci/fixtures";
import { parseRing } from "../../contract/src/envelope";
import {
  answerKey, answerOf, macHolds, type OperatorMessage, operatorMessage,
} from "../../contract/src/operator-message";
import { QUEUES } from "../../contract/src/queue-names";
import { Cloudflare } from "../cloudflare";
import { OPERATOR_NAMES } from "../facts";
import { askOperator, POLL_MS, WAIT_MS } from "../operator";

const ACCOUNT = "a".repeat(32);
const RING = parseRing(keyring("k2", ["k1", "k2"]))!;
const OWNER = "user_alice";
const START = new Date("2026-11-14T12:00:00.000Z");

/**
 * A fake account holding the operator queue (unless `queueless`), whose doors answer after
 * `polls` reads with `answer` (the message's own, refused, by default); and a clock each sleep
 * moves on.
 */
function account(options: { readonly polls?: number; readonly queueless?: boolean;
                            readonly answer?: (message: OperatorMessage) => unknown;
                            readonly failing?: boolean } = {}) {
  const sent: { queue: string; body: unknown }[] = [];
  const reads: string[] = [];
  let at = START.getTime();
  const json = (body: unknown, status = 200) => Response.json(body, { status });
  const cloudflare = new Cloudflare("cf-token", async (url, init) => {
    const path = url.slice("https://api.cloudflare.com/client/v4".length);
    const method = init?.method ?? "GET";
    if (path === `/accounts/${ACCOUNT}/queues` && method === "GET") {
      return json({ success: true, result: options.queueless === true ? []
        : [{ queue_id: "q-erasure", queue_name: QUEUES.ERASURES },
           { queue_id: "q-operator", queue_name: QUEUES.OPERATOR }] });
    }
    const queue = /^\/accounts\/a+\/queues\/([^/]+)\/messages$/.exec(path)?.[1];
    if (queue !== undefined && method === "POST") {
      const { body, content_type: type } = JSON.parse(String(init!.body)) as
        { body: unknown; content_type: string };
      expect(type).toBe("json");
      sent.push({ queue, body });
      return json({ success: true, result: null });
    }
    const object = `/accounts/${ACCOUNT}/r2/buckets/${OPERATOR_NAMES.bucket}/objects/`;
    if (path.startsWith(object) && method === "GET") {
      reads.push(path.slice(object.length));
      if (options.failing === true) {
        return json({ success: false, errors: [{ code: 10001 }] }, 500);
      }
      const message = operatorMessage(sent[0]!.body);
      if (reads.length <= (options.polls ?? 2)) {
        return json({ success: false, errors: [{ code: 10007 }] }, 404);
      }
      return json((options.answer ?? ((m) => answerOf(m, Math.floor(at / 1000), {
        refused: { code: "invalid", message: "the operator channel takes no op owner.tier" } })))(
        message));
    }
    throw new Error(`the fake account has no ${method} ${path}`);
  });
  const waiting = { now: () => new Date(at),
                    sleep: async (ms: number) => { at += ms; } };
  return { cloudflare, waiting, sent, reads, elapsed: () => at - START.getTime() };
}

describe("the kit's operator message", () => {
  it("is MAC'd under the active kid, sent to the operator queue, and its answer read until it " +
     "is there", async () => {
    const fake = account();
    const answer = await askOperator(fake.cloudflare, ACCOUNT, RING, "owner.tier", OWNER,
                                     { tier: "paid" }, fake.waiting);
    expect(fake.sent).toHaveLength(1);
    expect(fake.sent[0]!.queue).toBe("q-operator");
    const message = operatorMessage(fake.sent[0]!.body);
    expect(message).toMatchObject({ v: 1, op: "owner.tier", owner: OWNER, args: { tier: "paid" },
                                    at: START.getTime() / 1000, kid: "k2" });
    expect(await macHolds(RING, message)).toBe(true);
    expect(fake.reads).toEqual(Array(3).fill(answerKey(message.ask)));
    expect(fake.elapsed()).toBe(3 * POLL_MS);
    expect(answer).toMatchObject({ ask: message.ask, refused: { code: "invalid" } });
  });

  it("gives each message its own ask", async () => {
    const first = account();
    const second = account();
    await askOperator(first.cloudflare, ACCOUNT, RING, "owner.tier", OWNER, {}, first.waiting);
    await askOperator(second.cloudflare, ACCOUNT, RING, "owner.tier", OWNER, {}, second.waiting);
    expect(operatorMessage(first.sent[0]!.body).ask)
      .not.toBe(operatorMessage(second.sent[0]!.body).ask);
  });

  it("stops loudly with no answer in its wait, naming the ask and the dead-letter queue",
     async () => {
    const fake = account({ polls: Infinity });
    const asked = askOperator(fake.cloudflare, ACCOUNT, RING, "owner.tier", OWNER, {},
                              fake.waiting);
    await expect(asked).rejects.toThrow(new RegExp(
      `did not answer the operator message [0-9a-f]{32} \\(owner\\.tier on ${OWNER}\\) in ` +
      `${WAIT_MS / 1000} s: if it is in the dead-letter queue ` +
      `${OPERATOR_NAMES.dead_letter_queue}, they gave up on it`));
    expect(fake.reads).toHaveLength(WAIT_MS / POLL_MS);
  });

  it.each([
    ["another message's answer", { answer: (m: OperatorMessage) =>
      answerOf({ ...m, ask: "0".repeat(32) }, 1, { result: null }) }, "names another message"],
    ["an answer with other args", { answer: (m: OperatorMessage) =>
      answerOf({ ...m, args: { tier: "free" } }, 1, { result: null }) }, "names another message"],
    ["an answer out of shape", { answer: () => ({ done: 1 }) }, "is out of shape"],
    ["a refusal of no code", { answer: (m: OperatorMessage) =>
      answerOf(m, 1, { refused: { code: "nope" as "invalid", message: "x" } }) },
     "carries a refusal out of shape"],
    ["a failed read", { failing: true }, `reading answers/`],
    ["no operator queue", { queueless: true },
     `the account holds no queue ${QUEUES.OPERATOR}: no dark deploy made it`],
  ] as const)("stops loudly on %s", async (_what, options, why) => {
    const fake = account(options);
    await expect(askOperator(fake.cloudflare, ACCOUNT, RING, "owner.tier", OWNER,
                             { tier: "paid" }, fake.waiting)).rejects.toThrow(why);
  });
});
