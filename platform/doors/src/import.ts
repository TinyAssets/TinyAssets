/**
 * The cutover import's consumer (`import_research.md` §3 and §4, slices 4 and 5): the import
 * queue's, as the ring's and the erasure's are (`{owner, ...}` messages, loud drops). For each
 * owner the importer's job (`platform/import/stage.ts`) put a bundle in the staging bucket
 * (`IMPORT_STAGING`, at the message's `prefix`) and one message on the queue
 * (`contract/src/import-message.ts`); its later steps (`platform/import/window.ts`) put a
 * `check`, a `discard` or an `admit` message for it, each answered in the staging bucket.
 * Deleted at the cutover with the job, the import doors, the queue, its dead-letter queue,
 * the bucket and `IMPORT_KEY` (tasks.md §9).
 *
 * Every gate holds before anything is written, in this order, and a message that fails one
 * is dropped loudly (acknowledged, so the queue never retries it):
 * 1. the window's `IMPORT_KEY` is set (a Worker secret set only for the window): without
 *    it, every message is dropped;
 * 2. the message is in its exact shape;
 * 3. its MAC under `IMPORT_KEY` over its owner and bundle digest holds;
 * 4. the directory holds no `o:` record of the owner: no owner who ever signed up, nor one
 *    erased, is imported into, checked or admitted (an admit of an owner whose record says
 *    `admitted` already goes on, idempotently, and a discard takes a rehearsal's admitted
 *    record, `rehearsalRun`, never another; an op the job waits on reads its index first,
 *    so this refusal is answered too);
 * 5. the bundle's index (`bundle.json`) is the bundle the MAC names (its SHA-256), and every
 *    part it names (the owner, the rows and manifest pages) is that part (its own SHA-256),
 *    each read and checked before the slice's first write; every inline file is its content
 *    id and every chunk its hash, each checked as it is read, before it is written;
 * 6. the owner's object is `pending`: its import doors refuse anything else
 *    (a terminal `refused`), so a live owner's object takes nothing; only the discard takes a
 *    rehearsal's name in any state.
 *
 * A `check`, `discard` or `admit` reads the index alone (the admit its `owner.json` too), then:
 * - `check`: each part of `importCheck` (`CHECK_PARTS`) page by page, each page written at
 *   `answerName(prefix, ask, "<part>-<n>")`, then the rest at `answerName(prefix, ask)`, in
 *   bounded slices with their own continuation, as an import's;
 * - `discard`: `importDiscard`, its namespace answered;
 * - `admit`: the owner as the old stack held it (`owner.json`): never one it never admitted
 *   (`absent`); `tierSet`, `admissionSet {admit:true}`, each suspension's reason, then the
 *   directory's `o:` record admitted, in that order (review L8), each idempotent. The job
 *   sends it only once every owner's import is done, its dead-letter queue is empty and
 *   every check shows no difference (`window.ts`).
 * A gate or refusal after the MAC and the index held is answered too (`refused`), so the job
 * reads why. An import's last slice marks it done (`doneName`).
 *
 * Then, in the bundle's order, each step through the owner's import doors: the rows page by
 * page (`importRows`); each manifest page's folders and inline files in batches of the
 * doors' bounds (`importFiles`), and each chunked file through the upload door's own steps
 * (`importFileBegin`, its chunks one at a time from the bucket to `FILES` by
 * `file-bytes.ts`, `importFileStored`, `importFileCommit`), its bytes never passing through
 * the object, each file with its mtime; then the owner's one notice (`importNotice`). A
 * delivery is one bounded slice:
 * at most `SLICE_MS` or `SLICE_BYTES` moved, after which it sends its own continuation (the
 * same message, with the step and entry to go on from) and is acknowledged. Every step is
 * idempotent from the owner's own state, so a slice killed anywhere costs a repeat and the
 * run converges: a refusal of the moment, or any failure but a gate's, is retried by the
 * queue, then left in its dead-letter queue for the job (`window.ts`, `dlq`). Every staged
 * object is read only when the bucket says it is at most `IMPORT_HOLD_BYTES` (one chunk, as
 * the upload door holds one), so at most one is in hand at once.
 */
import { chunkedId, inlineId } from "../../contract/src/content";
import { sha256Hex } from "../../contract/src/digest";
import { HEX64 } from "../../contract/src/ids";
import {
  answerName, CHECK_PARTS, type Cursor, doneName, IMPORT_BATCH_BYTES, IMPORT_FILES, IMPORT_KEY,
  importKeyBytes, importMessage, type ImportMessage, macHolds, rehearsalRun,
} from "../../contract/src/import-message";
import { CHUNK_BYTES, INLINE_BYTES } from "../../contract/src/limits";
import { refusalOf } from "../../contract/src/refusal";

import { bindImport, bindPlatformFor, type Bound } from "./binder";
import { namedSecret, now } from "./config";
import type { DoorsEnv } from "./env";
import { importTiers } from "./control";
import { putOwner, readOwner } from "./directory";
import { putImportChunk } from "./file-bytes";

/** A delivery's slice: at most this long, or this many bytes moved, then its continuation. */
export const SLICE_MS = 60_000;
export const SLICE_BYTES = 256 * 1024 * 1024;
/**
 * What a delivery holds at once: one staged object in hand, at most a chunk, as the upload door
 * holds one; a larger object is never read (`object`).
 */
export const IMPORT_HOLD_BYTES = 4 * 1024 * 1024;
/** The suspensions an admit carries from the old stack (`admission.ts`'s reasons). */
const SUSPENSIONS: readonly string[] = Object.freeze(["abuse", "billing"]);
/** The bundle format the consumer reads (`import/bundle.ts`'s `FORMAT`). */
export const BUNDLE_FORMAT = 1;

/** A gate that does not hold, or a bundle that is not itself: the message is dropped. */
export class Dropped extends Error {
  override readonly name = "Dropped";
}

/** A bundle's index (`bundle.json`), as the job wrote it and the MAC names it. */
interface Index {
  readonly parts: Readonly<Record<string, string>>;
}

/** One entry of the bundle's manifest (`import/build.ts`'s `Entry`). */
interface Entry {
  readonly path: string;
  readonly kind: "dir" | "file";
  readonly size?: number;
  readonly mode?: 0 | 1;
  readonly mtime_ms?: number;
  readonly content_id?: string;
  readonly chunks?: readonly string[];
}

/** A step of the bundle: a rows page, a manifest page, or the notice. */
type Step = { readonly kind: "rows" | "manifest"; readonly part: string }
  | { readonly kind: "notice" };

/** The bundle's steps in its order: the rows pages, the manifest pages, then the notice. */
export function stepsOf(index: Index): Step[] {
  const pages = (kind: "rows" | "manifest"): Step[] => {
    const numbers = Object.keys(index.parts)
      .map((name) => new RegExp(`^${kind}/(0|[1-9][0-9]{0,8})\\.json$`).exec(name))
      .flatMap((match) => match === null ? [] : [Number(match[1])]).sort((a, b) => a - b);
    // Each page is numbered from 0 with no gap, so none is left out unseen.
    if (numbers.some((number, n) => number !== n)) {
      throw new Dropped("a bundle's pages are numbered from 0 with no gap");
    }
    return numbers.map((n) => ({ kind, part: `${kind}/${n}.json` }));
  };
  return [...pages("rows"), ...pages("manifest"), { kind: "notice" }];
}

/**
 * The staging bucket's object at `name`, its bytes, or a loud failure (a retry); one larger than
 * `IMPORT_HOLD_BYTES` is dropped before its bytes are read, so a delivery holds one at most.
 */
async function object(env: DoorsEnv, message: ImportMessage, name: string): Promise<Uint8Array> {
  const found = await env.IMPORT_STAGING.get(`${message.prefix}/${name}`);
  if (found === null) {
    throw new Error(`the staging bucket holds no ${name} of the bundle`);
  }
  if (found.size > IMPORT_HOLD_BYTES) {
    await found.body.cancel();
    throw new Dropped(`the bundle's ${name} is past the ${IMPORT_HOLD_BYTES} bytes a delivery holds`);
  }
  return new Uint8Array(await found.arrayBuffer());
}

/** An answer to the job, at `name` in the staging bucket, as JSON. */
async function answer(env: DoorsEnv, name: string, value: unknown): Promise<void> {
  await env.IMPORT_STAGING.put(name, `${JSON.stringify(value)}\n`);
}

const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The bundle's index, held to the digest the MAC names, the message's owner and format. */
async function indexOf(env: DoorsEnv, message: ImportMessage): Promise<Index> {
  const bytes = await object(env, message, "bundle.json");
  if (sha256Hex(bytes) !== message.bundle) {
    throw new Dropped("the bundle's index is not the bundle its message names");
  }
  const index: unknown = JSON.parse(new TextDecoder().decode(bytes));
  if (!plain(index) || index["format"] !== BUNDLE_FORMAT || index["owner"] !== message.owner
      || !plain(index["parts"])
      || !Object.values(index["parts"]).every((sha) => typeof sha === "string" && HEX64.test(sha))
      || !("owner.json" in index["parts"])) {
    throw new Dropped("the bundle's index is out of shape, or another owner's");
  }
  return { parts: index["parts"] as Record<string, string> };
}

/** One part the index names, held to its SHA-256 there, as JSON. */
async function part(env: DoorsEnv, message: ImportMessage, index: Index, name: string):
    Promise<Record<string, unknown>> {
  const want = index.parts[name];
  if (want === undefined) {
    throw new Dropped(`the bundle's index names no ${name}`);
  }
  const bytes = await object(env, message, name);
  if (sha256Hex(bytes) !== want) {
    throw new Dropped(`the bundle's ${name} is not the part its index names`);
  }
  const value: unknown = JSON.parse(new TextDecoder().decode(bytes));
  if (!plain(value)) {
    throw new Dropped(`the bundle's ${name} is out of shape`);
  }
  return value;
}

/**
 * Every part the index names (the owner, the rows and manifest pages) held to its SHA-256
 * there, one at a time, before a slice writes anything: so a page changed under its index
 * drops the message before its first write, not partway. Each inline file and chunk is held
 * to its own id as it is read, before it is written.
 */
async function verified(env: DoorsEnv, message: ImportMessage, index: Index): Promise<void> {
  for (const name of Object.keys(index.parts)) {
    await part(env, message, index, name);
  }
}

/** A list a page holds under `key`, or a drop. */
function listOf(page: Record<string, unknown>, key: string): unknown[] {
  const list = page[key];
  if (!Array.isArray(list)) {
    throw new Dropped(`a bundle's page holds no ${key}`);
  }
  return list;
}

function entryOf(value: unknown): Entry {
  if (!plain(value) || typeof value["path"] !== "string"
      || (value["kind"] !== "dir" && value["kind"] !== "file")) {
    throw new Dropped("a bundle's manifest entry is out of shape");
  }
  if (value["kind"] === "file" && (typeof value["size"] !== "number"
      || !Number.isSafeInteger(value["size"]) || value["size"] < 0
      || (value["mode"] !== 0 && value["mode"] !== 1) || typeof value["content_id"] !== "string"
      || typeof value["mtime_ms"] !== "number" || !Number.isSafeInteger(value["mtime_ms"])
      || value["mtime_ms"] < 0)) {
    throw new Dropped("a bundle's file entry is out of shape");
  }
  return value as unknown as Entry;
}

/** A slice's bounds: production's, unless a test passes smaller. */
export interface SliceBounds {
  readonly ms: number;
  readonly bytes: number;
}

const SLICE: SliceBounds = Object.freeze({ ms: SLICE_MS, bytes: SLICE_BYTES });

/** One delivery's slice of one owner's bundle. */
class Slice {
  readonly #env: DoorsEnv;
  readonly #message: ImportMessage;
  readonly #index: Index;
  readonly #bound: Bound;
  readonly #bounds: SliceBounds;
  readonly #started = Date.now();
  #moved = 0;
  /** The owner's doors this slice called: none yet, and it is never over. */
  #calls = 0;

  constructor(env: DoorsEnv, message: ImportMessage, index: Index, bound: Bound,
              bounds: SliceBounds) {
    this.#env = env;
    this.#message = message;
    this.#index = index;
    this.#bound = bound;
    this.#bounds = bounds;
  }

  /**
   * Whether the slice has run its time or moved its bytes, once it has called a door at
   * least: so every slice makes progress, and a run always ends.
   */
  #over(): boolean {
    return this.#calls > 0 && (Date.now() - this.#started >= this.#bounds.ms
                               || this.#moved >= this.#bounds.bytes);
  }

  /** A door's call, counted. */
  #call<T>(call: Promise<T>): Promise<T> {
    this.#calls += 1;
    return call;
  }

  /** From `at` on: null once the bundle is done, else the cursor to go on from. */
  async run(at: Cursor): Promise<Cursor | null> {
    const steps = stepsOf(this.#index);
    const { binding, stub } = this.#bound;
    for (let step = at.step, entry = at.entry; step < steps.length; step += 1, entry = 0) {
      const each = steps[step]!;
      if (this.#over()) {
        return { step, entry };
      }
      if (each.kind === "notice") {
        const owner = await part(this.#env, this.#message, this.#index, "owner.json");
        if (typeof owner["notice"] !== "string") {
          throw new Dropped("the bundle's owner holds no notice");
        }
        await this.#call(stub.importNotice(binding, { detail: owner["notice"] }));
        continue;
      }
      const page = await part(this.#env, this.#message, this.#index, each.part);
      if (each.kind === "rows") {
        await this.#call(stub.importRows(binding, { rows: listOf(page, "rows") }));
        continue;
      }
      const stopped = await this.#files(listOf(page, "entries").map(entryOf), entry);
      if (stopped !== null) {
        return { step, entry: stopped };
      }
    }
    return null;
  }

  /**
   * A check from `at` (its part and page): each page of `importCheck` written for the job, then
   * the rest; null once the answer is written, else the cursor to go on from.
   */
  async check(at: Cursor, ask: string): Promise<Cursor | null> {
    const { binding, stub } = this.#bound;
    const prefix = this.#message.prefix;
    for (let step = at.step, page = at.entry; step < CHECK_PARTS.length; step += 1, page = 0) {
      const part = CHECK_PARTS[step]!;
      for (;;) {
        if (this.#over()) {
          return { step, entry: page };
        }
        this.#calls += 1;
        const read = await stub.importCheck(binding, { part, page });
        if (part === "state") {
          await answer(this.#env, answerName(prefix, ask),
                       { op: "check", bundle: this.#message.bundle, ...read });
          return null;
        }
        await answer(this.#env, answerName(prefix, ask, `${part}-${page}`), read);
        if (!read.more) {
          break;
        }
        page += 1;
      }
    }
    return null;
  }

  /** A manifest page's entries from `first`: null when done, else the entry to go on from. */
  async #files(entries: readonly Entry[], first: number): Promise<number | null> {
    const { binding, stub } = this.#bound;
    let batch: unknown[] = [];
    let bytes = 0;
    const flush = async () => {
      if (batch.length > 0) {
        await this.#call(stub.importFiles(binding, { entries: batch }));
      }
      batch = [];
      bytes = 0;
    };
    for (let at = first; at < entries.length; at += 1) {
      if (this.#over()) {
        await flush();
        return at;
      }
      const entry = entries[at]!;
      const size = new TextEncoder().encode(entry.path).byteLength;
      if (entry.kind === "file" && entry.size! > INLINE_BYTES) {
        await flush();
        if (!await this.#chunked(entry)) {
          return at;
        }
        continue;
      }
      const item = entry.kind === "dir" ? { kind: "dir", path: entry.path }
        : await this.#inline(entry);
      const weight = size + (entry.kind === "file" ? entry.size! : 0);
      if (batch.length === IMPORT_FILES || bytes + weight > IMPORT_BATCH_BYTES) {
        await flush();
      }
      batch.push(item);
      bytes += weight;
    }
    await flush();
    return null;
  }

  /** An inline file and its bytes, held to its content id. */
  async #inline(entry: Entry): Promise<unknown> {
    const bytes = await object(this.#env, this.#message, `inline/${entry.content_id}`);
    if (bytes.byteLength !== entry.size || inlineId(bytes) !== entry.content_id) {
      throw new Dropped("an inline file's bytes are not its content id");
    }
    return { kind: "file", path: entry.path, size: entry.size, mode: entry.mode,
             content_id: entry.content_id, mtime_ms: entry.mtime_ms, bytes };
  }

  /**
   * A chunked file through the upload door's steps, one chunk in hand at a time: true once it
   * is committed or was there already, false when the slice ran out first (its upload stays
   * open, and the next slice resumes it).
   */
  async #chunked(entry: Entry): Promise<boolean> {
    const { binding, stub } = this.#bound;
    const chunks = entry.chunks;
    if (!Array.isArray(chunks) || !chunks.every((sha) => typeof sha === "string" && HEX64.test(sha))
        || chunkedId(entry.size!, chunks) !== entry.content_id) {
      throw new Dropped("a chunked file's chunks are not its content id");
    }
    const request = { path: entry.path, size: entry.size!, mode: entry.mode!,
                      content_id: entry.content_id!, chunks: [...chunks] };
    this.#calls += 1;
    const upload = await stub.importFileBegin(binding, request);
    if (upload.skipped) {
      return true;
    }
    const plan = new Map(upload.chunks.map((chunk) => [chunk.sha256, chunk]));
    let put = 0;
    for (const sha of new Set(upload.pending)) {
      // A slice that begins or resumes a file puts at least one of its chunks: it ends.
      if (put > 0 && this.#over()) {
        return false;
      }
      put += 1;
      const chunk = plan.get(sha);
      if (chunk === undefined || chunk.size > CHUNK_BYTES) {
        throw new Error("an import upload's plan names no such chunk");
      }
      const bytes = await object(this.#env, this.#message, `chunks/${sha}`);
      if (sha256Hex(bytes) !== sha) {
        throw new Dropped("a chunk's bytes are not its hash");
      }
      await putImportChunk(this.#env, upload, chunk, bytes);
      this.#moved += bytes.byteLength;
      await this.#call(stub.importFileStored(binding, { intent_id: upload.intent_id,
                                                        chunks: [sha] }));
    }
    await this.#call(stub.importFileCommit(binding, {
      intent_id: upload.intent_id, path: entry.path, mode: entry.mode!,
      content_id: entry.content_id!, mtime_ms: entry.mtime_ms! }));
    return true;
  }
}

/** The window's key, held to its grammar, and the message, held to its shape and MAC. */
function gated(env: DoorsEnv, body: unknown): ImportMessage {
  const key = namedSecret(env, IMPORT_KEY);
  if (key === null) {
    throw new Dropped("the doors hold no IMPORT_KEY: the import window is closed");
  }
  try {
    importKeyBytes(key);
  } catch (error) {
    throw new Dropped((error as Error).message);
  }
  let message: ImportMessage;
  try {
    message = importMessage(body);
  } catch {
    throw new Dropped("an import message is out of shape");
  }
  if (!macHolds(key, message)) {
    throw new Dropped("an import message's MAC does not hold under IMPORT_KEY");
  }
  return message;
}

/**
 * The directory's record of the owner, held to the op: none for an import or a check; none or
 * admitted for an admit (an admit cut short goes on); none, or a rehearsal's admitted one, for
 * a discard. Anyone else's is never imported into, checked, admitted or discarded.
 */
async function recorded(env: DoorsEnv, message: ImportMessage): Promise<void> {
  const record = await readOwner(env, message.owner);
  if (record === null || (record.s === "admitted" && (message.op === "admit"
      || (message.op === "discard" && rehearsalRun(message.owner) !== null)))) {
    return;
  }
  throw new Dropped("the directory holds a record of this owner: it is never imported into");
}

/** The continuation of `message` from `cursor`, sent as JSON (the job's reader takes JSON). */
async function goOn(env: DoorsEnv, message: ImportMessage, cursor: Cursor): Promise<"more"> {
  const { cursor: _was, ...first } = message;
  await env.IMPORTS.send({ ...first, cursor }, { contentType: "json" });
  return "more";
}

/**
 * The admit, as the old stack held the owner (`owner.json`): its tier set, then admitted, then
 * each suspension's reason, then its `o:` record (review L8); never an owner it never admitted.
 */
async function admitted(env: DoorsEnv, message: ImportMessage, index: Index):
    Promise<Record<string, unknown>> {
  const owner = await part(env, message, index, "owner.json");
  const admission = owner["admission"] as { state?: unknown; reasons?: unknown } | undefined;
  const reasons = admission?.reasons;
  if (owner["owner"] !== message.owner || typeof owner["tier"] !== "string"
      || (admission?.state !== "admitted" && admission?.state !== "suspended")
      || !Array.isArray(reasons) || !reasons.every((reason) => SUSPENSIONS.includes(reason))
      || (admission.state === "suspended") !== (reasons.length > 0)) {
    throw new Dropped("the bundle's owner is not one the old stack admitted: never admitted");
  }
  const { binding, stub } = bindPlatformFor(env, message.owner);
  await stub.tierSet(binding, { tier: owner["tier"] as "free" | "paid" });
  let state = await stub.admissionSet(binding, { admit: true });
  for (const reason of reasons as string[]) {
    state = await stub.admissionSet(binding, { reason, suspended: true });
  }
  await putOwner(env, message.owner, { s: "admitted", t: now() });
  return { op: "admit", bundle: message.bundle, tier: owner["tier"], ...state };
}

/**
 * One message's gates, then its op: `done` when its bundle or check is through (or its discard
 * or admit made), `more` when its continuation was sent. A gate that does not hold throws
 * `Dropped`; past the MAC and the index, an op the job waits on is answered as refused too.
 */
export async function importOne(env: DoorsEnv, body: unknown,
                                bounds: SliceBounds = SLICE): Promise<"done" | "more"> {
  const message = gated(env, body);
  const ask = message.ask;
  if (ask === undefined) {
    await recorded(env, message);
  }
  const index = await indexOf(env, message);
  stepsOf(index);
  if (ask === undefined) {
    await verified(env, message, index);
    const bound = bindImport(env, message.owner, message.bundle, await importTiers(env));
    const next = await new Slice(env, message, index, bound, bounds).run(message.cursor
      ?? { step: 0, entry: 0 });
    if (next !== null) {
      return goOn(env, message, next);
    }
    await answer(env, doneName(message.prefix), { op: "import", bundle: message.bundle,
                                                  done: true });
    return "done";
  }
  try {
    await recorded(env, message);
    if (message.op === "check") {
      const bound = bindImport(env, message.owner, message.bundle, await importTiers(env));
      const next = await new Slice(env, message, index, bound, bounds).check(message.cursor
        ?? { step: 0, entry: 0 }, ask);
      return next === null ? "done" : await goOn(env, message, next);
    }
    if (message.op === "discard") {
      const { binding, stub } = bindImport(env, message.owner, message.bundle,
                                           await importTiers(env));
      const { namespace } = await stub.importDiscard(binding);
      await answer(env, answerName(message.prefix, ask), { op: "discard",
                                                           bundle: message.bundle,
                                                           discarded: true, namespace });
      return "done";
    }
    await answer(env, answerName(message.prefix, ask), await admitted(env, message, index));
    return "done";
  } catch (error) {
    const refused = refusalOf(error);
    if (error instanceof Dropped || (refused !== null && !refused.transient)) {
      await answer(env, answerName(message.prefix, ask), {
        op: message.op, bundle: message.bundle,
        refused: error instanceof Dropped ? error.message : refused!.message });
    }
    throw error;
  }
}

/** The import queue's consumer: each message's op; a gate's drop or a terminal refusal acked. */
export async function importQueue(batch: MessageBatch<unknown>, env: DoorsEnv,
                                  bounds: SliceBounds = SLICE): Promise<void> {
  for (const message of batch.messages) {
    const at = (message.body as { prefix?: unknown } | null)?.prefix;
    const where = typeof at === "string" && at.length <= 96 ? at : "?";
    try {
      await importOne(env, message.body, bounds);
      message.ack();
    } catch (error) {
      if (error instanceof Dropped) {
        console.error(`an import message (${where}) is dropped: ${error.message}`);
        message.ack();
        continue;
      }
      // The owner's object's word: a terminal refusal (not pending, a conflicting file, a
      // malformed step) is dropped, never retried; one for the moment only is retried, as
      // any other failure is, and the queue's dead-letter queue keeps what it gives up on.
      const refused = refusalOf(error);
      if (refused !== null && !refused.transient) {
        console.error(`an import message (${where}) is refused by the owner's object; ` +
                      `dropped: ${refused.message}`);
        message.ack();
        continue;
      }
      console.error(`an import slice (${where}) failed; the queue tries again`, error);
      message.retry({ delaySeconds: 30 });
    }
  }
}
