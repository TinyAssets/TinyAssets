/**
 * The cutover import's queue messages (`import_research.md` §3 and §4, slices 3 to 5): what
 * the importer's job puts on the import queue for each owner (`platform/import/stage.ts`,
 * `platform/import/window.ts`), and what the doors' consumer takes (`doors/src/import.ts`).
 * One module, so the two cannot disagree; deleted at the cutover with both.
 *
 * A message is `{op, owner, bundle, prefix, mac}`; `check`, `discard` and `admit` add `ask`,
 * and a consumer's own continuation adds `cursor`:
 * - `op`, what the consumer does with the bundle: `import` it, `check` the owner's object
 *   against it (`importCheck`), `discard` the owner's object (`importDiscard`), or `admit`
 *   the owner (`tierSet`, `admissionSet`, any suspension, then its `o:` record);
 * - `owner`, the owner's name (the old principal, or a rehearsal's name, `rehearsalName`),
 *   whose object the bundle goes into;
 * - `bundle`, the SHA-256 of the bundle's `bundle.json` (`import/bundle.ts`), which names
 *   every other part by its own SHA-256;
 * - `prefix`, where the bundle sits in the staging bucket: `<run>/<key>`;
 * - `mac`, HMAC-SHA-256 under the window's `IMPORT_KEY` over the op, the owner and the
 *   bundle's digest, base64url: the key is the doors' Worker secret, set only for the
 *   window, and the job's input, so a stray or forged message names no bundle the doors take;
 * - `ask`, 16 hex the job picks: the consumer writes its answer at
 *   `<prefix>/answers/<ask>.json` (and a check's pages below `<prefix>/answers/<ask>/`) in
 *   the staging bucket, where the job reads it (`answerName`);
 * - `cursor`, the step and entry a continuation goes on from: a place to skip to, never an
 *   authority (every step is idempotent from the owner's own state, and a check only reads).
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

import { chunkedId, cut } from "./content";
import { HEX16, HEX64 } from "./ids";
import { principal } from "./text";

/**
 * The import doors' bounds (`owner/src/import.ts`), which the consumer batches by: a call's
 * most rows, and its most bytes encoded (the export's page); a call's most folders and
 * inline files, their bytes held to the same; and the most import uploads open at once,
 * under the files plane's `MAX_OPEN_INTENTS` (64).
 */
export const IMPORT_ROWS = 256;
export const IMPORT_BATCH_BYTES = 1024 * 1024;
export const IMPORT_FILES = 256;
export const IMPORT_INTENTS = 8;

/** The import queue, the doors' producer's and consumer's. */
export const IMPORT_QUEUE = "tinyassets-import";
/** The window's secret, by name: the doors' Worker secret, and the job's input. */
export const IMPORT_KEY = "IMPORT_KEY";
/** The shortest key, in bytes once decoded. */
export const IMPORT_KEY_MIN_BYTES = 32;
const DOMAIN = "tinyassets-import-v1";
const KEY = /^[A-Za-z0-9_-]+$/;
/** A run's id (the staging bucket's top folder), and an owner's key (`sha256(principal)[:16]`). */
export const RUN = /^[a-z0-9][a-z0-9-]{0,63}$/;
/** What a message asks of the consumer. */
export const IMPORT_OPS = Object.freeze(["import", "check", "discard", "admit"] as const);
export type ImportOp = (typeof IMPORT_OPS)[number];
const PREFIX = /^[a-z0-9][a-z0-9-]{0,63}\/[0-9a-f]{16}$/;
const MAC = /^[A-Za-z0-9_-]{43}$/;

/** Where a continuation goes on from: the bundle's step, and the entry within it. */
export interface Cursor {
  readonly step: number;
  readonly entry: number;
}

export interface ImportMessage {
  readonly op: ImportOp;
  readonly owner: string;
  readonly bundle: string;
  readonly prefix: string;
  readonly mac: string;
  readonly ask?: string;
  readonly cursor?: Cursor;
}

/** The key's bytes, or a loud failure: a key shorter than the window's, or not base64url. */
export function importKeyBytes(key: string): Buffer {
  if (!KEY.test(key) || Buffer.from(key, "base64url").byteLength < IMPORT_KEY_MIN_BYTES) {
    throw new Error(`${IMPORT_KEY} is at least ${IMPORT_KEY_MIN_BYTES} bytes, base64url`);
  }
  return Buffer.from(key, "base64url");
}

/**
 * The message's MAC: HMAC-SHA-256 under `key` over its op, the owner and the bundle's digest,
 * so a message MAC'd for one op is never taken as another's.
 */
export function importMac(key: string, op: ImportOp, owner: string, bundle: string): string {
  return createHmac("sha256", importKeyBytes(key))
    .update(`${DOMAIN}\0${op}\0${principal(owner)}\0${bundle}`).digest("base64url");
}

/** Whether `message`'s MAC is its op's, owner's and bundle's under `key`, in constant time. */
export function macHolds(key: string, message: ImportMessage): boolean {
  const want = Buffer.from(importMac(key, message.op, message.owner, message.bundle),
                           "base64url");
  const got = Buffer.from(message.mac, "base64url");
  return got.byteLength === want.byteLength && timingSafeEqual(got, want);
}

const whole = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

/** A message in its exact shape, or a throw: no field more, none missing, each its grammar. */
export function importMessage(value: unknown): ImportMessage {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("an import message is an object");
  }
  const body = value as Record<string, unknown>;
  const op = body["op"];
  if (!IMPORT_OPS.includes(op as ImportOp)) {
    throw new Error(`an import message's op is one of ${IMPORT_OPS.join(", ")}`);
  }
  const asks = op !== "import";
  const keys = Object.keys(body).filter((key) => key !== "cursor").sort().join();
  if (keys !== (asks ? "ask,bundle,mac,op,owner,prefix" : "bundle,mac,op,owner,prefix")) {
    throw new Error("an import message is its op, owner, bundle, prefix and mac, an ask " +
                    "for any op but an import, and a cursor");
  }
  const owner = principal(body["owner"]);
  const { bundle, prefix, mac, cursor, ask } = body;
  if (typeof bundle !== "string" || !HEX64.test(bundle) || typeof prefix !== "string"
      || !PREFIX.test(prefix) || typeof mac !== "string" || !MAC.test(mac)
      || (asks && (typeof ask !== "string" || !HEX16.test(ask)))) {
    throw new Error("an import message's bundle, prefix, mac or ask is out of shape");
  }
  const message: ImportMessage = { op: op as ImportOp, owner, bundle, prefix, mac,
                                   ...(asks ? { ask: ask as string } : {}) };
  if (cursor === undefined) {
    return message;
  }
  if (op === "discard" || op === "admit") {
    throw new Error("only an import's or a check's continuation carries a cursor");
  }
  const at = cursor as Record<string, unknown> | null;
  if (typeof at !== "object" || at === null || Object.keys(at).sort().join() !== "entry,step"
      || !whole(at["step"]) || !whole(at["entry"])) {
    throw new Error("an import message's cursor is a step and an entry");
  }
  return { ...message, cursor: { step: at["step"], entry: at["entry"] } };
}

/** Where the consumer writes the answer to `ask`, beside its bundle in the staging bucket. */
export function answerName(prefix: string, ask: string, page?: string): string {
  return page === undefined ? `${prefix}/answers/${ask}.json`
    : `${prefix}/answers/${ask}/${page}.json`;
}

/** Where the consumer marks an owner's import done: its whole bundle through the doors. */
export function doneName(prefix: string): string {
  return `${prefix}/answers/import.json`;
}

// -- THE CHECK (`importCheck`, `owner/src/import.ts`; the job's `verify`, `import/window.ts`) --

/** The check's parts, in its order: the files, every chunk read back, the rows, the rest. */
export const CHECK_PARTS = Object.freeze(["files", "chunks", "rows", "state"] as const);
export type CheckPart = (typeof CHECK_PARTS)[number];
/** A check page's most entries, chunks read back, and rows. */
export const CHECK_FILES = 256;
export const CHECK_CHUNKS = 8;
export const CHECK_ROWS = 256;

/** A file's or folder's line in a check's listing, as the files plane lists it. */
export interface CheckEntry {
  readonly path: string;
  readonly kind: "dir" | "file";
  readonly size: number;
  readonly mode: 0 | 1;
  readonly content_id: string | null;
  readonly mtime_ms: number;
}

/**
 * A chunk read back through `FILES` under the owner's data key: its hash as the owner's
 * contents name it, its size, and the hash the read proved (`FILES` opens the chunk and
 * rechecks its bytes against the hash it was asked for), or null when the bucket lacks it,
 * the key does not open it, or its bytes are not its hash.
 */
export interface CheckChunk {
  readonly sha256: string;
  readonly size: number;
  readonly read: string | null;
}

/** The rest: the policy's digest, the tier and admission, and the storage the owner uses. */
export interface CheckState {
  readonly policy_digest: string;
  readonly tier: string;
  readonly state: string;
  readonly reasons: readonly string[];
  readonly used_bytes: number;
  readonly limit_bytes: number | null;
}

/** A body's id: its UTF-8 bytes' content id as chunked content, as a placed body is named. */
export function bodyId(body: string): string {
  const bytes = new TextEncoder().encode(body);
  return chunkedId(bytes.byteLength, cut(bytes).map((chunk) => chunk.sha256));
}

/**
 * One collection row's leaf in the order-free digest: its collection, id and privacy, and its
 * body's id (`bodyId`), which names the body's bytes by their SHA-256s (a placed body's chunks
 * are each read back by the check's chunks part).
 */
export function rowLeaf(collection: string, rowId: string, privateRow: 0 | 1,
                        id: string): string {
  return createHash("sha256")
    .update(`ta-import-row\0${collection}\0${rowId}\0${privateRow}\0${id}`).digest("hex");
}

/** The order-free digest of no rows. */
export const NO_ROWS = "0000000000000000000000000000000000000000000000000000000000000000";
const SPACE = 1n << 256n;

/** Two order-free digests summed (each a sum of leaves, mod 2^256): the same in any order. */
export function rowsSum(a: string, b: string): string {
  return ((BigInt(`0x${a}`) + BigInt(`0x${b}`)) % SPACE).toString(16).padStart(64, "0");
}

// -- REHEARSAL NAMES (`import_research.md` §4) -----------------------------------------------

const REHEARSAL = /^rehearsal-([a-z0-9][a-z0-9-]{0,63})-([0-9a-f]{24})$/;

/**
 * A rehearsal's name for the real principal `owner` in run `run`:
 * `rehearsal-<run>-<sha256(owner)[:24]>`. No issuer's subject is one (an AuthKit owner's is
 * `user_...`, a foreign issuer's `iss-...`, and the doors refuse an owner issuer's subject
 * of this shape, `doors/src/binder.ts`), so no rehearsal's object is ever signed into, and
 * its discard (`importDiscard`) needs no erasure. The job and the discard door share it.
 */
export function rehearsalName(run: string, owner: string): string {
  if (!RUN.test(run)) {
    throw new Error("a run's id is lowercase letters, digits and dashes, at most 64");
  }
  const digest = createHash("sha256").update(principal(owner)).digest("hex");
  return `rehearsal-${run}-${digest.slice(0, 24)}`;
}

/** The run a rehearsal's name is of, or null for a name that is no rehearsal's. */
export function rehearsalRun(name: unknown): string | null {
  return typeof name === "string" ? REHEARSAL.exec(name)?.[1] ?? null : null;
}
