/**
 * What an unauthenticated caller can make the platform do, bounded (tasks 2, rate limits;
 * slice L, after its review). The founder's rule: no invented limits. So no authenticated
 * caller is ever counted: the tier's simultaneous runs, the owner's own settings and
 * vendors' limits bound a signed-in owner, an MCP client and an API key. Only work an
 * unauthenticated request makes the platform do, and only work that costs something, is
 * counted; a cheap refusal (a non-navigation, a malformed request) never is. Each number
 * and its derivation is in `limits-audit.md` ("Security"):
 *
 * - **Sign-in starts** (`SIGNIN_LIMIT`): an `/authorize` that will write the provider's
 *   consent or upstream state, a consent decision, and a callback that recovered its state
 *   and will exchange a code. Each writes Workers KV.
 * - **Metadata fetches** (`CIMD_LIMIT`): an `/authorize` naming a `client_id` that is a
 *   Client ID Metadata Document URL the provider's cache does not hold, which makes the
 *   provider fetch it. At the token and revoke endpoints, where Claude's and ChatGPT's
 *   shared backend IPs refresh, such a request is counted only when it fails, and refused
 *   before its fetch once its address is past the bound.
 * - **Failures** (`FAIL_LIMIT`): a bearer the doors cannot validate (`/mcp`, `/api`,
 *   `/app/api`), a token-endpoint failure (counted per address and client), and an inbound
 *   hook refused before or at its check (an unknown route, a bad signature or proof). Each
 *   costs Workers KV reads. A failure is counted after it happens; an address past the bound
 *   is refused before any work, by a mark the counter leaves (`blocked`).
 *
 * The client id is found as the OAuth provider finds it (`clientIdOf`): an HTTP Basic
 * header first, then a `client_assertion`'s issuer, then the form's `client_id`; and a
 * metadata URL is recognised by the provider's own test (`metadataUrl`).
 *
 * Keys are the caller's address, an IPv6 one by its /64 (`callerKey`): one holder gets one
 * /64. Cloudflare's binding is approximate and per location, by its own documentation.
 */
import type { DoorsEnv, RateLimit } from "./config";

/** The OAuth provider's CIMD cache (`@cloudflare/workers-oauth-provider` 1.2.4, pinned). */
export const CIMD_CACHE = "workers-oauth-provider:cimd:v1";
/** The doors' own record of addresses past a failure bound: a minute, the bindings' period. */
export const BLOCKED_CACHE = "tinyassets-throttle:v1";
const BLOCKED_S = 60;
/** A token or revoke request's form, read before the provider reads it: a few fields. */
export const OAUTH_FORM_MAX = 16 * 1024;
/** The bound on a client assertion the provider reads its issuer from (its own figure). */
const ASSERTION_MAX = 16 * 1024;

/** A counted kind of unauthenticated work, and its binding. */
export type Counted = "signin" | "cimd" | "fail";

function binding(env: DoorsEnv, kind: Counted): RateLimit {
  return kind === "signin" ? env.SIGNIN_LIMIT : kind === "cimd" ? env.CIMD_LIMIT
    : env.FAIL_LIMIT;
}

/** An IPv6 address's first four groups, its /64; null when it is not one. */
function slash64(address: string): string | null {
  if (!address.includes(":")) {
    return null;
  }
  const [head = "", tail] = address.split("::");
  const left = head === "" ? [] : head.split(":");
  const right = tail === undefined || tail === "" ? [] : tail.split(":");
  const groups = tail === undefined ? left
    : [...left, ...Array<string>(Math.max(0, 8 - left.length - right.length)).fill("0"),
       ...right];
  if (groups.length < 4 || groups.slice(0, 4).some((g) => !/^[0-9a-f]{1,4}$/i.test(g))) {
    return null;
  }
  return `${groups.slice(0, 4).map((g) => parseInt(g, 16).toString(16)).join(":")}::/64`;
}

/**
 * The caller as the bounds key it: its IPv4 address, or its IPv6 /64. Cloudflare sets
 * `CF-Connecting-IP`; a request without it (none in production) shares one key.
 */
export function callerKey(request: Request): string {
  const address = (request.headers.get("CF-Connecting-IP") ?? "").trim();
  return slash64(address) ?? (address === "" ? "no-ip" : address);
}

/**
 * Whether `error` is the runtime's report that a Durable Object is overloaded (its
 * `.overloaded`, which Cloudflare's error handling page says never to retry).
 */
export function overloaded(error: unknown): boolean {
  return typeof error === "object" && error !== null
    && (error as { overloaded?: unknown }).overloaded === true;
}

export function tooMany(): Response {
  return new Response("too many requests; try again in a minute", {
    status: 429, headers: { "Retry-After": String(BLOCKED_S), "Cache-Control": "no-store",
                            "Content-Type": "text/plain; charset=utf-8" },
  });
}

function blockedUrl(kind: Counted, key: string): string {
  return `https://throttle.invalid/${kind}/${encodeURIComponent(key)}`;
}

/** Whether `key` is past `kind`'s bound, by the mark its counter left: no count is made. */
export async function blocked(kind: Counted, key: string): Promise<boolean> {
  const cache = await caches.open(BLOCKED_CACHE);
  return await cache.match(blockedUrl(kind, key)) !== undefined;
}

/**
 * One unit of `kind`'s work by `key`, counted: whether it is within the bound. Past it, the
 * key is marked for a minute, so `blocked` refuses its next requests before any work.
 */
export async function count(env: DoorsEnv, kind: Counted, key: string): Promise<boolean> {
  const { success } = await binding(env, kind).limit({ key: `${kind}:${key}` });
  if (!success) {
    const cache = await caches.open(BLOCKED_CACHE);
    await cache.put(blockedUrl(kind, key), new Response("", {
      headers: { "Cache-Control": `max-age=${BLOCKED_S}` } }));
  }
  return success;
}

/**
 * Whether `clientId` is a metadata document URL the provider would fetch: its own test, an
 * `https:` URL by the URL parser (any case of the scheme) with a non-empty raw path.
 */
export function metadataUrl(clientId: string | null | undefined): clientId is string {
  if (typeof clientId !== "string") {
    return false;
  }
  try {
    if (new URL(clientId).protocol !== "https:") {
      return false;
    }
  } catch {
    return false;
  }
  const scheme = clientId.indexOf("://");
  if (scheme === -1) {
    return false;
  }
  const after = clientId.slice(scheme + 3);
  const end = after.search(/[/?#]/);
  return end !== -1 && after[end] === "/";
}

/** Whether the provider's cache holds `clientId`'s document: its next resolve fetches none. */
export async function cached(clientId: string): Promise<boolean> {
  const cache = await caches.open(CIMD_CACHE);
  return await cache.match(clientId) !== undefined;
}

function decodeForm(value: string): string {
  return decodeURIComponent(value.replace(/\+/g, " "));
}

/** A client assertion's `iss`, as the provider reads it before verifying anything. */
function assertionIssuer(assertion: string | null): string | undefined {
  if (assertion === null || assertion.length > ASSERTION_MAX) {
    return undefined;
  }
  const parts = assertion.split(".");
  if (parts.length !== 3) {
    return undefined;
  }
  try {
    const json = atob(parts[1]!.replace(/-/g, "+").replace(/_/g, "/"));
    const iss = (JSON.parse(json) as { iss?: unknown }).iss;
    return typeof iss === "string" && iss !== "" ? iss : undefined;
  } catch {
    return undefined;
  }
}

/**
 * The client id a token or revoke request names, found as the provider finds it: the HTTP
 * Basic header's user (its scheme compared without case), else, with a client assertion,
 * the form's `client_id` or the assertion's `iss`, else the form's `client_id`.
 */
export function clientIdOf(request: Request, form: URLSearchParams): string | undefined {
  const header = request.headers.get("Authorization");
  if (header !== null) {
    const space = header.search(/[ \t]/);
    const scheme = (space === -1 ? header : header.slice(0, space)).toLowerCase();
    if (scheme === "basic") {
      try {
        const credentials = atob(header.slice(space).trim());
        const colon = credentials.indexOf(":");
        return colon === -1 ? undefined : decodeForm(credentials.slice(0, colon));
      } catch {
        return undefined;
      }
    }
  }
  if (form.has("client_assertion") || form.has("client_assertion_type")) {
    return form.get("client_id") || assertionIssuer(form.get("client_assertion"));
  }
  return form.get("client_id") ?? undefined;
}
