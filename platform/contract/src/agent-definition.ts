/**
 * An agent's definition (ADR-034 decision 2; the founder's calls 1 to 3 of 2026-11-21): the files
 * under `/workspace/agents/<agent>/` that make the agent, the same for every engine, and the one
 * reading of each of its JSON files. Each engine's source record says, as data, how each part
 * reaches that engine (`sources/src/schema.ts` `Definition`), so no engine is named here.
 *
 * - `agent.json`: its engine, model and run scopes (`agentFile`).
 * - `AGENTS.md`: its instructions, and `SOUL.md`, which they name.
 * - `.agents/skills/<name>/SKILL.md`: its own skills (the Agent Skills layout), which with the
 *   owner's shared ones make its skills (`skillUnion`).
 * - `mcp.json`: its own MCP servers (`agentMcp`), each a URL or a command, never a credential:
 *   a server's credential comes through the owner's connection, as a connector's does.
 * - `hooks.json`: its own hooks (`agentHooks`), each an argv run on an engine-neutral event. A
 *   hook can block a call or add context and nothing else: the format has no way to allow a
 *   call or rewrite its input, so R5's root-owned hook decides every call on every engine.
 *
 * An absent `mcp.json` or `hooks.json` is an agent with no servers or no hooks; an absent
 * `agent.json` is no agent (`AGENT_UNDEFINED`). A file that does not fit is refused whole
 * (`agent_file_invalid`, naming the file), never half-read (Fact 8).
 */
import { isHookKind, type HookKind, TOOL_MATCHER } from "./acts.ts";
import { RUN_CAPABILITIES, type RunCapability } from "./capabilities.ts";
import { keyed, object, whole } from "./checks.ts";
import { CONNECTION_ID } from "./ids.ts";
import { ABILITIES_DIR, AGENTS_DIR, SKILLS_DIR } from "./paths.ts";
import { Refusal } from "./refusal.ts";
import { CREDENTIAL_KINDS, type CredentialKind } from "./sandbox.ts";
import { AGENT_ID, CREDENTIAL_NAME, isPrintable } from "./text.ts";

/** The definition's files, by part, relative to the agent's directory. */
export const AGENT_FILES = Object.freeze({
  agent: "agent.json",
  instructions: "AGENTS.md",
  soul: "SOUL.md",
  skills: ".agents/skills",
  mcp: "mcp.json",
  hooks: "hooks.json",
});

export const AUTO = "auto";
export const AGENT_UNDEFINED = "agent_undefined: the agent has no agent.json";
export const AGENT_DISABLED = "agent_disabled: the agent is disabled in its agent.json";
/** The run's own TinyAssets door among an engine's MCP servers: no agent server takes it. */
export const RUN_DOOR_SERVER = "tinyassets";

/** The path of `agent`'s definition file `name` (`AGENT_FILES`). */
export function agentPath(agent: string, name: string): string {
  return `${AGENTS_DIR}/${agent}/${name}`;
}

// ---- the skills ----

/** The file that makes a directory a skill (the Agent Skills layout). */
const SKILL_FILE = "SKILL.md";

/** The owner's files as the skill union reads them, at their `/workspace` paths. */
export interface SkillTree {
  /** The names of the directories directly in `dir`; none when it is no directory. */
  dirs(dir: string): readonly string[];
  /** Whether `path` is a file. */
  file(path: string): boolean;
}

/**
 * `agent`'s skills, each name once with the directory it is served from: the agent's own
 * (`.agents/skills` in its directory), then the owner's shared abilities
 * (`/workspace/.agents/skills`), then the owner's skills (`/workspace/skills`), the first that
 * holds a name winning it, in name order within each source. A skill is a directory, its name
 * not hidden, holding `SKILL.md`. The runner projects these into each engine's skills paths (or
 * its argv, in this order) and the doors serve them to a main agent, so both read one union.
 */
export function skillUnion(agent: string, tree: SkillTree): ReadonlyMap<string, string> {
  const chosen = new Map<string, string>();
  for (const source of [agentPath(agent, AGENT_FILES.skills), `${ABILITIES_DIR}/skills`,
                        SKILLS_DIR]) {
    for (const name of [...tree.dirs(source)].sort()) {
      if (!name.startsWith(".") && !chosen.has(name)
          && tree.file(`${source}/${name}/${SKILL_FILE}`)) {
        chosen.set(name, `${source}/${name}`);
      }
    }
  }
  return chosen;
}

/**
 * An agent's definition as the owner answers the doors (`definitionRead`), which serve it to a
 * main agent as MCP resources, the `agent` prompt and the Skills extension: its instructions
 * and soul (null when absent), and each skill of its union (`skillUnion`) with every file's
 * bytes by its path in the skill's directory. The bytes are as stored (Fact 9), never run.
 */
export interface ServedDefinition {
  readonly agent: string;
  readonly instructions: Uint8Array | null;
  readonly soul: Uint8Array | null;
  readonly skills: readonly ServedSkill[];
}

export interface ServedSkill {
  readonly name: string;
  readonly files: Readonly<Record<string, Uint8Array>>;
}

function refuse(name: string, what: string): never {
  throw new Refusal("agent_file_invalid", `${name}: ${what}`);
}

function parsed(text: string, name: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return refuse(name, "not JSON");
  }
  return object(value) ? value : refuse(name, "a JSON object");
}

// ---- agent.json ----

const AGENT_KEYS: readonly string[] = Object.freeze(["connection", "credential", "enabled",
                                                     "engine", "model", "scopes"]);
const RECORD = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/**
 * `agent.json`:
 * - `engine`: a CLI source record's id (`claude-code`, `codex`, `pi`, ...), or `auto`, the
 *   owner's strongest connected source that has an engine (Direction 4);
 * - `credential` (optional): `vendor_login_in_sandbox` or `api_key`, when the agent pins
 *   which the engine runs on;
 * - `connection` (optional): the owner's connection an API-key run spends on;
 * - `model` (optional): the model the engine is asked for;
 * - `scopes`: the capabilities its runs' MCP door holds (`RUN_CAPABILITIES`), which an
 *   owner-started or scheduled run gets whole and a client-started run only where that
 *   client's grant holds them too (ADR-034 decision 6);
 * - `enabled` (optional, true by default): false refuses every run (`AGENT_DISABLED`).
 */
export interface AgentFile {
  readonly engine: string;
  readonly credential: CredentialKind | null;
  readonly connection: string | null;
  readonly model: string | null;
  readonly scopes: readonly RunCapability[];
  readonly enabled: boolean;
}

/** `agent.json`'s text, checked whole. */
export function agentFile(text: string): AgentFile {
  const name = AGENT_FILES.agent;
  const file = parsed(text, name);
  if (!keyed(file, [], AGENT_KEYS)) {
    refuse(name, `holds only ${AGENT_KEYS.join(", ")}`);
  }
  const { engine, credential, connection, model, scopes, enabled } = file;
  if (typeof engine !== "string" || !RECORD.test(engine)) {
    refuse(name, "its engine is a source record's id, or auto");
  }
  if (credential !== undefined && !CREDENTIAL_KINDS.includes(credential as CredentialKind)) {
    refuse(name, `its credential is one of ${CREDENTIAL_KINDS.join(", ")}`);
  }
  if (connection !== undefined && (typeof connection !== "string"
                                   || !CONNECTION_ID.test(connection))) {
    refuse(name, "its connection is a connection id");
  }
  if (connection !== undefined && credential === "vendor_login_in_sandbox") {
    refuse(name, "a run on the vendor's own sign-in is on no connection");
  }
  if (model !== undefined && (typeof model !== "string" || model.length < 1
                              || model.length > 256 || !isPrintable(model))) {
    refuse(name, "its model is 1 to 256 printable characters");
  }
  if (!Array.isArray(scopes) || new Set(scopes).size !== scopes.length
      || !scopes.every((scope) => (RUN_CAPABILITIES as readonly unknown[]).includes(scope))) {
    refuse(name, `its scopes are distinct capabilities of ${RUN_CAPABILITIES.join(", ")}`);
  }
  if (enabled !== undefined && typeof enabled !== "boolean") {
    refuse(name, "its enabled is true or false");
  }
  return Object.freeze({
    engine: engine as string, credential: (credential as CredentialKind | undefined) ?? null,
    connection: (connection as string | undefined) ?? null,
    model: (model as string | undefined) ?? null,
    scopes: Object.freeze([...scopes] as RunCapability[]),
    enabled: (enabled as boolean | undefined) ?? true,
  });
}

// ---- values every engine takes as they are ----

/**
 * A value an engine reads literally: printable (`isPrintable`), so no NUL, which no argument,
 * variable or C string can carry, and nothing a TOML string cannot (Codex reads a server's
 * values as TOML, and a lone surrogate or a control character fails there into another value);
 * no `${NAME}` or `$NAME`, which Claude Code's and Gemini CLI's MCP config expand from the
 * environment; and no leading `!`, which pi runs as a command for the value. So a value is the
 * same on every engine, and none reads anything from the sandbox.
 */
function literal(value: unknown, name: string, what: string): string {
  if (typeof value !== "string" || !isPrintable(value) || /\$(?:\{|[A-Za-z_])/.test(value)
      || value.startsWith("!")) {
    refuse(name, `${what} is printable text the engine takes as it is: no control character, ` +
                 "no $NAME or ${NAME}, no leading !");
  }
  return value as string;
}

function literals(value: unknown, name: string, what: string): readonly string[] {
  if (!Array.isArray(value)) {
    refuse(name, `${what} is a list of strings`);
  }
  return Object.freeze((value as unknown[]).map((item) => literal(item, name, what)));
}

// ---- mcp.json ----

/** A server the engine reaches by URL: streamable HTTP. */
export interface McpHttp {
  readonly url: string;
}

/** A server the engine starts as the run's user, by its command. */
export interface McpStdio {
  readonly command: string;
  readonly args: readonly string[];
  readonly env: Readonly<Record<string, string>>;
}

export type McpServer = McpHttp | McpStdio;

/** `mcp.json`: the agent's own servers, by name. */
export interface AgentMcp {
  readonly servers: Readonly<Record<string, McpServer>>;
}

/** A name the shell and every engine take for a variable (POSIX). */
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * Whether `name` is a server's name: one an owner's rule and a hook can name its tools by
 * (`mcp:<server>:<tool>`, `TOOL_MATCHER`), with no wildcard.
 */
function serverName(name: string): boolean {
  return !name.includes("*") && TOOL_MATCHER.test(`mcp:${name}:tool`);
}

/**
 * `mcp.json`'s text, checked whole: `{"mcpServers": {<name>: <server>}}`, the shape the CLIs
 * share, each server either `{"url"}` (https, with no user or password in it) or
 * `{"command", "args"?, "env"?}`, every value literal (`literal`), no name shaped like a
 * credential (`credentialShaped`: a variable, a URL's query parameter or an argument's option),
 * and no server named as the run's own door.
 */
export function agentMcp(text: string): AgentMcp {
  const name = AGENT_FILES.mcp;
  const file = parsed(text, name);
  if (!keyed(file, ["mcpServers"]) || !object(file["mcpServers"])) {
    refuse(name, "holds only mcpServers, an object of servers by name");
  }
  const servers: Record<string, McpServer> = {};
  for (const [server, entry] of Object.entries(file["mcpServers"] as Record<string, unknown>)) {
    const what = `server ${JSON.stringify(server)}`;
    if (!serverName(server) || server === RUN_DOOR_SERVER) {
      refuse(name, `${what}: a name is letters, digits, _ and -, at most 64, and not ` +
                   `${RUN_DOOR_SERVER}, the run's own door`);
    }
    if (keyed(entry, ["url"])) {
      servers[server] = Object.freeze({ url: httpsUrl(entry["url"], name, what) });
    } else if (keyed(entry, ["command"], ["args", "env"])) {
      const command = literal(entry["command"], name, `${what}'s command`);
      if (command === "") {
        refuse(name, `${what}'s command is a program`);
      }
      servers[server] = Object.freeze({
        command,
        args: entry["args"] === undefined ? Object.freeze([])
          : optionsOf(literals(entry["args"], name, `${what}'s args`), name, what),
        env: serverEnv(entry["env"] ?? {}, name, what),
      });
    } else {
      refuse(name, `${what} is {"url"} or {"command", "args", "env"}: a credential comes ` +
                   "through a connection, never in the file");
    }
  }
  return Object.freeze({ servers: Object.freeze(servers) });
}

function httpsUrl(value: unknown, name: string, what: string): string {
  let url: URL | null = null;
  try {
    url = new URL(literal(value, name, `${what}'s url`));
  } catch {
    url = null;
  }
  if (url === null || url.protocol !== "https:" || url.username !== ""
      || url.password !== "" || url.hash !== ""
      || [...url.searchParams.keys()].some(credentialShaped)) {
    refuse(name, `${what}'s url is https, with no user, password, fragment or query ` +
                 "parameter shaped like a credential");
  }
  return value as string;
}

/**
 * Whether a variable's, a query parameter's or an option's `name` is shaped like a credential,
 * an endpoint or a proxy (`CREDENTIAL_NAME`, `-` read as `_`): a credential comes through a
 * connection, never in the file.
 */
function credentialShaped(name: string): boolean {
  return CREDENTIAL_NAME.test(name.toUpperCase().replaceAll("-", "_"));
}

/** `args`, none an option (`-x`, `--x`, `--x=v`) whose name is `credentialShaped`. */
function optionsOf(args: readonly string[], name: string, what: string): readonly string[] {
  const option = args.find((arg) => credentialShaped(/^--?([^=]*)/.exec(arg)?.[1] ?? ""));
  if (option !== undefined) {
    refuse(name, `${what}'s args give ${JSON.stringify(option)}: an option is never one ` +
                 "shaped like a credential, an endpoint or a proxy");
  }
  return args;
}

function serverEnv(value: unknown, name: string, what: string):
    Readonly<Record<string, string>> {
  if (!object(value)) {
    refuse(name, `${what}'s env is an object of variables`);
  }
  const env: Record<string, string> = {};
  for (const [variable, entry] of Object.entries(value as Record<string, unknown>)) {
    if (!ENV_NAME.test(variable) || credentialShaped(variable)) {
      refuse(name, `${what}'s env sets ${JSON.stringify(variable)}: a variable is a name, ` +
                   "never one shaped like a credential, an endpoint or a proxy");
    }
    env[variable] = literal(entry, name, `${what}'s env ${variable}`);
  }
  return Object.freeze(env);
}

// ---- hooks.json ----

/**
 * The engine-neutral events a hook runs on, and whether each is a tool call's, which a hook's
 * `match` narrows. What a hook may answer on each (block on `pre_tool` and `prompt`, context
 * on `session_start` and `prompt`) is the dispatcher's, which reads the answer.
 */
export const HOOK_EVENTS = Object.freeze({
  pre_tool: Object.freeze({ tool: true }),
  post_tool: Object.freeze({ tool: true }),
  prompt: Object.freeze({ tool: false }),
  session_start: Object.freeze({ tool: false }),
  stop: Object.freeze({ tool: false }),
  session_end: Object.freeze({ tool: false }),
  pre_compact: Object.freeze({ tool: false }),
});

export type HookEvent = keyof typeof HOOK_EVENTS;

/** Whether `name` is one of `HOOK_EVENTS`. */
export function isHookEvent(name: unknown): name is HookEvent {
  return typeof name === "string" && Object.hasOwn(HOOK_EVENTS, name);
}

/**
 * The tool calls a hook runs on: any call of one of the kinds (`acts.json`, each engine's own
 * names in its record's `decide.kinds`), or of one of the tools (`mcp:<server>:<tool>`, as a
 * rule names them). Absent, every call.
 */
export interface HookMatch {
  readonly kinds?: readonly HookKind[];
  readonly tools?: readonly string[];
}

/** One hook: what it runs (an argv, never a shell line), on which calls, for how long. */
export interface Hook {
  readonly match?: HookMatch;
  readonly run: readonly string[];
  /** Whole seconds; absent, the engine's own default. */
  readonly timeout_s?: number;
}

/** `hooks.json`: the agent's hooks, by event, every event listed. */
export interface AgentHooks {
  readonly hooks: Readonly<Record<HookEvent, readonly Hook[]>>;
}

/**
 * `hooks.json`'s text, checked whole: `{"hooks": {<event>: [<hook>]}}`, each event one of
 * `HOOK_EVENTS`, each hook its `run` (a program and its arguments), a `match` on a tool
 * event's only, and a `timeout_s`. There is no field to allow a call or change its input.
 */
export function agentHooks(text: string): AgentHooks {
  const name = AGENT_FILES.hooks;
  const file = parsed(text, name);
  if (!keyed(file, ["hooks"]) || !object(file["hooks"])) {
    refuse(name, "holds only hooks, an object of hooks by event");
  }
  const events = file["hooks"] as Record<string, unknown>;
  const unknown = Object.keys(events).find((event) => !isHookEvent(event));
  if (unknown !== undefined) {
    refuse(name, `${JSON.stringify(unknown)} is not an event; the events are ` +
                 Object.keys(HOOK_EVENTS).join(", "));
  }
  const hooks = Object.fromEntries(Object.keys(HOOK_EVENTS).map((event) => {
    const list = events[event] ?? [];
    if (!Array.isArray(list)) {
      refuse(name, `${event} is a list of hooks`);
    }
    return [event, Object.freeze((list as unknown[]).map((entry, index) =>
      hook(entry, event as HookEvent, name, `${event}[${index}]`)))];
  }));
  return Object.freeze({ hooks: Object.freeze(hooks) as AgentHooks["hooks"] });
}

function hook(entry: unknown, event: HookEvent, name: string, what: string): Hook {
  if (!keyed(entry, ["run"], ["match", "timeout_s"])) {
    refuse(name, `${what} is its run, and optionally match and timeout_s: a hook blocks or ` +
                 "adds context, never allows or changes a call");
  }
  if (!Array.isArray(entry["run"]) || entry["run"].length === 0 || entry["run"][0] === "") {
    refuse(name, `${what}'s run is a program and its arguments, a list, never a shell line`);
  }
  const run = (entry["run"] as unknown[]).map((arg) => {
    if (typeof arg !== "string" || arg.includes("\0")) {
      refuse(name, `${what}'s run is strings with no NUL`);
    }
    return arg as string;
  });
  const timeout = entry["timeout_s"];
  if (timeout !== undefined && !whole(timeout, 1)) {
    refuse(name, `${what}'s timeout_s is whole seconds from 1`);
  }
  const match = entry["match"];
  if (match !== undefined && !HOOK_EVENTS[event].tool) {
    refuse(name, `${what}: only a tool event's hook has a match`);
  }
  return Object.freeze({
    ...(match === undefined ? {} : { match: hookMatch(match, name, what) }),
    run: Object.freeze(run),
    ...(timeout === undefined ? {} : { timeout_s: timeout as number }),
  });
}

function hookMatch(value: unknown, name: string, what: string): HookMatch {
  const distinct = (list: unknown, test: (item: unknown) => boolean) =>
    Array.isArray(list) && list.length > 0 && new Set(list).size === list.length
    && list.every(test);
  if (!keyed(value, [], ["kinds", "tools"]) || Object.keys(value).length === 0
      || (value["kinds"] !== undefined && !distinct(value["kinds"], isHookKind))
      || (value["tools"] !== undefined && !distinct(value["tools"], (tool) =>
        typeof tool === "string" && TOOL_MATCHER.test(tool)))) {
    refuse(name, `${what}'s match is kinds (tool kinds acts.json names) or tools ` +
                 "(mcp:<server>:<tool>, * matching any run), each distinct, at least one");
  }
  return Object.freeze({
    ...(value["kinds"] === undefined ? {}
      : { kinds: Object.freeze([...value["kinds"] as HookKind[]]) }),
    ...(value["tools"] === undefined ? {}
      : { tools: Object.freeze([...value["tools"] as string[]]) }),
  });
}

// ---- the definition's files ----

/** The reader of the definition's JSON file `file`, or null for any other name. */
function readerOf(file: string): ((text: string) => unknown) | null {
  return file === AGENT_FILES.agent ? agentFile : file === AGENT_FILES.mcp ? agentMcp
    : file === AGENT_FILES.hooks ? agentHooks : null;
}

/** Which of the definition's JSON files a stored path is, or null for any other path. */
export function definitionFileOf(path: string): string | null {
  const prefix = `${AGENTS_DIR}/`;
  const [agent, file, ...rest] = path.startsWith(prefix)
    ? path.slice(prefix.length).split("/") : [];
  return agent !== undefined && AGENT_ID.test(agent) && rest.length === 0
    && file !== undefined && readerOf(file) !== null ? file : null;
}

/** The text of a definition file at `path`, checked whole by its reader; any other, as it is. */
export function checkDefinitionFile(path: string, text: string): void {
  const file = definitionFileOf(path);
  if (file !== null) {
    readerOf(file)!(text);
  }
}

/**
 * What replacing a definition file's `before` (null when there was none, and so when it does not
 * read) with `after` adds to what the agent may do, or null: scopes `agent.json` adds, servers
 * `mcp.json` adds or changes, hooks `hooks.json` adds or changes.
 */
export function definitionWidening(file: string, before: string | null, after: string):
    string | null {
  const read = <T>(reader: (text: string) => T, none: T): T => {
    try {
      return before === null ? none : reader(before);
    } catch {
      return none;
    }
  };
  const added = (had: readonly string[], has: readonly string[]) =>
    has.filter((item) => !had.includes(item));
  if (file === AGENT_FILES.agent) {
    const scopes = added(read(agentFile, null)?.scopes ?? [], agentFile(after).scopes);
    return scopes.length === 0 ? null : `agent.json adds the scopes ${scopes.join(", ")}`;
  }
  if (file === AGENT_FILES.mcp) {
    const had = read(agentMcp, { servers: {} }).servers;
    const servers = Object.entries(agentMcp(after).servers).filter(([name, server]) =>
      JSON.stringify(had[name]) !== JSON.stringify(server)).map(([name]) => name);
    return servers.length === 0 ? null
      : `mcp.json adds or changes the servers ${servers.join(", ")}`;
  }
  if (file === AGENT_FILES.hooks) {
    const had = read(agentHooks, null)?.hooks;
    const events = Object.entries(agentHooks(after).hooks).filter(([event, hooks]) =>
      added((had?.[event as HookEvent] ?? []).map((hook) => JSON.stringify(hook)),
            hooks.map((hook) => JSON.stringify(hook))).length > 0).map(([event]) => event);
    return events.length === 0 ? null
      : `hooks.json adds or changes hooks on ${events.join(", ")}`;
  }
  return null;
}
