/**
 * The kit's operator path (d3 §G; `operator_research.md` §2): what its modes `control` and
 * `owner` (`deploy.ts`) read and send.
 *
 * - **`control`** edits the control record (`contract/src/control.ts`) in KV itself, never
 *   through the doors, so the kill switch works when they don't. Its edits (`--set`,
 *   `controlEdits`) apply to the live record, or to the whole record `--replace` gives in its
 *   place (`edited`), and the result is written only if `parseControl`, the parser every
 *   reader uses, takes it; the plan is each field's difference (`differences`).
 * - **`owner`** asks the doors one op of `OPERATOR_OPS` (`--act`, `ownerAct`) through the
 *   operator channel (`contract/src/operator-message.ts`): a message MAC'd under
 *   `OPERATOR_KEYRING`'s active kid and put on the operator queue (`QUEUES.OPERATOR`) over the
 *   Queues API, then its answer read from the operator bucket (`OPERATOR_NAMES`) every
 *   `POLL_MS`, for at most `WAIT_MS`. An answer is held to the message it names; none in time
 *   is a loud stop naming the ask and the dead-letter queue, where a message the doors gave up
 *   on is left.
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
import type { Ring } from "../contract/src/envelope.ts";
import {
  answerKey, type OperatorAnswer, operatorAnswer, type OperatorArgs, type OperatorOp,
  OPERATOR_OPS, signOperator,
} from "../contract/src/operator-message.ts";
import { QUEUES } from "../contract/src/queue-names.ts";
import type { Cloudflare } from "./cloudflare.ts";
import { OPERATOR_NAMES } from "./facts.ts";

// --- Mode `control` ---------------------------------------------------------------------

/**
 * One edit of the control record: at a dotted path of its keys, a value set (`=`), or added
 * to (`+=`) or taken from (`-=`) the list there, each value once. The value is JSON where it
 * reads as JSON (`5`, `true`, `[]`), and otherwise the text as it is (`on`, an owner's id).
 */
export interface Edit {
  readonly path: readonly string[];
  readonly how: "=" | "+=" | "-=";
  readonly value: unknown;
}

/** An edit's path, its operator at the first `+=`, `-=` or `=`, and its value. */
const EDIT = /^([^=]+?)(\+=|-=|=)(.*)$/s;
/** One key of an edit's path. */
const KEY = /^[A-Za-z0-9_-]+$/;

/** `--set`'s edits, separated by white space: `outside=on canary+=user_x tiers.free.runs=5`. */
export function controlEdits(text: string): Edit[] {
  return text.split(/\s+/).filter(Boolean).map((edit) => {
    const [, path, how, value] = EDIT.exec(edit) ?? [];
    const keys = path?.split(".") ?? [];
    if (keys.length === 0 || !keys.every((key) => KEY.test(key))) {
      throw new Error(`--set's edit ${edit} is not <path>=<value>, <path>+=<value> or ` +
                      "<path>-=<value>, its path the record's keys joined by dots");
    }
    let parsed: unknown = value;
    try {
      parsed = JSON.parse(value!);
    } catch {
      // Not JSON: the text itself.
    }
    return Object.freeze({ path: Object.freeze(keys), how: how as Edit["how"], value: parsed });
  });
}

const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** A copy of the record `base` with `edits` made in order; `base` is left as it is. */
export function edited(base: unknown, edits: readonly Edit[]): Record<string, unknown> {
  if (!plain(base)) {
    throw new Error("the control record the edits start from is not an object");
  }
  const record = structuredClone(base);
  for (const { path, how, value } of edits) {
    let at: Record<string, unknown> = record;
    for (const key of path.slice(0, -1)) {
      at[key] ??= {};
      const next = at[key];
      if (!plain(next)) {
        throw new Error(`the control record's ${path.join(".")} is inside no object`);
      }
      at = next;
    }
    const last = path.at(-1)!;
    if (how === "=") {
      at[last] = value;
      continue;
    }
    const list = at[last] ?? [];
    if (!Array.isArray(list)) {
      throw new Error(`the control record's ${path.join(".")} is no list to ${
        how === "+=" ? "add to" : "take from"}`);
    }
    const others = list.filter((item) => !same(item, value));
    at[last] = how === "+=" ? [...others, value] : others;
  }
  return record;
}

/** Each leaf of `value` (an object's own, a list whole), by its dotted path, as JSON. */
function leaves(value: unknown, path: string, into: Map<string, string>): Map<string, string> {
  if (!plain(value) || (path !== "" && Object.keys(value).length === 0)) {
    into.set(path || "the record", JSON.stringify(value));
    return into;
  }
  for (const [key, each] of Object.entries(value)) {
    leaves(each, path === "" ? key : `${path}.${key}`, into);
  }
  return into;
}

/**
 * How the record `after` differs from `before` (undefined when there is none): each leaf
 * whose value changed, with its old and its new value, `absent` where it has none.
 */
export function differences(before: unknown, after: unknown): string[] {
  const old = before === undefined ? new Map<string, string>() : leaves(before, "", new Map());
  const now = leaves(after, "", new Map());
  return [...new Set([...old.keys(), ...now.keys()])]
    .filter((path) => old.get(path) !== now.get(path))
    .map((path) => `${path}: ${old.get(path) ?? "absent"} → ${now.get(path) ?? "absent"}`);
}

// --- Mode `owner` -----------------------------------------------------------------------

/** An `owner` mode's op and its args. */
export interface Act {
  readonly op: OperatorOp;
  readonly args: OperatorArgs;
}

const OWNER_OP = "owner.";

/** Each act `--act` takes: an `owner.` op of `OPERATOR_OPS` by its last word, with its arg. */
const ACTS: readonly string[] = Object.freeze(Object.entries(OPERATOR_OPS)
  .filter(([op]) => op.startsWith(OWNER_OP))
  .map(([op, args]) => `${op.slice(OWNER_OP.length)}${args.map((arg) => `=<${arg}>`).join("")}`));

/** `--act`'s op: `<verb>=<value>` for an op of one arg (`tier=paid`), `<verb>` for one of none. */
export function ownerAct(text: string): Act {
  const at = text.indexOf("=");
  const op = `${OWNER_OP}${at < 0 ? text : text.slice(0, at)}`;
  const names: readonly string[] | undefined = Object.hasOwn(OPERATOR_OPS, op)
    ? OPERATOR_OPS[op as OperatorOp] : undefined;
  if (names === undefined || names.length > 1 || (at < 0) !== (names.length === 0)) {
    throw new Error(`--act is one of ${ACTS.join(", ")}`);
  }
  return Object.freeze({ op: op as OperatorOp, args: Object.freeze(
    names.length === 0 ? {} : { [names[0]!]: text.slice(at + 1) }) });
}

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
