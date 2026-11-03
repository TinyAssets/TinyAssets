/**
 * The runner's sync agent (`engines-replan.md` §2.1 steps 4 and 7), run as its own
 * unprivileged user (`ta-sync`, in group `ta`), never root (slice S review H-2): the owner's
 * files into the sandbox's `/workspace` before a run, and the run's changes back out after
 * it, through the sync doors on the egress host by the lease's sync token (`x-ta-sync`,
 * beside the egress token E2B's transform sets, so the token is good from inside the VM
 * alone), the object fencing each commit by the lease's id and epoch.
 *
 * What it holds at once is bounded whatever the tree holds, in bytes and in paths: the walk
 * reads one directory entry at a time (`walkFiles`), the mirror of what was synced is a
 * SQLite database on disk read a row or a page at a time (`openMirror`), a file's bytes move
 * a chunk at a time in both directions, and a commit holds one batch of ops (at most
 * `COMMIT_OPS`, small files at most `INLINE_BYTES` each).
 *
 * - **In:** the change feed from the mirror's cursor, each path's last change applied with
 *   no link followed (`fsafe.ts`), a file's chunks streamed into a temporary file and renamed
 *   into place (`safeStream`); a reset reads the whole tree again by listing, page by page.
 *   The engines' sessions (`/workspace/.sessions/<authority>/<engine>/...`) are never written
 *   into the shared workspace, where every engine's user could reach them (review H-3): only
 *   a path under that engine's own `session_paths` is taken, into the sync agent's own
 *   directory, for the root runner to put in that engine's store.
 * - **Out:** each engine's sessions the runner staged first, in commits of their own; then
 *   every regular file under `/workspace` whose bytes differ from what was last synced
 *   (links never; the `.sessions` tree never, from the workspace; nothing the sync-ignore
 *   names, `SYNC_IGNORE`); a file of up to 4 KiB inline, a larger one through the files
 *   plane's upload doors a chunk at a time, read as it is sent. Each file's identity (its size
 *   and modification time) is taken when it is offered, so a change made while the sync runs
 *   is offered again next time, and a file gone meanwhile is skipped and noted. The mirror
 *   is written as each batch commits, so nothing a refusal or a crash cuts short loses what
 *   was already sent. A refusal of one file (the owner's storage full among them,
 *   `storage_full`, or an upload the object will not begin) is that file's alone, kept in the
 *   sandbox and offered again by the next sync; an upload given up is aborted at once. A
 *   client's sandbox never offers a path in the owner's own trees. A file holding a secret's
 *   bytes (`guards.ts`) is refused here and reported, never sent. The agents' scratch
 *   (`/scratch`, `scratch.ts`) is outside `/workspace`, so never synced.
 */
import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, rmSync, type Stats } from "node:fs";
import { join, relative } from "node:path";
import { DatabaseSync } from "node:sqlite";

import type { SourceRecord } from "../../doors/src/sources";

import { isKept, keptPaths } from "./engine";
import { remove, safeDirs, safePieces, safeRead, safeStream, safeWrite, walkFiles } from "./fsafe";
import { heldSecret } from "./guards";
import { defaultSyncIgnore, ignoredBy, SYNC_IGNORE } from "../../owner/src/sync-ignore";

export { DEFAULT_IGNORES, defaultSyncIgnore, ignoredBy, SYNC_IGNORE, wildcard }
  from "../../owner/src/sync-ignore";

/**
 * The object's sync bounds: ops a commit (`MAX_SYNC_OPS`), a chunk (`CHUNK_BYTES`), and an
 * inline write (`INLINE_BYTES`): a file past it goes through the files plane's upload. The
 * runner's bundle takes nothing of the object's storage modules, so `guards/config.test.ts`
 * holds each equal to the object's.
 */
export const COMMIT_OPS = 1000;
export const CHUNK_BYTES = 4 * 1024 * 1024;
export const INLINE_BYTES = 4096;
/** A sync door's body, the doors' own most (`SYNC_BODY_MAX`). */
export const SYNC_BODY = 12 * 1024 * 1024;
/**
 * A commit's ops, each counted at its size: the door's body less the commit's own fields
 * (its lease id, epoch and the JSON around them, under 128 bytes).
 */
const BODY_MAX = SYNC_BODY - 128;
/** The feed's page, at the object's own most (`MAX_LIST`). */
export const FEED_LIMIT = 1000;
/** The owner's trees a client's sandbox never writes (`owner/src/paths.ts`). */
export const OWNER_TREES = Object.freeze(["/workspace/agents", "/workspace/.agents",
                                          "/workspace/skills", "/workspace/prompts"]);
export const SESSIONS = "/workspace/.sessions";
/** The mirror's rows a page of a removal scan or a reset reads at once. */
const PAGE = 1000;

export class SyncFenced extends Error {
  override readonly name = "SyncFenced";
}

/** A sync door's refusal of this call (not a fence): its fixed code and the object's words. */
export class SyncRefused extends Error {
  override readonly name = "SyncRefused";
  constructor(readonly code: string, message: string, readonly status: number) {
    super(message);
  }
}

/**
 * The sync doors' client: a call refused for the moment (the network, `429`, `503`) is
 * asked again with no count, its wait doubling from `firstMs` (the runner's flush cadence:
 * no sooner than its next batch of frames would go) to `ceilingMs` (a heartbeat: the run's
 * report is never held longer than one). A lost lease (`401`, or the code `sync_fenced`) is
 * `SyncFenced`; any other refusal is `SyncRefused`, with the object's whole message, for the
 * caller to report as that call's own (a full storage's used and total among it).
 */
export class SyncClient {
  constructor(readonly origin: string, readonly token: string, readonly firstMs: number,
              readonly ceilingMs: number, readonly fetcher: typeof fetch = fetch) {}

  async post(door: string, body: unknown): Promise<Record<string, unknown>> {
    for (let wait = this.firstMs; ; wait = Math.min(wait * 2, this.ceilingMs)) {
      let response: Response;
      try {
        response = await this.fetcher(`${this.origin}/sync/${door}`, {
          method: "POST", body: JSON.stringify(body),
          headers: { "content-type": "application/json", "x-ta-sync": this.token } });
      } catch {
        await new Promise((resolve) => setTimeout(resolve, wait));
        continue;
      }
      const text = await response.text();
      if (response.status === 200) {
        return JSON.parse(text) as Record<string, unknown>;
      }
      if (response.status === 404 && door === "read") {
        return { missing: true };
      }
      if (response.status === 503 || response.status === 429) {
        await new Promise((resolve) => setTimeout(resolve, wait));
        continue;
      }
      let error: { code?: unknown; message?: unknown } = {};
      try {
        error = ((JSON.parse(text) as { error?: typeof error }).error) ?? {};
      } catch {
        error = {};
      }
      const code = typeof error.code === "string" ? error.code : `http_${response.status}`;
      const message = typeof error.message === "string" ? error.message : text;
      if (response.status === 401 || code === "sync_fenced") {
        throw new SyncFenced(`the sync door refused (${response.status}): ${message}`);
      }
      throw new SyncRefused(code, message, response.status);
    }
  }
}

/** What the sandbox last synced of one path: its version, and its bytes' identity. */
export interface Known {
  readonly version: number;
  readonly size: number;
  readonly mtime_ms: number;
  readonly sha256: string;
}

/**
 * What the sandbox last synced: the feed's cursor, and each path's version and identity,
 * read and written a row at a time.
 */
export interface Mirror {
  cursor(): number;
  setCursor(cursor: number): void;
  get(path: string): Known | undefined;
  put(path: string, known: Known): void;
  drop(path: string): void;
  /** `path` and everything under it dropped. */
  dropUnder(path: string): void;
  /** Whether anything at or under `path` is known. */
  knowsUnder(path: string): boolean;
  /** Up to `limit` known paths after `after`, in order. */
  page(after: string, limit: number): string[];
  /** `work` in one transaction (a batch's outcomes written together). */
  batch(work: () => void): void;
  /** A reset's seen set: begun, marked, then what was not seen, page by page. */
  resetBegin(): void;
  seen(path: string): void;
  unseen(after: string, limit: number): string[];
  resetEnd(): void;
  close(): void;
}

/** A mirror in memory, for tests and tools: its rows on `files`. */
export function memoryMirror(init: { cursor?: number; files?: Record<string, Known> } = {}):
    Mirror & { readonly files: Record<string, Known> } {
  let cursor = init.cursor ?? 0;
  const files: Record<string, Known> = init.files ?? {};
  let seenSet: Set<string> | null = null;
  const sorted = () => Object.keys(files).sort();
  return {
    files,
    cursor: () => cursor,
    setCursor: (next) => { cursor = next; },
    get: (path) => files[path],
    put: (path, known) => { files[path] = known; },
    drop: (path) => { delete files[path]; },
    dropUnder: (path) => {
      for (const known of Object.keys(files)) {
        if (known === path || known.startsWith(`${path}/`)) {
          delete files[known];
        }
      }
    },
    knowsUnder: (path) => Object.keys(files).some((known) =>
      known === path || known.startsWith(`${path}/`)),
    page: (after, limit) => sorted().filter((path) => path > after).slice(0, limit),
    batch: (work) => work(),
    resetBegin: () => { seenSet = new Set(); },
    seen: (path) => { seenSet!.add(path); },
    unseen: (after, limit) => sorted().filter((path) => path > after && !seenSet!.has(path))
      .slice(0, limit),
    resetEnd: () => { seenSet = null; },
    close: () => undefined,
  };
}

/**
 * The sync agent's mirror on disk (`path`, a SQLite database), so its size never sits in
 * memory: a legacy `mirror.json` (`legacy`) is taken in once, then removed.
 */
export function openMirror(path: string, legacy?: string): Mirror {
  const db = new DatabaseSync(path);
  db.exec("PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; " +
          "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, version INTEGER NOT NULL, " +
          "size INTEGER NOT NULL, mtime_ms REAL NOT NULL, sha256 TEXT NOT NULL) WITHOUT ROWID; " +
          "CREATE TABLE IF NOT EXISTS meta (only INTEGER PRIMARY KEY CHECK (only = 1), " +
          "cursor INTEGER NOT NULL); INSERT OR IGNORE INTO meta VALUES (1, 0);");
  const get = db.prepare("SELECT version, size, mtime_ms, sha256 FROM files WHERE path = ?");
  const put = db.prepare("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)");
  const drop = db.prepare("DELETE FROM files WHERE path = ?");
  const under = db.prepare("DELETE FROM files WHERE path = ? OR (path > ? AND path < ?)");
  const knows = db.prepare("SELECT 1 FROM files WHERE path = ? OR (path > ? AND path < ?) " +
                           "LIMIT 1");
  const page = db.prepare("SELECT path FROM files WHERE path > ? ORDER BY path LIMIT ?");
  const mirror: Mirror = {
    cursor: () => (db.prepare("SELECT cursor FROM meta").get() as { cursor: number }).cursor,
    setCursor: (cursor) => { db.prepare("UPDATE meta SET cursor = ?").run(cursor); },
    get: (at) => {
      const row = get.get(at) as Known | undefined;
      return row === undefined ? undefined : { ...row };
    },
    put: (at, known) => {
      put.run(at, known.version, known.size, known.mtime_ms, known.sha256);
    },
    drop: (at) => { drop.run(at); },
    // Below `p/` is every path from `p/` up to `p0` (`/` is the code point before `0`).
    dropUnder: (at) => { under.run(at, `${at}/`, `${at}0`); },
    knowsUnder: (at) => knows.get(at, `${at}/`, `${at}0`) !== undefined,
    page: (after, limit) => (page.all(after, limit) as { path: string }[])
      .map((row) => row.path),
    batch: (work) => {
      db.exec("BEGIN");
      try {
        work();
        db.exec("COMMIT");
      } catch (error) {
        db.exec("ROLLBACK");
        throw error;
      }
    },
    resetBegin: () => {
      db.exec("DROP TABLE IF EXISTS seen; CREATE TEMP TABLE seen (path TEXT PRIMARY KEY) " +
              "WITHOUT ROWID");
    },
    seen: (at) => { db.prepare("INSERT OR IGNORE INTO seen VALUES (?)").run(at); },
    unseen: (after, limit) => (db.prepare(
      "SELECT path FROM files WHERE path > ? AND path NOT IN (SELECT path FROM seen) " +
        "ORDER BY path LIMIT ?").all(after, limit) as { path: string }[]).map((row) => row.path),
    resetEnd: () => { db.exec("DROP TABLE IF EXISTS seen"); },
    close: () => { db.close(); },
  };
  if (legacy !== undefined && existsSync(legacy)) {
    const old = JSON.parse(readFileSync(legacy, "utf8")) as
      { cursor: number; files: Record<string, Known> };
    mirror.batch(() => {
      mirror.setCursor(old.cursor);
      for (const [at, known] of Object.entries(old.files)) {
        mirror.put(at, known);
      }
    });
    rmSync(legacy, { force: true });
  }
  return mirror;
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Where the sandbox keeps the owner's files, and the sync agent's own directories. */
export interface Place {
  /** The local directory standing for `/workspace`. */
  readonly local: string;
  /** The sync agent's own directory: sessions in from the feed, and out from the store. */
  readonly sessionsIn: string;
  readonly sessionsOut: string;
  /** Whose sandbox: `owner`, or a client's id (the owner's trees read-only, never written). */
  readonly authority: string;
  /** The records, for each engine's own `session_paths`. */
  readonly records: ReadonlyMap<string, SourceRecord>;
}

function localOf(place: Pick<Place, "local">, path: string): string {
  return join(place.local, relative("/workspace", path));
}

function inOwnerTrees(path: string): boolean {
  return OWNER_TREES.some((tree) => path === tree || path.startsWith(`${tree}/`));
}

/**
 * A session path's engine and its path in that engine's home, when it is under one of the
 * engine's own `session_paths`; null for anything else (another authority's, a path no
 * session path names: never restored).
 */
export function sessionOf(place: Place, path: string):
    { engine: string; rel: string } | null {
  const prefix = `${SESSIONS}/${place.authority}/`;
  if (!path.startsWith(prefix)) {
    return null;
  }
  const [engine, ...rest] = path.slice(prefix.length).split("/");
  const record = engine === undefined ? undefined : place.records.get(engine);
  const rel = rest.join("/");
  if (record?.cli === undefined || rel === "" || rel.split("/").includes("..")) {
    return null;
  }
  return isKept(keptPaths(record).sessions, rel) ? { engine: engine!, rel } : null;
}

/**
 * A file's bytes from the owner's files into `local` (under `base`), streamed: inline, or two
 * chunks a call each appended to a temporary file as it comes and renamed into place once
 * whole, its hash taken as it streams. Null when the path is gone or is no file.
 */
async function fetchInto(client: SyncClient, path: string, base: string, local: string,
                         modeOf: (mode: number) => number, dirMode: number):
    Promise<{ version: number; size: number; sha256: string } | null> {
  const read = await client.post("read", { path });
  if (read["missing"] === true) {
    return null;
  }
  const entry = read["entry"] as { version: number; mode: number; kind: string };
  if (entry.kind !== "file") {
    return null;
  }
  const out = safeStream(base, local, modeOf(entry.mode), undefined, dirMode);
  const hash = createHash("sha256");
  let size = 0;
  const take = (piece: Uint8Array) => {
    out.append(piece);
    hash.update(piece);
    size += piece.byteLength;
  };
  try {
    if (typeof read["inline"] === "string") {
      take(new Uint8Array(Buffer.from(read["inline"], "base64")));
    } else {
      const chunks = (read["chunks"] ?? []) as { sha256: string; size: number }[];
      for (let first = 0; first < chunks.length; first += 2) {
        const got = await client.post("chunks", { path, version: entry.version, first,
                                                   count: Math.min(2, chunks.length - first) });
        for (const part of got["chunks"] as string[]) {
          take(new Uint8Array(Buffer.from(part, "base64")));
        }
      }
    }
    out.commit();
  } catch (error) {
    out.abort();
    throw error;
  }
  return { version: entry.version, size, sha256: hash.digest("hex") };
}

/** One path's state after the feed: gone, a directory, or a file at a version. */
async function apply(client: SyncClient, place: Place, mirror: Mirror, path: string,
                     kind: string | null): Promise<void> {
  if (path === SESSIONS || path.startsWith(`${SESSIONS}/`)) {
    const session = sessionOf(place, path);
    if (session === null || kind !== "file") {
      return;
    }
    const got = await fetchInto(client, path, place.sessionsIn,
                                join(place.sessionsIn, session.engine, session.rel),
                                () => 0o600, 0o700);
    if (got !== null) {
      mirror.put(path, { version: got.version, size: got.size, mtime_ms: 0,
                         sha256: got.sha256 });
    }
    return;
  }
  const local = localOf(place, path);
  const readOnly = place.authority !== "owner" && inOwnerTrees(path);
  if (kind === null) {
    remove(local);
    mirror.dropUnder(path);
    return;
  }
  if (kind === "dir") {
    safeDirs(place.local, local, readOnly ? 0o2755 : 0o2775);
    return;
  }
  const dirMode = readOnly ? 0o2755 : 0o2775;
  const got = await fetchInto(client, path, place.local, local, (mode) => readOnly
    ? (mode === 1 ? 0o555 : 0o444) : (mode === 1 ? 0o775 : 0o664), dirMode);
  if (got === null) {
    return;  // gone since the feed was read: a later change says so
  }
  mirror.put(path, { version: got.version, size: got.size,
                     mtime_ms: trusted(lstatSync(local).mtimeMs, Date.now()),
                     sha256: got.sha256 });
}

/** Every path of the readable tree, by listing, applied as each page comes (a reset). */
async function listApply(client: SyncClient, place: Place, mirror: Mirror,
                         dir: string): Promise<void> {
  let after: string | null = null;
  for (;;) {
    const page = await client.post("list", { dir, limit: FEED_LIMIT,
                                             ...(after === null ? {} : { after }) });
    for (const entry of page["entries"] as { path: string; kind: string }[]) {
      await apply(client, place, mirror, entry.path, entry.kind);
      mirror.seen(entry.path);
      if (entry.kind === "dir") {
        await listApply(client, place, mirror, entry.path);
      }
    }
    after = page["next"] as string | null;
    if (after === null) {
      return;
    }
  }
}

/** The owner's files into the sandbox, from the mirror's cursor; the mirror updated. */
export async function syncIn(client: SyncClient, place: Place, mirror: Mirror): Promise<void> {
  for (;;) {
    const cursor = mirror.cursor();
    const page = await client.post("changes", { after: cursor, limit: FEED_LIMIT });
    const changes = page["changes"] as { path: string; kind: string | null; version: number }[];
    if (page["reset"] === true) {
      mirror.resetBegin();
      try {
        await listApply(client, place, mirror, "/workspace");
        for (let after = "", gone = mirror.unseen(after, PAGE); gone.length > 0;
             gone = mirror.unseen(after, PAGE)) {
          for (const path of gone) {
            if (!path.startsWith(`${SESSIONS}/`)) {
              await apply(client, place, mirror, path, null);
            }
          }
          after = gone.at(-1)!;
        }
      } finally {
        mirror.resetEnd();
      }
      mirror.setCursor(page["cursor"] as number);
      continue;
    }
    const last = new Map<string, string | null>();
    for (const change of changes) {
      last.delete(change.path);
      last.set(change.path, change.kind);
    }
    for (const [path, kind] of last) {
      if (path === "/workspace" || path.startsWith("/workspace/")) {
        await apply(client, place, mirror, path, kind);
      }
    }
    mirror.setCursor(page["cursor"] as number);
    if (changes.length < FEED_LIMIT) {
      return;
    }
  }
}

export interface SyncReport {
  readonly committed: number;
  readonly conflicts: readonly string[];
  readonly refused: readonly { readonly path: string; readonly reason: string }[];
  /**
   * Files skipped for this sync: gone, or changed, while it ran (sent by the next one); and
   * files the owner keeps whose changes a custom sync-ignore line holds back.
   */
  readonly skipped: readonly { readonly path: string; readonly reason: string }[];
  /** Skipped files past the listed ones (held back files are listed at most `LISTED`). */
  readonly skipped_unlisted: number;
}

/** Held-back files a report lists by path, at most: past them it counts them. */
const LISTED = 100;
/** Why a kept file's changes stay in the sandbox. */
const HELD_BACK = "held back by a sync-ignore line: your files keep the version they have";

/**
 * A change time the mirror may trust: one older than the filesystem's coarsest tick before
 * `at` (when it was read). A file changed within that tick of its read could change again,
 * at the same size, with no new change time (git's "racily clean"), so its time is kept as
 * `RACY`, matching nothing: the next sync reads and hashes it again, and the mirror's hash
 * is always of the bytes sent.
 */
function trusted(mtime: number, at: number): number {
  return mtime >= at - RACY_MS ? RACY : mtime;
}
/** The coarsest change-time tick a filesystem keeps (FAT's two seconds), in ms. */
const RACY_MS = 2_000;
const RACY = -1;

/**
 * The sync-ignore rules of the workspace at `local`: its file's lines, or the defaults, the
 * file written with them (so the owner sees and edits them) when it is missing.
 */
export function syncIgnore(local: string): readonly string[] {
  const path = localOf({ local }, SYNC_IGNORE);
  const read = safeRead(local, path);
  if (read !== null) {
    return read.toString("utf8").split("\n");
  }
  if (!existsSync(path)) {
    safeWrite(local, path, new TextEncoder().encode(`${defaultSyncIgnore().join("\n")}\n`),
              0o664, undefined, 0o2775);
  }
  return defaultSyncIgnore();
}

/** A file offered: what it is and what the mirror knew; its bytes only when it is small. */
interface Op {
  readonly op: "write" | "remove";
  readonly path: string;
  readonly base: number | null;
  readonly mode?: number;
  /** Where it is read from, for a file past an inline write, read again chunk by chunk. */
  readonly from?: { readonly base: string; readonly local: string };
  /** A small file's bytes (at most `INLINE_BYTES`), carried whole. */
  readonly inline?: Uint8Array;
  /** Its identity when it was offered: what the mirror records once it lands. */
  readonly size?: number;
  readonly mtime_ms?: number;
  readonly sha256?: string;
  /** A large file's chunks' hashes, as read when it was offered. */
  readonly chunks?: readonly string[];
}

/** The longest secret atom, four bytes a character: the overlap a piece's scan keeps. */
function overlapOf(atoms: readonly string[]): number {
  return 4 * atoms.reduce((most, atom) => Math.max(most, atom.length), 0);
}

/**
 * A large file read once, a chunk at a time: its whole hash, each chunk's, and whether any
 * of it holds a secret's bytes (each piece scanned with the end of the one before, so no atom
 * hides across a boundary). Null when it is no regular file to read.
 */
async function scanned(base: string, local: string, atoms: readonly string[]):
    Promise<{ sha256: string; chunks: string[]; size: number; secret: boolean } | null> {
  const whole = createHash("sha256");
  const chunks: string[] = [];
  const keep = overlapOf(atoms);
  let tail = Buffer.alloc(0);
  let secret = false;
  let size = 0;
  const ok = await safePieces(base, local, CHUNK_BYTES, (piece) => {
    whole.update(piece);
    chunks.push(sha256(piece));
    size += piece.byteLength;
    if (!secret && atoms.length > 0) {
      const seen = Buffer.concat([tail, piece]);
      secret = heldSecret(seen.toString("latin1"), atoms) !== null
        || heldSecret(seen.toString("utf8"), atoms) !== null;
      tail = seen.subarray(Math.max(0, seen.byteLength - keep));
    }
  });
  return ok ? { sha256: whole.digest("hex"), chunks, size, secret } : null;
}

class ChangedWhileSyncing extends Error {
  override readonly name = "ChangedWhileSyncing";
}

/**
 * A large file through the files plane's upload doors, a chunk at a time as it is read: its
 * intent's id. A chunk that no longer matches what was offered (the file changed since), or
 * a file gone, ends it (`ChangedWhileSyncing`); a refusal of a chunk ends it too. Either way
 * the upload is aborted at once, so nothing holds the owner's storage for it.
 */
async function upload(client: SyncClient, op: Op,
                      lease: { lease_id: string; epoch: number }): Promise<string> {
  const begun = await client.post("upload_begin", { lease_id: lease.lease_id,
                                                    epoch: lease.epoch, size: op.size,
                                                    chunks: op.chunks });
  const intentId = begun["intent_id"] as string;
  try {
    const missing = new Set(begun["missing"] as string[]);
    const ok = await safePieces(op.from!.base, op.from!.local, CHUNK_BYTES,
                                async (piece, i) => {
      const hash = sha256(piece);
      if (hash !== op.chunks![i]) {
        throw new ChangedWhileSyncing(op.path);
      }
      if (missing.delete(hash)) {
        await client.post("upload_chunks", { lease_id: lease.lease_id, epoch: lease.epoch,
                                             intent_id: intentId, chunks: [
          { sha256: hash, bytes: piece.toString("base64") }] });
      }
    });
    if (!ok || missing.size > 0) {
      throw new ChangedWhileSyncing(op.path);
    }
  } catch (error) {
    try {
      await client.post("upload_abort", { lease_id: lease.lease_id, epoch: lease.epoch,
                                          intent_id: intentId });
    } catch (aborting) {
      // A lost lease lets its uploads go itself (`Files.#endUploads`); any other refusal of
      // the abort leaves the upload to its own expiry, and the first error stands.
      if (aborting instanceof SyncFenced) {
        throw aborting;
      }
    }
    throw error;
  }
  return intentId;
}

/** A path's local identity now: a regular file's stat, or null when it is gone or not one. */
function fileStat(local: string): Stats | null {
  try {
    const stat = lstatSync(local);
    return stat.isFile() ? stat : null;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT"
        || (error as NodeJS.ErrnoException).code === "ENOTDIR") {
      return null;
    }
    throw error;
  }
}

/**
 * The sandbox's changes back to the owner's files, as fenced commits on the lease; `atoms`
 * the secrets' atoms (`guards.ts`) no synced file may hold. What it holds is bounded whatever
 * the tree holds (the module's note); the mirror is written as each batch commits.
 */
export async function syncOut(client: SyncClient, place: Place, mirror: Mirror,
                              lease: { lease_id: string; epoch: number },
                              atoms: readonly string[]): Promise<SyncReport> {
  const refused: { path: string; reason: string }[] = [];
  const conflicts: string[] = [];
  const skipped: { path: string; reason: string }[] = [];
  /** Kept files a custom line holds back, and of those the ones with changes here. */
  let kept = 0;
  let held = 0;
  let committed = 0;
  let batch: Op[] = [];
  let bytes = 0;
  const send = async () => {
    if (batch.length === 0) {
      return;
    }
    const taken = batch;
    batch = [];
    bytes = 0;
    const sent: Record<string, unknown>[] = [];
    const ops: Op[] = [];
    for (const op of taken) {
      const fields = { op: op.op, path: op.path, base: op.base,
                       ...(op.mode === undefined ? {} : { mode: op.mode }),
                       // The list's own write carries what it holds back, for the owner's
                       // notice (counted here, in the sandbox, never in the object's write).
                       ...(op.path === SYNC_IGNORE && op.op === "write"
                         ? { held_back: { kept, changed: held } } : {}) };
      if (op.op === "remove") {
        sent.push(fields);
      } else if (op.inline !== undefined) {
        sent.push({ ...fields, inline: Buffer.from(op.inline).toString("base64") });
      } else {
        try {
          sent.push({ ...fields, intent_id: await upload(client, op, lease) });
        } catch (error) {
          if (error instanceof ChangedWhileSyncing) {
            skipped.push({ path: op.path,
                           reason: "changed while syncing; the next sync sends it" });
            continue;
          }
          if (error instanceof SyncRefused) {
            refused.push({ path: op.path, reason: error.message });
            continue;
          }
          throw error;
        }
      }
      ops.push(op);
    }
    if (sent.length === 0) {
      return;
    }
    const answer = await client.post("commit", { lease_id: lease.lease_id, epoch: lease.epoch,
                                                  ops: sent });
    const outcomes = answer["outcomes"] as { path: string; outcome: string;
                                             entry?: { version: number } | null;
                                             reason?: string }[];
    mirror.batch(() => {
      for (const outcome of outcomes) {
        const op = ops.find((each) => each.path === outcome.path);
        if (outcome.outcome === "committed") {
          committed += 1;
          if (op?.op === "remove" || outcome.entry === null || outcome.entry === undefined) {
            mirror.drop(outcome.path);
          } else {
            mirror.put(outcome.path, { version: outcome.entry.version, size: op!.size!,
                                       mtime_ms: op!.mtime_ms!, sha256: op!.sha256! });
          }
        } else if (outcome.outcome === "conflict") {
          conflicts.push(outcome.path);
        } else {
          refused.push({ path: outcome.path, reason: outcome.reason ?? "refused" });
        }
      }
    });
  };
  const add = async (op: Op) => {
    const size = op.inline === undefined ? op.path.length + 128
      : Math.ceil(op.inline.byteLength / 3) * 4 + op.path.length + 128;
    if (batch.length >= COMMIT_OPS || bytes + size > BODY_MAX) {
      await send();
    }
    batch.push(op);
    bytes += size;
  };
  const offer = async (path: string, base: string, local: string): Promise<void> => {
    const at = Date.now();
    const stat = fileStat(local);
    if (stat === null) {
      skipped.push({ path, reason: "gone before it was sent" });
      return;
    }
    const size = Number(stat.size);
    const mtime = trusted(Number(stat.mtimeMs), at);
    const known = mirror.get(path);
    if (known !== undefined && mtime !== RACY && known.size === size
        && known.mtime_ms === mtime) {
      return;
    }
    const mode = (Number(stat.mode) & 0o111) !== 0 ? 1 : 0;
    if (size <= INLINE_BYTES) {
      const small = safeRead(base, local);
      if (small === null) {
        skipped.push({ path, reason: "gone before it was sent" });
        return;
      }
      if (small.byteLength > INLINE_BYTES) {
        return offer(path, base, local);  // it grew since its stat: read it as a large one
      }
      const digest = sha256(small);
      if (known !== undefined && known.sha256 === digest) {
        mirror.put(path, { ...known, size: small.byteLength, mtime_ms: mtime });
        return;
      }
      if (heldSecret(small.toString("latin1"), atoms) !== null
          || heldSecret(small.toString("utf8"), atoms) !== null) {
        refused.push({ path, reason: "holds a secret's bytes" });
        return;
      }
      await add({ op: "write", path, base: known?.version ?? null, mode,
                  inline: new Uint8Array(small), size: small.byteLength, mtime_ms: mtime,
                  sha256: digest });
      return;
    }
    const read = await scanned(base, local, atoms);
    if (read === null) {
      skipped.push({ path, reason: "gone before it was sent" });
      return;
    }
    if (known !== undefined && known.sha256 === read.sha256) {
      mirror.put(path, { ...known, size: read.size, mtime_ms: mtime });
      return;
    }
    if (read.secret) {
      refused.push({ path, reason: "holds a secret's bytes" });
      return;
    }
    await add({ op: "write", path, base: known?.version ?? null, mode,
                from: { base, local }, size: read.size, mtime_ms: mtime, sha256: read.sha256,
                chunks: read.chunks });
  };
  // The sessions first, sent before any workspace file is read.
  for (const rel of walkFiles(place.sessionsOut)) {
    const [engine, ...rest] = rel.split("/");
    const path = `${SESSIONS}/${place.authority}/${engine}/${rest.join("/")}`;
    if (sessionOf(place, path) !== null) {
      await offer(path, place.sessionsOut, join(place.sessionsOut, rel));
    }
  }
  await send();
  const ignored = ignoredBy(syncIgnore(place.local));
  /** Kept out: a custom line's path, or a default line's path the owner does not keep yet. */
  const out = (rel: string): boolean => {
    const why = ignored(rel);
    return why === "custom" || (why === "default"
                                && !mirror.knowsUnder(`/workspace/${rel}`));
  };
  for (const rel of walkFiles(place.local, (each) => {
    const name = each.split("/").at(-1)!;
    return each === ".sessions" || name.startsWith(".ta-") || out(each.split("\\").join("/"));
  })) {
    const path = `/workspace/${rel.split("\\").join("/")}`;
    if (place.authority !== "owner" && inOwnerTrees(path)) {
      continue;
    }
    if (path === SYNC_IGNORE) {
      continue;  // offered last, once what its lines hold back is counted
    }
    await offer(path, place.local, localOf(place, path));
  }
  // What the mirror knows and the workspace no longer holds goes, page by page; a path a
  // custom line keeps out is never removed (the owner keeps it as it was).
  for (let after = "", known = mirror.page(after, PAGE); known.length > 0;
       known = mirror.page(after, PAGE)) {
    for (const path of known) {
      if (path.startsWith(`${SESSIONS}/`) || !path.startsWith("/workspace/")
          || (place.authority !== "owner" && inOwnerTrees(path))) {
        continue;
      }
      if (ignored(path.slice("/workspace/".length)) === "custom") {
        // Never removed (the owner keeps it as it was); its changes, never sent, are said.
        kept += 1;
        if (await heldBack(place, path, mirror.get(path)!)) {
          held += 1;
          if (held <= LISTED) {
            skipped.push({ path, reason: HELD_BACK });
          }
        }
        continue;
      }
      if (fileStat(localOf(place, path)) === null) {
        await add({ op: "remove", path, base: mirror.get(path)!.version });
      }
    }
    after = known.at(-1)!;
  }
  await offer(SYNC_IGNORE, place.local, localOf(place, SYNC_IGNORE));
  await send();
  return { committed, conflicts, refused, skipped, skipped_unlisted: Math.max(0, held - LISTED) };
}

/** Whether a kept file's sandbox copy differs from what the owner's files hold. */
async function heldBack(place: Place, path: string, known: Known): Promise<boolean> {
  const local = localOf(place, path);
  const stat = fileStat(local);
  if (stat === null) {
    return false;  // gone here: a held-back path is never removed, and nothing is to send
  }
  if (Number(stat.size) !== known.size) {
    return true;
  }
  if (known.mtime_ms !== RACY && Number(stat.mtimeMs) === known.mtime_ms) {
    return false;
  }
  const read = await scanned(place.local, local, []);
  return read !== null && read.sha256 !== known.sha256;
}
