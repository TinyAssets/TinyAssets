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
 * in `OPS`, its result or its refusal, and refused (`invalid`) when `OPS` takes no such op.
 * A refusal of the moment, or any failure, is retried by the queue, and then left in the
 * dead-letter queue the kit names when no answer comes. Every op is idempotent from the
 * owner's own state, since the queue delivers at least once.
 */
import {
  acceptance, acceptanceKey, acceptanceOf, answerKey, answerOf, macHolds, type OperatorArgs,
  type OperatorMessage, operatorMessage, OPERATOR_WINDOW_S, type Refused,
} from "../../contract/src/operator-message";
import { parseRing } from "../../contract/src/envelope";
import { refusalOf } from "../../contract/src/refusal";

import { now } from "./config";
import type { DoorsEnv } from "./env";
import { readOwner } from "./directory";

/** An op: what it does to the owner with its args, and its result. */
type Op = (env: DoorsEnv, owner: string, args: OperatorArgs) => Promise<unknown>;

/** The ops the channel takes, by name: none is wired yet (d3 §G's next PR adds them). */
const OPS: Readonly<Record<string, Op>> = Object.freeze({});

/** A put that only makes: null when the key is already there. */
const ONLY_NEW = Object.freeze({ onlyIf: Object.freeze({ etagDoesNotMatch: "*" }) });

/** Why a message is dropped, loudly. */
class Dropped extends Error {}

/** The message's acceptance record made, or the one there held to it; otherwise a drop. */
async function accept(env: DoorsEnv, message: OperatorMessage): Promise<void> {
  const key = acceptanceKey(message.ask);
  const mine = await acceptanceOf(message);
  let held = await env.OPERATOR_LOG.get(key);
  if (held === null) {
    if (Math.abs(now() - message.at) > OPERATOR_WINDOW_S) {
      throw new Dropped(`was signed more than ${OPERATOR_WINDOW_S} s from now`);
    }
    if (await env.OPERATOR_LOG.put(key, JSON.stringify(mine), ONLY_NEW) !== null) {
      return;
    }
    // Another delivery made it first: held to it as any record there is.
    held = await env.OPERATOR_LOG.get(key);
    if (held === null) {
      throw new Error(`the acceptance record of ${message.ask} is neither made nor there`);
    }
  }
  const record = acceptance(await held.json());
  if (record.op !== mine.op || record.owner !== mine.owner || record.digest !== mine.digest) {
    throw new Dropped("names another op, owner or args under an ask already accepted");
  }
}

/** The message's outcome: refused for an erased owner or an op `OPS` lacks; else its op's. */
async function verdict(env: DoorsEnv, message: OperatorMessage):
    Promise<{ result: unknown } | { refused: Refused }> {
  const refused = (code: Refused["code"], text: string) => ({ refused: { code, message: text } });
  if ((await readOwner(env, message.owner))?.s === "erased") {
    return refused("refused", "the owner is erased");
  }
  const op = Object.hasOwn(OPS, message.op) ? OPS[message.op] : undefined;
  if (op === undefined) {
    return refused("invalid", `the operator channel takes no op ${message.op}`);
  }
  try {
    return { result: await op(env, message.owner, message.args) };
  } catch (error) {
    const refusal = refusalOf(error);
    if (refusal === null || refusal.transient) {
      throw error;
    }
    return refused(refusal.code, refusal.message);
  }
}

/** One message through the gates, then answered; a gate's failure is a `Dropped`. */
async function take(env: DoorsEnv, body: unknown): Promise<void> {
  const ring = parseRing(env.OPERATOR_KEYRING);
  if (ring === null) {
    throw new Dropped("finds OPERATOR_KEYRING unset or no keyring");
  }
  let message: OperatorMessage;
  try {
    message = operatorMessage(body);
  } catch {
    throw new Dropped("is out of shape");
  }
  if (!(await macHolds(ring, message))) {
    throw new Dropped("has a MAC that does not hold under its kid");
  }
  if (await env.OPERATOR_LOG.head(answerKey(message.ask)) !== null) {
    throw new Dropped("is answered already: a replay");
  }
  await accept(env, message);
  const answer = answerOf(message, now(), await verdict(env, message));
  // A put that only makes: a delivery that answered first keeps its answer.
  await env.OPERATOR_LOG.put(answerKey(message.ask), `${JSON.stringify(answer)}\n`, ONLY_NEW);
}

/** The operator queue's consumer: each message gated, then answered; a failure retried. */
export async function operatorQueue(batch: MessageBatch<unknown>, env: DoorsEnv): Promise<void> {
  for (const message of batch.messages) {
    try {
      await take(env, message.body);
      message.ack();
    } catch (error) {
      if (error instanceof Dropped) {
        console.error(`an operator message ${error.message}; dropped`);
        message.ack();
        continue;
      }
      console.error("an operator message failed; the queue tries again", error);
      message.retry();
    }
  }
}
