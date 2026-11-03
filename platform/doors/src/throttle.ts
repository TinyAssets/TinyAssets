/**
 * What an unauthenticated caller can make the platform do, bounded (tasks 2, rate limits;
 * slice L). The founder's rule: no invented limits. So no authenticated caller is capped
 * here: the tier's simultaneous runs, the owner's own settings and vendors' limits already
 * bound a signed-in owner, an MCP client and an API key. These two bounds are security,
 * each recorded in `limits-audit.md` with its reason, on Cloudflare's `ratelimits`
 * binding, keyed by the caller's IP (`CF-Connecting-IP`, which Cloudflare sets and a
 * client cannot):
 *
 * - **CIMD fetches** (`CIMD_LIMIT`): a `client_id` that is an https URL makes the OAuth
 *   provider fetch that URL's metadata document when its cache does not hold it
 *   (`CIMD_CACHE`, the provider's own Cache API name). Only such a fetch counts: a known
 *   client (Claude's, ChatGPT's) answered from the cache costs nothing and is never
 *   refused, however many people use it from one backend IP.
 * - **Sign-in attempts** (`SIGNIN_LIMIT`): each `/authorize` and upstream callback starts or
 *   finishes an upstream sign-in (KV records, a code exchange, a JWKS fetch).
 *
 * Past either, `429` with `Retry-After`, before the provider or the upstream is reached.
 */
import type { DoorsEnv, RateLimit } from "./config";

/** The OAuth provider's CIMD cache (`@cloudflare/workers-oauth-provider` 1.2.4, pinned). */
export const CIMD_CACHE = "workers-oauth-provider:cimd:v1";
/** A token or revoke request's form, read before the provider reads it: a few fields. */
export const OAUTH_FORM_MAX = 16 * 1024;

function caller(request: Request): string {
  return request.headers.get("CF-Connecting-IP") ?? "no-ip";
}

export function tooMany(): Response {
  return new Response("too many requests; try again in a minute", {
    status: 429, headers: { "Retry-After": "60", "Cache-Control": "no-store",
                            "Content-Type": "text/plain; charset=utf-8" },
  });
}

async function allowed(limit: RateLimit, key: string): Promise<boolean> {
  return (await limit.limit({ key })).success;
}

/** A sign-in attempt from this caller is within its bound. */
export function signInAllowed(env: DoorsEnv, request: Request): Promise<boolean> {
  return allowed(env.SIGNIN_LIMIT, `signin:${caller(request)}`);
}

/** Whether `clientId` is a metadata document URL the provider would fetch. */
export function metadataUrl(clientId: string | null): clientId is string {
  return clientId !== null && clientId.startsWith("https://");
}

/**
 * Whether the provider may resolve `clientId` for this caller: anything that is not a
 * metadata URL, or one its cache holds, costs no fetch and passes; an unknown one counts.
 */
export async function cimdAllowed(env: DoorsEnv, request: Request, clientId: string | null):
    Promise<boolean> {
  if (!metadataUrl(clientId)) {
    return true;
  }
  const cache = await caches.open(CIMD_CACHE);
  if (await cache.match(clientId) !== undefined) {
    return true;
  }
  return allowed(env.CIMD_LIMIT, `cimd:${caller(request)}`);
}
