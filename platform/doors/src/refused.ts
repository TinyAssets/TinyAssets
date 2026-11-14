/**
 * The doors' one answer for a refusal (`contract/src/refusal.ts`), on every surface that
 * answers one as JSON: `{error: <its message>}`, its code's status, and its `Retry-After`
 * when it has one. The protocols that say a refusal in their own shape (MCP's tool result,
 * the egress door's `{error: {code, message}}`, the inbound hooks', the events door's
 * design-sketch errors) take its fields from the same refusal.
 */
import type { Refusal } from "../../contract/src/refusal";

import { failure } from "./kit";

/** A refusal's answer. */
export function refusalResponse(refusal: Refusal): Response {
  const seconds = refusal.retryAfter;
  return failure(refusal.status, refusal.message,
                 seconds === undefined ? {} : { "Retry-After": String(seconds) });
}

/**
 * The code a protocol that shows one shows: the refusal's own, when its message names it;
 * else its message (a quiet refusal's words, which were always what such a protocol said).
 */
export function shownCode(refusal: Refusal): string {
  return refusal.named ? refusal.code : refusal.message;
}
