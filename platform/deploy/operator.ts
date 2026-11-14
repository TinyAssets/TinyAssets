/**
 * The kit's sending side of the operator channel (d3 §G; `contract/src/operator-message.ts`):
 * one op asked of the doors, as a message MAC'd under `OPERATOR_KEYRING`'s active kid and put
 * on the operator queue (`QUEUES.OPERATOR`) over the Queues API, then its answer read from the
 * operator bucket (`OPERATOR_NAMES`) every `POLL_MS`, for at most `WAIT_MS`. An answer is held
 * to the message it names; none in time is a loud stop naming the ask and the dead-letter
 * queue, where a message the doors gave up on is left. The kit's operator modes (d3 §G's
 * next PR) call it.
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
import type { Ring } from "../contract/src/envelope.ts";
import {
  answerKey, type OperatorAnswer, operatorAnswer, type OperatorArgs, signOperator,
} from "../contract/src/operator-message.ts";
import { QUEUES } from "../contract/src/queue-names.ts";
import type { Cloudflare } from "./cloudflare.ts";
import { OPERATOR_NAMES } from "./facts.ts";

/** How often the kit reads the bucket for an answer, and how long it waits for one. */
export const POLL_MS = 2_000;
export const WAIT_MS = 120_000;

/** The clock the kit waits by: replaced whole by the tests. */
export interface Waiting {
  readonly now: () => Date;
  readonly sleep: (ms: number) => Promise<void>;
}

/** `op` on `owner` with `args` asked of the doors in `account`, and their answer. */
export async function askOperator(cloudflare: Cloudflare, account: string, ring: Ring, op: string,
                                  owner: string, args: OperatorArgs, waiting: Waiting):
    Promise<OperatorAnswer> {
  const queue = (await cloudflare.queues(account)).get(QUEUES.OPERATOR);
  if (queue === undefined) {
    throw new Error(`the account holds no queue ${QUEUES.OPERATOR}: no dark deploy made it`);
  }
  const start = waiting.now().getTime();
  const message = await signOperator(ring, op, owner, args, Math.floor(start / 1000));
  await cloudflare.send(account, queue, message);
  for (;;) {
    await waiting.sleep(POLL_MS);
    const found = await cloudflare.object(account, OPERATOR_NAMES.bucket, answerKey(message.ask));
    if (found !== undefined) {
      return operatorAnswer(found, message);
    }
    if (waiting.now().getTime() - start >= WAIT_MS) {
      throw new Error(`the doors did not answer the operator message ${message.ask} (${op} on ` +
                      `${owner}) in ${WAIT_MS / 1000} s: if it is in the dead-letter queue ` +
                      `${OPERATOR_NAMES.dead_letter_queue}, they gave up on it`);
    }
  }
}
