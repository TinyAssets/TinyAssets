/**
 * The runner's sync agent (`engines-replan.md` §2.1 steps 4 and 7): the owner's files into
 * the sandbox's `/workspace` before a run, and the run's changes back out after it, through
 * the sync doors on the egress host by the lease's sync token (`doors/src/egress.ts`), the
 * object fencing each commit by the lease's id and epoch.
 *
 * - **In:** the change feed from the mirror's cursor, each path's last change applied; a
 *   reset (history the cursor needed was pruned, or a client sandbox's readable set moved)
 *   reads the whole tree again by listing. A file's bytes come inline or chunk by chunk.
 * - **Out:** every regular file under `/workspace` whose bytes differ from what was last
 *   synced, written on its base version (a conflict keeps both sides, the object's rule),
 *   and every synced file now gone, removed. Symbolic links are never synced (the runner's
 *   projections are links). A client's sandbox never offers a path in the owner's own
 *   trees (`OWNER_TREES`: the object refuses them anyway). A file holding a secret's bytes
 *   (`guards.ts`), or one past one chunk (4 MiB: a bigger file waits for the upload doors,
 *   owed by tasks 6.1), is refused here and reported, never sent.
 */
import { createHash } from "node:crypto";
import { chmodSync, chownSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync,
         renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";

import { heldSecret } from "./guards";

/** The object's sync bounds: ops a commit, a file's inline bytes, a door's body. */
export const COMMIT_OPS = 1000;
export const INLINE_MAX = 4 * 1024 * 1024;
const BODY_MAX = 8 * 1024 * 1024 - 64 * 1024;
const FEED_LIMIT = 500;
/** The owner's trees a client's sandbox never writes (`owner/src/paths.ts`). */
export const OWNER_TREES = Object.freeze(["/workspace/agents", "/workspace/.agents",
                                          "/workspace/skills", "/workspace/prompts"]);

export class SyncFenced extends Error {
  override readonly name = "SyncFenced";
}

/** The sync doors, by the sync token. */
export class SyncClient {
  constructor(readonly origin: string, readonly token: string,
              readonly fetcher: typeof fetch = fetch) {}

  async post(door: string, body: unknown): Promise<Record<string, unknown>> {
    for (let attempt = 0; ; attempt += 1) {
      let response: Response;
      try {
        response = await this.fetcher(`${this.origin}/sync/${door}`, {
          method: "POST", body: JSON.stringify(body),
          headers: { "content-type": "application/json", "x-ta-sync": this.token } });
      } catch (error) {
        if (attempt >= 5) {
          throw error;
        }
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
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
      if ((response.status === 503 || response.status === 429) && attempt < 5) {
        await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
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

export function writeMirror(path: string, mirror: Mirror): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  writeFileSync(`${path}.tmp`, JSON.stringify(mirror), { mode: 0o600 });
  renameSync(`${path}.tmp`, path);
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Where the sandbox keeps the owner's files: the workspace path under the runner's root. */
export interface Place {
  /** The local directory standing for `/workspace` (the root's own in the sandbox). */
  readonly local: string;
  /** The group every engine's user is in, which the synced files are shared with. */
  readonly gid: number | null;
  /** A client's sandbox: the owner's trees read-only, never written back. */
  readonly client: boolean;
}

function localOf(place: Place, path: string): string {
  return join(place.local, relative("/workspace", path));
}

function inOwnerTrees(path: string): boolean {
  return OWNER_TREES.some((tree) => path === tree || path.startsWith(`${tree}/`));
}

/** A synced file written whole (a temporary file renamed over it), shared with the group. */
function put(place: Place, path: string, bytes: Uint8Array, mode: number): void {
  const local = localOf(place, path);
  mkdirSync(dirname(local), { recursive: true, mode: 0o2775 });
  const readOnly = place.client && inOwnerTrees(path);
  writeFileSync(`${local}.ta-sync`, bytes);
  chmodSync(`${local}.ta-sync`, readOnly ? (mode === 1 ? 0o555 : 0o444)
    : (mode === 1 ? 0o775 : 0o664));
  if (place.gid !== null) {
    chownSync(`${local}.ta-sync`, 0, place.gid);
  }
  renameSync(`${local}.ta-sync`, local);
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
  const local = localOf(place, path);
  if (kind === null) {
    rmSync(local, { recursive: true, force: true });
    for (const known of Object.keys(mirror.files)) {
      if (known === path || known.startsWith(`${path}/`)) {
        delete mirror.files[known];
      }
    }
    return;
  }
  if (kind === "dir") {
    mkdirSync(local, { recursive: true, mode: 0o2775 });
    return;
  }
  const got = await fetchBytes(client, path);
  if (got === null) {
    return;  // gone since the feed was read: a later change says so
  }
  put(place, path, got.bytes, got.mode);
  mirror.files[path] = { version: got.version, size: got.bytes.byteLength,
                         mtime_ms: lstatSync(local).mtimeMs, sha256: sha256(got.bytes) };
}

/** The whole readable tree, by listing, for a reset. */
async function listAll(client: SyncClient, dir: string,
                       out: Map<string, string>): Promise<void> {
  let after: string | null = null;
  for (;;) {
    const page = await client.post("list", { dir, limit: 500,
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
  mkdirSync(place.local, { recursive: true, mode: 0o2775 });
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

/** Every regular file under `dir`, by path from it; links and other kinds skipped. */
export function walk(dir: string, out: string[] = [], base = dir): string[] {
  if (!existsSync(dir)) {
    return out;
  }
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(path, out, base);
    } else if (entry.isFile() && !entry.name.endsWith(".ta-sync")) {
      out.push(relative(base, path));
    }
  }
  return out;
}

export interface SyncReport {
  readonly committed: number;
  readonly conflicts: readonly string[];
  readonly refused: readonly { readonly path: string; readonly reason: string }[];
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
  const ops: { op: string; path: string; base: number | null; mode?: number;
               inline?: string; size: number }[] = [];
  const present = new Set<string>();
  for (const rel of walk(place.local)) {
    const path = `/workspace/${rel.split("\\").join("/")}`;
    present.add(path);
    if (place.client && inOwnerTrees(path)) {
      continue;
    }
    const local = localOf(place, path);
    const stat = lstatSync(local);
    const known = mirror.files[path];
    if (known !== undefined && known.size === stat.size && known.mtime_ms === stat.mtimeMs) {
      continue;
    }
    const bytes = readFileSync(local);
    const digest = sha256(bytes);
    if (known !== undefined && known.sha256 === digest) {
      known.mtime_ms = stat.mtimeMs;
      continue;
    }
    if (bytes.byteLength > INLINE_MAX) {
      refused.push({ path, reason: "over one chunk: the upload doors are owed" });
      continue;
    }
    const secret = heldSecret(bytes.toString("latin1"), atoms)
      ?? heldSecret(bytes.toString("utf8"), atoms);
    if (secret !== null) {
      refused.push({ path, reason: "holds a secret's bytes" });
      continue;
    }
    ops.push({ op: "write", path, base: known?.version ?? null,
               mode: (stat.mode & 0o111) !== 0 ? 1 : 0, inline: bytes.toString("base64"),
               size: bytes.byteLength });
  }
  for (const [path, known] of Object.entries(mirror.files)) {
    if (!present.has(path) && !(place.client && inOwnerTrees(path))) {
      ops.push({ op: "remove", path, base: known.version, size: 0 });
    }
  }
  let committed = 0;
  let batch: typeof ops = [];
  let bytes = 0;
  const send = async () => {
    if (batch.length === 0) {
      return;
    }
    const answer = await client.post("commit", {
      lease_id: lease.lease_id, epoch: lease.epoch,
      ops: batch.map(({ size: _size, ...op }) => op) });
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
          const local = localOf(place, outcome.path);
          const stat = lstatSync(local);
          mirror.files[outcome.path] = { version: outcome.entry.version, size: stat.size,
                                         mtime_ms: stat.mtimeMs,
                                         sha256: sha256(readFileSync(local)) };
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
    const size = (op.inline?.length ?? 0) + op.path.length + 64;
    if (batch.length >= COMMIT_OPS || bytes + size > BODY_MAX) {
      await send();
    }
    batch.push(op);
    bytes += size;
  }
  await send();
  return { committed, conflicts, refused };
}
