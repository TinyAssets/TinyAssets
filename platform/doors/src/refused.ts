/**
 * The doors' answers for a refusal (`contract/src/refusal.ts`), one per surface, each read
 * from the refusal alone: its code's status, the `Retry-After` it names or its code does, and
 * its message. No surface picks a status or a wait of its own, and none reads a code from a
 * message. A door runs its work through `answered`, so a refusal thrown anywhere in it, a
 * closed gate's (`control.ts`) among them, is answered as that door's protocol says it.
 *
 * A quiet code (`refused`, `invalid` and the few the object tells apart inside) is one a
 * protocol that shows a code does not show: the inbound hooks show its message in its place,
 * the egress door `egress_refused`.
 */
import {
  type AuthRequest, authorizationErrorRedirect,
} from "@cloudflare/workers-oauth-provider";
import { type CallToolResult, ProtocolError } from "@modelcontextprotocol/server";

import { SUBSCRIPTIONS_PER_AGENT } from "../../contract/src/limits";
import { type FaultCode, type Refusal, refusalOf } from "../../contract/src/refusal";

import { answer, failure, redirect } from "./kit";

/** How one surface answers a refusal. */
export type Surface<A> = (refusal: Refusal) => A;

/** `work`'s answer, or the refusal it threw as `surface` answers it; anything else is a bug. */
export async function answered<A>(surface: Surface<A>, work: () => Promise<A>): Promise<A> {
  try {
    return await work();
  } catch (error) {
    const refusal = refusalOf(error);
    if (refusal === null) {
      throw error;
    }
    return surface(refusal);
  }
}

/** The `Retry-After` header a refusal is answered with, if it names a wait or its code does. */
export function retryAfter(refusal: Refusal): Record<string, string> {
  const seconds = refusal.retryAfter;
  return seconds === undefined ? {} : { "Retry-After": String(seconds) };
}

/** The code a protocol that shows one shows: the refusal's own, or a quiet one's message. */
function shownCode(refusal: Refusal): string {
  return refusal.named ? refusal.code : refusal.message;
}

/** Every JSON surface's (`/api`, `/app/api`, `/mcp`'s HTTP answers): `{error: <message>}`. */
export const asJson: Surface<Response> = (refusal) =>
  failure(refusal.status, refusal.message, retryAfter(refusal));

/**
 * The egress door's, in the gateway's own error shape, so a provider SDK shows its code:
 * `{error: {code, message}}`, and the object's receipt when it kept one.
 */
export function asEgress(refusal: Refusal, receipt: string | null = null): Response {
  return answer(refusal.status,
                { error: { code: refusal.named ? refusal.code : "egress_refused",
                           message: refusal.message },
                  ...(receipt === null ? {} : { receipt }) },
                retryAfter(refusal));
}

/** A path the sandbox's sync agent may not take, as its protocol says it: `404`, by its code. */
export function asEgressFault(code: FaultCode): Response {
  return answer(404, { error: { code, message: code } });
}

/** An MCP tool's: the tool's error result, its text the refusal's message. */
export const asTool: Surface<CallToolResult> = (refusal) =>
  ({ isError: true, content: [{ type: "text", text: refusal.message }] });

/** The inbound hooks', to a capability URL's sender: `{error: <shown code>}`. */
export const asInbound: Surface<Response> = (refusal) =>
  answer(refusal.status, { error: shownCode(refusal) }, retryAfter(refusal));

/**
 * The inbound hooks', to a shared app: `200`, so its failure budget with its vendor is kept,
 * unless the refusal is for the moment only (a full pool, a closed gate): then its status and
 * wait, so the vendor sends the event again rather than losing it.
 */
export const asShared: Surface<Response> = (refusal) => {
  const body = { state: "refused", error: shownCode(refusal) };
  return refusal.transient ? answer(refusal.status, body, retryAfter(refusal))
    : answer(200, body);
};

/** MCP Events' error codes: the design sketch's (`-32023` to `-32026`), and JSON-RPC's. */
export const EVENT_ERRORS = Object.freeze({
  notFound: -32023, forbidden: -32024, exhausted: -32025, unsupported: -32026,
  invalidParams: -32602,
});

/** MCP Events': the design sketch's error, thrown, which the SDK answers. */
export const asEvent: Surface<never> = (refusal) => {
  if (refusal.code === "subscription_limit") {
    throw new ProtocolError(EVENT_ERRORS.exhausted, "ResourceExhausted",
                            { limit: "subscriptions", max: SUBSCRIPTIONS_PER_AGENT });
  }
  if (refusal.invalid) {
    throw new ProtocolError(EVENT_ERRORS.invalidParams, refusal.message);
  }
  throw new ProtocolError(EVENT_ERRORS.forbidden, "Forbidden", { reason: refusal.message });
};

/**
 * The owner's sign-in's, back to the client in OAuth's own words: a refusal for the moment
 * (a closed gate, a full pool) is `temporarily_unavailable`; an erased owner's is
 * `access_denied`. Any other refusal at a sign-in is a bug, thrown.
 */
export function asRedirect(original: AuthRequest, headers: Headers): Surface<Response> {
  return (refusal) => {
    if (refusal.transient) {
      return redirect(authorizationErrorRedirect(original, "temporarily_unavailable"), headers);
    }
    if (refusal.code === "erasing" || refusal.code === "owner_erased") {
      return redirect(authorizationErrorRedirect(original, "access_denied",
                                                 "this account was erased"), headers);
    }
    throw refusal;
  };
}
