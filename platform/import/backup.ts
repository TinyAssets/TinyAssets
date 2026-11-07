/**
 * Reading a backup copy, and only reading it (`import_research.md` §1, §5).
 *
 * A backup copy is the private work folder the research's §1 commands make, on a disposable
 * host or root-private on the droplet:
 * - `manifest.json`, the newest verified platform manifest (`platform_backup.py`);
 * - its archive, `tinyassets-data-<ts>.tar.gz`, extracted at `data/` (the shared layer,
 *   `data/_data/`);
 * - each pinned owner image, uncompressed, at `<uid>.img`, mounted read-only at `mnt/<uid>`
 *   (`mountArgs`, the line `diagnose_connected_sources.py` uses).
 *
 * Nothing here writes to it. A store is opened read-only and immutable, so SQLite takes no
 * lock and writes no sidecar; a file is opened `O_NOFOLLOW`; a tree is read by `lstat`, so a
 * link is listed and never followed. A name is read as bytes and decoded strictly: one that
 * is not UTF-8 is never guessed at.
 */
import { createHash } from "node:crypto";
import { closeSync, constants, lstatSync, openSync, readFileSync, readSync } from "node:fs";
import { join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { CHUNK_BYTES } from "../contract/src/limits";
import { utf8Text } from "../contract/src/text";

/** One owner image's pin in a manifest: its uid, snapshot and the uncompressed image's hash. */
export interface Pin {
  readonly uid: number;
  readonly snapshot_id: string;
  readonly sha256: string;
}

export interface Manifest {
  readonly version: 1;
  readonly archive: string;
  readonly sha256: string;
  readonly snapshots: readonly Pin[];
}

/** The old stack's owner uids (`owner_identities.py`). */
export const UID_FIRST = 300_001;
export const UID_LAST = 399_999;
/** The image mount's options, verbatim from `diagnose_connected_sources.py`. */
export const MOUNT_OPTIONS = "ro,norecovery,nosuid,nodev,loop,nouuid";

const HEX64 = /^[0-9a-f]{64}$/;
const HEX32 = /^[0-9a-f]{32}$/;
const ARCHIVE = /^tinyassets-data-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz$/;

/** The work folder's layout. */
export function layout(work: string) {
  return Object.freeze({
    manifest: join(work, "manifest.json"),
    shared: join(work, "data", "_data"),
    image: (uid: number) => join(work, `${uid}.img`),
    mount: (uid: number) => join(work, "mnt", String(uid)),
    archive: (name: string) => join(work, name),
  });
}

/** A manifest, held to `platform_backup.validate_manifest`'s shape, or thrown loudly. */
export function manifestOf(text: string): Manifest {
  const doc = JSON.parse(text) as Record<string, unknown>;
  const snapshots = doc["snapshots"];
  if (doc["version"] !== 1 || !Array.isArray(snapshots) || typeof doc["archive"] !== "string"
      || !ARCHIVE.test(doc["archive"]) || typeof doc["sha256"] !== "string"
      || !HEX64.test(doc["sha256"])) {
    throw new Error("the manifest is no version 1 platform manifest");
  }
  const uids = new Set<number>();
  for (const pin of snapshots as Record<string, unknown>[]) {
    const uid = pin["uid"];
    if (typeof uid !== "number" || !Number.isInteger(uid) || uid < UID_FIRST || uid > UID_LAST
        || typeof pin["snapshot_id"] !== "string" || !HEX32.test(pin["snapshot_id"])
        || typeof pin["sha256"] !== "string" || !HEX64.test(pin["sha256"]) || uids.has(uid)) {
      throw new Error("the manifest holds a pin that is no uid, snapshot and image hash");
    }
    uids.add(uid);
  }
  return doc as unknown as Manifest;
}

/** A regular file, opened without following a link; a link or any other kind is refused. */
export function openFile(path: string | Buffer): number {
  return openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
}

/** The SHA-256 of a file's bytes, read in chunks (a 5 GiB image never sits in memory). */
export function fileSha256(path: string | Buffer): string {
  const hash = createHash("sha256");
  const fd = openFile(path);
  try {
    const buffer = Buffer.allocUnsafe(CHUNK_BYTES);
    for (let n = readSync(fd, buffer); n > 0; n = readSync(fd, buffer)) {
      hash.update(buffer.subarray(0, n));
    }
  } finally {
    closeSync(fd);
  }
  return hash.digest("hex");
}

/** SQLite's own header: a file that starts with it is a store. */
const SQLITE_MAGIC = Buffer.from("SQLite format 3\0", "latin1");

/** Whether the regular file at `path` is a SQLite store (an empty file is one with no table). */
export function isStore(path: string | Buffer): boolean {
  const fd = openFile(path);
  try {
    const head = Buffer.alloc(SQLITE_MAGIC.length);
    const n = readSync(fd, head, 0, head.length, 0);
    return n === 0 || (n === head.length && head.equals(SQLITE_MAGIC));
  } finally {
    closeSync(fd);
  }
}

/**
 * A store's URI, read-only and immutable: SQLite takes no lock and reads or writes no
 * sidecar. Both matter: `mode=ro` alone, on a store in WAL mode, makes its `-wal` and
 * `-shm` beside it (node:sqlite 22, measured), and the shared layer is an extracted
 * archive on a writable disk. Immutable reads the main file alone, so a store with an
 * unmerged log is refused before it is opened (`survey.ts`, `unclean_store`). The path is
 * made absolute as written, never resolved through a link.
 */
export function storeUri(path: string | Buffer): string {
  const text = resolve(typeof path === "string" ? path : utf8Text(path, "keep"));
  return `file:${encodeURI(text).replace(/[?#]/g, encodeURIComponent)}?mode=ro&immutable=1`;
}

/** A store opened by `storeUri`, once `lstat` has it a regular file. */
export function openStore(path: string | Buffer): DatabaseSync {
  if (!lstatSync(path).isFile()) {
    throw new Error("a store is a regular file");
  }
  return new DatabaseSync(storeUri(path), { readOnly: true });
}

/** `name` as an SQL identifier. */
export function quoted(name: string): string {
  return `"${name.replaceAll('"', '""')}"`;
}

/** A store's own tables, SQLite's internal ones left out. */
export function tablesOf(db: DatabaseSync): string[] {
  return db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE " +
                    "'sqlite\\_%' ESCAPE '\\' ORDER BY name").all()
    .map((row) => String(row["name"]));
}

export function rowsIn(db: DatabaseSync, table: string): number {
  return Number(db.prepare(`SELECT COUNT(*) AS n FROM ${quoted(table)}`).get()!["n"]);
}

/** Whether the store passes SQLite's own `integrity_check`. */
export function intact(db: DatabaseSync): boolean {
  const rows = db.prepare("PRAGMA integrity_check").all();
  return rows.length === 1 && rows[0]!["integrity_check"] === "ok";
}

/** A name read as bytes, as strict UTF-8, or null when it is not. */
export function nameText(name: Buffer): string | null {
  try {
    return utf8Text(name, "keep");
  } catch {
    return null;
  }
}

/** The bytes of a small file read whole, never through a link. */
export function readSmall(path: string | Buffer): Buffer {
  const fd = openFile(path);
  try {
    return readFileSync(fd);
  } finally {
    closeSync(fd);
  }
}

/** A `/proc/self/mountinfo` field, its octal escapes (`\040` for a space) read back. */
function unescaped(field: string): string {
  return field.replace(/\\([0-7]{3})/g, (_, octal: string) =>
    String.fromCharCode(parseInt(octal, 8)));
}

/**
 * Why `at` is not an XFS image mounted read-only, by the kernel's own word
 * (`/proc/self/mountinfo`, the last mount at that point), or null when it is. Held before
 * an owner's image is read, so the job never reads a live volume or a writable one.
 */
export function mountProblem(mountinfo: string, at: string,
                             backing: (device: string) => string | null = () => null,
                             image: string | null = null): string | null {
  let found: { options: string[]; type: string; source: string } | null = null;
  for (const line of mountinfo.split("\n")) {
    const [left, right] = line.split(" - ");
    const fields = (left ?? "").split(" ");
    if (right === undefined || fields.length < 6 || unescaped(fields[4]!) !== at) {
      continue;
    }
    const [type, source] = right.split(" ");
    found = { options: fields[5]!.split(","), type: type!, source: unescaped(source ?? "") };
  }
  if (found === null) {
    return "is not a mount point";
  }
  if (!found.options.includes("ro")) {
    return "is mounted writable";
  }
  if (found.type !== "xfs") {
    return `is ${found.type}, not an owner's XFS image`;
  }
  if (image !== null && backing(found.source) !== image) {
    return "is not its own image: its loop device's backing file is another";
  }
  return null;
}

/**
 * The file a loop device reads (`/sys/block/loopN/loop/backing_file`), or null when the
 * device is none: what ties a mount to the image whose hash was its pin.
 */
export function loopBacking(device: string): string | null {
  const loop = /^\/dev\/(loop\d+)$/.exec(device);
  if (loop === null) {
    return null;
  }
  try {
    return readFileSync(`/sys/block/${loop[1]}/loop/backing_file`, "utf8").trim();
  } catch {
    return null;
  }
}

/** `mount`'s arguments for one owner image (run as root on the host, never on a live volume). */
export function mountArgs(image: string, at: string): string[] {
  return ["-t", "xfs", "-o", MOUNT_OPTIONS, image, at];
}
