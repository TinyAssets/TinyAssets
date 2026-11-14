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
import identity from "../identity.json" with { type: "json" };

import type { EgressScope } from "./egress-scope";
import type { NetworkRule } from "./sandbox";
import { Refusal } from "./refusal";
import { CONNECTION_ID, HEX64, RUN_ID, SIGNIN_ID } from "./ids";
import { forbids } from "./text";

// Read from `identity.json`, their one copy, as text, so this module holds strings alone.
/** Our zone: the public host's, the egress host's, and every name a callback never reaches. */
export const ZONE = `${identity.host}`;
/** The egress door's host: always allowed, the one host E2B's transform is set on. */
export const EGRESS_HOST = `${identity.egress}`;
export const EGRESS_ORIGIN = `https://${EGRESS_HOST}`;
/**
 * The one resolver the sandbox may reach. Which resolver is best is unverified (research
 * §5.4.1); 8b's live probe checks that the sandbox resolves through it and nothing else.
 */
export const DNS_SERVERS: readonly string[] = Object.freeze(["1.1.1.1"]);
/** E2B's `ALL_TRAFFIC`: every address, what a sandbox's `denyOut` names. */
export const ALL_TRAFFIC = "0.0.0.0/0";

/**
 * A sandbox's `allowOut` for `hosts`: each once, sorted, then the egress door, last and
 * always (the owner's network document, the Worker's widening for a sign-in, and the
 * template's checks all list it so).
 */
export function allowList(hosts: Iterable<string>): string[] {
  const names = new Set(hosts);
  names.delete(EGRESS_HOST);
  return [...[...names].sort(), EGRESS_HOST];
}

/**
 * A network that lets out only `hosts` and the egress door, denies everything else, and
 * pins the resolvers; no transform rules (the owner's network document adds the door's).
 */
export function closedNetwork(hosts: Iterable<string>): {
  allowOut: string[]; denyOut: string[]; dnsServers: string[];
  rules: Record<string, readonly NetworkRule[]>;
} {
  return { allowOut: allowList(hosts), denyOut: [ALL_TRAFFIC], dnsServers: [...DNS_SERVERS],
           rules: {} };
}

/** The header E2B's transform sets to the sandbox's egress token. */
export const SANDBOX_HEADER = "x-ta-sandbox";
/** The header the transform sets to the egress generation its network was made under. */
export const GENERATION_HEADER = "x-ta-network-generation";

/**
 * The headers the egress door's one transform sets in a network document (an owner's plan,
 * or a live sandbox's as E2B's info returns it), or null when it sets none.
 */
export function doorHeaders(network: unknown): Readonly<Record<string, unknown>> | null {
  const rules = (network as { rules?: Record<string, unknown> } | null)?.rules;
  const door = rules?.[EGRESS_HOST];
  const first = Array.isArray(door) ? door[0] as { transform?: { headers?: unknown } } : undefined;
  const headers = first?.transform?.headers;
  return typeof headers === "object" && headers !== null
    ? headers as Readonly<Record<string, unknown>> : null;
}

/** `network` with the door's one transform setting `name` to `value` beside what it sets. */
function withDoorHeader<N extends { readonly rules: object }>(network: N, name: string,
                                                              value: string): N {
  const headers: Record<string, unknown> = { ...doorHeaders(network) };
  headers[name] = value;
  const rules: Record<string, unknown> = { ...network.rules };
  rules[EGRESS_HOST] = [{ transform: { headers } }];
  return { ...network, rules } as N;
}

/** The egress token a network document's door transform carries, or null: its one reader. */
export function egressTokenOf(network: unknown): string | null {
  const token = doorHeaders(network)?.[SANDBOX_HEADER];
  return typeof token === "string" && token !== "" ? token : null;
}

/**
 * `network` with the lease's egress token set in the door's transform, the one place it
 * goes (E2B sets it outside the VM): its one writer, for the start that opens the lease and
 * the sandbox Worker's re-apply.
 */
export function withEgressToken<N extends { readonly rules: object }>(network: N,
                                                                     token: string): N {
  return withDoorHeader(network, SANDBOX_HEADER, token);
}

/** A generation as `GENERATION_HEADER` carries it: a whole number from 1, in decimal. */
const GENERATION_TEXT = /^[1-9][0-9]{0,14}$/;

/**
 * The egress generation a `GENERATION_HEADER` value names, or null: the one reader of its
 * text, for the egress door (a request's header) and a network document (`generationOf`).
 */
export function generationIn(header: unknown): number | null {
  return typeof header === "string" && GENERATION_TEXT.test(header) ? Number(header) : null;
}

/** The egress generation a network document's door transform carries, or null. */
export function generationOf(network: unknown): number | null {
  return generationIn(doorHeaders(network)?.[GENERATION_HEADER]);
}

/**
 * `network` with the egress `generation` it is made under set in the door's transform, so
 * E2B stamps it on every call through the door: its one writer, for the owner's network
 * document. A generation its reader would not read back is refused.
 */
export function withGeneration<N extends { readonly rules: object }>(network: N,
                                                                    generation: number): N {
  const text = String(generation);
  if (generationIn(text) !== generation) {
    throw new Refusal("invalid", "a policy generation is a whole number from 1");
  }
  return withDoorHeader(network, GENERATION_HEADER, text);
}
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

/** How the egress door saw a stream end. */
export const OUTCOMES: readonly string[] = Object.freeze(["complete", "aborted", "failed",
                                                          "deadline", "ended"]);
export type Outcome = "complete" | "aborted" | "failed" | "deadline" | "ended";

/**
 * How often a streaming call's door beats its open reservation (`egressBeat`), in seconds:
 * the object settles a reservation lost three missed beats on (`LOST_AFTER_S`).
 */
export const EGRESS_BEAT_S = 60;

/** The sandbox's report of the engines' sign-ins, the runner's. */
const SIGNIN_DOOR = "/signin";
/** The sync agent's doors, each `/sync/<door>`. */
export const SYNC_DOORS = Object.freeze(["changes", "list", "read", "chunks", "commit",
  "upload_begin", "upload_chunks", "upload_abort"] as const);
export type SyncDoor = (typeof SYNC_DOORS)[number];

/** The egress door's path segment before a connection id: `/c/<connection>/...`. */
const CONNECTION_SEGMENT = "c";
/** The egress door's path segment before a run id: `/r/<run>/...`. */
const RUN_SEGMENT = "r";

/**
 * A door on the egress host, as its path names it (`egressDoor`): a run's model call (its
 * run, its secret or none when its header carries it, a connection, the provider's own
 * path), a run's MCP door (by its own secret), a run's event door (the runner's), the
 * sandbox's sign-in report, a sign-in's events (the runner's), or a sync agent's door.
 */
export type EgressDoor =
  | { readonly door: "model"; readonly run: string; readonly secret: string | undefined;
      readonly connection: string; readonly path: string }
  | { readonly door: "mcp"; readonly run: string; readonly secret: string | undefined }
  | { readonly door: "events"; readonly run: string }
  | { readonly door: "signin" }
  | { readonly door: "signin_events"; readonly signin: string }
  | { readonly door: "sync"; readonly sync: SyncDoor };

/**
 * The door a path on the egress host names, each id held to its own grammar (`ids.ts`), or
 * null for any other path: the one reader of the paths `runUrls`, `signinUrls` and
 * `egressBaseUrl` make.
 */
export function egressDoor(path: string): EgressDoor | null {
  const parts = path.split("/");
  if (parts[0] !== "") {
    return null;
  }
  if (path === SIGNIN_DOOR) {
    return { door: "signin" };
  }
  if (`/${parts[1]}` === SIGNIN_DOOR) {
    const signin = parts[2] ?? "";
    return parts.length === 4 && SIGNIN_ID.test(signin) && parts[3] === "events"
      ? { door: "signin_events", signin } : null;
  }
  if (parts[1] === "sync") {
    const sync = SYNC_DOORS.find((name) => name === parts[2]);
    return parts.length === 3 && sync !== undefined ? { door: "sync", sync } : null;
  }
  const run = parts[2] ?? "";
  if (parts[1] !== RUN_SEGMENT || !RUN_ID.test(run)) {
    return null;
  }
  if (parts.length === 4 && parts[3] === "events") {
    return { door: "events", run };
  }
  const secret = HEX64.test(parts[3] ?? "") ? parts[3] : undefined;
  const rest = parts.slice(secret === undefined ? 3 : 4);
  if (rest.length === 1 && rest[0] === "mcp") {
    return { door: "mcp", run, secret };
  }
  const connection = rest[1] ?? "";
  return rest.length >= 3 && rest[0] === CONNECTION_SEGMENT && CONNECTION_ID.test(connection)
    ? { door: "model", run, secret, connection, path: `/${rest.slice(2).join("/")}` } : null;
}

/**
 * A run's own path on the egress host: `/r/<run>/<secret>`, before its model calls and its
 * MCP door. The run's own secret is in it, so only a process holding the run's URLs (its
 * CLI's environment) calls as that run; without the secret, `/r/<run>` (its event door,
 * the runner's).
 */
export function runPath(run: string, secret?: string): string {
  if (!RUN_ID.test(run)) {
    throw new Refusal("invalid", "invalid run");
  }
  if (secret !== undefined && !HEX64.test(secret)) {
    throw new Refusal("invalid", "invalid run secret");
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

/** A sign-in's event door, and the sandbox's sign-in report door, on the egress host. */
export function signinUrls(signin: string): { readonly events: string; readonly signin: string } {
  if (!SIGNIN_ID.test(signin)) {
    throw new Refusal("invalid", "invalid sign-in");
  }
  return Object.freeze({ events: `${EGRESS_ORIGIN}${SIGNIN_DOOR}/${signin}/events`,
                         signin: `${EGRESS_ORIGIN}${SIGNIN_DOOR}` });
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
    throw new Refusal("invalid", "invalid connection");
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
      throw new Refusal("run_config_invalid",
                        "a run on the vendor's own sign-in is on no connection");
    }
    return Object.freeze({});
  }
  const own = connections.find((entry) => entry.connection === run.connection);
  if (own === undefined) {
    throw new Refusal("run_config_invalid",
                      "an API-key run is on one of the owner's active connections");
  }
  const { connection, provider, egress } = own;
  if (!Object.hasOwn(run.api_key_env, provider)) {
    throw new Refusal("run_config_invalid",
                      "the engine runs on no key of this connection's provider");
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
      throw new Refusal("invalid", "invalid run secret");
    }
    set(run.secret_header.env, run.secret_header.value.replace("{run_secret}", run.secret));
  }
  set(names.api_key, placeholder);
  return Object.freeze(env);
}
