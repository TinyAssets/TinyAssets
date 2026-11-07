/**
 * The egress wire (gateway research §5.4; tasks 4 G4): the host, paths, header names, codes
 * and statuses the sandbox, the egress door (`doors/src/egress.ts`), the owner's object
 * (`owner/src/egress.ts`, `owner/src/network.ts`) and the gateway's forward speak, and the
 * environment a run's model clients reach the door by. Pure: nothing here reaches E2B or the
 * network.
 *
 * **The doors on the egress host.** A run's own path is `/r/<run>/<secret>`, the run's own
 * secret in it, so only a process holding the run's URLs (its CLI's environment) calls as
 * that run; without the secret, `/r/<run>` (its event door, the runner's). Before a model
 * call, the connection: `/r/<run>/<secret>/c/<connection>/<the provider's own path>`. The
 * run's MCP door is `/r/<run>/<secret>/mcp`; the sandbox's sign-in report `/signin`, a
 * sign-in's events `/signin/<sign-in>/events`, and the sync agent's doors `/sync/<door>`.
 *
 * **The environment** (`sandboxEnv`): one engine run's, and it carries only that run's own
 * credential. A run on the vendor's own sign-in in the sandbox carries no API-key or
 * base-URL variable at all, and none its record forbids (`forbid_env`), so the CLI runs on
 * the sign-in and never on a key or through the door. An API-key run carries its one
 * connection's: a base URL on the egress door and a placeholder key,
 * `ta_ph_<connection>_<random>`, which carries no secret: the door ignores it, the gateway
 * strips it with every other inbound credential (`forward.ts`) and injects the sealed one.
 * The names follow one rule, never a provider's own code: `<PROVIDER>_BASE_URL` and
 * `<PROVIDER>_API_KEY` (the provider id in upper case, `-` as `_`: `ANTHROPIC_BASE_URL`,
 * `OPENAI_API_KEY`, `GROQ_API_KEY`), `TA_<CONNECTION>_BASE_URL` and
 * `TA_<CONNECTION>_API_KEY`, and the engine's own names for that provider as its source
 * record declares them (`cli.api_key_env`). A base URL is the door, the run, the connection,
 * then the path of the sealed base URL: `https://egress.tinyassets.io/r/<run>/c/<connection>/`
 * for Anthropic's, `…/r/<run>/c/<connection>/v1` for OpenAI's, so a client appends its own
 * API path.
 */
import type { EgressScope } from "./egress-scope";
import { Invalid } from "./errors";
import { CONNECTION_ID, HEX64, RUN_ID } from "./ids";
import { forbids } from "./text";

/** Our zone: the public host's, the egress host's, and every name a callback never reaches. */
export const ZONE = "tinyassets.io";
/** The egress door's host: always allowed, the one host E2B's transform is set on. */
export const EGRESS_HOST = `egress.${ZONE}`;
export const EGRESS_ORIGIN = `https://${EGRESS_HOST}`;
/**
 * The one resolver the sandbox may reach. Which resolver is best is unverified (research
 * §5.4.1); 8b's live probe checks that the sandbox resolves through it and nothing else.
 */
export const DNS_SERVERS: readonly string[] = Object.freeze(["1.1.1.1"]);

/** The header E2B's transform sets to the sandbox's egress token. */
export const SANDBOX_HEADER = "x-ta-sandbox";
/** The header the transform sets to the egress generation its network was made under. */
export const GENERATION_HEADER = "x-ta-network-generation";
/**
 * The run's own secret as a header, where its CLI sends one (`run_secret_env`, its MCP
 * file's `headers`); the path carries it otherwise. Never forwarded (`x-ta-*`).
 */
export const RUN_HEADER = "x-ta-run";
/** The runner's own secret, beside the egress token, on its event and sign-in posts. */
export const RUNNER_HEADER = "x-ta-runner";
/** The sync agent's lease token on its sync doors (the lease's `sync` kind). */
export const SYNC_HEADER = "x-ta-sync";
/** The gateway's own header on its answer, a blob resealed at the next version, for the object. */
export const RESEALED_HEADER = "x-ta-resealed";

/**
 * Every refusal the egress door and the owner's object answer a sandbox request with, each
 * a fixed code (`owner/src/egress.ts`, `doors/src/egress.ts`), and the HTTP status it is
 * answered with.
 */
export const EGRESS_STATUS = Object.freeze({
  egress_token_missing: 401, egress_token_unknown: 401, egress_token_expired: 401,
  egress_lease_closed: 401, egress_run_closed: 401, egress_run_connection: 403,
  egress_policy_stale: 409,
  egress_network_stale: 409,
  egress_connection_refused: 403, egress_endpoint_refused: 403, egress_unmetered: 403,
  egress_length_required: 411, egress_rate: 429, egress_spend_refused: 402,
  egress_unknown_model: 404, egress_unpriced_option: 403, egress_too_large: 413,
} as const);
export type EgressCode = keyof typeof EGRESS_STATUS;

/** How the egress door saw a stream end. */
export const OUTCOMES: readonly string[] = Object.freeze(["complete", "aborted", "failed",
                                                          "deadline", "ended"]);
export type Outcome = "complete" | "aborted" | "failed" | "deadline" | "ended";

/**
 * How often a streaming call's door beats its open reservation (`egressBeat`), in seconds:
 * the object settles a reservation lost three missed beats on (`LOST_AFTER_S`).
 */
export const EGRESS_BEAT_S = 60;

/** A run's model call: its run, its secret (or none: its header), a connection, the path. */
export const MODEL_DOOR = /^\/r\/(run_[0-9a-f]{32})(?:\/([0-9a-f]{64}))?\/c\/(c[0-9a-f]{24})(\/.*)$/;
/** A run's MCP door, by its own secret. */
export const MCP_DOOR = /^\/r\/(run_[0-9a-f]{32})(?:\/([0-9a-f]{64}))?\/mcp$/;
/** A run's event door, the runner's. */
export const EVENTS_DOOR = /^\/r\/(run_[0-9a-f]{32})\/events$/;
/** The runner's report of the engines' sign-ins, for the sandbox as a whole. */
export const SIGNIN_DOOR = "/signin";
/** A sign-in's events, the runner's. */
export const SIGNIN_EVENTS_DOOR = /^\/signin\/(si_[0-9a-f]{32})\/events$/;
/** The sync agent's doors. */
export const SYNC_DOOR =
  /^\/sync\/(changes|list|read|chunks|commit|upload_begin|upload_chunks|upload_abort)$/;
export type SyncDoor = "changes" | "list" | "read" | "chunks" | "commit" | "upload_begin"
  | "upload_chunks" | "upload_abort";

/** The egress door's path segment before a connection id: `/c/<connection>/...`. */
const CONNECTION_SEGMENT = "c";
/** The egress door's path segment before a run id: `/r/<run>/...`. */
const RUN_SEGMENT = "r";

/**
 * A run's own path on the egress host: `/r/<run>/<secret>`, before its model calls and its
 * MCP door. The run's own secret is in it, so only a process holding the run's URLs (its
 * CLI's environment) calls as that run; without the secret, `/r/<run>` (its event door,
 * the runner's).
 */
export function runPath(run: string, secret?: string): string {
  if (!RUN_ID.test(run)) {
    throw new Invalid("invalid run");
  }
  if (secret !== undefined && !HEX64.test(secret)) {
    throw new Invalid("invalid run secret");
  }
  return `/${RUN_SEGMENT}/${run}${secret === undefined ? "" : `/${secret}`}`;
}

/**
 * The run's MCP door (the record's `{mcp_url}`), its event door, and the sandbox's sign-in
 * report door, on the egress host.
 */
export function runUrls(run: string, secret: string):
    { readonly mcp: string; readonly mcp_bare: string; readonly events: string;
      readonly signin: string } {
  return Object.freeze({ mcp: `${EGRESS_ORIGIN}${runPath(run, secret)}/mcp`,
                         mcp_bare: `${EGRESS_ORIGIN}${runPath(run)}/mcp`,
                         events: `${EGRESS_ORIGIN}${runPath(run)}/events`,
                         signin: `${EGRESS_ORIGIN}${SIGNIN_DOOR}` });
}

/** A sign-in's event door on the egress host. */
export function signinEventsUrl(signin: string): string {
  return `${EGRESS_ORIGIN}${SIGNIN_DOOR}/${signin}/events`;
}

/** A connection the sandbox may reach: its id, its provider and where it may be forwarded. */
export interface Egressible {
  readonly connection: string;
  readonly provider: string;
  readonly egress: EgressScope;
}

/** The environment-variable stem a name makes: upper case, every other character `_`. */
export function envStem(name: string): string {
  return name.toUpperCase().replace(/[^A-Z0-9]/g, "_");
}

/**
 * One engine run, as its environment is made (from its record, by `sources/src/run-env.ts`'s
 * `runEnv`): the credential it runs on; an API-key run's connection (null on the vendor's
 * own sign-in); the engine's own variables for each provider's key: its base-URL variable
 * (none when its base URL comes by a file template) and key variable, and whether its base
 * URL is the sealed one on the door (`sealed`) or the door and the connection alone
 * (`connection`: its SDK appends the API version itself, as Gemini CLI's `@google/genai`
 * client appends `/v1beta`); and the variables the credential forbids. No provider is
 * named here.
 */
export interface EngineRun {
  /** The run itself, whose path its base URLs carry, so each call names its run. */
  readonly run: string;
  /** The run's own secret, which its base URLs carry beside it, or its header. */
  readonly secret: string;
  /** Where the CLI takes the secret as a header instead (its record's `run_secret_env`). */
  readonly secret_header?: { readonly env: string; readonly value: string };
  readonly credential: "vendor_login_in_sandbox" | "api_key";
  readonly connection: string | null;
  readonly api_key_env: Readonly<Record<string, {
    readonly base_url: string | null; readonly api_key: string;
    readonly base: "sealed" | "connection" }>>;
  readonly forbid_env: readonly string[];
}

/**
 * The base URL a run's clients use for a connection: the door, the run, the connection, the
 * sealed path (or, with no scope, the door, the run and the connection alone). The run is in
 * the path so that each call names its run, and the door holds it to that run's own
 * connection: simultaneous runs each spend on their own.
 */
export function egressBaseUrl(connection: string, egress: EgressScope | null, run: string,
                              secret: string | undefined): string {
  if (!CONNECTION_ID.test(connection)) {
    throw new Invalid("invalid connection");
  }
  const path = egress === null ? "/" : new URL(egress.base_url).pathname;
  return `${EGRESS_ORIGIN}${runPath(run, secret)}/${CONNECTION_SEGMENT}/${connection}${
    path === "/" ? "/" : path.replace(/\/+$/, "")}`;
}

/**
 * One engine run's environment (`run`), from the owner's egress-able `connections`:
 * nothing on the vendor's own sign-in; on an API key, its one connection's base URL on the
 * door and a placeholder key, `random()` making the placeholder's random part (hex), under
 * the rule's names and the engine's own. A placeholder carries no secret. A connection
 * that is not the owner's egress-able one, a provider the engine runs no key of, two names
 * for one variable that disagree, or a variable the credential forbids fails loudly.
 */
export function sandboxEnv(connections: readonly Egressible[], random: () => string,
                           run: EngineRun): Readonly<Record<string, string>> {
  if (run.credential === "vendor_login_in_sandbox") {
    if (run.connection !== null) {
      throw new Invalid("a run on the vendor's own sign-in is on no connection");
    }
    return Object.freeze({});
  }
  const own = connections.find((entry) => entry.connection === run.connection);
  if (own === undefined) {
    throw new Invalid("an API-key run is on one of the owner's active connections");
  }
  const { connection, provider, egress } = own;
  if (!Object.hasOwn(run.api_key_env, provider)) {
    throw new Invalid("the engine runs on no key of this connection's provider");
  }
  const env: Record<string, string> = {};
  const set = (name: string, value: string) => {
    if (env[name] !== undefined && env[name] !== value) {
      throw new Error(`the source records set ${name} to two values`);
    }
    if (forbids(run.forbid_env, name)) {
      throw new Error(`the run's credential forbids ${name}`);
    }
    env[name] = value;
  };
  // The secret in the path, unless the CLI sends it as a header (`RUN_HEADER`).
  const inPath = run.secret_header === undefined ? run.secret : undefined;
  const base = egressBaseUrl(connection, egress, run.run, inPath);
  const part = random();
  if (!/^[0-9a-f]{16,64}$/.test(part)) {
    throw new Error("a placeholder's random part is 16 to 64 hex");
  }
  const placeholder = `ta_ph_${connection}_${part}`;
  set(`TA_${envStem(connection)}_BASE_URL`, base);
  set(`TA_${envStem(connection)}_API_KEY`, placeholder);
  set(`${envStem(provider)}_BASE_URL`, base);
  set(`${envStem(provider)}_API_KEY`, placeholder);
  const names = run.api_key_env[provider]!;
  if (names.base_url !== null) {
    set(names.base_url, names.base === "connection"
      ? egressBaseUrl(connection, null, run.run, inPath) : base);
  }
  if (run.secret_header !== undefined) {
    if (!HEX64.test(run.secret)) {
      throw new Invalid("invalid run secret");
    }
    set(run.secret_header.env, run.secret_header.value.replace("{run_secret}", run.secret));
  }
  set(names.api_key, placeholder);
  return Object.freeze(env);
}
