/**
 * The runner's sync agent (`engines-replan.md` §2.1 steps 4 and 7), run as its own
 * unprivileged user (`ta-sync`, in group `ta`), never root: the owner's
 * files into the sandbox's `/workspace` before a run, and the run's changes back out after
 * it, through the sync doors on the egress host by the lease's sync token (`x-ta-sync`,
 * beside the egress token E2B's transform sets, so the token is good from inside the VM
 * alone), the object fencing each commit by the lease's id and epoch.
 *
 * What it holds at once is bounded whatever the tree holds, in bytes and in paths: the walk
 * reads one directory entry at a time (`walkFiles`), the mirror of what was synced is a
 * SQLite database on disk read a row or a page at a time (`openMirror`), a file's bytes move
 * a chunk at a time in both directions, and a commit holds one batch of ops (at most
 * `MAX_SYNC_OPS`, small files at most `INLINE_BYTES` each).
 *
 * - **In:** the change feed from the mirror's cursor, each path's last change applied with
 *   no link followed (`fsafe.ts`), a file's chunks streamed into a temporary file and renamed
 *   into place (`safeStream`); a reset reads the whole tree again by listing, page by page.
 *   A change at the version the mirror holds is this sandbox's own write, never fetched
 *   back. The engines' sessions (`/workspace/.sessions/<authority>/<engine>/...`) are never
 *   fetched by the feed: a session path it names is marked changed in the mirror (`unread`),
 *   and a resume fetches its own files (`fetchSessions`, the record's `resume_paths`), only
 *   under that engine's own `session_paths`, into the sync agent's own directory, for the
 *   root runner to put in that engine's store (the rows moved only for what it took,
 *   `tookSessions`); never into the shared workspace, where every engine's user could reach
 *   them.
 * - **Unsent changes, for every path:** a copy whose bytes are not the ones the mirror last
 *   synced, and that sync-out sends (`Selection`), is never replaced, removed or dropped by
 *   the owner's newer version: it goes out against the base it was read at, so the owner's
 *   commit finds the base moved and keeps both (the owner's in place, this sandbox's beside
 *   it as `<name>.ta-conflict-<epoch>`, reported, `ta.sync_conflicts`). Then this sandbox
 *   takes the owner's version: a workspace file at once (`theirs`), where it is still the
 *   copy sent; a session at its next staging (the store's copy let go) and a resume's fetch.
 *   So the race ends in one conflict copy, and a copy changed again since it was sent is a
 *   change of its own, sent again. A path whose kind changed (the owner's file where the
 *   sandbox holds a folder, or the reverse) is taken the same way: the sandbox's unsent files
 *   go out beside the owner's, then it takes the owner's kind.
 * - **Out:** each engine's sessions the root runner staged first (`stageSessions`, from the
 *   engines' stores, which only root reads), in commits of their own; then
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
import { sha256Hex } from "../../contract/src/digest";
import { createHash } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, renameSync, type Stats } from "node:fs";
import { dirname, join, relative } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { type Served, served } from "../../sources/src/served";

import { isKept, keptPaths } from "./engine";
import { remove, removeEmpty, safeDirs, safePieces, safeRead, safeReadHead, safeStream,
         safeWrite, walkFiles, walkTree } from "./fsafe";
import { heldSecret } from "./guards";
import { STATE_DIR } from "./protocol";
import { readJson, STORE, withStore } from "./session";
import { SYNC_HOME } from "./users";
import { type SYNC_DOORS, SYNC_HEADER, type SyncDoor } from "../../contract/src/egress";
import type { RelayedAnswer } from "../../contract/src/owner";
import { CLIENT_READ_ONLY, WORKSPACE } from "../../contract/src/paths";
import {
  CHUNK_BYTES, INLINE_BYTES, INTENT_ID_BYTES, MAX_LIST, MAX_SYNC_OPS, SYNC_BODY_MAX,
} from "../../contract/src/limits";
import {
  defaultSyncIgnore, ignoredBy, type Kind, LIST_BYTES, SYNC_IGNORE, type Unapplied,
} from "../../contract/src/sync-ignore";

/** An upload's intent id, as the object makes it: `INTENT_ID_BYTES` as hex. */
const INTENT_ID_CHARS = 2 * INTENT_ID_BYTES;
/**
 * A commit's ops, each counted at its bytes in the body (`opBytes`): the door's body less the
 * commit's own fields (its lease id, epoch and the JSON around them, under 128 bytes).
 */
const BODY_MAX = SYNC_BODY_MAX - 128;
export const SESSIONS = `${WORKSPACE}/.sessions`;
/** The mirror's rows a page of a removal scan or a reset reads at once. */
const PAGE = 1000;

/** The engines' sessions as last hashed, so an unchanged one is not read again a run. */
export const SESSIONS_SEEN = `${STATE_DIR}/sessions-seen.json`;

/** A session in an engine's store as last hashed: its identity, and a secret refusal's. */
interface SeenSession {
  readonly size: number;
  readonly mtime_ms: number;
  readonly sha256: string;
  /** The digest of the secrets it was refused against, when it held one's bytes. */
  readonly refused?: string;
}

/** The engines' sessions as last hashed (`SESSIONS_SEEN`), or none. */
function readSeen(): Record<string, SeenSession> {
  try {
    return existsSync(SESSIONS_SEEN) ? readJson<Record<string, SeenSession>>(SESSIONS_SEEN) : {};
  } catch {
    return {};  // unreadable: every session is hashed again, nothing skipped
  }
}

/**
 * A session in an engine's store: its engine, its path in the home and in the owner's files,
 * its bytes' hash, and its size and change time.
 */
export interface StoreSession {
  readonly rec: Served;
  readonly rel: string;
  readonly path: string;
  readonly digest: string;
  readonly size: number;
  readonly mtime_ms: number;
}

/**
 * Every engine's session in its store (`authority`'s paths in the owner's files), each hash
 * read again only when its size or change time moved since it was last hashed (or that time
 * was too recent to trust, `RACY_MS`), and the hashes to keep (`SESSIONS_SEEN`, `keepSeen`).
 */
export function storeSessions(authority: string):
    { sessions: StoreSession[]; seen: Record<string, SeenSession> } {
  const seen = readSeen();
  const next: Record<string, SeenSession> = {};
  const sessions: StoreSession[] = [];
  for (const each of served().values()) {
    const store = `${STORE}/${each.id}`;
    for (const rel of walkFiles(store)) {
      if (!isKept(keptPaths(each).sessions, rel)) {
        continue;
      }
      const path = `${SESSIONS}/${authority}/${each.id}/${rel}`;
      const at = Date.now();
      const stat = lstatSync(join(store, rel));
      const was = seen[path];
      let sha = was !== undefined && was.size === stat.size && was.mtime_ms === stat.mtimeMs
        && stat.mtimeMs < at - RACY_MS ? was.sha256 : null;
      if (sha === null) {
        const bytes = safeRead(store, join(store, rel));
        if (bytes === null) {
          continue;
        }
        sha = sha256Hex(bytes);
      }
      next[path] = { size: stat.size, mtime_ms: stat.mtimeMs, sha256: sha,
                     ...(was?.sha256 === sha && was.refused !== undefined
                       ? { refused: was.refused } : {}) };
      sessions.push({ rec: each, rel, digest: sha, path, size: stat.size,
                      mtime_ms: stat.mtimeMs });
    }
  }
  return { sessions, seen: next };
}

/** Whether the store's copy at `local` is still the one `session` hashed: its size and time. */
export function stillHashed(local: string, session: Pick<StoreSession, "size" | "mtime_ms">):
    boolean {
  const stat = lstatSync(local, { throwIfNoEntry: false });
  return stat?.size === session.size && stat.mtimeMs === session.mtime_ms;
}

/** The sessions' hashes kept, so the next read of the store hashes only what moved. */
export function keepSeen(seen: Record<string, SeenSession>): void {
  safeWrite(STATE_DIR, SESSIONS_SEEN, new TextEncoder().encode(JSON.stringify(seen)), 0o600);
}

/** What the mirror holds of a path for the root runner (`known`): its hash, and its mark. */
export type Held = Pick<Known, "sha256" | "unread"> | null;

/**
 * The root runner's step before a run's sync-out: every engine's session whose bytes are not
 * the ones the owner's files were last synced with (`known`: the sync mirror's row of each
 * path, read under the sync agent's lock) staged into the sync agent's `out/<run>`: this
 * run's, and any an earlier sync-out left unsent (refused at a full storage, changed while
 * sent, fenced, cut short), so each is offered again until it lands, whether or not a later
 * run touches it, and one the owner's files changed meanwhile is a conflict copy there. A
 * session synced as it is is never copied, and one the owner's files changed since (`unread`)
 * is a stale copy: it leaves the store, if it is still the copy hashed, and a resume fetches
 * the owner's. One holding a secret's bytes is never staged; it is returned (as
 * `{home}/<path>`) once, until it or the secrets change.
 */
export async function stageSessions(
  authority: string, run: string, secrets: readonly string[],
  known: (paths: readonly string[]) => Promise<Record<string, Held>>,
): Promise<string[]> {
  const out = `${SYNC_HOME}/out/${run}`;
  remove(out);
  const against = sha256Hex(new TextEncoder().encode(JSON.stringify([...secrets].sort())));
  const { sessions, seen } = storeSessions(authority);
  const held = sessions.length === 0 ? {} : await known(sessions.map((each) => each.path));
  const refused: string[] = [];
  for (const session of sessions) {
    const store = `${STORE}/${session.rec.id}`;
    const row = held[session.path];
    if (row?.sha256 === session.digest) {
      if (row.unread) {
        await withStore(session.rec, () => {
          if (stillHashed(join(store, session.rel), session)) {
            remove(join(store, session.rel));
          }
        });
      }
      continue;
    }
    if (seen[session.path]!.refused === against) {
      continue;
    }
    const bytes = safeRead(store, join(store, session.rel));
    if (bytes === null) {
      continue;
    }
    if (heldSecret(bytes.toString("latin1"), secrets) !== null) {
      refused.push(`{home}/${session.rel}`);
      seen[session.path] = { ...seen[session.path]!, refused: against };
      continue;
    }
    safeWrite(SYNC_HOME, join(out, session.rec.id, session.rel), bytes, 0o644, undefined,
              0o755);
  }
  keepSeen(seen);
  return refused;
}

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

/** A sync door's answer: its owner door's, as the doors relay it (`SYNC_DOORS`). */
type SyncAnswer<D extends SyncDoor> = RelayedAnswer<(typeof SYNC_DOORS)[D]>;

/**
 * The sync doors' client: a call refused for the moment (the network, `429`, `503`) is
 * asked again with no count, its wait doubling from `firstMs` (the runner's flush cadence:
 * no sooner than its next batch of frames would go) to `ceilingMs` (a heartbeat: the run's
 * report is never held longer than one). A lost lease (`401`, or the code `sync_fenced`) is
 * `SyncFenced`; any other refusal is `SyncRefused`, with the object's whole message, for the
 * caller to report as that call's own (a full storage's used and total among it, a path
 * gone's `404`).
 */
export class SyncClient {
  constructor(readonly origin: string, readonly token: string, readonly firstMs: number,
              readonly ceilingMs: number) {}

  async post<D extends SyncDoor>(door: D, body: unknown): Promise<SyncAnswer<D>> {
    for (let wait = this.firstMs; ; wait = Math.min(wait * 2, this.ceilingMs)) {
      let response: Response;
      try {
        response = await fetch(`${this.origin}/sync/${door}`, {
          method: "POST", body: JSON.stringify(body),
          headers: { "content-type": "application/json", [SYNC_HEADER]: this.token } });
      } catch {
        await new Promise((resolve) => setTimeout(resolve, wait));
        continue;
      }
      const text = await response.text();
      if (response.status === 200) {
        return JSON.parse(text) as SyncAnswer<D>;
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

/**
 * What the sandbox last synced of one path: the version it last read or wrote (the base of
 * its next write; null where it has read none), its bytes' identity (the bytes it last read
 * or wrote, or saved beside the owner's as a conflict copy; a size of 0 and no hash where it
 * holds none), and whether the owner's files hold a version of it this sandbox has not read
 * (`unread`: a session the feed named, or a reset no longer lists, kept for a resume to fetch;
 * a workspace file the sandbox's own unsent files stood in the way of, `theirs`).
 */
interface Known {
  readonly version: number | null;
  readonly size: number;
  readonly mtime_ms: number;
  readonly sha256: string;
  readonly unread: boolean;
}

/**
 * What the sandbox last synced: the feed's cursor, and each path's version and identity,
 * read and written a row at a time.
 */
interface Mirror {
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

/**
 * The sync agent's mirror on disk (`path`, a SQLite database), so its size never sits in
 * memory.
 */
export function openMirror(path: string): Mirror {
  const db = new DatabaseSync(path);
  const files = "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, version INTEGER, " +
    "size INTEGER NOT NULL, mtime_ms REAL NOT NULL, sha256 TEXT NOT NULL, " +
    "unread INTEGER NOT NULL) WITHOUT ROWID";
  db.exec("PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;");
  // A format 1 mirror's rows (`state-format.json`) rebuilt as this format's: its base was
  // never null, and its hash `""` was the mark, so a copy marked there holds no hash the
  // store's can match, and is sent, never dropped.
  const columns = db.prepare("SELECT name FROM pragma_table_info('files')").all();
  if (columns.length > 0 && !columns.some((each) => each["name"] === "unread")) {
    db.exec(`BEGIN IMMEDIATE; ALTER TABLE files RENAME TO files_1; ${files}; INSERT INTO ` +
            "files SELECT path, version, size, mtime_ms, sha256, sha256 = '' FROM files_1; " +
            "DROP TABLE files_1; COMMIT;");
  }
  db.exec(`${files}; CREATE TABLE IF NOT EXISTS meta (only INTEGER PRIMARY KEY CHECK ` +
          "(only = 1), cursor INTEGER NOT NULL); INSERT OR IGNORE INTO meta VALUES (1, 0);");
  const get = db.prepare("SELECT version, size, mtime_ms, sha256, unread FROM files " +
                         "WHERE path = ?");
  const put = db.prepare("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)");
  const drop = db.prepare("DELETE FROM files WHERE path = ?");
  const under = db.prepare("DELETE FROM files WHERE path = ? OR (path > ? AND path < ?)");
  const knows = db.prepare("SELECT 1 FROM files WHERE path = ? OR (path > ? AND path < ?) " +
                           "LIMIT 1");
  const page = db.prepare("SELECT path FROM files WHERE path > ? ORDER BY path LIMIT ?");
  return {
    cursor: () => (db.prepare("SELECT cursor FROM meta").get() as { cursor: number }).cursor,
    setCursor: (cursor) => { db.prepare("UPDATE meta SET cursor = ?").run(cursor); },
    get: (at) => {
      const row = get.get(at) as (Omit<Known, "unread"> & { unread: number }) | undefined;
      return row === undefined ? undefined : { ...row, unread: row.unread === 1 };
    },
    put: (at, known) => {
      put.run(at, known.version, known.size, known.mtime_ms, known.sha256,
              known.unread ? 1 : 0);
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
}

/** Where the sandbox keeps the owner's files, and the sync agent's own directories. */
export interface Place {
  /** The local directory standing for `/workspace`. */
  readonly local: string;
  /** The sync agent's own directory: sessions in from the feed, and out from the store. */
  readonly sessionsIn: string;
  readonly sessionsOut: string;
  /** Whose sandbox: `owner`, or a client's id (`CLIENT_READ_ONLY` read-only, never written). */
  readonly authority: string;
  /** The records, for each engine's own `session_paths`. */
  readonly records: ReadonlyMap<string, Served>;
}

function localOf(place: Pick<Place, "local">, path: string): string {
  return join(place.local, relative(WORKSPACE, path));
}

function clientReadOnly(path: string): boolean {
  return CLIENT_READ_ONLY.some((tree) => path === tree || path.startsWith(`${tree}/`));
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
  if (record === undefined || rel === "" || rel.split("/").includes("..")) {
    return null;
  }
  return isKept(keptPaths(record).sessions, rel) ? { engine: engine!, rel } : null;
}

/** A file fetched whole into a temporary file beside its place: put there, or let go. */
interface Fetched {
  readonly version: number;
  readonly size: number;
  readonly sha256: string;
  put(): void;
  drop(): void;
}

/**
 * A file's bytes from the owner's files beside `local` (under `base`), streamed: inline, or
 * two chunks a call each appended to a temporary file as it comes, its hash taken as it
 * streams, for the caller to put in place (renamed) or let go. Null when the path is gone or
 * is no file.
 */
async function fetchInto(client: SyncClient, path: string, base: string, local: string,
                         modeOf: (mode: number) => number, dirMode: number):
    Promise<Fetched | null> {
  let read: SyncAnswer<"read">;
  try {
    read = await client.post("read", { path });
  } catch (error) {
    if (error instanceof SyncRefused && error.status === 404) {
      return null;
    }
    throw error;
  }
  const entry = read.entry as { version: number; mode: number; kind: string };
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
    if (typeof read.inline === "string") {
      take(new Uint8Array(Buffer.from(read.inline, "base64")));
    } else {
      const chunks = (read.chunks ?? []) as { sha256: string; size: number }[];
      for (let first = 0; first < chunks.length; first += 2) {
        const got = await client.post("chunks", { path, version: entry.version, first,
                                                   count: Math.min(2, chunks.length - first) });
        for (const part of got.chunks) {
          take(new Uint8Array(Buffer.from(part, "base64")));
        }
      }
    }
  } catch (error) {
    out.abort();
    throw error;
  }
  return { version: entry.version, size, sha256: hash.digest("hex"),
           put: () => { out.commit(); }, drop: () => { out.abort(); } };
}

/** A row for a path this sandbox holds no bytes of and has read no version of. */
const NOTHING: Known = { version: null, size: 0, mtime_ms: 0, sha256: "", unread: false };

/**
 * A session the owner's files changed since this sandbox read it: marked so (`unread`), its
 * row's base and identity kept (the version and the bytes this sandbox last read or wrote),
 * its bytes left where they are, for a resume to fetch (`fetchSessions`). So a copy staged
 * before the mark is a conflict at the owner's files, kept beside their newer one, never
 * over it; one the store holds as synced is let go at the next staging; and one holding
 * unsent changes is sent, a conflict there too. A path with no row is marked with no base:
 * a copy of it is a conflict wherever the owner's files hold the path.
 */
function unread(place: Place, mirror: Mirror, path: string): void {
  if (sessionOf(place, path) !== null) {
    mark(mirror, path);
  }
}

/** `path` marked as holding a version in the owner's files this sandbox has not read. */
function mark(mirror: Mirror, path: string): void {
  mirror.put(path, { ...(mirror.get(path) ?? NOTHING), unread: true });
}

/** Whether `path` is the sessions' tree or under it, which the feed never writes locally. */
function inSessions(path: string): boolean {
  return path === SESSIONS || path.startsWith(`${SESSIONS}/`);
}

/** Two looks at one path the same: both nothing, or one file unchanged between them. */
function same(now: Stats | null, was: Stats | null): boolean {
  return now === null ? was === null : was !== null && now.ino === was.ino
    && now.size === was.size && now.mtimeMs === was.mtimeMs;
}

/**
 * The owner's state of a workspace path (`kind`: a file, a folder, or null when gone) into the
 * sandbox, unless the sandbox's file there holds changes not sent: changes sync-out sends
 * (`sends`), bytes other than `sent`'s (the identity the owner's files hold of its bytes;
 * none: any bytes). One that does is left as it is, its row too, so sync-out sends it against
 * the base it was read at. A file's bytes are fetched beside it and put in place only while
 * the sandbox's copy is still the one looked at (a run's write meanwhile is left the same
 * way); a folder gone goes but for the files under it holding unsent changes.
 *
 * Where the path changed kind, the sandbox's kind goes the same way: a file of its own where
 * the owner's files hold a folder is replaced by one, and a folder where they hold a file
 * goes but for its files holding unsent changes. A file the sandbox cannot take yet, its
 * folder holding such files or a file holding them standing on its way, is marked (`mark`):
 * those go out against their bases, the owner's commit keeps them beside its own, and the
 * sync-out after takes each marked path (`syncOut`'s scan).
 */
async function theirs(client: SyncClient, place: Place, mirror: Mirror,
                      select: () => Selection, path: string, kind: string | null,
                      sent: Identity | undefined): Promise<void> {
  const local = localOf(place, path);
  const was = fileStat(local);
  if (was !== null && select().sends(path) && await differs(place, path, was, sent)) {
    return;
  }
  if (onFile(place, local)) {
    if (kind === "file") {
      mark(mirror, path);
    }
    return;
  }
  const readOnly = place.authority !== "owner" && clientReadOnly(path);
  const dirMode = readOnly ? 0o2755 : 0o2775;
  if (kind === "dir") {
    if (was !== null) {
      if (!same(fileStat(local), was)) {
        return;
      }
      remove(local);
    }
    mirror.drop(path);
    safeDirs(place.local, local, dirMode);
    return;
  }
  const folder = was === null
    && lstatSync(local, { throwIfNoEntry: false })?.isDirectory() === true;
  if (kind === null) {
    if (folder) {
      await goneFolder(place, mirror, select, path);
    } else if (same(fileStat(local), was)) {
      remove(local);
      mirror.dropUnder(path);
    }
    return;
  }
  if (folder && !await goneFolder(place, mirror, select, path)) {
    mark(mirror, path);
    return;
  }
  const got = await fetchInto(client, path, place.local, local, (mode) => readOnly
    ? (mode === 1 ? 0o555 : 0o444) : (mode === 1 ? 0o775 : 0o664), dirMode);
  if (got === null) {
    return;  // gone since the feed was read: a later change says so
  }
  if (!same(fileStat(local), was)) {
    got.drop();
    return;
  }
  got.put();
  mirror.put(path, { version: got.version, size: got.size,
                     mtime_ms: trusted(lstatSync(local).mtimeMs, Date.now()),
                     sha256: got.sha256, unread: false });
}

/**
 * A folder gone from the owner's files, gone from the sandbox but for the files under it that
 * hold changes sync-out sends: whether it went. Its tree is walked an entry at a time,
 * whatever a folder lists, and each entry goes once the walk met it, its rows with it: a file
 * unless it holds unsent changes (kept with its row, sent against its base, a conflict
 * there), and what sync-out never sends (`passed`, `unnamed`) whole. A directory read as it
 * changes still gives every entry neither made nor removed since once (POSIX `readdir`), but
 * one made since may be missed, so each folder goes only once the walk left it, and only
 * while it holds nothing (`removeEmpty`). A file a run made in it after the walk read past it
 * keeps its folders, and the next sync-out sends it as it sends any new file (the owner's
 * commit makes its folders again).
 */
async function goneFolder(place: Place, mirror: Mirror, select: () => Selection,
                          path: string): Promise<boolean> {
  const local = localOf(place, path);
  if (!select().sends(path, true)) {
    remove(local);
  } else {
    const under = `${path.slice(`${WORKSPACE}/`.length)}/`;
    for (const met of walkTree(local, { skip: (rel, isDir) => select().skip(under + rel,
                                                                            isDir) })) {
      if (met.kind === "unnamed") {
        remove(met.at);
        continue;
      }
      const each = `${path}/${met.rel}`;
      let gone = true;
      if (met.kind === "left") {
        gone = removeEmpty(met.at);
      } else if (met.kind === "passed") {
        remove(met.at);
      } else {
        const stat = fileStat(met.at);
        gone = (stat === null || !await differs(place, each, stat, mirror.get(each)))
          && same(fileStat(met.at), stat);
        if (gone) {
          remove(met.at);
        }
      }
      if (gone) {
        mirror.dropUnder(each);
      }
    }
  }
  const went = removeEmpty(local);
  if (went) {
    mirror.dropUnder(path);
  }
  return went;
}

/**
 * Whether a regular file of the sandbox's stands on `local`'s way, where the owner's files
 * hold a folder (a link there is left to `fsafe.ts`'s refusal).
 */
function onFile(place: Pick<Place, "local">, local: string): boolean {
  const parent = dirname(local);
  if (parent === place.local) {
    return false;
  }
  try {
    return lstatSync(parent).isFile();
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOTDIR") {
      return onFile(place, parent);
    }
    if (code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

/** A path the owner's files changed (to `kind`, null when gone), as sync-in takes it. */
async function change(client: SyncClient, place: Place, mirror: Mirror,
                      select: () => Selection, path: string, kind: string | null):
    Promise<void> {
  if (inSessions(path)) {
    if (kind !== "dir") {
      unread(place, mirror, path);
    }
    return;
  }
  await theirs(client, place, mirror, select, path, kind, mirror.get(path));
}

/**
 * One path's state after the feed: gone, a directory, or a file at a version. One at the
 * version the mirror holds is already here (this sandbox's own write, or one it read).
 */
async function apply(client: SyncClient, place: Place, mirror: Mirror,
                     select: () => Selection, path: string, kind: string | null,
                     version: number): Promise<void> {
  if (mirror.get(path)?.version !== version) {
    await change(client, place, mirror, select, path, kind);
  }
}

/** Every path of the readable tree, by listing, applied as each page comes (a reset). */
async function listApply(client: SyncClient, place: Place, mirror: Mirror,
                         select: () => Selection, dir: string): Promise<void> {
  let after: string | null = null;
  for (;;) {
    const page: SyncAnswer<"list"> = await client.post("list", {
      dir, limit: MAX_LIST, ...(after === null ? {} : { after }) });
    for (const entry of page.entries as { path: string; kind: string; version: number }[]) {
      await apply(client, place, mirror, select, entry.path, entry.kind, entry.version);
      mirror.seen(entry.path);
      if (entry.kind === "dir") {
        await listApply(client, place, mirror, select, entry.path);
      }
    }
    after = page.next;
    if (after === null) {
      return;
    }
  }
}

/** The owner's files into the sandbox, from the mirror's cursor; the mirror updated. */
export async function syncIn(client: SyncClient, place: Place, mirror: Mirror): Promise<void> {
  // The sync-ignore is read only where a file is met that may hold changes, as it stands then.
  let selected: Selection | null = null;
  const select = () => selected ??= selection(place, mirror);
  for (;;) {
    const cursor = mirror.cursor();
    const page = await client.post("changes", { after: cursor, limit: MAX_LIST });
    const changes = page.changes as { path: string; kind: string | null; version: number }[];
    if (page.reset === true) {
      mirror.resetBegin();
      try {
        await listApply(client, place, mirror, select, WORKSPACE);
        for (let after = "", gone = mirror.unseen(after, PAGE); gone.length > 0;
             gone = mirror.unseen(after, PAGE)) {
          for (const path of gone) {
            await change(client, place, mirror, select, path, null);
          }
          after = gone.at(-1)!;
        }
      } finally {
        mirror.resetEnd();
      }
      mirror.setCursor(page.cursor);
      continue;
    }
    const last = new Map<string, { kind: string | null; version: number }>();
    for (const each of changes) {
      last.delete(each.path);
      last.set(each.path, each);
    }
    for (const [path, { kind, version }] of last) {
      if (path === WORKSPACE || path.startsWith(`${WORKSPACE}/`)) {
        await apply(client, place, mirror, select, path, kind, version);
      }
    }
    mirror.setCursor(page.cursor);
    if (changes.length < MAX_LIST) {
      return;
    }
  }
}

/**
 * A resume's session as fetched (`fetchSessions`): the owner's version of it (`got`, null
 * where their files no longer hold it), and the base and hash of the row it was fetched
 * against, which it moves only once the store took it (`tookSessions`).
 */
export interface Fetch {
  readonly path: string;
  readonly got: { readonly version: number; readonly size: number; readonly sha256: string }
    | null;
  readonly against: Pick<Known, "version" | "sha256">;
}

/**
 * A resume's files (`resumed`, from the engine's home: its record's `resume_paths`, the sid
 * filled) that the owner's files hold, by the mirror's rows, fetched into the sync agent's
 * own directory where the store lacks one (`have`: the store's copies among them, each by its
 * hash) or holds one the owner's files changed since (`unread`) that holds no change of its
 * own (its hash the row's). One holding changes stays for the resume, and is sent at the
 * run's staging, a conflict copy at the owner's files. One gone from the owner's files is
 * named with no bytes, for the root runner to drop the store's copy. No row moves here: the
 * root says what the store took (`tookSessions`), and with it the feed's cursor (`cursor`).
 */
export async function fetchSessions(client: SyncClient, place: Place, mirror: Mirror,
                                    engine: string, resumed: readonly string[],
                                    have: ReadonlyMap<string, string>):
    Promise<{ fetched: Fetch[]; cursor: number }> {
  const prefix = `${SESSIONS}/${place.authority}/${engine}/`;
  const fetched: Fetch[] = [];
  for (let after = prefix, paths = mirror.page(after, PAGE);
       paths.length > 0 && paths[0]!.startsWith(prefix); paths = mirror.page(after, PAGE)) {
    for (const path of paths.filter((each) => each.startsWith(prefix))) {
      const session = sessionOf(place, path);
      const row = mirror.get(path)!;
      const held = have.get(path);
      if (session === null || !isKept(resumed, session.rel)
          || (held !== undefined && (!row.unread || held !== row.sha256))) {
        continue;
      }
      const got = await fetchInto(client, path, place.sessionsIn,
                                  join(place.sessionsIn, engine, session.rel), () => 0o600,
                                  0o700);
      got?.put();
      fetched.push({ path, against: { version: row.version, sha256: row.sha256 },
                     got: got === null ? null
                       : { version: got.version, size: got.size, sha256: got.sha256 } });
    }
    after = paths.at(-1)!;
  }
  return { fetched, cursor: mirror.cursor() };
}

/**
 * The rows of a resume's sessions the root runner put in the store, or whose store copy it
 * let go where the owner's files no longer hold them (`took`, from `fetchSessions` with the
 * feed's `cursor` then): each moved to the owner's state as fetched, only while it is still
 * the row fetched against, and marked again where it was marked and a sync-in since may have
 * named a newer version (the cursor moved on). One the root did not take (a run kept a newer
 * copy in the store meanwhile) stays on the base that copy was made on, so it is sent against
 * it: a conflict copy at the owner's files, never one over theirs.
 */
export function tookSessions(mirror: Mirror, took: readonly Fetch[], cursor: number): void {
  const named = mirror.cursor() !== cursor;
  mirror.batch(() => {
    for (const each of took) {
      const row = mirror.get(each.path);
      if (row === undefined || row.version !== each.against.version
          || row.sha256 !== each.against.sha256) {
        continue;
      }
      const unread = named && row.unread;
      if (each.got === null && !unread) {
        mirror.drop(each.path);
      } else {
        mirror.put(each.path, { ...(each.got === null ? NOTHING : { ...each.got, mtime_ms: 0 }),
                                unread });
      }
    }
  });
}

export interface SyncReport {
  readonly committed: number;
  readonly conflicts: readonly string[];
  /**
   * Files refused: by the owner's object or its doors (their words, and their code), or here
   * (`secret_bytes`: the file holds a secret's bytes, its code alone).
   */
  readonly refused: readonly Refused[];
  /**
   * Files skipped for this sync, each by its code: gone (`gone`), or changed (`changed`),
   * while it ran (sent by the next one); and files the owner keeps whose changes a custom
   * sync-ignore line holds back (`held_back`).
   */
  readonly skipped: readonly { readonly path: string; readonly code: SkipCode }[];
  /** Skipped files past the listed ones (held back files are listed at most `LISTED`). */
  readonly skipped_unlisted: number;
  /** How long matching the sync-ignore list took this sync, in ms. */
  readonly match_ms: number;
  /**
   * The list's lines not applied (the files they name go on syncing), each with the ceiling
   * it is past (`AUTOMATON_STATES` or `LIST_BYTES`): the first `LISTED`, and how many.
   */
  readonly unapplied: readonly Unapplied[];
  readonly unapplied_count: number;
  /** The list's bytes past `LIST_BYTES`, never read nor applied. */
  readonly list_bytes_past: number;
  /**
   * Files and folders whose names are not UTF-8, so never synced (a path is text at the
   * owner's files): each from `/workspace`, its bytes escaped (`\xNN`), the first `LISTED`,
   * and how many.
   */
  readonly unnamed: readonly string[];
  readonly unnamed_count: number;
}

/** A file a sync refused: the object's words and code, or the sync agent's code alone. */
export type Refused = { readonly path: string; readonly reason: string; readonly code: string }
  | { readonly path: string; readonly code: "secret_bytes" };
/** Why a file was skipped this sync (the app says each in words). */
export type SkipCode = "changed" | "gone" | "held_back";

/** Held-back files a report lists by path, at most: past them it counts them. */
const LISTED = 100;

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
/**
 * The coarsest change-time tick a filesystem keeps (FAT's two seconds), in ms: a change time
 * newer than this is not trusted (here and in `runner.ts`).
 */
export const RACY_MS = 2_000;
const RACY = -1;

/**
 * The sync-ignore rules of the workspace at `local`: its file's lines, read to `LIST_BYTES`
 * at most (a line the cut falls in, and every one after it, not applied: `past` says how
 * many bytes), or the defaults while it is missing (sync-out writes them, `keepDefaults`).
 */
function syncIgnore(local: string): { lines: readonly string[]; past: number } {
  const read = safeReadHead(local, localOf({ local }, SYNC_IGNORE), LIST_BYTES);
  if (read === null) {
    return { lines: defaultSyncIgnore(), past: 0 };
  }
  let text = read.bytes;
  if (read.size > read.bytes.length) {
    const cut = text.lastIndexOf(10);
    text = text.subarray(0, cut < 0 ? 0 : cut);
  }
  return { lines: text.toString("utf8").split("\n"), past: read.size - text.length };
}

/**
 * The sync-ignore file written with the defaults where it is missing, so the owner sees and
 * edits them: by sync-out alone, so a sync-in never makes a copy of it the owner's own would
 * meet as one holding changes.
 */
function keepDefaults(local: string): void {
  const path = localOf({ local }, SYNC_IGNORE);
  if (!existsSync(path)) {
    safeWrite(local, path, new TextEncoder().encode(`${defaultSyncIgnore().join("\n")}\n`),
              0o664, undefined, 0o2775);
  }
}

/** A file's bytes as synced: its size and change time, and its hash. */
type Identity = Pick<Known, "size" | "mtime_ms" | "sha256">;

/**
 * What sync-out offers of the workspace, by its sync-ignore as read when this was made: every
 * regular file with a UTF-8 name but what the walk keeps out (`skip`); the one rule sync-in
 * reads too (`sends`).
 */
interface Selection {
  /** The kind of line that keeps `rel` (from `/workspace`) out, or null; its time counted. */
  readonly ignored: (rel: string, isDir?: boolean) => Kind | null;
  /**
   * An entry kept out, a directory with its whole tree, by its path from `/workspace`: the
   * sessions' tree (the store's to send), a temporary file's name (`.ta-`), a client's
   * read-only tree, a custom line's path, and a default line's path the owner does not keep.
   */
  readonly skip: (rel: string, isDir: boolean) => boolean;
  /** Whether sync-out offers `path` (a folder's tree, `isDir`): nothing on its way kept out. */
  readonly sends: (path: string, isDir?: boolean) => boolean;
  /** The list's lines not applied, past a ceiling, and its bytes past `LIST_BYTES`. */
  readonly unapplied: readonly Unapplied[];
  readonly past: number;
  /** How long matching has taken, in ms. */
  readonly matchMs: () => number;
}

function selection(place: Pick<Place, "local" | "authority">, mirror: Mirror): Selection {
  const list = syncIgnore(place.local);
  const matcher = ignoredBy(list.lines);
  let matchMs = 0;
  const ignored = (rel: string, isDir = false) => {
    const began = performance.now();
    try {
      return matcher(rel, isDir);
    } finally {
      matchMs += performance.now() - began;
    }
  };
  const skip = (rel: string, isDir: boolean): boolean => {
    const path = `${WORKSPACE}/${rel}`;
    if (path === SESSIONS || rel.split("/").at(-1)!.startsWith(".ta-")
        || (place.authority !== "owner" && clientReadOnly(path))) {
      return true;
    }
    const why = ignored(rel, isDir);
    return why === "custom" || (why === "default" && !mirror.knowsUnder(path));
  };
  const sends = (path: string, isDir = false): boolean => {
    const parts = path.slice(`${WORKSPACE}/`.length).split("/");
    return parts.every((_, i) => !skip(parts.slice(0, i + 1).join("/"),
                                       isDir || i < parts.length - 1));
  };
  return { ignored, skip, sends, unapplied: matcher.unapplied, past: list.past,
           matchMs: () => matchMs };
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
    chunks.push(sha256Hex(piece));
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
  const intentId = begun.intent_id;
  if (typeof intentId !== "string" || intentId.length !== INTENT_ID_CHARS) {
    // Each commit op is counted with an intent id of `INTENT_ID_CHARS` (`opBytes`).
    throw new Error(`the upload door's intent id is not ${INTENT_ID_CHARS} characters: ${
      JSON.stringify(intentId)}`);
  }
  try {
    const missing = new Set(begun.missing as string[]);
    const ok = await safePieces(op.from!.base, op.from!.local, CHUNK_BYTES,
                                async (piece, i) => {
      const hash = sha256Hex(piece);
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
  const refused: Refused[] = [];
  const conflicts: string[] = [];
  const skipped: { path: string; code: SkipCode }[] = [];
  /** Kept files a custom line holds back, and of those the ones with changes here. */
  let kept = 0;
  let held = 0;
  let committed = 0;
  let batch: Op[] = [];
  let bytes = 0;
  // What the walk offers, made once the sessions are sent (its sync-ignore read then).
  let selected: Selection | null = null;
  const select = () => selected ??= selection(place, mirror);
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
      const fields = fieldsOf(op);
      if (op.op === "remove") {
        sent.push(fields);
      } else if (op.inline !== undefined) {
        sent.push({ ...fields, inline: Buffer.from(op.inline).toString("base64") });
      } else {
        try {
          sent.push({ ...fields, intent_id: await upload(client, op, lease) });
        } catch (error) {
          if (error instanceof ChangedWhileSyncing) {
            skipped.push({ path: op.path, code: "changed" });
            continue;
          }
          if (error instanceof SyncRefused) {
            refused.push({ path: op.path, reason: error.message, code: error.code });
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
    const outcomes = answer.outcomes as { path: string; outcome: string;
                                             entry?: { version: number } | null;
                                             current?: { kind: string } | null;
                                             reason?: string; code?: string }[];
    const blind = outcomes.find((each) => each.outcome === "conflict"
                                && each.current === undefined);
    if (blind !== undefined) {
      throw new Error(`the commit's conflict at ${blind.path} names no current entry`);
    }
    const sentBeside: { op: Op; kind: string | null }[] = [];
    mirror.batch(() => {
      for (const outcome of outcomes) {
        const op = ops.find((each) => each.path === outcome.path);
        if (outcome.outcome === "committed") {
          committed += 1;
          if (op?.op === "remove" || outcome.entry === null || outcome.entry === undefined) {
            mirror.drop(outcome.path);
          } else {
            mirror.put(outcome.path, { version: outcome.entry.version, size: op!.size!,
                                       mtime_ms: op!.mtime_ms!, sha256: op!.sha256!,
                                       unread: false });
          }
        } else if (outcome.outcome === "conflict") {
          conflicts.push(outcome.path);
          if (inSessions(outcome.path)) {
            // Saved beside the owner's newer one: the row keeps its base and takes the copy's
            // bytes, marked, so the store's copy goes at the next staging while it is still
            // the one sent (a resume fetches the owner's), and one changed since is sent.
            mirror.put(outcome.path, { version: op!.base, size: op!.size!,
                                       mtime_ms: op!.mtime_ms!, sha256: op!.sha256!,
                                       unread: true });
          } else {
            sentBeside.push({ op: op!, kind: outcome.current?.kind ?? null });
          }
        } else {
          refused.push({ path: outcome.path, reason: outcome.reason ?? "refused",
                         code: outcome.code ?? "refused" });
        }
      }
    });
    // A workspace file saved beside the owner's newer version takes theirs, where it is still
    // the copy sent; one changed since is a change of its own, sent next time.
    for (const { op, kind } of sentBeside) {
      await theirs(client, place, mirror, select, op.path, kind, op.op === "remove" ? undefined
        : { size: op.size!, mtime_ms: op.mtime_ms!, sha256: op.sha256! });
    }
  };
  /** What a commit says of `op`, but its bytes (inline) or its upload (intent id). */
  const fieldsOf = (op: Op): Record<string, unknown> => ({
    op: op.op, path: op.path, base: op.base,
    ...(op.mode === undefined ? {} : { mode: op.mode }),
    // The list's own write carries what it holds back, for the owner's notice (counted
    // here, in the sandbox, never in the object's write). It is offered last, the counts
    // final.
    ...(op.path === SYNC_IGNORE && op.op === "write"
      ? { held_back: { kept, changed: held } } : {}) });
  const add = async (op: Op) => {
    const size = opBytes(op, fieldsOf(op));
    if (batch.length >= MAX_SYNC_OPS || bytes + size > BODY_MAX) {
      await send();
    }
    batch.push(op);
    bytes += size;
  };
  const offer = async (path: string, base: string, local: string): Promise<void> => {
    const at = Date.now();
    const stat = fileStat(local);
    if (stat === null) {
      skipped.push({ path, code: "gone" });
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
        skipped.push({ path, code: "gone" });
        return;
      }
      if (small.byteLength > INLINE_BYTES) {
        return offer(path, base, local);  // it grew since its stat: read it as a large one
      }
      const digest = sha256Hex(small);
      if (known !== undefined && known.sha256 === digest) {
        mirror.put(path, { ...known, size: small.byteLength, mtime_ms: mtime });
        return;
      }
      if (heldSecret(small.toString("latin1"), atoms) !== null
          || heldSecret(small.toString("utf8"), atoms) !== null) {
        refused.push({ path, code: "secret_bytes" });
        return;
      }
      await add({ op: "write", path, base: known?.version ?? null, mode,
                  inline: new Uint8Array(small), size: small.byteLength, mtime_ms: mtime,
                  sha256: digest });
      return;
    }
    const read = await scanned(base, local, atoms);
    if (read === null) {
      skipped.push({ path, code: "gone" });
      return;
    }
    if (known !== undefined && known.sha256 === read.sha256) {
      mirror.put(path, { ...known, size: read.size, mtime_ms: mtime });
      return;
    }
    if (read.secret) {
      refused.push({ path, code: "secret_bytes" });
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
  keepDefaults(place.local);
  // Lines past a ceiling (`AUTOMATON_STATES`, `LIST_BYTES`) are not applied, and said; and
  // how long matching the list took, so a slow list says so in the run's report.
  const { ignored, skip, unapplied, past, matchMs } = select();
  const unnamed: string[] = [];
  let unnamedCount = 0;
  for (const rel of walkFiles(place.local, { skip, unnamed: (rel) => {
    unnamedCount += 1;
    if (unnamed.length < LISTED) {
      unnamed.push(`${WORKSPACE}/${rel}`);
    }
  } })) {
    const path = `${WORKSPACE}/${rel}`;
    if (path === SYNC_IGNORE) {
      continue;  // offered last, once what its lines hold back is counted
    }
    await offer(path, place.local, localOf(place, path));
  }
  // The walk's files land (their conflicts taken) before the scan, so a path marked behind
  // them is taken by this sync.
  await send();
  // What the mirror knows and the workspace no longer holds goes, page by page; a path a
  // custom line keeps out is never removed (the owner keeps it as it was); a path marked
  // (`mark`: the owner's file this sandbox could not take) is taken, where it now can be.
  for (let after = "", known = mirror.page(after, PAGE); known.length > 0;
       known = mirror.page(after, PAGE)) {
    for (const path of known) {
      if (path.startsWith(`${SESSIONS}/`) || !path.startsWith(`${WORKSPACE}/`)
          || (place.authority !== "owner" && clientReadOnly(path))) {
        continue;
      }
      if (ignored(path.slice(`${WORKSPACE}/`.length)) === "custom") {
        // Never removed (the owner keeps it as it was); its changes, never sent, are said.
        kept += 1;
        const stat = fileStat(localOf(place, path));
        if (stat !== null && await differs(place, path, stat, mirror.get(path))) {
          held += 1;
          if (held <= LISTED) {
            skipped.push({ path, code: "held_back" });
          }
        }
        continue;
      }
      if (fileStat(localOf(place, path)) !== null) {
        continue;
      }
      const row = mirror.get(path);
      if (row === undefined) {
        continue;  // gone with a folder a take on this page let go
      }
      if (row.unread) {
        mirror.drop(path);
        await theirs(client, place, mirror, select, path, "file", undefined);
      } else {
        await add({ op: "remove", path, base: row.version });
      }
    }
    after = known.at(-1)!;
  }
  await offer(SYNC_IGNORE, place.local, localOf(place, SYNC_IGNORE));
  await send();
  return { committed, conflicts, refused, skipped, skipped_unlisted: Math.max(0, held - LISTED),
           match_ms: Math.round(matchMs()), unapplied: unapplied.slice(0, LISTED),
           unapplied_count: unapplied.length, list_bytes_past: past, unnamed,
           unnamed_count: unnamedCount };
}

/**
 * A template move's part of the sync's state (`runner.ts` `carry-out`), written into `dir` (the
 * sync agent's own): `dir/mirror.db`, a mirror of every session row (what the owner's files hold
 * of each store copy, carried with the store), its cursor 0. The workspace is not carried: the
 * next sandbox's first sync-in reads it from the owner's files, as a new sandbox's does. So the
 * carry is refused (false; its caller drops `dir`) while the workspace holds anything sync-out
 * would send, by sync-out's own rule (`differs`: a file's size and change time against its row,
 * its bytes read only where those do not settle it): a file with no row or another identity, or
 * a removal not sent (a row whose file is gone). The next run's end sends it, and a later try
 * moves. What the sync-ignore keeps out is the working copy's own, never sent; nor is a mode
 * alone, which no row keeps.
 */
export async function carryOut(place: Pick<Place, "local" | "authority">, mirror: Mirror,
                               dir: string): Promise<boolean> {
  const { ignored, skip } = selection(place, mirror);
  for (const rel of walkFiles(place.local, { skip })) {
    const path = `${WORKSPACE}/${rel}`;
    const stat = fileStat(localOf(place, path));
    if (stat !== null && await differs(place, path, stat, mirror.get(path))) {
      return false;
    }
  }
  // The rows a page at a time, each session row written as it comes.
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const out = openMirror(join(dir, "mirror.db"));
  try {
    for (let after = "", known = mirror.page(after, PAGE); known.length > 0;
         known = mirror.page(after, PAGE)) {
      for (const path of known) {
        const row = mirror.get(path)!;
        if (inSessions(path)) {
          out.put(path, row);
        } else if (path.startsWith(`${WORKSPACE}/`) && !row.unread
                   && ignored(path.slice(`${WORKSPACE}/`.length)) !== "custom"
                   && fileStat(localOf(place, path)) === null) {
          return false;
        }
      }
      after = known.at(-1)!;
    }
  } finally {
    out.close();
  }
  return true;
}

/**
 * A move's carry (`carryOut`'s `dir`, in a new sandbox) put in place: the carried mirror as this
 * sandbox's (`mirrorPath`). A sandbox that already holds a mirror is no new one, and is refused,
 * changing nothing.
 */
export function carryIn(dir: string, mirrorPath: string): void {
  if (existsSync(mirrorPath)) {
    throw new Error("this sandbox holds a mirror already: a carry goes into a new sandbox only");
  }
  renameSync(join(dir, "mirror.db"), mirrorPath);
}

/**
 * An op's bytes in a commit's body, as the body carries them: its fields as JSON, as UTF-8
 * (a path's non-ASCII characters 2 to 4 bytes, a control character escaped to 6, `"` and
 * `\` to 2), its file's bytes as base64 or its intent id, and the comma after it.
 */
function opBytes(op: Op, fields: Record<string, unknown>): number {
  const carried = op.op === "remove" ? 0
    : op.inline !== undefined ? `,"inline":""`.length + Math.ceil(op.inline.byteLength / 3) * 4
    : `,"intent_id":""`.length + INTENT_ID_CHARS;
  return Buffer.byteLength(JSON.stringify(fields), "utf8") + carried + 1;
}

/**
 * Whether the sandbox's file at `path` (`stat`, as just read) holds bytes other than `sent`'s,
 * the identity the owner's files hold of it (none: any bytes are others): read again only
 * when its size and change time do not settle it.
 */
async function differs(place: Pick<Place, "local">, path: string, stat: Stats,
                       sent: Identity | undefined): Promise<boolean> {
  if (sent === undefined || Number(stat.size) !== sent.size) {
    return true;
  }
  if (sent.mtime_ms !== RACY && Number(stat.mtimeMs) === sent.mtime_ms) {
    return false;
  }
  const read = await scanned(place.local, localOf(place, path), []);
  return read !== null && read.sha256 !== sent.sha256;
}
