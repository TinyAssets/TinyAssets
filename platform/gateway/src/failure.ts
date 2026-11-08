/**
 * Every refusal and failure the gateway reports, by a fixed code only.
 *
 * The rule is the Python's (`tinyassets/broker/model_connect.py:110-112`): provider prose
 * can echo credentials, so only our fixed code crosses the boundary. No error the gateway
 * returns, throws or streams carries a secret, an upstream body, an upstream message, or
 * the text of an exception it caught. An RPC caller gets a refusal (`contract/src/refusal.ts`)
 * whose code is one of `GATEWAY_CODES` and whose message is that code; an HTTP caller sees
 * it as `{error: {code, message}}` with the code's status.
 */
import {
  type GatewayCode, isGatewayCode, Refusal, REFUSALS, refusalOf,
} from "../../contract/src/refusal";

/** The gateway code of a refusal, or `fallback` for anything else (whose text is never shown). */
export function codeOf(error: unknown, fallback: GatewayCode): GatewayCode {
  const code = refusalOf(error)?.code;
  return isGatewayCode(code) ? code : fallback;
}

/** Runs an RPC method, letting only a fixed code out of it. */
export async function guarded<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw new Refusal(codeOf(error, "internal"));
  }
}

/**
 * The HTTP answer for a code: `{error: {code, message}}`, and nothing from upstream, with
 * its code's status and `Retry-After`.
 */
export function failureResponse(code: GatewayCode, extra?: Readonly<Record<string, string>>):
    Response {
  const retry = new Refusal(code).retryAfter;
  return Response.json({ error: { code, message: code } },
                       { status: REFUSALS[code].status,
                         headers: { ...extra, "cache-control": "no-store",
                                    ...(retry === undefined ? {}
                                      : { "retry-after": String(retry) }) } });
}

/**
 * The code for an upstream HTTP status. 401 and 403 mean the credential, 408 a request
 * the provider timed out, 429 its limit, any other 4xx a request it refused; 503 and 529
 * a provider overloaded, any other 5xx its own failure.
 */
export function upstreamCode(status: number): GatewayCode {
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
