/**
 * Owner file paths (plan §6): absolute POSIX paths under the owner's two roots, stored
 * verbatim.
 *
 * `/workspace` holds the owner's files; `/.ta/tmp` is the reserved temporary area
 * (`createTempDir`, spill files), whose entries expire. Both roots always exist and are
 * never written, removed or renamed themselves. A stored path is absolute, has no empty,
 * `.` or `..` component and no NUL, is well-formed Unicode, and is at most 4,096 UTF-8
 * bytes with components of at most 255. It is never normalized (no NFC, no case
 * folding): two names that differ in any code point are two files, as on Linux. That is
 * also why a path never becomes an R2 key (R2 NFC-normalizes keys; `files/src/keys.ts`).
 *
 * `resolve` is the one place a relative or dotted path is turned into a stored one, as
 * the pi environment's `absolutePath` does against its `cwd`; the RPC doors take only
 * stored paths and refuse anything else.
 */
import { Invalid } from "./errors";
import { utf8Bytes } from "./text";

export const WORKSPACE = "/workspace";
export const TEMP = "/.ta/tmp";
export const ROOTS: readonly string[] = Object.freeze([WORKSPACE, TEMP]);
export const MAX_PATH_BYTES = 4096;
export const MAX_NAME_BYTES = 255;
const LONE_SURROGATE = /\p{Cs}/u;

/** Whether `path` is one of the two roots. */
export function isRoot(path: string): boolean {
  return ROOTS.includes(path);
}

/** Whether `path` is `root` or below it. */
export function within(path: string, root: string): boolean {
  return path === root || path.startsWith(`${root}/`);
}

/**
 * A stored path: absolute, canonical, verbatim, under a root (or a root itself), or
 * refused as invalid.
 */
export function storedPath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.includes("\0")
      || LONE_SURROGATE.test(value) || utf8Bytes(value) > MAX_PATH_BYTES) {
    throw new Invalid("a path is an absolute, well-formed path of at most 4096 bytes");
  }
  const parts = value.split("/").slice(1);
  if (parts.some((part) => part === "" || part === "." || part === ".."
                           || utf8Bytes(part) > MAX_NAME_BYTES)) {
    throw new Invalid("a path has no empty, . or .. component, and names of at most 255 bytes");
  }
  if (!ROOTS.some((root) => within(value, root))) {
    throw new Invalid("a path is under /workspace or /.ta/tmp");
  }
  return value;
}

/**
 * `path` against `cwd`, as POSIX resolves it lexically: `.` and empty components drop,
 * `..` goes up (never above `/`), `~` is the workspace. Not checked against the roots;
 * `storedPath` does that.
 */
export function resolve(cwd: string, path: string): string {
  const start = path === "~" || path.startsWith("~/") ? WORKSPACE + path.slice(1)
    : path.startsWith("/") ? path : `${cwd}/${path}`;
  const parts: string[] = [];
  for (const part of start.split("/")) {
    if (part === "" || part === ".") {
      continue;
    }
    if (part === "..") {
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return `/${parts.join("/")}`;
}

/** The parent of a stored path below a root. */
export function parentOf(path: string): string {
  return path.slice(0, path.lastIndexOf("/")) || "/";
}

/** The last component of a stored path. */
export function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The ancestors of `path` from just below its root down to its parent. */
export function ancestors(path: string): string[] {
  const root = ROOTS.find((r) => within(path, r)) ?? "/";
  const found: string[] = [];
  for (let at = parentOf(path); at !== root && at.length > root.length; at = parentOf(at)) {
    found.unshift(at);
  }
  return found;
}
