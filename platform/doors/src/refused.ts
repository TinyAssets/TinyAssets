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
import {
  type CallToolResult, ProtocolError, ProtocolErrorCode,
} from "@modelcontextprotocol/server";

import { keyed, whole } from "../../contract/src/checks";
import { taken } from "../../contract/src/handed";
import { ERASED_SIGN_IN } from "../../contract/src/signin";
import {
  callAgain, type FaultCode, type Refusal, refusalOf, told,
} from "../../contract/src/refusal";

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

/** The `Retry-After` header a refusal is answered with, if it is for the moment (`retryAfter`). */
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

/** MCP Events' own error codes: the design sketch's (`-32023` to `-32026`). */
export const EVENT_ERRORS = Object.freeze({
  notFound: -32023, forbidden: -32024, exhausted: -32025, unsupported: -32026,
});

/**
 * An MCP tool's: the tool's error result, its text the refusal's message, then whether to make
 * the call again, and when (`callAgain`), read as every other MCP request's error reads it
 * (`told`). A tool call's failure is a result the model reads, never a JSON-RPC error (MCP
 * 2026-07-28 `server/tools.mdx` "Error Handling", at `862397cec702`), and the pinned SDK
 * (`@modelcontextprotocol/server` 2.4.0) answers anything a tool throws as one.
 */
export const asTool: Surface<CallToolResult> = (refusal) =>
  ({ isError: true,
     content: [{ type: "text", text: `${refusal.message}\n${callAgain(refusal)}` }] });

/**
 * Every MCP request's but a tool call's (the definition's resources, prompt and skills,
 * `definition.ts`; MCP Events' but a terminal refusal, `asEvent`): its JSON-RPC error, thrown,
 * which the SDK answers: invalid params for an invalid request, else an internal error, the
 * refusal's reason in its `data`, and its wait when it is for the moment.
 * MCP names no code for a refusal for the moment, and its codes `-32000` to `-32019` mean
 * nothing across implementations (its 2026-07-28 "Error Codes", at `862397cec702`, read
 * 2026-11-22), so `-32603` with `retryAfterMs` is how a client is told to try again.
 */
export const asRequest: Surface<never> = (refusal) => {
  const said = told(refusal);
  throw new ProtocolError(
    said.kind === "invalid" ? ProtocolErrorCode.InvalidParams : ProtocolErrorCode.InternalError,
    refusal.message,
    { reason: refusal.message,
      ...(said.kind === "temporary" ? { retryAfterMs: said.waitS * 1000 } : {}) },
  );
};

/**
 * MCP Events': an invalid request or a refusal for the moment is any MCP request's
 * (`asRequest`), as the design sketch answers them (`InvalidParams`, and its "Transient
 * per-occurrence error", `-32603` with `data.reason`). Any other refusal is the sketch's own
 * error: a full agent's `ResourceExhausted` with the owner's limit, `{limit, max}`, from its
 * refusal's facts (a `subscription_limit` refusal without them is a bug, thrown, never an
 * answer with no limit), and the rest `Forbidden`, which a client stops on (its open question
 * 6). The sketch at `c77b84395eff`, read 2026-11-21, still its `HEAD` on 2026-11-22.
 */
export const asEvent: Surface<never> = (refusal) => {
  if (told(refusal).kind !== "final") {
    return asRequest(refusal);
  }
  if (refusal.code === "subscription_limit") {
    const facts = refusal.facts;
    if (!keyed(facts, ...taken("subscription_limit")) || typeof facts["limit"] !== "string"
        || !whole(facts["max"], 0)) {
      throw new Error("a subscription_limit refusal carries no {limit, max}");
    }
    throw new ProtocolError(EVENT_ERRORS.exhausted, "ResourceExhausted", facts);
  }
  throw new ProtocolError(EVENT_ERRORS.forbidden, "Forbidden", { reason: refusal.message });
};

/**
 * The owner's sign-in's, back to the client in OAuth's own words, its reason in
 * `error_description` for the page that shows it (`doors/app/signed-in.js`): a refusal for
 * the moment (a closed gate, a full pool) is `temporarily_unavailable` with its message, the
 * canary gate's (`CANARY_ONLY`) among them; an erased owner's is `access_denied`
 * (`ERASED_SIGN_IN`). Any other refusal at a sign-in is a bug, thrown.
 */
export function asRedirect(original: AuthRequest, headers: Headers): Surface<Response> {
  return (refusal) => {
    if (refusal.transient) {
      return redirect(authorizationErrorRedirect(original, "temporarily_unavailable",
                                                 refusal.message), headers);
    }
    if (refusal.code === "erasing" || refusal.code === "owner_erased") {
      return redirect(authorizationErrorRedirect(original, "access_denied", ERASED_SIGN_IN),
                      headers);
    }
    throw refusal;
  };
}
