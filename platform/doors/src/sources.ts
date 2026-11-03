/**
 * Model source records (gateway research §2.4, §2.6, §7.2; tasks 4 G3): what an owner can
 * connect, as data. Each is an editable JSON file in `platform/sources/` (Direction 3),
 * ported from the Python's `tinyassets/providers/source_records/`, and checked here
 * whole, so a record that does not fit fails loudly where it is used and in the tests
 * (`guards/sources.test.ts`), never half-read.
 *
 * A record names how it connects (`connect`):
 * - `api_key`: a pasted key, sealed by the gateway (`connect.ts`).
 * - `oauth`: an authorization-code flow with PKCE, its code exchanged by the gateway:
 *   `rfc6749` (RFC 6749 and 7636, tokens) or `pkce_user_key_v1` (the Python's protocol,
 *   OpenRouter's: the code buys an API key). A record marked `unwired` names why it is
 *   not served yet, and every connect of it is refused with that reason.
 * - `cli`: an engine (ADR-033 decision 2, ADR-034 decisions 1 and 5): a provider's own
 *   unmodified command-line agent, run whole in the owner's sandbox, never a backend of
 *   ours, so it has no single-inference `invoke`. It never enters the gateway, so it names
 *   no upstream. Its record is everything the sandbox runner needs, as data (`CliSpec`):
 *   install, sign-in, the new-run and resume invocations and their event matchers, the
 *   instruction and skills paths, the MCP template, tool filters and unattended flags, its
 *   home, the credentials it runs on, whether each may run unattended (with the vendor
 *   page that says so), its `session_paths`, which sync to the owner's files, and its
 *   `secret_paths`, which never leave the sandbox (ADR-031 decision 2). Every path is
 *   under `{home}`, the runner's engine home, which is sandbox-only and outside
 *   `/workspace`, so secret and session paths compare in one namespace. A prompt goes on
 *   stdin or joined to its option (`--prompt={prompt}`), never bare; no value configures
 *   the engine to nothing (an empty argument, list or map); a run on the vendor's own
 *   sign-in carries none of the variables its `forbid_env` lists, its API-key variables
 *   among them; and a sign-in with regions takes the owner's choice as `{region}`. The
 *   checker holds shape only: no vendor is named here.
 *
 * A gateway source (`api_key`, `oauth`) names the pi-ai provider (`custom` for a declared
 * endpoint), the API its models speak, the base URL, how the secret is sent (`auth`), the
 * setup check (`verify_url`), the only endpoints the secret may reach, and its `engines`:
 * the CLI records that can run on it through the egress door. pi-ai's catalog
 * supplies a known provider's base URL and models, and a record of one restates them
 * (the sources guard holds the two equal); only a `custom` record declares its own models.
 * Those values are what the gateway seals with the secret. They are checked by the
 * gateway's own `source` (so its `SUPPORTED_APIS`, its endpoint and URL grammar are the
 * one rule), here and again in the gateway when it seals.
 */
import {
  type Endpoint, keyExchangeSource, type Source, source as gatewaySource, type Upstream,
} from "../../gateway/src/credential";
import { SIGNIN_CODE_MAX, SIGNIN_URL_MAX } from "../../owner/src/run-rows";
import { forbids, SOURCE_ID } from "../../owner/src/text";

export { forbids };

import anthropic from "../../sources/anthropic.json";
import cerebras from "../../sources/cerebras.json";
import chatgpt from "../../sources/chatgpt.json";
import claudeCode from "../../sources/claude-code.json";
import codex from "../../sources/codex.json";
import gemini from "../../sources/gemini.json";
import googleAiStudio from "../../sources/google_ai_studio.json";
import groq from "../../sources/groq.json";
import kimi from "../../sources/kimi.json";
import mistral from "../../sources/mistral.json";
import openai from "../../sources/openai.json";
import openrouter from "../../sources/openrouter_user_models_v1.json";
import pi from "../../sources/pi.json";
import xai from "../../sources/xai.json";

/** The records the doors serve, as the files hold them. Never written: each read is a copy. */
const BUNDLED: readonly unknown[] = Object.freeze([
  anthropic, cerebras, chatgpt, claudeCode, codex, gemini, googleAiStudio, groq, kimi, mistral,
  openai, openrouter, pi, xai,
]);

export type ConnectMethod = "api_key" | "oauth" | "cli";

/** An RFC 6749 authorization-code source (`protocol: "rfc6749"`). */
export interface Rfc6749 {
  readonly protocol: "rfc6749";
  readonly authorization_endpoint: string;
  readonly authorize_params: Readonly<Record<string, string>>;
  readonly scope?: string;
  readonly client_id: string;
  readonly token_endpoint: string;
  readonly revocation_endpoint?: string;
  readonly resource?: string;
  readonly refresh_with: "provider" | "rfc6749";
  /** The authorization server's issuer (RFC 8414), which an `iss` response must name. */
  readonly issuer?: string;
  /** Its metadata's RFC 9207 flag: every authorization response then carries `iss`. */
  readonly authorization_response_iss_parameter_supported?: true;
}

/** The Python's `pkce_user_key_v1` (OpenRouter): the code buys an API key. */
export interface UserKey {
  readonly protocol: "pkce_user_key_v1";
  readonly authorization_endpoint: string;
  readonly authorize_params: Readonly<Record<string, string>>;
  readonly exchange_endpoint: string;
}

export type OAuthSpec = Rfc6749 | UserKey;

/** What an engine runs on: the vendor's own sign-in in the sandbox, or an egress-keyed key. */
export type CredentialKind = "vendor_login_in_sandbox" | "api_key";
export const CREDENTIAL_KINDS: readonly CredentialKind[] = Object.freeze([
  "vendor_login_in_sandbox", "api_key"]);

/**
 * How the engine binary is installed into the sandbox template, pinned: an npm package by
 * version, integrity and the digest of its committed lock (`platform/sources/locks/<id>/`),
 * a pip package by version and the digest of its committed hashed requirements, or a
 * binary by its https URL and SHA-256.
 */
export type Install =
  | { readonly manager: "npm"; readonly package: string; readonly version: string;
      readonly integrity: string; readonly lock_sha256: string; readonly bin: string;
      readonly verify_argv: readonly string[]; readonly lifecycle_scripts?: true }
  | { readonly manager: "pip"; readonly package: string; readonly version: string;
      readonly lock_sha256: string; readonly bin: string; readonly verify_argv: readonly string[] }
  | { readonly manager: "binary"; readonly url: string; readonly sha256: string;
      readonly version: string; readonly bin: string; readonly verify_argv: readonly string[] };

/** One event matcher: an event whose fields at `match` equal those values, read at `path`. */
export interface EventMatcher {
  readonly match: Readonly<Record<string, string | number | boolean>>;
  /** A dotted path into the event; empty for the event itself. */
  readonly path: string;
  /** Every match's value is joined in order (a streamed text), not only the last. */
  readonly accumulate?: true;
}

/** How the runner reads the engine's stream (ADR-034 decision 10). */
export interface EngineEvents {
  readonly format: "jsonl" | "json";
  readonly session_id: readonly EventMatcher[];
  readonly final_text: readonly EventMatcher[];
  readonly error: readonly EventMatcher[];
  readonly usage: readonly EventMatcher[];
  readonly tool_call: readonly EventMatcher[];
}

/**
 * One run of the engine (ADR-034 decision 4.3): a new conversation's argv, and the argv
 * that resumes one, by its session id (`{sid}`) or by continuing in the conversation's own
 * working directory when the stream names no id.
 */
export interface Engine {
  readonly argv: readonly string[];
  readonly resume_argv: readonly string[];
  readonly resume: "session_id" | "continue_in_cwd";
  readonly stdin?: "{prompt}";
  readonly model_argv?: readonly string[];
  readonly events: EngineEvents;
}

/** Where the agent's canonical `AGENTS.md` is read, and what would shadow it. */
export interface Instructions {
  readonly file: string;
  /** Files that, when present, the engine reads instead: the runner never projects them. */
  readonly shadowed_by: readonly string[];
  readonly max_bytes?: number;
  /** The record's file whose template makes the engine read `file`. */
  readonly setting?: string;
}

/** How TinyAssets' run-scoped MCP door reaches the engine, and how its trust is handled. */
export type Mcp =
  | { readonly argv: readonly string[]; readonly file?: string; readonly template?: string;
      readonly trust: { readonly argv: readonly string[]; readonly note: string } }
  | { readonly unsupported: string };

/** A tool filter: joined into `{tools}`, one argv per tool (`{tool}`), or a file's key. */
export type ToolFilter =
  | { readonly argv: readonly string[]; readonly join: string }
  | { readonly argv: readonly string[]; readonly each: true }
  | { readonly file: string; readonly key: string };

/** Whether unattended hosted runs on one credential are allowed, with the vendor page. */
export type Unattended =
  | { readonly allowed: true; readonly cite: string; readonly read: string;
      readonly read_note?: string }
  | { readonly allowed: false; readonly reason: string; readonly cite?: string;
      readonly read?: string; readonly read_note?: string };

/** The variables a CLI reads its egress base URL and placeholder key from, for a provider. */
export interface ApiKeyEnv {
  /**
   * Null when a file template or an API-key argv (`cli.per_credential.api_key.argv`) carries
   * `{base_url}`: the CLI takes its base URL from there, not from a variable.
   */
  readonly base_url: string | null;
  readonly api_key: string;
  /** The door, the connection and the sealed base path, or the door and connection alone. */
  readonly base: "sealed" | "connection";
}

/** What differs by the credential a run is on. */
export interface PerCredential {
  readonly argv?: readonly string[];
  readonly env?: Readonly<Record<string, string>>;
  readonly files?: Readonly<Record<string, string>>;
  /** Arguments no argv of the record may hold on this credential. */
  readonly forbid_argv?: readonly string[];
  /**
   * Variables a run on this credential never carries (the runner unsets them): on the
   * vendor's own sign-in, every variable that would move the run onto a key or another
   * endpoint, its `api_key_env` names among them.
   */
  readonly forbid_env?: readonly string[];
}

/** A region the owner chooses for the vendor's own sign-in: its hosts, `{region}` its id. */
export interface Region {
  readonly display_name: string;
  readonly hosts: Readonly<Record<"login" | "provider", readonly string[]>>;
}

/** An engine (`connect: ["cli"]`): a provider's own CLI, run whole in the sandbox. */
export interface CliSpec {
  readonly install: Install;
  readonly credential: readonly CredentialKind[];
  readonly login?: Readonly<Record<string, unknown>>;
  /** The sign-in's regions, by id: the owner's choice fills `{region}` and adds its hosts. */
  readonly regions?: Readonly<Record<string, Region>>;
  readonly api_key_env?: Readonly<Record<string, ApiKeyEnv>>;
  readonly unattended: Readonly<Partial<Record<CredentialKind, Unattended>>>;
  readonly per_credential?: Readonly<Partial<Record<CredentialKind, PerCredential>>>;
  readonly engine: Engine;
  readonly instructions: Instructions;
  readonly skills_paths: readonly string[];
  readonly mcp: Mcp;
  /**
   * Where the CLI's model calls carry the run's own secret as a header (`x-ta-run`) rather
   * than in their base URL's path: the variable it reads extra headers from, and the
   * value, `{run_secret}` in it. Absent, the base URL's path carries it (the fallback).
   */
  readonly run_secret_env?: { readonly env: string; readonly value: string };
  readonly tools: { readonly allow?: ToolFilter; readonly deny?: ToolFilter };
  readonly unattended_argv: readonly string[];
  readonly home_env: Readonly<Record<string, string>>;
  readonly env?: Readonly<Record<string, string>>;
  readonly files?: Readonly<Record<string, string>>;
  readonly no_update: { readonly env?: Readonly<Record<string, string>>;
                        readonly argv?: readonly string[]; readonly file?: string }
    | { readonly reason: string };
  readonly secret_paths: readonly string[];
  readonly session_paths: readonly string[];
  readonly hosts: Readonly<Record<"registry" | "login" | "provider", readonly string[]>>;
}

export interface SourceRecord {
  readonly id: string;
  readonly display_name: string;
  readonly help_url?: string;
  readonly suggested: boolean;
  readonly connect: readonly ConnectMethod[];
  /** Why every connect of this source is refused for now; absent when it is served. */
  readonly unwired?: string;
  /** What the gateway seals with the secret: every source but a CLI's. */
  readonly upstream?: Upstream;
  readonly endpoints?: readonly Endpoint[];
  readonly oauth?: OAuthSpec;
  /** A gateway source's: the CLI records that can run on its key, through the egress door. */
  readonly engines?: readonly string[];
  readonly cli?: CliSpec;
}

/** A record that does not fit: a bug in a data file, never a request to act on. */
export class SourceRecordError extends Error {
  override readonly name = "SourceRecordError";
}

/** The records a doors Worker serves, by id. */
export interface Sources {
  /** The record, checked whole; undefined for an id no record has. */
  get(id: string): SourceRecord | undefined;
  /** Every record's id. */
  ids(): readonly string[];
}

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

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

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
    "protocol", "authorization_endpoint", "token_endpoint", "client_id", "client_auth",
    "refresh_with"], ["authorize_params", "scope", "revocation_endpoint", "resource",
                      "issuer", "authorization_response_iss_parameter_supported"]);
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
  // `refresh_with`, like the token endpoint it implies, is the gateway's own check
  // (`sealedSource` below runs it).
  const refreshWith = fields["refresh_with"];
  const optional = (key: string, read: (item: unknown) => string) =>
    fields[key] === undefined ? {} : Object.fromEntries([[key, read(fields[key])]]);
  return Object.freeze({
    protocol,
    authorization_endpoint: check.https(fields["authorization_endpoint"],
                                        "oauth.authorization_endpoint"),
    authorize_params: authorizeParams(check, fields["authorize_params"]),
    token_endpoint: check.https(fields["token_endpoint"], "oauth.token_endpoint"),
    client_id: check.text(fields["client_id"], CLIENT_ID, "oauth.client_id"),
    refresh_with: refreshWith as Rfc6749["refresh_with"],
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
/** A pi-ai provider id, as the gateway's `ID`. */
const PROVIDER = /^[a-z0-9][a-z0-9-]{0,63}$/;
/** One argument: any text, a placeholder being `{name}` among `PLACEHOLDERS`. */
const ARG = /^.{0,4096}$/su;
/** The placeholders the runner fills, in an argv, an env value or a file template. */
const PLACEHOLDERS: readonly string[] = Object.freeze([
  "{bin}", "{prompt}", "{sid}", "{model}", "{provider}", "{home}", "{agent}", "{mcp_url}",
  "{mcp_config}", "{base_url}", "{tools}", "{tool}", "{region}", "{run_secret}", "{system}",
]);
/**
 * The one shape `{prompt}` may take in an argv: joined to its option (`--prompt={prompt}`),
 * so no prompt is ever read as an option or a file include (`-x`, `@file`). Where the CLI
 * reads stdin, the prompt goes there instead (`engine.stdin`).
 */
const PROMPT_ARG = /^--[A-Za-z0-9][A-Za-z0-9-]*=\{prompt\}$/;
/** A value that turns a setting off: nothing, bare quotes, or an empty list or map. */
const EMPTY_VALUE = /^\s*(?:""|''|\[\s*\]|\{\s*\})?\s*$/;
/** An empty list or map anywhere in a text (`tools.core: []`, `mcp_servers={}`). */
const EMPTY_COLLECTION = /\[\s*\]|\{\s*\}/;
/** A variable `forbid_env` names: a name, or every name with a prefix (`OPENAI_*`). */
const FORBID = /^[A-Z][A-Z0-9_]{0,63}\*?$/;
/**
 * A variable name shaped like a credential or an endpoint: a record's own variables, which
 * every run carries, are never one, so nothing credential-shaped is set but a key run's
 * own placeholder (`sandboxEnv`).
 */
const CREDENTIAL_NAME =
  /(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH|BASE_URL|ENDPOINT)(?:_|$)/;
/** A settings key shaped like a credential, a credential helper or an environment block. */
const CREDENTIAL_KEY = /^(?:env|environment)$|api_?key|token|secret|password|credential|helper/i;
/** A settings key that moves where a run's calls go: never in a signed-in run's files. */
const ENDPOINT_KEY = /base_?url|endpoint/i;
/**
 * An argument that moves where a run's calls go, or onto another model provider: never in an
 * argv a signed-in run gets (the argv twin of `ENDPOINT_KEY`), so no `-c openai_base_url=...`
 * sends a vendor sign-in's tokens elsewhere.
 */
const ENDPOINT_ARG = /base_?url|endpoint|provider/i;
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

/**
 * The process basics a run keeps from the sandbox template's own environment, beside
 * `HOME`, which the runner sets to the run's engine home (`{home}`): nothing else of the
 * sandbox's environment reaches a run. `NODE_EXTRA_CA_CERTS` is E2B's egress CA (slice S's
 * probe: E2B's proxy re-signs the HTTPS it sets the egress token on, and Node trusts only
 * its own bundle), so a Node engine's calls to the egress door verify.
 */
export const RUN_BASICS: readonly string[] = Object.freeze([
  "PATH", "LANG", "LC_ALL", "TERM", "TZ", "TMPDIR", "USER", "SHELL", "NODE_EXTRA_CA_CERTS",
]);

/** A region's id, as the runner passes it in `{region}`. */
const REGION = /^[a-z0-9][a-z0-9-]{0,31}$/;
/** A dotted path into a JSON value (an event's field, a settings file's key). */
const DOTTED = /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,7}$/;
/** A path segment: no `.` or `..`, a `*` only whole (any one segment). */
const SEGMENT = /^(?:\*|(?!\.{1,2}$)[A-Za-z0-9._-]{1,128})$/;
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
 * CLI's system settings: it skips one any of whose ancestors another user owns, slice S's
 * probe). Never a secret or session path, which live in the home.
 */
function runFilePath(value: unknown): string[] | null {
  if (typeof value === "string" && value.startsWith("{system}/")) {
    return homePath(`{home}/${value.slice("{system}/".length)}`, false);
  }
  return homePath(value, false);
}

/** Whether one home path is the other or under it, a `*` segment matching any one. */
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
  // `invoke`, a single inference, is not an engine: a CLI is never a backend (ADR-033 D2).
  if (object(value) && "invoke" in value) {
    check.fail("cli has no invoke: a CLI is an engine, never a backend (ADR-033 decision 2)");
  }
  const fields = check.keys(value, "cli", [
    "install", "credential", "unattended", "engine", "instructions", "skills_paths", "mcp",
    "tools", "unattended_argv", "home_env", "no_update", "secret_paths", "session_paths",
    "hosts"], ["login", "regions", "api_key_env", "per_credential", "env", "files",
               "run_secret_env"]);
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

  // The install, pinned.
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
  // The run secret as a header on the model calls, where the CLI takes extra headers.
  let runSecretEnv: { env: string; value: string } | undefined;
  if (fields["run_secret_env"] !== undefined) {
    const header = check.keys(fields["run_secret_env"], "cli.run_secret_env", ["env", "value"]);
    if (!keyed || !ENV_NAME.test(String(header["env"])) || typeof header["value"] !== "string"
        || !/^x-ta-run: \{run_secret\}$/.test(header["value"])) {
      check.fail("cli.run_secret_env names the variable an API-key run reads extra headers " +
                 "from, and the value x-ta-run: {run_secret}");
    }
    runSecretEnv = { env: header["env"] as string, value: header["value"] as string };
  }

  // Its home, environment and files.
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

  // The MCP door's way in.
  const mcp = mcpSpec(check, fields["mcp"], argv, template, sandboxPath, declared);

  // The engine itself.
  const engine = engineSpec(check, fields["engine"], argv);
  const instructions = instructionsSpec(check, fields["instructions"]);
  const skills = check.strings(fields["skills_paths"], "cli.skills_paths", /^.{1,256}$/su, 16);
  if (skills.length === 0 || !skills.every(relative)) {
    check.fail("cli.skills_paths lists at least one path relative to the agent's directory");
  }
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
    ["cli.instructions.setting", instructions.setting],
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
    // A declared channel (rule 1): no base-URL variable, so a file template or the API-key
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
    const path = what === "cli.mcp.template" ? ("file" in mcp ? mcp.file! : "")
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
                 "secret never syncs (ADR-031 decision 2)");
    }
  }
  if (signsIn) {
    const login = loginSpec(check, fields["login"], argv);
    for (const state of (login["state_checks"] ?? []) as { path: string }[]) {
      const segments = homePath(state.path, false);
      if (segments === null || !secretSegments.some((secret) =>
        secret.length <= segments.length && overlaps(secret, segments))) {
        check.fail(`cli.login.state_checks ${state.path} is a credential file among the ` +
                   "secret paths");
      }
    }
    fields["login"] = login;
  }

  // The sign-in's regions: the owner's choice fills `{region}` and adds its hosts.
  let regions: Record<string, Region> | undefined;
  if (fields["regions"] !== undefined) {
    const map = fields["regions"];
    if (!signsIn || !object(map) || Object.keys(map).length === 0
        || Object.keys(map).length > 8 || !Object.keys(map).every((id) => REGION.test(id))) {
      check.fail("cli.regions maps 1 to 8 region ids to their hosts, for a record that signs " +
                 "in with the vendor's own flow");
    }
    regions = {};
    for (const [id, entry] of Object.entries(map as Record<string, unknown>)) {
      const what = `cli.regions.${id}`;
      const region = check.keys(entry, what, ["display_name", "hosts"]);
      const hosts = check.keys(region["hosts"], `${what}.hosts`, ["login", "provider"]);
      for (const kind of ["login", "provider"] as const) {
        hosts[kind] = Object.freeze(check.strings(hosts[kind], `${what}.hosts.${kind}`, HOST, 16));
      }
      regions[id] = Object.freeze({
        display_name: check.text(region["display_name"], TEXT, `${what}.display_name`),
        hosts: Object.freeze(hosts) as unknown as Region["hosts"] });
    }
    const loginArgv = argvs.find(([what]) => what === "cli.login.argv")![1];
    if (!loginArgv.some((item) => item.includes("{region}"))) {
      check.fail("cli.login.argv carries {region}, which the owner's choice of region fills");
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
      if (found === "{region}" && (regions === undefined || !what.startsWith("cli.login."))) {
        check.fail(`${what} uses {region}, which only the sign-in carries, under cli.regions`);
      }
      // The run's own secret only in its MCP file's header, never an argument.
      if (found === "{run_secret}" && what !== "cli.mcp.template") {
        check.fail(`${what} uses {run_secret}, which only cli.mcp.template's header carries`);
      }
      if (found === "{mcp_config}" && !("file" in mcp)) {
        check.fail(`${what} uses {mcp_config}, which names cli.mcp.file, and there is none`);
      }
    }
  }
  // `{base_url}` only where only an API-key run reads it (rule 2): the API-key run's own
  // argv, env or files, or anywhere in a record that runs on nothing but a key; and in an
  // argv joined to its key, never bare (rule 3).
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
  // No argument a signed-in run gets moves where its calls go (rule 4): every argv of a
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
    ...(signsIn ? { login: Object.freeze(fields["login"] as object) } : {}),
    ...(regions === undefined ? {} : { regions: Object.freeze(regions) }),
    ...(apiKeyEnv === undefined ? {} : { api_key_env: Object.freeze(apiKeyEnv) }),
    unattended: Object.freeze(unattended),
    ...(perCredential === undefined ? {} : { per_credential: Object.freeze(perCredential) }),
    engine,
    instructions,
    skills_paths: Object.freeze(skills),
    mcp,
    ...(runSecretEnv === undefined ? {} : { run_secret_env: Object.freeze(runSecretEnv) }),
    tools: Object.freeze(tools),
    unattended_argv: Object.freeze(unattendedArgv),
    home_env: Object.freeze(homeEnv),
    ...(env === undefined ? {} : { env: Object.freeze(env) }),
    ...(files === undefined ? {} : { files: Object.freeze(files) }),
    no_update: noUpdate,
    secret_paths: Object.freeze(secrets),
    session_paths: Object.freeze(sessions),
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
        || !/^[0-9a-f]{64}$/.test(String(fields["lock_sha256"]))
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
        || !/^[0-9a-f]{64}$/.test(String(fields["lock_sha256"]))) {
      check.fail("cli.install pins a pip package by exact version and hashed-lock digest");
    }
    pinned(fields, "pins a pip package");
    return Object.freeze(fields) as unknown as Install;
  }
  if (manager === "binary") {
    const fields = check.keys(value, "cli.install", ["manager", "url", "sha256", "version",
                                                     "bin", "verify_argv"]);
    check.https(fields["url"], "cli.install.url");
    if (!/^[0-9a-f]{64}$/.test(String(fields["sha256"]))) {
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

function matchers(check: ReturnType<typeof checker>, value: unknown, what: string):
    readonly EventMatcher[] {
  if (!Array.isArray(value) || value.length > 8) {
    return check.fail(`${what} is a list of at most 8 matchers`);
  }
  return Object.freeze(value.map((entry, index) => {
    const fields = check.keys(entry, `${what}[${index}]`, ["match", "path"], ["accumulate"]);
    const match = fields["match"];
    if (!object(match) || Object.keys(match).length === 0 || Object.keys(match).length > 8
        || !Object.entries(match).every(([key, item]) => DOTTED.test(key)
          && ["string", "number", "boolean"].includes(typeof item))) {
      check.fail(`${what}[${index}].match maps 1 to 8 dotted paths to scalars`);
    }
    if (fields["path"] !== "" && !DOTTED.test(String(fields["path"]))) {
      check.fail(`${what}[${index}].path is a dotted path, or empty for the event itself`);
    }
    if (fields["accumulate"] !== undefined && fields["accumulate"] !== true) {
      check.fail(`${what}[${index}].accumulate is true or absent`);
    }
    return Object.freeze({ ...fields, match: Object.freeze({ ...match as object }) }) as
      unknown as EventMatcher;
  }));
}

function engineSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv): Engine {
  // `invoke`, a single inference, is not an engine: a CLI is never a backend (ADR-033 D2).
  if (object(value) && "invoke" in value) {
    check.fail("cli.engine has no invoke: a CLI is an engine, never a backend (ADR-033 " +
               "decision 2)");
  }
  const fields = check.keys(value, "cli.engine", ["argv", "resume_argv", "resume", "events"],
                            ["stdin", "model_argv"]);
  const run = argv(fields["argv"], "cli.engine.argv");
  const resume = argv(fields["resume_argv"], "cli.engine.resume_argv");
  if (run[0] !== "{bin}" || resume[0] !== "{bin}") {
    check.fail("cli.engine's argv and resume_argv each run {bin}");
  }
  const mode = fields["resume"];
  const resumesById = resume.some((item) => item.includes("{sid}"));
  if (mode !== "session_id" && mode !== "continue_in_cwd") {
    check.fail("cli.engine.resume is session_id or continue_in_cwd");
  }
  if (resumesById !== (mode === "session_id")) {
    check.fail("cli.engine.resume_argv carries {sid} exactly when a run resumes by session id");
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
  const events = check.keys(fields["events"], "cli.engine.events", [
    "format", "session_id", "final_text", "error", "usage", "tool_call"]);
  if (events["format"] !== "jsonl" && events["format"] !== "json") {
    check.fail("cli.engine.events.format is jsonl or json");
  }
  for (const kind of ["session_id", "final_text", "error", "usage", "tool_call"] as const) {
    events[kind] = matchers(check, events[kind], `cli.engine.events.${kind}`);
  }
  if ((events["final_text"] as unknown[]).length === 0) {
    check.fail("cli.engine.events.final_text has at least one matcher");
  }
  if (((events["session_id"] as unknown[]).length > 0) !== (mode === "session_id")) {
    check.fail("cli.engine.events.session_id has matchers exactly when a run resumes by " +
               "session id");
  }
  return Object.freeze({
    argv: Object.freeze(run), resume_argv: Object.freeze(resume),
    resume: mode as Engine["resume"],
    ...(prompted ? { stdin: "{prompt}" as const } : {}),
    ...(model === undefined ? {} : { model_argv: Object.freeze(model) }),
    events: Object.freeze(events) as unknown as EngineEvents,
  });
}

function instructionsSpec(check: ReturnType<typeof checker>, value: unknown): Instructions {
  const fields = check.keys(value, "cli.instructions", ["file", "shadowed_by"],
                            ["max_bytes", "setting"]);
  const names = check.strings(fields["shadowed_by"], "cli.instructions.shadowed_by",
                              /^.{1,256}$/su, 8);
  if (typeof fields["file"] !== "string" || !relative(fields["file"])
      || !names.every(relative) || names.includes(fields["file"])) {
    check.fail("cli.instructions names its file, and what shadows it, relative to the " +
               "agent's directory");
  }
  const max = fields["max_bytes"];
  if (max !== undefined && (!Number.isSafeInteger(max) || (max as number) < 1)) {
    check.fail("cli.instructions.max_bytes is a whole number from 1");
  }
  if (fields["setting"] !== undefined && runFilePath(fields["setting"]) === null) {
    check.fail("cli.instructions.setting is a path under {home} or {system}");
  }
  return Object.freeze({ ...fields, shadowed_by: Object.freeze(names) }) as unknown as
    Instructions;
}

function mcpSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv,
                 template: (item: unknown, what: string) => string,
                 sandboxPath: (item: unknown, what: string) => string[],
                 declared: string[]): Mcp {
  if (object(value) && "unsupported" in value) {
    const fields = check.keys(value, "cli.mcp", ["unsupported"]);
    return Object.freeze({ unsupported: check.text(fields["unsupported"], REASON,
                                                   "cli.mcp.unsupported") });
  }
  const fields = check.keys(value, "cli.mcp", ["argv", "trust"], ["file", "template"]);
  const flags = argv(fields["argv"], "cli.mcp.argv", 16);
  if ((fields["file"] === undefined) !== (fields["template"] === undefined)) {
    check.fail("cli.mcp has a file exactly when it has the file's template");
  }
  let text: string | undefined;
  if (fields["file"] !== undefined) {
    sandboxPath(fields["file"], "cli.mcp.file");
    text = template(fields["template"], "cli.mcp.template");
    declared.push(fields["file"] as string);
  }
  if (![...flags, text ?? ""].some((item) => item.includes("{mcp_url}"))) {
    check.fail("cli.mcp carries {mcp_url}, in its file's template or its argv");
  }
  const trust = check.keys(fields["trust"], "cli.mcp.trust", ["argv", "note"]);
  return Object.freeze({
    argv: Object.freeze(flags),
    ...(text === undefined ? {} : { file: fields["file"] as string, template: text }),
    trust: Object.freeze({ argv: Object.freeze(argv(trust["argv"], "cli.mcp.trust.argv", 16)),
                           note: check.text(trust["note"], REASON, "cli.mcp.trust.note") }),
  });
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

/** The hosts a record's sign-in may send the owner to: its login hosts, and its region's. */
export function signinHosts(rec: SourceRecord, region: string | null): string[] {
  const cli = rec.cli;
  if (cli === undefined) {
    return [];
  }
  return [...cli.hosts.login, ...(region === null ? [] : cli.regions?.[region]?.hosts.login ?? [])];
}

/**
 * A sign-in's states, the only ones a frame from the VM may carry (review M1): no text the
 * VM chose leaves it but the record's own URL and code.
 */
export const SIGNIN_STATES = Object.freeze(["starting", "open", "code_sent", "signed_in",
                                            "signed_out", "failed", "cancelled", "refused",
                                            "expired"] as const);
export type SigninState = (typeof SIGNIN_STATES)[number];

/**
 * Why a sign-in was refused, each in plain words (review L6): the reason a refusal carries,
 * one of these keys, never a text the VM chose.
 */
export const SIGNIN_REFUSALS = Object.freeze({
  url: "it showed a link that is not the vendor's own sign-in page",
  code: "it showed a code that is not the vendor's kind of code",
  credential: "it showed something that looked like a credential",
  state: "it reported something a sign-in never reports",
} as const);
export type SigninRefusal = keyof typeof SIGNIN_REFUSALS;

/**
 * A URL parameter or fragment key named like a credential, anywhere in its name (so a
 * suffixed or prefixed one too: `refresh_token_x`, `x_api_key`).
 */
const CREDENTIAL_PARAM = /token|secret|passw|api_?key|credential|session/i;
/** A JSON Web Token: three base64url segments, the first a JSON object's (`eyJ`). */
const JWT = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/;

/**
 * Whether `value` looks like a credential: a JSON Web Token, or, as a URL, a parameter or
 * fragment key named like one. Checked in the VM (beside the engines' own secrets' atoms)
 * and again on the trusted side.
 */
export function credentialLike(value: string): boolean {
  if (JWT.test(value)) {
    return true;
  }
  let url: URL | null = null;
  try {
    url = /^https?:\/\//.test(value) ? new URL(value) : null;
  } catch {
    url = null;
  }
  return url !== null && [...url.searchParams.keys(),
                          ...new URLSearchParams(url.hash.replace(/^#/, "")).keys()]
    .some((key) => CREDENTIAL_PARAM.test(key));
}

/** A free parameter's value: RFC 3986 section 2.3's unreserved characters, so never a URL. */
const UNRESERVED = /^[A-Za-z0-9._~-]{1,128}$/;

/**
 * Why a sign-in's prompt is not the record's own shape (a `SigninRefusal`), or null. The URL:
 * visible ASCII at most `SIGNIN_URL_MAX`, matching the record's `url_pattern` whole,
 * `https` on one of the record's `url.hosts` that is among its login hosts (and the chosen
 * region's), no user, password, port or fragment, its path the record's `url.path` exactly, each of the record's `url.params`
 * present with exactly its value, the record's `url.free` parameters each at most once with
 * an unreserved value (a state, a PKCE challenge, a user code: never a URL, so no redirect
 * chain), and no other parameter (so no other OAuth client's page). The code: a device
 * code's characters, at most `SIGNIN_CODE_MAX`. Neither credential-shaped
 * (`credentialLike`). Checked in the VM before a prompt leaves it, and again by the owner's
 * object before the owner sees it.
 */
export function signinShape(rec: SourceRecord, region: string | null, url: string | null,
                            code: string | null): SigninRefusal | null {
  const login = rec.cli?.login as LoginSpec | undefined;
  if (login === undefined) {
    return "state";
  }
  if (url !== null) {
    if (url.length > SIGNIN_URL_MAX || !/^[\x21-\x7e]+$/.test(url)) {
      return "url";
    }
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return "url";
    }
    const whole = new RegExp(login.url_pattern).exec(url);
    if (whole === null || whole[1] !== url || parsed.protocol !== "https:"
        || parsed.username !== "" || parsed.password !== "" || parsed.port !== ""
        || parsed.hash !== "" || url.includes("#")
        || !signinHosts(rec, region).includes(parsed.hostname)
        || !login.url.hosts.includes(parsed.hostname) || parsed.pathname !== login.url.path) {
      return "url";
    }
    const seen = new Set<string>();
    for (const [key, value] of parsed.searchParams) {
      if (seen.has(key)) {
        return "url";
      }
      seen.add(key);
      const pinned = login.url.params[key];
      if (pinned !== undefined ? value !== pinned
          : !login.url.free.includes(key) || !UNRESERVED.test(value)) {
        return "url";
      }
    }
    if (Object.keys(login.url.params).some((key) => !seen.has(key))) {
      return "url";
    }
    if (credentialLike(url)) {
      return "credential";
    }
  }
  // A device code: letters, digits and hyphens (RFC 8628 section 6.1), from a record that
  // shows one at all; the runner took it from the record's `code_pattern` group.
  if (code !== null) {
    if (login.code_pattern === undefined || code.length > SIGNIN_CODE_MAX
        || !/^[A-Za-z0-9-]+$/.test(code)) {
      return "code";
    }
    if (credentialLike(code)) {
      return "credential";
    }
  }
  return null;
}

/**
 * Whether a source's vendor sign-in is switched on: the control record's switch for it
 * (`signins`), or else its record's verdict on the vendor's terms for hosted use (on only
 * when `allowed`).
 */
export function signinOn(rec: SourceRecord,
                         switches: Readonly<Record<string, boolean>> | null): boolean {
  const login = rec.cli?.login as LoginSpec | undefined;
  return login !== undefined && rec.unwired === undefined
    && (switches?.[rec.id] ?? login.hosted_use.verdict === "allowed");
}

/** The served records whose sign-in is switched on, sorted: what the binder hands the object. */
export function signinsOn(sources: Sources,
                          switches: Readonly<Record<string, boolean>> | null): string[] {
  return sources.ids().filter((id) => signinOn(sources.get(id)!, switches)).sort();
}

/** The verdicts on the vendor's terms for a platform hosting its CLI's sign-in. */
const HOSTED_USE: readonly string[] = Object.freeze(["allowed", "unclear", "forbidden"]);

/** A record's sign-in, as `loginSpec` checked it. */
export interface LoginSpec {
  readonly argv: readonly string[];
  readonly url_pattern: string;
  /**
   * The vendor's sign-in page exactly: its hosts (among the login hosts), its path, its fixed
   * parameters, its free ones.
   */
  readonly url: { readonly hosts: readonly string[]; readonly path: string;
                  readonly params: Readonly<Record<string, string>>;
                  readonly free: readonly string[] };
  readonly code_pattern?: string;
  readonly accepts_code?: true;
  /** The vendor's code's lifetime, whole seconds, and where that is documented or measured. */
  readonly expires: { readonly s: number; readonly citation: string };
  readonly hosted_use: { readonly verdict: string; readonly citation: string };
}

/** A URL parameter's name, as a record pins it. */
const PARAM_NAME = /^[a-z][a-z0-9_]{0,63}$/;

/**
 * The vendor's own sign-in in the sandbox, as the runner drives it (always on a terminal of
 * its own): its command, what of its output is shown (`url_pattern` on the record's login
 * hosts, its page pinned by `url`, `code_pattern`), whether it takes a pasted code, its
 * status, its sign-out (`logout_argv`, the vendor's own, before its secrets are deleted),
 * the vendor's code's lifetime (`expires`, cited: the sign-in's deadline), and the verdict on
 * the vendor's terms for hosting it (`hosted_use`, cited), which sets whether the platform
 * offers it unless the control record says otherwise.
 */
function loginSpec(check: ReturnType<typeof checker>, value: unknown, argv: Argv):
    Record<string, unknown> {
  const fields = check.keys(value, "cli.login", ["argv", "url_pattern", "url", "expires",
                                                 "hosted_use"], [
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
  if (typeof expires["s"] !== "number" || !Number.isSafeInteger(expires["s"])
      || expires["s"] < 1) {
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
  if (!HOSTED_USE.includes(String(hosted["verdict"]))) {
    check.fail("cli.login.hosted_use.verdict is allowed, unclear or forbidden");
  }
  check.text(hosted["citation"], CITATION, "cli.login.hosted_use.citation");
  for (const key of ["url_pattern", "code_pattern"] as const) {
    const pattern = fields[key];
    if (pattern === undefined) {
      continue;
    }
    let compiles = typeof pattern === "string" && pattern.length <= 512;
    try {
      new RegExp(String(pattern));
    } catch {
      compiles = false;
    }
    if (!compiles) {
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
  return fields;
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
  const optional = ["help_url", "suggested", "unwired"];
  const cli = connect.includes("cli");
  const fields = cli ? check.keys(copy, "the record", [...base, "cli"], optional)
    : check.keys(copy, "the record",
                 [...base, ...GATEWAY_KEYS.filter((key) => key !== "models"), "engines",
                  ...(connect.includes("oauth") ? ["oauth"] : [])],
                 [...optional, "models"]);
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
  if (fields["unwired"] !== undefined) {
    record["unwired"] = check.text(fields["unwired"], /^[^\p{Cc}]{1,256}$/u, "unwired");
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
      token_endpoint: oauth.token_endpoint, refresh_with: oauth.refresh_with,
      ...(oauth.revocation_endpoint === undefined ? {}
        : { revocation_endpoint: oauth.revocation_endpoint }),
      ...(oauth.resource === undefined ? {} : { resource: oauth.resource }),
    },
  }, true);
}

/** `records` as the doors serve them: each checked whole when it is read. */
export function catalogOf(records: readonly unknown[]): Sources {
  const ids = records.map((record) => object(record) ? record["id"] : undefined);
  if (new Set(ids).size !== ids.length) {
    // Two records under one id: which one is served would be an accident (Fact 8).
    const repeated = ids.find((id, index) => ids.indexOf(id) !== index);
    throw new SourceRecordError(`source record ${JSON.stringify(String(repeated))}: ` +
                                "its id is another record's");
  }
  const byId = (id: string): unknown => records.find((record) =>
    object(record) && record["id"] === id);
  return {
    get: (id) => {
      const found = typeof id === "string" && SOURCE_ID.test(id) ? byId(id) : undefined;
      if (found === undefined) {
        return undefined;
      }
      const record = sourceRecord(found);
      // Each engine a key names is a served CLI record that runs on a key of this provider.
      for (const engine of record.engines ?? []) {
        const named = byId(engine) === undefined ? undefined : sourceRecord(byId(engine));
        if (named?.unwired !== undefined) {
          throw new SourceRecordError(`source record ${JSON.stringify(record.id)}: engine ` +
            `${JSON.stringify(engine)} is unwired (${named.unwired}), so no key runs it`);
        }
        const cli = named?.cli;
        if (cli?.api_key_env?.[record.upstream!.provider] === undefined) {
          throw new SourceRecordError(`source record ${JSON.stringify(record.id)}: engine ` +
            `${JSON.stringify(engine)} is a CLI record that runs on an API key of provider ` +
            `${JSON.stringify(record.upstream!.provider)}`);
        }
      }
      return record;
    },
    ids: () => records.map((record) => object(record) ? String(record["id"]) : "?"),
  };
}

/**
 * What one engine run's environment is made from (`sandboxEnv`, G4's `network.ts`): the
 * credential it runs on, the engine's own variables for each provider's key (an API-key
 * run's alone), and the variables that credential forbids.
 */
export interface RunEnv {
  readonly credential: CredentialKind;
  readonly api_key_env: Readonly<Record<string, ApiKeyEnv>>;
  readonly forbid_env: readonly string[];
  /**
   * The run's environment, as an allow-list: `HOME` at the engine home and the record's
   * own variables for this credential (its home, `env`, `no_update` and the credential's
   * own), each a template the runner fills. The runner starts from an empty environment
   * and sets these, the process basics it keeps (`inherit`) and, on an API key alone, the
   * key's variables (`sandboxEnv`); nothing else of the sandbox's environment reaches it.
   */
  readonly env: Readonly<Record<string, string>>;
  readonly inherit: readonly string[];
  /** On an API key, the header variable that carries the run's secret (`run_secret_env`). */
  readonly secret_header?: { readonly env: string; readonly value: string };
}

/**
 * The environment `engine` runs on `credential` with, from its record: refused (a
 * `SourceRecordError`) unless the engine is a served CLI record that runs on that
 * credential. A run on the vendor's own sign-in gets no API-key variables at all.
 */
export function runEnv(sources: Sources, engine: string, credential: CredentialKind): RunEnv {
  const record = sources.get(engine);
  const cli = record?.cli;
  if (record === undefined || cli === undefined || record.unwired !== undefined
      || !cli.credential.includes(credential)) {
    throw new SourceRecordError(`engine ${JSON.stringify(engine)} is not a served CLI ` +
                                `record that runs on ${credential}`);
  }
  const forbidEnv = cli.per_credential?.[credential]?.forbid_env ?? Object.freeze([]);
  const env: Record<string, string> = {
    HOME: "{home}", ...cli.home_env, ...cli.env,
    ...("env" in cli.no_update ? cli.no_update.env : {}),
    ...cli.per_credential?.[credential]?.env,
  };
  const forbidden = Object.keys(env).find((name) => forbids(forbidEnv, name));
  if (forbidden !== undefined) {
    throw new SourceRecordError(`engine ${JSON.stringify(engine)} sets ${forbidden}, which ` +
                                `${credential} forbids`);
  }
  return Object.freeze({
    credential,
    api_key_env: credential === "api_key" ? cli.api_key_env ?? Object.freeze({})
      : Object.freeze({}),
    forbid_env: forbidEnv,
    env: Object.freeze(env),
    inherit: RUN_BASICS,
    ...(credential === "api_key" && cli.run_secret_env !== undefined
      ? { secret_header: cli.run_secret_env } : {}),
  });
}

/** The bundled records (`platform/sources/`). */
export function bundledSources(): Sources {
  return catalogOf(BUNDLED);
}

/** Every record's file contents, for the guards and tests. */
export function bundledRecords(): readonly unknown[] {
  return BUNDLED;
}
