/**
 * File operations that never follow a link: every tree the
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
 *
 * One way of each: one write path (`safeStream`, of which `safeWrite` is the one-piece
 * case), one read path (`openRegular`, under `safeReadHead`, `safeRead` and `safePieces`),
 * and one walker (`walkTree`, of which `walkFiles` is the files alone).
 */
import { randomBytes } from "node:crypto";
import { closeSync, constants, type Dir, fchmodSync, fchownSync, fstatSync, lstatSync, mkdirSync,
         opendirSync, openSync, readSync, renameSync, rmdirSync, rmSync, unlinkSync,
         writeSync } from "node:fs";
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
    let found = kind(at);
    if (found === null) {
      try {
        mkdirSync(at, { mode });
      } catch (error) {
        // Made by another process between the look and the make (two runs of one sandbox
        // starting at once): a real directory is what was asked for, and like any directory
        // found it keeps its maker's mode and owner; anything else is refused below.
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
          throw error;
        }
        found = kind(at);
      }
    }
    if (found === null) {
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
 * A regular file written a piece at a time at `path` (under `base`), with no link followed
 * on its path: its directories made as `safeDirs` makes them, a fresh file created under a
 * random name beside it (`O_EXCL | O_NOFOLLOW`), each piece appended as it comes, then its
 * mode and owner set on the descriptor and renamed over whatever is there (a link at the
 * name is replaced, never written through). What it holds at once is the piece in hand,
 * whatever the file's size; a failure, or `abort`, removes what was written.
 */
export function safeStream(base: string, path: string, mode: number,
                           owner?: { uid: number; gid: number }, dirMode = 0o700):
    { append(piece: Uint8Array): void; commit(): void; abort(): void } {
  const segments = below(base, path);
  const parent = segments.length === 1 ? base : join(base, ...segments.slice(0, -1));
  safeDirs(base, parent, dirMode, owner);
  const temp = join(parent, `.ta-${randomBytes(12).toString("hex")}`);
  const fd = openSync(temp, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL
                      | constants.O_NOFOLLOW, mode);
  let open = true;
  const close = () => {
    if (open) {
      open = false;
      closeSync(fd);
    }
  };
  const failed = (error: unknown): never => {
    close();
    unlinkSync(temp);
    throw error;
  };
  return {
    append(piece) {
      try {
        for (let at = 0; at < piece.byteLength;) {
          at += writeSync(fd, piece, at, piece.byteLength - at);
        }
      } catch (error) {
        failed(error);
      }
    },
    commit() {
      try {
        fchmodSync(fd, mode);
        if (owner !== undefined) {
          fchownSync(fd, owner.uid, owner.gid);
        }
      } catch (error) {
        failed(error);
      }
      close();
      if (kind(path) === "dir") {
        unlinkSync(temp);
        throw new LinkRefused(`${path} is a directory`);
      }
      renameSync(temp, path);
    },
    abort() {
      close();
      rmSync(temp, { force: true });
    },
  };
}

/** `bytes` written whole at `path` (under `base`): `safeStream`'s file, in one piece. */
export function safeWrite(base: string, path: string, bytes: Uint8Array, mode: number,
                          owner?: { uid: number; gid: number }, dirMode = 0o700): void {
  const out = safeStream(base, path, mode, owner, dirMode);
  out.append(bytes);
  out.commit();
}

/**
 * A regular file at `path` (under `base`) opened for reading with no link followed on its
 * path (each directory on it `lstat`ed, the file itself `O_NOFOLLOW`): its descriptor, or
 * null when it is not a regular file this user may read.
 */
function openRegular(base: string, path: string): number | null {
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
  let regular = false;
  try {
    regular = fstatSync(fd).isFile();
  } finally {
    if (!regular) {
      closeSync(fd);
    }
  }
  return regular ? fd : null;
}

/** A regular file's bytes, read with no link followed on its path; null when not one. */
export function safeRead(base: string, path: string): Buffer | null {
  return safeReadHead(base, path, Number.POSITIVE_INFINITY)?.bytes ?? null;
}

/**
 * A regular file's first `max` bytes at most, and its whole size, read with no link
 * followed on its path; null when not one.
 */
export function safeReadHead(base: string, path: string, max: number):
    { bytes: Buffer; size: number } | null {
  const fd = openRegular(base, path);
  if (fd === null) {
    return null;
  }
  try {
    const size = fstatSync(fd).size;
    const want = Math.min(size, max);
    const out = Buffer.alloc(want);
    for (let read = 0; read < want;) {
      const got = readSync(fd, out, read, want - read, read);
      if (got === 0) {
        return { bytes: out.subarray(0, read), size };
      }
      read += got;
    }
    return { bytes: out, size };
  } finally {
    closeSync(fd);
  }
}

/**
 * A regular file's bytes a piece at a time (`pieceBytes` each, the last shorter), read with
 * no link followed on its path, so a large file never sits whole in memory: `each` is
 * awaited with every piece in order, on one descriptor (an empty file is one empty piece).
 * False when it is not a regular file this user may read.
 */
export async function safePieces(base: string, path: string, pieceBytes: number,
                                 each: (piece: Buffer, index: number) => void | Promise<void>):
    Promise<boolean> {
  const fd = openRegular(base, path);
  if (fd === null) {
    return false;
  }
  try {
    const piece = Buffer.alloc(pieceBytes);
    for (let index = 0, offset = 0; ; index += 1) {
      let filled = 0;
      while (filled < pieceBytes) {
        const got = readSync(fd, piece, filled, pieceBytes - filled, offset + filled);
        if (got === 0) {
          break;
        }
        filled += got;
      }
      if (filled === 0 && index > 0) {
        return true;
      }
      await each(Buffer.from(piece.subarray(0, filled)), index);
      offset += filled;
      if (filled < pieceBytes) {
        return true;
      }
    }
  } finally {
    closeSync(fd);
  }
}

/** How a walk goes: what it skips, what it is told of a name that is not UTF-8, its order. */
export interface Walk {
  /** An entry kept out (a directory with its whole tree), by its path from the walk's top. */
  readonly skip?: (rel: string, isDir: boolean) => boolean;
  /** A name that is not UTF-8, which no path names: told, its bytes escaped. */
  readonly unnamed?: (rel: string) => void;
  /**
   * A directory this user may not list, by its path from the walk's top: told before its
   * `left`, which then follows with nothing met under it, though it may hold anything.
   */
  readonly unlisted?: (rel: string) => void;
  /**
   * Each directory's entries in their names' byte order, that directory read whole and
   * closed before any is offered, so the tree may change as it is walked; otherwise as the
   * directory lists them, one entry at a time.
   */
  readonly sorted?: boolean;
}

/**
 * An entry a walk meets (`walkTree`), by its path (`at`) and its path from the walk's top
 * (`rel`): a regular file; a directory once everything under it was met (`left`); or one the
 * walk passes over (`passed`): kept out (`skip`, its whole tree with it), a link, or anything
 * else neither a file nor a directory. A name that is not UTF-8 has no path from the top a
 * string names (`unnamed`, told to the walk's `unnamed`), so its path is its bytes.
 */
export type Met =
  | { readonly kind: "file" | "left" | "passed"; readonly rel: string; readonly at: string }
  | { readonly kind: "unnamed"; readonly at: Buffer };

/**
 * Every entry under `dir`, by `lstat` alone (links never entered), a directory met after
 * everything under it. What the walk holds is one directory a level of depth, never the
 * tree's paths, however many there are: unsorted, one open directory read entry by entry
 * (`opendir`).
 */
export function* walkTree(dir: string, walk: Walk = {}, base = dir): Generator<Met> {
  if (kind(dir) !== "dir") {
    return;
  }
  for (const raw of entries(dir, walk.sorted === true,
                             () => walk.unlisted?.(relative(base, dir)))) {
    // Read by its bytes: a name that is not UTF-8 has no path a string can name, so it is
    // told (`unnamed`, its bytes escaped), never silently missed.
    const name = raw.toString("utf8");
    if (!Buffer.from(name, "utf8").equals(raw)) {
      walk.unnamed?.(join(relative(base, dir), escapedName(raw)));
      yield { kind: "unnamed", at: Buffer.concat([Buffer.from(`${dir}${sep}`), raw]) };
      continue;
    }
    const at = join(dir, name);
    const rel = relative(base, at);
    const found = kind(at);
    if (found === null) {
      continue;  // gone since it was listed
    }
    if (walk.skip?.(rel, found === "dir") === true) {
      yield { kind: "passed", rel, at };
    } else if (found === "dir") {
      yield* walkTree(at, walk, base);
      yield { kind: "left", rel, at };
    } else {
      yield { kind: found === "file" ? "file" : "passed", rel, at };
    }
  }
}

/** Every regular file under `dir`, by path from it: `walkTree`'s files. */
export function* walkFiles(dir: string, walk: Walk = {}): Generator<string> {
  for (const met of walkTree(dir, walk)) {
    if (met.kind === "file") {
      yield met.rel;
    }
  }
}

/**
 * A directory's entries' names, as bytes: read whole and sorted, or one at a time; none, told
 * (`unlisted`), where this user may not list it.
 */
function* entries(dir: string, sorted: boolean, unlisted: () => void): Generator<Buffer> {
  let handle: Dir;
  try {
    // Buffer names (Node takes `buffer` here, though its types say only text encodings), read
    // one at a time: Node reads a batch otherwise (32), each name a Buffer of its own, and the
    // walk holds an open directory a folder deep, so each folder of a deep path would hold up
    // to 31 names more (15.5 KB a folder at 250-byte names, Node 22.22.0, 2026-11-22).
    handle = opendirSync(dir, { encoding: "buffer" as BufferEncoding, bufferSize: 1 });
  } catch {
    unlisted();
    return;  // one this user may not list: its contents are not ours to walk
  }
  const all: Buffer[] = [];
  try {
    for (let entry = handle.readSync(); entry !== null; entry = handle.readSync()) {
      const raw = entry.name as unknown as Buffer;
      if (sorted) {
        all.push(raw);
      } else {
        yield raw;
      }
    }
  } finally {
    handle.closeSync();
  }
  yield* all.sort(Buffer.compare);
}

/**
 * A name's bytes as text a person reads: printable ASCII as itself (a backslash doubled),
 * every other byte `\xNN`.
 */
function escapedName(raw: Buffer): string {
  let out = "";
  for (const b of raw) {
    out += b === 92 ? "\\\\" : b >= 32 && b < 127 ? String.fromCharCode(b)
      : `\\x${b.toString(16).padStart(2, "0")}`;
  }
  return out;
}

/** A tree removed, links removed as links (Node's `rm` never follows one). */
export function remove(path: string | Buffer): void {
  rmSync(path, { recursive: true, force: true });
}

/**
 * A directory removed only while it holds nothing: whether it is gone. `rmdir` refuses one
 * holding an entry (EEXIST or ENOTEMPTY, POSIX) and anything there not a directory, a link
 * among them (ENOTDIR), so an entry made in it up to its removal keeps it.
 */
export function removeEmpty(path: string): boolean {
  try {
    rmdirSync(path);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "EEXIST" || code === "ENOTEMPTY" || code === "ENOTDIR") {
      return false;
    }
    if (code !== "ENOENT") {
      throw error;
    }
  }
  return true;
}
