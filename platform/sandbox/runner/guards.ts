/**
 * The runner's refusals over what an agent's files hold, each pure (the runner reads the
 * files, `runner.ts`):
 *
 * - **The definition** (`sources/src/schema.ts` `Definition`): a run reads its agent's
 *   definition and nothing beside it. It is refused while its cwd or an ancestor under
 *   `/workspace` holds a file the engine would read instead of the agent's instructions
 *   (`instructions.shadowed_by`) or as its own layers (`native.refuses`), or while the
 *   instructions there hold more than the engine reads (`instructions.max_bytes`), which it
 *   would cut unseen.
 * - **Project env files** (tasks, "Slice S verifies each engine record live"): a run's
 *   environment is the allow-list alone, but an agent can write the files a CLI loads
 *   variables or a credential helper from in its working directory (Claude's
 *   `.claude/settings.json` `env` and `apiKeyHelper`, Gemini's `.env` and `.gemini/.env`,
 *   any CLI's own). The runner reads every `.env` file at the agent's top and one dot
 *   directory down, and every JSON settings file one dot directory down, and refuses the
 *   run when one sets a variable its credential forbids, or any credential-, endpoint- or
 *   proxy-shaped one, or names a credential helper. That last holds on a key run too:
 *   a project's own key or base URL would take the run's model calls past the egress
 *   door, and with them past the owner's caps.
 * - **The content backstop** (doors spec, "The runner never syncs a secret's bytes"): no
 *   synced file or transcript holds the bytes of a `secret_paths` file. A secret's atoms
 *   are its JSON string values (and their JSON-escaped forms) and its lines, each of at
 *   least `ATOM_MIN` characters; a file holding any is refused whole, whatever path it was
 *   written to.
 */
import { dirname } from "node:path";

import { WORKSPACE } from "../../contract/src/paths";
import { CREDENTIAL_NAME, forbids } from "../../contract/src/text";
import type { Definition } from "../../sources/src/schema";

/** Why a run is refused for what its engine would read beside its definition, or cut of it. */
export type DefinitionRefusal =
  | { readonly code: "instructions_shadowed" | "engine_settings_refused";
      readonly path: string }
  | { readonly code: "instructions_too_large"; readonly paths: readonly string[];
      readonly bytes: number; readonly max_bytes: number };

/**
 * Why the run is refused, from what its cwd and each of its ancestors under `/workspace` hold
 * (`bytesOf`: a path's size, null when nothing is there), or null. The instructions are counted
 * together, as an engine that reads every copy gets them.
 */
export function definitionRefusal(definition: Definition, cwd: string,
                                  bytesOf: (path: string) => number | null):
    DefinitionRefusal | null {
  const dirs = [cwd];
  for (let dir = dirname(cwd); dir === WORKSPACE || dir.startsWith(`${WORKSPACE}/`);
       dir = dirname(dir)) {
    dirs.push(dir);
  }
  const { instructions, native } = definition;
  for (const [code, names] of [["instructions_shadowed", instructions.shadowed_by],
                               ["engine_settings_refused", native.refuses]] as const) {
    for (const path of dirs.flatMap((dir) => names.map((name) => `${dir}/${name}`))) {
      if (bytesOf(path) !== null) {
        return { code, path };
      }
    }
  }
  if (instructions.max_bytes === undefined) {
    return null;
  }
  const read = dirs.map((dir) => `${dir}/${instructions.file}`)
    .flatMap((path) => {
      const bytes = bytesOf(path);
      return bytes === null ? [] : [{ path, bytes }];
    });
  const bytes = read.reduce((total, each) => total + each.bytes, 0);
  return bytes <= instructions.max_bytes ? null
    : { code: "instructions_too_large", paths: read.map((each) => each.path), bytes,
        max_bytes: instructions.max_bytes };
}

/** A settings key naming a credential helper. */
const HELPER_KEY = /helper/i;
/**
 * The shortest secret atom: the shortest credential any record's provider issues, a Google
 * API key (39 characters, `AIza` and 35 more; Anthropic's, OpenAI's and Moonshot's keys
 * and every engine's OAuth tokens are longer). A shorter string in a secret file (a flag, a
 * region, a model name, an account id) is no credential, and occurs in ordinary text, so
 * refusing it would refuse files that hold no secret.
 */
const ATOM_MIN = 39;

/** A `.env` file's variable names (`NAME=...`, `export NAME=...`). */
function dotenvNames(text: string): string[] {
  return text.split(/\r?\n/).flatMap((line) => {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/.exec(line);
    return match === null ? [] : [match[1]!];
  });
}

/** A JSON settings file's environment names and helper keys, anywhere in it. */
function settingsNames(text: string): { env: string[]; helpers: string[] } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  const env: string[] = [];
  const helpers: string[] = [];
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
    } else if (typeof node === "object" && node !== null) {
      for (const [key, value] of Object.entries(node)) {
        if (/^(?:env|environment)$/i.test(key) && typeof value === "object" && value !== null
            && !Array.isArray(value)) {
          env.push(...Object.keys(value));
        }
        if (HELPER_KEY.test(key)) {
          helpers.push(key);
        }
        walk(value);
      }
    }
  };
  walk(parsed);
  return { env, helpers };
}

/**
 * Whether a path, from a directory the env-file scan reads (the agent's, or one of the cwd's
 * ancestors), is within its reach: a dot entry at the top, or anything one dot directory
 * down. A CLI looks for its env and settings files there and no deeper.
 */
export function envScanReach(relative: string): boolean {
  const parts = relative.split("/");
  return parts.length <= 2 && parts[0]!.startsWith(".");
}

/** Whether a path, from the agent's directory, is one the scan reads. */
export function projectEnvFile(relative: string): "dotenv" | "settings" | null {
  if (!envScanReach(relative)) {
    return null;
  }
  const parts = relative.split("/");
  const name = parts.at(-1)!;
  if (name === ".env" || name.startsWith(".env.")) {
    return "dotenv";
  }
  return parts.length === 2 && name.endsWith(".json") ? "settings" : null;
}

/** Why a run is refused for its project files: the file, and the helper or name it sets. */
export type ProjectEnvRefusal = { readonly code: "project_env_refused"; readonly path: string }
  & ({ readonly helper: string } | { readonly sets: string });

/**
 * Why the run is refused, from its agent's project files (`files`: path from the agent's
 * directory to text, only those `projectEnvFile` names), or null: its code and facts, which
 * the app says in words.
 */
export function projectEnvRefusal(files: ReadonlyMap<string, string>,
                                  forbid: readonly string[],
                                  kindOf: (path: string) => "dotenv" | "settings" | null
                                    = projectEnvFile): ProjectEnvRefusal | null {
  for (const [path, text] of files) {
    const kind = kindOf(path);
    if (kind === null) {
      continue;
    }
    let names: string[];
    if (kind === "dotenv") {
      names = dotenvNames(text);
    } else {
      const settings = settingsNames(text);
      if (settings === null) {
        continue;  // not JSON: no CLI loads variables from it as settings
      }
      if (settings.helpers.length > 0) {
        return { code: "project_env_refused", path, helper: settings.helpers[0]! };
      }
      names = settings.env;
    }
    const bad = names.find((name) => forbids(forbid, name)
      || CREDENTIAL_NAME.test(name.toUpperCase()));
    if (bad !== undefined) {
      return { code: "project_env_refused", path, sets: bad };
    }
  }
  return null;
}

/**
 * Whether a path in the run's home (its CLIs' user-level settings: Claude's
 * `$CLAUDE_CONFIG_DIR/settings.json` and `.claude.json`, Gemini's `settings.json`, any
 * CLI's own, at any depth) is one the scan reads: every `.json` file as settings, every
 * `.env` file as one. The home holds only what the engine's store keeps between runs (its
 * `secret_paths` and `session_paths`) and the run's own config files.
 */
export function homeEnvFile(relative: string): "dotenv" | "settings" | null {
  const name = relative.split("/").at(-1)!;
  if (name === ".env" || name.startsWith(".env.")) {
    return "dotenv";
  }
  return name.endsWith(".json") ? "settings" : null;
}

/**
 * Whether a path, from one of the cwd's ancestors (a CLI searches upward from its cwd for
 * an env file: Gemini CLI's `.env` and `.gemini/.env`), is one the scan reads: the
 * ancestor's own `.env` files, and those one dot directory down.
 */
export function ancestorEnvFile(relative: string): "dotenv" | null {
  const name = relative.split("/").at(-1)!;
  return envScanReach(relative) && (name === ".env" || name.startsWith(".env.")) ? "dotenv"
    : null;
}

/** A secret file's atoms: its JSON string values or its lines, long enough to be one. */
export function secretAtoms(text: string): string[] {
  const atoms = new Set<string>();
  const add = (value: string) => {
    const trimmed = value.trim();
    if (trimmed.length >= ATOM_MIN) {
      atoms.add(trimmed);
      const escaped = JSON.stringify(trimmed).slice(1, -1);
      if (escaped !== trimmed) {
        atoms.add(escaped);
      }
    }
  };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = undefined;
  }
  if (parsed !== undefined) {
    const walk = (node: unknown): void => {
      if (typeof node === "string") {
        add(node);
      } else if (Array.isArray(node)) {
        node.forEach(walk);
      } else if (typeof node === "object" && node !== null) {
        Object.values(node).forEach(walk);
      }
    };
    walk(parsed);
  } else {
    for (const line of text.split(/\r?\n/)) {
      // A `key = "value"` line's value, and the line itself.
      const value = /=\s*"?([^"]*)"?\s*$/.exec(line)?.[1];
      if (value !== undefined) {
        add(value);
      }
      add(line);
    }
  }
  return [...atoms];
}

/** The first secret atom `content` holds, or null: a file holding one is never synced. */
export function heldSecret(content: string, atoms: readonly string[]): string | null {
  return atoms.find((atom) => content.includes(atom)) ?? null;
}
