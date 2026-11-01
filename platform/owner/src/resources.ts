/**
 * The agent's abilities as files (Direction 3; tasks 5, A5; pi audit F9): skills, prompt
 * templates and declarative packages, read from the owner's files the pi-coding-agent way,
 * so the platform builds no feature editor: the owner edits files.
 *
 * - **Skills** are the Agent Skills format: a directory holding `SKILL.md`, found under
 *   `/workspace/.agents/skills` and `/workspace/skills` at any depth (a directory holding
 *   `SKILL.md` is a skill and is not looked into further; a dot directory and
 *   `node_modules` are skipped, as pi skips them), then each selected package's skills.
 *   Each is parsed with agents' `parseSkill` (spec-checked, a broken rule a warning, an
 *   unusable skill refused) and listed, when `isModelInvocable`, in the `skills` prompt
 *   section of the trusted `ta.skills` extension: its name, description and path, in pi's
 *   own words (`formatSkillsForPrompt`, pi-coding-agent 1.4.0, MIT). The model reads a
 *   skill's `SKILL.md` with `read`; there is no skill tool, so the model's tools stay the
 *   five. A name taken twice keeps the first found, as pi keeps it.
 * - **Prompt templates** are the direct `.md` children of `/workspace/.agents/prompts` and
 *   `/workspace/prompts`, then each selected package's: the file's name is the command.
 * - **`/skill:name args`** and **`/template args`** in the owner's own input are expanded at
 *   the doors as pi's `prompt` expands them (`expand`): a skill's body in pi's `<skill>`
 *   block, its arguments after it; a template's body with pi's argument substitution
 *   (`$1`, `$@`, `${1:-default}`, `${@:N:L}`). An unknown name is left as typed. Both read
 *   the file as it stands at expansion, as pi reads it then.
 * - **Packages** are pi packages under `/workspace/.agents/packages` (a directory, its
 *   `package.json` holding the `pi` and `tinyassets` keys), selected by owner policy
 *   (`policy.ts`, the `packages` table, pi's settings form) with pi's filter grammar per
 *   resource type (`applyPatterns`: plain globs include, `!` excludes, `+` forces an exact
 *   path in, `-` out; omitted loads all, `[]` none). A package contributes data only: skills
 *   and prompt templates, and executable hooks that run only remotely. One that declares
 *   code to run in-process is refused whole, loudly, with the fixed code
 *   `package_executable` and an owner notice: any npm key but its data (an entry point, a
 *   binary, a script), any `pi` key but its skills and prompts (pi's UI data, `themes`,
 *   `image` and `video`, skipped with a notice), a `pi.extensions` that is not an empty
 *   list, or a conventional `extensions/` directory. The owner object runs no package code.
 * - **Hooks** are the one executable part a package may hold, and only remotely (H1,
 *   `hooks.ts`): its `tinyassets.hooks` entries (`{module, points, tools?, timeout_ms?}`),
 *   each module a prebundled ESM file in the package that the owner's own authority wrote,
 *   become remote wrappers on the package's `pkg:<name>` extension (its directory below
 *   `/workspace/.agents/packages`), which call the hooks Worker; the modules of every
 *   selected package make the owner's one bundle, at most `BUNDLE_MAX` bytes (a package
 *   that would pass it is refused whole, `package_invalid`). An entry that is not one, or a
 *   module that cannot be read as the owner's, refuses the package whole too. The
 *   `tinyassets` key's other fields are not loaded yet (settings and model routing hints
 *   have no consumer: owner policy is the one settings source, tasks 5): each is skipped
 *   with a notice, the package's skills and prompts still loading.
 *
 * Every root here, and every package, is in a tree only the owner's own authority changes
 * (`OWNER_TREES`, `paths.ts`: refused to a client at the file doors and in the agent's
 * environment, each change the owner's agent makes noticed), so no client's file is ever
 * beside an owner's skill or in a selected package. As a second layer, only a skill,
 * template or manifest the owner's own authority wrote configures the agent, as `AGENTS.md`
 * does (`OwnerDO.#agentConfig`), any other skipped with a notice. The section reaches a
 * conversation only while its authority may read the owner's files (`read_files`).
 *
 * The section is built once at the first open and rebuilt only when the files' change feed
 * (`Files.watch`) touches a `SKILL.md` or a template under a root, or anything under a
 * selected package's directory, or the owner's package selection changes: never per
 * request, so a conversation's prompt, and the provider's prompt cache, stays as it was. A
 * rebuild installs the extension again under its name (`registry.install`), which pi
 * swaps in place: running work keeps the section it took, the next request reads the new
 * one (pi-durable README, "Reload"). Rebuilds coalesce: one at a time, and one more after
 * it when a change came during it.
 */
import { isModelInvocable, parseSkill } from "agents/skills";

import { type Context } from "@earendil-works/chord/context";
import { defineExtension, type Extension, type PromptInput } from "@earendil-works/pi-durable";

import { Invalid, Refused } from "./errors";
import { type Content, type Entry, FileFault, type Files, MAX_LIST } from "./files";
import type { Write } from "./gate";
import {
  type Bundle, BUNDLE_MAX, bundleOf, type HookEntry, hookEntries, PACKAGE_EXTENSION,
  type PackageHooks,
} from "./hooks";
import type { Notices } from "./notices";
import { nameOf, PACKAGES_DIR, parentOf, resolve, within } from "./paths";
import { codePoints } from "./text";

/** Where skills are found, in order: the Agent Skills location, then the workspace's. */
export const SKILL_ROOTS: readonly string[] = Object.freeze(["/workspace/.agents/skills",
                                                             "/workspace/skills"]);
/** Where prompt templates are found, in order. */
export const PROMPT_ROOTS: readonly string[] = Object.freeze(["/workspace/.agents/prompts",
                                                              "/workspace/prompts"]);
/** The trusted extension that carries the skills section, and the section's key. */
export const SKILLS_EXTENSION = "ta.skills";
export const SKILLS_SECTION = "skills";
export const SKILL_FILE = "SKILL.md";
export const MANIFEST_FILE = "package.json";
/** The skills the section lists at most, past which the rest are told in a notice. */
export const SKILLS_LISTED = 128;
/** The prompt templates kept at most. */
export const TEMPLATES_KEPT = 128;
/** A description's most characters in the section, as the spec bounds it. */
export const DESCRIPTION_MAX = 1_024;
/** The largest skill, template or manifest read, in bytes. */
export const RESOURCE_FILE_MAX = 65_536;
/** The entries one build visits at most under each root or package, and the deepest walk. */
export const WALK_MAX = 2_048;
export const WALK_DEPTH = 8;
/** The entries one manifest list holds at most. */
export const MANIFEST_ENTRIES = 64;
/**
 * A manifest's npm keys that are only data about the package: any other (an entry point, its
 * conditional entry points, a binary, a lifecycle script, a bundler's field) names code, or
 * nothing the object knows, and refuses the package.
 */
const NPM_DATA: readonly string[] = Object.freeze([
  "name", "version", "description", "keywords", "license", "author", "contributors",
  "maintainers", "repository", "homepage", "bugs", "funding", "private", "type", "engines",
  "files", "dependencies", "devDependencies", "peerDependencies", "peerDependenciesMeta",
  "optionalDependencies", "publishConfig", "types", "typings", "pi", "tinyassets"]);
/** The `pi` keys the object loads, and those it knows as data it does not load (UI). */
const PI_DATA: readonly string[] = Object.freeze(["skills", "prompts"]);
const PI_UNLOADED: readonly string[] = Object.freeze(["themes", "image", "video"]);
/** The `pi` key that lists a package's extensions: code. */
const PI_CODE = "extensions";

/** A package refused for declaring code: the owner object runs none. */
export const PACKAGE_EXECUTABLE = "package_executable";
/** The `tinyassets` key the object loads: a package's remote hooks. */
const OURS_LOADED = "hooks";

/** A package refused for a manifest or directory that cannot be read as one. */
export const PACKAGE_INVALID = "package_invalid";
/** The notices: a skill or template not loaded, a package refused, a field not loaded. */
export const SKILL_SKIPPED = "skill_skipped";
export const PACKAGE_REFUSED = "package_refused";
export const PACKAGE_FIELD_SKIPPED = "package_field_skipped";
export const RESOURCES_TRUNCATED = "resources_truncated";
export const RESOURCES_UNBUILT = "resources_unbuilt";
/** A `/skill:` input naming a skill that cannot be read now. */
export const SKILL_UNREADABLE = "skill_unreadable";

/** One package the owner selected, and its filters (pi's settings form). */
export interface PackageSelection {
  /** The package's directory, under `/workspace`. */
  readonly source: string;
  readonly skills?: readonly string[];
  readonly prompts?: readonly string[];
}

/** A skill as the index holds it. */
export interface Skill {
  readonly name: string;
  readonly description: string;
  /** Its `SKILL.md`. */
  readonly path: string;
  /** Its directory, which its relative paths resolve against. */
  readonly dir: string;
  readonly invocable: boolean;
}

/** A prompt template as the index holds it: its command name and file. */
export interface Template {
  readonly name: string;
  readonly path: string;
}

/**
 * What a build found: the skills, the templates and the section they make, and the selected
 * packages' hooks with the owner's bundle of their modules (none: no package has hooks).
 */
export interface Index {
  readonly skills: readonly Skill[];
  readonly templates: readonly Template[];
  readonly section: string | undefined;
  readonly hooks: readonly PackageHooks[];
  readonly bundle: Bundle | null;
}

const EMPTY: Index = Object.freeze({ skills: Object.freeze([]), templates: Object.freeze([]),
                                     section: undefined, hooks: Object.freeze([]),
                                     bundle: null });

/** Whether a file's writer is the owner's own authority: the owner, or its agent. */
export function ownersOwn(writer: string): boolean {
  return writer === "owner" || writer === "agent";
}

/** A file read as the owner's text, or why it is skipped; null when there is none. */
type Read = { readonly text: string } | { readonly skipped: string };

/**
 * `path`'s text, when the owner's own authority wrote it, it is within `max` bytes (a
 * resource's, by default) and it is UTF-8.
 */
export async function ownedText(files: Files, path: string, max = RESOURCE_FILE_MAX):
    Promise<Read | null> {
  let content: Content;
  try {
    content = files.open(path);
  } catch (error) {
    if (error instanceof FileFault && error.code === "not_found") {
      return null;
    }
    if (error instanceof FileFault) {
      return { skipped: "not a file" };
    }
    throw error;
  }
  if (!ownersOwn(content.entry.writer)) {
    return { skipped: "not written by the owner" };
  }
  if (content.entry.size > max) {
    return { skipped: `larger than ${max} bytes` };
  }
  const parts = content.inline !== undefined ? [content.inline]
    : await files.fetch((content.chunks ?? []).map(({ sha256 }) => ({ sha256 })));
  const bytes = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
  let at = 0;
  for (const part of parts) {
    bytes.set(part, at);
    at += part.byteLength;
  }
  try {
    return { text: new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes) };
  } catch {
    return { skipped: "not UTF-8 text" };
  }
}

/** A bounded walk's budget: the entries it may still visit, and whether it ran out. */
interface Budget {
  left: number;
  truncated: boolean;
}

/** A directory's entries, every page, within the budget; none for a path not a directory. */
function listed(files: Files, dir: string, budget: Budget): Entry[] {
  if (files.stat(dir)?.kind !== "dir") {
    return [];
  }
  const out: Entry[] = [];
  let after: string | null = null;
  do {
    const page = files.list(dir, after, Math.min(MAX_LIST, Math.max(1, budget.left)));
    for (const entry of page.entries) {
      if (budget.left <= 0) {
        budget.truncated = true;
        return out;
      }
      budget.left--;
      out.push(entry);
    }
    after = page.next;
  } while (after !== null);
  return out;
}

/** Whether a walk looks into a directory: never a dot directory or `node_modules`. */
function walked(name: string): boolean {
  return !name.startsWith(".") && name !== "node_modules";
}

/**
 * The `SKILL.md` files under `dir`, as pi finds them: a directory holding one is a skill
 * and is not looked into further; otherwise each subdirectory is, to `WALK_DEPTH`.
 */
export function skillFiles(files: Files, dir: string, budget: Budget, depth = 0): string[] {
  const entries = listed(files, dir, budget);
  const own = entries.find((entry) => entry.kind === "file" && nameOf(entry.path) === SKILL_FILE);
  if (own !== undefined) {
    return [own.path];
  }
  if (depth >= WALK_DEPTH) {
    return [];
  }
  return entries.filter((entry) => entry.kind === "dir" && walked(nameOf(entry.path)))
    .flatMap((entry) => skillFiles(files, entry.path, budget, depth + 1));
}

/** The prompt templates of `dir`: its direct `.md` children, as pi's prompt directories. */
export function templateFiles(files: Files, dir: string, budget: Budget): string[] {
  return listed(files, dir, budget)
    .filter((entry) => entry.kind === "file" && entry.path.endsWith(".md"))
    .map((entry) => entry.path);
}

/** Every file below `dir` at any depth, skipping dot paths and `node_modules` (a glob's). */
function allFiles(files: Files, dir: string, budget: Budget, depth = 0): string[] {
  const out: string[] = [];
  for (const entry of listed(files, dir, budget)) {
    if (entry.kind === "file") {
      out.push(entry.path);
    } else if (depth < WALK_DEPTH && walked(nameOf(entry.path))) {
      out.push(...allFiles(files, entry.path, budget, depth + 1));
    }
  }
  return out;
}

// -- pi's filter grammar (pi-coding-agent 1.4.0 `package-manager.js`, MIT): ported.

/** A glob as minimatch reads its common forms: `**` any depth, `*` and `?` within a part. */
export function globMatch(text: string, pattern: string): boolean {
  let source = "";
  for (let at = 0; at < pattern.length; at++) {
    const char = pattern[at]!;
    if (char === "*" && pattern[at + 1] === "*") {
      const slash = pattern[at + 2] === "/";
      source += slash ? "(?:.*/)?" : ".*";
      at += slash ? 2 : 1;
    } else if (char === "*") {
      source += "[^/]*";
    } else if (char === "?") {
      source += "[^/]";
    } else {
      source += char.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${source}$`).test(text);
}

/** `path` relative to `base`, POSIX. */
function relativeTo(base: string, path: string): string {
  return path === base ? "" : path.startsWith(`${base}/`) ? path.slice(base.length + 1) : path;
}

/** A pattern as pi normalizes it: no leading `./`. */
function normalized(pattern: string): string {
  return pattern.startsWith("./") ? pattern.slice(2) : pattern;
}

/** The forms a file is matched by: its relative path, name and path, and a skill's directory's. */
function forms(path: string, base: string): { files: string[]; skill: string[] } {
  const files = [relativeTo(base, path), nameOf(path), path];
  if (nameOf(path) !== SKILL_FILE) {
    return { files, skill: [] };
  }
  const dir = parentOf(path);
  return { files, skill: [relativeTo(base, dir), nameOf(dir), dir] };
}

function matchesAny(path: string, patterns: readonly string[], base: string): boolean {
  const { files, skill } = forms(path, base);
  return patterns.some((pattern) => [...files, ...skill].some((form) =>
    globMatch(form, normalized(pattern))));
}

function matchesExact(path: string, patterns: readonly string[], base: string): boolean {
  const { files, skill } = forms(path, base);
  const exact = [files[0]!, files[2]!, ...(skill.length === 0 ? [] : [skill[0]!, skill[2]!])];
  return patterns.some((pattern) => exact.includes(normalized(pattern)));
}

/**
 * pi's `applyPatterns`: plain patterns include (all, when there are none), `!pattern`
 * excludes, `+path` forces an exact path back in, `-path` forces one out.
 */
export function applyPatterns(paths: readonly string[], patterns: readonly string[],
                              base: string): Set<string> {
  const includes = patterns.filter((p) => !/^[!+-]/.test(p));
  const excludes = patterns.filter((p) => p.startsWith("!")).map((p) => p.slice(1));
  const forced = patterns.filter((p) => p.startsWith("+")).map((p) => p.slice(1));
  const dropped = patterns.filter((p) => p.startsWith("-")).map((p) => p.slice(1));
  let result = includes.length === 0 ? [...paths]
    : paths.filter((path) => matchesAny(path, includes, base));
  if (excludes.length > 0) {
    result = result.filter((path) => !matchesAny(path, excludes, base));
  }
  for (const path of paths) {
    if (forced.length > 0 && !result.includes(path) && matchesExact(path, forced, base)) {
      result.push(path);
    }
  }
  if (dropped.length > 0) {
    result = result.filter((path) => !matchesExact(path, dropped, base));
  }
  return new Set(result);
}

/** Whether a manifest entry is one of the grammar's overrides rather than a path. */
function override(entry: string): boolean {
  return /^[!+-]/.test(entry);
}

// -- Packages.

/** A package's resources and its hooks with their modules' text, or why it is refused. */
type Loaded =
  | { readonly skills: readonly string[]; readonly templates: readonly string[];
      readonly skipped: readonly string[]; readonly hooks: readonly HookEntry[];
      readonly modules: Readonly<Record<string, string>> }
  | { readonly refused: string };

/**
 * The text of each hook module `entries` name, by path: each a file the owner's own
 * authority wrote, UTF-8, within the bundle's bound; else `Invalid`.
 */
async function hookModules(files: Files, entries: readonly HookEntry[]):
    Promise<Record<string, string>> {
  const modules: Record<string, string> = {};
  for (const { module } of entries) {
    if (Object.hasOwn(modules, module)) {
      continue;
    }
    const read = await ownedText(files, module, BUNDLE_MAX);
    if (read === null || "skipped" in read) {
      throw new Invalid(`the hook module ${module} is ${read === null ? "missing"
        : read.skipped}`);
    }
    modules[module] = read.text;
  }
  return modules;
}

/** The size of `modules`, in bytes of UTF-8. */
function bytesOf(modules: Readonly<Record<string, string>>): number {
  const encoder = new TextEncoder();
  return Object.values(modules).reduce((sum, text) => sum + encoder.encode(text).byteLength, 0);
}

/** A selected package's extension: `pkg:` and its directory below the packages tree. */
export function packageExtension(source: string): string {
  return `${PACKAGE_EXTENSION}${source.slice(PACKAGES_DIR.length + 1)}`;
}

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A manifest list: at most `MANIFEST_ENTRIES` strings, else invalid. */
function entriesOf(value: unknown, what: string): readonly string[] | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value) || value.length > MANIFEST_ENTRIES
      || value.some((entry) => typeof entry !== "string" || entry === "")) {
    throw new Invalid(`${what} is a list of at most ${MANIFEST_ENTRIES} paths`);
  }
  return value as string[];
}

/**
 * The files a package's manifest list names for one kind, as pi collects them: each path,
 * or each glob's matches, within the package; a directory's skills or templates, a file
 * itself; then the list's own overrides applied. With no list (or an empty one), the
 * conventional directory (`skills/`, `prompts/`).
 */
function manifestFiles(files: Files, root: string, kind: "skills" | "prompts",
                       entries: readonly string[] | undefined, budget: Budget): string[] {
  const collect = (path: string): string[] => {
    const entry = files.stat(path);
    if (entry === null) {
      return [];
    }
    if (entry.kind === "dir") {
      return kind === "skills" ? skillFiles(files, path, budget)
        : templateFiles(files, path, budget);
    }
    return kind === "skills" ? (nameOf(path) === SKILL_FILE ? [path] : [])
      : (path.endsWith(".md") ? [path] : []);
  };
  if (entries === undefined || entries.length === 0) {
    return collect(`${root}/${kind}`);
  }
  let everything: string[] | undefined;
  const found: string[] = [];
  for (const entry of entries.filter((item) => !override(item))) {
    if (/[*?]/.test(entry)) {
      everything ??= allFiles(files, root, budget);
      const pattern = normalized(entry);
      const roots = everything.filter((path) => globMatch(relativeTo(root, path), pattern));
      found.push(...roots.flatMap((path) => collect(path)));
      // A glob naming directories (`skills/*`) matches them by the files below them.
      const dirs = new Set<string>();
      for (const path of everything) {
        for (let dir = parentOf(path); dir !== root && within(dir, root); dir = parentOf(dir)) {
          if (globMatch(relativeTo(root, dir), pattern)) {
            dirs.add(dir);
          }
        }
      }
      found.push(...[...dirs].flatMap((dir) => collect(dir)));
      continue;
    }
    const path = resolve(root, entry);
    if (!within(path, root)) {
      throw new Invalid(`pi.${kind} names ${entry}, outside the package`);
    }
    found.push(...collect(path));
  }
  const unique = [...new Set(found)];
  const overrides = entries.filter(override);
  if (overrides.length === 0) {
    return unique;
  }
  const enabled = applyPatterns(unique, overrides, root);
  return unique.filter((path) => enabled.has(path));
}

/**
 * A selected package's skills and templates, filtered by the owner's selection, or why it
 * is refused: no such directory, a manifest that is not the owner's JSON object, or code
 * declared (`package_executable`).
 */
export async function loadPackage(files: Files, selection: PackageSelection):
    Promise<Loaded> {
  const root = selection.source;
  if (files.stat(root)?.kind !== "dir") {
    return { refused: `${PACKAGE_INVALID}: no such package directory` };
  }
  const read = await ownedText(files, `${root}/${MANIFEST_FILE}`);
  let manifest: Record<string, unknown> = {};
  if (read !== null) {
    if ("skipped" in read) {
      return { refused: `${PACKAGE_INVALID}: ${MANIFEST_FILE} is ${read.skipped}` };
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(read.text);
    } catch {
      return { refused: `${PACKAGE_INVALID}: ${MANIFEST_FILE} is not JSON` };
    }
    if (!plain(parsed)) {
      return { refused: `${PACKAGE_INVALID}: ${MANIFEST_FILE} is not an object` };
    }
    manifest = parsed;
  }
  const pi = manifest["pi"] ?? {};
  const ours = manifest["tinyassets"] ?? {};
  if (!plain(pi) || !plain(ours)) {
    return { refused: `${PACKAGE_INVALID}: the pi and tinyassets keys are objects` };
  }
  const budget: Budget = { left: WALK_MAX, truncated: false };
  const skipped: string[] = [];
  try {
    // Code is refused whole: any npm key but its data (an entry point, a binary, a script),
    // any `pi` key but the data the
    // object loads (an empty `extensions` list declares none), or, with none listed, an
    // extensions directory.
    const entry = Object.keys(manifest).find((key) => !NPM_DATA.includes(key));
    if (entry !== undefined) {
      return { refused: `${PACKAGE_EXECUTABLE}: ${entry} is code, or no data the object ` +
                        "loads, and the owner object runs no package code" };
    }
    for (const [key, value] of Object.entries(pi)) {
      if (PI_DATA.includes(key) || (key === PI_CODE && Array.isArray(value)
                                    && value.length === 0)) {
        continue;
      }
      if (PI_UNLOADED.includes(key)) {
        skipped.push(`pi.${key}`);
        continue;
      }
      return { refused: `${PACKAGE_EXECUTABLE}: pi.${key} is code, or no data the object ` +
                        "loads, and the owner object runs no package code" };
    }
    if (listed(files, `${root}/extensions`, budget).length > 0) {
      return { refused: `${PACKAGE_EXECUTABLE}: its extensions directory is code, and the ` +
                        "owner object runs no package code" };
    }
    const skills = manifestFiles(files, root, "skills", entriesOf(pi["skills"], "pi.skills"),
                                 budget);
    const templates = manifestFiles(files, root, "prompts",
                                    entriesOf(pi["prompts"], "pi.prompts"), budget);
    const pick = (paths: string[], filter: readonly string[] | undefined) => {
      if (filter === undefined || filter.length === 0) {
        return filter === undefined ? paths : [];
      }
      const enabled = applyPatterns(paths, filter, root);
      return paths.filter((path) => enabled.has(path));
    };
    skipped.push(...Object.keys(ours).filter((key) => key !== OURS_LOADED)
      .map((key) => `tinyassets.${key}`));
    // Hooks run only remotely, each module a file of this package.
    const hooks = ours[OURS_LOADED] === undefined ? []
      : hookEntries(ours[OURS_LOADED], (module) => {
        const path = resolve(root, module);
        if (!within(path, root) || path === root) {
          throw new Invalid(`tinyassets.hooks names ${module}, outside the package`);
        }
        return path;
      });
    const modules = await hookModules(files, hooks);
    if (budget.truncated) {
      skipped.push(`files past the first ${WALK_MAX}`);
    }
    return { skills: pick(skills, selection.skills), templates: pick(templates,
                                                                     selection.prompts),
             skipped, hooks, modules };
  } catch (error) {
    if (error instanceof Invalid) {
      return { refused: `${PACKAGE_INVALID}: ${error.message}` };
    }
    throw error;
  }
}

// -- The section, in pi's words (`formatSkillsForPrompt`).

function escapeXml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&apos;");
}

/** The skills section: each skill's name, description and path, as given; none: none. */
export function skillsSection(visible: readonly Skill[]): string | undefined {
  if (visible.length === 0) {
    return undefined;
  }
  const lines = [
    "The following skills provide specialized instructions for specific tasks.",
    "Use the read tool to load a skill's file when the task matches its description.",
    "When a skill file references a relative path, resolve it against the skill directory " +
      "(parent of SKILL.md / dirname of the path) and use that absolute path in tool " +
      "commands.",
    "",
    "<available_skills>",
  ];
  for (const skill of visible) {
    lines.push("  <skill>", `    <name>${escapeXml(skill.name)}</name>`,
               `    <description>${escapeXml(skill.description)}</description>`,
               `    <location>${escapeXml(skill.path)}</location>`, "  </skill>");
  }
  lines.push("</available_skills>");
  return lines.join("\n");
}

/**
 * The extension that carries `section` (none: no section), rendered for a conversation only
 * when `readable` says its authority may read the owner's files, where the skills are: a
 * client granted no `read_files` is shown none of the owner's skills.
 */
export function skillsExtension(section: string | undefined,
                                readable: (input: PromptInput, context: Context) =>
                                  Promise<boolean>): Extension {
  return defineExtension({
    name: SKILLS_EXTENSION,
    sections: section === undefined ? [] : [{
      key: SKILLS_SECTION, tag: false,
      render: async (input, context) => (await readable(input, context) ? section : undefined),
    }],
  });
}

// -- Expansion, as pi's `prompt` expands an input (pi-coding-agent 1.4.0, MIT).

/** pi's `parseCommandArgs`: shell-like words, quotes kept together. */
export function commandArgs(text: string): string[] {
  const args: string[] = [];
  let current = "";
  let token = false;
  let quote: string | null = null;
  for (const char of text) {
    if (quote !== null) {
      if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
    } else if (char === "\"" || char === "'") {
      quote = char;
      token = true;
    } else if (/\s/.test(char)) {
      if (token) {
        args.push(current);
        current = "";
        token = false;
      }
    } else {
      current += char;
      token = true;
    }
  }
  if (token) {
    args.push(current);
  }
  return args;
}

/** pi's `substituteArgs`, on the template's text only. */
export function substituteArgs(content: string, args: readonly string[]): string {
  const all = args.join(" ");
  return content.replace(/\$\{(\d+|ARGUMENTS|@):-([^}]*)\}|\$\{@:(\d+)(?::(\d+))?\}|\$(ARGUMENTS|@|\d+)/g,
    (_match, target?: string, fallback?: string, start?: string, length?: string,
     simple?: string) => {
      if (target !== undefined) {
        const value = target === "@" || target === "ARGUMENTS" ? all
          : args[Number.parseInt(target, 10) - 1];
        return value ? value : fallback ?? "";
      }
      if (start !== undefined) {
        const from = Math.max(0, Number.parseInt(start, 10) - 1);
        return length === undefined ? args.slice(from).join(" ")
          : args.slice(from, from + Number.parseInt(length, 10)).join(" ");
      }
      if (simple === "ARGUMENTS" || simple === "@") {
        return all;
      }
      return args[Number.parseInt(simple!, 10) - 1] ?? "";
    });
}

const FRONTMATTER = /^﻿?---[ \t]*\r?\n(?:[\s\S]*?\r?\n)?---[ \t]*(?:\r?\n([\s\S]*))?$/;

/** A Markdown file's body: what follows its frontmatter, when it has one. */
export function bodyOf(text: string): string {
  const match = FRONTMATTER.exec(text);
  return match === null ? text : match[1] ?? "";
}

/** What the resources are built from. */
export interface ResourceParts {
  readonly files: Files;
  readonly notices: Notices;
  readonly write: Write;
  /** The owner's package selection, as policy holds it now. */
  readonly selection: () => readonly PackageSelection[];
  /** Install an extension on the object's registry, replacing one of its name. */
  readonly install: (extension: Extension) => void;
  /** Whether a request's conversation may read the owner's files: it is shown the skills. */
  readonly readable: (input: PromptInput, context: Context) => Promise<boolean>;
  /** The owner the object is named by: its bundle's id names it. */
  readonly owner: () => string;
  /** A package's `pkg:<name>` extension of remote hook wrappers, over the owner's bundle. */
  readonly remote: (bundle: Bundle, hooks: PackageHooks) => Extension;
  /** Remove the installed extension of a name: a package no longer selected, or hooked. */
  readonly uninstall: (name: string) => void;
}

/**
 * The agent's skills and templates for one object instance: built at the first open, rebuilt
 * from the files' change feed, installed as `ta.skills`, and expanding the owner's inputs.
 */
export class Resources {
  readonly #parts: ResourceParts;
  #index: Index = EMPTY;
  #ready: Promise<void> | undefined;
  #building: Promise<void> | undefined;
  #next: Promise<void> | undefined;
  #stop: (() => void) | undefined;
  /** The selection the index was built from, as JSON, and its packages' directories. */
  #selected = "[]";
  #packages: readonly string[] = [];
  /** The packages' hook extensions installed by the last build, by name. */
  #hooked: readonly string[] = [];
  /** How many times the section was built and installed on this instance (a diagnostic). */
  #builds = 0;

  constructor(parts: ResourceParts) {
    this.#parts = parts;
  }

  /** How many builds this instance installed. */
  get builds(): number {
    return this.#builds;
  }

  /** The index as last built. */
  get index(): Index {
    return this.#index;
  }

  /**
   * The first build, once per instance (again after one that failed), with the change feed
   * heard from before it reads, so no change lands unheard between them.
   */
  ready(): Promise<void> {
    this.#stop ??= this.#parts.files.watch((paths) => {
      if (this.#relevant(paths)) {
        this.rebuild();
      }
    });
    this.#ready ??= this.#serial().catch((error: unknown) => {
      this.#ready = undefined;
      throw error;
    });
    return this.#ready;
  }

  /** The owner's package selection may have changed (a policy write): rebuild if it did. */
  selectionChanged(): void {
    if (this.#ready !== undefined
        && JSON.stringify(this.#parts.selection()) !== this.#selected) {
      this.rebuild();
    }
  }

  /**
   * Rebuild now, or once more after the build in flight; before the first build is asked
   * for, nothing (it reads everything). A failed one is told loudly; the last build stands.
   */
  rebuild(): void {
    if (this.#ready === undefined) {
      return;
    }
    this.#serial().catch(() => {
      console.error("the agent's skills could not be rebuilt; the last build stands");
      this.#notice(RESOURCES_UNBUILT, "the skills section could not be rebuilt; the last " +
                   "build stands until the next change");
    });
  }

  /**
   * One build at a time: one asked for while another runs runs once after it, for every ask
   * made meanwhile, so an older index never replaces a newer one.
   */
  #serial(): Promise<void> {
    if (this.#next !== undefined) {
      return this.#next;
    }
    if (this.#building === undefined) {
      const building = this.#build().finally(() => {
        this.#building = undefined;
      });
      this.#building = building;
      return building;
    }
    const next = this.#building.catch(() => undefined).then(() => {
      this.#next = undefined;
      return this.#serial();
    });
    this.#next = next;
    return next;
  }

  /** Settles once no build is in flight or queued (a test's, and a door's that expands). */
  async settled(): Promise<void> {
    while (this.#building !== undefined || this.#next !== undefined) {
      await (this.#next ?? this.#building)!.catch(() => undefined);
    }
  }

  /**
   * Whether a committed change's paths touch what the index is built from: a `SKILL.md` or a
   * template under a root, anything under a selected package's directory, or a directory
   * above any of them. The selection is the last build's: a policy write that moves it
   * rebuilds on its own (`selectionChanged`).
   */
  #relevant(paths: readonly string[]): boolean {
    const packages = this.#packages;
    const above = (path: string, roots: readonly string[]) =>
      roots.some((root) => within(root, path));
    return paths.some((path) =>
      (nameOf(path) === SKILL_FILE && SKILL_ROOTS.some((root) => within(path, root)))
      || (path.endsWith(".md") && PROMPT_ROOTS.some((root) => within(path, root)))
      || packages.some((root) => within(path, root))
      || above(path, [...SKILL_ROOTS, ...PROMPT_ROOTS, ...packages]));
  }

  #notice(type: string, detail: string): void {
    this.#parts.write(() => this.#parts.notices.once(type, detail, Date.now() / 1000))
      .catch(() => console.error("a resources notice could not be kept"));
  }

  /** Read everything, then install the section and keep the index. */
  async #build(): Promise<void> {
    const { files } = this.#parts;
    const selection = this.#parts.selection();
    const selected = JSON.stringify(selection);
    // Heard from now: a change under a package being read rebuilds after this build.
    this.#packages = selection.map((chosen) => chosen.source);
    const skillPaths: string[] = [];
    const templatePaths: string[] = [];
    const hooks: PackageHooks[] = [];
    const modules: Record<string, string> = {};
    let truncated = false;
    for (const root of SKILL_ROOTS) {
      const budget: Budget = { left: WALK_MAX, truncated: false };
      skillPaths.push(...skillFiles(files, root, budget));
      truncated ||= budget.truncated;
    }
    for (const root of PROMPT_ROOTS) {
      const budget: Budget = { left: WALK_MAX, truncated: false };
      templatePaths.push(...templateFiles(files, root, budget));
      truncated ||= budget.truncated;
    }
    for (const chosen of selection) {
      const loaded = await loadPackage(files, chosen);
      if ("refused" in loaded) {
        this.#notice(PACKAGE_REFUSED, `${chosen.source}: ${loaded.refused}`);
        continue;
      }
      if (bytesOf({ ...modules, ...loaded.modules }) > BUNDLE_MAX) {
        this.#notice(PACKAGE_REFUSED, `${chosen.source}: ${PACKAGE_INVALID}: its hook modules ` +
                     `take the owner's bundle past ${BUNDLE_MAX} bytes`);
        continue;
      }
      if (loaded.hooks.length > 0) {
        Object.assign(modules, loaded.modules);
        hooks.push({ extension: packageExtension(chosen.source), entries: loaded.hooks });
      }
      skillPaths.push(...loaded.skills);
      templatePaths.push(...loaded.templates);
      for (const field of loaded.skipped) {
        this.#notice(PACKAGE_FIELD_SKIPPED, `${chosen.source}: ${field} is not loaded`);
      }
    }
    const skills: Skill[] = [];
    for (const path of skillPaths) {
      const read = await ownedText(files, path);
      if (read === null) {
        continue;
      }
      if ("skipped" in read) {
        this.#notice(SKILL_SKIPPED, `${path}: ${read.skipped}`);
        continue;
      }
      const dir = parentOf(path);
      const parsed = parseSkill(read.text, { directory: nameOf(dir) });
      if (!parsed.ok) {
        this.#notice(SKILL_SKIPPED, `${path}: ${parsed.error.message}`);
        continue;
      }
      const taken = skills.find((skill) => skill.name === parsed.value.name);
      if (taken !== undefined) {
        this.#notice(SKILL_SKIPPED, `${path}: the name ${parsed.value.name} is ${taken.path}'s`);
        continue;
      }
      skills.push({ name: parsed.value.name, path, dir,
                    description: [...parsed.value.description].slice(0, DESCRIPTION_MAX)
                      .join(""),
                    invocable: isModelInvocable(parsed.value) });
    }
    const templates: Template[] = [];
    for (const path of templatePaths) {
      const name = nameOf(path).slice(0, -".md".length);
      if (!templates.some((template) => template.name === name)) {
        templates.push({ name, path });
      }
    }
    // Only a model-invocable skill is listed; any skill expands by its command.
    const listed = skills.filter((skill) => skill.invocable);
    if (truncated || listed.length > SKILLS_LISTED || templates.length > TEMPLATES_KEPT) {
      this.#notice(RESOURCES_TRUNCATED, `only the first ${SKILLS_LISTED} skills, ` +
                   `${TEMPLATES_KEPT} templates and ${WALK_MAX} entries a root are read`);
    }
    const kept = listed.slice(0, SKILLS_LISTED);
    const bundle = hooks.length === 0 ? null : bundleOf(this.#parts.owner(), modules);
    const index: Index = {
      skills: skills.filter((skill) => !skill.invocable || kept.includes(skill)),
      templates: templates.slice(0, TEMPLATES_KEPT),
      section: skillsSection(kept),
      hooks, bundle,
    };
    this.#parts.install(skillsExtension(index.section, this.#parts.readable));
    // Each hooked package's wrappers, in place; a package no longer hooked, gone.
    const named = hooks.map((hooked) => hooked.extension);
    for (const hooked of hooks) {
      this.#parts.install(this.#parts.remote(bundle!, hooked));
    }
    for (const name of this.#hooked.filter((name) => !named.includes(name))) {
      this.#parts.uninstall(name);
    }
    this.#hooked = named;
    this.#index = index;
    this.#selected = selected;
    this.#builds++;
  }

  /**
   * The owner's input as pi's `prompt` expands it: `/skill:name args` to the skill's body in
   * pi's `<skill>` block with its arguments after, then `/name args` to a template's body
   * with its arguments substituted; anything else, and an unknown name, as typed. A skill
   * or template that cannot be read now is refused loudly (`skill_unreadable`).
   */
  async expand(text: string): Promise<string> {
    if (!text.startsWith("/")) {
      return text;
    }
    if (this.#ready === undefined) {
      throw new Error("the agent's resources expand only once they are built");
    }
    await this.#ready;
    await this.settled();
    let expanded = text;
    if (expanded.startsWith("/skill:")) {
      const space = expanded.indexOf(" ");
      const name = space === -1 ? expanded.slice(7) : expanded.slice(7, space);
      const args = space === -1 ? "" : expanded.slice(space + 1).trim();
      const skill = this.#index.skills.find((found) => found.name === name);
      if (skill !== undefined) {
        const read = await ownedText(this.#parts.files, skill.path);
        if (read === null || "skipped" in read) {
          throw new Refused(`${SKILL_UNREADABLE}: ${skill.path} is ${read === null ? "gone"
            : read.skipped}`);
        }
        const block = `<skill name="${skill.name}" location="${skill.path}">\n` +
          `References are relative to ${skill.dir}.\n\n${bodyOf(read.text).trim()}\n</skill>`;
        expanded = args === "" ? block : `${block}\n\n${args}`;
      }
    }
    const match = /^\/([^\s]+)(?:\s+([\s\S]*))?$/.exec(expanded);
    const template = match === null ? undefined
      : this.#index.templates.find((found) => found.name === match[1]);
    if (template === undefined) {
      return expanded;
    }
    const read = await ownedText(this.#parts.files, template.path);
    if (read === null || "skipped" in read) {
      throw new Refused(`${SKILL_UNREADABLE}: ${template.path} is ${read === null ? "gone"
        : read.skipped}`);
    }
    return substituteArgs(bodyOf(read.text), commandArgs(match![2] ?? ""));
  }

  /** Stop hearing the change feed (the object's instance ends). */
  dispose(): void {
    this.#stop?.();
    this.#stop = undefined;
  }
}

/** Whether `value` is a filter list: at most 64 patterns of 1 to 512 characters. */
export function filterList(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.length <= MANIFEST_ENTRIES
    && value.every((entry) => typeof entry === "string" && entry !== ""
                              && codePoints(entry) <= 512);
}
