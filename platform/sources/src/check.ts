/**
 * The one checker of a source record (`schema.ts`): `sourceRecord` takes a record as its
 * file holds it and gives it back checked whole, a frozen copy, or a `SourceRecordError`
 * naming the record and the field. The catalog runs it on every record it serves
 * (`catalog.ts`). Its gateway part is the gateway's own check (`contract/src/egress-scope.ts`),
 * and `sealedSource` is what the doors hand the gateway to seal.
 */
import { isHookKind } from "../../contract/src/acts";
import {
  HOOK_EVENTS, type HookEvent, RUN_DOOR_SERVER,
} from "../../contract/src/agent-definition";
import { object, valueAtPath, whole } from "../../contract/src/checks";
import { RUN_HEADER } from "../../contract/src/egress";
import {
  allows, ID as PROVIDER, keyExchangeSource, type Source, source as gatewaySource,
} from "../../contract/src/egress-scope";
import { type CreditRead, creditRead } from "../../contract/src/gateway";
import { HEX64 } from "../../contract/src/ids";
import { MODEL_ID as MODEL_PATTERN } from "../../contract/src/model-record";
import {
  type EngineEvents, type EventMatcher, PATTERN_FLAGS,
} from "../../contract/src/run-events";
import { CREDENTIAL_NAME, forbids, SOURCE_ID } from "../../contract/src/text";

import { CODE_LOADING_ENV, RUN_BASICS } from "./run-env";
import {
  type ApiKeyEnv, type CliSpec, type ConnectMethod, type Credit, type Decide, type Definition,
  type DefinitionHooks, type DefinitionNative, type DefinitionSkills,
  type Engine, type HostedUse,
  type Install, type Instructions, type LoginSpec, type Mcp, type OAuthSpec, type PerCredential,
  type SourceRecord, SourceRecordError, type ToolFilter, type Unattended,
} from "./schema";
import type { CredentialKind } from "../../contract/src/sandbox";

const FLOW_PARAMS: readonly string[] = Object.freeze([
  "response_type", "client_id", "redirect_uri", "state", "code_challenge",
  "code_challenge_method", "callback_url", "scope", "resource",
]);
const GATEWAY_KEYS: readonly string[] = Object.freeze([
  "provider", "api", "base_url", "auth", "verify_url", "endpoints", "models",
]);
const HOST = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const PARAM = /^[A-Za-z0-9_.-]{1,64}$/;
const TEXT = /^[^\p{Cc}]{1,128}$/u;
/** A verdict's citation: where it was read, and what it says. */
const CITATION = /^[^\p{Cc}]{1,1024}$/u;
const SCOPE = /^[\x21-\x7e]+(?: [\x21-\x7e]+)*$/;
const CLIENT_ID = /^[\x21-\x7e]{1,512}$/;

/** The record's checks, each failing with the record and the field it is about. */
function checker(id: string) {
  const fail = (what: string): never => {
    throw new SourceRecordError(`source record ${JSON.stringify(id)}: ${what}`);
  };
  const keys = (value: unknown, what: string, required: readonly string[],
                optional: readonly string[] = []): Record<string, unknown> => {
    if (!object(value)) {
      return fail(`${what} is an object`);
    }
    const present = Object.keys(value);
    const missing = required.filter((key) => !present.includes(key));
    const extra = present.filter((key) => !required.includes(key) && !optional.includes(key));
    if (missing.length > 0 || extra.length > 0) {
      fail(`${what} has exactly ${required.join(", ")}${optional.length > 0
        ? ` (and optionally ${optional.join(", ")})` : ""}; missing ${JSON.stringify(missing)}, ` +
           `extra ${JSON.stringify(extra)}`);
    }
    return value;
  };
  const text = (value: unknown, pattern: RegExp, what: string): string =>
    typeof value === "string" && pattern.test(value) ? value : fail(`${what} is invalid`);
  const https = (value: unknown, what: string): string => {
    let url: URL | null = null;
    try {
      url = typeof value === "string" ? new URL(value) : null;
    } catch {
      url = null;
    }
    if (url === null || url.protocol !== "https:" || url.href !== value || url.hash !== ""
        || url.search !== "" || url.username !== "" || url.password !== "") {
      fail(`${what} is a canonical https URL without a query`);
    }
    return value as string;
  };
  const strings = (value: unknown, what: string, pattern: RegExp, max: number): string[] =>
    Array.isArray(value) && value.length <= max
      && value.every((item) => typeof item === "string" && pattern.test(item))
      ? value as string[] : fail(`${what} is a list of at most ${max} valid strings`);
  const stringMap = (value: unknown, what: string, keyPattern: RegExp, max: number):
      Record<string, string> => {
    if (!object(value) || Object.keys(value).length > max
        || Object.entries(value).some(([key, item]) => !keyPattern.test(key)
                                                       || typeof item !== "string")) {
      fail(`${what} maps at most ${max} names to strings`);
    }
    return value as Record<string, string>;
  };
  return { fail, keys, text, https, strings, stringMap };
}

function authorizeParams(check: ReturnType<typeof checker>, value: unknown):
    Readonly<Record<string, string>> {
  const params = check.stringMap(value ?? {}, "oauth.authorize_params", PARAM, 8);
  for (const [key, item] of Object.entries(params)) {
    // TEXT bounds a value at 1 to 128 code points, as the Python's `len()` did.
    if (FLOW_PARAMS.includes(key) || !TEXT.test(item)) {
      check.fail(`oauth.authorize_params may not set ${key}, and each value is 1 to 128 ` +
                 "printable characters");
    }
  }
  return Object.freeze({ ...params });
}

function oauthSpec(check: ReturnType<typeof checker>, value: unknown): OAuthSpec {
  const protocol = object(value) ? value["protocol"] : undefined;
  if (protocol === "pkce_user_key_v1") {
    const fields = check.keys(value, "oauth", ["protocol", "authorization_endpoint",
                                               "exchange_endpoint"], ["authorize_params"]);
    return Object.freeze({
      protocol,
      authorization_endpoint: check.https(fields["authorization_endpoint"],
                                          "oauth.authorization_endpoint"),
      authorize_params: authorizeParams(check, fields["authorize_params"]),
      exchange_endpoint: check.https(fields["exchange_endpoint"], "oauth.exchange_endpoint"),
    });
  }
  if (protocol !== "rfc6749") {
    return check.fail("oauth.protocol is rfc6749 or pkce_user_key_v1");
  }
  const fields = check.keys(value, "oauth", [
    "protocol", "authorization_endpoint", "token_endpoint", "client_id", "client_auth"],
    ["authorize_params", "scope", "revocation_endpoint", "resource", "issuer",
     "authorization_response_iss_parameter_supported"]);
  const issuer = fields["issuer"];
  const iss = fields["authorization_response_iss_parameter_supported"];
  if (issuer !== undefined) {
    // RFC 8414 §2: an https URL with no query or fragment, compared as a string; a bare
    // origin is written without its root's `/`.
    const bare = typeof issuer === "string" && /^https:\/\/[^/]+$/.test(issuer);
    check.https(typeof issuer === "string" ? `${issuer}${bare ? "/" : ""}` : null,
                "oauth.issuer");
  }
  if (iss !== undefined && (iss !== true || issuer === undefined)) {
    check.fail("oauth.authorization_response_iss_parameter_supported is true, with the issuer");
  }
  // A client secret is never data: a confidential client's would need a secret binding.
  if (JSON.stringify(fields["client_auth"]) !== JSON.stringify({ method: "none" })) {
    check.fail("oauth.client_auth is {\"method\": \"none\"}: a record holds no secret");
  }
  const optional = (key: string, read: (item: unknown) => string) =>
    fields[key] === undefined ? {} : Object.fromEntries([[key, read(fields[key])]]);
  return Object.freeze({
    protocol,
    authorization_endpoint: check.https(fields["authorization_endpoint"],
                                        "oauth.authorization_endpoint"),
    authorize_params: authorizeParams(check, fields["authorize_params"]),
    token_endpoint: check.https(fields["token_endpoint"], "oauth.token_endpoint"),
    client_id: check.text(fields["client_id"], CLIENT_ID, "oauth.client_id"),
    ...optional("scope", (item) => check.text(item, SCOPE, "oauth.scope")),
    ...optional("revocation_endpoint",
                (item) => check.https(item, "oauth.revocation_endpoint")),
    ...optional("resource", (item) => check.https(item, "oauth.resource")),
    ...(issuer === undefined ? {} : { issuer: issuer as string }),
    ...(iss === undefined ? {} : { authorization_response_iss_parameter_supported: true }),
  });
}

/** An environment variable's name. */
const ENV_NAME = /^[A-Z][A-Z0-9_]{0,63}$/;
/** One argument: any text, a placeholder being `{name}` among `PLACEHOLDERS`. */
const ARG = /^.{0,4096}$/su;
/** The placeholders the runner fills, in an argv, an env value or a file template. */
const PLACEHOLDERS: readonly string[] = Object.freeze([
  "{bin}", "{prompt}", "{sid}", "{model}", "{provider}", "{home}", "{agent}", "{name}",
  "{mcp_config}", "{base_url}", "{tools}", "{tool}", "{run_secret}", "{system}",
  "{matcher}", "{decide}", "{decide_argv}", "{decide_url}", "{decide_header}", "{decide_env}",
  "{skill}", "{dispatcher}", "{dispatcher_argv}", "{hook_events}", "{event}", "{native}",
  "{on_failure}",
]);
/** The placeholders only the hook's template takes (`cli.decide.template`). */
const HOOK_PLACEHOLDERS: readonly string[] = Object.freeze([
  "{matcher}", "{decide}", "{decide_argv}", "{decide_url}", "{decide_header}", "{decide_env}"]);
/**
 * The placeholders only the dispatcher's file takes (`cli.definition.hooks`), `{event}`,
 * `{native}` and `{on_failure}` only its `each`.
 */
const DISPATCH_PLACEHOLDERS: readonly string[] = Object.freeze([
  "{dispatcher}", "{dispatcher_argv}", "{hook_events}", "{event}", "{native}", "{on_failure}"]);
/** What each way a CLI runs its hook carries in its template, beside `{matcher}`. */
const RUNS_CARRY = Object.freeze({
  http: Object.freeze(["{decide_url}", "{decide_header}", "{decide_env}"]),
  shell: Object.freeze(["{decide}"]),
  exec: Object.freeze(["{decide_argv}", "{decide_env}"]),
});
/**
 * The one shape `{prompt}` may take in an argv: joined to its option (`--prompt={prompt}`),
 * so no prompt is ever read as an option or a file include (`-x`, `@file`). Where the CLI
 * reads stdin, the prompt goes there instead (`engine.stdin`).
 */
const PROMPT_ARG = /^--[A-Za-z0-9][A-Za-z0-9-]*=\{prompt\}$/;
/** The one shape `{name}` takes, for the same reason: an agent's server may be named `-y`. */
const NAME_ARG = /^--[A-Za-z0-9][A-Za-z0-9-]*=\{name\}$/;
/** A value that turns a setting off: nothing, bare quotes, or an empty list or map. */
const EMPTY_VALUE = /^\s*(?:""|''|\[\s*\]|\{\s*\})?\s*$/;
/** An empty list or map anywhere in a text (`tools.core: []`, `mcp_servers={}`). */
const EMPTY_COLLECTION = /\[\s*\]|\{\s*\}/;
/** A variable `forbid_env` names: a name, or every name with a prefix (`OPENAI_*`). */
const FORBID = /^[A-Z][A-Z0-9_]{0,63}\*?$/;
/** A settings key shaped like a credential, a credential helper or an environment block. */
const CREDENTIAL_KEY = /^(?:env|environment)$|api_?key|token|secret|password|credential|helper/i;
/**
 * A settings key that moves where a run's calls go, onto another model provider, or through
 * a proxy: never in a signed-in run's files.
 */
const ENDPOINT_KEY = /base_?url|endpoint|provider|proxy/i;
/**
 * An argument that moves where a run's calls go, onto another model provider (Codex's
 * `--oss` names its local ones) or through a proxy: never in an argv a signed-in run gets
 * (the argv twin of `ENDPOINT_KEY`), so no `-c openai_base_url=...` sends a vendor sign-in's
 * tokens elsewhere.
 */
const ENDPOINT_ARG = /base_?url|endpoint|provider|proxy|^--oss(?:=|$)/i;
/**
 * The shapes `{base_url}` may take in an argv: joined to its key, as an option's value
 * (`--base-url={base_url}`) or a config override's quoted value (`a.b="{base_url}"`), never
 * a bare argument.
 */
const BASE_URL_ARG = /^(?:--[A-Za-z0-9][A-Za-z0-9-]*=\{base_url\}|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*="\{base_url\}")$/;
/** A bare key, dotted or not, as a line of a settings template that is not JSON writes it. */
const BARE_KEY = /^[A-Za-z0-9_{}-]+(?:\.[A-Za-z0-9_{}-]+)*$/;
/**
 * A settings template's real keys and string values, so no spelling hides one: a `.json`
 * file's from its parse (escapes decoded), walked whole; any other file's from its lines,
 * which may each be blank, a `#` comment, a table header or a `key = value` pair whose key
 * is bare and whose value holds no `=` (no inline table) and no backslash (no escape).
 * There is no TOML parser in the lock, so a quoted key, an escape or any other line is
 * refused (null), never read past.
 */
function settingsOf(path: string, text: string):
    { readonly keys: readonly string[]; readonly values: readonly string[] } | null {
  const keys: string[] = [];
  const values: string[] = [];
  if (path.endsWith(".json")) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      return null;
    }
    const walk = (node: unknown): void => {
      if (typeof node === "string") {
        values.push(node);
      } else if (Array.isArray(node)) {
        node.forEach(walk);
      } else if (object(node)) {
        for (const [key, item] of Object.entries(node)) {
          keys.push(key);
          walk(item);
        }
      }
    };
    walk(parsed);
    return { keys, values };
  }
  if (text.includes("\\")) {
    return null;
  }
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) {
      continue;
    }
    const table = /^\[\[?([^\]]*)\]\]?$/.exec(line);
    const pair = /^([^=]*?)\s*=\s*([^=]*)$/.exec(line);
    const key = table?.[1] ?? pair?.[1];
    if (key === undefined || !BARE_KEY.test(key)) {
      return null;
    }
    keys.push(...key.split("."));
    if (pair !== null && table === null) {
      values.push(pair[2]!);
    }
  }
  return { keys, values };
}


/** A dotted path into a JSON value (an event's field, a settings file's key). */
const DOTTED = /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,7}$/;
/** A path segment: no `.` or `..`, a `*` only whole (any one segment). */
const SEGMENT = /^(?:\*|(?!\.{1,2}$)[A-Za-z0-9._-]{1,128})$/;
/** A resume path's segment: a name's characters, `*` and `{sid}`, never `.` or `..`. */
const RESUME_SEGMENT = /^(?!\.{1,2}$)(?:[A-Za-z0-9._*-]|\{sid\}){1,128}$/;
const DATE = /^20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/;
const REASON = /^[^\p{Cc}]{1,512}$/u;
const TEMPLATE_MAX = 64 * 1024;

/**
 * A path's segments under `{home}`, the runner's engine home (sandbox-only, outside
 * `/workspace`), or null. Every path a record names is under it: any other root (`/`,
 * `/proc`, `/workspace`, `~`, a relative path) is refused, as are `.` and `..` segments, a
 * `*` as the first segment (it would hold every other path in the home), and `{home}`
 * itself. A `*` elsewhere matches any one segment, where `wildcards` allows it; a trailing
 * `/` names a directory.
 */
function homePath(value: unknown, wildcards: boolean): string[] | null {
  if (typeof value !== "string" || value.length > 512 || !value.startsWith("{home}/")) {
    return null;
  }
  const rest = value.slice("{home}/".length);
  const segments = (rest.endsWith("/") ? rest.slice(0, -1) : rest).split("/");
  return segments[0] !== "*" && segments.every((segment) => SEGMENT.test(segment)
    && (wildcards || segment !== "*")) ? segments : null;
}

/**
 * A file the runner writes for one run: under `{home}`, or under `{system}`, the run's own
 * directory outside the home that root owns at every level (`/etc/ta/runs/<run>`, readable
 * by the run's user alone), for a file a CLI takes only from a root-owned path (Gemini
 * CLI's system settings: it skips one any of whose ancestors another user owns). Never a
 * secret or session path, which live in the home.
 */
function runFilePath(value: unknown): string[] | null {
  if (typeof value === "string" && value.startsWith("{system}/")) {
    return homePath(`{home}/${value.slice("{system}/".length)}`, false);
  }
  return homePath(value, false);
}

/** Whether one path is the other or under it (by segments), a `*` segment matching any one. */
function overlaps(a: readonly string[], b: readonly string[]): boolean {
  const shorter = Math.min(a.length, b.length);
  for (let i = 0; i < shorter; i += 1) {
    if (a[i] !== b[i] && a[i] !== "*" && b[i] !== "*") {
      return false;
    }
  }
  return true;
}

function cliSpec(check: ReturnType<typeof checker>, value: unknown): CliSpec {
  if (object(value) && "invoke" in value) {
    check.fail("cli has no invoke: a CLI is an engine, never a backend (ADR-033 decision 2)");
  }
  const fields = check.keys(value, "cli", [
    "install", "credential", "unattended", "engine", "definition", "mcp", "decide", "tools",
    "unattended_argv", "home_env", "no_update", "secret_paths", "session_paths",
    "resume_paths", "hosts"], ["login", "api_key_env", "per_credential", "env", "files",
               "run_secret_env", "compaction"]);
  const argvs: [string, string[]][] = [];
  const argv = (item: unknown, what: string, max = 64): string[] => {
    const list = check.strings(item, what, ARG, max);
    for (const arg of list) {
      // An engine is never configured to nothing: no empty argument (`--tools ""`), no
      // option set to an empty value (`--tools=`), and no empty list or map
      // (`mcp_servers={}`), which would turn its tools or its servers off (ADR-033 D2).
      const equals = arg.indexOf("=");
      if (EMPTY_VALUE.test(arg) || (equals >= 0 && EMPTY_VALUE.test(arg.slice(equals + 1)))
          || EMPTY_COLLECTION.test(arg)) {
        check.fail(`${what} holds an empty value: an engine is never configured to nothing, ` +
                   "never a backend (ADR-033 decision 2)");
      }
    }
    argvs.push([what, list]);
    return list;
  };
  const templates: [string, string][] = [];
  const template = (item: unknown, what: string): string => {
    if (typeof item !== "string" || item.length === 0 || item.length > TEMPLATE_MAX) {
      check.fail(`${what} is a template of 1 to ${TEMPLATE_MAX} characters`);
    }
    if (EMPTY_COLLECTION.test(item as string)) {
      check.fail(`${what} holds an empty list or map (\`tools.core: []\`): an engine is ` +
                 "never configured to nothing, never a backend (ADR-033 decision 2)");
    }
    templates.push([what, item as string]);
    return item as string;
  };
  const envMap = (item: unknown, what: string): Record<string, string> => {
    const map = check.stringMap(item, what, ENV_NAME, 32);
    for (const [name, entry] of Object.entries(map)) {
      if (!/^[^\p{Cc}]{0,4096}$/u.test(entry)) {
        check.fail(`${what}.${name} is at most 4096 characters, none a control character`);
      }
      if (EMPTY_COLLECTION.test(entry)) {
        check.fail(`${what}.${name} holds an empty list or map: an engine is never ` +
                   "configured to nothing, never a backend (ADR-033 decision 2)");
      }
      templates.push([`${what}.${name}`, entry]);
    }
    return map;
  };
  /** A file the record writes: under `{home}` or `{system}`, no wildcard. */
  const sandboxPath = (item: unknown, what: string): string[] => {
    const segments = runFilePath(item);
    return segments ?? check.fail(`${what} is a path under {home} or {system}, with no *, . ` +
                                  "or .. segment");
  };
  const declared: string[] = [];
  const fileMap = (item: unknown, what: string): Record<string, string> => {
    if (!object(item) || Object.keys(item).length > 16) {
      check.fail(`${what} maps at most 16 sandbox paths to templates`);
    }
    for (const [path, entry] of Object.entries(item as Record<string, unknown>)) {
      sandboxPath(path, `${what} path ${JSON.stringify(path)}`);
      template(entry, `${what}.${path}`);
      declared.push(path);
    }
    return item as Record<string, string>;
  };

  const install = installSpec(check, fields["install"], argv);

  // The credentials it runs on, and each one's unattended verdict (ADR-033 decision 3).
  const credential = check.strings(fields["credential"], "cli.credential",
                                   /^(?:vendor_login_in_sandbox|api_key)$/, 2) as CredentialKind[];
  if (credential.length === 0 || new Set(credential).size !== credential.length) {
    check.fail("cli.credential lists vendor_login_in_sandbox and api_key, at least one, each once");
  }
  const unattended = check.keys(fields["unattended"], "cli.unattended", credential);
  for (const kind of credential) {
    unattended[kind] = verdict(check, unattended[kind], `cli.unattended.${kind}`);
  }
  const signsIn = credential.includes("vendor_login_in_sandbox");
  if ((fields["login"] !== undefined) !== signsIn) {
    check.fail("cli.login is present exactly when the record signs in with the vendor's own " +
               "flow in the sandbox");
  }
  const keyed = credential.includes("api_key");
  if ((fields["api_key_env"] !== undefined) !== keyed) {
    check.fail("cli.api_key_env is present exactly when the record runs on an API key");
  }
  let runSecretEnv: { env: string; value: string } | undefined;
  if (fields["run_secret_env"] !== undefined) {
    const header = check.keys(fields["run_secret_env"], "cli.run_secret_env", ["env", "value"]);
    if (!keyed || !ENV_NAME.test(String(header["env"])) || typeof header["value"] !== "string"
        || header["value"] !== `${RUN_HEADER}: {run_secret}`) {
      check.fail("cli.run_secret_env names the variable an API-key run reads extra headers " +
                 `from, and the value ${RUN_HEADER}: {run_secret}`);
    }
    runSecretEnv = { env: header["env"] as string, value: header["value"] as string };
  }
  let compaction: { argv: readonly string[]; marker: string } | undefined;
  if (fields["compaction"] !== undefined) {
    const spec = check.keys(fields["compaction"], "cli.compaction", ["argv", "marker"]);
    compaction = { argv: Object.freeze(argv(spec["argv"], "cli.compaction.argv", 16)),
                   marker: check.text(spec["marker"], /^[^\p{Cc}]{8,256}$/u,
                                      "cli.compaction.marker") };
  }

  const homeEnv = check.stringMap(fields["home_env"], "cli.home_env", ENV_NAME, 8);
  if (Object.keys(homeEnv).length === 0 || Object.values(homeEnv).some((path) =>
    path !== "{home}" && homePath(path, false) === null)) {
    check.fail("cli.home_env sets at least one variable, each to {home} or a path under it");
  }
  const env = fields["env"] === undefined ? undefined : envMap(fields["env"], "cli.env");
  const files = fields["files"] === undefined ? undefined : fileMap(fields["files"], "cli.files");

  const perCredential = fields["per_credential"] === undefined ? undefined
    : check.keys(fields["per_credential"], "cli.per_credential", [], credential);
  const forbidden: [CredentialKind, string[]][] = [];
  const forbidEnv: Partial<Record<CredentialKind, string[]>> = {};
  for (const [kind, entry] of Object.entries(perCredential ?? {})) {
    const what = `cli.per_credential.${kind}`;
    const part = check.keys(entry, what, [], ["argv", "env", "files", "forbid_argv",
                                              "forbid_env"]);
    if (part["forbid_env"] !== undefined) {
      forbidEnv[kind as CredentialKind] = check.strings(part["forbid_env"],
                                                        `${what}.forbid_env`, FORBID, 64);
    }
    perCredential![kind] = Object.freeze({
      ...(part["argv"] === undefined ? {} : { argv: Object.freeze(argv(part["argv"], `${what}.argv`)) }),
      ...(part["env"] === undefined ? {} : { env: Object.freeze(envMap(part["env"], `${what}.env`)) }),
      ...(part["files"] === undefined ? {}
        : { files: Object.freeze(fileMap(part["files"], `${what}.files`)) }),
      ...(part["forbid_argv"] === undefined ? {} : { forbid_argv: Object.freeze(
        check.strings(part["forbid_argv"], `${what}.forbid_argv`, /^.{1,256}$/su, 16)) }),
      ...(part["forbid_env"] === undefined ? {}
        : { forbid_env: Object.freeze(forbidEnv[kind as CredentialKind]!) }),
    });
    if (part["forbid_argv"] !== undefined) {
      forbidden.push([kind as CredentialKind, part["forbid_argv"] as string[]]);
    }
  }

  const mcp = mcpSpec(check, fields["mcp"], argv, template, sandboxPath, declared);
  // The CLI's own pre-tool hook.
  const decide = decideSpec(check, fields["decide"], argv, template, declared);

  const engine = engineSpec(check, fields["engine"], argv);
  const definition = definitionSpec(check, fields["definition"], argv, template, decide);
  const tools = check.keys(fields["tools"], "cli.tools", [], ["allow", "deny"]);
  for (const side of ["allow", "deny"] as const) {
    if (tools[side] !== undefined) {
      tools[side] = toolFilter(check, tools[side], `cli.tools.${side}`, argv);
    }
  }
  const unattendedArgv = argv(fields["unattended_argv"], "cli.unattended_argv");
  const noUpdate = noUpdateSpec(check, fields["no_update"], argv, envMap);

  // Every file a part names is one the record writes.
  const named: [string, string | undefined][] = [
    ["cli.definition.instructions.setting", definition.instructions.setting],
    ["cli.no_update.file", "file" in noUpdate ? noUpdate.file : undefined],
    ...(["allow", "deny"] as const).map((side): [string, string | undefined] => [
      `cli.tools.${side}.file`,
      object(tools[side]) ? (tools[side] as { file?: string }).file : undefined]),
  ];
  for (const [what, path] of named) {
    if (path !== undefined && !declared.includes(path)) {
      check.fail(`${what} names a file the record writes (cli.files, cli.mcp.file or ` +
                 "cli.per_credential's files)");
    }
  }

  // An API key's variables, each provider's (`sandboxEnv` sets them).
  let apiKeyEnv: Record<string, ApiKeyEnv> | undefined;
  if (keyed) {
    const map = fields["api_key_env"];
    if (!object(map) || Object.keys(map).length === 0 || Object.keys(map).length > 32
        || !Object.keys(map).every((provider) => PROVIDER.test(provider))) {
      check.fail("cli.api_key_env maps 1 to 32 pi-ai provider ids to their variables");
    }
    apiKeyEnv = {};
    for (const [provider, entry] of Object.entries(map as Record<string, unknown>)) {
      const what = `cli.api_key_env.${provider}`;
      const vars = check.keys(entry, what, ["base_url", "api_key", "base"]);
      if ((vars["base_url"] !== null && !ENV_NAME.test(String(vars["base_url"])))
          || !ENV_NAME.test(String(vars["api_key"]))
          || (vars["base"] !== "sealed" && vars["base"] !== "connection")) {
        check.fail(`${what} names its base-URL variable (or null) and its key variable, and ` +
                   "a base of sealed or connection");
      }
      apiKeyEnv[provider] = Object.freeze(vars as unknown as ApiKeyEnv);
    }
    // A declared channel: no base-URL variable, so a file template or the API-key
    // run's own argv carries `{base_url}`.
    const keyArgv = argvs.find(([what]) => what === "cli.per_credential.api_key.argv")?.[1] ?? [];
    if (Object.values(apiKeyEnv).some((vars) => vars.base_url === null)
        && !templates.some(([, text]) => text.includes("{base_url}"))
        && !keyArgv.some((arg) => arg.includes("{base_url}"))) {
      check.fail("cli.api_key_env names no base-URL variable, so a file template or " +
                 "cli.per_credential.api_key.argv carries {base_url}");
    }
  }

  // A run's environment carries only its own credential: a run on the vendor's own
  // sign-in never carries a variable that would move it onto a key or another endpoint,
  // the record's own API-key variables first among them, and nothing the record sets for
  // a run on a credential is a variable that credential forbids.
  if (signsIn) {
    const owned = Object.values(apiKeyEnv ?? {}).flatMap((vars) =>
      vars.base_url === null ? [vars.api_key] : [vars.base_url, vars.api_key]);
    const listed = forbidEnv.vendor_login_in_sandbox ?? [];
    if (listed.length === 0 || owned.some((name) => !forbids(listed, name))) {
      check.fail("cli.per_credential.vendor_login_in_sandbox.forbid_env lists every " +
                 "variable that would move a signed-in run onto a key or another endpoint, " +
                 "each of its cli.api_key_env names among them");
    }
  }
  for (const kind of credential) {
    const sets = [...Object.keys(homeEnv), ...Object.keys(env ?? {}),
                  ...Object.keys(("env" in noUpdate ? noUpdate.env : undefined) ?? {}),
                  ...Object.keys((perCredential?.[kind] as PerCredential | undefined)?.env ?? {})];
    const set = sets.find((name) => forbids(forbidEnv[kind] ?? [], name));
    if (set !== undefined) {
      check.fail(`the record sets ${set}, which the ${kind} credential forbids`);
    }
    // The record's own variables are the run's allow-list (`runEnv`): none is shaped like
    // a credential or an endpoint, and none is HOME or a basic, which the runner sets.
    const loading = sets.find((name) => forbids(CODE_LOADING_ENV, name));
    if (loading !== undefined) {
      check.fail(`the record sets ${loading}, which loads code into the CLI's process`);
    }
    const shaped = sets.find((name) => CREDENTIAL_NAME.test(name) || name === "HOME"
      || RUN_BASICS.includes(name));
    if (shaped !== undefined) {
      check.fail(`the record sets ${shaped}: a record's own variables carry no credential ` +
                 "or endpoint, and the runner alone sets HOME and the process basics");
    }
  }

  // Its settings files carry no credential either: no environment block, no key shaped
  // like a credential or a credential helper, no variable a credential forbids, and, in a
  // file a signed-in run gets, no key that moves where its calls go.
  const settings = templates.filter(([what]) => what === "cli.mcp.template"
    || what.startsWith("cli.files.") || /^cli\.per_credential\.[a-z_]+\.files\./.test(what));
  for (const [what, text] of settings) {
    const kinds = what.startsWith("cli.per_credential.")
      ? [what.split(".")[2] as CredentialKind] : credential;
    const path = what === "cli.mcp.template" ? mcp.file ?? ""
      : what.slice(what.indexOf(".files.") + ".files.".length);
    const read = settingsOf(path, text);
    if (read === null) {
      return check.fail(`${what} is a settings file read whole: a .json file that parses, or ` +
                        "lines of bare keys (no quoted key, escape or inline table)");
    }
    for (const key of read.keys) {
      if (CREDENTIAL_KEY.test(key)) {
        check.fail(`${what} sets ${key}: a settings file carries no credential, credential ` +
                   "helper or environment block");
      }
      if (kinds.includes("vendor_login_in_sandbox") && ENDPOINT_KEY.test(key)) {
        check.fail(`${what} sets ${key}, which would move a signed-in run's calls`);
      }
    }
    for (const kind of kinds) {
      const named = (forbidEnv[kind] ?? []).find((entry) => new RegExp(
        `(?<![A-Z0-9_])${entry.endsWith("*") ? entry.slice(0, -1) : `${entry}(?![A-Z0-9_])`}`)
        .test([text, ...read.keys, ...read.values].join("\n")));
      if (named !== undefined) {
        check.fail(`${what} names ${named}, which the ${kind} credential forbids`);
      }
    }
  }

  // Secrets never leave the sandbox: outside /workspace, and outside every synced path.
  const secrets = check.strings(fields["secret_paths"], "cli.secret_paths", /^.{1,512}$/su, 16);
  const sessions = check.strings(fields["session_paths"], "cli.session_paths", /^.{1,512}$/su, 16);
  if (secrets.length === 0 || sessions.length === 0) {
    check.fail("cli.secret_paths and cli.session_paths each list at least one path");
  }
  // Both lists are under {home}, so the two compare in one namespace: no absolute path
  // (`/*/`, `/root/`, `/proc/self/cwd/...`) can sweep a secret into a sync unseen.
  const underHome = (what: string) => (path: string) => homePath(path, true)
    ?? check.fail(`${what} ${path} is a path under {home}, not {home} itself, with no . or ` +
                  ".. segment and no * as its first segment");
  const secretSegments = secrets.map(underHome("cli.secret_paths"));
  const sessionSegments = sessions.map(underHome("cli.session_paths"));
  for (const [index, secret] of secretSegments.entries()) {
    if (sessionSegments.some((session) => overlaps(secret, session))) {
      check.fail(`cli.secret_paths ${secrets[index]} is outside every session path: a ` +
                 "secret never syncs (ADR-039 decision 2.20)");
    }
  }
  // What a resume reads is some of the sessions, so never a secret: under a session path, a
  // segment's `*` a run of characters in it, `{sid}` the session id.
  const resumes = check.strings(fields["resume_paths"], "cli.resume_paths", /^.{1,512}$/su, 16);
  if (resumes.length === 0) {
    check.fail("cli.resume_paths lists at least one path: every engine resumes");
  }
  for (const path of resumes) {
    const segments = path.startsWith("{home}/")
      ? (path.endsWith("/") ? path.slice(0, -1) : path).slice("{home}/".length).split("/") : [];
    if (segments.length === 0 || !segments.every((segment) => RESUME_SEGMENT.test(segment))
        || !sessionSegments.some((session) => session.length <= segments.length
          && session.every((segment, i) => segment === segments[i]))) {
      check.fail(`cli.resume_paths ${path} is under one of cli.session_paths, each segment ` +
                 "names, * and {sid} alone, with no . or .. segment");
    }
  }
  const login = signsIn ? loginSpec(check, fields["login"], argv) : undefined;
  if (login !== undefined) {
    if (engine.model_argv === undefined) {
      check.fail("cli.engine.model_argv is present on a record that signs in: a run on the " +
                 "sign-in passes cli.login.model");
    }
    for (const state of login.state_checks ?? []) {
      const segments = homePath(state.path, false);
      if (segments === null || !secretSegments.some((secret) =>
        secret.length <= segments.length && overlaps(secret, segments))) {
        check.fail(`cli.login.state_checks ${state.path} is a credential file among the ` +
                   "secret paths");
      }
    }
  }

  // Placeholders the runner knows, `{sid}` only where a run resumes, and `{mcp_config}`
  // only beside the file it names.
  for (const [what, text] of [...argvs.flatMap(([what, list]) =>
    list.map((item): [string, string] => [what, item])), ...templates]) {
    for (const found of text.match(/\{[a-z_]+\}/g) ?? []) {
      if (!PLACEHOLDERS.includes(found)) {
        check.fail(`${what} uses ${found}, which is not a placeholder the runner fills`);
      }
      if (found === "{sid}" && what !== "cli.engine.resume_argv") {
        check.fail(`${what} uses {sid}, which only the resume argv carries`);
      }
      // A prompt is read as text, never as an option or a file include: on stdin, or
      // joined to its option in the engine's own argv.
      if (found === "{prompt}" && (!["cli.engine.argv", "cli.engine.resume_argv"].includes(what)
                                   || !PROMPT_ARG.test(text))) {
        check.fail(`${what} carries {prompt} other than as --option={prompt} in the engine's ` +
                   "argv: a prompt goes on stdin, or joined to its option, never bare");
      }
      // The run's own secret only in its MCP file's header, never an argument.
      if (found === "{run_secret}" && what !== "cli.mcp.template") {
        check.fail(`${what} uses {run_secret}, which only cli.mcp.template's header carries`);
      }
      if (found === "{name}" && what !== "cli.mcp.argv_each") {
        check.fail(`${what} uses {name}, which only cli.mcp.argv_each carries`);
      }
      if (found === "{skill}" && what !== "cli.definition.skills.argv_each") {
        check.fail(`${what} uses {skill}, which only cli.definition.skills.argv_each carries`);
      }
      if (HOOK_PLACEHOLDERS.includes(found) && what !== "cli.decide.template") {
        check.fail(`${what} uses ${found}, which only cli.decide.template carries`);
      }
      if (DISPATCH_PLACEHOLDERS.includes(found) && (!["cli.definition.hooks.template",
        "cli.definition.hooks.each"].includes(what) || (["{event}", "{native}", "{on_failure}"].includes(found)
          && what !== "cli.definition.hooks.each"))) {
        check.fail(`${what} uses ${found}, which only cli.definition.hooks's file carries`);
      }
      if (found === "{mcp_config}" && mcp.file === undefined) {
        check.fail(`${what} uses {mcp_config}, which names cli.mcp.file, and there is none`);
      }
    }
  }
  // `{base_url}` only where only an API-key run reads it: the API-key run's own
  // argv, env or files, or anywhere in a record that runs on nothing but a key; and in an
  // argv joined to its key, never bare.
  const keyOnly = credential.length === 1 && credential[0] === "api_key";
  for (const [what, text] of [...argvs.flatMap(([what, list]) =>
    list.map((item): [string, string] => [what, item])), ...templates]) {
    if (!text.includes("{base_url}")) {
      continue;
    }
    if (!keyOnly && !what.startsWith("cli.per_credential.api_key.")) {
      check.fail(`${what} carries {base_url}, which only an API-key run reads: put it under ` +
                 "cli.per_credential.api_key");
    }
    if (argvs.some(([name]) => name === what) && !BASE_URL_ARG.test(text)) {
      check.fail(`${what} carries {base_url} other than joined to its key ` +
                 "(--option={base_url}, key=\"{base_url}\"), never bare");
    }
  }
  // No argument a signed-in run gets moves where its calls go: every argv of a
  // record that signs in but the API-key run's own and the install's check.
  if (signsIn) {
    for (const [what, list] of argvs) {
      if (what.startsWith("cli.per_credential.api_key.") || what.startsWith("cli.install.")) {
        continue;
      }
      const moved = list.find((arg) => ENDPOINT_ARG.test(arg));
      if (moved !== undefined) {
        check.fail(`${what} holds ${JSON.stringify(moved)}, which would move a signed-in ` +
                   "run's calls to another endpoint or provider");
      }
    }
  }
  // A credential's forbidden arguments are in no argv of the record.
  for (const [kind, words] of forbidden) {
    for (const [what, list] of argvs) {
      const other = what.startsWith("cli.per_credential.") && !what.startsWith(
        `cli.per_credential.${kind}.`);
      if (!other && list.some((item) => words.includes(item))) {
        check.fail(`${what} holds an argument the ${kind} credential forbids`);
      }
    }
  }

  const hosts = check.keys(fields["hosts"], "cli.hosts", ["registry", "login", "provider"]);
  for (const kind of ["registry", "login", "provider"] as const) {
    check.strings(hosts[kind], `cli.hosts.${kind}`, HOST, 16);
  }
  return Object.freeze({
    install,
    credential: Object.freeze(credential),
    ...(login === undefined ? {} : { login }),
    ...(apiKeyEnv === undefined ? {} : { api_key_env: Object.freeze(apiKeyEnv) }),
    unattended: Object.freeze(unattended),
    ...(perCredential === undefined ? {} : { per_credential: Object.freeze(perCredential) }),
    engine,
    definition,
    mcp,
    decide,
    ...(runSecretEnv === undefined ? {} : { run_secret_env: Object.freeze(runSecretEnv) }),
    ...(compaction === undefined ? {} : { compaction: Object.freeze(compaction) }),
    tools: Object.freeze(tools),
    unattended_argv: Object.freeze(unattendedArgv),
    home_env: Object.freeze(homeEnv),
    ...(env === undefined ? {} : { env: Object.freeze(env) }),
    ...(files === undefined ? {} : { files: Object.freeze(files) }),
    no_update: noUpdate,
    secret_paths: Object.freeze(secrets),
    session_paths: Object.freeze(sessions),
    resume_paths: Object.freeze(resumes),
    hosts: Object.freeze(hosts as unknown as CliSpec["hosts"]),
  }) as CliSpec;
}

/** Whether a path is relative to the agent's directory, with no `.` or `..` segment. */
function relative(path: string): boolean {
  return !path.startsWith("/") && path.split("/").filter((part, i, all) =>
    !(i === all.length - 1 && part === "")).every((part) => SEGMENT.test(part) && part !== "*");
}

type Argv = (item: unknown, what: string, max?: number) => string[];

function installSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv): Install {
  const manager = object(value) ? value["manager"] : undefined;
  const pinned = (fields: Record<string, unknown>, what: string) => {
    if (!/^\d+\.\d+\.\d+$/.test(String(fields["version"]))
        || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(String(fields["bin"]))) {
      check.fail(`cli.install ${what}, its exact version and its binary's name`);
    }
    argv(fields["verify_argv"], "cli.install.verify_argv", 16);
  };
  if (manager === "npm") {
    const fields = check.keys(value, "cli.install", ["manager", "package", "version",
      "integrity", "bin", "verify_argv", "lock_sha256"], ["lifecycle_scripts"]);
    if (!/^(?:@[a-z0-9-]+\/)?[a-z0-9-]+$/.test(String(fields["package"]))
        || !/^sha512-[A-Za-z0-9+/]{86}==$/.test(String(fields["integrity"]))
        || !HEX64.test(String(fields["lock_sha256"]))
        || (fields["lifecycle_scripts"] !== undefined && fields["lifecycle_scripts"] !== true)) {
      check.fail("cli.install pins an npm package by exact version, integrity and lock digest");
    }
    pinned(fields, "pins an npm package");
    return Object.freeze(fields) as unknown as Install;
  }
  if (manager === "pip") {
    const fields = check.keys(value, "cli.install", ["manager", "package", "version", "bin",
                                                     "verify_argv", "lock_sha256"]);
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(String(fields["package"]))
        || !HEX64.test(String(fields["lock_sha256"]))) {
      check.fail("cli.install pins a pip package by exact version and hashed-lock digest");
    }
    pinned(fields, "pins a pip package");
    return Object.freeze(fields) as unknown as Install;
  }
  if (manager === "binary") {
    const fields = check.keys(value, "cli.install", ["manager", "url", "sha256", "version",
                                                     "bin", "verify_argv"]);
    check.https(fields["url"], "cli.install.url");
    if (!HEX64.test(String(fields["sha256"]))) {
      check.fail("cli.install pins a binary by its https URL and SHA-256");
    }
    pinned(fields, "pins a binary");
    return Object.freeze(fields) as unknown as Install;
  }
  return check.fail("cli.install.manager is npm, pip or binary");
}

/** An unattended verdict: allowed only with the vendor page and the day it was read. */
function verdict(check: ReturnType<typeof checker>, value: unknown, what: string): Unattended {
  const allowed = object(value) ? value["allowed"] : undefined;
  if (allowed === true) {
    const fields = check.keys(value, what, ["allowed", "cite", "read"], ["read_note"]);
    check.https(fields["cite"], `${what}.cite`);
    check.text(fields["read"], DATE, `${what}.read`);
    if (fields["read_note"] !== undefined) {
      check.text(fields["read_note"], REASON, `${what}.read_note`);
    }
    return Object.freeze(fields) as unknown as Unattended;
  }
  if (allowed === false) {
    const fields = check.keys(value, what, ["allowed", "reason"], ["cite", "read",
                                                                 "read_note"]);
    check.text(fields["reason"], REASON, `${what}.reason`);
    if ((fields["cite"] === undefined) !== (fields["read"] === undefined)) {
      check.fail(`${what} cites a page together with the day it was read`);
    }
    if (fields["cite"] !== undefined) {
      check.https(fields["cite"], `${what}.cite`);
      check.text(fields["read"], DATE, `${what}.read`);
    }
    if (fields["read_note"] !== undefined) {
      if (fields["cite"] === undefined) {
        check.fail(`${what}.read_note says how its cited page was read, so it has a cite`);
      }
      check.text(fields["read_note"], REASON, `${what}.read_note`);
    }
    return Object.freeze(fields) as unknown as Unattended;
  }
  return check.fail(`${what}.allowed is true, with the vendor page that permits it and the ` +
                    "day it was read (ADR-033 decision 3), or false, with the reason");
}

/** Whether `pattern` is a regular expression of at most 512 units, under its reader's `flags`. */
function compiles(pattern: unknown, flags: string): boolean {
  if (typeof pattern !== "string" || pattern.length < 1 || pattern.length > 512) {
    return false;
  }
  try {
    new RegExp(pattern, flags);
    return true;
  } catch {
    return false;
  }
}

function matchers(check: ReturnType<typeof checker>, value: unknown, what: string):
    readonly EventMatcher[] {
  if (!Array.isArray(value) || value.length > 8) {
    return check.fail(`${what} is a list of at most 8 matchers`);
  }
  return Object.freeze(value.map((entry, index) => {
    const fields = check.keys(entry, `${what}[${index}]`, ["match", "path"],
                              ["accumulate", "pattern"]);
    const match = fields["match"];
    if (!object(match) || Object.keys(match).length === 0 || Object.keys(match).length > 8
        || !Object.entries(match).every(([key, item]) => DOTTED.test(key)
          && ["string", "number", "boolean"].includes(typeof item))) {
      check.fail(`${what}[${index}].match maps 1 to 8 dotted paths to scalars`);
    }
    const pattern = fields["pattern"];
    if (pattern !== undefined && (!object(pattern) || Object.keys(pattern).length === 0
        || Object.keys(pattern).length > 8
        || !Object.entries(pattern).every(([key, item]) => DOTTED.test(key)
          && compiles(item, PATTERN_FLAGS)))) {
      check.fail(`${what}[${index}].pattern maps 1 to 8 dotted paths to regular expressions`);
    }
    if (fields["path"] !== "" && !DOTTED.test(String(fields["path"]))) {
      check.fail(`${what}[${index}].path is a dotted path, or empty for the event itself`);
    }
    if (fields["accumulate"] !== undefined && fields["accumulate"] !== true) {
      check.fail(`${what}[${index}].accumulate is true or absent`);
    }
    return Object.freeze({ ...fields, match: Object.freeze({ ...match as object }),
                           ...(pattern === undefined ? {}
                             : { pattern: Object.freeze({ ...pattern as object }) }) }) as
      unknown as EventMatcher;
  }));
}

function engineSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv): Engine {
  if (object(value) && "invoke" in value) {
    check.fail("cli.engine has no invoke: a CLI is an engine, never a backend (ADR-033 " +
               "decision 2)");
  }
  const fields = check.keys(value, "cli.engine", ["argv", "resume_argv", "events"],
                            ["stdin", "model_argv"]);
  const run = argv(fields["argv"], "cli.engine.argv");
  const resume = argv(fields["resume_argv"], "cli.engine.resume_argv");
  if (run[0] !== "{bin}" || resume[0] !== "{bin}") {
    check.fail("cli.engine's argv and resume_argv each run {bin}");
  }
  if (!resume.some((item) => item.includes("{sid}"))) {
    check.fail("cli.engine.resume_argv carries {sid}: a run resumes by its session id");
  }
  if (fields["stdin"] !== undefined && fields["stdin"] !== "{prompt}") {
    check.fail("cli.engine.stdin is {prompt} or absent");
  }
  const prompted = fields["stdin"] !== undefined;
  for (const [what, list] of [["argv", run], ["resume_argv", resume]] as const) {
    if (prompted === list.some((item) => item.includes("{prompt}"))) {
      check.fail(`cli.engine.${what} carries {prompt} exactly when the prompt is not on stdin`);
    }
  }
  const model = fields["model_argv"] === undefined ? undefined
    : argv(fields["model_argv"], "cli.engine.model_argv", 8);
  const kinds = ["session_id", "final_text", "error", "usage", "tool_call", "deferred",
                 "vendor_limit", "retry"] as const;
  const events = check.keys(fields["events"], "cli.engine.events", ["format", ...kinds]);
  if (events["format"] !== "jsonl" && events["format"] !== "json") {
    check.fail("cli.engine.events.format is jsonl or json");
  }
  for (const kind of kinds) {
    events[kind] = matchers(check, events[kind], `cli.engine.events.${kind}`);
  }
  if ((events["final_text"] as unknown[]).length === 0) {
    check.fail("cli.engine.events.final_text has at least one matcher");
  }
  if ((events["session_id"] as unknown[]).length === 0) {
    check.fail("cli.engine.events.session_id has at least one matcher: a run resumes by it");
  }
  return Object.freeze({
    argv: Object.freeze(run), resume_argv: Object.freeze(resume),
    ...(prompted ? { stdin: "{prompt}" as const } : {}),
    ...(model === undefined ? {} : { model_argv: Object.freeze(model) }),
    events: Object.freeze(events) as unknown as EngineEvents,
  });
}

function instructionsSpec(check: ReturnType<typeof checker>, value: unknown): Instructions {
  const what = "cli.definition.instructions";
  const fields = check.keys(value, what, ["file", "shadowed_by"], ["max_bytes", "setting"]);
  const names = check.strings(fields["shadowed_by"], `${what}.shadowed_by`, /^.{1,256}$/su, 8);
  if (typeof fields["file"] !== "string" || !relative(fields["file"])
      || !names.every(relative) || names.includes(fields["file"])) {
    check.fail(`${what} names its file, and what shadows it, relative to the agent's directory`);
  }
  const max = fields["max_bytes"];
  if (max !== undefined && !whole(max, 1)) {
    check.fail(`${what}.max_bytes is a whole number from 1`);
  }
  if (fields["setting"] !== undefined && runFilePath(fields["setting"]) === null) {
    check.fail(`${what}.setting is a path under {home} or {system}`);
  }
  return Object.freeze({ ...fields, shadowed_by: Object.freeze(names) }) as unknown as
    Instructions;
}

/** A key of an engine's own config: a server entry's, or a hook event's name. */
const NATIVE_KEY = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
/** What a server's entry carries of the server's own, by transport (`Mcp`). */
const SERVER_CARRY = Object.freeze({
  http: Object.freeze(["{url}"]),
  stdio: Object.freeze(["{command}", "{args}", "{env}"]),
});

/**
 * How the agent's definition reaches the engine (`Definition`): its instructions; its skills,
 * linked into folders relative to the agent's directory or given one argv a skill; the engine's
 * own layers it keeps out, none of them a path the definition renders; and its hooks'
 * dispatcher, held to the `decide` hook's root-owned layer, so present exactly when that hook
 * is (`definitionHooks`). Its own MCP servers go where the run's door does (`mcpSpec`).
 */
function definitionSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                        template: (item: unknown, what: string) => string,
                        decide: Decide): Definition {
  const fields = check.keys(value, "cli.definition", ["instructions", "skills", "native",
                                                      "hooks"]);
  const instructions = instructionsSpec(check, fields["instructions"]);
  const skills = definitionSkills(check, fields["skills"], argv);
  return Object.freeze({
    instructions, skills,
    native: definitionNative(check, fields["native"], argv,
                             [instructions.file, ...instructions.shadowed_by, ...skills.paths]),
    hooks: definitionHooks(check, fields["hooks"], argv, template, decide),
  });
}

function definitionSkills(check: ReturnType<typeof checker>, value: unknown, argv: Argv):
    DefinitionSkills {
  const what = "cli.definition.skills";
  const fields = check.keys(value, what, ["paths"], ["argv_each"]);
  const paths = check.strings(fields["paths"], `${what}.paths`, /^.{1,256}$/su, 16);
  const each = fields["argv_each"] === undefined ? undefined
    : argv(fields["argv_each"], `${what}.argv_each`, 4);
  if (!paths.every(relative) || (paths.length === 0 && each === undefined)) {
    check.fail(`${what} links each skill into paths relative to the agent's directory, or ` +
               "gives it argv_each, or both");
  }
  if (each !== undefined && each.join(" ").split("{skill}").length !== 2) {
    check.fail(`${what}.argv_each carries {skill}, the skill's directory, once`);
  }
  return Object.freeze({ paths: Object.freeze(paths),
                         ...(each === undefined ? {} : { argv_each: Object.freeze(each) }) });
}

function definitionNative(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                          rendered: readonly string[]): DefinitionNative {
  const what = "cli.definition.native";
  const fields = check.keys(value, what, ["refuses", "cite", "read"], ["argv"]);
  const refuses = check.strings(fields["refuses"], `${what}.refuses`, /^.{1,256}$/su, 16);
  if (refuses.length === 0 || !refuses.every(relative) || new Set(refuses).size !== refuses.length
      || refuses.some((path) => rendered.some((own) =>
        overlaps(path.split("/"), own.split("/"))))) {
    check.fail(`${what}.refuses lists the engine's own layers' paths, distinct and relative ` +
               "to its cwd, none of them one the definition renders (its instructions, what " +
               "shadows them, its skills folders)");
  }
  const loads = fields["argv"] === undefined ? undefined
    : argv(fields["argv"], `${what}.argv`, 8);
  check.https(fields["cite"], `${what}.cite`);
  check.text(fields["read"], DATE, `${what}.read`);
  return Object.freeze({
    refuses: Object.freeze(refuses), ...(loads === undefined ? {} : { argv: Object.freeze(loads) }),
    cite: fields["cite"] as string, read: fields["read"] as string,
  });
}

/**
 * The agent's hooks' dispatcher (`DefinitionHooks`): the engine's own distinct name of every
 * event; its file, the decide hook's (its only shared one) or another of the run's own under
 * `{system}`, a `.json`, `.toml` or `.mjs` one; a settings file's `template` and `each` (each
 * event's entry, `{event}` and `{native}` in it) read whole once filled, with no credential key,
 * carrying `{dispatcher}`; an `.mjs` file's `template` carrying `{dispatcher_argv}` and
 * `{hook_events}` and naming each event's own name; `argv` only for a file of the run's own, and
 * loading it; how the engine runs it, whether it fails open, how the agent can alter it, and
 * the page it was read from.
 */
function definitionHooks(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                         template: (item: unknown, what: string) => string, decide: Decide):
    DefinitionHooks {
  const what = "cli.definition.hooks";
  if (object(value) && "unsupported" in value) {
    const fields = check.keys(value, what, ["unsupported"]);
    if (!("unsupported" in decide)) {
      check.fail(`${what} is unsupported only where cli.decide is: its hooks go in that layer`);
    }
    return Object.freeze({ unsupported: check.text(fields["unsupported"], REASON,
                                                   `${what}.unsupported`) });
  }
  if ("unsupported" in decide) {
    check.fail(`${what} is unsupported where cli.decide is: no root-owned layer holds them`);
  }
  const fields = check.keys(value, what, ["events", "file", "runs", "fails", "cite", "read"],
                            ["template", "each", "argv", "alterable"]);
  const events = check.keys(fields["events"], `${what}.events`, Object.keys(HOOK_EVENTS));
  const names = Object.values(events) as string[];
  if (!names.every((name) => typeof name === "string" && NATIVE_KEY.test(name))
      || new Set(names).size !== names.length) {
    check.fail(`${what}.events names each event by the engine's own distinct name`);
  }
  const file = fields["file"];
  const own = typeof file === "string" && file.startsWith("{system}/")
    && runFilePath(file) !== null;
  if (typeof file !== "string" || (!own && ("unsupported" in decide || file !== decide.file))
      || !/\.(?:json|toml|mjs)$/.test(file)) {
    check.fail(`${what}.file is cli.decide.file, or a .json, .toml or .mjs file of the run's ` +
               "own under {system}");
  }
  const text = fields["template"] === undefined ? undefined
    : template(fields["template"], `${what}.template`);
  const each = fields["each"] === undefined ? undefined
    : template(fields["each"], `${what}.each`);
  if (String(file).endsWith(".mjs")) {
    if (each !== undefined || text === undefined || !text.includes("{dispatcher_argv}")
        || !text.includes("{hook_events}")
        || names.some((name) => !text.includes(JSON.stringify(name)))) {
      check.fail(`${what}.template is the .mjs file whole, carrying {dispatcher_argv} and ` +
                 "{hook_events} and naming each event's own name");
    }
  } else {
    const settings = [...(text === undefined ? [] : [text]),
                      ...names.map((name, at) => each === undefined ? ""
                        : each.replaceAll("{native}", name)
                          .replaceAll("{event}", Object.keys(events)[at]!))];
    if (each === undefined || !each.includes("{dispatcher}") || settings.some((part) => {
      const read = settingsOf(String(file), part);
      return read === null || read.keys.some((key) => CREDENTIAL_KEY.test(key));
    })) {
      check.fail(`${what}.template and each are settings read whole, each event's entry ` +
                 "carrying {dispatcher}, with no credential key");
    }
  }
  const loads = fields["argv"] === undefined ? undefined
    : argv(fields["argv"], `${what}.argv`, 8);
  if (loads !== undefined && (!own || !loads.includes(file as string))) {
    check.fail(`${what}.argv loads the run's own file, under {system}`);
  }
  const runs = (object(fields["runs"]) ? fields["runs"] : {}) as Record<string, unknown>;
  const shell = runs["shell"];
  const exec = Object.keys(runs).length === 1 && runs["exec"] === true;
  if (!exec && !(Object.keys(runs).length === 1 && Array.isArray(shell) && shell.length >= 1
      && shell.length <= 4 && shell.every((arg) => typeof arg === "string"
        && /^[A-Za-z0-9/_.-]{1,64}$/.test(arg)))) {
    check.fail(`${what}.runs is a shell and its arguments, or exec`);
  }
  if (exec !== String(file).endsWith(".mjs")) {
    check.fail(`${what}.runs is exec exactly where its file is the record's own .mjs`);
  }
  if (fields["fails"] !== "open" && fields["fails"] !== "closed") {
    check.fail(`${what}.fails is open or closed`);
  }
  if (fields["alterable"] !== undefined) {
    check.text(fields["alterable"], REASON, `${what}.alterable`);
  }
  check.https(fields["cite"], `${what}.cite`);
  check.text(fields["read"], DATE, `${what}.read`);
  return Object.freeze({
    events: Object.freeze({ ...events }) as Readonly<Record<HookEvent, string>>,
    file: file as string,
    ...(text === undefined ? {} : { template: text }),
    ...(each === undefined ? {} : { each }),
    ...(loads === undefined ? {} : { argv: Object.freeze(loads) }),
    runs: Object.freeze(exec ? { exec: true as const }
      : { shell: Object.freeze([...(shell as string[])]) }),
    fails: fields["fails"] as "open" | "closed",
    ...(fields["alterable"] === undefined ? {} : { alterable: fields["alterable"] as string }),
    cite: fields["cite"] as string, read: fields["read"] as string,
  });
}

/**
 * Where a run's MCP servers go (`Mcp`), the run's door and the agent's own alike: under
 * `servers` in the record's JSON file (its template, if any, holding nothing of the servers but
 * the door's own keys, and nothing at `names`) or as `flag` pairs, exactly one of the two; each
 * transport's entry carrying the server's own values once each; `argv_each` carrying `{name}`,
 * joined to its option (`NAME_ARG`).
 */
function mcpSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                 template: (item: unknown, what: string) => string,
                 sandboxPath: (item: unknown, what: string) => string[],
                 declared: string[]): Mcp {
  const what = "cli.mcp";
  const fields = check.keys(value, what, ["servers", "http", "stdio", "argv", "trust"],
                            ["file", "template", "flag", "names", "argv_each"]);
  const servers = check.text(fields["servers"], DOTTED, `${what}.servers`);
  const entries = Object.fromEntries((["http", "stdio"] as const).map((transport) => {
    const entry = check.stringMap(fields[transport], `${what}.${transport}`, NATIVE_KEY, 16);
    const carried = Object.values(entry).filter((item) => item.includes("{"));
    if (carried.length !== SERVER_CARRY[transport].length
        || !SERVER_CARRY[transport].every((item) => carried.includes(item))
        || Object.values(entry).some((item) => !REASON.test(item))) {
      check.fail(`${what}.${transport} carries ${SERVER_CARRY[transport].join(", ")} once ` +
                 "each, and otherwise fixed text");
    }
    return [transport, Object.freeze({ ...entry })];
  })) as Record<"http" | "stdio", Readonly<Record<string, string>>>;
  if ((fields["file"] === undefined) === (fields["flag"] === undefined)
      || (fields["file"] === undefined
          && (fields["template"] !== undefined || fields["names"] !== undefined))) {
    check.fail(`${what} puts its servers in a file (with its template and names, if any) or ` +
               "by a flag, one of the two");
  }
  if (fields["flag"] !== undefined) {
    check.text(fields["flag"], /^-{1,2}[A-Za-z0-9][A-Za-z0-9-]*$/, `${what}.flag`);
  }
  const names = fields["names"] === undefined ? undefined
    : check.text(fields["names"], DOTTED, `${what}.names`);
  let text: string | undefined;
  if (fields["file"] !== undefined) {
    sandboxPath(fields["file"], `${what}.file`);
    if (!(fields["file"] as string).endsWith(".json")) {
      check.fail(`${what}.file is a .json file, which the servers are rendered into`);
    }
    declared.push(fields["file"] as string);
  }
  if (fields["template"] !== undefined) {
    text = template(fields["template"], `${what}.template`);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
    const held = valueAtPath(parsed, servers.split("."));
    if (!object(parsed) || (held !== undefined && (!object(held)
        || Object.keys(held).some((name) => name !== RUN_DOOR_SERVER)))
        || (names !== undefined && valueAtPath(parsed, names.split(".")) !== undefined)) {
      check.fail(`${what}.template is a JSON object holding no server but the door's own ` +
                 `keys (${RUN_DOOR_SERVER}), and nothing at names: the servers are rendered`);
    }
  }
  const each = fields["argv_each"] === undefined ? undefined
    : argv(fields["argv_each"], `${what}.argv_each`, 8);
  if (each !== undefined && (!each.some((arg) => arg.includes("{name}"))
      || each.some((arg) => arg.includes("{name}") && !NAME_ARG.test(arg)))) {
    check.fail(`${what}.argv_each carries {name}, the server's name, joined to its option ` +
               "(--option={name})");
  }
  return Object.freeze({
    servers, ...entries,
    ...(fields["file"] === undefined ? {} : { file: fields["file"] as string }),
    ...(text === undefined ? {} : { template: text }),
    ...(fields["flag"] === undefined ? {} : { flag: fields["flag"] as string }),
    ...(names === undefined ? {} : { names }),
    argv: Object.freeze(argv(fields["argv"], `${what}.argv`, 16)),
    ...(each === undefined ? {} : { argv_each: Object.freeze(each) }),
    trust: check.text(fields["trust"], REASON, `${what}.trust`),
  });
}

/** A tool's name as a CLI calls it, and an MCP name's prefix and join. */
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,64}$/;
const NAME_PART = /^[A-Za-z0-9_]{1,16}$/;
/** A shared hook file's path: under `/etc`, no `.` or `..` segment. */
const SHARED_PATH = /^\/etc(?:\/(?!\.{1,2}(?:\/|$))[A-Za-z0-9._-]{1,128}){1,4}$/;

/**
 * The CLI's own pre-tool hook (`Decide`): its file under `{system}` (merged into the record's
 * JSON file of that path, if it writes one) or a shared root path under `/etc`, a `.json`,
 * `.toml` or `.mjs` file whose template carries `{matcher}` and what its way of running
 * carries (`RUNS_CARRY`), and no credential key; how the CLI runs it (an `http` hook's refusal
 * a JSON template whose one `{reason}` the door fills, a shell its program and arguments, or
 * `exec`); its argv, only for a file of the run's own; its kinds' tool names; its MCP names;
 * whether it fails open, and how the agent can alter it, if it can; and the page it was read
 * from. Or why no hook holds.
 */
function decideSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                    template: (item: unknown, what: string) => string,
                    declared: readonly string[]): Decide {
  if (object(value) && "unsupported" in value) {
    const fields = check.keys(value, "cli.decide", ["unsupported"]);
    return Object.freeze({ unsupported: check.text(fields["unsupported"], REASON,
                                                   "cli.decide.unsupported") });
  }
  const fields = check.keys(value, "cli.decide", ["file", "template", "runs", "kinds", "mcp",
                                                  "fails", "cite", "read"],
                            ["argv", "alterable"]);
  const runs = (object(fields["runs"]) ? fields["runs"] : {}) as Record<string, unknown>;
  const way = Object.keys(runs).length === 1 ? Object.keys(runs)[0] : undefined;
  const http = runs["http"];
  const deny = way === "http" && object(http) && Object.keys(http).length === 1
    ? http["deny"] : undefined;
  let denyParses = false;
  try {
    denyParses = typeof deny === "string" && object(JSON.parse(deny))
      && deny.split("{reason}").length === 2;
  } catch {
    denyParses = false;
  }
  const shell = runs["shell"];
  if (!(way === "http" ? denyParses
    : way === "shell" ? Array.isArray(shell) && shell.length >= 1 && shell.length <= 4
      && shell.every((arg) => typeof arg === "string" && /^[A-Za-z0-9/_.-]{1,64}$/.test(arg))
      : way === "exec" && runs["exec"] === true)) {
    check.fail("cli.decide.runs is http (its deny a JSON object with one {reason}), a shell " +
               "and its arguments, or exec");
  }
  const file = fields["file"];
  const own = typeof file === "string" && file.startsWith("{system}/")
    && runFilePath(file) !== null;
  if (typeof file !== "string" || (!own && !SHARED_PATH.test(file))
      || !/\.(?:json|toml|mjs)$/.test(file)
      || (declared.includes(file) && !file.endsWith(".json"))) {
    check.fail("cli.decide.file is a .json, .toml or .mjs path under {system}, or under /etc " +
               "shared by every run, and only a .json file the record writes besides");
  }
  const text = template(fields["template"], "cli.decide.template");
  const carries = ["{matcher}", ...RUNS_CARRY[way as keyof typeof RUNS_CARRY]];
  if (!HOOK_PLACEHOLDERS.every((placeholder) => text.includes(placeholder)
                                                === carries.includes(placeholder))) {
    check.fail(`cli.decide.template carries ${carries.join(", ")}, and no other hook ` +
               "placeholder");
  }
  if (!String(file).endsWith(".mjs")) {
    const read = settingsOf(String(file), text);
    if (read === null || read.keys.some((key) => CREDENTIAL_KEY.test(key))) {
      check.fail("cli.decide.template is a settings file read whole, with no credential key");
    }
  }
  const loads = fields["argv"] === undefined ? undefined
    : argv(fields["argv"], "cli.decide.argv", 8);
  if (loads !== undefined && own && !loads.includes(file as string)) {
    check.fail("cli.decide.argv loads the run's own hook file, under {system}");
  }
  const kinds = fields["kinds"];
  if (!object(kinds) || Object.keys(kinds).length === 0 || !Object.entries(kinds).every(
    ([kind, names]) => isHookKind(kind) && Array.isArray(names) && names.length > 0
      && names.length <= 8 && names.every((name) => typeof name === "string"
        && TOOL_NAME.test(name)))) {
    check.fail("cli.decide.kinds maps tool kinds (acts.json) to the CLI's names of their tools");
  }
  const mcp = check.keys(fields["mcp"], "cli.decide.mcp", ["prefix", "join"]);
  check.text(mcp["prefix"], NAME_PART, "cli.decide.mcp.prefix");
  check.text(mcp["join"], NAME_PART, "cli.decide.mcp.join");
  if (fields["fails"] !== "open" && fields["fails"] !== "closed") {
    check.fail("cli.decide.fails is open or closed");
  }
  if (fields["alterable"] !== undefined) {
    check.text(fields["alterable"], REASON, "cli.decide.alterable");
  }
  check.https(fields["cite"], "cli.decide.cite");
  check.text(fields["read"], DATE, "cli.decide.read");
  return Object.freeze({
    ...fields, ...(loads === undefined ? {} : { argv: Object.freeze(loads) }),
    kinds: Object.freeze(Object.fromEntries(Object.entries(kinds as Record<string, string[]>)
      .map(([kind, names]) => [kind, Object.freeze([...names])]))),
    mcp: Object.freeze(mcp),
    runs: Object.freeze(way === "http" ? { http: Object.freeze({ deny }) }
      : way === "shell" ? { shell: Object.freeze([...(shell as string[])]) }
      : { exec: true }),
  }) as unknown as Decide;
}

function toolFilter(check: ReturnType<typeof checker>, value: unknown, what: string,
                    argv: Argv): ToolFilter {
  if (object(value) && "file" in value) {
    const fields = check.keys(value, what, ["file", "key"]);
    if (runFilePath(fields["file"]) === null || !DOTTED.test(String(fields["key"]))) {
      check.fail(`${what} names a file under {home} or {system} and a dotted key`);
    }
    return Object.freeze(fields) as unknown as ToolFilter;
  }
  const each = object(value) && "each" in value;
  const fields = check.keys(value, what, ["argv", each ? "each" : "join"]);
  const list = argv(fields["argv"], `${what}.argv`, 8);
  const hole = each ? "{tool}" : "{tools}";
  if (each ? fields["each"] !== true : typeof fields["join"] !== "string"
      || !/^.{1,4}$/su.test(fields["join"])) {
    check.fail(`${what} joins its tools with 1 to 4 characters, or repeats per tool (each: true)`);
  }
  if (!list.some((item) => item.includes(hole))
      || list.some((item) => item.includes(each ? "{tools}" : "{tool}"))) {
    check.fail(`${what}.argv carries ${hole}`);
  }
  return Object.freeze({ ...fields, argv: Object.freeze(list) }) as unknown as ToolFilter;
}

function noUpdateSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                      envMap: (item: unknown, what: string) => Record<string, string>):
    CliSpec["no_update"] {
  if (object(value) && "reason" in value) {
    const fields = check.keys(value, "cli.no_update", ["reason"]);
    return Object.freeze({ reason: check.text(fields["reason"], REASON, "cli.no_update.reason") });
  }
  const fields = check.keys(value, "cli.no_update", [], ["env", "argv", "file"]);
  if (Object.keys(fields).length === 0) {
    check.fail("cli.no_update pins the binary by env, argv or a file, or says why it needs none");
  }
  return Object.freeze({
    ...(fields["env"] === undefined ? {}
      : { env: Object.freeze(envMap(fields["env"], "cli.no_update.env")) }),
    ...(fields["argv"] === undefined ? {}
      : { argv: Object.freeze(argv(fields["argv"], "cli.no_update.argv", 16)) }),
    ...(fields["file"] === undefined ? {} : { file: String(fields["file"]) }),
  });
}
const HOSTED_USE: readonly HostedUse[] = Object.freeze(["allowed", "unclear", "forbidden"]);

/** A URL parameter's name, as a record pins it. */
const PARAM_NAME = /^[a-z][a-z0-9_]{0,63}$/;
/** A model id as a CLI's `--model` takes it: a vendor's id, or its alias with a `/` prefix. */
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;

/** The vendor's own sign-in in the sandbox (`LoginSpec`), checked whole, frozen. */
function loginSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv): LoginSpec {
  const fields = check.keys(value, "cli.login", ["argv", "url_pattern", "url", "expires",
                                                 "hosted_use", "model"], [
    "status_argv", "status_equals", "accepts_code", "code_pattern", "state_checks",
    "complete_on_state", "logout_argv"]);
  argv(fields["argv"], "cli.login.argv", 16);
  if (fields["status_argv"] !== undefined) {
    argv(fields["status_argv"], "cli.login.status_argv", 16);
  }
  if (fields["logout_argv"] !== undefined) {
    argv(fields["logout_argv"], "cli.login.logout_argv", 16);
  }
  const expires = check.keys(fields["expires"], "cli.login.expires", ["s", "citation"]);
  if (!whole(expires["s"], 1)) {
    check.fail("cli.login.expires.s is the vendor's code's lifetime, whole seconds");
  }
  check.text(expires["citation"], CITATION, "cli.login.expires.citation");
  const page = check.keys(fields["url"], "cli.login.url", ["hosts", "path", "params", "free"]);
  if (check.strings(page["hosts"], "cli.login.url.hosts", HOST, 8).length === 0) {
    check.fail("cli.login.url.hosts names the sign-in page's hosts");
  }
  if (typeof page["path"] !== "string" || !/^\/[!-~]{0,255}$/.test(page["path"])
      || /[?#]/.test(page["path"])) {
    check.fail("cli.login.url.path is the sign-in page's path exactly");
  }
  const params = page["params"];
  if (!object(params) || Object.keys(params).length > 16
      || !Object.entries(params).every(([key, item]) => PARAM_NAME.test(key)
        && typeof item === "string" && item.length <= 1024)) {
    check.fail("cli.login.url.params maps parameter names to their fixed values");
  }
  const free = check.strings(page["free"], "cli.login.url.free", PARAM_NAME, 8);
  if (free.some((key) => key in (params as Record<string, unknown>))) {
    check.fail("cli.login.url.free names parameters the record does not fix");
  }
  const hosted = check.keys(fields["hosted_use"], "cli.login.hosted_use",
                            ["verdict", "citation"]);
  if (!(HOSTED_USE as readonly unknown[]).includes(hosted["verdict"])) {
    check.fail("cli.login.hosted_use.verdict is allowed, unclear or forbidden");
  }
  check.text(hosted["citation"], CITATION, "cli.login.hosted_use.citation");
  const model = check.keys(fields["model"], "cli.login.model", ["id", "citation"]);
  check.text(model["id"], MODEL_ID, "cli.login.model.id");
  check.text(model["citation"], CITATION, "cli.login.model.citation");
  for (const key of ["url_pattern", "code_pattern"] as const) {
    const pattern = fields[key];
    if (pattern !== undefined && !compiles(pattern, "")) {
      check.fail(`cli.login.${key} is a regular expression`);
    }
  }
  for (const key of ["accepts_code", "complete_on_state"] as const) {
    if (fields[key] !== undefined && fields[key] !== true) {
      check.fail(`cli.login.${key} is true or absent`);
    }
  }
  const equals = fields["status_equals"];
  if (equals !== undefined && (!object(equals) || !Object.entries(equals).every(([key, item]) =>
    DOTTED.test(key) && ["string", "number", "boolean"].includes(typeof item)))) {
    check.fail("cli.login.status_equals maps dotted paths to scalars");
  }
  const states = fields["state_checks"];
  if (states !== undefined) {
    if (!Array.isArray(states) || states.length > 4) {
      check.fail("cli.login.state_checks is a list of at most 4 checks");
    }
    for (const [index, state] of (states as unknown[]).entries()) {
      const what = `cli.login.state_checks[${index}]`;
      const check1 = check.keys(state, what, ["path", "present"]);
      const present = check.strings(check1["present"], `${what}.present`, DOTTED, 8);
      if (present.length === 0) {
        check.fail(`${what}.present names at least one field`);
      }
    }
  }
  return Object.freeze(fields) as unknown as LoginSpec;
}

/**
 * A gateway source's `credit` (`schema.ts` `Credit`): its read by the gateway's own rule
 * (`creditRead`), a `GET` its endpoints allow; 1 to 8 distinct HTTP error statuses; 1 to 16
 * distinct model patterns, each a model id's characters (`*` among them), the gateway's rule;
 * and the vendor's page.
 */
function creditSpec(check: ReturnType<typeof checker>, value: unknown, sealed: Source):
    Credit {
  const fields = check.keys(value, "credit", ["url", "none_when", "refused", "free", "cite"]);
  let read: CreditRead;
  try {
    read = creditRead({ url: fields["url"], none_when: fields["none_when"] });
  } catch {
    return check.fail("credit's url and none_when are refused by the gateway's own check");
  }
  if (!allows(sealed.endpoints, "GET", new URL(read.url))) {
    check.fail("credit.url is a GET its endpoints allow");
  }
  const refused = fields["refused"];
  if (!Array.isArray(refused) || refused.length < 1 || refused.length > 8
      || !refused.every((status) => whole(status, 400, 599))
      || new Set(refused).size !== refused.length) {
    check.fail("credit.refused lists HTTP error statuses, at least one, each once");
  }
  const free = check.strings(fields["free"], "credit.free", MODEL_PATTERN, 16);
  if (free.length === 0 || new Set(free).size !== free.length) {
    check.fail("credit.free lists model patterns, at least one, each once");
  }
  return Object.freeze({ ...read, refused: Object.freeze([...refused as number[]]),
                         free: Object.freeze(free),
                         cite: check.https(fields["cite"], "credit.cite") });
}

/** One record, checked whole: a copy, frozen, or a `SourceRecordError`. */
export function sourceRecord(value: unknown): SourceRecord {
  const copy: unknown = structuredClone(value);
  const id = object(copy) && typeof copy["id"] === "string" ? copy["id"] : "?";
  const check = checker(id);
  if (!object(copy) || !SOURCE_ID.test(id)) {
    return check.fail("a record is an object whose id is a source id");
  }
  const connect = check.strings(copy["connect"], "connect", /^(?:api_key|oauth|cli)$/, 2)
    .map((method) => method as ConnectMethod);
  if (connect.length === 0 || new Set(connect).size !== connect.length
      || (connect.includes("cli") && connect.length !== 1)) {
    check.fail("connect lists api_key and oauth, or cli alone, each once");
  }
  const base = ["id", "display_name", "connect"];
  const optional = ["help_url", "suggested", "curated"];
  const cli = connect.includes("cli");
  const fields = cli ? check.keys(copy, "the record", [...base, "cli"], optional)
    : check.keys(copy, "the record",
                 [...base, ...GATEWAY_KEYS.filter((key) => key !== "models"), "engines",
                  ...(connect.includes("oauth") ? ["oauth"] : [])],
                 [...optional, "models", "not_interactive", "credit"]);
  const record: Record<string, unknown> = {
    id,
    display_name: check.text(fields["display_name"], TEXT, "display_name"),
    suggested: fields["suggested"] === undefined ? false
      : fields["suggested"] === true ? true : check.fail("suggested is true or absent"),
    connect: Object.freeze(connect),
  };
  if (fields["help_url"] !== undefined) {
    record["help_url"] = check.https(fields["help_url"], "help_url");
  }
  if (fields["curated"] !== undefined) {
    record["curated"] = fields["curated"] === false ? false
      : check.fail("curated is false or absent: a record is the platform's own unless marked");
  }
  if (cli) {
    record["cli"] = cliSpec(check, fields["cli"]);
    return Object.freeze(record) as unknown as SourceRecord;
  }
  const sealed = Object.fromEntries(GATEWAY_KEYS.filter((key) => fields[key] !== undefined)
    .map((key) => [key, fields[key]]));
  const { endpoints, ...upstream } = sealed;
  let checked: Source;
  try {
    // The gateway's own check of what it will seal: one rule for the supported APIs,
    // the URL grammar, the endpoints and a custom endpoint's models.
    checked = gatewaySource({ upstream, endpoints }, false);
  } catch {
    return check.fail("its gateway values are refused by the gateway's own check " +
                      "(an unsupported API, a non-canonical URL, a base URL or setup check " +
                      "outside its endpoints, or a malformed endpoint or model)");
  }
  record["upstream"] = checked.upstream;
  record["endpoints"] = checked.endpoints;
  const engines = check.strings(fields["engines"], "engines", SOURCE_ID, 16);
  if (new Set(engines).size !== engines.length) {
    check.fail("engines names each CLI record once");
  }
  record["engines"] = Object.freeze(engines);
  if (fields["not_interactive"] !== undefined) {
    // A model pattern is a model id's characters (`*` among them), the gateway's rule.
    const patterns = check.strings(fields["not_interactive"], "not_interactive", MODEL_PATTERN, 16);
    if (patterns.length === 0 || new Set(patterns).size !== patterns.length) {
      check.fail("not_interactive lists model patterns, at least one, each once");
    }
    record["not_interactive"] = Object.freeze(patterns);
  }
  if (fields["credit"] !== undefined) {
    record["credit"] = creditSpec(check, fields["credit"], checked);
  }
  if (connect.includes("oauth")) {
    const oauth = oauthSpec(check, fields["oauth"]);
    record["oauth"] = oauth;
    try {
      sealedSource(record as unknown as SourceRecord, "oauth");
    } catch {
      check.fail("its OAuth client is refused by the gateway's own check");
    }
  }
  return Object.freeze(record) as unknown as SourceRecord;
}

/**
 * What the binder hands the gateway for a connect of `record` by `method`: the values it
 * seals with the secret, checked by the gateway's own rule.
 */
export function sealedSource(record: SourceRecord, method: "api_key" | "oauth"): Source {
  const { upstream, endpoints, oauth } = record;
  if (upstream === undefined || endpoints === undefined || !record.connect.includes(method)) {
    throw new SourceRecordError(`source record ${JSON.stringify(record.id)} does not connect ` +
                                `by ${method}`);
  }
  if (method === "api_key") {
    return gatewaySource({ upstream, endpoints }, false);
  }
  if (oauth?.protocol === "pkce_user_key_v1") {
    return keyExchangeSource({ upstream, endpoints,
                               key_exchange: { endpoint: oauth.exchange_endpoint } });
  }
  if (oauth?.protocol !== "rfc6749") {
    throw new SourceRecordError(`source record ${JSON.stringify(record.id)} has no OAuth flow`);
  }
  return gatewaySource({
    upstream, endpoints,
    oauth: {
      client_id: oauth.client_id, client_auth: { method: "none" },
      token_endpoint: oauth.token_endpoint,
      ...(oauth.revocation_endpoint === undefined ? {}
        : { revocation_endpoint: oauth.revocation_endpoint }),
      ...(oauth.resource === undefined ? {} : { resource: oauth.resource }),
    },
  }, true);
}

/**
 * The rules a catalog's records hold together, read from the files as they are, so they
 * hold over every record, whole or not (each is checked whole when it is read):
 * - each id is one record's: which one is served would be an accident (Fact 8);
 * - no record the platform does not curate speaks OAuth beside a key exchange (doors spec,
 *   "No uncurated source shares the flow family with a key exchange"): the key exchange
 *   sends no `state` and no `iss`, so its callback could not tell the two servers apart
 *   (the residual mix-up). A record marked `curated` at all counts as uncurated here.
 */
export function checkedTogether(records: readonly unknown[]): void {
  const ids = records.map((record) => object(record) ? record["id"] : undefined);
  const repeated = ids.find((id, index) => ids.indexOf(id) !== index);
  if (ids.length !== new Set(ids).size) {
    throw new SourceRecordError(`source record ${JSON.stringify(String(repeated))}: ` +
                                "its id is another record's");
  }
  const objects = records.filter(object);
  const keyExchange = objects.some((record) => object(record["oauth"])
    && record["oauth"]["protocol"] === "pkce_user_key_v1");
  const mixed = objects.find((record) => record["curated"] !== undefined
    && record["oauth"] !== undefined);
  if (keyExchange && mixed !== undefined) {
    throw new SourceRecordError(`source record ${JSON.stringify(String(mixed["id"]))}: an ` +
      "authorization server the platform does not curate shares no connect flow with a key " +
      "exchange (the mix-up)");
  }
}
