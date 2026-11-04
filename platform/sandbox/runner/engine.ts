/**
 * One run's invocation, from its engine's source record as data (ADR-034 decision 4.3;
 * `doors/src/sources.ts`'s `CliSpec`): the argv, stdin, environment, cwd and the files
 * the record's templates make. Pure: the runner (`runner.ts`) does the I/O. No provider is
 * named here; every difference is the record's.
 *
 * - The environment is the run's allow-list alone (`config.env`, the lease's `runEnv` and
 *   `sandboxEnv`), its templates filled, and the process basics it keeps (`config.inherit`)
 *   from the runner's own; nothing else of the sandbox's environment reaches the CLI. A
 *   variable the run's credential forbids (`forbid_env`) fails the run loudly.
 * - The argv: the binary, then the credential's own arguments, the model (each option
 *   joined to its value, `--model=<id>`, so a reported id that starts with `-` is never a
 *   flag), the unattended flags on an unattended run, the MCP arguments, the update switch,
 *   then the record's `argv` or `resume_argv` past its binary. The prompt goes on stdin
 *   where the record says so, or joined to its option (`--prompt={prompt}`), never bare.
 * - The run's MCP door is `urls.mcp`, or `urls.mcp_bare` where the record's template sends
 *   the run's secret as a header (`{run_secret}`), so the secret is out of the URL.
 * - Files: the record's own templates, the credential's, and the MCP template, each filled
 *   (a `.json` file's values JSON-escaped), several templates of one JSON file merged.
 */
import type { CliSpec, CredentialKind, SourceRecord } from "../../doors/src/sources";
import { forbids } from "../../owner/src/text";

/** A start as the sandbox Worker hands it to the runner: no lease secrets in it. */
export interface StartDoc {
  readonly run: string;
  readonly agent: string;
  readonly conversation: string;
  readonly engine: string;
  readonly credential: CredentialKind;
  readonly model: string | null;
  readonly session_id: string | null;
  readonly prompt: string;
  readonly unattended: boolean;
  readonly authority: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly config: { readonly generation: number; readonly env: Readonly<Record<string, string>>;
                     readonly inherit: readonly string[] };
  readonly urls: { readonly mcp: string; readonly mcp_bare: string; readonly events: string;
                   readonly signin: string };
  /**
   * How long an agent's scratch may go unused before it is deleted (`scratch.ts`): the
   * control record's `scratch_retention_days`, handed by the sandbox Worker; absent or null
   * when the record names none, and then only the disk's own pressure lets scratch go.
   */
  readonly scratch_retention_days?: number | null;
}

export const WORKSPACE = "/workspace";
export const AGENTS_DIR = "/workspace/agents";
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
export function binPath(record: SourceRecord): string {
  const install = record.cli!.install;
  return install.manager === "npm"
    ? `${ENGINE_ROOT}/${record.id}/node_modules/.bin/${install.bin}`
    : `${ENGINE_ROOT}/${record.id}/bin/${install.bin}`;
}

/** `{name}` filled from `values`; a placeholder with no value fails loudly. */
export function fill(text: string, values: Readonly<Record<string, string>>,
                     json = false): string {
  return text.replace(/\{([a-z_]+)\}/g, (whole, name: string) => {
    if (!Object.hasOwn(values, name)) {
      throw new Error(`the record's ${whole} has no value in this run`);
    }
    const value = values[name]!;
    return json ? JSON.stringify(value).slice(1, -1) : value;
  });
}

/** The run's own secret, as its MCP URL's path carries it (`/r/<run>/<secret>/mcp`). */
export function runSecret(mcpUrl: string): string {
  const match = /\/r\/run_[0-9a-f]{32}\/([0-9a-f]{64})\/mcp$/.exec(new URL(mcpUrl).pathname);
  if (match === null) {
    throw new Error("a run's MCP URL carries its secret");
  }
  return match[1]!;
}

/**
 * The provider an API-key run's connection is of: the one of the record's key providers
 * whose base-URL variable the run's environment sets (`<PROVIDER>_BASE_URL`, as
 * `sandboxEnv` names it), and that URL.
 */
export function keyProvider(cli: CliSpec, env: Readonly<Record<string, string>>):
    { provider: string; base_url: string } | null {
  const found = Object.keys(cli.api_key_env ?? {}).filter((provider) =>
    Object.hasOwn(env, `${provider.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_BASE_URL`));
  if (found.length > 1) {
    throw new Error("an API-key run is on one provider's connection");
  }
  if (found.length === 0) {
    return null;
  }
  const provider = found[0]!;
  return { provider,
           base_url: env[`${provider.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_BASE_URL`]! };
}

/**
 * The record's model arguments, each option joined to its value (`--model=<id>`), so a
 * value is never read as a flag; a placeholder not after an option is refused when its
 * value could be read as one.
 */
export function modelArgs(template: readonly string[], values: Readonly<Record<string, string>>):
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
}

/**
 * The run's invocation. `inherited` is the runner's own environment, read only for the
 * basics the run keeps; `continued` whether a `continue_in_cwd` conversation already ran
 * in its cwd; `prompt` the input (a recovery's continuation, or the start's own).
 */
export function invocation(record: SourceRecord, start: StartDoc,
                           inherited: Readonly<Record<string, string | undefined>>,
                           options: { readonly continued: boolean; readonly prompt?: string;
                                      readonly resume?: boolean;
                                      /** A prefix of every path: none in the sandbox. */
                                      readonly root?: string;
                                      /** The run's own home and system directory. */
                                      readonly home: string; readonly system: string;
                                      /** A `continue_in_cwd` conversation's own cwd. */
                                      readonly conversationDir?: string }): Invocation {
  const cli = record.cli;
  if (cli === undefined || record.unwired !== undefined
      || !cli.credential.includes(start.credential)) {
    throw new Error(`engine ${record.id} does not run on ${start.credential}`);
  }
  const root = options.root ?? "";
  const home = options.home;
  const system = options.system;
  const agentDir = `${root}${AGENTS_DIR}/${start.agent}`;
  const mcp = "unsupported" in cli.mcp ? null : cli.mcp;
  const secretInHeader = mcp?.template?.includes("{run_secret}") === true;
  const values: Record<string, string> = {
    bin: `${root}${binPath(record)}`, home, system, agent: start.agent,
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
  const banned = Object.keys(env).find((name) => forbids(forbidden, name));
  if (banned !== undefined) {
    throw new Error(`the run's credential forbids ${banned}`);
  }
  if (env["HOME"] !== home) {
    throw new Error("a run's HOME is its engine home");
  }
  // Which invocation: a resume by session id, a continued cwd, or a new conversation.
  const resumed = options.resume !== false && (cli.engine.resume === "session_id"
    ? start.session_id !== null : options.continued);
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
    ...(mcp === null ? [] : [...mcp.argv, ...mcp.trust.argv].map((arg) => fill(arg, values))),
    ...("argv" in cli.no_update ? cli.no_update.argv ?? [] : []).map((arg) => fill(arg, values)),
    ...tail,
  ];
  const refusedArgs = credential?.forbid_argv ?? [];
  const bad = argv.find((arg) => refusedArgs.includes(arg));
  if (bad !== undefined) {
    throw new Error(`the run's credential forbids ${bad}`);
  }
  // The files: the record's, the credential's, then the MCP template; one JSON file's
  // templates merged in that order.
  const files = new Map<string, string>();
  const write = (pathTemplate: string, template: string) => {
    const path = fill(pathTemplate, values);
    const json = path.endsWith(".json");
    const text = fill(template, values, json);
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
  for (const path of files.keys()) {
    if (!path.startsWith(`${home}/`) && !path.startsWith(`${system}/`)) {
      throw new Error("a record's files are under the run's home or its system directory");
    }
  }
  if (cli.engine.resume === "continue_in_cwd" && options.conversationDir === undefined) {
    throw new Error("a run that continues in its cwd is given its conversation's own");
  }
  const cwd = cli.engine.resume === "continue_in_cwd" ? options.conversationDir! : agentDir;
  return { argv, stdin: cli.engine.stdin === undefined ? null : prompt, env, cwd, files,
           resumed };
}

/** A record's `secret_paths` and `session_paths`, each from its home (`a/b`, `a/` a tree). */
export function keptPaths(record: SourceRecord): { secrets: string[]; sessions: string[] } {
  const rel = (path: string) => path.slice("{home}/".length);
  return { secrets: (record.cli?.secret_paths ?? []).map(rel),
           sessions: (record.cli?.session_paths ?? []).map(rel) };
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
