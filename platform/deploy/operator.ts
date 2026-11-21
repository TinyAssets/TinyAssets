/**
 * The kit's operator path (d3 §G): what its modes `control` and `owner` (`deploy.ts`) read and
 * send.
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
 * - **`owner`** asks the doors one op of `OPERATOR_OPS` (`--act`, `ownerAct`), with
 *   `owner.state` before and after it, through the operator channel
 *   (`contract/src/operator-message.ts`): each a message MAC'd under
 *   `OPERATOR_KEYRING`'s active kid and put on the operator queue (`QUEUES.OPERATOR`) over the
 *   Queues API, then its answer read from the operator bucket (`OPERATOR_NAMES`) every
 *   `POLL_MS`, for at most `WAIT_MS`. An answer is held to the message it names; none in time
 *   is a loud stop naming the ask and the dead-letter queue, where a message the doors gave up
 *   on is left.
 * - **`rotation`** asks one keyring's op of every owner the directory lists, a batch push at a
 *   time (`rotate`, `askOwners`), its progress in the control namespace (`RotationRecord`).
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
import { keyed, object, whole } from "../contract/src/checks.ts";
import { sha256Hex } from "../contract/src/digest.ts";
import type { Ring } from "../contract/src/envelope.ts";
import { HEX16, HEX32, HEX64, OWNER_ENTRY } from "../contract/src/ids.ts";
import {
  answerKey, type OperatorAnswer, operatorAnswer, type OperatorArgs, type OperatorMessage,
  type OperatorOp, OPERATOR_OPS, signOperator,
} from "../contract/src/operator-message.ts";
import { QUEUES } from "../contract/src/queue-names.ts";
import { BATCH, type Cloudflare } from "./cloudflare.ts";
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

/** What an owner loses at a restore, which its plan says. */
export const RESTORE_LOSES = "the owner loses its sessions (it signs in again), every agent's " +
  "grant (each authorizes again), its API keys and inbound URLs (it makes new ones), its model " +
  "connections (it connects them again), and anything written after the generation";

/** How often the kit reads the bucket for an answer, and how long it waits for one. */
export const POLL_MS = 2_000;
export const WAIT_MS = 120_000;
/**
 * How long a mode that asks the doors again and again runs before it stops by its own words,
 * inside the apply job's 30 minutes (`platform-deploy.yml`) with an ask's `WAIT_MS` to spare,
 * never by the job's timeout: mode `restore` asking a pin to write the owner's present state
 * (it stops loudly, the owner still held; a run again carries the same generation on, the
 * longest, the worst stream's, under 42 minutes: `owner/src/backup.ts`, and a release gives the
 * pin up), and mode `rotation` sweeping the directory (it stops at a page's end, its record
 * saying where a run again goes on).
 */
export const RUN_MS = 20 * 60_000;

/** The clock the kit waits by: replaced whole by the tests. */
export interface Waiting {
  readonly now: () => Date;
  readonly sleep: (ms: number) => Promise<void>;
}

/** The operator queue's id in `account`, or a loud stop while no dark deploy made it. */
async function operatorQueue(cloudflare: Cloudflare, account: string): Promise<string> {
  const queue = (await cloudflare.queues(account)).get(QUEUES.OPERATOR);
  if (queue === undefined) {
    throw new Error(`the account holds no queue ${QUEUES.OPERATOR}: no dark deploy made it`);
  }
  return queue;
}

/** The doors' answer to `message` in the operator bucket, held to it, or undefined while none. */
async function answerTo(cloudflare: Cloudflare, account: string, message: OperatorMessage):
    Promise<OperatorAnswer | undefined> {
  const found = await cloudflare.object(account, OPERATOR_NAMES.bucket, answerKey(message.ask));
  return found === undefined ? undefined : operatorAnswer(found, message);
}

/** `op` on `owner` with `args` asked of the doors in `account`, and their answer. */
export async function askOperator(cloudflare: Cloudflare, account: string, ring: Ring, op: string,
                                  owner: string, args: OperatorArgs, waiting: Waiting):
    Promise<OperatorAnswer> {
  const queue = await operatorQueue(cloudflare, account);
  const start = waiting.now().getTime();
  const message = await signOperator(ring, op, owner, args, Math.floor(start / 1000));
  await cloudflare.send(account, queue, message);
  for (;;) {
    await waiting.sleep(POLL_MS);
    const answer = await answerTo(cloudflare, account, message);
    if (answer !== undefined) {
      return answer;
    }
    if (waiting.now().getTime() - start >= WAIT_MS) {
      throw new Error(`the doors did not answer the operator message ${message.ask} (${op} on ` +
                      `${owner}) in ${WAIT_MS / 1000} s: if it is in the dead-letter queue ` +
                      `${OPERATOR_NAMES.dead_letter_queue}, they gave up on it`);
    }
  }
}

// --- Mode `rotation` --------------------------------------------------------------------

/**
 * The gap before each answer a rotation reads: Cloudflare's API takes 1,200 requests per five
 * minutes per user (its global rate limit, 4 a second; Fact 15, a vendor's limit), and a
 * rotation reads one answer an owner, so it reads 2.5 a second, its batch pushes and listings
 * well inside the rest.
 */
export const PACE_MS = 400;

/** One ask of `askOwners`: whom it is addressed to, and its args. */
export interface Ask {
  readonly owner: string;
  readonly args: OperatorArgs;
}

/**
 * `op` asked of the doors for each of `asks` at once (batch pushes of `BATCH`), and each one's
 * answer, in order: null for one not answered within `WAIT_MS`, which the dead-letter queue
 * may hold. Each answer is read at least once, each read after a `PACE_MS` gap.
 */
export async function askOwners(cloudflare: Cloudflare, account: string, ring: Ring, op: string,
                                asks: readonly Ask[], waiting: Waiting):
    Promise<(OperatorAnswer | null)[]> {
  const queue = await operatorQueue(cloudflare, account);
  const start = waiting.now().getTime();
  const messages = await Promise.all(asks.map(({ owner, args }) =>
    signOperator(ring, op, owner, args, Math.floor(start / 1000))));
  await cloudflare.sendBatch(account, queue, messages);
  const answers: (OperatorAnswer | null)[] = messages.map(() => null);
  do {
    for (const [at, message] of messages.entries()) {
      if (answers[at] === null) {
        await waiting.sleep(PACE_MS);
        answers[at] = await answerTo(cloudflare, account, message) ?? null;
      }
    }
  } while (answers.includes(null) && waiting.now().getTime() - start < WAIT_MS);
  return answers;
}

/** A rotation's record's kind. */
const ROTATION_KIND = "tinyassets-rotation";

/**
 * One pass of a rotation over the directory: when its first answer was done (`began`), the
 * last owner of the last page answered whole, in the directory's order (`through`: null
 * before the first), the owners whose answer left a blob under another kid, or that no answer
 * reached (`owing`), and when its last answer was done once `through` is the directory's end
 * and nothing is owing (`swept`). Every time is an operator answer's `done`, in seconds.
 */
export interface Pass {
  readonly began: number | null;
  readonly through: string | null;
  readonly owing: readonly string[];
  readonly swept: number | null;
}

/**
 * A rotation's progress (`deploy:rotation:<keyring>:<active kid>` in the control namespace),
 * written after each page: the keyring, its active kid, each kid of the ring it was begun with
 * by the SHA-256 of its key (never the key), and its passes in order.
 */
export interface RotationRecord {
  readonly kind: typeof ROTATION_KIND;
  readonly keyring: string;
  readonly active: string;
  readonly keys: Readonly<Record<string, string>>;
  readonly passes: readonly Pass[];
}

/** Where a rotation of `keyring` to `active` keeps its record, in the control namespace. */
export const rotationKey = (keyring: string, active: string): string =>
  `deploy:rotation:${keyring}:${active}`;

/** Each kid of `ring` by the SHA-256 of its key, as hex. */
export function keyHashes(ring: Ring): Record<string, string> {
  return Object.fromEntries([...ring.keys].map(([kid, key]) => [kid, sha256Hex(key)]));
}

const seconds = (value: unknown): boolean => value === null || whole(value, 0);

/** The record `key` holds, in its exact shape, or a loud stop: a malformed record is a bug. */
export function rotationRecord(value: unknown, key: string): RotationRecord {
  const passes = object(value) ? value["passes"] : undefined;
  const keys = object(value) ? value["keys"] : undefined;
  if (!keyed(value, ["kind", "keyring", "active", "keys", "passes"])
      || value["kind"] !== ROTATION_KIND || typeof value["keyring"] !== "string"
      || typeof value["active"] !== "string" || !object(keys)
      || !Object.values(keys).every((hash) => typeof hash === "string" && HEX64.test(hash))
      || !Array.isArray(passes) || !passes.every((pass) =>
        keyed(pass, ["began", "through", "owing", "swept"]) && seconds(pass["began"])
        && seconds(pass["swept"]) && (pass["through"] === null
          || typeof pass["through"] === "string") && Array.isArray(pass["owing"])
        && pass["owing"].every((owner: unknown) => typeof owner === "string"))) {
    throw new Error(`the rotation record ${key} is out of shape`);
  }
  return value as unknown as RotationRecord;
}

/** Owners in the directory's order: by their names' UTF-8 bytes, as KV lists keys. */
const inOrder = (a: string, b: string): number => Buffer.compare(Buffer.from(a), Buffer.from(b));

/**
 * Why `owner` still owes after `answer` (null: it owes nothing): no answer, a refusal (but
 * `owner_erased`: an erased owner's keys went with its object), or blobs `left` under another
 * kid. An answer that left nothing yet names a kid other than `active` stops the run: the
 * deployed ring is not the stored one. One that left some names the kid the owner's own key
 * is still under (a restore's hold, a key that no longer opens), which proves no ring.
 */
export function owes(owner: string, answer: OperatorAnswer | null, active: string):
    string | null {
  if (answer === null) {
    return `no answer within ${WAIT_MS / 1000} s (the dead-letter queue ` +
      `${OPERATOR_NAMES.dead_letter_queue} may hold it)`;
  }
  if ("refused" in answer) {
    return answer.refused.code === "owner_erased" ? null
      : `refused ${answer.refused.code}: ${answer.refused.message}`;
  }
  const result = answer.result;
  if (!object(result) || !whole(result["left"], 0)
      || (result["kid"] !== null && typeof result["kid"] !== "string")) {
    throw new Error(`the doors answered ${answer.op} for ${owner} out of shape: ` +
                    JSON.stringify(result));
  }
  if (result["left"] === 0 && result["kid"] !== null && result["kid"] !== active) {
    throw new Error(`the doors answered ${answer.op} for ${owner} under ${result["kid"]}, not ` +
                    `the stored ring's active kid ${active}: the deployed ring is not the ` +
                    "stored one (deploy it with --rotate), and nothing of this page is recorded");
  }
  return result["left"] === 0 ? null : `left ${result["left"]}`;
}

/** What a rotation reaches and is given. */
export interface Sweep {
  readonly cloudflare: Cloudflare;
  readonly account: string;
  /** The operator channel's ring, which the messages are MAC'd under. */
  readonly operator: Ring;
  /** The keyring swept, by name, the op that sweeps one owner, and the ring as stored. */
  readonly keyring: string;
  readonly op: string;
  readonly stored: Ring;
  /** The directory's and the control record's namespaces, by id. */
  readonly directory: string;
  readonly control: string;
  readonly waiting: Waiting;
  readonly say: (...lines: string[]) => void;
}

/**
 * The record a rotation of the stored ring goes on from (`rotationKey`), or a new one; one
 * begun with another ring under the same active kid stops the run.
 */
export async function heldRotation(cloudflare: Cloudflare, account: string, control: string,
                                   keyring: string, stored: Ring): Promise<RotationRecord> {
  const key = rotationKey(keyring, stored.active);
  const keys = keyHashes(stored);
  const held = await cloudflare.value(account, control, key);
  if (held === undefined) {
    return { kind: ROTATION_KIND, keyring, active: stored.active, keys, passes: [] };
  }
  const record = rotationRecord(held, key);
  if (record.keyring !== keyring || record.active !== stored.active
      || JSON.stringify(Object.entries(record.keys).sort())
        !== JSON.stringify(Object.entries(keys).sort())) {
    throw new Error(`the rotation record ${key} was begun with another ring under ` +
                    `${stored.active} (kids ${Object.keys(record.keys).join(", ")}): nothing is ` +
                    "asked");
  }
  return record;
}

/**
 * Mode `rotation`'s sweep (`deploy.ts`): `op` asked of every owner the directory lists
 * (`OWNER_ENTRY`), a page of `BATCH` at a time, until a pass answers `left` 0 for every one.
 * - The last pass goes on, or, once it has swept, a new one begins.
 * - The owners it owes are asked first; then the directory from after `through`, in its order;
 *   then whoever owes, once more. Whoever still owes is left in the record, named with its
 *   restore step (`owner.state`), for the next run to ask first: no sleep loop, no queue retry.
 * - The record is written after every page, so a crashed run goes on after `through` and a
 *   page cut short is asked again (every op is idempotent).
 * - Every answer's kid is held to the stored ring's active kid (`owes`), which stops the run.
 * - At `RUN_MS` it stops at a page's end, saying to run again.
 */
export async function rotate(sweep: Sweep): Promise<RotationRecord> {
  const { cloudflare, account, control, stored, waiting, say } = sweep;
  const key = rotationKey(sweep.keyring, stored.active);
  const record = await heldRotation(cloudflare, account, control, sweep.keyring, stored);
  const passes = [...record.passes];
  const last = passes.at(-1);
  let pass: Pass = last ?? { began: null, through: null, owing: [], swept: null };
  if (last === undefined || last.swept !== null) {
    pass = { began: null, through: null, owing: [], swept: null };
    passes.push(pass);
    say(`begin pass ${passes.length} of ${sweep.keyring}'s rotation to ${stored.active}`);
  } else {
    say(`carry pass ${passes.length} of ${sweep.keyring}'s rotation to ${stored.active} on, ` +
        `after ${pass.through ?? "no owner yet"}, ${pass.owing.length} owing`);
  }
  const listed = (await cloudflare.keys(account, sweep.directory, OWNER_ENTRY))
    .map((name) => name.slice(OWNER_ENTRY.length)).sort(inOrder);
  const after = (through: string | null) => through === null ? listed
    : listed.filter((owner) => inOrder(owner, through) > 0);
  const until = waiting.now().getTime() + RUN_MS;
  const why = new Map<string, string>();
  let written: RotationRecord = record;
  /** `owners` asked, the pass moved on by `move`, and the record written: false past `RUN_MS`. */
  const page = async (owners: readonly string[],
                      move: (owed: readonly string[]) => Pick<Pass, "through" | "owing">) => {
    if (waiting.now().getTime() >= until) {
      return false;
    }
    const answers = await askOwners(cloudflare, account, sweep.operator, sweep.op,
                                    owners.map((owner) => ({ owner, args: {} })), waiting);
    const owed = owners.filter((owner, at) => {
      const reason = owes(owner, answers[at]!, stored.active);
      if (reason !== null) {
        why.set(owner, reason);
      }
      return reason !== null;
    });
    const done = answers.flatMap((answer) => answer === null ? [] : [answer.done]);
    const moved = move(owed);
    const ended = moved.owing.length === 0 && after(moved.through).length === 0;
    pass = { began: pass.began ?? (done.length === 0 ? null : Math.min(...done)), ...moved,
             swept: ended && done.length > 0 ? Math.max(...done) : null };
    passes[passes.length - 1] = pass;
    written = { ...record, passes: [...passes] };
    const status = await cloudflare.put(account, control, key, written);
    if (status !== 200) {
      throw new Error(`writing the rotation record ${key} failed (${status})`);
    }
    say(`asked ${owners.length} owners ${sweep.op}: ${owed.length} owe`);
    return true;
  };
  const owingAgain = async (): Promise<boolean> => {
    for (const owners of pages(pass.owing, BATCH)) {
      if (!await page(owners, (owed) => ({ through: pass.through, owing: [
        ...pass.owing.filter((owner) => !owners.includes(owner)), ...owed] }))) {
        return false;
      }
    }
    return true;
  };
  let finished = await owingAgain();
  for (const owners of finished ? pages(after(pass.through), BATCH) : []) {
    if (!await page(owners, (owed) => ({ through: owners.at(-1)!,
                                         owing: [...pass.owing, ...owed] }))) {
      finished = false;
      break;
    }
  }
  finished = finished && await owingAgain();
  if (!finished) {
    say(`stopped after ${RUN_MS / 60_000} minutes, the record saying where: run again to go on`);
    return written;
  }
  if (pass.swept !== null) {
    say(`pass ${passes.length} swept every owner to left 0, the last at ${
      new Date(pass.swept * 1000).toISOString()}`);
    return written;
  }
  if (listed.length === 0) {
    say("the directory lists no owner: nothing is swept");
    return written;
  }
  for (const owners of pages(pass.owing, BATCH)) {
    if (waiting.now().getTime() >= until) {
      say(`${pass.owing.length} owners still owe, the rest unnamed past ${RUN_MS / 60_000} ` +
          "minutes: the record holds them, and the next run asks them first");
      break;
    }
    const states = await askOwners(cloudflare, account, sweep.operator, "owner.state",
                                   owners.map((owner) => ({ owner, args: {} })), waiting);
    say(...owners.map((owner, at) => {
      const state = states[at] ?? null;
      return `${owner} still owes (${why.get(owner) ?? "carried"}): ${state === null
        ? "owner.state unanswered" : "refused" in state ? `owner.state refused ${
          state.refused.code}` : `restore step ${String((state.result as
            { restore?: unknown }).restore ?? "none")}`}`;
    }), "run again once their restores are done: it asks them first");
  }
  return written;
}

/** `items` in pages of `size`. */
export function pages<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let at = 0; at < items.length; at += size) {
    out.push(items.slice(at, at + size));
  }
  return out;
}
