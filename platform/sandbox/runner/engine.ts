/**
 * One run's invocation, from its engine's source record as data (ADR-034 decision 4.3;
 * `sources/src/schema.ts`'s `CliSpec`): the argv, stdin, environment, cwd and the files
 * the record's templates make. Pure: the runner (`runner.ts`) does the I/O. No provider is
 * named here; every difference is the record's.
 *
 * - The environment is the run's allow-list alone (`config.env`, the lease's `runEnv` and
 *   `sandboxEnv`), its templates filled, and the process basics it keeps (`config.inherit`)
 *   from the runner's own; nothing else of the sandbox's environment reaches the CLI. A
 *   variable the run's credential forbids (`forbid_env`) fails the run loudly.
 * - The argv: the binary, then the credential's own arguments, the model (each option
 *   joined to its value, `--model=<id>`, so a reported id that starts with `-` is never a
 *   flag), the unattended flags on an unattended run, what keeps the engine's own settings
 *   layers out (`definition.native.argv`, every run), the agent's skills where the engine
 *   takes each by its argv (`definition.skills.argv_each`), the MCP arguments, the update
 *   switch, then the record's `argv` or `resume_argv` past its binary. The prompt goes on stdin
 *   where the record says so, or joined to its option (`--prompt={prompt}`), never bare.
 * - The run's MCP door is `urls.mcp`, or `urls.mcp_bare` where the record's template sends
 *   the run's secret as a header (`{run_secret}`), so the secret is out of the URL.
 * - Files: the record's own templates, the credential's, and the MCP template, each filled
 *   (a `.json`, `.toml` or `.mjs` file's values escaped as a quoted string's), several
 *   templates of one JSON file merged.
 * - The CLI's own pre-tool hook (R5 P-hooks), where its record has one: the run's decide
 *   credential in its environment (`DECIDE_ENV`), and, when the owner's rules name tools it
 *   matches (`hook.tools`), its file filled from the record's template (`{matcher}` those
 *   tools; the door's address, its header and the credential's variable; the helper's command
 *   line and its argv, the door their argument), what the record adds to its argv, and its
 *   pinned settings files in the home (`pins`, root's like every run config file). A file
 *   every run of the engine
 *   shares (under `/etc`) is the runner's to write, as `shared`: null text when no tool is
 *   matched, so the latest run's start leaves the owner's latest rules there; null for a call
 *   given no hook.
 */
import type { CliSpec } from "../../sources/src/schema";
import type { Served } from "../../sources/src/served";
import { DECIDE_HEADER, decideDoor, egressDoor, envStem } from "../../contract/src/egress";
import { AGENTS_DIR } from "../../contract/src/paths";
import { forbids } from "../../contract/src/text";

import { DECIDE_ENV, helperArgv, helperCommand, matcherOf } from "./decide";
import type { StartDoc } from "./protocol";

export const ENGINE_ROOT = "/opt/ta/engines";

/**
 * Where one run's own home is made (`{home}`): a fresh directory a run, owned by the run's
 * own user, filled from its engine's store and emptied back into it (`runner.ts`); and its
 * own root-owned directory (`{system}`), for the files a CLI takes only from a path root
 * owns at every level.
 */
export const HOMES = "/var/lib/ta-homes";
export const SYSTEM = "/etc/ta/runs";

/** The engine's binary as the template installs it. */
export function binPath(record: Served): string {
  const install = record.cli.install;
  return install.manager === "npm"
    ? `${ENGINE_ROOT}/${record.id}/node_modules/.bin/${install.bin}`
    : `${ENGINE_ROOT}/${record.id}/bin/${install.bin}`;
}

/**
 * `{name}` filled from `values`, each escaped as a JSON string's inside when `quoted` (which a
 * TOML or a JavaScript string takes too); a placeholder with no value fails loudly.
 */
export function fill(text: string, values: Readonly<Record<string, string>>,
                     quoted = false): string {
  return text.replace(/\{([a-z_]+)\}/g, (whole, name: string) => {
    if (!Object.hasOwn(values, name)) {
      throw new Error(`the record's ${whole} has no value in this run`);
    }
    const value = values[name]!;
    return quoted ? JSON.stringify(value).slice(1, -1) : value;
  });
}

/** The run's own secret, as its MCP URL's path carries it (`/r/<run>/<secret>/mcp`). */
function runSecret(mcpUrl: string): string {
  const door = egressDoor(new URL(mcpUrl).pathname);
  const secret = door?.door === "mcp" ? door.secret : undefined;
  if (secret === undefined) {
    throw new Error("a run's MCP URL carries its secret");
  }
  return secret;
}

/**
 * The provider an API-key run's connection is of: the one of the record's key providers
 * whose base-URL variable the run's environment sets (`<PROVIDER>_BASE_URL`, as
 * `sandboxEnv` names it), and that URL.
 */
function keyProvider(cli: CliSpec, env: Readonly<Record<string, string>>):
    { provider: string; base_url: string } | null {
  const found = Object.keys(cli.api_key_env ?? {}).filter((provider) =>
    Object.hasOwn(env, `${envStem(provider)}_BASE_URL`));
  if (found.length > 1) {
    throw new Error("an API-key run is on one provider's connection");
  }
  if (found.length === 0) {
    return null;
  }
  const provider = found[0]!;
  return { provider, base_url: env[`${envStem(provider)}_BASE_URL`]! };
}

/**
 * The record's model arguments, each option joined to its value (`--model=<id>`), so a
 * value is never read as a flag; a placeholder not after an option is refused when its
 * value could be read as one.
 */
function modelArgs(template: readonly string[], values: Readonly<Record<string, string>>):
    string[] {
  const out: string[] = [];
  for (let i = 0; i < template.length; i += 1) {
    const arg = template[i]!;
    const next = template[i + 1];
    if (arg.startsWith("--") && !arg.includes("=") && next !== undefined
        && /^\{[a-z_]+\}$/.test(next)) {
      out.push(`${arg}=${fill(next, values)}`);
      i += 1;
    } else {
      const filled = fill(arg, values);
      if (/^\{[a-z_]+\}$/.test(arg) && filled.startsWith("-")) {
        throw new Error("a value that reads as a flag is never a bare argument");
      }
      out.push(filled);
    }
  }
  return out;
}

/** Deep merge of JSON objects, `b` over `a`. */
function merged(a: unknown, b: unknown): unknown {
  if (typeof a === "object" && a !== null && !Array.isArray(a)
      && typeof b === "object" && b !== null && !Array.isArray(b)) {
    const out: Record<string, unknown> = { ...a as Record<string, unknown> };
    for (const [key, value] of Object.entries(b)) {
      out[key] = merged(out[key], value);
    }
    return out;
  }
  return b;
}

export interface Invocation {
  readonly argv: readonly string[];
  readonly stdin: string | null;
  readonly env: Readonly<Record<string, string>>;
  readonly cwd: string;
  /** Files to write before the CLI runs, by absolute path, owned by its user. */
  readonly files: ReadonlyMap<string, string>;
  /** Whether it resumes the conversation (its `resume_argv`). */
  readonly resumed: boolean;
  /** The hook file every run of the engine shares, by path, and its text (null: none). */
  readonly shared: { readonly path: string; readonly text: string | null } | null;
}

/**
 * The run's invocation. `inherited` is the runner's own environment, read only for the
 * basics the run keeps; `prompt` the input (a recovery's continuation, or the start's own).
 */
export function invocation(record: Served, start: StartDoc,
                           inherited: Readonly<Record<string, string | undefined>>,
                           options: { readonly prompt?: string;
                                      /** The run's own home and system directory. */
                                      readonly home: string; readonly system: string;
                                      /** The agent's skills' directories (`skillsOf`). */
                                      readonly skills: readonly string[];
                                      /**
                                       * A run's hook, where its record has one: the patterns
                                       * of the tools it matches (none: no hook) and the
                                       * helper's runner, by its digest. Absent, none is
                                       * written.
                                       */
                                      readonly hook?: { readonly tools: readonly string[];
                                                        readonly digest: string } }):
    Invocation {
  const cli = record.cli;
  const decide = "unsupported" in cli.decide ? null : cli.decide;
  const hooked = decide !== null && options.hook !== undefined && options.hook.tools.length > 0;
  if (!cli.credential.includes(start.credential)) {
    throw new Error(`engine ${record.id} does not run on ${start.credential}`);
  }
  const home = options.home;
  const system = options.system;
  const agentDir = `${AGENTS_DIR}/${start.agent}`;
  const mcp = "unsupported" in cli.mcp ? null : cli.mcp;
  const secretInHeader = mcp?.template?.includes("{run_secret}") === true;
  const values: Record<string, string> = {
    bin: binPath(record), home, system, agent: start.agent,
    mcp_url: secretInHeader ? start.urls.mcp_bare : start.urls.mcp,
    run_secret: runSecret(start.urls.mcp),
  };
  if (mcp?.file !== undefined) {
    values["mcp_config"] = fill(mcp.file, values);
  }
  if (start.model !== null) {
    values["model"] = start.model;
  }
  if (start.session_id !== null) {
    values["sid"] = start.session_id;
  }
  const key = start.credential === "api_key" ? keyProvider(cli, start.config.env) : null;
  if (key !== null) {
    values["provider"] = key.provider;
    values["base_url"] = key.base_url;
  }
  // The environment: the allow-list, filled, and the basics kept; nothing else.
  const forbidden = cli.per_credential?.[start.credential]?.forbid_env ?? [];
  const env: Record<string, string> = {};
  for (const name of start.config.inherit) {
    const value = inherited[name];
    if (value !== undefined) {
      env[name] = value;
    }
  }
  for (const [name, value] of Object.entries(start.config.env)) {
    env[name] = fill(value, values);
  }
  const door = decide === null ? null : decideDoor(start.urls.mcp);
  if (door !== null) {
    env[DECIDE_ENV] = door.credential;
  }
  const banned = Object.keys(env).find((name) => forbids(forbidden, name));
  if (banned !== undefined) {
    throw new Error(`the run's credential forbids ${banned}`);
  }
  if (env["HOME"] !== home) {
    throw new Error("a run's HOME is its engine home");
  }
  const resumed = start.session_id !== null;
  const base = resumed ? cli.engine.resume_argv : cli.engine.argv;
  const prompt = options.prompt ?? start.prompt;
  const promptValues = { ...values, prompt };
  const tail = base.slice(1).map((arg) => arg.includes("{prompt}")
    ? fill(arg, promptValues) : fill(arg, values));
  const credential = cli.per_credential?.[start.credential];
  const argv = [
    values["bin"]!,
    ...(credential?.argv ?? []).map((arg) => fill(arg, values)),
    ...(start.model === null ? [] : modelArgs(cli.engine.model_argv ?? [], values)),
    ...(start.unattended ? cli.unattended_argv.map((arg) => fill(arg, values)) : []),
    ...(cli.definition.native.argv ?? []).map((arg) => fill(arg, values)),
    ...options.skills.flatMap((skill) => (cli.definition.skills.argv_each ?? [])
      .map((arg) => fill(arg, { ...values, skill }))),
    ...(mcp === null ? [] : [...mcp.argv, ...mcp.trust.argv].map((arg) => fill(arg, values))),
    ...(hooked ? decide!.argv ?? [] : []).map((arg) => fill(arg, values)),
    ...("argv" in cli.no_update ? cli.no_update.argv ?? [] : []).map((arg) => fill(arg, values)),
    ...tail,
  ];
  const refusedArgs = credential?.forbid_argv ?? [];
  const bad = argv.find((arg) => refusedArgs.includes(arg));
  if (bad !== undefined) {
    throw new Error(`the run's credential forbids ${bad}`);
  }
  const hookValues = options.hook === undefined || door === null ? values : {
    ...values, matcher: matcherOf(options.hook.tools), decide_url: door.url,
    decide_header: DECIDE_HEADER, decide_env: DECIDE_ENV,
    decide: helperCommand(options.hook.digest, door.url),
    decide_argv: JSON.stringify(helperArgv(options.hook.digest, door.url)) };
  // The files: the record's, the credential's, the MCP template, then the run's own hook;
  // one JSON file's templates merged in that order.
  const files = new Map<string, string>();
  const write = (pathTemplate: string, template: string) => {
    const path = fill(pathTemplate, values);
    const json = path.endsWith(".json");
    const text = fill(template, hookValues, /\.(?:json|toml|mjs)$/.test(path));
    if (json && files.has(path)) {
      files.set(path, JSON.stringify(merged(JSON.parse(files.get(path)!), JSON.parse(text))));
    } else {
      files.set(path, json ? JSON.stringify(JSON.parse(text)) : text);
    }
  };
  for (const [path, template] of Object.entries(cli.files ?? {})) {
    write(path, template);
  }
  for (const [path, template] of Object.entries(credential?.files ?? {})) {
    write(path, template);
  }
  if (mcp?.file !== undefined && mcp.template !== undefined) {
    write(mcp.file, mcp.template);
  }
  const own = decide?.file.startsWith("{system}/") === true;
  if (hooked && own) {
    write(decide!.file, decide!.template);
  }
  for (const [path, template] of Object.entries(hooked ? decide!.pins ?? {} : {})) {
    write(path, template);
  }
  const shared = decide === null || own || options.hook === undefined ? null : {
    path: decide.file, text: hooked ? fill(decide.template, hookValues, true) : null };
  for (const path of files.keys()) {
    if (!path.startsWith(`${home}/`) && !path.startsWith(`${system}/`)) {
      throw new Error("a record's files are under the run's home or its system directory");
    }
  }
  return { argv, stdin: cli.engine.stdin === undefined ? null : prompt, env, cwd: agentDir,
           files, resumed, shared };
}

/** A record's `secret_paths` and `session_paths`, each from its home (`a/b`, `a/` a tree). */
export function keptPaths(record: Served): { secrets: string[]; sessions: string[] } {
  const rel = (path: string) => path.slice("{home}/".length);
  return { secrets: record.cli.secret_paths.map(rel),
           sessions: record.cli.session_paths.map(rel) };
}

/**
 * Whether `rel`, a path in a home, is one of `kept`: that file, or under that tree (`a/`),
 * a `*` segment matching any one segment.
 */
export function isKept(kept: readonly string[], rel: string): boolean {
  const parts = rel.split("/");
  return kept.some((path) => {
    const tree = path.endsWith("/");
    const want = (tree ? path.slice(0, -1) : path).split("/");
    if (tree ? parts.length <= want.length : parts.length !== want.length) {
      return false;
    }
    return want.every((segment, i) => segment === "*" || segment === parts[i]);
  });
}
