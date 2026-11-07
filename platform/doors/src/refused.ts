/**
 * The doors' one answer for a refusal (`contract/src/refusal.ts`), on every surface that
 * answers one as JSON: `{error: <its message>}`, its code's status, and its `Retry-After`
 * when it has one. The protocols that say a refusal in their own shape (MCP's tool result,
 * the egress door's `{error: {code, message}}`, the inbound hooks', the events door's
 * design-sketch errors) take its fields from the same refusal.
 */
import type { Refusal } from "../../contract/src/refusal";

/** A refusal's `Retry-After`, as a header, when it has one. */
export function retryAfter(refusal: Refusal): Record<string, string> {
  const seconds = refusal.retryAfter;
  return seconds === undefined ? {} : { "Retry-After": String(seconds) };
}

/** A refusal's answer, with `headers` (a surface's own, such as its `Cache-Control`). */
export function refusalResponse(refusal: Refusal, headers: Record<string, string> = {}):
    Response {
  return Response.json({ error: refusal.message },
                       { status: refusal.status, headers: { ...headers, ...retryAfter(refusal) } });
}

/**
 * The code a protocol that shows one shows: the refusal's own, when its message names it;
 * else its message (a quiet refusal's words, which were always what such a protocol said).
 */
export function shownCode(refusal: Refusal): string {
  return refusal.named ? refusal.code : refusal.message;
}
