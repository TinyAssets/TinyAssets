/**
 * The kit's operator path (d3 §G; `operator_research.md` §2): what its modes `control` and
 * `owner` (`deploy.ts`) read and send.
 *
 * - **`control`** edits the control record (`contract/src/control.ts`) in KV itself, never
 *   through the doors, so the kill switch works when they don't. Its edits (`--set`,
 *   `controlEdits`) apply to the live record, or to the whole record `--replace` gives in its
 *   place (`edited`), and the result is written only if `parseControl`, the parser every
 *   reader uses, takes it; the plan is each field's difference (`differences`).
 * - **`restore`** asks the doors `restore.source` for an owner, or, given a generation
 *   (`--generation`, `restoreFrom`), its restore from it (`owner/src/restore.ts`): begun,
 *   stepped until done, then the owner admitted, each op through the operator channel.
 * - **`erase`** asks the doors `owner.erase` for an owner, only with the words that confirm it
 *   (`eraseWords`), which `--act` never carries.
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
import { object } from "../contract/src/checks.ts";
import type { Ring } from "../contract/src/envelope.ts";
import { HEX16, HEX32 } from "../contract/src/ids.ts";
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

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** `at`'s own `key`, never one it inherits (`__proto__`, `constructor`). */
const own = (at: Record<string, unknown>, key: string): unknown =>
  Object.hasOwn(at, key) ? at[key] : undefined;

/**
 * `value` made `at`'s own `key`, as JSON would hold it: a key such as `__proto__` is a field
 * like any other, which the parser then refuses, and never a prototype.
 */
function setOwn(at: Record<string, unknown>, key: string, value: unknown): void {
  Object.defineProperty(at, key, { value, enumerable: true, writable: true, configurable: true });
}

/** A copy of the record `base` with `edits` made in order; `base` is left as it is. */
export function edited(base: unknown, edits: readonly Edit[]): Record<string, unknown> {
  if (!object(base)) {
    throw new Error("the control record the edits start from is not an object");
  }
  const record = structuredClone(base);
  for (const { path, how, value } of edits) {
    let at: Record<string, unknown> = record;
    for (const key of path.slice(0, -1)) {
      if (own(at, key) === undefined) {
        setOwn(at, key, {});
      }
      const next = own(at, key);
      if (!object(next)) {
        throw new Error(`the control record's ${path.join(".")} is inside no object`);
      }
      at = next;
    }
    const last = path.at(-1)!;
    if (how === "=") {
      setOwn(at, last, value);
      continue;
    }
    const list = own(at, last) ?? [];
    if (!Array.isArray(list)) {
      throw new Error(`the control record's ${path.join(".")} is no list to ${
        how === "+=" ? "add to" : "take from"}`);
    }
    const others = list.filter((item) => !same(item, value));
    setOwn(at, last, how === "+=" ? [...others, value] : others);
  }
  return record;
}

/** Each leaf of `value` (an object's own, a list whole), by its dotted path, as JSON. */
function leaves(value: unknown, path: string, into: Map<string, string>): Map<string, string> {
  if (!object(value) || (path !== "" && Object.keys(value).length === 0)) {
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

/**
 * The ops `--act` asks, each by its args: the `owner.` ops of `OPERATOR_OPS` but one that
 * words confirm (`confirm`), which is a mode's own, with its `--confirm` (`erase`).
 */
const OWNER_OPS: Readonly<Record<string, readonly string[]>> = Object.freeze(Object.fromEntries(
  Object.entries(OPERATOR_OPS).filter(([op, args]) => op.startsWith(OWNER_OP)
    && !(args as readonly string[]).includes("confirm"))));

/** Each act `--act` takes: an op of `OWNER_OPS` by its last word, with its arg. */
const ACTS: readonly string[] = Object.freeze(Object.entries(OWNER_OPS)
  .map(([op, args]) => `${op.slice(OWNER_OP.length)}${args.map((arg) => `=<${arg}>`).join("")}`));

/** `--act`'s op: `<verb>=<value>` for an op of one arg (`tier=paid`), `<verb>` for one of none. */
export function ownerAct(text: string): Act {
  const at = text.indexOf("=");
  const op = `${OWNER_OP}${at < 0 ? text : text.slice(0, at)}`;
  const names = Object.hasOwn(OWNER_OPS, op) ? OWNER_OPS[op] : undefined;
  if (names === undefined || names.length > 1 || (at < 0) !== (names.length === 0)) {
    throw new Error(`--act is one of ${ACTS.join(", ")}`);
  }
  return Object.freeze({ op: op as OperatorOp, args: Object.freeze(
    names.length === 0 ? {} : { [names[0]!]: text.slice(at + 1) }) });
}

// --- Mode `restore` ---------------------------------------------------------------------

/** A generation to restore from: its namespace and id, as `restore.source` answers them. */
export interface Generation {
  readonly namespace: string;
  readonly gen: string;
}

/** `--generation`'s generation: `<namespace>/<gen>`, 32 and 16 lowercase hex characters. */
export function restoreFrom(text: string): Generation {
  const [namespace, gen, ...more] = text.split("/");
  if (more.length > 0 || !HEX32.test(namespace ?? "") || !HEX16.test(gen ?? "")) {
    throw new Error(`--generation ${text} is not <namespace>/<gen>`);
  }
  return Object.freeze({ namespace: namespace!, gen: gen! });
}

/** What an erasure does, which its plan says (`owner/src/erasure.ts`, `doors/src/erase.ts`). */
export const ERASE_LOSES = "every door of the owner closed at once and every run ended, then " +
  "its keys, inbound URLs, grants and identity deleted, its R2 copies and database deleted " +
  "once the erasure's grace is past, and its name never admitted again: no restore takes it back";

/** What an owner loses at a restore (`operator_research.md` §3.3), which its plan says. */
export const RESTORE_LOSES = "the owner loses its sessions (it signs in again), every agent's " +
  "grant (each authorizes again), its API keys and inbound URLs (it makes new ones), its model " +
  "connections (it connects them again), and anything written after the generation";

/** How often the kit reads the bucket for an answer, and how long it waits for one. */
export const POLL_MS = 2_000;
export const WAIT_MS = 120_000;
/**
 * How long mode `restore` asks a pin to write the owner's present state before it stops,
 * loudly, the owner still held: inside the apply job's 30 minutes (`platform-deploy.yml`), so
 * the run ends by its own words, never the job's timeout. A run again carries the same
 * generation on (the longest, the worst stream's, is under 42 minutes: `backup.ts`), and a
 * release gives the pin up.
 */
export const PIN_WAIT_MS = 20 * 60_000;

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
