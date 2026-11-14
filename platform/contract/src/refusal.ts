/**
 * The one refusal: what every Worker throws when it will not do a request, as opposed to a
 * bug. It is a value: a `code` from the closed list below, the `status` that list gives the
 * code, a `retryAfterS` for one the caller may try again, and `facts`, a JSON object. Its
 * message is human text only, shown as it is and read by no code.
 *
 * Across Workers RPC (a Durable Object's stub, a service binding) a thrown error reaches the
 * caller by workerd's own serialization. Under `enhanced_error_serialization` (on from the
 * compatibility date 2026-04-21, every Worker's is later) that keeps an error's `name`, its
 * `message` and its own properties, but never its class: the caller gets an `Error`. So a
 * refusal keeps its fields as own data properties, and `refusalOf` rebuilds one from that
 * shape (`contract/guards/refusal.test.ts` holds the compatibility settings;
 * `doors/test/api-keys.test.ts` crosses the owner's object's real RPC boundary).
 *
 * A code is `transient` or terminal. A transient refusal says nothing of the request: the
 * same one succeeds later. Most say when, with their `Retry-After` (a pool, a ceiling or a
 * post gate full for the moment, a platform gate closed); a files root that changed under a
 * rewrap is run again at once. So a lane that settles a run, a schedule or a mail on a
 * refusal settles it only on a terminal one, and leaves the item to be tried again on a
 * transient one.
 *
 * A `quiet` code is one a protocol that shows a code (the egress door, the inbound hooks)
 * does not show: it shows the refusal's message in its place, or the egress door's
 * `egress_refused`. They are the two families' own, `refused` and `invalid`, and a few the
 * object tells apart inside. No code is ever read from a message
 * (`ci/guards/refusals.test.ts`).
 */

interface Entry {
  readonly status: number;
  /** The `Retry-After` a refusal of this code carries when it names none of its own. */
  readonly retryAfterS?: number;
  /** A code a protocol that shows one shows as the refusal's message (see the header). */
  readonly quiet?: true;
  /** A refusal for the moment, the same request taken later (see the header). */
  readonly transient?: true;
}

/** How long a `storage_busy` refusal says to wait before trying again: about one lane pass. */
export const STORAGE_BUSY_RETRY_S = 30;

const REFUSED = Object.freeze({ status: 403 });
const QUIET_REFUSED = Object.freeze({ status: 403, quiet: true });
const QUIET_INVALID = Object.freeze({ status: 400, quiet: true });
const BAD_GATEWAY = Object.freeze({ status: 502 });

/** Every code, and what it is answered with. */
export const REFUSALS = Object.freeze({
  // The two families: a request the trusted side will not do (the caller lacks the right,
  // or a bound is reached), and a malformed one (a wrong field, type or size).
  refused: QUIET_REFUSED,
  invalid: QUIET_INVALID,

  // The owner's object's.
  agent_changed: REFUSED,
  agent_disabled: REFUSED,
  agent_retired: REFUSED,
  agent_undefined: REFUSED,
  archive_changed: REFUSED,
  archive_full: REFUSED,
  // An inbound binding never made, or withdrawn: 404, as the doors answer a route never made.
  binding_not_configured: Object.freeze({ status: 404 }),
  binding_withdrawn: Object.freeze({ status: 404 }),
  code_mode_off: REFUSED,
  conflict: REFUSED,
  conversation_busy: REFUSED,
  credential_refresh_failed: REFUSED,
  egress_ip_literal: REFUSED,
  // The runner's posts: the object's held posts full, or another batch of the run landing
  // between a post's units; the runner posts again after `Retry-After`.
  events_busy: Object.freeze({ status: 503, retryAfterS: 1, transient: true }),
  events_raced: Object.freeze({ status: 503, retryAfterS: 1, transient: true }),
  // Another writer changed the file under each try.
  file_busy: Object.freeze({ status: 409 }),
  // The files plane's one technical ceiling: its index in the object is full (`files.ts`
  // `INDEX_BYTES`). Room comes only from deleting files or old versions: never an upgrade.
  file_index_full: Object.freeze({ status: 507 }),
  file_quota: REFUSED,
  file_share: REFUSED,
  // An inbound sender whose proof does not verify.
  inbound_proof_invalid: Object.freeze({ status: 401 }),
  install_proof_required: REFUSED,
  ledger_agents_full: REFUSED,
  ledger_full: REFUSED,
  mail_missing: REFUSED,
  mail_tampered: REFUSED,
  message_changed: REFUSED,
  no_agent: REFUSED,
  no_conversation: REFUSED,
  no_run: REFUSED,
  no_schedule: REFUSED,
  no_source: REFUSED,
  owner_erased: REFUSED,
  owner_limit: REFUSED,
  owner_suspended: REFUSED,
  owner_unadmitted: REFUSED,
  result_too_large: REFUSED,
  row_changed: REFUSED,
  run_ended: REFUSED,
  run_inputs_full: REFUSED,
  run_no_mailbox: REFUSED,
  run_starts_none: REFUSED,
  signin_lapsed: REFUSED,
  signin_live: REFUSED,
  signin_off: REFUSED,
  sockets_full: REFUSED,
  // The hot ceiling while the archive lane moves acked mail out: about one lane pass.
  storage_busy: Object.freeze({ status: 503, retryAfterS: STORAGE_BUSY_RETRY_S,
                                transient: true }),
  // The one wall (`owner/src/storage-full.ts`), and open uploads that alone leave no room.
  storage_full: Object.freeze({ status: 507 }),
  uploads_open: Object.freeze({ status: 409 }),
  subscription_limit: REFUSED,
  sync_fenced: REFUSED,
  ticket_refused: REFUSED,
  tier_runs: REFUSED,
  tier_unread: REFUSED,
  tool_not_served: REFUSED,
  unattended_refused: REFUSED,

  // The owner's object's quiet ones: the object tells these apart inside, and a refused
  // waiting run keeps its code as its own (`runs.ts` `refuseWaiting`).
  authority_changed: QUIET_REFUSED,
  no_authority: QUIET_REFUSED,
  not_granted: QUIET_REFUSED,
  not_owner: QUIET_REFUSED,
  // The agent's `agent.json`, and the run its record and connection make, that do not hold.
  agent_file_invalid: QUIET_INVALID,
  run_config_invalid: QUIET_INVALID,
  connection_unavailable: QUIET_REFUSED,
  credential_expired: QUIET_REFUSED,
  chunk_deleting: QUIET_REFUSED,
  erasing: QUIET_REFUSED,
  message_body: QUIET_INVALID,
  post_conflict: QUIET_REFUSED,
  // The files plane: a wrapped data key that does not open for its namespace; a manifest
  // root that changed while its key was wrapped again, so the rewrap is run again.
  data_key_refused: QUIET_INVALID,
  root_changed: Object.freeze({ status: 409, transient: true }),

  // The platform gates (`control.ts`), closed: the maintenance gate (`MAINTENANCE`) or the
  // outside switch (`OUTSIDE_OFF`), each the refusal's message.
  unavailable: Object.freeze({ status: 503, retryAfterS: 60, transient: true }),

  // The doors' own: a request with no credential the door takes, at a path it does not
  // serve, by a method it does not take, or too large to read; an API key or a connect
  // refused takes `invalid`, `refused` or `not_found`.
  runner_token_missing: Object.freeze({ status: 401 }),
  run_secret_missing: Object.freeze({ status: 401 }),
  sync_token_missing: Object.freeze({ status: 401 }),
  sync_token_unknown: Object.freeze({ status: 401 }),
  sync_token_expired: Object.freeze({ status: 401 }),
  method_not_allowed: Object.freeze({ status: 405 }),
  events_too_large: Object.freeze({ status: 413 }),

  // The egress door's (`egress.ts`), answered with the object's receipt.
  egress_token_missing: Object.freeze({ status: 401 }),
  egress_token_unknown: Object.freeze({ status: 401 }),
  egress_token_expired: Object.freeze({ status: 401 }),
  egress_lease_closed: Object.freeze({ status: 401 }),
  egress_run_closed: Object.freeze({ status: 401 }),
  egress_run_connection: REFUSED,
  egress_policy_stale: Object.freeze({ status: 409 }),
  egress_network_stale: Object.freeze({ status: 409 }),
  egress_connection_refused: REFUSED,
  egress_endpoint_refused: REFUSED,
  egress_unmetered: REFUSED,
  egress_length_required: Object.freeze({ status: 411 }),
  // The tier's open calls all in flight, for the moment: a second's wait.
  egress_rate: Object.freeze({ status: 429, retryAfterS: 1, transient: true }),
  egress_spend_refused: Object.freeze({ status: 402 }),
  egress_unknown_model: Object.freeze({ status: 404 }),
  // The egress door's own, with no receipt: the gateway's call failed, or the door stopped
  // the stream before its answer (its deadline, the client gone, the object's end signal).
  egress_gateway_failed: BAD_GATEWAY,
  egress_deadline: Object.freeze({ status: 504 }),
  egress_aborted: Object.freeze({ status: 503 }),
  egress_ended: Object.freeze({ status: 503 }),

  // The sandbox Worker's (`sandbox/src/control.ts`), the founder's alert: a sandbox's runner
  // whose bytes are not their digest, or that speaks a protocol the Worker does not.
  sandbox_runner_mismatch: BAD_GATEWAY,
  sandbox_runner_protocol: BAD_GATEWAY,

  // The gateway's, the only text it lets out (`gateway/src/failure.ts`): provider prose can
  // echo credentials, so its message is its code and nothing else.
  invalid_request: Object.freeze({ status: 400 }),
  invalid_binding: Object.freeze({ status: 400 }),
  invalid_source: Object.freeze({ status: 400 }),
  not_found: Object.freeze({ status: 404 }),
  credential_refused: REFUSED,
  endpoint_refused: REFUSED,
  unmetered: REFUSED,
  // A sandbox call the reservation cannot bound (G4): several choices, or a server-side
  // tool the provider bills beyond tokens.
  egress_multiple_choices: Object.freeze({ status: 400 }),
  egress_unmetered_tool: REFUSED,
  // An option priced beyond tokens that pi-ai does not price.
  egress_unpriced_option: REFUSED,
  // The metered path's (`forward.ts`): a key the rules read spelled another way (`aliased`);
  // the owner's object refused the call's reservation (the egress door answers the object's
  // own code and receipt in its place); a call that alone weighs more than the pool
  // (`held.ts`).
  egress_aliased_key: Object.freeze({ status: 400 }),
  egress_reserve_refused: REFUSED,
  egress_too_large: Object.freeze({ status: 413 }),
  // A pool full past its wait, the gateway's (`held.ts`) or an isolate's (`slots.ts`):
  // told, retryable after `Retry-After`, never a loss.
  busy: Object.freeze({ status: 503, retryAfterS: 1, transient: true }),
  unknown_model: Object.freeze({ status: 404 }),
  unsupported_source: Object.freeze({ status: 422 }),
  not_refreshable: Object.freeze({ status: 409 }),
  deadline: Object.freeze({ status: 504 }),
  // The gateway's and the files plane's: a keyring that does not open.
  keyring_unavailable: Object.freeze({ status: 500 }),
  // A bug in the gateway: what was thrown is never shown, only that it was.
  internal: Object.freeze({ status: 500 }),
  // Upstream outcomes: the OAuth and verify calls throw them, and egress answers them.
  upstream_unauthorized: BAD_GATEWAY,
  upstream_rate_limited: BAD_GATEWAY,
  upstream_timeout: BAD_GATEWAY,
  upstream_rejected: BAD_GATEWAY,
  upstream_overloaded: BAD_GATEWAY,
  upstream_server_error: BAD_GATEWAY,
  upstream_unreachable: BAD_GATEWAY,
  upstream_error: BAD_GATEWAY,
  aborted: Object.freeze({ status: 499 }),
  oauth_invalid_grant: BAD_GATEWAY,
  oauth_rejected: BAD_GATEWAY,
  oauth_unavailable: BAD_GATEWAY,
  oauth_unreachable: BAD_GATEWAY,
  oauth_malformed: BAD_GATEWAY,
} satisfies Readonly<Record<string, Entry>>);

export type RefusalCode = keyof typeof REFUSALS;

/** The gateway's codes: the only ones it lets out of a call (`gateway/src/failure.ts`). */
export const GATEWAY_CODES = Object.freeze([
  "invalid_request", "invalid_binding", "invalid_source", "not_found", "credential_refused",
  "endpoint_refused", "unmetered", "egress_multiple_choices", "egress_unmetered_tool",
  "egress_unpriced_option", "egress_aliased_key", "egress_reserve_refused", "egress_too_large",
  "busy", "unknown_model", "unsupported_source", "not_refreshable", "deadline",
  "keyring_unavailable", "internal", "upstream_unauthorized", "upstream_rate_limited",
  "upstream_timeout", "upstream_rejected", "upstream_overloaded", "upstream_server_error",
  "upstream_unreachable", "upstream_error", "aborted", "oauth_invalid_grant",
  "oauth_rejected", "oauth_unavailable", "oauth_unreachable", "oauth_malformed",
] as const satisfies readonly RefusalCode[]);
export type GatewayCode = (typeof GATEWAY_CODES)[number];

/** Whether `code` is one of the gateway's codes. */
export function isGatewayCode(code: unknown): code is GatewayCode {
  return (GATEWAY_CODES as readonly unknown[]).includes(code);
}

/**
 * Every code the egress door refuses with itself (`doors/src/egress.ts`), its status from
 * `REFUSALS`: the object's, each with its receipt, then the door's own and its closed gate.
 * A refusal the object throws keeps its own code (see the header).
 */
export const EGRESS_CODES = Object.freeze([
  "egress_token_missing", "egress_token_unknown", "egress_token_expired",
  "egress_lease_closed", "egress_run_closed", "egress_run_connection", "egress_policy_stale",
  "egress_network_stale", "egress_connection_refused", "egress_endpoint_refused",
  "egress_unmetered", "egress_length_required", "egress_rate", "egress_spend_refused",
  "egress_unknown_model", "egress_unpriced_option", "egress_too_large",
  "runner_token_missing", "run_secret_missing", "sync_token_missing", "sync_token_unknown",
  "sync_token_expired", "not_found", "method_not_allowed", "events_too_large",
  "egress_gateway_failed", "egress_deadline", "egress_aborted", "egress_ended", "unavailable",
] as const satisfies readonly RefusalCode[]);
export type EgressCode = (typeof EGRESS_CODES)[number];

/** What a refusal carries for its caller to act on: plain JSON, never prose. */
export type Facts = { readonly [key: string]: unknown };

export class Refusal extends Error {
  override readonly name = "Refusal";
  readonly code: RefusalCode;
  readonly retryAfterS?: number;
  readonly facts?: Facts;

  /** `message` is for people, and is the code itself when none is given. */
  constructor(code: RefusalCode, message: string = code,
              more: { readonly retryAfterS?: number; readonly facts?: Facts } = {}) {
    super(message);
    this.code = code;
    if (more.retryAfterS !== undefined) {
      this.retryAfterS = more.retryAfterS;
    }
    if (more.facts !== undefined) {
      this.facts = more.facts;
    }
  }

  /** The status its code is answered with. */
  get status(): number {
    return REFUSALS[this.code].status;
  }

  /** The `Retry-After` it is answered with, in seconds, if any. */
  get retryAfter(): number | undefined {
    return this.retryAfterS ?? (REFUSALS[this.code] as Entry).retryAfterS;
  }

  /** Whether the request itself was malformed, the `invalid` family's (`400`). */
  get invalid(): boolean {
    return this.status === 400;
  }

  /** Whether a protocol that shows a code shows this one, rather than its message. */
  get named(): boolean {
    return (REFUSALS[this.code] as Entry).quiet !== true;
  }

  /** Whether it is for the moment only: the same request may succeed later. */
  get transient(): boolean {
    return (REFUSALS[this.code] as Entry).transient === true;
  }
}

function plainObject(value: unknown): value is Facts {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The refusal `error` is, here or as it crossed RPC; null for anything else, which is a
 * bug. The one test of a refusal against a bug: a refusal of an unknown code (another
 * version's) is a bug too, loudly.
 */
export function refusalOf(error: unknown): Refusal | null {
  if (error instanceof Refusal) {
    return error;
  }
  if (!(error instanceof Error) || error.name !== "Refusal") {
    return null;
  }
  const { code, retryAfterS, facts } = error as Error & Record<string, unknown>;
  if (typeof code !== "string" || !Object.hasOwn(REFUSALS, code)
      || (retryAfterS !== undefined
          && (typeof retryAfterS !== "number" || !Number.isSafeInteger(retryAfterS)
              || retryAfterS < 0))
      || (facts !== undefined && !plainObject(facts))) {
    return null;
  }
  return new Refusal(code as RefusalCode, error.message, {
    ...(retryAfterS === undefined ? {} : { retryAfterS: retryAfterS as number }),
    ...(facts === undefined ? {} : { facts }) });
}

/**
 * What a POSIX call would fail with: the path is missing, or of the wrong kind. Not a
 * refusal: the file doors answer it as their own protocol does (the sync agent's, `404`
 * with its code). Its message is its code, then words for people.
 */
export const FAULT_CODES = Object.freeze(["not_found", "is_directory", "not_directory",
                                          "exists", "not_empty"] as const);
export type FaultCode = (typeof FAULT_CODES)[number];

export class FileFault extends Error {
  override readonly name = "FileFault";
  readonly code: FaultCode;

  constructor(code: FaultCode, message: string) {
    super(`${code}: ${message}`);
    this.code = code;
  }
}

/** The fault `error` is, here or as it crossed RPC (its own `code`), or null. */
export function faultOf(error: unknown): FaultCode | null {
  if (!(error instanceof Error) || error.name !== "FileFault") {
    return null;
  }
  const code = (error as Error & { code?: unknown }).code;
  return typeof code === "string" && (FAULT_CODES as readonly string[]).includes(code)
    ? code as FaultCode : null;
}

/** What every door and lane answers while the owner is erased (`owner/src/erasure.ts`). */
export const ERASING = "this owner is being erased";
