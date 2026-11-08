/**
 * Owner file paths (plan §6): absolute POSIX paths under the owner's two roots, stored
 * verbatim. The owner's object stores them, the sandbox's runner holds paths of the owner's
 * agent trees, and the cutover's importer checks every name it would write against the same
 * rule (`platform/import/`).
 *
 * `/workspace` holds the owner's files; `/.ta/tmp` is the reserved temporary area
 * (`createTempDir`, spill files), whose entries expire. Both roots always exist and are
 * never written, removed or renamed themselves. A stored path is absolute, has no empty,
 * `.` or `..` component and no NUL, is well-formed Unicode, and is at most 4,096 UTF-8
 * bytes with components of at most 255. It is never normalized (no NFC, no case
 * folding): two names that differ in any code point are two files, as on Linux. That is
 * also why a path never becomes an R2 key (R2 NFC-normalizes keys; `files/src/keys.ts`).
 *
 * `resolve` is the one place a relative or dotted path is turned into a stored one, against
 * a `cwd`; the RPC doors take only stored paths and refuse anything else.
 *
 * The owner's agent trees (Direction 3: abilities are editable files): the agent
 * definitions, the abilities' trees that reach every engine's instructions, the owner's
 * goals and the owner's profile.
 */
import { Refusal } from "./refusal";
import { MAX_PATH_BYTES } from "./limits";
import { utf8Bytes } from "./text";

export const WORKSPACE = "/workspace";
export const TEMP = "/.ta/tmp";
export const ROOTS: readonly string[] = Object.freeze([WORKSPACE, TEMP]);
export const MAX_NAME_BYTES = 255;
const LONE_SURROGATE = /\p{Cs}/u;

/** The owner's agent definitions, `/workspace/agents/<agent>/`: the owner's alone to change. */
export const AGENTS_DIR = "/workspace/agents";
/** The agent's abilities: its `SKILL.md` folders, which every engine discovers itself. */
export const ABILITIES_DIR = "/workspace/.agents";
export const SKILLS_DIR = "/workspace/skills";
export const PROMPTS_DIR = "/workspace/prompts";
/** The abilities' trees, which reach an engine's instructions: every run reads them. */
export const ABILITY_TREES: readonly string[] = Object.freeze([
  ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR]);
/**
 * The owner's goals, one page a goal: what its agents may do toward each without asking is
 * the goal's Agreed section, so a goal is the owner's own word, as an agent's definition is.
 */
export const GOALS_DIR = "/workspace/goals";
/** The owner's profile: its directives are every agent's standing preferences. */
export const PROFILE = "/workspace/USER.md";
/**
 * Where the cutover's importer puts an owner's tree from the old stack (`platform/import/`,
 * the owner's import doors, `owner/src/import.ts`): the only tree those doors write.
 */
export const IMPORTED = "/workspace/imported";
/**
 * Every tree only the owner's own authority changes: the agent definitions, the abilities'
 * trees, the goals and the profile. A grant the owner narrowed changes nothing in them
 * (`owner/src/paths.ts`'s `ownersOnly`).
 */
export const OWNER_TREES: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR, GOALS_DIR, PROFILE]);
/**
 * What an outside client's sandbox holds read-only and never sends back: the agent
 * definitions and the abilities' trees, its always-read set (owner-object spec, a client's
 * sandbox). Its goals and the owner's profile are not in it: a client under a full grant
 * may write them, and the owner's object holds a narrowed one to `OWNER_TREES`.
 */
export const CLIENT_READ_ONLY: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR]);

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
    throw new Refusal("invalid", "a path is an absolute, well-formed path of at most 4096 bytes");
  }
  const parts = value.split("/").slice(1);
  if (parts.some((part) => part === "" || part === "." || part === ".."
                           || utf8Bytes(part) > MAX_NAME_BYTES)) {
    throw new Refusal("invalid",
                      "a path has no empty, . or .. component, and names of at most 255 bytes");
  }
  if (!ROOTS.some((root) => within(value, root))) {
    throw new Refusal("invalid", "a path is under /workspace or /.ta/tmp");
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
