/**
 * Model source records (gateway research §2.4, §2.6, §7.2; tasks 4 G3): what an owner can
 * connect, as data. Each is an editable JSON file beside this module (`platform/sources/`,
 * Direction 3), ported from the Python's `tinyassets/providers/source_records/`, and checked
 * whole (`check.ts`), so a record that does not fit fails loudly where it is used and in the
 * tests, never half-read.
 *
 * A record names how it connects (`connect`):
 * - `api_key`: a pasted key, sealed by the gateway (`doors/src/connect.ts`).
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
 *   checker holds shape only: no vendor is named there.
 *
 * A gateway source (`api_key`, `oauth`) names the pi-ai provider (`custom` for a declared
 * endpoint), the API its models speak, the base URL, how the secret is sent (`auth`), the
 * setup check (`verify_url`), the only endpoints the secret may reach, and its `engines`:
 * the CLI records that can run on it through the egress door. pi-ai's catalog
 * supplies a known provider's base URL and models, and a record of one restates them
 * (the sources guard holds the two equal); only a `custom` record declares its own models.
 * Those values are what the gateway seals with the secret. They are checked by the
 * gateway's own `source` (so its `SUPPORTED_APIS`, its endpoint and URL grammar are the
 * one rule), in the checker and again in the gateway when it seals.
 *
 * A record from outside the platform's own (an MCP server's authorization server, a source
 * from an owner's package) is marked `curated: false`; none exists yet.
 */
import type { Endpoint, Upstream } from "../../contract/src/egress-scope";
import type { EngineEvents } from "../../contract/src/run-events";

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

/** The verdicts on the vendor's terms for a platform hosting its CLI's sign-in. */
export type HostedUse = "allowed" | "unclear" | "forbidden";

/** A credential file the sign-in writes, and the fields it holds once signed in. */
export interface StateCheck {
  readonly path: string;
  readonly present: readonly string[];
}

/**
 * The vendor's own sign-in in the sandbox, as the runner drives it (always on a terminal of
 * its own): its command, what of its output is shown (`url_pattern` on the record's login
 * hosts, its page pinned by `url`, `code_pattern`), whether it takes a pasted code, its
 * status (`status_argv` and `status_equals`, or `state_checks` over its store), its sign-out
 * (`logout_argv`, the vendor's own, before its secrets are deleted), the vendor's code's
 * lifetime (`expires`, cited: the sign-in's deadline), and the verdict on the vendor's terms
 * for hosting it (`hosted_use`, cited), which sets whether the platform offers it unless the
 * control record says otherwise.
 */
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
  readonly hosted_use: { readonly verdict: HostedUse; readonly citation: string };
  readonly status_argv?: readonly string[];
  readonly status_equals?: Readonly<Record<string, string | number | boolean>>;
  readonly state_checks?: readonly StateCheck[];
  /** The sign-in is complete once its `state_checks` hold, though the CLI stays up. */
  readonly complete_on_state?: true;
  readonly logout_argv?: readonly string[];
}

/** An engine (`connect: ["cli"]`): a provider's own CLI, run whole in the sandbox. */
export interface CliSpec {
  readonly install: Install;
  readonly credential: readonly CredentialKind[];
  readonly login?: LoginSpec;
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
  /**
   * How the route probe makes the CLI compact its context at its next turn (`argv`, put
   * after its binary on a resumed turn), and the text its compaction request carries
   * (`marker`, its compact prompt's): the probe holds a compaction's requests to the door's
   * rules as a turn's. Absent for a CLI the probe does not compact.
   */
  readonly compaction?: { readonly argv: readonly string[]; readonly marker: string };
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
  /** A record the platform does not curate; absent for the platform's own. */
  readonly curated?: false;
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

/** The records a catalog serves, by id. */
export interface Sources {
  /** The record, checked whole; undefined for an id no record has. */
  get(id: string): SourceRecord | undefined;
  /** Every record's id. */
  ids(): readonly string[];
}
