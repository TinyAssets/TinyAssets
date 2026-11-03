/**
 * The runner's sync agent (`engines-replan.md` §2.1 steps 4 and 7), run as its own
 * unprivileged user (`ta-sync`, in group `ta`), never root (slice S review H-2): the owner's
 * files into the sandbox's `/workspace` before a run, and the run's changes back out after
 * it, through the sync doors on the egress host by the lease's sync token (`x-ta-sync`,
 * beside the egress token E2B's transform sets, so the token is good from inside the VM
 * alone), the object fencing each commit by the lease's id and epoch.
 *
 * - **In:** the change feed from the mirror's cursor, each path's last change applied with
 *   no link followed (`fsafe.ts`); a reset reads the whole tree again by listing. A file's
 *   bytes come inline or chunk by chunk. The engines' sessions
 *   (`/workspace/.sessions/<authority>/<engine>/...`) are never written into the shared
 *   workspace, where every engine's user could reach them (review H-3): only a path under
 *   that engine's own `session_paths` is taken, into the sync agent's own directory, for
 *   the root runner to put in that engine's store.
 * - **Out:** every regular file under `/workspace` whose bytes differ from what was last
 *   synced (links never; the `.sessions` tree never, from the workspace), and each engine's
 *   sessions the runner staged; a file within one chunk (4 MiB) inline, a larger one through
 *   the files plane's upload doors chunk by chunk. A client's sandbox never offers a path in
 *   the owner's own trees. A file holding a secret's bytes (`guards.ts`) is refused here and
 *   reported, never sent.
 */
import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import type { SourceRecord } from "../../doors/src/sources";

import { isKept, keptPaths } from "./engine";
import { remove, safeDirs, safeRead, safeWalk, safeWrite } from "./fsafe";
import { heldSecret } from "./guards";

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

export class SyncFenced extends Error {
  override readonly name = "SyncFenced";
}

/**
 * The sync doors. A transient failure (the network, `429`, `503`) is tried again, its wait
 * doubling to at most `ceilingMs` (the runner's heartbeat, the cadence the lease is renewed
 * at), with no count: the run's own cancel ends it. Any other answer fails at once.
 */
/**
 * The sync doors' client: a call refused for the moment (the network, `429`, `503`) is
 * asked again with no count, its wait doubling from `firstMs` (the runner's flush cadence:
 * no sooner than its next batch of frames would go) to `ceilingMs` (a heartbeat: the run's
 * report is never held longer than one).
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
      if (response.status === 401 || response.status === 403) {
        throw new SyncFenced(`the sync door refused (${response.status}): ${text.slice(0, 200)}`);
      }
      if (response.status === 404 && door === "read") {
        return { missing: true };
      }
      if (response.status === 503 || response.status === 429) {
        await new Promise((resolve) => setTimeout(resolve, wait));
        continue;
      }
      throw new Error(`the sync door ${door} answered ${response.status}: ${text.slice(0, 200)}`);
    }
  }
}

/** What the sandbox last synced: the feed's cursor, and each file's version and bytes. */
export interface Mirror {
  cursor: number;
  files: Record<string, { version: number; size: number; mtime_ms: number; sha256: string }>;
}

export function readMirror(path: string): Mirror {
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) as Mirror
    : { cursor: 0, files: {} };
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

function localOf(place: Place, path: string): string {
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

async function fetchBytes(client: SyncClient, path: string):
    Promise<{ bytes: Uint8Array; version: number; mode: number } | null> {
  const read = await client.post("read", { path });
  if (read["missing"] === true) {
    return null;
  }
  const entry = read["entry"] as { version: number; mode: number; kind: string };
  if (entry.kind !== "file") {
    return null;
  }
  if (typeof read["inline"] === "string") {
    return { bytes: new Uint8Array(Buffer.from(read["inline"], "base64")),
             version: entry.version, mode: entry.mode };
  }
  const chunks = (read["chunks"] ?? []) as { sha256: string; size: number }[];
  const parts: Uint8Array[] = [];
  for (let first = 0; first < chunks.length; first += 2) {
    const got = await client.post("chunks", { path, version: entry.version, first,
                                               count: Math.min(2, chunks.length - first) });
    for (const part of got["chunks"] as string[]) {
      parts.push(new Uint8Array(Buffer.from(part, "base64")));
    }
  }
  return { bytes: new Uint8Array(Buffer.concat(parts)), version: entry.version,
           mode: entry.mode };
}

/** One path's state after the feed: gone, a directory, or a file at a version. */
async function apply(client: SyncClient, place: Place, mirror: Mirror, path: string,
                     kind: string | null): Promise<void> {
  if (path === SESSIONS || path.startsWith(`${SESSIONS}/`)) {
    const session = sessionOf(place, path);
    if (session === null || kind !== "file") {
      return;
    }
    const got = await fetchBytes(client, path);
    if (got !== null) {
      safeWrite(place.sessionsIn, join(place.sessionsIn, session.engine, session.rel),
                got.bytes, 0o600);
      mirror.files[path] = { version: got.version, size: got.bytes.byteLength, mtime_ms: 0,
                             sha256: sha256(got.bytes) };
    }
    return;
  }
  const local = localOf(place, path);
  const readOnly = place.authority !== "owner" && inOwnerTrees(path);
  if (kind === null) {
    remove(local);
    for (const known of Object.keys(mirror.files)) {
      if (known === path || known.startsWith(`${path}/`)) {
        delete mirror.files[known];
      }
    }
    return;
  }
  if (kind === "dir") {
    safeDirs(place.local, local, readOnly ? 0o2755 : 0o2775);
    return;
  }
  const got = await fetchBytes(client, path);
  if (got === null) {
    return;  // gone since the feed was read: a later change says so
  }
  const dirMode = readOnly ? 0o2755 : 0o2775;
  const mode = readOnly ? (got.mode === 1 ? 0o555 : 0o444) : (got.mode === 1 ? 0o775 : 0o664);
  safeWrite(place.local, local, got.bytes, mode, undefined, dirMode);
  mirror.files[path] = { version: got.version, size: got.bytes.byteLength,
                         mtime_ms: lstatSync(local).mtimeMs, sha256: sha256(got.bytes) };
}

/** The whole readable tree, by listing, for a reset. */
async function listAll(client: SyncClient, dir: string,
                       out: Map<string, string>): Promise<void> {
  let after: string | null = null;
  for (;;) {
    const page = await client.post("list", { dir, limit: FEED_LIMIT,
                                             ...(after === null ? {} : { after }) });
    const entries = page["entries"] as { path: string; kind: string }[];
    for (const entry of entries) {
      out.set(entry.path, entry.kind);
      if (entry.kind === "dir") {
        await listAll(client, entry.path, out);
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
    const page = await client.post("changes", { after: mirror.cursor, limit: FEED_LIMIT });
    const changes = page["changes"] as { path: string; kind: string | null; version: number }[];
    if (page["reset"] === true) {
      const tree = new Map<string, string>();
      await listAll(client, "/workspace", tree);
      for (const known of Object.keys(mirror.files)) {
        if (tree.get(known) !== "file") {
          await apply(client, place, mirror, known, null);
        }
      }
      for (const [path, kind] of tree) {
        await apply(client, place, mirror, path, kind);
      }
      mirror.cursor = page["cursor"] as number;
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
    mirror.cursor = page["cursor"] as number;
    if (changes.length < FEED_LIMIT) {
      return;
    }
  }
}

export interface SyncReport {
  readonly committed: number;
  readonly conflicts: readonly string[];
  readonly refused: readonly { readonly path: string; readonly reason: string }[];
}

interface Op {
  readonly op: "write" | "remove";
  readonly path: string;
  readonly base: number | null;
  readonly mode?: number;
  readonly bytes: Uint8Array | null;
}

/** A file past an inline write, uploaded through the files plane: its intent's id. */
async function upload(client: SyncClient, bytes: Uint8Array,
                      lease: { lease_id: string; epoch: number }): Promise<string> {
  const chunks: Uint8Array[] = [];
  for (let at = 0; at < bytes.byteLength; at += CHUNK_BYTES) {
    chunks.push(bytes.subarray(at, Math.min(at + CHUNK_BYTES, bytes.byteLength)));
  }
  const hashes = chunks.map(sha256);
  const begun = await client.post("upload_begin", { lease_id: lease.lease_id,
                                                    epoch: lease.epoch, size: bytes.byteLength,
                                                    chunks: hashes });
  const missing = new Set(begun["missing"] as string[]);
  for (let i = 0; i < chunks.length; i += 1) {
    if (missing.has(hashes[i]!)) {
      await client.post("upload_chunks", { lease_id: lease.lease_id, epoch: lease.epoch,
                                           intent_id: begun["intent_id"], chunks: [
        { sha256: hashes[i], bytes: Buffer.from(chunks[i]!).toString("base64") }] });
      missing.delete(hashes[i]!);
    }
  }
  return begun["intent_id"] as string;
}

/**
 * The sandbox's changes back to the owner's files, as one or more fenced commits on the
 * lease; `atoms` the secrets' atoms (`guards.ts`) no synced file may hold.
 */
export async function syncOut(client: SyncClient, place: Place, mirror: Mirror,
                              lease: { lease_id: string; epoch: number },
                              atoms: readonly string[]): Promise<SyncReport> {
  const refused: { path: string; reason: string }[] = [];
  const conflicts: string[] = [];
  const ops: Op[] = [];
  const present = new Set<string>();
  const where = new Map<string, string>();
  const offer = (path: string, base: string, local: string) => {
    where.set(path, local);
    const known = mirror.files[path];
    const stat = lstatSync(local);
    if (known !== undefined && known.size === stat.size && known.mtime_ms === stat.mtimeMs) {
      return;
    }
    const bytes = safeRead(base, local);
    if (bytes === null) {
      refused.push({ path, reason: "not a regular file the sync may read" });
      return;
    }
    const digest = sha256(bytes);
    if (known !== undefined && known.sha256 === digest) {
      known.mtime_ms = stat.mtimeMs;
      return;
    }
    if (heldSecret(bytes.toString("latin1"), atoms) !== null
        || heldSecret(bytes.toString("utf8"), atoms) !== null) {
      refused.push({ path, reason: "holds a secret's bytes" });
      return;
    }
    ops.push({ op: "write", path, base: known?.version ?? null,
               mode: (stat.mode & 0o111) !== 0 ? 1 : 0, bytes });
  };
  for (const rel of safeWalk(place.local, (each) => each === ".sessions"
                             || each.split("/").at(-1)!.startsWith(".ta-"))) {
    const path = `/workspace/${rel.split("\\").join("/")}`;
    present.add(path);
    if (place.authority !== "owner" && inOwnerTrees(path)) {
      continue;
    }
    offer(path, place.local, localOf(place, path));
  }
  for (const rel of safeWalk(place.sessionsOut)) {
    const [engine, ...rest] = rel.split("/");
    const path = `${SESSIONS}/${place.authority}/${engine}/${rest.join("/")}`;
    if (sessionOf(place, path) !== null) {
      offer(path, place.sessionsOut, join(place.sessionsOut, rel));
    }
  }
  for (const [path, known] of Object.entries(mirror.files)) {
    if (!path.startsWith(`${SESSIONS}/`) && !present.has(path)
        && !(place.authority !== "owner" && inOwnerTrees(path))) {
      ops.push({ op: "remove", path, base: known.version, bytes: null });
    }
  }
  let committed = 0;
  let batch: Op[] = [];
  let bytes = 0;
  const send = async () => {
    if (batch.length === 0) {
      return;
    }
    const sent: Record<string, unknown>[] = [];
    for (const op of batch) {
      const { bytes: content, ...rest } = op;
      if (content === null) {
        sent.push(rest);
      } else if (content.byteLength <= INLINE_BYTES) {
        sent.push({ ...rest, inline: Buffer.from(content).toString("base64") });
      } else {
        sent.push({ ...rest, intent_id: await upload(client, content, lease) });
      }
    }
    const answer = await client.post("commit", { lease_id: lease.lease_id, epoch: lease.epoch,
                                                  ops: sent });
    const outcomes = answer["outcomes"] as { path: string; outcome: string;
                                             entry?: { version: number } | null;
                                             reason?: string }[];
    for (const outcome of outcomes) {
      const op = batch.find((each) => each.path === outcome.path);
      if (outcome.outcome === "committed") {
        committed += 1;
        if (op?.op === "remove" || outcome.entry === null || outcome.entry === undefined) {
          delete mirror.files[outcome.path];
        } else {
          const stat = lstatSync(where.get(outcome.path)!);
          mirror.files[outcome.path] = { version: outcome.entry.version, size: stat.size,
                                         mtime_ms: stat.mtimeMs, sha256: sha256(op!.bytes!) };
        }
      } else if (outcome.outcome === "conflict") {
        conflicts.push(outcome.path);
      } else {
        refused.push({ path: outcome.path, reason: outcome.reason ?? "refused" });
      }
    }
    batch = [];
    bytes = 0;
  };
  for (const op of ops) {
    const size = op.bytes === null || op.bytes.byteLength > INLINE_BYTES ? op.path.length + 128
      : Math.ceil(op.bytes.byteLength / 3) * 4 + op.path.length + 128;
    if (batch.length >= COMMIT_OPS || bytes + size > BODY_MAX) {
      await send();
    }
    batch.push(op);
    bytes += size;
  }
  await send();
  return { committed, conflicts, refused };
}
