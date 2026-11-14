/**
 * The operator channel's consumer (d3 §G; `operator_research.md` §1.4): the operator queue's,
 * as the ring's and the erasure's are. The deploy kit puts one MAC'd message on the queue for
 * each platform act it asks for (`contract/src/operator-message.ts`, `deploy/operator.ts`), and
 * reads the answer this writes to the operator bucket (`OPERATOR_LOG`), which keeps it, and the
 * acceptance record before it, under a lock: the channel's audit log. It is permanent, and
 * shares no file with the cutover's import.
 *
 * Every gate holds before anything is written, in this order, and a message that fails one
 * is dropped loudly (acknowledged, so the queue never retries it):
 * 1. `OPERATOR_KEYRING` is set and is a ring (`parseRing`);
 * 2. the message is in its exact shape (`operatorMessage`);
 * 3. its kid is in the ring and its MAC holds under that kid's key, in constant time;
 * 4. no answer to its ask is in the bucket: once answered, a message is a replay;
 * 5. its acceptance record (`asks/<ask>.json`) is made now, by a put that only makes, and only
 *    when its `at` is within `OPERATOR_WINDOW_S` of the doors' clock; one already there must
 *    name the message's op, owner and args digest, so a redelivery goes on and a used ask
 *    is never given another meaning.
 * Then the message is answered (`answers/<ask>.json`, a put that only makes): refused
 * (`refused`) when the directory's `o:` record says the owner is erased; otherwise by its op
 * in `OPS`, its result or its refusal, and refused (`invalid`) when `OPS` takes no such op or
 * its args are not exactly the op's (`OPERATOR_OPS`).
 * A refusal of the moment, or any failure, is retried by the queue, and then left in the
 * dead-letter queue the kit names when no answer comes. Every op is idempotent from the
 * owner's own state, since the queue delivers at least once.
 */
import {
  acceptance, acceptanceKey, acceptanceOf, answerKey, answerOf, macHolds, type OperatorArgs,
  type OperatorMessage, operatorMessage, type OperatorOp, OPERATOR_OPS, OPERATOR_WINDOW_S,
  type Refused,
} from "../../contract/src/operator-message";
import { parseRing } from "../../contract/src/envelope";
import type { TierRequest } from "../../contract/src/owner";
import { refusalOf } from "../../contract/src/refusal";

import { admit } from "./authorize";
import { bindPlatformFor } from "./binder";
import { now } from "./config";
import type { DoorsEnv } from "./env";
import { readOwner } from "./directory";
import { exactKeys } from "./kit";

/** An op: what it does to the owner with its args, exactly `OPERATOR_OPS` names, and its result. */
type Op = (env: DoorsEnv, owner: string, args: OperatorArgs) => Promise<unknown>;

/** The platform's change of `owner`'s admission: a suspension's reason, held or lifted. */
function suspension(env: DoorsEnv, owner: string, { reason }: OperatorArgs,
                    suspended: boolean): Promise<unknown> {
  const { binding, stub } = bindPlatformFor(env, owner);
  return stub.admissionSet(binding, { reason: reason as string, suspended });
}

/**
 * Each op the channel takes, by its owner door, whose answer is its result: the args are the
 * door's own request, which it checks.
 */
const OPS: Readonly<Record<OperatorOp, Op>> = Object.freeze({
  "owner.tier": async (env, owner, args) => {
    const { binding, stub } = bindPlatformFor(env, owner);
    return stub.tierSet(binding, args as unknown as TierRequest);
  },
  "owner.suspend": (env, owner, args) => suspension(env, owner, args, true),
  "owner.lift": (env, owner, args) => suspension(env, owner, args, false),
  "owner.admit": (env, owner) => admit(env, owner),
});

/** A put that only makes: null when the key is already there. */
const ONLY_NEW = Object.freeze({ onlyIf: Object.freeze({ etagDoesNotMatch: "*" }) });

/**
 * The message's acceptance record made, or the one there held to it: null, or why the message
 * is dropped.
 */
async function accept(env: DoorsEnv, message: OperatorMessage): Promise<string | null> {
  const key = acceptanceKey(message.ask);
  const mine = await acceptanceOf(message);
  let held = await env.OPERATOR_LOG.get(key);
  if (held === null) {
    if (Math.abs(now() - message.at) > OPERATOR_WINDOW_S) {
      return `was signed more than ${OPERATOR_WINDOW_S} s from now`;
    }
    if (await env.OPERATOR_LOG.put(key, JSON.stringify(mine), ONLY_NEW) !== null) {
      return null;
    }
    // Another delivery made it first: held to it as any record there is.
    held = await env.OPERATOR_LOG.get(key);
    if (held === null) {
      throw new Error(`the acceptance record of ${message.ask} is neither made nor there`);
    }
  }
  const record = acceptance(await held.json());
  return record.op === mine.op && record.owner === mine.owner && record.digest === mine.digest
    ? null : "names another op, owner or args under an ask already accepted";
}

/** The message's outcome: refused for an erased owner or an op `OPS` lacks; else its op's. */
async function verdict(env: DoorsEnv, message: OperatorMessage):
    Promise<{ result: unknown } | { refused: Refused }> {
  const refused = (code: Refused["code"], text: string) => ({ refused: { code, message: text } });
  if ((await readOwner(env, message.owner))?.s === "erased") {
    return refused("refused", "the owner is erased");
  }
  if (!Object.hasOwn(OPS, message.op)) {
    return refused("invalid", `the operator channel takes no op ${message.op}`);
  }
  const op = message.op as OperatorOp;
  if (!exactKeys(message.args, OPERATOR_OPS[op])) {
    return refused("invalid", `${op} takes exactly ${
      OPERATOR_OPS[op].join(", ") || "no args"}`);
  }
  try {
    return { result: await OPS[op](env, message.owner, message.args) };
  } catch (error) {
    const refusal = refusalOf(error);
    if (refusal === null || refusal.transient) {
      throw error;
    }
    return refused(refusal.code, refusal.message);
  }
}

/** One message through the gates, then answered: null, or why a gate dropped it. */
async function take(env: DoorsEnv, body: unknown): Promise<string | null> {
  const ring = parseRing(env.OPERATOR_KEYRING);
  if (ring === null) {
    return "finds OPERATOR_KEYRING unset or no keyring";
  }
  let message: OperatorMessage;
  try {
    message = operatorMessage(body);
  } catch {
    return "is out of shape";
  }
  if (!(await macHolds(ring, message))) {
    return "has a MAC that does not hold under its kid";
  }
  if (await env.OPERATOR_LOG.head(answerKey(message.ask)) !== null) {
    return "is answered already: a replay";
  }
  const dropped = await accept(env, message);
  if (dropped !== null) {
    return dropped;
  }
  const answer = answerOf(message, now(), await verdict(env, message));
  // A put that only makes: a delivery that answered first keeps its answer.
  await env.OPERATOR_LOG.put(answerKey(message.ask), `${JSON.stringify(answer)}\n`, ONLY_NEW);
  return null;
}

/** The operator queue's consumer: each message gated, then answered; a failure retried. */
export async function operatorQueue(batch: MessageBatch<unknown>, env: DoorsEnv): Promise<void> {
  for (const message of batch.messages) {
    let dropped: string | null;
    try {
      dropped = await take(env, message.body);
    } catch (error) {
      console.error("an operator message failed; the queue tries again", error);
      message.retry();
      continue;
    }
    if (dropped !== null) {
      console.error(`an operator message ${dropped}; dropped`);
    }
    message.ack();
  }
}
