/**
 * Owner file paths: absolute POSIX paths under the owner's one root, stored
 * verbatim. The owner's object stores them, the sandbox's runner holds paths of the owner's
 * agent trees, and the cutover's importer checks every name it would write against the same
 * rule (`platform/import/`).
 *
 * `/workspace` holds the owner's files. The root always exists and is never written,
 * removed or renamed itself. A stored path is absolute, has no empty,
 * `.` or `..` component and no NUL, is well-formed Unicode, and is at most 4,096 UTF-8
 * bytes with components of at most 255. It is never normalized (no NFC, no case
 * folding): two names that differ in any code point are two files, as on Linux. That is
 * also why a path never becomes an R2 key (R2 NFC-normalizes keys; `files/src/keys.ts`).
 *
 * `resolve` is the one place a relative or dotted path is turned into a stored one, against
 * a `cwd`; the RPC doors take only stored paths and refuse anything else.
 *
 * A new name is one every place the owner's files reach can hold (`unportable`): they sync
 * to Linux sandboxes and to the owner's own computers, Windows and macOS among them
 * (ADR-040), so a name Windows cannot hold, or one holding a control character, is never
 * made. The owner's object holds the rule wherever an entry is made (`owner/src/files.ts`
 * `#upsert`: the file tools, uploads, the sync, a rename's target, a restore), and the
 * cutover's importer keeps such a name in `refused.tar` (`newPath`). A name made before the
 * rule is read, written, removed and renamed as it is (`untangle-2026-11/stage-batch-3.md`).
 *
 * The owner's agent trees (Direction 3: abilities are editable files): the agent
 * definitions, the abilities' trees that reach every engine's instructions, the owner's
 * goals and the owner's profile.
 */
import { Refusal } from "./refusal.ts";
import { MAX_PATH_BYTES } from "./limits.ts";
import { utf8Bytes } from "./text.ts";

export const WORKSPACE = "/workspace";
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
 * The owner's own model ranking: present, it replaces the platform's whole
 * (`sources/src/ranking.ts`) for the owner's every `auto` choice.
 */
export const RANKING = "/workspace/model-ranking.json";
/**
 * Where the cutover's importer puts an owner's tree from the old stack (`platform/import/`,
 * the owner's import doors, `owner/src/import.ts`): the only tree those doors write.
 */
export const IMPORTED = "/workspace/imported";
/**
 * Every tree only the owner's own authority changes: the agent definitions, the abilities'
 * trees, the goals, the profile and the model ranking. A grant the owner narrowed changes
 * nothing in them (`owner/src/paths.ts`'s `ownersOnly`).
 */
export const OWNER_TREES: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR, GOALS_DIR, PROFILE, RANKING]);
/**
 * What an outside client's sandbox holds read-only and never sends back: the agent
 * definitions and the abilities' trees, its always-read set (owner-object spec, a client's
 * sandbox). Its goals and the owner's profile are not in it: a client under a full grant
 * may write them, and the owner's object holds a narrowed one to `OWNER_TREES`.
 */
export const CLIENT_READ_ONLY: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR]);

/** Whether `path` is the root. */
export function isRoot(path: string): boolean {
  return path === WORKSPACE;
}

/** Whether `path` is `root` or below it. */
export function within(path: string, root: string): boolean {
  return path === root || path.startsWith(`${root}/`);
}

/**
 * A stored path: absolute, canonical, verbatim, under the root (or the root itself), or
 * refused as invalid.
 */
export function storedPath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/")) {
    throw new Refusal("invalid", "a path is absolute, such as /workspace/notes.md");
  }
  if (value.includes("\0")) {
    throw new Refusal("invalid", "a path holds no NUL character");
  }
  if (LONE_SURROGATE.test(value) || utf8Bytes(value) > MAX_PATH_BYTES) {
    throw new Refusal("invalid", "a path is well-formed Unicode of at most 4096 bytes");
  }
  const parts = value.split("/").slice(1);
  if (parts.some((part) => part === "" || part === "." || part === ".."
                           || utf8Bytes(part) > MAX_NAME_BYTES)) {
    throw new Refusal("invalid",
                      "a path has no empty, . or .. component, and names of at most 255 bytes");
  }
  if (!within(value, WORKSPACE)) {
    throw new Refusal("invalid", "a path is under /workspace");
  }
  return value;
}

/** A control character: C0 (NUL, a newline, a tab among them), DEL and C1. */
const CONTROL = /\p{Cc}/u;
/**
 * What a Windows file name cannot hold (Microsoft's "Naming Files, Paths, and Namespaces"),
 * beside the `/` no stored name holds; OneDrive and Nextcloud's Windows-compatible names
 * refuse the same.
 */
const NOT_ON_WINDOWS = /[\\:*?"<>|]/u;
/**
 * The device names Windows reserves, in any case and with any extension (`NUL.txt` is
 * `NUL`), the part before the first dot read without its trailing spaces.
 */
const RESERVED = /^(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])$/iu;

/**
 * Why `name`, one component, cannot be made in the owner's files, or null: a control
 * character, a character Windows names cannot hold, a trailing space or dot (which Windows
 * drops), or a device name Windows reserves. Every sync tool that reaches Windows meets these
 * (Dropbox, OneDrive, Syncthing, git's `core.protectNTFS`); refused here, before a name is
 * made, a name stays the same on every place it reaches.
 */
export function unportable(name: string): string | null {
  const shown = JSON.stringify(name);
  if (CONTROL.test(name)) {
    return `the name ${shown} holds a control character (such as NUL, a tab or a newline), ` +
      "which no file name holds";
  }
  const held = NOT_ON_WINDOWS.exec(name)?.[0];
  if (held !== undefined) {
    return `the name ${shown} holds ${held}, which a Windows file name cannot hold ` +
      "(none of \\ : * ? \" < > |); the owner's files reach Windows computers, so choose " +
      "another name";
  }
  if (/[ .]$/u.test(name)) {
    return `the name ${shown} ends in a space or a dot, which Windows drops from a file name`;
  }
  if (RESERVED.test(name.split(".")[0]!.trimEnd())) {
    return `the name ${shown} is a device name Windows reserves (CON, PRN, AUX, NUL, COM0 ` +
      "to COM9 and LPT0 to LPT9, with any extension), so choose another name";
  }
  return null;
}

/**
 * A stored path whose last name may be made (`unportable`), or refused as invalid: what a
 * walk that makes each name below its parent checks, one name at a time (the importer's).
 */
export function newPath(value: unknown): string {
  const path = storedPath(value);
  const why = unportable(nameOf(path));
  if (why !== null) {
    throw new Refusal("invalid", why);
  }
  return path;
}

/**
 * `path` against `cwd`, as POSIX resolves it lexically: `.` and empty components drop,
 * `..` goes up (never above `/`), `~` is the workspace. Not checked against the root;
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

/** The parent of a stored path below the root. */
export function parentOf(path: string): string {
  return path.slice(0, path.lastIndexOf("/")) || "/";
}

/** The last component of a stored path. */
export function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The ancestors of a stored path from just below the root down to its parent. */
export function ancestors(path: string): string[] {
  const found: string[] = [];
  for (let at = parentOf(path); at.length > WORKSPACE.length; at = parentOf(at)) {
    found.unshift(at);
  }
  return found;
}
