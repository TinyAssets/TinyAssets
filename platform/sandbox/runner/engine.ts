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
 * - The run's MCP servers (`cli.mcp`): its door, `urls.mcp` (or `urls.mcp_bare` where the
 *   record's template sends the run's secret as a header, `{run_secret}`, so the secret is out
 *   of the URL), then the agent's own as its admission kept them (`mcp_servers`), each in the
 *   record's entry for its transport, in the record's MCP file or as its flag's pairs.
 * - Files: the record's own templates, the credential's, the MCP servers and their file's
 *   template, each filled (a `.json`, `.toml` or `.mjs` file's values escaped as a quoted
 *   string's), several parts of one file joined (`joined`: a JSON file's merged, its lists one
 *   after the other; any other's texts one after the other).
 * - The CLI's own pre-tool hook (R5 P-hooks), where its record has one: the run's decide
 *   credential in its environment (`DECIDE_ENV`), and, when the owner's rules name tools it
 *   matches (`hook.tools`), its file filled from the record's template (`{matcher}` those
 *   tools; the door's address, its header and the credential's variable; the helper's command
 *   line and its argv, the door their argument), what the record adds to its argv, and its
 *   pinned settings files in the home (`pins`, root's like every run config file).
 * - The agent's own hooks (D5, `definition.hooks`), where its record has a decide hook: the
 *   run's own file of them for the dispatcher (`{system}/hooks.json`, `runHooks`), and the
 *   dispatcher's entries in the hook file (`{dispatcher}` its command line, each event's entry
 *   its own), after the decide hook's: a file every run shares carries every event, a run's own
 *   the events the agent has hooks on (and is loaded by the record's argv), none without one.
 * - A file every run of the engine shares (under `/etc`) is the runner's to write, as
 *   `shared`, the decide hook's part and the dispatcher's joined, so the latest run's start
 *   leaves the owner's latest rules there; null text when it holds neither, null for a call
 *   given no hook.
 */
import type { CliSpec, Mcp } from "../../sources/src/schema";
import type { Served } from "../../sources/src/served";
import { type HookKind, toolPatterns, type ToolNames } from "../../contract/src/acts";
import {
  HOOK_EVENTS, HOOK_TIMEOUT_S, type HookEvent, RUN_DOOR_SERVER,
} from "../../contract/src/agent-definition";
import { object } from "../../contract/src/checks";
import { DECIDE_HEADER, decideDoor, egressDoor, envStem } from "../../contract/src/egress";
import { AGENTS_DIR } from "../../contract/src/paths";
import { forbids } from "../../contract/src/text";

import { DECIDE_ENV, helperArgv, helperCommand, matcherOf } from "./decide";
import { COMMAND, type DocHook, type DocMcpServer, RUNNERS, type StartDoc } from "./protocol";

export const ENGINE_ROOT = "/opt/ta/engines";

/** The run's own hooks, in its system directory. */
export const HOOKS_FILE = "hooks.json";
/**
 * The dispatcher's argv before its event, as a hook file names it: the runner of `digest`, as
 * the loader installed it, by the Node the loader runs it with, Node's own warnings off.
 */
export function dispatcherArgv(digest: string): string[] {
  return [process.execPath, "--no-warnings", `${RUNNERS}/${digest}/runner.mjs`, COMMAND.hooks];
}

/** One hook, as the run's file holds it: its argv, its timeout, the calls it matches. */
export interface RunHook {
  readonly run: readonly string[];
  readonly timeout_s: number;
  /** The anchored pattern of the CLI's tool names it runs on; null, every call. */
  readonly matcher: string | null;
}

/** The run's own hooks (`HOOKS_FILE`): its engine, the agent's directory, its tools' kinds. */
export interface RunHooks {
  readonly engine: string;
  readonly cwd: string;
  /** The kind of each of the CLI's tools that is one (`decide.kinds`, by name). */
  readonly kinds: Readonly<Record<string, HookKind>>;
  readonly hooks: Readonly<Record<HookEvent, readonly RunHook[]>>;
}

/**
 * The run's own hooks file, from its start's (`hooks`, by event) and its CLI's tool names
 * (`names`, its decide hook's): a hook's `match` made the anchored pattern of the CLI's own
 * names of its kinds and tools (`toolPatterns`, the one rule a rule's tools take too), its
 * timeout its own or `HOOK_TIMEOUT_S`.
 */
export function runHooks(engine: string, names: ToolNames, cwd: string,
                         hooks: Readonly<Record<string, readonly DocHook[]>>): RunHooks {
  const kinds = Object.fromEntries(Object.entries(names.kinds).flatMap(([kind, tools]) =>
    (tools ?? []).map((tool) => [tool, kind as HookKind])));
  return {
    engine, cwd, kinds,
    hooks: Object.fromEntries((Object.keys(HOOK_EVENTS) as HookEvent[]).map((event) =>
      [event, (hooks[event] ?? []).map((hook) => ({
        run: hook.run, timeout_s: hook.timeout_s ?? HOOK_TIMEOUT_S,
        matcher: hook.match === undefined ? null : matcherOf(toolPatterns(
          names, (hook.match.kinds ?? []) as HookKind[], hook.match.tools ?? [])),
      }))])) as Record<HookEvent, RunHook[]>,
  };
}

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

/**
 * The run's MCP servers by name: its door (`url`, an `http` server), then the agent's own, as
 * its admission kept them; no agent server takes the door's name.
 */
function runServers(start: StartDoc, url: string): [string, DocMcpServer][] {
  if (Object.hasOwn(start.mcp_servers, RUN_DOOR_SERVER)) {
    throw new Error(`no agent server is named ${RUN_DOOR_SERVER}, the run's own door`);
  }
  return [[RUN_DOOR_SERVER, { url }], ...Object.entries(start.mcp_servers)];
}

/** `server`'s entry in the engine's own keys: its transport's, its own values in their places. */
function entryOf(mcp: Mcp, server: DocMcpServer): Record<string, unknown> {
  const own: Readonly<Record<string, unknown>> = "url" in server ? { "{url}": server.url }
    : { "{command}": server.command, "{args}": server.args, "{env}": server.env };
  return Object.fromEntries(Object.entries("url" in server ? mcp.http : mcp.stdio).map(
    ([key, value]) => [key, Object.hasOwn(own, value) ? own[value] : value]));
}

/** `value` at the dotted `path` in `into`, its objects made on the way. */
function setAt(into: Record<string, unknown>, path: string, value: unknown): void {
  const keys = path.split(".");
  let at = into;
  for (const key of keys.slice(0, -1)) {
    at = (at[key] ??= {}) as Record<string, unknown>;
  }
  at[keys.at(-1)!] = value;
}

/** The servers as the record's file holds them: under `servers`, every name on `names`'s list. */
function serversFile(mcp: Mcp, servers: readonly [string, DocMcpServer][]):
    Record<string, unknown> {
  const file: Record<string, unknown> = {};
  setAt(file, mcp.servers, Object.fromEntries(servers.map(([name, server]) =>
    [name, entryOf(mcp, server)])));
  if (mcp.names !== undefined) {
    setAt(file, mcp.names, servers.map(([name]) => name));
  }
  return file;
}

/**
 * The servers as the record's flag gives them (none without one): one
 * `<flag> <servers>.<name>.<key>=<JSON>` pair a value, a map's one an entry.
 */
function serverPairs(mcp: Mcp, servers: readonly [string, DocMcpServer][]): string[] {
  const flag = mcp.flag;
  return flag === undefined ? [] : servers.flatMap(([name, server]) =>
    Object.entries(entryOf(mcp, server)).flatMap(([key, value]) => object(value)
      ? Object.entries(value).flatMap(([entry, each]) =>
        [flag, `${mcp.servers}.${name}.${key}.${entry}=${JSON.stringify(each)}`])
      : [flag, `${mcp.servers}.${name}.${key}=${JSON.stringify(value)}`]));
}

/** Deep merge of JSON values, `b` over `a`: objects key by key, lists one after the other. */
function merged(a: unknown, b: unknown): unknown {
  if (Array.isArray(a) && Array.isArray(b)) {
    return [...a, ...b];
  }
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

/** Two parts of the file at `path` as one: a JSON file's merged (`merged`), another's joined. */
function joined(path: string, a: string, b: string): string {
  return path.endsWith(".json") ? JSON.stringify(merged(JSON.parse(a), JSON.parse(b)))
    : `${a}${b}`;
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
  const mcp = cli.mcp;
  const servers = runServers(start, mcp.template?.includes("{run_secret}") === true
    ? start.urls.mcp_bare : start.urls.mcp);
  const values: Record<string, string> = {
    bin: binPath(record), home, system, agent: start.agent,
    run_secret: runSecret(start.urls.mcp),
  };
  if (mcp.file !== undefined) {
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
  // The agent's own hooks: the events the hook file carries (a shared file every one, the
  // run's own those the agent has a hook on).
  const hooks = "unsupported" in cli.definition.hooks ? null : cli.definition.hooks;
  const used = (Object.keys(HOOK_EVENTS) as HookEvent[]).filter((event) =>
    (start.hooks[event] ?? []).length > 0);
  if (used.length > 0 && (hooks === null || options.hook === undefined)) {
    throw new Error(`engine ${record.id} runs no hooks of the agent's here`);
  }
  const ownHooks = hooks?.file.startsWith("{system}/") === true;
  const carried = hooks === null || options.hook === undefined ? []
    : ownHooks ? used : Object.keys(HOOK_EVENTS) as HookEvent[];
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
    ...serverPairs(mcp, servers),
    ...mcp.argv.map((arg) => fill(arg, values)),
    ...servers.flatMap(([name]) => (mcp.argv_each ?? []).map((arg) =>
      fill(arg, { ...values, name }))),
    ...(hooked ? decide!.argv ?? [] : []).map((arg) => fill(arg, values)),
    ...(carried.length > 0 ? hooks!.argv ?? [] : []).map((arg) => fill(arg, values)),
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
  const dispatchValues = options.hook === undefined ? hookValues : {
    ...hookValues, dispatcher: dispatcherArgv(options.hook.digest).join(" "),
    dispatcher_argv: JSON.stringify(dispatcherArgv(options.hook.digest)),
    hook_events: JSON.stringify(used) };
  // The files: the record's, the credential's, the MCP servers and their file's template,
  // then the run's own hook, then the dispatcher's entries; one file's parts joined in that
  // order (`joined`).
  const files = new Map<string, string>();
  const put = (path: string, text: string) => {
    files.set(path, files.has(path) ? joined(path, files.get(path)!, text)
      : path.endsWith(".json") ? JSON.stringify(JSON.parse(text)) : text);
  };
  const quoted = (path: string) => /\.(?:json|toml|mjs)$/.test(path);
  const write = (pathTemplate: string, template: string) => {
    const path = fill(pathTemplate, values);
    put(path, fill(template, hookValues, quoted(path)));
  };
  const hookPath = hooks === null ? null : fill(hooks.file, values);
  const hookParts = carried.length === 0 ? [] : [
    ...(hooks!.template === undefined ? []
      : [fill(hooks!.template, dispatchValues, quoted(hookPath!))]),
    ...(hooks!.each === undefined ? [] : carried.map((event) => fill(hooks!.each!, {
      ...dispatchValues, event, native: hooks!.events[event],
      on_failure: HOOK_EVENTS[event].block ? "block" : "continue" }, quoted(hookPath!))))];
  for (const [path, template] of Object.entries(cli.files ?? {})) {
    write(path, template);
  }
  for (const [path, template] of Object.entries(credential?.files ?? {})) {
    write(path, template);
  }
  if (mcp.file !== undefined) {
    put(values["mcp_config"]!, JSON.stringify(serversFile(mcp, servers)));
    if (mcp.template !== undefined) {
      write(mcp.file, mcp.template);
    }
  }
  const own = decide?.file.startsWith("{system}/") === true;
  if (hooked && own) {
    write(decide!.file, decide!.template);
  }
  for (const [path, template] of Object.entries(hooked ? decide!.pins ?? {} : {})) {
    write(path, template);
  }
  if (hooks !== null && options.hook !== undefined) {
    put(`${system}/${HOOKS_FILE}`, JSON.stringify(runHooks(record.id, decide!, agentDir,
                                                           start.hooks)));
  }
  for (const part of ownHooks ? hookParts : []) {
    put(hookPath!, part);
  }
  const sharedParts = [...(hooked && !own ? [fill(decide!.template, hookValues, true)] : []),
                       ...(ownHooks ? [] : hookParts)];
  const shared = decide === null || own || options.hook === undefined ? null : {
    path: decide.file, text: sharedParts.length === 0 ? null
      : sharedParts.reduce((a, b) => joined(decide.file, a, b)) };
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
