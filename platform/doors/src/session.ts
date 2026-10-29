/**
 * The owner's session cookie at the doors (research §8; checklist 6 and 7), and a model
 * connect's browser binding (below). The only source that reads a request's `Cookie`
 * header (the binder guard).
 *
 * The cookie is `__Host-ta-owner`: Secure, HttpOnly, Path=/, SameSite=Strict, eight
 * hours. It carries no owner and the doors never look it up: the object minted it and
 * checks its own row (`sessions.ts`), and the owner comes from a separate verified
 * credential. It is forwarded as the binding's `session` only when every rule below
 * holds, so a cross-site page can never make the owner's browser widen anything:
 * - the path is the owner surface, `/app/api/*`;
 * - the method is not GET, HEAD or OPTIONS;
 * - the media type is exactly `application/json`;
 * - `Origin` is byte-equal to the one public origin;
 * - `Sec-Fetch-Site`, when present, is `same-origin`;
 * - the cookie appears once, 32 to 512 characters.
 * Otherwise the session is left unset, and the object refuses any widening as
 * `interactive owner session required`. On `/mcp`, `/api` and `/hooks` the `Cookie`
 * header is removed before anything else sees the request.
 *
 * Strict, not the Python's Lax: the cookie only ever needs to ride our own page's
 * same-origin POSTs, and a Strict cookie set on the sign-in callback's response is kept
 * though that navigation came from the identity provider.
 */
import { PUBLIC_ORIGIN } from "./config";
import { codePoints } from "../../owner/src/text";

export const COOKIE = "__Host-ta-owner";
/** The Python's 28 800 s, the object's session life (`SESSION_LIMITS.ttlS`). */
export const COOKIE_MAX_AGE_S = 28_800;
export const OWNER_SURFACE = "/app/api/";
const SAFE_METHODS: readonly string[] = Object.freeze(["GET", "HEAD", "OPTIONS"]);

/** The media type of `Content-Type`, lowercased, without parameters; "" when absent. */
export function mediaType(request: Request): string {
  return (request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase();
}

/** The request without its `Cookie` header: what `/mcp`, `/api` and `/hooks` see. */
export function withoutCookie(request: Request): Request {
  if (!request.headers.has("Cookie")) {
    return request;
  }
  const headers = new Headers(request.headers);
  headers.delete("Cookie");
  return new Request(request, { headers });
}

/** The session cookie's one value, or undefined: absent, repeated or out of bounds. */
function cookieValue(request: Request): string | undefined {
  const values = (request.headers.get("Cookie") ?? "").split(";")
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${COOKIE}=`))
    .map((part) => part.slice(COOKIE.length + 1));
  if (values.length !== 1) {
    return undefined;
  }
  const value = values[0]!;
  return codePoints(value) >= 32 && codePoints(value) <= 512 ? value : undefined;
}

/** The browser's session cookie for `binding.session`, only when every rule holds. */
export function sessionOf(request: Request): string | undefined {
  const url = new URL(request.url);
  const site = request.headers.get("Sec-Fetch-Site");
  if (!url.pathname.startsWith(OWNER_SURFACE)
      || SAFE_METHODS.includes(request.method.toUpperCase())
      || mediaType(request) !== "application/json"
      || request.headers.get("Origin") !== PUBLIC_ORIGIN
      || (site !== null && site !== "same-origin")) {
    return undefined;
  }
  return cookieValue(request);
}

/** The cookie a sign-in sets, or renews with the session's life. */
export function sessionCookie(value: string): string {
  return `${COOKIE}=${value}; Secure; HttpOnly; Path=/; SameSite=Strict; ` +
    `Max-Age=${COOKIE_MAX_AGE_S}`;
}

/** The cookie a sign-out sets. */
export function clearedCookie(): string {
  return `${COOKIE}=; Secure; HttpOnly; Path=/; SameSite=Strict; Max-Age=0`;
}

/**
 * A model source's OAuth flow, bound to the browser that began it, as the OAuth
 * provider's `beginUpstream` binds its own (gateway research §7.2): one `__Host-` cookie
 * per flow, named by its digest's first 16 hex and holding the whole digest of the
 * flow's state, so flows in two tabs never replace each other. Lax, not Strict: it must
 * ride the provider's top-level redirect back to the callback, a cross-site navigation.
 * It vouches for nothing but the browser; the session the flow was begun in is checked by
 * the object (`flowFinish`).
 */
export const CONNECT_COOKIE = "__Host-ta-connect";

async function flowDigest(state: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256",
                                           new TextEncoder().encode(`tinyassets-connect\0${state}`));
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function connectName(digest: string): string {
  return `${CONNECT_COOKIE}-${digest.slice(0, 16)}`;
}

/** The binding cookie a flow's begin sets, for `maxAge` seconds. */
export async function connectCookie(state: string, maxAge: number): Promise<string> {
  const digest = await flowDigest(state);
  return `${connectName(digest)}=${digest}; Secure; HttpOnly; Path=/; SameSite=Lax; ` +
    `Max-Age=${maxAge}`;
}

/** The cookie a flow's finish sets: its binding cleared. */
export async function clearedConnectCookie(state: string): Promise<string> {
  return `${connectName(await flowDigest(state))}=; Secure; HttpOnly; Path=/; SameSite=Lax; ` +
    "Max-Age=0";
}

/** Whether this browser began the flow for `state`: its one binding cookie matches. */
export async function connectBound(request: Request, state: string): Promise<boolean> {
  const digest = await flowDigest(state);
  const name = connectName(digest);
  const values = (request.headers.get("Cookie") ?? "").split(";")
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${name}=`))
    .map((part) => part.slice(name.length + 1));
  if (values.length !== 1 || values[0]!.length !== digest.length) {
    return false;
  }
  const encoder = new TextEncoder();
  return crypto.subtle.timingSafeEqual(encoder.encode(values[0]!), encoder.encode(digest));
}
