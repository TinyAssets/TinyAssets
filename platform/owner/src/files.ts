/**
 * Owner files: the manifest in the owner's own object (ADR-032, plan §6).
 *
 * The bytes are immutable chunks of at most 4 MiB in one R2 bucket shared by every
 * owner, each named by its SHA-256 under this owner's random 128-bit namespace `ns`,
 * which is made on first use and held only here. The files Worker (`FILES`) moves them
 * and never sees a path. Everything that says what a file is lives in this object's
 * SQLite: paths, versions and history, which chunks make which content, the chunks'
 * reference counts, upload intents, quotas, the change feed and the sync lease. Files of
 * 4 KiB or less are kept inline, so reading `AGENTS.md` or a skill touches no bucket.
 *
 * - Versions. Every commit is stamped with the next owner-wide `seq`, and a version is
 *   named by that seq: it never repeats across a delete, a re-create or a history prune,
 *   with no tombstone kept. Each write is a compare-and-swap on the entry's etag, as on
 *   the board (S34): `expected` null writes unconditionally, `""` only creates, and
 *   anything else must be the current etag; a lost swap is answered with the entry as it
 *   stands ({@link FileConflict}).
 * - Content. A content id names its kind and size with its bytes (inline, or a chunk
 *   list), so no content of one kind can name another's; content already stored under
 *   an id is reused only when it is the same content, else the write fails loudly. A
 *   content is held by every entry and every kept history row naming it, in any root,
 *   so content anything still names is never collected.
 * - History. Every workspace change appends a history row (`file_history`), which is
 *   also the change feed; the temporary area keeps none. Retention keeps each live
 *   path's newest `keep_versions`, and its versions younger than `retention_days` up to
 *   `keep_recent` in all; a version past those goes in the change that pushed it out. A
 *   row kept only for its age is due for pruning (`prune_after`) at that age, and
 *   housekeeping prunes due rows; a retention change is applied
 *   to the rows already kept, by housekeeping too. A gone path's rows are `released`:
 *   a change that would take history past its quota first prunes that many of them,
 *   earliest due first, so churn of deleted paths never fills it. `restore` writes an
 *   old version as a new one; `redact` drops one old version's content, whose bytes go
 *   once no other row holds the same content; `purge` drops a path's old versions.
 * - Uploads (restic's order: bytes, then index, then snapshot). An upload intent
 *   declares a file's size and its chunks, cut at 4 MiB, and records them as held
 *   before any byte is sent. A chunk counts as stored only once `FILES` accepted a put
 *   R2 verified, recorded after the put resolves; a commit refuses any chunk not stored.
 *   So every object in the bucket has a row first, garbage collection never lists the
 *   bucket, and a deduplicated chunk is always one the bucket holds. A chunk whose
 *   references and holds both reach zero becomes garbage, deleted after a 24 h grace; a
 *   stored garbage chunk revives when wanted again, one never stored is sent again, and
 *   one being deleted is never held.
 * - Quotas (owner policy, `FILE_DEFAULTS`): chunk bytes live and in history, inline
 *   bytes, entries, history rows and a file's size, each refused loudly at its bound,
 *   with nothing left behind. A change that only removes (a delete, a redaction, a purge,
 *   housekeeping) is never refused, so an owner at a bound can always make room. They
 *   read counters the schema's triggers keep, as do the bounds on open uploads.
 * - The sync lease, for the sandbox's sync agent (tasks 6): a random `lease_id` is the
 *   claim token and a rising `epoch` the fencing number, as the ledger's claims are. A
 *   sync commit is refused unless both are the live lease's; within it, each op has its
 *   own outcome and nothing one op does undoes another. API writes never take the
 *   lease. A sync write made against a version that has moved on, or blocked by what the
 *   API made of a parent, leaves the API's state in place and saves the sandbox's content
 *   beside it under a new conflict name, reported in the answer: nothing is dropped and
 *   nothing is silent.
 *
 * Every change runs in one `transactionSync`, so a refusal or a thrown error leaves
 * nothing behind; only chunk bytes and their deletes cross to `FILES`, between
 * transactions, at most two chunks a call, each call drawn first from the budget of the
 * invocation running it (`calls.ts`), read from the object's scope. Watchers in the object
 * (the pi environment's `watch`) hear each committed change.
 */
import { createHash, randomBytes } from "node:crypto";

import type { CallScope } from "./calls";
import { Invalid, Refused } from "./errors";
import { inTime } from "./schedule";
import {
  ancestors, isRoot, MAX_NAME_BYTES, MAX_PATH_BYTES, nameOf, parentOf, storedPath, TEMP, within,
  WORKSPACE,
} from "./paths";
import { codePoints, isPrintable, utf8Bytes } from "./text";

/** A chunk's most bytes: the files Worker's `CHUNK_BYTES` (a test ties the two). */
export const CHUNK_BYTES = 4 * 1024 * 1024;
/**
 * The most bytes one `FILES` call moves, the files Worker's `BATCH_BYTES`: two chunks, so
 * no call holds much of the isolate's memory.
 */
export const BATCH_BYTES = 2 * CHUNK_BYTES;
/**
 * The most bytes one whole-file write moves through the object (four chunks, two `FILES`
 * calls); larger files go by upload, a chunk at a time.
 */
export const WHOLE_BYTES = 4 * CHUNK_BYTES;
/** Files this size or smaller are kept inline in the object. */
export const INLINE_BYTES = 4096;
/** An upload intent's life: past it, its uncommitted chunks become garbage. */
export const INTENT_TTL_S = 3600;
/** How long a garbage chunk waits before it is deleted (the schema's trigger sets it). */
export const GC_GRACE_S = 86_400;
/** When a failed chunk delete is tried again. */
export const GC_RETRY_S = 300;
/** The sync lease's life without a renewal. */
export const LEASE_TTL_S = 60;
/** A temporary directory's life. */
export const TEMP_TTL_S = 86_400;
/**
 * How late housekeeping may run after it is due, so one alarm takes everything due in
 * an hour rather than each prune or delete arming its own.
 */
export const HOUSEKEEPING_SLACK_S = 3600;
/** R2's most keys in one delete, and so the most chunks one alarm run deletes. */
export const DELETE_BATCH = 1000;
/**
 * The least of a pass's budget a chunk delete is begun with: one that could not finish
 * would only mark its chunks `deleting` to be tried again `GC_RETRY_S` later.
 */
export const DELETE_FLOOR_MS = 1000;
/** Rows one alarm run prunes or expires per kind, and paths it re-marks for retention. */
export const HOUSEKEEPING_BATCH = 500;
export const MAX_LIST = 1000;
/** Chunks one intent may hold: a 40 GiB file, policy's highest `max_file_bytes`. */
export const MAX_INTENT_CHUNKS = 10_000;
/** Open upload intents, and the chunks they hold in all. */
export const MAX_OPEN_INTENTS = 64;
export const MAX_HELD_CHUNKS = 20_000;
/** Ops in one sync commit. */
export const MAX_SYNC_OPS = 1000;
/** The most versions policy can keep of a path (`FILE_SETTINGS.keep_versions`). */
export const KEEP_CEILING = 1000;
/** Conflict copies of one path one epoch may make. */
export const MAX_CONFLICT_COPIES = 100;

const HEX64 = /^[0-9a-f]{64}$/;
const HEX32 = /^[0-9a-f]{32}$/;

/** The owner's files settings the object enforces (owner policy's `files` table). */
export interface FileLimits {
  readonly max_bytes: number;
  readonly max_inline_bytes: number;
  readonly max_entries: number;
  readonly max_file_bytes: number;
  readonly max_history: number;
  readonly retention_days: number;
  readonly keep_versions: number;
  readonly keep_recent: number;
}

/** What the files Worker does for the object; the `FILES` binding. */
export interface ChunkStore {
  putChunks(ns: string, chunks: { sha256: string; bytes: Uint8Array }[]):
    Promise<{ created: number }>;
  getChunks(ns: string, reads: { sha256: string; offset?: number; length?: number }[]):
    Promise<Uint8Array[]>;
  deleteChunks(ns: string, sha256s: string[]): Promise<{ deleted: number }>;
}

export type FileKind = "file" | "dir";

/** A path as it stands. */
export interface Entry {
  readonly path: string;
  readonly kind: FileKind;
  readonly version: number;
  readonly etag: string;
  readonly size: number;
  /** The executable bit: 1 or 0. */
  readonly mode: number;
  readonly mtime_ms: number;
  readonly writer: string;
}

/** One chunk of a file: its hash and size. */
export interface ChunkRef {
  readonly sha256: string;
  readonly size: number;
}

/** A file's content, pinned: its bytes when inline, else its chunks in order. */
export interface Content {
  readonly entry: Entry;
  readonly content_id: string;
  readonly inline?: Uint8Array;
  readonly chunks?: ChunkRef[];
}

/** One history row, which is also one change in the feed. */
export type Change = {
  readonly version: number;
  readonly path: string;
  readonly op: string;
  /** What the path is after the change; null when it is gone. */
  readonly kind: FileKind | null;
  readonly content_id: string | null;
  readonly size: number;
  readonly mode: number;
  readonly writer: string;
  readonly at: number;
};

export interface Usage {
  readonly entries: number;
  readonly history: number;
  readonly chunk_bytes: number;
  readonly inline_bytes: number;
  readonly intents: number;
  readonly intent_chunks: number;
  readonly limits: FileLimits;
}

export interface Lease {
  readonly lease_id: string;
  readonly epoch: number;
  readonly expires_at: number;
  /** The feed's seq when the lease was taken. */
  readonly base: number;
}

/** A sync op: what the sandbox's scan found, against the version it last synced. */
export type SyncOp =
  | { readonly op: "write"; readonly path: string; readonly base: number | null;
      readonly mode?: number; readonly inline?: Uint8Array; readonly intent_id?: string }
  | { readonly op: "mkdir" | "remove"; readonly path: string; readonly base: number | null };

export type SyncOutcome =
  | { readonly path: string; readonly outcome: "committed"; readonly entry: Entry | null }
  | { readonly path: string; readonly outcome: "conflict"; readonly current: Entry | null;
      readonly copy: Entry | null }
  | { readonly path: string; readonly outcome: "refused"; readonly reason: string };

/** The expected etag is not the entry's; `current` is the entry as stored, or null. */
export class FileConflict extends Error {
  override readonly name = "FileConflict";
  constructor(readonly current: Entry | null) {
    super("conflict");
  }
}

export type FaultCode = "not_found" | "is_directory" | "not_directory" | "exists" | "not_empty";

/** What a POSIX call would fail with: the path is missing, or of the wrong kind. */
export class FileFault extends Error {
  override readonly name = "FileFault";
  constructor(readonly code: FaultCode, message: string) {
    super(`${code}: ${message}`);
  }
}

type StoredEntry = {
  parent: string;
  name: string;
  kind: FileKind;
  version: number;
  content_id: string | null;
  size: number;
  mode: number;
  mtime_ms: number;
  writer: string;
};

type Space = {
  ns: string | null;
  seq: number;
  feed_floor: number;
  entries: number;
  history: number;
  chunk_bytes: number;
  inline_bytes: number;
  intents: number;
  intent_chunks: number;
  lease_id: string | null;
  epoch: number;
  holder: string | null;
  lease_expires: number;
  retained_keep: number;
  retained_recent: number;
  retained_days: number;
  retain_cursor: string | null;
};

type Counters = Pick<Space, "entries" | "history" | "chunk_bytes" | "inline_bytes">;

/** Paths each change touched, for the object's watchers. */
type Touched = string[];

type Intent = { size: number; chunks: string[] };

export function sha256Hex(bytes: Uint8Array | string): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/** The content id of small content kept inline: its kind, size and bytes. */
export function inlineId(bytes: Uint8Array): string {
  return createHash("sha256").update(`ta-inline\n${bytes.byteLength}\n`).update(bytes)
    .digest("hex");
}

/**
 * The content id of chunked content: its kind, size and ordered chunks. A one-chunk file
 * is a list of one, so no kind of its own; the kind keeps it apart from a chunk's own
 * hash and from inline content.
 */
export function chunkedId(size: number, shas: readonly string[]): string {
  return sha256Hex(`ta-chunks\n${size}\n${shas.join("\n")}`);
}

function etag(e: { version: number; kind: string; content_id: string | null; mode: number }):
    string {
  return sha256Hex(`${e.version}:${e.kind}:${e.content_id ?? ""}:${e.mode}`);
}

function pathOf(e: StoredEntry): string {
  return `${e.parent}/${e.name}`;
}

function shown(e: StoredEntry): Entry {
  return { path: pathOf(e), kind: e.kind, version: e.version, etag: etag(e), size: e.size,
           mode: e.mode, mtime_ms: e.mtime_ms, writer: e.writer };
}

function rootEntry(path: string): Entry {
  return { path, kind: "dir", version: 0, etag: etag({ version: 0, kind: "dir",
           content_id: null, mode: 0 }), size: 0, mode: 0, mtime_ms: 0, writer: "system" };
}

function buffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as
    ArrayBuffer;
}

function expectedEtag(value: unknown): string | null {
  if (value !== null && typeof value !== "string") {
    throw new Invalid("expected_etag is null, empty or an etag");
  }
  return value;
}

/** The executable bit, or null to keep the path's (0 for a new file). */
function modeOf(value: unknown): number | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (value !== 0 && value !== 1) {
    throw new Invalid("mode is 0 or 1");
  }
  return value;
}

function count(value: unknown, what: string, low: number, high: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < low || value > high) {
    throw new Invalid(`${what} is a whole number from ${low} to ${high}`);
  }
  return value;
}

function hashes(packed: string): string[] {
  return packed.match(/.{64}/g) ?? [];
}

/** The chunk sizes a file of `size` bytes is cut into. */
export function chunkPlan(size: number): number[] {
  const sizes: number[] = [];
  for (let at = 0; at < size; at += CHUNK_BYTES) {
    sizes.push(Math.min(CHUNK_BYTES, size - at));
  }
  return sizes;
}

/** Bytes cut into 4 MiB chunks, each with its hash. */
export function cut(bytes: Uint8Array): { sha256: string; size: number; bytes: Uint8Array }[] {
  return chunkPlan(bytes.byteLength).map((size, index) => {
    const piece = bytes.subarray(index * CHUNK_BYTES, index * CHUNK_BYTES + size);
    return { sha256: sha256Hex(piece), size, bytes: piece };
  });
}

/** Groups of items whose `size` sums to at most `BATCH_BYTES` each. */
export function batches<T extends { size: number }>(items: readonly T[]): T[][] {
  const groups: T[][] = [];
  let bytes = BATCH_BYTES;
  for (const item of items) {
    if (bytes + item.size > BATCH_BYTES) {
      groups.push([]);
      bytes = 0;
    }
    groups.at(-1)!.push(item);
    bytes += item.size;
  }
  return groups;
}

/**
 * A conflict copy's name: `name` cut at a code point to leave room, then
 * `.ta-conflict-<epoch>` and, past the first, `-<n>`, at most 255 bytes in all.
 */
export function conflictName(name: string, epoch: number, n: number): string {
  const suffix = `.ta-conflict-${epoch}${n > 1 ? `-${n}` : ""}`;
  const points = [...name];
  while (utf8Bytes(points.join("")) + suffix.length > MAX_NAME_BYTES) {
    points.pop();
  }
  return points.join("") + suffix;
}

export class Files {
  readonly #storage: DurableObjectStorage;
  readonly #store: ChunkStore;
  readonly #limits: () => FileLimits;
  /** The object's invocations, whose current budget each call draws on (throws outside one). */
  readonly #scope: CallScope;
  readonly #watchers = new Set<(paths: string[]) => void>();

  constructor(storage: DurableObjectStorage, store: ChunkStore, limits: () => FileLimits,
              scope: CallScope) {
    this.#storage = storage;
    this.#store = store;
    this.#limits = limits;
    this.#scope = scope;
  }

  get #sql(): SqlStorage {
    return this.#storage.sql;
  }

  #space(): Space {
    return this.#sql.exec<Space>("SELECT * FROM file_space").one();
  }

  /** The owner's namespace, made on first use; it never leaves the object but to `FILES`. */
  ns(): string {
    return this.#storage.transactionSync(() => {
      const stored = this.#space().ns;
      if (stored !== null) {
        return stored;
      }
      const made = randomBytes(16).toString("hex");
      this.#sql.exec("UPDATE file_space SET ns = ?", made);
      return made;
    });
  }

  /** Hear every committed change's paths; returns the call that stops it. */
  watch(listener: (paths: string[]) => void): () => void {
    this.#watchers.add(listener);
    return () => this.#watchers.delete(listener);
  }

  #notify(paths: Touched): void {
    if (paths.length === 0) {
      return;
    }
    for (const listener of [...this.#watchers]) {
      try {
        listener([...paths]);
      } catch {
        console.error("a file watcher failed");  // a watcher never undoes a commit
      }
    }
  }

  /**
   * Run `act` in one transaction, then tell watchers. At the end, history is first fitted
   * to its quota from gone paths' rows (`#fit`); then the quotas are checked (`#bounds`),
   * which never refuses a change that leaves every counter no higher, so a redaction, a
   * purge, an abort or housekeeping always passes. `freeing` exempts the changes that free
   * while writing a record: a remove (its delete row adds to history) and a sync commit
   * (whose ops are bounded one by one, a remove's among them exempt).
   */
  #change<T>(act: (touched: Touched, now: number) => T, freeing = false): T {
    const touched: Touched = [];
    const result = this.#storage.transactionSync(() => {
      const before = this.#space();
      const value = act(touched, Date.now() / 1000);
      this.#fit(before);
      if (!freeing) {
        this.#bounds(before);
      }
      return value;
    });
    this.#notify(touched);
    return result;
  }

  /**
   * Refuse a change that took a counter past its bound. A change that leaves a counter
   * no higher than it found it passes, so an owner over a lowered quota can still delete.
   */
  #bounds(before: Counters): void {
    const after = this.#space();
    const limits = this.#limits();
    const checks: [keyof Counters, number, string][] = [
      ["chunk_bytes", limits.max_bytes, "the owner's file bytes are at their quota"],
      ["inline_bytes", limits.max_inline_bytes, "the owner's small-file bytes are at their quota"],
      ["entries", limits.max_entries, "the owner's file entries are at their quota"],
      ["history", limits.max_history, "the owner's file history is at its quota"],
    ];
    for (const [counter, bound, reason] of checks) {
      if (after[counter] > bound && after[counter] > before[counter]) {
        throw new Refused(reason);
      }
    }
  }

  /**
   * Retention run early, only on rows it has already let go: when a change took history
   * past `max_history` (or past where it found it, if a lowered policy left it higher),
   * prune that many of the earliest-due rows of gone paths (`released`), raising the
   * feed's floor as housekeeping does. A live path's rows are never taken early, and the
   * policy keeps every live path's rows within `max_history`, so once housekeeping has
   * applied the retention in force, and with the entries quota met, there are always enough
   * gone rows and the history quota never refuses a change.
   * The work is bounded by the change's own: it prunes at most the rows it recorded.
   */
  #fit(before: Counters): void {
    const over = this.#space().history - Math.max(this.#limits().max_history, before.history);
    if (over > 0) {
      this.#expire(this.#sql.exec<{ version: number; path: string }>(
        "SELECT version, path FROM file_history WHERE released = 1 " +
          "ORDER BY prune_after, version LIMIT ?", over).toArray());
    }
  }

  // -- READING ------------------------------------------------------------------

  #entry(path: string): StoredEntry | undefined {
    return this.#sql.exec<StoredEntry>(
      "SELECT * FROM file_entries WHERE parent = ? AND name = ?", parentOf(path), nameOf(path),
    ).toArray()[0];
  }

  /** A path as it stands, a root included, or null. */
  stat(path: unknown): Entry | null {
    const at = storedPath(path);
    if (isRoot(at)) {
      return rootEntry(at);
    }
    const found = this.#entry(at);
    return found === undefined ? null : shown(found);
  }

  #dir(path: string): Entry {
    const found = this.stat(path);
    if (found === null) {
      throw new FileFault("not_found", "no such directory");
    }
    if (found.kind !== "dir") {
      throw new FileFault("not_directory", "not a directory");
    }
    return found;
  }

  /** A directory's entries in name order, after the name `after`, and the next cursor. */
  list(dir: unknown, after: unknown, limit: unknown): { entries: Entry[]; next: string | null } {
    const at = storedPath(dir);
    this.#dir(at);
    if (after !== null && typeof after !== "string") {
      throw new Invalid("after is a name or null");
    }
    const most = count(limit, "limit", 1, MAX_LIST);
    const rows = this.#sql.exec<StoredEntry>(
      "SELECT * FROM file_entries WHERE parent = ? AND name > ? ORDER BY name LIMIT ?",
      at, after ?? "", most + 1,
    ).toArray();
    const page = rows.slice(0, most);
    return { entries: page.map(shown),
             next: rows.length > most ? page.at(-1)!.name : null };
  }

  /** Every entry below `dir`, any depth (the subtree's parents are a key range). */
  #below(dir: string): StoredEntry[] {
    return [
      ...this.#sql.exec<StoredEntry>("SELECT * FROM file_entries WHERE parent = ?", dir),
      ...this.#sql.exec<StoredEntry>(
        "SELECT * FROM file_entries WHERE parent >= ? AND parent < ?", `${dir}/`, `${dir}0`),
    ];
  }

  #content(contentId: string, size: number): { inline?: Uint8Array; chunks?: ChunkRef[] } {
    if (size <= INLINE_BYTES) {
      const row = this.#sql.exec<{ inline: ArrayBuffer }>(
        "SELECT inline FROM file_contents WHERE content_id = ?", contentId).one();
      return { inline: new Uint8Array(row.inline) };
    }
    const sizes = chunkPlan(size);
    const shas = this.#sql.exec<{ sha256: string }>(
      "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx", contentId,
    ).toArray();
    if (shas.length !== sizes.length) {
      throw new Error("a file's chunk list does not make up its size");
    }
    return { chunks: shas.map((row, index) => ({ sha256: row.sha256, size: sizes[index]! })) };
  }

  /** A file's current content, pinned: its bytes or its chunk list. */
  open(path: unknown): Content {
    const at = storedPath(path);
    const found = isRoot(at) ? undefined : this.#entry(at);
    if (isRoot(at) || found?.kind === "dir") {
      throw new FileFault("is_directory", "is a directory");
    }
    if (found === undefined) {
      throw new FileFault("not_found", "no such file");
    }
    return { entry: shown(found), content_id: found.content_id!,
             ...this.#content(found.content_id!, found.size) };
  }

  /** One kept version's content, pinned, as `open` gives the current one. */
  openVersion(path: unknown, version: unknown): Content & { change: Change } {
    const row = this.#version(storedPath(path), version);
    if (row.kind !== "file" || row.content_id === null) {
      throw new Invalid("that version holds no file content");
    }
    const entry: Entry = { path: row.path, kind: "file", version: row.version,
                           etag: etag({ ...row, kind: "file" }), size: row.size, mode: row.mode,
                           mtime_ms: Math.floor(row.at * 1000), writer: row.writer };
    return { entry, change: row, content_id: row.content_id,
             ...this.#content(row.content_id, row.size) };
  }

  #version(path: string, version: unknown): Change {
    const v = count(version, "version", 1, Number.MAX_SAFE_INTEGER);
    const row = this.#sql.exec<Change>(
      "SELECT version, path, op, kind, content_id, size, mode, writer, at FROM file_history " +
        "WHERE version = ? AND path = ?", v, path,
    ).toArray()[0];
    if (row === undefined) {
      throw new FileFault("not_found", "no such version");
    }
    return row;
  }

  /**
   * Chunk bytes in order, fetched from `FILES` at most two chunks a call, every call drawn
   * from the invocation's budget before the first is made.
   */
  async fetch(reads: readonly { sha256: string; offset?: number; length?: number }[]):
      Promise<Uint8Array[]> {
    if (reads.length === 0) {
      return [];
    }
    const ns = this.ns();
    const groups = batches(reads.map((read) => ({ read, size: read.length ?? CHUNK_BYTES })));
    this.#scope.current().draw("files", groups.length);
    const out: Uint8Array[] = [];
    for (const group of groups) {
      out.push(...await this.#store.getChunks(ns, group.map((item) => item.read)));
    }
    return out;
  }

  /** A path's kept history, newest first, before version `before`, and the next cursor. */
  history(path: unknown, before: unknown, limit: unknown):
      { changes: Change[]; next: number | null } {
    const at = storedPath(path);
    const most = count(limit, "limit", 1, MAX_LIST);
    const until = before === null ? Number.MAX_SAFE_INTEGER
      : count(before, "before", 1, Number.MAX_SAFE_INTEGER);
    const rows = this.#sql.exec<Change>(
      "SELECT version, path, op, kind, content_id, size, mode, writer, at FROM file_history " +
        "WHERE path = ? AND version < ? ORDER BY version DESC LIMIT ?", at, until, most + 1,
    ).toArray();
    const page = rows.slice(0, most);
    return { changes: page, next: rows.length > most ? page.at(-1)!.version : null };
  }

  /**
   * The workspace's changes after `after`, in order, and the next cursor. `reset` is true
   * when history the cursor still needed has been pruned: the reader rescans instead.
   */
  changes(after: unknown, limit: unknown): { changes: Change[]; cursor: number; reset: boolean } {
    const from = count(after, "after", 0, Number.MAX_SAFE_INTEGER);
    const most = count(limit, "limit", 1, MAX_LIST);
    const space = this.#space();
    const rows = this.#sql.exec<Change>(
      "SELECT version, path, op, kind, content_id, size, mode, writer, at FROM file_history " +
        "WHERE version > ? AND version <= ? ORDER BY version LIMIT ?", from, space.seq, most,
    ).toArray();
    return {
      changes: rows,
      cursor: rows.length === most ? rows.at(-1)!.version : space.seq,
      reset: from < space.feed_floor,
    };
  }

  usage(): Usage {
    const { entries, history, chunk_bytes, inline_bytes, intents, intent_chunks } = this.#space();
    return { entries, history, chunk_bytes, inline_bytes, intents, intent_chunks,
             limits: this.#limits() };
  }

  // -- WRITING ------------------------------------------------------------------

  /**
   * Stamp the next version and, for a workspace path, append its history row; return
   * the version. The temporary area keeps no history.
   */
  #record(path: string, op: string, kind: FileKind | null, contentId: string | null,
          size: number, mode: number, writer: string, now: number): number {
    const version = this.#sql.exec<{ seq: number }>(
      "UPDATE file_space SET seq = seq + 1 RETURNING seq").one().seq;
    if (within(path, TEMP)) {
      return version;
    }
    this.#sql.exec(
      "INSERT INTO file_history (version, path, op, kind, content_id, size, mode, writer, at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      version, path, op, kind, contentId, size, mode, writer, now,
    );
    this.#retain(path, kind !== null);
    return version;
  }

  /**
   * Apply retention to `path` after a change. Its newest `keep` versions (none once it is
   * gone) are kept; the next ones, up to `keep_recent` in all, are due `retention_days`
   * after they were written; any older one goes now. So a path holds at most
   * `max(keep_versions, keep_recent)` rows, and every live path's history fits in
   * `max_history` (policy.ts). On a change only the newest rows move between those
   * bands, so this looks no further than the most rows policy can keep; a policy change
   * re-marks every row through housekeeping (`#remark`). A gone path's rows are all
   * `released`, which a live path's never are: retention has let them go, so a change at
   * the history quota may prune them early (`#fit`); a path made again takes them back.
   */
  #retain(path: string, live: boolean): void {
    const limits = this.#limits();
    const keep = live ? limits.keep_versions : 0;
    const cap = Math.max(keep, limits.keep_recent);
    const released = live ? 0 : 1;
    const newest = "SELECT version FROM file_history WHERE path = ? ORDER BY version DESC " +
      "LIMIT ? OFFSET ?";
    if (keep > 0) {
      this.#sql.exec("UPDATE file_history SET prune_after = NULL, released = 0 WHERE " +
                       `(prune_after IS NOT NULL OR released = 1) AND version IN (${newest})`,
                     path, keep, 0);
    }
    if (cap > keep) {
      this.#sql.exec("UPDATE file_history SET prune_after = coalesce(prune_after, at + ?), " +
                       "released = ? WHERE (prune_after IS NULL OR released != ?) AND " +
                       `version IN (${newest})`, limits.retention_days * 86_400, released,
                     released, path, cap - keep, keep);
    }
    this.#sql.exec(`DELETE FROM file_history WHERE version IN (${newest})`, path, KEEP_CEILING,
                   cap);
  }

  /** Every row of `path` marked, or dropped, as the policy in force keeps it. */
  #remark(path: string, limits: FileLimits): void {
    const live = this.#entry(path) !== undefined;
    const keep = live ? limits.keep_versions : 0;
    const cap = Math.max(keep, limits.keep_recent);
    const days = limits.retention_days * 86_400;
    const newest = "SELECT version FROM file_history WHERE path = ? ORDER BY version DESC LIMIT ?";
    this.#sql.exec(`DELETE FROM file_history WHERE path = ? AND version NOT IN (${newest})`,
                   path, path, cap);
    this.#sql.exec(`UPDATE file_history SET prune_after = NULL WHERE path = ? ` +
                   `AND prune_after IS NOT NULL AND version IN (${newest})`, path, path, keep);
    this.#sql.exec(`UPDATE file_history SET prune_after = at + ? WHERE path = ? ` +
                   `AND version NOT IN (${newest}) AND (prune_after IS NULL ` +
                   `OR prune_after != at + ?)`, days, path, path, keep, days);
  }

  #upsert(path: string, kind: FileKind, version: number, contentId: string | null, size: number,
          mode: number, writer: string, now: number): StoredEntry {
    return this.#sql.exec<StoredEntry>(
      "INSERT INTO file_entries (parent, name, kind, version, content_id, size, mode, " +
        "mtime_ms, writer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (parent, name) DO " +
        "UPDATE SET kind = excluded.kind, version = excluded.version, " +
        "content_id = excluded.content_id, size = excluded.size, mode = excluded.mode, " +
        "mtime_ms = excluded.mtime_ms, writer = excluded.writer RETURNING *",
      parentOf(path), nameOf(path), kind, version, contentId, size, mode, Math.floor(now * 1000),
      writer,
    ).one();
  }

  /** The compare-and-swap: `expected` null passes, `""` needs no entry, else its etag. */
  #swap(current: StoredEntry | undefined, expected: string | null): void {
    if (expected !== null && (current === undefined ? "" : etag(current)) !== expected) {
      throw new FileConflict(current === undefined ? null : shown(current));
    }
  }

  /**
   * The temporary area holds only what `tempDir` made, so every temporary entry sits in
   * a directory that expires.
   */
  #temporary(path: string): void {
    if (within(path, TEMP) && this.#sql.exec("SELECT 1 FROM file_temps WHERE name = ?",
                                             path.split("/")[3]).toArray().length === 0) {
      throw new Invalid("the temporary area holds only temporary directories");
    }
  }

  /** Each missing ancestor made a directory (`parents`), or refused as missing. */
  #parents(path: string, parents: boolean, writer: string, now: number, touched: Touched): void {
    for (const dir of ancestors(path)) {
      const found = this.#entry(dir);
      if (found?.kind === "file") {
        throw new FileFault("not_directory", "a parent is not a directory");
      }
      if (found === undefined) {
        if (!parents) {
          throw new FileFault("not_found", "no such parent directory");
        }
        this.#upsert(dir, "dir", this.#record(dir, "mkdir", "dir", null, 0, 0, writer, now),
                     null, 0, 0, writer, now);
        touched.push(dir);
      }
    }
  }

  /** Commit content as `path`'s next version, inside the caller's transaction. */
  #commit(path: string, expected: string | null, contentId: string, size: number,
          wanted: number | null, writer: string, op: string, parents: boolean, now: number,
          touched: Touched): Entry {
    if (isRoot(path)) {
      throw new FileFault("is_directory", "a root is a directory");
    }
    const current = this.#entry(path);
    this.#swap(current, expected);
    if (current?.kind === "dir") {
      throw new FileFault("is_directory", "is a directory");
    }
    this.#temporary(path);
    if (size > this.#limits().max_file_bytes) {
      throw new Refused("the file is larger than the owner's file size quota");
    }
    this.#parents(path, parents, writer, now, touched);
    const mode = wanted ?? current?.mode ?? 0;
    const version = this.#record(path, op, "file", contentId, size, mode, writer, now);
    touched.push(path);
    return shown(this.#upsert(path, "file", version, contentId, size, mode, writer, now));
  }

  /**
   * Content already stored under `contentId` must be this content: the same size and
   * kind, and the same bytes or chunk list. Anything else fails loudly, never reused.
   * Returns whether it was there.
   */
  #known(contentId: string, size: number, inline: Uint8Array | null,
         shas: readonly string[] | null): boolean {
    const row = this.#sql.exec<{ size: number; inline: ArrayBuffer | null }>(
      "SELECT size, inline FROM file_contents WHERE content_id = ?", contentId).toArray()[0];
    if (row === undefined) {
      return false;
    }
    const same = row.size === size && (inline === null ? row.inline === null
      : row.inline !== null && sha256Hex(new Uint8Array(row.inline)) === sha256Hex(inline))
      && (shas === null || this.#sql.exec<{ sha256: string }>(
        "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx", contentId)
        .toArray().map((r) => r.sha256).join() === shas.join());
    if (!same) {
      throw new Error("a content id names other stored content");
    }
    return true;
  }

  /** Small content kept inline: its id, the row made when new. */
  #inline(bytes: Uint8Array): string {
    const contentId = inlineId(bytes);
    if (!this.#known(contentId, bytes.byteLength, bytes, null)) {
      this.#sql.exec("INSERT INTO file_contents (content_id, size, inline) VALUES (?, ?, ?)",
                     contentId, bytes.byteLength, buffer(bytes));
    }
    return contentId;
  }

  /** Write small content (at most 4 KiB) in one transaction. */
  #writeInline(path: string, bytes: Uint8Array, expected: string | null, mode: number | null,
               writer: string, parents: boolean): Entry {
    return this.#change((touched, now) => this.#commit(
      path, expected, this.#inline(bytes), bytes.byteLength, mode, writer, "write", parents,
      now, touched));
  }

  /**
   * Write bytes as `path`: inline, or chunked (intent, bytes to `FILES`, commit), the
   * object's own writes and the doors' small ones, at most `WHOLE_BYTES` at once.
   * `prefix` is whole chunks of content already stored that come first (an append or a
   * truncate keeps them), so only the chunks after them are cut from `bytes` and sent.
   * Every put is drawn from the invocation's budget before the first is sent.
   */
  async write(path: unknown, bytes: unknown, options: { expected: unknown; mode?: unknown;
              writer: string; parents?: boolean; prefix?: readonly ChunkRef[] }):
      Promise<Entry> {
    const at = storedPath(path);
    if (!(bytes instanceof Uint8Array)) {
      throw new Invalid("content is bytes");
    }
    if (bytes.byteLength > WHOLE_BYTES) {
      throw new Invalid(`a whole write moves at most ${WHOLE_BYTES} bytes; upload larger files`);
    }
    const expected = expectedEtag(options.expected);
    const mode = modeOf(options.mode);
    const parents = options.parents ?? true;
    const prefix = options.prefix ?? [];
    if (prefix.some((chunk) => chunk.size !== CHUNK_BYTES)) {
      throw new Invalid("a kept prefix is whole chunks");
    }
    const size = prefix.length * CHUNK_BYTES + bytes.byteLength;
    if (size <= INLINE_BYTES) {
      return this.#writeInline(at, bytes, expected, mode, options.writer, parents);
    }
    const tail = cut(bytes);
    const { intent_id, missing } = this.begin(size, [...prefix, ...tail]
      .map((chunk) => chunk.sha256));
    try {
      const wanted = new Set(missing);
      const puts = tail.filter((chunk) => wanted.delete(chunk.sha256));
      if (wanted.size > 0) {
        throw new Refused("a kept chunk of this file is gone; write it whole");
      }
      const groups = batches(puts);
      this.#scope.current().draw("files", groups.length);
      for (const group of groups) {
        await this.#put(group);
      }
      return this.commit(intent_id, at, { expected, mode, writer: options.writer, parents });
    } catch (error) {
      this.abort(intent_id);
      throw error;
    }
  }

  /** Put chunks through `FILES`, and record them stored once it accepted them. */
  async #put(chunks: readonly { sha256: string; bytes: Uint8Array }[]): Promise<void> {
    await this.#store.putChunks(this.ns(), chunks.map(({ sha256, bytes }) => ({ sha256, bytes })));
    this.#storage.transactionSync(() => {
      for (const { sha256 } of chunks) {
        this.#sql.exec("UPDATE file_chunks SET stored = 1 WHERE sha256 = ? AND stored = 0",
                       sha256);
      }
    });
  }

  /**
   * Record an upload of a file of `size` bytes made of `chunks`, cut at 4 MiB: each chunk
   * held, and the ones whose bytes must be sent. A stored chunk is not sent again; one
   * being deleted is refused, to begin again.
   */
  begin(size: unknown, chunks: unknown): { intent_id: string; missing: string[];
                                          expires_at: number } {
    const bytes = count(size, "size", INLINE_BYTES + 1, Number.MAX_SAFE_INTEGER);
    if (bytes > this.#limits().max_file_bytes) {
      throw new Refused("the file is larger than the owner's file size quota");
    }
    const plan = chunkPlan(bytes);
    if (plan.length > MAX_INTENT_CHUNKS) {
      throw new Invalid(`an upload holds at most ${MAX_INTENT_CHUNKS} chunks`);
    }
    if (!Array.isArray(chunks) || chunks.length !== plan.length
        || chunks.some((sha) => typeof sha !== "string" || !HEX64.test(sha))) {
      throw new Invalid("an upload names a 64-hex hash for each 4 MiB of the file");
    }
    const wanted = new Map<string, number>();
    (chunks as string[]).forEach((sha256, index) => {
      if ((wanted.get(sha256) ?? plan[index]) !== plan[index]) {
        throw new Invalid("a chunk hash names one size");
      }
      wanted.set(sha256, plan[index]!);
    });
    const intentId = randomBytes(16).toString("hex");
    return this.#change((_touched, now) => {
      const sql = this.#sql;
      const space = this.#space();
      if (space.intents >= MAX_OPEN_INTENTS || space.intent_chunks + plan.length > MAX_HELD_CHUNKS) {
        throw new Refused("too many uploads are open; commit or abort one first");
      }
      const missing: string[] = [];
      for (const [sha256, chunkSize] of wanted) {
        const row = sql.exec<{ state: string; stored: number; size: number }>(
          "SELECT state, stored, size FROM file_chunks WHERE sha256 = ?", sha256).toArray()[0];
        if (row !== undefined && row.size !== chunkSize) {
          throw new Invalid("a chunk hash names one size");
        }
        if (row === undefined) {
          sql.exec("INSERT INTO file_chunks (sha256, size, state, stored, holds) " +
                     "VALUES (?, ?, 'uploading', 0, 1)", sha256, chunkSize);
        } else if (row.state === "deleting") {
          throw new Refused("a chunk of this upload is being deleted; begin again");
        } else if (row.state === "garbage") {
          sql.exec("UPDATE file_chunks SET state = 'uploading', holds = 1, gc_after = NULL " +
                     "WHERE sha256 = ?", sha256);
        } else {
          sql.exec("UPDATE file_chunks SET holds = holds + 1 WHERE sha256 = ?", sha256);
        }
        if (row === undefined || !row.stored) {
          missing.push(sha256);
        }
      }
      const expiresAt = now + INTENT_TTL_S;
      sql.exec("INSERT INTO file_intents (intent_id, size, chunks, expires_at) " +
                 "VALUES (?, ?, ?, ?)", intentId, bytes, (chunks as string[]).join(""),
               expiresAt);
      return { intent_id: intentId, missing, expires_at: expiresAt };
    });
  }

  /** A live intent, or refused. */
  #intent(intentId: unknown, now: number): Intent {
    if (typeof intentId !== "string" || !HEX32.test(intentId)) {
      throw new Invalid("an intent id is 32 lowercase hex characters");
    }
    const row = this.#sql.exec<{ size: number; chunks: string; expires_at: number }>(
      "SELECT size, chunks, expires_at FROM file_intents WHERE intent_id = ?", intentId)
      .toArray()[0];
    if (row === undefined || row.expires_at <= now) {
      throw new Refused("no such upload: it was committed, abandoned or has expired");
    }
    return { size: row.size, chunks: hashes(row.chunks) };
  }

  /** End an intent: its holds go, and chunks nothing else holds become garbage. */
  #release(intentId: string): void {
    const sql = this.#sql;
    const row = sql.exec<{ chunks: string }>(
      "DELETE FROM file_intents WHERE intent_id = ? RETURNING chunks", intentId).toArray()[0];
    for (const sha256 of new Set(hashes(row?.chunks ?? ""))) {
      sql.exec("UPDATE file_chunks SET holds = holds - 1 WHERE sha256 = ?", sha256);
    }
  }

  /** Abandon an upload now rather than at its expiry; a gone one is no error. */
  abort(intentId: unknown): { aborted: boolean } {
    if (typeof intentId !== "string" || !HEX32.test(intentId)) {
      throw new Invalid("an intent id is 32 lowercase hex characters");
    }
    return this.#change(() => {
      const held = this.#sql.exec("SELECT 1 FROM file_intents WHERE intent_id = ?", intentId)
        .toArray().length > 0;
      this.#release(intentId);
      return { aborted: held };
    });
  }

  /**
   * Send bytes for chunks an intent holds, at most two chunks a call, each the size the
   * intent declared; each is stored once `FILES` accepted it. The put is drawn from the
   * invocation's budget first.
   */
  async upload(intentId: unknown, chunks: unknown): Promise<{ created: number }> {
    const held = new Set(this.#intent(intentId, Date.now() / 1000).chunks);
    if (!Array.isArray(chunks) || chunks.length === 0) {
      throw new Invalid("an upload sends a list of chunks");
    }
    const puts = (chunks as unknown[]).map((chunk) => {
      const { sha256, bytes } = (chunk ?? {}) as Record<string, unknown>;
      if (typeof sha256 !== "string" || !held.has(sha256)) {
        throw new Invalid("an upload sends only its intent's chunks");
      }
      const size = this.#sql.exec<{ size: number }>(
        "SELECT size FROM file_chunks WHERE sha256 = ?", sha256).one().size;
      if (!(bytes instanceof Uint8Array) || bytes.byteLength !== size) {
        throw new Invalid("a chunk's bytes are the size its upload declared");
      }
      return { sha256, bytes, size };
    });
    if (puts.reduce((sum, put) => sum + put.size, 0) > BATCH_BYTES) {
      throw new Invalid(`an upload sends at most ${BATCH_BYTES} bytes at once`);
    }
    this.#scope.current().draw("files", 1);
    await this.#put(puts);
    return { created: puts.length };
  }

  /**
   * The content an intent declared, inside the caller's transaction: every chunk stored,
   * made live, and the content row made or checked. A chunk `FILES` never accepted is
   * refused: no commit can claim bytes the bucket does not hold.
   */
  #fromIntent(intent: Intent): { contentId: string; size: number } {
    const sql = this.#sql;
    for (const sha256 of new Set(intent.chunks)) {
      const row = sql.exec<{ stored: number }>(
        "SELECT stored FROM file_chunks WHERE sha256 = ?", sha256).toArray()[0];
      if (row?.stored !== 1) {
        throw new Refused("a chunk of this commit was never stored; send its bytes first");
      }
      sql.exec("UPDATE file_chunks SET state = 'live' WHERE sha256 = ? AND state = 'uploading'",
               sha256);
    }
    const contentId = chunkedId(intent.size, intent.chunks);
    if (!this.#known(contentId, intent.size, null, intent.chunks)) {
      sql.exec("INSERT INTO file_contents (content_id, size) VALUES (?, ?)", contentId,
               intent.size);
      intent.chunks.forEach((sha256, index) => sql.exec(
        "INSERT INTO file_content_chunks (content_id, idx, sha256) VALUES (?, ?, ?)",
        contentId, index, sha256));
    }
    return { contentId, size: intent.size };
  }

  /** Commit an upload as `path`'s next version; the intent ends with it. */
  commit(intentId: unknown, path: unknown, request: { expected: unknown; mode?: unknown;
         writer: string; parents?: boolean }): Entry {
    const at = storedPath(path);
    const expected = expectedEtag(request.expected);
    const mode = modeOf(request.mode);
    return this.#change((touched, now) => {
      const { contentId, size } = this.#fromIntent(this.#intent(intentId, now));
      const entry = this.#commit(at, expected, contentId, size, mode, request.writer, "write",
                                 request.parents ?? true, now, touched);
      this.#release(intentId as string);
      return entry;
    });
  }

  /** Make a directory; an existing one is no error unless `expected` says otherwise. */
  mkdir(path: unknown, options: { expected: unknown; parents?: boolean; writer: string }):
      Entry {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    return this.#change((touched, now) => this.#mkdir(at, expected, options.parents ?? true,
                                                      options.writer, now, touched));
  }

  #mkdir(path: string, expected: string | null, parents: boolean, writer: string, now: number,
         touched: Touched): Entry {
    if (isRoot(path)) {
      if (expected !== null) {
        throw new Invalid("a root is never written");
      }
      return rootEntry(path);
    }
    const current = this.#entry(path);
    this.#swap(current, expected);
    if (current?.kind === "file") {
      throw new FileFault("exists", "a file is in the way");
    }
    if (current !== undefined) {
      return shown(current);
    }
    this.#temporary(path);
    this.#parents(path, parents, writer, now, touched);
    touched.push(path);
    return shown(this.#upsert(path, "dir", this.#record(path, "mkdir", "dir", null, 0, 0,
                                                        writer, now),
                              null, 0, 0, writer, now));
  }

  /**
   * Remove a file, or a directory and everything below it (`recursive`). Never refused
   * for a quota: it only frees.
   */
  remove(path: unknown, options: { expected: unknown; recursive?: boolean; writer: string }):
      { removed: number } {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    return this.#change((touched, now) => this.#remove(at, expected, options.recursive ?? false,
                                                       options.writer, now, touched), true);
  }

  #remove(path: string, expected: string | null, recursive: boolean, writer: string, now: number,
          touched: Touched): { removed: number } {
    if (isRoot(path)) {
      throw new Invalid("a root is never removed");
    }
    const current = this.#entry(path);
    if (current === undefined && expected === null) {
      throw new FileFault("not_found", "no such file or directory");
    }
    this.#swap(current, expected);
    if (current === undefined) {
      throw new FileFault("not_found", "no such file or directory");
    }
    const below = current.kind === "dir" ? this.#below(path) : [];
    if (current.kind === "dir" && !recursive) {
      throw new FileFault(below.length ? "not_empty" : "is_directory",
                          "a directory is removed with recursive");
    }
    for (const entry of [...below, current]) {
      this.#drop(pathOf(entry), "delete", writer, now, touched);
    }
    return { removed: below.length + 1 };
  }

  #drop(path: string, op: string, writer: string, now: number, touched: Touched): void {
    this.#sql.exec("DELETE FROM file_entries WHERE parent = ? AND name = ?", parentOf(path),
                   nameOf(path));
    this.#record(path, op, null, null, 0, 0, writer, now);
    touched.push(path);
  }

  /**
   * Move a file or a directory with everything below it, in one transaction; no byte
   * moves. `replace` lets a file replace a file, or a directory an empty directory, as
   * POSIX `rename` does; without it an existing destination is a conflict. A path renamed
   * onto itself changes nothing.
   */
  rename(from: unknown, to: unknown, options: { expected: unknown; replace: boolean;
         writer: string }): Entry {
    const source = storedPath(from);
    const target = storedPath(to);
    const expected = expectedEtag(options.expected);
    if (isRoot(source) || isRoot(target)) {
      throw new Invalid("a root is never renamed");
    }
    if (source !== target && within(target, source)) {
      throw new Invalid("a directory is never moved into itself");
    }
    return this.#change((touched, now) => {
      const moving = this.#entry(source);
      if (moving === undefined && expected === null) {
        throw new FileFault("not_found", "no such file or directory");
      }
      this.#swap(moving, expected);
      if (moving === undefined) {
        throw new FileFault("not_found", "no such file or directory");
      }
      if (source === target) {
        return shown(moving);
      }
      const parent = this.stat(parentOf(target));
      if (parent === null) {
        throw new FileFault("not_found", "no such destination directory");
      }
      if (parent.kind !== "dir") {
        throw new FileFault("not_directory", "the destination's parent is not a directory");
      }
      this.#temporary(target);
      const there = this.#entry(target);
      if (there !== undefined) {
        if (!options.replace) {
          throw new FileConflict(shown(there));
        }
        if (moving.kind === "file" && there.kind === "dir") {
          throw new FileFault("is_directory", "a file never replaces a directory");
        }
        if (moving.kind === "dir" && there.kind === "file") {
          throw new FileFault("not_directory", "a directory never replaces a file");
        }
        if (there.kind === "dir" && this.#below(target).length > 0) {
          throw new FileFault("not_empty", "the destination directory is not empty");
        }
      }
      // The feed reads each source's leaving before its arrival, but the source entries
      // go last: each content is held by its new entry before its old one lets go, so a
      // move never leaves content unreferenced, in any root.
      const tree = [moving, ...(moving.kind === "dir" ? this.#below(source) : [])];
      for (const entry of tree) {
        this.#record(pathOf(entry), "rename_from", null, null, 0, 0, options.writer, now);
      }
      let moved: StoredEntry | undefined;
      for (const entry of tree) {
        const path = target + pathOf(entry).slice(source.length);
        const version = this.#record(path, "rename_to", entry.kind, entry.content_id,
                                     entry.size, entry.mode, options.writer, now);
        const made = this.#upsert(path, entry.kind, version, entry.content_id, entry.size,
                                  entry.mode, options.writer, now);
        moved ??= made;
        touched.push(path);
      }
      for (const entry of tree) {
        this.#sql.exec("DELETE FROM file_entries WHERE parent = ? AND name = ?", entry.parent,
                       entry.name);
        touched.push(pathOf(entry));
      }
      return shown(moved!);
    });
  }

  /** Write a kept version's content as the path's next version; nothing rewinds. */
  restore(path: unknown, version: unknown, options: { expected: unknown; writer: string }):
      Entry {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    return this.#change((touched, now) => {
      const row = this.#version(at, version);
      if (row.kind !== "file" || row.content_id === null) {
        throw new Invalid("that version holds no file content");
      }
      return this.#commit(at, expected, row.content_id, row.size, row.mode, options.writer,
                          "restore", true, now, touched);
    });
  }

  /**
   * Drop one kept version's content (a secret written by mistake). The current version
   * is never redacted: write a new one first. Content is shared by every row that holds
   * the same bytes, so its bytes go only once no other kept row, the current file's
   * included, holds them; redact each such version to drop them all.
   */
  redact(path: unknown, version: unknown): { redacted: number } {
    const at = storedPath(path);
    return this.#change(() => {
      const row = this.#version(at, version);
      if (row.content_id === null) {
        throw new Invalid("that version holds no content");
      }
      if (this.#entry(at)?.version === row.version) {
        throw new Refused("the current version is never redacted; write a new one first");
      }
      this.#sql.exec("UPDATE file_history SET content_id = NULL WHERE version = ?", row.version);
      return { redacted: row.version };
    });
  }

  /**
   * Drop every kept version of `path` but its current one: the owner's way out of a full
   * history, never refused for a quota. A gone path's history goes whole, and the feed's
   * floor rises past it.
   */
  purge(path: unknown): { purged: number } {
    const at = storedPath(path);
    return this.#change(() => {
      const current = this.#entry(at)?.version ?? 0;
      const gone = this.#sql.exec<{ version: number }>(
        "DELETE FROM file_history WHERE path = ? AND version != ? RETURNING version", at, current,
      ).toArray().map((row) => row.version);
      if (current === 0 && gone.length > 0) {
        this.#sql.exec("UPDATE file_space SET feed_floor = MAX(feed_floor, ?)", Math.max(...gone));
      }
      return { purged: gone.length };
    });
  }

  /** A new temporary directory under `/.ta/tmp`, expired after a day. */
  tempDir(prefix: unknown, writer: string): string {
    if (typeof prefix !== "string" || prefix.includes("/") || codePoints(prefix) > 64
        || !isPrintable(prefix)) {
      throw new Invalid("a temporary prefix is at most 64 printable characters, no slash");
    }
    const name = `${prefix}${randomBytes(8).toString("hex")}`;
    const path = `${TEMP}/${name}`;
    this.#change((touched, now) => {
      this.#sql.exec("INSERT INTO file_temps (name, expires_at) VALUES (?, ?)", name,
                     now + TEMP_TTL_S);
      this.#mkdir(path, "", false, writer, now, touched);
    });
    return path;
  }

  /** Remove temporary directories now (an environment's `cleanup`). */
  dropTemps(paths: readonly string[], writer: string): void {
    this.#change((touched, now) => {
      for (const path of paths) {
        this.#dropTemp(nameOf(path), writer, now, touched);
      }
    });
  }

  #dropTemp(name: string, writer: string, now: number, touched: Touched): void {
    this.#sql.exec("DELETE FROM file_temps WHERE name = ?", name);
    if (this.#entry(`${TEMP}/${name}`) !== undefined) {
      this.#remove(`${TEMP}/${name}`, null, true, writer, now, touched);
    }
  }

  // -- THE SYNC LEASE -------------------------------------------------------------

  /** Take the sync lease, when no live one is held: a new lease id and the next epoch. */
  leaseAcquire(holder: unknown): Lease {
    if (typeof holder !== "string" || !holder || codePoints(holder) > 128
        || !isPrintable(holder)) {
      throw new Invalid("a lease holder is 1 to 128 printable characters");
    }
    return this.#change((_touched, now) => {
      const space = this.#space();
      if (space.lease_id !== null && space.lease_expires > now) {
        throw new Refused("the sync lease is held");
      }
      const leaseId = randomBytes(16).toString("hex");
      const row = this.#sql.exec<{ epoch: number; seq: number }>(
        "UPDATE file_space SET lease_id = ?, epoch = epoch + 1, holder = ?, lease_expires = ? " +
          "RETURNING epoch, seq", leaseId, holder, now + LEASE_TTL_S).one();
      return { lease_id: leaseId, epoch: row.epoch, expires_at: now + LEASE_TTL_S,
               base: row.seq };
    });
  }

  /** The live lease these name, or refused: a stale epoch or lease id is fenced. */
  #fenced(leaseId: unknown, epoch: unknown, now: number): Space {
    const space = this.#space();
    if (typeof leaseId !== "string" || leaseId !== space.lease_id || epoch !== space.epoch
        || space.lease_expires <= now) {
      throw new Refused("the sync lease was lost; this sync is fenced");
    }
    return space;
  }

  leaseRenew(leaseId: unknown, epoch: unknown): Lease {
    return this.#change((_touched, now) => {
      const space = this.#fenced(leaseId, epoch, now);
      this.#sql.exec("UPDATE file_space SET lease_expires = ?", now + LEASE_TTL_S);
      return { lease_id: space.lease_id!, epoch: space.epoch, expires_at: now + LEASE_TTL_S,
               base: space.seq };
    });
  }

  /** Give the lease back; one already lost is no error, and changes nothing. */
  leaseRelease(leaseId: unknown, epoch: unknown): { released: boolean } {
    return this.#change((_touched, now) => {
      try {
        this.#fenced(leaseId, epoch, now);
      } catch {
        return { released: false };
      }
      this.#sql.exec("UPDATE file_space SET lease_id = NULL, holder = NULL, lease_expires = 0");
      return { released: true };
    });
  }

  /**
   * The sandbox's sync-out, fenced by the lease, in one transaction with one outcome per
   * op, in order. Each op runs in its own savepoint with its own quota check, so one op
   * that cannot land is reported `refused` with its reason and undoes nothing else. A
   * write against a version that moved on, or under a parent the API made a file, is a
   * conflict: the API's state stays, and the sandbox's content is saved under a new
   * conflict name (`conflictName`). A directory is removed only once empty (the scan
   * removes its contents first), so a sync never deletes what the API wrote below it.
   */
  syncCommit(request: { lease_id: unknown; epoch: unknown; ops: unknown }):
      { outcomes: SyncOutcome[] } {
    const ops = request.ops;
    if (!Array.isArray(ops) || ops.length === 0 || ops.length > MAX_SYNC_OPS) {
      throw new Invalid(`a sync commit holds 1 to ${MAX_SYNC_OPS} ops`);
    }
    return this.#change((touched, now) => {
      const { epoch } = this.#fenced(request.lease_id, request.epoch, now);
      const writer = `lease:${epoch}`;
      const seen = new Set<string>();
      const outcomes = (ops as unknown[]).map((raw) => {
        const op = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
        const named = typeof op["path"] === "string" ? op["path"] : "";
        const mark = touched.length;
        try {
          return this.#storage.transactionSync(() => {
            const before = this.#space();
            const path = storedPath(op["path"]);
            if (!within(path, WORKSPACE) || isRoot(path) || seen.has(path)) {
              throw new Invalid("a sync op names one workspace path, once");
            }
            seen.add(path);
            const outcome = this.#syncOp(op, path, epoch, writer, now, touched);
            this.#fit(before);
            if (op["op"] !== "remove") {
              this.#bounds(before);
            }
            return outcome;
          });
        } catch (error) {
          touched.length = mark;
          if (error instanceof Invalid || error instanceof Refused || error instanceof FileFault
              || error instanceof FileConflict) {
            return { path: named, outcome: "refused" as const, reason: error.message };
          }
          throw error;
        }
      });
      return { outcomes };
    }, true);
  }

  /** Whether the API made a file of one of `path`'s ancestors. */
  #blocked(path: string): string | null {
    return ancestors(path).find((dir) => this.#entry(dir)?.kind === "file") ?? null;
  }

  /**
   * Where a conflicting write's content goes: the blocked ancestor, or else the path,
   * under the first conflict name nothing holds, so a copy never replaces anything. The
   * copy's name is a valid path and the rest is the end of one, so only the whole path's
   * length can fail: one that would pass 4096 bytes is refused, as that op's outcome. The sandbox keeps such a file and the sync agent
   * reports it on every sync until it is renamed or the conflict resolved (tasks 6).
   */
  #copyPath(path: string, epoch: number): string {
    const at = this.#blocked(path) ?? path;
    const rest = path.slice(at.length);
    for (let n = 1; n <= MAX_CONFLICT_COPIES; n++) {
      const copy = storedPath(`${parentOf(at)}/${conflictName(nameOf(at), epoch, n)}`);
      if (this.#entry(copy) === undefined) {
        if (utf8Bytes(copy + rest) > MAX_PATH_BYTES) {
          throw new Refused("the conflict copy's path would pass 4096 bytes; " +
                            "the sandbox keeps this file and reports it each sync");
        }
        return copy + rest;
      }
    }
    throw new Refused("too many conflict copies of this path in this epoch");
  }

  #syncOp(op: Record<string, unknown>, path: string, epoch: number, writer: string, now: number,
          touched: Touched): SyncOutcome {
    const base = op["base"] === null ? null : count(op["base"], "base", 1, Number.MAX_SAFE_INTEGER);
    const current = this.#entry(path);
    const moved = base === null ? current !== undefined : current?.version !== base;
    const conflict = (copy: Entry | null): SyncOutcome =>
      ({ path, outcome: "conflict", current: current === undefined ? null : shown(current),
         copy });
    switch (op["op"]) {
      case "write": {
        const mode = modeOf(op["mode"]);
        let contentId: string;
        let size: number;
        if (op["inline"] !== undefined) {
          const bytes = op["inline"];
          if (!(bytes instanceof Uint8Array) || bytes.byteLength > INLINE_BYTES
              || op["intent_id"] !== undefined) {
            throw new Invalid("an inline sync write is at most 4096 bytes");
          }
          contentId = this.#inline(bytes);
          size = bytes.byteLength;
        } else {
          ({ contentId, size } = this.#fromIntent(this.#intent(op["intent_id"], now)));
          this.#release(op["intent_id"] as string);
        }
        if (moved || current?.kind === "dir" || this.#blocked(path) !== null) {
          return conflict(this.#commit(this.#copyPath(path, epoch), "", contentId, size, mode,
                                       writer, "conflict", true, now, touched));
        }
        return { path, outcome: "committed",
                 entry: this.#commit(path, null, contentId, size, mode, writer, "write", true, now,
                                     touched) };
      }
      case "mkdir":
        if ((moved && current?.kind !== "dir") || this.#blocked(path) !== null) {
          return conflict(null);
        }
        return { path, outcome: "committed",
                 entry: this.#mkdir(path, null, true, writer, now, touched) };
      case "remove":
        if (moved || (current?.kind === "dir" && this.#below(path).length > 0)) {
          return conflict(null);
        }
        if (current !== undefined) {
          this.#remove(path, null, true, writer, now, touched);
        }
        return { path, outcome: "committed", entry: null };
      default:
        throw new Invalid("a sync op is a write, mkdir or remove");
    }
  }

  // -- HOUSEKEEPING, ON THE OWNER'S ONE SCHEDULE ---------------------------------------

  /** Whether the rows were marked under a retention other than the one in force. */
  #retentionChanged(space: Space, limits: FileLimits): boolean {
    return space.retained_keep !== limits.keep_versions
      || space.retained_recent !== limits.keep_recent
      || space.retained_days !== limits.retention_days;
  }

  /** Whether a retention change is still to be applied to the rows already kept. */
  #reretaining(space: Space, limits: FileLimits): boolean {
    return space.retain_cursor !== null || this.#retentionChanged(space, limits);
  }

  /**
   * When housekeeping is next due, never before `now`, or null: expired intents and
   * temporary directories, due history prunes, and due chunk deletes, each with
   * `HOUSEKEEPING_SLACK_S`, so one run takes an hour's work; and at once, a retention
   * change still to apply.
   */
  nextAlarm(now: number): number | null {
    if (this.#reretaining(this.#space(), this.#limits())) {
      return now;
    }
    const times = [
      "SELECT MIN(expires_at) AS due FROM file_intents",
      "SELECT MIN(expires_at) AS due FROM file_temps",
      "SELECT MIN(prune_after) AS due FROM file_history WHERE prune_after IS NOT NULL",
      "SELECT MIN(gc_after) AS due FROM file_chunks WHERE gc_after IS NOT NULL",
    ].map((query) => this.#sql.exec<{ due: number | null }>(query).one().due)
      .filter((due): due is number => due !== null);
    return times.length ? Math.max(Math.min(...times) + HOUSEKEEPING_SLACK_S, now) : null;
  }

  /**
   * One housekeeping pass, the system's own and never refused for a quota: apply a
   * retention change to kept rows, expire intents and temporary directories, prune due
   * history, then delete due garbage chunks. A delete marks its chunks `deleting` (never
   * held again) before it asks `FILES`, and removes their rows only after; one that fails
   * stays in those rows, retried after `GC_RETRY_S`, and never fails the pass (the `files`
   * lane's contract: an outside call's failure is its rows', a throw is a bug). No delete
   * is begun with less than `DELETE_FLOOR_MS` left before `until`, and the one delete is
   * drawn from the alarm's budget first.
   */
  async collect(now: number, until: number = Infinity): Promise<void> {
    const sql = this.#sql;
    this.#change((touched) => {
      this.#reretain();
      for (const { intent_id: intentId } of sql.exec<{ intent_id: string }>(
             "SELECT intent_id FROM file_intents WHERE expires_at <= ? ORDER BY expires_at " +
               "LIMIT ?", now, HOUSEKEEPING_BATCH).toArray()) {
        this.#release(intentId);
      }
      for (const { name } of sql.exec<{ name: string }>(
             "SELECT name FROM file_temps WHERE expires_at <= ? ORDER BY expires_at LIMIT ?",
             now, HOUSEKEEPING_BATCH).toArray()) {
        this.#dropTemp(name, "system", now, touched);
      }
      this.#prune(now);
    });
    const due = sql.exec<{ sha256: string }>(
      "SELECT sha256 FROM file_chunks WHERE gc_after IS NOT NULL AND gc_after <= ? " +
        "ORDER BY gc_after LIMIT ?", now, DELETE_BATCH).toArray().map((row) => row.sha256);
    if (due.length === 0 || until - Date.now() < DELETE_FLOOR_MS) {
      return;  // too little of this pass left: the chunks stay due for a later one
    }
    this.#scope.current().draw("files", 1);
    this.#storage.transactionSync(() => {
      for (const sha256 of due) {
        sql.exec("UPDATE file_chunks SET state = 'deleting', gc_after = ? WHERE sha256 = ?",
                 now + GC_RETRY_S, sha256);
      }
    });
    try {
      // Bounded by the pass's budget: a late delete is tried again, as a failed one is.
      if (await inTime(this.#store.deleteChunks(this.ns(), due), until - Date.now())
          === "late") {
        throw new Error("the chunk delete ran past its pass");
      }
    } catch {
      console.error("chunk delete failed; it is tried again later");  // no hashes in the log
      return;
    }
    this.#storage.transactionSync(() => {
      for (const sha256 of due) {
        sql.exec("DELETE FROM file_chunks WHERE sha256 = ? AND state = 'deleting'", sha256);
      }
    });
  }

  /**
   * Apply the retention policy in force to rows already kept, a batch of paths a pass,
   * from where the last pass stopped. A policy change during a pass starts it over.
   */
  #reretain(): void {
    const limits = this.#limits();
    const space = this.#space();
    if (!this.#reretaining(space, limits)) {
      return;
    }
    let cursor = space.retain_cursor ?? "";
    if (this.#retentionChanged(space, limits)) {
      cursor = "";
    }
    const paths = this.#sql.exec<{ path: string }>(
      "SELECT DISTINCT path FROM file_history WHERE path > ? ORDER BY path LIMIT ?", cursor,
      HOUSEKEEPING_BATCH).toArray().map((row) => row.path);
    for (const path of paths) {
      this.#remark(path, limits);
    }
    this.#sql.exec("UPDATE file_space SET retained_keep = ?, retained_recent = ?, " +
                     "retained_days = ?, retain_cursor = ?",
                   limits.keep_versions, limits.keep_recent, limits.retention_days,
                   paths.length === HOUSEKEEPING_BATCH ? paths.at(-1)! : null);
  }

  /**
   * Delete due history rows. A pruned row that was its path's newest (a deleted path's
   * last record) raises the feed's floor, so a reader behind it rescans instead of
   * missing the delete.
   */
  #prune(now: number): void {
    this.#expire(this.#sql.exec<{ version: number; path: string }>(
      "SELECT version, path FROM file_history WHERE prune_after IS NOT NULL AND " +
        "prune_after <= ? ORDER BY prune_after LIMIT ?", now, HOUSEKEEPING_BATCH).toArray());
  }

  /** Delete these history rows, the feed's floor raised past any that was its path's newest. */
  #expire(rows: readonly { version: number; path: string }[]): void {
    const sql = this.#sql;
    let floor = 0;
    for (const { version, path } of rows) {
      sql.exec("DELETE FROM file_history WHERE version = ?", version);
      const newer = sql.exec("SELECT 1 FROM file_history WHERE path = ? AND version > ? LIMIT 1",
                             path, version).toArray().length > 0;
      if (!newer) {
        floor = Math.max(floor, version);
      }
    }
    if (floor > 0) {
      sql.exec("UPDATE file_space SET feed_floor = MAX(feed_floor, ?)", floor);
    }
  }
}
