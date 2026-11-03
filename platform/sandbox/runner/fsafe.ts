/**
 * File operations that never follow a link (slice S review H-1, H-2): every tree the
 * runner or its sync agent writes into may hold an entry another user planted, so no
 * operation here resolves a symbolic link anywhere on its path. A path is walked from a
 * trusted base one segment at a time by `lstat`: a link, or anything but a directory where
 * a directory is needed, refuses the operation (`LinkRefused`). A file is opened with
 * `O_NOFOLLOW` (and created with `O_EXCL` under a random name, then renamed into place), and
 * its mode and owner are set on the open descriptor. Reads open with `O_NOFOLLOW` and take
 * regular files only. A race between the walk and the open needs a writer in the tree while
 * it runs: the runner works only in trees no agent's process can write (each run's own uid
 * is stopped before its home is read), and the sync agent's own uid can reach no file
 * outside the workspace a planted link could name.
 */
import { randomBytes } from "node:crypto";
import { closeSync, constants, fchmodSync, fchownSync, fstatSync, lstatSync, mkdirSync,
         openSync, readdirSync, readSync, renameSync, rmSync, unlinkSync, writeSync } from "node:fs";
import { join, relative, sep } from "node:path";

export class LinkRefused extends Error {
  override readonly name = "LinkRefused";
}

function kind(path: string): "dir" | "file" | "link" | "other" | null {
  try {
    const stat = lstatSync(path);
    return stat.isSymbolicLink() ? "link" : stat.isDirectory() ? "dir"
      : stat.isFile() ? "file" : "other";
  } catch {
    return null;
  }
}

/** The segments of `path` below `base`, or a refusal when it is not under it. */
function below(base: string, path: string): string[] {
  const rel = relative(base, path);
  if (rel === "" || rel.startsWith("..") || rel.split(sep).includes("..")) {
    throw new LinkRefused(`${path} is not under ${base}`);
  }
  return rel.split(sep);
}

/**
 * `dir` (under `base`) made a real directory at every level, none a link; each made one
 * gets `mode` and, given, `owner`. The base itself is trusted.
 */
export function safeDirs(base: string, dir: string, mode: number,
                         owner?: { uid: number; gid: number }): void {
  if (dir === base) {
    return;
  }
  let at = base;
  for (const segment of below(base, dir)) {
    at = join(at, segment);
    const found = kind(at);
    if (found === null) {
      mkdirSync(at, { mode });
      // The new directory is a real one this process made: its mode and owner set on it
      // by an open descriptor (`O_DIRECTORY | O_NOFOLLOW`).
      const fd = openSync(at, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      try {
        fchmodSync(fd, mode);
        if (owner !== undefined) {
          fchownSync(fd, owner.uid, owner.gid);
        }
      } finally {
        closeSync(fd);
      }
    } else if (found !== "dir") {
      throw new LinkRefused(`${at} is not a directory (${found})`);
    }
  }
}

/**
 * `bytes` written whole at `path` (under `base`): its directories made as `safeDirs` makes
 * them, a fresh file created `O_EXCL | O_NOFOLLOW` beside it, its mode and owner set on the
 * descriptor, then renamed over whatever is there (a link at the name is replaced, never
 * written through).
 */
export function safeWrite(base: string, path: string, bytes: Uint8Array, mode: number,
                          owner?: { uid: number; gid: number }, dirMode = 0o700): void {
  const segments = below(base, path);
  const parent = segments.length === 1 ? base : join(base, ...segments.slice(0, -1));
  safeDirs(base, parent, dirMode, owner);
  const temp = join(parent, `.ta-${randomBytes(12).toString("hex")}`);
  const fd = openSync(temp, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL
                      | constants.O_NOFOLLOW, mode);
  try {
    for (let at = 0; at < bytes.byteLength;) {
      at += writeSync(fd, bytes, at, bytes.byteLength - at);
    }
    fchmodSync(fd, mode);
    if (owner !== undefined) {
      fchownSync(fd, owner.uid, owner.gid);
    }
  } catch (error) {
    closeSync(fd);
    unlinkSync(temp);
    throw error;
  }
  closeSync(fd);
  if (kind(path) === "dir") {
    unlinkSync(temp);
    throw new LinkRefused(`${path} is a directory`);
  }
  renameSync(temp, path);
}

/** A regular file's bytes, read with no link followed on its path; null when not one. */
export function safeRead(base: string, path: string): Buffer | null {
  const segments = below(base, path);
  let at = base;
  for (const segment of segments.slice(0, -1)) {
    at = join(at, segment);
    if (kind(at) !== "dir") {
      return null;
    }
  }
  let fd: number;
  try {
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  } catch {
    return null;  // a link (ELOOP), a missing file, or one this user may not read
  }
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile()) {
      return null;
    }
    const out = Buffer.alloc(stat.size);
    for (let read = 0; read < stat.size;) {
      const got = readSync(fd, out, read, stat.size - read, read);
      if (got === 0) {
        return out.subarray(0, read);
      }
      read += got;
    }
    return out;
  } finally {
    closeSync(fd);
  }
}

/** Every regular file under `dir`, by path from it, by `lstat` alone: links never entered. */
export function safeWalk(dir: string, skip: (rel: string) => boolean = () => false,
                         out: string[] = [], base = dir): string[] {
  if (kind(dir) !== "dir") {
    return out;
  }
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;  // one this user may not list: its contents are not ours to sync
  }
  for (const name of entries) {
    const path = join(dir, name);
    const rel = relative(base, path);
    if (skip(rel)) {
      continue;
    }
    const found = kind(path);
    if (found === "dir") {
      safeWalk(path, skip, out, base);
    } else if (found === "file") {
      out.push(rel);
    }
  }
  return out;
}

/** A tree removed, links removed as links (Node's `rm` never follows one). */
export function remove(path: string): void {
  rmSync(path, { recursive: true, force: true });
}
