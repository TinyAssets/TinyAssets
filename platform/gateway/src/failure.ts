/**
 * Every refusal and failure the gateway reports, by a fixed code only.
 *
 * The rule is the Python's (`tinyassets/broker/model_connect.py:110-112`): provider prose
 * can echo credentials, so only our fixed code crosses the boundary. No error the gateway
 * returns, throws or streams carries a secret, an upstream body, an upstream message, or
 * the text of an exception it caught. An RPC caller sees the code as the error's message;
 * an HTTP caller sees it as `{error: {code, message}}` with the status below.
 */

/** Each code and the HTTP status it is answered with before any stream starts. */
const STATUS = Object.freeze({
  invalid_request: 400,
  invalid_binding: 400,
  invalid_source: 400,
  not_found: 404,
  credential_refused: 403,
  endpoint_refused: 403,
  unmetered: 403,
  // A sandbox call the reservation cannot bound (G4): several choices, or a server-side
  // tool the provider bills beyond tokens.
  egress_multiple_choices: 400,
  egress_unmetered_tool: 403,
  // An option priced beyond tokens that pi-ai does not price.
  egress_unpriced_option: 403,
  // The metered path's (`forward.ts`): a key the rules read spelled another way (`aliased`);
  // the owner's object refused the call's reservation (the egress door answers the object's
  // own code and receipt in its place); a call that alone weighs more than the pool
  // (`held.ts`); the pool full past its wait (`held.ts`: told, retryable after
  // `Retry-After`, never a loss).
  egress_aliased_key: 400,
  egress_reserve_refused: 403,
  egress_too_large: 413,
  busy: 503,
  unknown_model: 404,
  unsupported_source: 422,
  not_refreshable: 409,
  deadline: 504,
  keyring_unavailable: 500,
  // A bug in the gateway: what was thrown is never shown, only that it was.
  internal: 500,
  // Upstream outcomes: the OAuth and verify calls throw them, and egress answers them.
  upstream_unauthorized: 502,
  upstream_rate_limited: 502,
  upstream_timeout: 502,
  upstream_rejected: 502,
  upstream_overloaded: 502,
  upstream_server_error: 502,
  upstream_unreachable: 502,
  upstream_error: 502,
  aborted: 499,
  oauth_invalid_grant: 502,
  oauth_rejected: 502,
  oauth_unavailable: 502,
  oauth_unreachable: 502,
  oauth_malformed: 502,
});

export type Code = keyof typeof STATUS;

/** A refusal or failure, named by its fixed code; its message is the code and nothing else. */
export class Failure extends Error {
  override readonly name = "Failure";
  readonly code: Code;

  constructor(code: Code) {
    super(code);
    this.code = code;
  }
}

/** The code of a failure, or `fallback` for anything else (whose text is never shown). */
export function codeOf(error: unknown, fallback: Code): Code {
  return error instanceof Failure ? error.code : fallback;
}

/** Runs an RPC method, letting only a fixed code out of it. */
export async function guarded<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw new Failure(codeOf(error, "internal"));
  }
}

/**
 * The HTTP answer for a code: `{error: {code, message}}`, and nothing from upstream; `busy`
 * with its `Retry-After`.
 */
export function failureResponse(code: Code, extra?: Readonly<Record<string, string>>): Response {
  return Response.json({ error: { code, message: code } },
                       { status: STATUS[code],
                         headers: { ...extra, "cache-control": "no-store",
                                    ...(code === "busy" ? { "retry-after": "1" } : {}) } });
}

/**
 * The code for an upstream HTTP status. 401 and 403 mean the credential, 408 a request
 * the provider timed out, 429 its limit, any other 4xx a request it refused; 503 and 529
 * a provider overloaded, any other 5xx its own failure.
 */
export function upstreamCode(status: number): Code {
  if (status === 401 || status === 403) {
    return "upstream_unauthorized";
  }
  if (status === 408) {
    return "upstream_timeout";
  }
  if (status === 429) {
    return "upstream_rate_limited";
  }
  if (status >= 400 && status < 500) {
    return "upstream_rejected";
  }
  if (status === 503 || status === 529) {
    return "upstream_overloaded";
  }
  if (status >= 500) {
    return "upstream_server_error";
  }
  return "upstream_error";
}
