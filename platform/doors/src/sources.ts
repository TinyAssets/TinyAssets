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
 * - `cli`: the vendor's own command-line client, signed in by the owner in their sandbox
 *   (a consumer subscription, research §2.6). It never enters the gateway, so it names no
 *   upstream; its install, sign-in, invocation and hosts are kept for the sandbox (6).
 *
 * A gateway source (`api_key`, `oauth`) names the pi-ai provider (`custom` for a declared
 * endpoint), the API its models speak, the base URL, how the secret is sent (`auth`), the
 * setup check (`verify_url`) and the only endpoints the secret may reach. pi-ai's catalog
 * supplies a known provider's base URL and models, and a record of one restates them
 * (the sources guard holds the two equal); only a `custom` record declares its own models.
 * Those values are what the gateway seals with the secret. They are checked by the
 * gateway's own `source` (so its `SUPPORTED_APIS`, its endpoint and URL grammar are the
 * one rule), here and again in the gateway when it seals.
 */
import {
  type Endpoint, keyExchangeSource, type Source, source as gatewaySource, type Upstream,
} from "../../gateway/src/credential";
import { SOURCE_ID } from "../../owner/src/text";

import cerebras from "../../sources/cerebras.json";
import chatgpt from "../../sources/chatgpt.json";
import claudeCode from "../../sources/claude-code.json";
import codex from "../../sources/codex.json";
import gemini from "../../sources/gemini.json";
import googleAiStudio from "../../sources/google_ai_studio.json";
import groq from "../../sources/groq.json";
import kimi from "../../sources/kimi.json";
import mistral from "../../sources/mistral.json";
import openrouter from "../../sources/openrouter_user_models_v1.json";

/** The records the doors serve, as the files hold them. Never written: each read is a copy. */
const BUNDLED: readonly unknown[] = Object.freeze([
  cerebras, chatgpt, claudeCode, codex, gemini, googleAiStudio, groq, kimi, mistral, openrouter,
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

/** A vendor CLI's own sign-in (`connect: ["cli"]`), for the sandbox. */
export interface CliSpec {
  readonly install: Readonly<Record<string, unknown>>;
  readonly login: Readonly<Record<string, unknown>>;
  readonly invoke: Readonly<Record<string, unknown>>;
  readonly env?: Readonly<Record<string, string>>;
  readonly files?: Readonly<Record<string, string>>;
  readonly state_paths: readonly string[];
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
const SCOPE = /^[\x21-\x7e]+(?: [\x21-\x7e]+)*$/;
const CLIENT_ID = /^[\x21-\x7e]{1,512}$/;
const PATH = /^[A-Za-z0-9._/-]{1,256}$/;

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

function cliSpec(check: ReturnType<typeof checker>, value: unknown): CliSpec {
  const fields = check.keys(value, "cli", ["install", "login", "invoke", "state_paths",
                                           "hosts"], ["env", "files"]);
  const install = check.keys(fields["install"], "cli.install",
                             ["manager", "package", "version", "integrity", "bin",
                              "verify_argv", "lock_sha256"], ["lifecycle_scripts"]);
  if (install["manager"] !== "npm"
      || !/^(?:@[a-z0-9-]+\/)?[a-z0-9-]+$/.test(String(install["package"]))
      || !/^\d+\.\d+\.\d+$/.test(String(install["version"]))
      || !/^sha512-[A-Za-z0-9+/]{86}==$/.test(String(install["integrity"]))
      || !/^[0-9a-f]{64}$/.test(String(install["lock_sha256"]))) {
    check.fail("cli.install pins an npm package by exact version, integrity and lock digest");
  }
  check.strings(install["verify_argv"], "cli.install.verify_argv", /^.{1,256}$/su, 16);
  for (const part of ["login", "invoke"] as const) {
    const section = fields[part];
    if (!object(section)) {
      check.fail(`cli.${part} is an object`);
    }
    check.strings((section as Record<string, unknown>)["argv"], `cli.${part}.argv`,
                  /^.{0,4096}$/su, 64);
  }
  const hosts = check.keys(fields["hosts"], "cli.hosts", ["registry", "login", "provider"]);
  for (const kind of ["registry", "login", "provider"] as const) {
    check.strings(hosts[kind], `cli.hosts.${kind}`, HOST, 16);
  }
  const env = fields["env"] === undefined ? undefined
    : check.stringMap(fields["env"], "cli.env", /^[A-Z][A-Z0-9_]{0,63}$/, 16);
  const files = fields["files"] === undefined ? undefined
    : check.stringMap(fields["files"], "cli.files", PATH, 8);
  return Object.freeze({
    install: Object.freeze(install), login: Object.freeze(fields["login"] as object),
    invoke: Object.freeze(fields["invoke"] as object),
    ...(env === undefined ? {} : { env: Object.freeze(env) }),
    ...(files === undefined ? {} : { files: Object.freeze(files) }),
    state_paths: Object.freeze(check.strings(fields["state_paths"], "cli.state_paths", PATH, 8)),
    hosts: Object.freeze(hosts as unknown as CliSpec["hosts"]),
  }) as CliSpec;
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
                 [...base, ...GATEWAY_KEYS.filter((key) => key !== "models"),
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
      return found === undefined ? undefined : sourceRecord(found);
    },
    ids: () => records.map((record) => object(record) ? String(record["id"]) : "?"),
  };
}

/** The bundled records (`platform/sources/`). */
export function bundledSources(): Sources {
  return catalogOf(BUNDLED);
}

/** Every record's file contents, for the guards and tests. */
export function bundledRecords(): readonly unknown[] {
  return BUNDLED;
}
