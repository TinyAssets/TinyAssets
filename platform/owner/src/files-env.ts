/**
 * The owner's pi `ExecutionEnv` (pi-durable 1.4.0) over the file manifest (ADR-032 §4,
 * plan §6): the one environment the agent loop's tools use, built per tool call.
 *
 * Its file methods are manifest operations in the owner's own object (`files.ts`), with
 * chunk bytes from the files Worker; none wakes a sandbox, so files stay readable and
 * writable with no sandbox at all (Direction 8). Paths are the owner's: `/workspace`
 * (the `cwd`, and `~`) and the temporary area `/.ta/tmp`; any other absolute path is the
 * sandbox's scratch, missing to a read here and refused to a write.
 *
 * - An opened binary or line reader pins the content it opened (its chunk list, or its
 *   bytes when inline), so a rename or an overwrite never changes what it reads; the
 *   24 h garbage grace outlives any reader.
 * - `writeFile` carries an implicit compare-and-swap: when this environment read the
 *   path (or wrote it), the write must find the version it saw, or fails with
 *   `changed since read`. pi's `edit` reads then writes in one tool call, so a change
 *   from anywhere else between the two (the owner's app, `/api`, the sandbox's sync) is
 *   refused, never overwritten. pi's own mutation queue is not a lock against those.
 * - `appendFile` and `truncateFile` keep the chunks before the one they touch and send
 *   only the rest, each against the version it read in the same call.
 * - Bytes come from `FILES` two chunks a call. A whole-file read, and a write, moves at
 *   most `WHOLE_BYTES` (16 MiB) through the object; a larger file is read by range or
 *   by line, and sent by upload. One environment (one tool call) makes at most
 *   `FILES_CALLS` calls to `FILES`, refused loudly before the request's 32-call cap: a
 *   budget on the calls themselves, not a proxy in bytes, so any mix of methods in one
 *   tool call stays inside it. A refused size or budget is `not_supported`.
 * - `readTextFile` keeps a BOM: it is the file's bytes, which pi's `edit` writes back.
 * - `renameFile` of a directory is one transaction over its subtree; no byte moves.
 * - `watch` hears every commit in the object at once (`native`).
 * - `listFiles` walks depth first in byte order and, inside a work tree (a `.git` entry
 *   in the listed directory or above it), skips what `.git/info/exclude` and the
 *   `.gitignore` files of the directory, its subdirectories and its parents ignore.
 * - `exec` is `shell_unavailable` until the sandbox slice (tasks 6) wakes the microVM
 *   and syncs files in and out under the lease. Symbolic links come with it.
 *
 * Errors are returned, never thrown, as pi's contract has it: a missing path is
 * `not_found`, a refused bound `permission_denied`, a lost swap `unknown`.
 */
import {
  type BinaryReader, type DirReader, err, type ExecutionEnv, ExecutionError, FileError,
  type FileErrorCode, type FileInfo, type FileListEntry, type FileListing, type FileWatcher,
  type LineScan, LineScanner, type ListFilesOptions, ok, type Result, type ShellExecResult,
  StreamDecoder, type TextLine, type TextLineReader, type WatchChange, type WatchTarget,
} from "@earendil-works/pi-durable/env";

import { Invalid, Refused } from "./errors";
import {
  BATCH_BYTES, batches, CHUNK_BYTES, type ChunkRef, type Content, type Entry, type FaultCode,
  FileConflict, FileFault, type Files, INLINE_BYTES, sha256Hex, WHOLE_BYTES,
} from "./files";
import { nameOf, parentOf, resolve, ROOTS, storedPath, within, WORKSPACE } from "./paths";
import { utf8Bytes } from "./text";

type Context = Parameters<ExecutionEnv["readTextFile"]>[1];

const FAULTS: Readonly<Record<FaultCode, FileErrorCode>> = Object.freeze({
  not_found: "not_found", is_directory: "is_directory", not_directory: "not_directory",
  exists: "invalid", not_empty: "invalid",
});
/** `listFiles`'s defaults and ceiling, as pi's `ListFilesOptions` states them. */
const LIST_ENTRIES = 50_000;
const LIST_BYTES = 4 * 1024 * 1024;
/** 15.9 MiB, in whole bytes. */
const LIST_MAX_BYTES = 16_672_358;
const LIST_TIMEOUT_MS = 10_000;
const PAGE = 1000;
/**
 * The `FILES` calls one environment, built for one tool call, may make. A request may
 * make 32 service-binding calls in all (tasks 6 probes whether that holds per Durable
 * Object event); the rest are left to the gateway and hooks until the probe says more.
 */
export const FILES_CALLS = 24;
/** The characters a regex source escapes. */
const SPECIAL = ".*+?^${}()|[]\\/";

function escaped(c: string): string {
  return SPECIAL.includes(c) ? `\\${c}` : c;
}

/** The pi error for anything a file operation threw. */
function fault(error: unknown, path?: string): FileError {
  if (error instanceof FileError) {
    return error;
  }
  const cause = error instanceof Error ? error : new Error(String(error));
  if (error instanceof FileFault) {
    return new FileError(FAULTS[error.code], error.message, path, cause);
  }
  if (error instanceof FileConflict) {
    return new FileError("unknown", "changed since read", path, cause);
  }
  if (error instanceof Invalid) {
    return new FileError("invalid", error.message, path, cause);
  }
  if (error instanceof Refused) {
    return new FileError("permission_denied", error.message, path, cause);
  }
  return new FileError("unknown", cause.message, path, cause);
}

function aborted(context: Context, path?: string): Result<never, FileError> | undefined {
  return context.abortSignal?.aborted ? err(new FileError("aborted", "aborted", path))
    : undefined;
}

function info(entry: Entry): FileInfo {
  return { name: nameOf(entry.path), path: entry.path,
           kind: entry.kind === "dir" ? "directory" : "file", size: entry.size,
           mtimeMs: entry.mtime_ms };
}

function join(pieces: readonly Uint8Array[]): Uint8Array {
  if (pieces.length === 1) {
    return pieces[0]!;
  }
  const out = new Uint8Array(pieces.reduce((sum, piece) => sum + piece.byteLength, 0));
  let at = 0;
  for (const piece of pieces) {
    out.set(piece, at);
    at += piece.byteLength;
  }
  return out;
}

function bytesOf(content: unknown): Uint8Array {
  if (typeof content === "string") {
    return new TextEncoder().encode(content);
  }
  if (!(content instanceof Uint8Array)) {
    throw new Invalid("content is text or bytes");
  }
  return content;
}

/** POSIX `path.join`: the parts joined by `/` and normalized, `.` when empty. */
export function joinPaths(parts: readonly string[]): string {
  const joined = parts.filter((part) => part !== "").join("/");
  const absolute = joined.startsWith("/");
  const out: string[] = [];
  for (const part of joined.split("/")) {
    if (part === "" || part === ".") {
      continue;
    }
    if (part === ".." && out.length > 0 && out.at(-1) !== "..") {
      out.pop();
    } else if (part !== ".." || !absolute) {
      out.push(part);
    }
  }
  const body = out.join("/");
  return absolute ? `/${body}` : body || ".";
}

// -- gitignore ---------------------------------------------------------------------

/** One ignore rule, relative to the directory its file sits in (`base`). */
interface IgnoreRule {
  readonly base: string;
  readonly regex: RegExp;
  readonly negate: boolean;
  readonly dirOnly: boolean;
  /** Matched against the path from `base`; otherwise against the name alone. */
  readonly anchored: boolean;
}

/** A gitignore glob as a regex source: `*`, `?`, `[...]`, `**` and `\` escapes. */
export function globSource(glob: string): string {
  let out = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]!;
    if (c === "*" && glob[i + 1] === "*" && (i === 0 || glob[i - 1] === "/")
        && (i + 2 === glob.length || glob[i + 2] === "/")) {
      if (i + 2 === glob.length) {
        out += ".*";
        i += 1;
      } else {
        out += "(?:.*/)?";
        i += 2;
      }
    } else if (c === "*") {
      out += "[^/]*";
    } else if (c === "?") {
      out += "[^/]";
    } else if (c === "[") {
      let j = i + 1;
      if (glob[j] === "!" || glob[j] === "^") {
        j++;
      }
      if (glob[j] === "]") {
        j++;
      }
      const close = glob.indexOf("]", j);
      if (close === -1) {
        out += "\\[";
      } else {
        const inner = glob.slice(i + 1, close);
        const negated = inner.startsWith("!") || inner.startsWith("^");
        out += `[${negated ? "^" : ""}${(negated ? inner.slice(1) : inner)
          .replaceAll("\\", "\\\\").replaceAll("]", "\\]")}]`;
        i = close;
      }
    } else if (c === "\\" && i + 1 < glob.length) {
      i++;
      out += escaped(glob[i]!);
    } else {
      out += escaped(c);
    }
  }
  return out;
}

/** The rules of one ignore file, whose patterns are relative to `base`. */
export function ignoreRules(text: string, base: string): IgnoreRule[] {
  const rules: IgnoreRule[] = [];
  for (const raw of text.split("\n")) {
    let line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    // Trailing spaces go unless the last is escaped.
    while (line.endsWith(" ") && !line.endsWith("\\ ")) {
      line = line.slice(0, -1);
    }
    if (line === "" || line.startsWith("#")) {
      continue;
    }
    let negate = false;
    if (line.startsWith("!")) {
      negate = true;
      line = line.slice(1);
    } else if (line.startsWith("\\!") || line.startsWith("\\#")) {
      line = line.slice(1);
    }
    const dirOnly = line.endsWith("/");
    if (dirOnly) {
      line = line.slice(0, -1);
    }
    const anchored = line.includes("/");
    if (line.startsWith("/")) {
      line = line.slice(1);
    }
    if (line !== "") {
      rules.push({ base, negate, dirOnly, anchored, regex: new RegExp(`^${globSource(line)}$`) });
    }
  }
  return rules;
}

/** Whether the rules, in order (the last that matches decides), ignore `path`. */
export function ignored(rules: readonly IgnoreRule[], path: string, directory: boolean): boolean {
  let verdict = false;
  for (const rule of rules) {
    if (!path.startsWith(`${rule.base}/`) || (rule.dirOnly && !directory)) {
      continue;
    }
    const subject = rule.anchored ? path.slice(rule.base.length + 1) : nameOf(path);
    if (rule.regex.test(subject)) {
      verdict = !rule.negate;
    }
  }
  return verdict;
}

// -- readers ------------------------------------------------------------------------

/** Chunk bytes from `FILES`, each call counted against the tool call's budget. */
type Load = (reads: readonly { sha256: string; offset?: number; length?: number }[]) =>
  Promise<Uint8Array[]>;

/** Positional reads of one pinned content. */
class PinnedReader implements BinaryReader {
  readonly #load: Load;
  readonly #content: Content;
  #closed = false;

  constructor(load: Load, content: Content) {
    this.#load = load;
    this.#content = content;
  }

  #refused(context: Context): Result<never, FileError> | undefined {
    return aborted(context, this.#content.entry.path)
      ?? (this.#closed ? err(new FileError("invalid", "Binary reader is closed",
                                           this.#content.entry.path)) : undefined);
  }

  async info(context: Context): Promise<Result<FileInfo, FileError>> {
    return this.#refused(context) ?? ok(info(this.#content.entry));
  }

  async read(offset: number, length: number, context: Context):
      Promise<Result<Uint8Array, FileError>> {
    const refused = this.#refused(context);
    if (refused) {
      return refused;
    }
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(length)
        || length < 0) {
      return err(new FileError("invalid", "Offset and length must be non-negative safe integers",
                               this.#content.entry.path));
    }
    const end = Math.min(this.#content.entry.size, offset + length);
    if (end <= offset) {
      return ok(new Uint8Array(0));
    }
    try {
      return ok(await readRange(this.#load, this.#content, offset, end));
    } catch (error) {
      return err(fault(error, this.#content.entry.path));
    }
  }

  async scanLines(options: { startLine: number; endLine?: number }, context: Context):
      Promise<Result<LineScan, FileError>> {
    const refused = this.#refused(context);
    if (refused) {
      return refused;
    }
    let scanner: LineScanner;
    try {
      scanner = new LineScanner(options.startLine, options.endLine);
    } catch {
      return err(new FileError("invalid", "Invalid line range", this.#content.entry.path));
    }
    try {
      for await (const piece of pieces(this.#load, this.#content)) {
        const stop = aborted(context, this.#content.entry.path);
        if (stop) {
          return stop;
        }
        scanner.push(piece);
      }
      return ok(scanner.finish());
    } catch (error) {
      return err(fault(error, this.#content.entry.path));
    }
  }

  async close(_context: Context): Promise<void> {
    this.#closed = true;
  }
}

/** A pinned content's bytes in order, two chunks (one `FILES` call) at a time. */
async function* pieces(load: Load, content: Content): AsyncGenerator<Uint8Array> {
  if (content.inline !== undefined) {
    yield content.inline;
    return;
  }
  const chunks = content.chunks ?? [];
  const pair = BATCH_BYTES / CHUNK_BYTES;
  for (let at = 0; at < chunks.length; at += pair) {
    yield join(await load(chunks.slice(at, at + pair)
      .map((chunk) => ({ sha256: chunk.sha256 }))));
  }
}

/**
 * Refuse moving more than `WHOLE_BYTES` through the object at once. The refusal is
 * `not_supported`, the one code pi's `edit` shows (it prints no message): the operation,
 * not the caller's input, is what this environment does not do.
 */
function capped(bytes: number, what: string): void {
  if (bytes > WHOLE_BYTES) {
    throw new FileError("not_supported", `${what} moves at most ${WHOLE_BYTES} bytes at ` +
                        "once; read larger files by range or by line");
  }
}

/** Bytes `[from, to)` of a pinned content: only the chunks that hold them. */
async function readRange(load: Load, content: Content, from: number, to: number):
    Promise<Uint8Array> {
  capped(to - from, "a read");
  if (content.inline !== undefined) {
    return content.inline.slice(from, to);
  }
  const reads: { sha256: string; offset: number; length: number }[] = [];
  const chunks = content.chunks ?? [];
  for (let index = Math.floor(from / CHUNK_BYTES); index * CHUNK_BYTES < to; index++) {
    const start = Math.max(from, index * CHUNK_BYTES) - index * CHUNK_BYTES;
    const stop = Math.min(to, (index + 1) * CHUNK_BYTES) - index * CHUNK_BYTES;
    reads.push({ sha256: chunks[index]!.sha256, offset: start, length: stop - start });
  }
  return join(await load(reads));
}

async function wholeBytes(load: Load, content: Content): Promise<Uint8Array> {
  capped(content.entry.size, "a whole-file read");
  const out: Uint8Array[] = [];
  for await (const piece of pieces(load, content)) {
    out.push(piece);
  }
  return out.length === 0 ? new Uint8Array(0) : join(out);
}

/** Lines of one pinned content, split at LF, decoded as the whole file would be. */
class PinnedLines implements TextLineReader {
  readonly #path: string;
  readonly #source: AsyncGenerator<Uint8Array>;
  readonly #decoder = new StreamDecoder();
  #buffered = "";
  #ended = false;
  #closed = false;

  constructor(load: Load, content: Content) {
    this.#path = content.entry.path;
    this.#source = pieces(load, content);
  }

  async readLine(context: Context): Promise<Result<TextLine | undefined, FileError>> {
    const stop = aborted(context, this.#path);
    if (stop) {
      return stop;
    }
    if (this.#closed) {
      return err(new FileError("invalid", "Text line reader is closed", this.#path));
    }
    try {
      while (true) {
        const newline = this.#buffered.indexOf("\n");
        if (newline !== -1) {
          const text = this.#buffered.slice(0, newline);
          this.#buffered = this.#buffered.slice(newline + 1);
          return ok({ text, terminated: true });
        }
        if (this.#ended) {
          if (this.#buffered === "") {
            return ok(undefined);
          }
          const text = this.#buffered;
          this.#buffered = "";
          return ok({ text, terminated: false });
        }
        const next = await this.#source.next();
        if (next.done) {
          this.#buffered += this.#decoder.decode();
          this.#ended = true;
        } else {
          this.#buffered += this.#decoder.decode(next.value);
        }
      }
    } catch (error) {
      return err(fault(error, this.#path));
    }
  }

  async close(_context: Context): Promise<void> {
    this.#closed = true;
    this.#buffered = "";
  }
}

/** Pages of one directory's entries in name order; entries removed meanwhile are skipped. */
class ManifestDirReader implements DirReader {
  readonly #files: Files;
  readonly #path: string;
  #after: string | null = null;
  #done = false;
  #closed = false;

  constructor(files: Files, path: string) {
    this.#files = files;
    this.#path = path;
  }

  async next(maxEntries: number, context: Context):
      Promise<Result<{ entries: FileInfo[]; done: boolean }, FileError>> {
    const stop = aborted(context, this.#path);
    if (stop) {
      return stop;
    }
    if (this.#closed) {
      return err(new FileError("invalid", "Directory reader is closed", this.#path));
    }
    if (!Number.isSafeInteger(maxEntries) || maxEntries <= 0) {
      return err(new FileError("invalid", "maxEntries must be a positive safe integer",
                               this.#path));
    }
    if (this.#done) {
      return ok({ entries: [], done: true });
    }
    try {
      const page = this.#files.stat(this.#path)?.kind === "dir"
        ? this.#files.list(this.#path, this.#after, Math.min(maxEntries, PAGE))
        : { entries: [], next: null };
      this.#after = page.next;
      this.#done = page.next === null;
      return ok({ entries: page.entries.map(info), done: this.#done });
    } catch (error) {
      return err(fault(error, this.#path));
    }
  }

  async close(_context: Context): Promise<void> {
    this.#closed = true;
  }
}

export interface FilesEnvOptions {
  readonly files: Files;
  /** Who this environment's changes are by, in the history: the agent by default. */
  readonly writer?: string;
  readonly cwd?: string;
  /**
   * Point the object's one schedule at housekeeping the changes made due: the object's
   * `Schedule.sync()`, its Lifecycle files lane. Required, so no wiring can leave a
   * change's housekeeping unscheduled.
   */
  readonly rearm: () => Promise<void>;
}

export class OwnerFilesEnv implements ExecutionEnv {
  readonly id: string;
  cwd: string;
  readonly #files: Files;
  readonly #writer: string;
  readonly #rearm: () => Promise<void>;
  /** The etag this environment last saw at each path it read or wrote. */
  readonly #seen = new Map<string, string>();
  /** `FILES` calls made so far, against `FILES_CALLS`. */
  #calls = 0;
  readonly #temps = new Set<string>();

  constructor(options: FilesEnvOptions) {
    this.#files = options.files;
    this.#writer = options.writer ?? "agent";
    this.cwd = options.cwd ?? WORKSPACE;
    this.#rearm = options.rearm;
    // Named by a hash of the namespace, which never leaves the object itself.
    this.id = `ta-files:${sha256Hex(`ta-env\n${this.#files.ns()}`).slice(0, 32)}`;
  }

  /** A stored path for `path`: outside the owner's roots, missing to a read, refused to a write. */
  #where(path: unknown, write: boolean): string {
    if (typeof path !== "string") {
      throw new Invalid("a path is text");
    }
    const absolute = resolve(this.cwd, path);
    if (!ROOTS.some((root) => within(absolute, root))) {
      if (write) {
        throw new Invalid("files live under /workspace and /.ta/tmp; other paths are the sandbox's");
      }
      throw new FileFault("not_found", "no such file or directory");
    }
    return storedPath(absolute);
  }

  /** Run one file operation: aborted first, then its result or its error, never a throw. */
  async #run<T>(context: Context, path: unknown, act: () => T | Promise<T>):
      Promise<Result<T, FileError>> {
    const where = typeof path === "string" ? path : undefined;
    const stop = aborted(context, where);
    if (stop) {
      return stop;
    }
    try {
      return ok(await act());
    } catch (error) {
      return err(fault(error, where));
    }
  }

  /** Count `calls` more `FILES` calls, refused loudly before the budget is passed. */
  #spend(calls: number): void {
    if (this.#calls + calls > FILES_CALLS) {
      throw new FileError("not_supported", `one tool call makes at most ${FILES_CALLS} ` +
                          "file-store calls; read a smaller range, or fewer files at once");
    }
    this.#calls += calls;
  }

  /** Chunk bytes, each `FILES` call the manifest makes for them counted first. */
  readonly #load: Load = (reads) => {
    this.#spend(batches(reads.map((read) => ({ size: read.length ?? CHUNK_BYTES }))).length);
    return this.#files.fetch(reads);
  };

  /** The current content of a file, its etag remembered for this environment's writes. */
  #open(at: string): Content {
    const content = this.#files.open(at);
    this.#seen.set(at, content.entry.etag);
    return content;
  }

  /** Forget what was seen at `path` and below; remember `entry` when it is a file. */
  #moved(path: string, entry?: Entry): void {
    for (const seen of [...this.#seen.keys()]) {
      if (within(seen, path)) {
        this.#seen.delete(seen);
      }
    }
    if (entry?.kind === "file") {
      this.#seen.set(entry.path, entry.etag);
    }
  }

  async #write(at: string, bytes: Uint8Array, expected: string | null,
               extra: { prefix?: readonly ChunkRef[]; mode?: number } = {}): Promise<Entry> {
    // At most this many puts: the chunks after any kept prefix, two a call.
    const whole = (extra.prefix?.length ?? 0) * CHUNK_BYTES + bytes.byteLength;
    this.#spend(whole <= INLINE_BYTES ? 0 : Math.ceil(bytes.byteLength / BATCH_BYTES));
    const entry = await this.#files.write(at, bytes, { expected, writer: this.#writer,
                                                       parents: true, ...extra });
    this.#seen.set(at, entry.etag);
    await this.#rearm();
    return entry;
  }

  async absolutePath(path: string, _context: Context): Promise<Result<string, FileError>> {
    return ok(resolve(this.cwd, path));
  }

  async joinPath(parts: string[], _context: Context): Promise<Result<string, FileError>> {
    return ok(joinPaths(parts));
  }

  async readTextFile(path: string, context: Context): Promise<Result<string, FileError>> {
    // A BOM is the file's own bytes: pi's `edit` strips it and writes it back (Fact 9).
    const decoder = new TextDecoder("utf-8", { fatal: false, ignoreBOM: true });
    return this.#run(context, path, async () => decoder.decode(
      await wholeBytes(this.#load, this.#open(this.#where(path, false)))));
  }

  async openTextLineReader(path: string, context: Context):
      Promise<Result<TextLineReader, FileError>> {
    return this.#run(context, path,
                     () => new PinnedLines(this.#load, this.#open(this.#where(path, false))));
  }

  async readTextLines(path: string, options: { maxLines?: number } | undefined,
                      context: Context): Promise<Result<string[], FileError>> {
    if (options?.maxLines !== undefined && options.maxLines <= 0) {
      return ok([]);
    }
    const opened = await this.openTextLineReader(path, context);
    if (!opened.ok) {
      return opened;
    }
    const lines: string[] = [];
    try {
      while (options?.maxLines === undefined || lines.length < options.maxLines) {
        const line = await opened.value.readLine(context);
        if (!line.ok) {
          return line;
        }
        if (line.value === undefined) {
          break;
        }
        lines.push(line.value.text);
      }
      return ok(lines);
    } finally {
      await opened.value.close(context);
    }
  }

  async readBinaryFile(path: string, context: Context): Promise<Result<Uint8Array, FileError>> {
    return this.#run(context, path, () => wholeBytes(this.#load,
                                                     this.#open(this.#where(path, false))));
  }

  /** There are no symbolic links yet, so `noFollow` changes nothing. */
  async openBinaryReader(path: string, _options: { noFollow?: boolean } | undefined,
                         context: Context): Promise<Result<BinaryReader, FileError>> {
    return this.#run(context, path,
                     () => new PinnedReader(this.#load, this.#open(this.#where(path, false))));
  }

  async writeFile(path: string, content: string | Uint8Array, context: Context):
      Promise<Result<void, FileError>> {
    return this.#run(context, path, async () => {
      const at = this.#where(path, true);
      await this.#write(at, bytesOf(content), this.#seen.get(at) ?? null);
    });
  }

  /**
   * The first `keep` bytes of a content, as `appendFile` and `truncateFile` keep them:
   * the whole chunks among them, untouched, and the bytes after those, fetched.
   */
  async #kept(content: Content, keep: number): Promise<{ prefix: ChunkRef[]; tail: Uint8Array }> {
    if (content.inline !== undefined) {
      return { prefix: [], tail: content.inline.slice(0, keep) };
    }
    const whole = Math.floor(keep / CHUNK_BYTES);
    const from = whole * CHUNK_BYTES;
    return { prefix: (content.chunks ?? []).slice(0, whole),
             tail: keep > from ? await readRange(this.#load, content, from, keep)
               : new Uint8Array(0) };
  }

  async appendFile(path: string, content: string | Uint8Array, context: Context):
      Promise<Result<void, FileError>> {
    return this.#run(context, path, async () => {
      const at = this.#where(path, true);
      const add = bytesOf(content);
      let current: Content | null = null;
      try {
        current = this.#open(at);
      } catch (error) {
        if (!(error instanceof FileFault && error.code === "not_found")) {
          throw error;
        }
      }
      if (current === null) {
        await this.#write(at, add, "");
        return;
      }
      const { prefix, tail } = await this.#kept(current, current.entry.size);
      await this.#write(at, join([tail, add]), current.entry.etag,
                        { prefix, mode: current.entry.mode });
    });
  }

  async truncateFile(path: string, size: number, context: Context):
      Promise<Result<void, FileError>> {
    return this.#run(context, path, async () => {
      const at = this.#where(path, true);
      if (!Number.isSafeInteger(size) || size < 0) {
        throw new Invalid("File size must be a non-negative safe integer");
      }
      const current = this.#open(at);
      if (size === current.entry.size) {
        return;
      }
      capped(size - current.entry.size, "a truncate that grows a file");
      const { prefix, tail } = await this.#kept(current, Math.min(size, current.entry.size));
      const grow = new Uint8Array(Math.max(0, size - current.entry.size));
      await this.#write(at, join([tail, grow]), current.entry.etag,
                        { prefix, mode: current.entry.mode });
    });
  }

  /** A commit is durable when it resolves, so a flush only checks the file is there. */
  async flushFile(path: string, context: Context): Promise<Result<void, FileError>> {
    return this.#run(context, path, () => {
      this.#files.open(this.#where(path, false));
    });
  }

  async renameFile(sourcePath: string, destinationPath: string, context: Context):
      Promise<Result<void, FileError>> {
    return this.#run(context, sourcePath, async () => {
      const from = this.#where(sourcePath, true);
      const to = this.#where(destinationPath, true);
      if (from === to) {
        this.#files.open(from);
        return;
      }
      const entry = this.#files.rename(from, to, { expected: null, replace: true,
                                                   writer: this.#writer });
      this.#moved(from);
      this.#moved(to, entry);
      await this.#rearm();
    });
  }

  async fileInfo(path: string, context: Context): Promise<Result<FileInfo, FileError>> {
    return this.#run(context, path, () => {
      const entry = this.#files.stat(this.#where(path, false));
      if (entry === null) {
        throw new FileFault("not_found", "no such file or directory");
      }
      return info(entry);
    });
  }

  async listDir(path: string, context: Context): Promise<Result<FileInfo[], FileError>> {
    return this.#run(context, path, () => {
      const at = this.#where(path, false);
      const out: FileInfo[] = [];
      let after: string | null = null;
      do {
        const page = this.#files.list(at, after, PAGE);
        out.push(...page.entries.map(info));
        after = page.next;
      } while (after !== null);
      return out;
    });
  }

  async openDirReader(path: string, context: Context): Promise<Result<DirReader, FileError>> {
    return this.#run(context, path, () => {
      const at = this.#where(path, false);
      this.#files.list(at, null, 1);
      return new ManifestDirReader(this.#files, at);
    });
  }

  async listFiles(path: string, options: ListFilesOptions | undefined, context: Context):
      Promise<Result<FileListing, FileError>> {
    for (const key of ["maxEntries", "maxBytes", "timeoutMs"] as const) {
      const value = options?.[key];
      if (value !== undefined && (!Number.isSafeInteger(value) || value < 0)) {
        return err(new FileError("invalid", `${key} must be a non-negative safe integer`, path));
      }
    }
    return this.#run(context, path, async () => {
      const at = this.#where(path, false);
      const top = this.#files.stat(at);
      if (top === null) {
        throw new FileFault("not_found", "no such directory");
      }
      if (top.kind !== "dir") {
        throw new FileFault("not_directory", "not a directory");
      }
      return this.#walk(at, options ?? {}, context);
    });
  }

  async #walk(at: string, options: ListFilesOptions, context: Context): Promise<FileListing> {
    const maxEntries = options.maxEntries ?? LIST_ENTRIES;
    const maxBytes = Math.min(options.maxBytes ?? LIST_BYTES, LIST_MAX_BYTES);
    const deadline = Date.now() + (options.timeoutMs ?? LIST_TIMEOUT_MS);
    const hidden = options.hidden ?? true;
    const exclude = new Set(options.exclude ?? []);
    const tree = (options.gitignore ?? true) ? await this.#workTree(at) : null;
    const entries: FileListEntry[] = [];
    let bytes = 0;
    let truncated = false;
    const visit = async (dir: string, rel: string, inherited: IgnoreRule[]): Promise<boolean> => {
      if (context.abortSignal?.aborted) {
        throw new FileError("aborted", "aborted", dir);
      }
      const rules = tree === null ? [] : [...inherited, ...await this.#ignoreFile(dir)];
      let after: string | null = null;
      do {
        const page = this.#files.list(dir, after, PAGE);
        for (const entry of page.entries) {
          const name = nameOf(entry.path);
          const directory = entry.kind === "dir";
          if (name === ".git" || (!hidden && name.startsWith(".")) || exclude.has(name)
              || (tree !== null && ignored(rules, entry.path, directory))) {
            continue;
          }
          const path = rel === "" ? name : `${rel}/${name}`;
          const cost = utf8Bytes(path) + 2;
          if (entries.length >= maxEntries || bytes + cost > maxBytes || Date.now() > deadline) {
            truncated = true;
            return true;
          }
          entries.push({ path, kind: directory ? "directory" : "file" });
          bytes += cost;
          if (directory && await visit(entry.path, path, rules)) {
            return true;
          }
        }
        after = page.next;
      } while (after !== null);
      return false;
    };
    await visit(at, "", tree ?? []);
    return { entries, truncated };
  }

  /**
   * The rules a listing of `at` inherits when it is inside a work tree: the tree's
   * `.git/info/exclude`, then the `.gitignore` of each directory from the tree's root
   * down to `at`'s parent. Null outside a work tree.
   */
  async #workTree(at: string): Promise<IgnoreRule[] | null> {
    const chain: string[] = [];
    for (let dir = at; ; dir = parentOf(dir)) {
      chain.unshift(dir);
      if (this.#files.stat(`${dir}/.git`) !== null) {
        const rules = await this.#ignoreFile(`${dir}/.git/info`, "exclude", dir);
        for (const parent of chain.slice(0, -1)) {
          rules.push(...await this.#ignoreFile(parent));
        }
        return rules;
      }
      if (ROOTS.includes(dir)) {
        return null;
      }
    }
  }

  async #ignoreFile(dir: string, name = ".gitignore", base = dir): Promise<IgnoreRule[]> {
    const entry = this.#files.stat(`${dir}/${name}`);
    if (entry === null || entry.kind !== "file") {
      return [];
    }
    const content = this.#files.open(`${dir}/${name}`);
    return ignoreRules(new TextDecoder().decode(await wholeBytes(this.#load, content)), base);
  }

  async watch(targets: readonly WatchTarget[], onChange: (change: WatchChange) => void,
              context: Context): Promise<Result<FileWatcher, FileError>> {
    return this.#run(context, undefined, () => {
      const watched = targets.map((target) => {
        if (typeof target?.path !== "string") {
          throw new Invalid("a watch target is a path");
        }
        return { ...target, path: resolve(this.cwd, target.path) };
      });
      let open = true;
      const stop = this.#files.watch((paths) => {
        const hits = [...new Set(paths.flatMap((path) => watched.flatMap((target) =>
          covered(target, path) ? [path] : [])))];
        if (open && hits.length > 0) {
          onChange({ paths: hits });
        }
      });
      return {
        mode: "native" as const,
        close: async (_context: Context) => {
          open = false;
          stop();
        },
      };
    });
  }

  async canonicalPath(path: string, context: Context): Promise<Result<string, FileError>> {
    return this.#run(context, path, () => {
      const at = this.#where(path, false);
      if (this.#files.stat(at) === null) {
        throw new FileFault("not_found", "no such file or directory");
      }
      return at;
    });
  }

  async exists(path: string, context: Context): Promise<Result<boolean, FileError>> {
    const found = await this.fileInfo(path, context);
    if (found.ok) {
      return ok(true);
    }
    return found.error.code === "not_found" ? ok(false) : err(found.error);
  }

  async createDir(path: string, options: { recursive?: boolean } | undefined,
                  context: Context): Promise<Result<void, FileError>> {
    return this.#run(context, path, async () => {
      const at = this.#where(path, true);
      const recursive = options?.recursive ?? true;
      if (!recursive && this.#files.stat(at) !== null) {
        throw new FileFault("exists", "already exists");
      }
      this.#files.mkdir(at, { expected: null, parents: recursive, writer: this.#writer });
      await this.#rearm();
    });
  }

  async remove(path: string, options: { recursive?: boolean; force?: boolean } | undefined,
               context: Context): Promise<Result<void, FileError>> {
    return this.#run(context, path, async () => {
      const at = this.#where(path, true);
      try {
        this.#files.remove(at, { expected: null, recursive: options?.recursive ?? false,
                                 writer: this.#writer });
      } catch (error) {
        if (!(options?.force && error instanceof FileFault && error.code === "not_found")) {
          throw error;
        }
      }
      this.#moved(at);
      await this.#rearm();
    });
  }

  async createTempDir(prefix: string | undefined, context: Context):
      Promise<Result<string, FileError>> {
    return this.#run(context, undefined, async () => {
      const dir = this.#files.tempDir(prefix ?? "tmp-", this.#writer);
      this.#temps.add(dir);
      await this.#rearm();
      return dir;
    });
  }

  async createTempFile(options: { prefix?: string; suffix?: string } | undefined,
                       context: Context): Promise<Result<string, FileError>> {
    const dir = await this.createTempDir("tmp-", context);
    if (!dir.ok) {
      return dir;
    }
    return this.#run(context, undefined, async () => {
      const at = storedPath(`${dir.value}/${options?.prefix ?? ""}${crypto.randomUUID()}${
        options?.suffix ?? ""}`);
      if (parentOf(at) !== dir.value) {
        throw new Invalid("a temporary file's prefix and suffix hold no slash");
      }
      await this.#write(at, new Uint8Array(0), "");
      return at;
    });
  }

  /** The shell comes with the sandbox (tasks 6); until then there is none. */
  async exec(_command: string | readonly string[], _options: unknown, _context: Context):
      Promise<Result<ShellExecResult, ExecutionError>> {
    return err(new ExecutionError("shell_unavailable",
                                  "no sandbox yet: commands run once the sandbox slice lands"));
  }

  /** Remove this environment's temporary directories. */
  async cleanup(_context: Context): Promise<void> {
    if (this.#temps.size > 0) {
      this.#files.dropTemps([...this.#temps], this.#writer);
      this.#temps.clear();
      await this.#rearm();
    }
  }
}

/**
 * Whether a change at `path` is one `target` reports: the target itself or anything
 * above it (creating, removing or moving an ancestor moves it), or below a directory
 * target (its entries only, unless recursive), minus excluded names below it.
 */
function covered(target: WatchTarget, path: string): boolean {
  if (within(target.path, path)) {
    return true;
  }
  if (!within(path, target.path)) {
    return false;
  }
  const parts = path.slice(target.path.length + 1).split("/");
  if (target.recursive !== true && parts.length > 1) {
    return false;
  }
  return !parts.some((part) => (target.exclude?.hidden === true && part.startsWith("."))
                               || (target.exclude?.names ?? []).includes(part));
}
