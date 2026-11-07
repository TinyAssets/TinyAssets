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
 * shape (`contract/guards/refusal.test.ts` holds the compatibility settings; owner's
 * `refusal-rpc.test.ts` crosses a real boundary).
 *
 * Most codes are named by the message too, as its prefix (`storage_busy: ...`): the doors
 * show that code where a protocol carries one (the egress door, the inbound hooks), and the
 * object keeps it as an undeliverable mail's reason. A `quiet` code is one a message never
 * names (the two families' own, `refused` and `invalid`, and a few the object tells apart
 * inside): where a code is shown, a quiet refusal shows its message, or the egress door's
 * `egress_refused`, as it always has.
 */

interface Entry {
  readonly status: number;
  /** The `Retry-After` a refusal of this code carries when it names none of its own. */
  readonly retryAfterS?: number;
  /** A code its message never names (see the header). */
  readonly quiet?: true;
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

  // The owner's object, named in its messages.
  agent_changed: REFUSED,
  agent_disabled: REFUSED,
  agent_retired: REFUSED,
  agent_undefined: REFUSED,
  archive_changed: REFUSED,
  archive_full: REFUSED,
  binding_not_configured: REFUSED,
  binding_withdrawn: REFUSED,
  code_mode_off: REFUSED,
  conflict: REFUSED,
  conversation_busy: REFUSED,
  credential_refresh_failed: REFUSED,
  egress_ip_literal: REFUSED,
  // The runner's posts: the object's held posts full, or another batch of the run landing
  // between a post's units; the runner posts again after `Retry-After`.
  events_busy: Object.freeze({ status: 503, retryAfterS: 1 }),
  events_raced: Object.freeze({ status: 503, retryAfterS: 1 }),
  // Another writer changed the file under each try.
  file_busy: Object.freeze({ status: 409 }),
  file_quota: REFUSED,
  file_share: REFUSED,
  inbound_proof_invalid: REFUSED,
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
  sandbox_absent: REFUSED,
  signin_lapsed: REFUSED,
  signin_live: REFUSED,
  signin_off: REFUSED,
  sockets_full: REFUSED,
  // The hot ceiling while the archive lane moves acked mail out: about one lane pass.
  storage_busy: Object.freeze({ status: 503, retryAfterS: STORAGE_BUSY_RETRY_S }),
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

  // The owner's object, never named in a message: the object tells these apart inside.
  connection_unavailable: QUIET_REFUSED,
  credential_expired: QUIET_REFUSED,
  chunk_deleting: QUIET_REFUSED,
  erasing: QUIET_REFUSED,
  message_body: QUIET_INVALID,
  post_conflict: QUIET_REFUSED,
  // The files plane: a wrapped data key that does not open for its namespace.
  data_key_refused: QUIET_INVALID,

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
  egress_rate: Object.freeze({ status: 429 }),
  egress_spend_refused: Object.freeze({ status: 402 }),
  egress_unknown_model: Object.freeze({ status: 404 }),

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
  busy: Object.freeze({ status: 503, retryAfterS: 1 }),
  unknown_model: Object.freeze({ status: 404 }),
  unsupported_source: Object.freeze({ status: 422 }),
  not_refreshable: Object.freeze({ status: 409 }),
  deadline: Object.freeze({ status: 504 }),
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

/** The egress door's codes, each answered with the object's receipt (`doors/src/egress.ts`). */
export const EGRESS_CODES = Object.freeze([
  "egress_token_missing", "egress_token_unknown", "egress_token_expired",
  "egress_lease_closed", "egress_run_closed", "egress_run_connection", "egress_policy_stale",
  "egress_network_stale", "egress_connection_refused", "egress_endpoint_refused",
  "egress_unmetered", "egress_length_required", "egress_rate", "egress_spend_refused",
  "egress_unknown_model", "egress_unpriced_option", "egress_too_large",
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

  /** Whether a protocol that shows a code shows this one: its message names it. */
  get named(): boolean {
    return (REFUSALS[this.code] as Entry).quiet !== true;
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
