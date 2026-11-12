/**
 * The doors' router: every path the doors Worker serves on `tinyassets.io`, and the egress
 * door on `egress.tinyassets.io`.
 *
 * - `egress.tinyassets.io/r/<run>/...`: a run's model calls (`c/<connection>/*`), its MCP
 *   door (`mcp`) and its event door (`events`), by the egress token (`egress.ts`), the
 *   `Cookie` header removed. Nothing else is served on that
 *   origin, and the egress door is served on no other, so a sandbox token reaches no other
 *   door. No route is deployed for it until the routes slice.
 *
 * - `/mcp`: the `Cookie` header removed, a present `Origin` refused (`403`), then the
 *   resource host (the token), the kill switch, the binder and the MCP handler.
 * - `/api/*`: the `Cookie` header removed, then the key (`api.ts`).
 * - `/hooks/inbound/<token>`: the inbound door (`inbound.ts`), the `Cookie` header removed;
 *   any other `/hooks/` path is `404`, its request never read.
 *
 * No caller is counted or capped by the doors (the founder's rule: no invented limits;
 * slice L's review). An owner object the runtime reports overloaded (Cloudflare's soft
 * limit of 1,000 requests a second an object) is answered `429` with `Retry-After`, never
 * retried: a technical ceiling, not ours. Abuse at volume meets Cloudflare's own DDoS
 * protection, in front of every Worker.
 * - `/app/api/*`: the owner surface (`app.ts`).
 * - `/app` and its named pages and files: the owner app itself (`pages.ts`).
 * - `/authorize`, `/oauth/upstream/callback`: the sign-in (`authorize.ts`).
 * - `/oauth/connect/callback/<source>[/<state>]`: a model source's OAuth redirect
 *   (`connect.ts`).
 * - `/oauth/clients/web.json`: the first-party app's Client ID Metadata Document.
 * - `/.well-known/oauth-protected-resource/mcp`: the resource's metadata.
 * - `/.well-known/oauth-authorization-server`, `/oauth/token`, `/oauth/register` (only
 *   with `DCR_ENABLED`): the provider's own endpoints.
 *
 * Any other origin or path is `404`. The upstream sign-in and the source records are the
 * caller's: `index.ts` passes the configured sign-in and the bundled records,
 * and the tests a fake sign-in and the records with a test source beside them.
 */
import { e2bLifecycle } from "./e2b-hooks";
import { serveApi } from "./api";
import { serveApp } from "./app";
import { serveEgress } from "./egress";
import { authorize, callback } from "./authorize";
import { connectCallback } from "./connect";
import { bindMcp, withTiers } from "./binder";
import {
  AUTHORIZE_PATH, CONNECT_CALLBACK_PATH, E2B_HOOK_PATH, INBOUND_PATH,
  MCP_PATH, PUBLIC_ORIGIN, UPSTREAM_CALLBACK_PATH, WEB_CLIENT, WEB_REDIRECT,
} from "./config";
import type { DoorsEnv } from "./env";
import { EGRESS_ORIGIN } from "../../contract/src/egress";
import { outsideRead } from "./control";
import { serveInbound } from "./inbound";
import { serveMcp } from "./mcp";
import { authorizationServer, mcpResource } from "./oauth";
import { appPath, servePage } from "./pages";
import { OWNER_SURFACE, withoutCookie } from "./session";
import type { Sources } from "../../sources/src/schema";
import type { Upstream } from "./upstream";

function notFound(): Response {
  return new Response("not found", { status: 404 });
}

/** The first-party app's metadata document, at its own `client_id` URL. */
function webClient(): Response {
  return Response.json({
    client_id: WEB_CLIENT, client_name: "TinyAssets", client_uri: PUBLIC_ORIGIN,
    redirect_uris: [WEB_REDIRECT], token_endpoint_auth_method: "none",
    grant_types: ["authorization_code", "refresh_token"], response_types: ["code"],
  }, { headers: { "Cache-Control": "public, max-age=3600" } });
}

async function mcpDoor(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                       sources: Sources): Promise<Response> {
  // No browser origin may send to `/mcp`: a present `Origin` is refused (MCP 2026-07-28
  // Streamable HTTP), an absent one is allowed.
  if (request.headers.get("Origin") !== null) {
    return new Response("origin not allowed", { status: 403 });
  }
  return mcpResource(async (req, environment, token) => {
    const bound = await bindMcp(environment, token);
    const read = await outsideRead(environment, bound.binding.owner);
    return read instanceof Response ? read : serveMcp(req, withTiers(bound, read, sources),
                                                      environment);
  }).fetch(request, env, ctx);
}

/**
 * Whether `error` is the runtime's report that a Durable Object is overloaded (its
 * `.overloaded`, which Cloudflare's error-handling page says never to retry).
 */
export function overloaded(error: unknown): boolean {
  return typeof error === "object" && error !== null
    && (error as { overloaded?: unknown }).overloaded === true;
}

/** Serve one request through the doors, with `upstream` as the owner's sign-in. */
export async function serve(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                            upstream: Upstream | (() => Upstream), sources: Sources):
    Promise<Response> {
  try {
    return await route(request, env, ctx, upstream, sources);
  } catch (error) {
    if (overloaded(error)) {
      return new Response("this account is busy; try again shortly", {
        status: 429, headers: { "Retry-After": "1", "Cache-Control": "no-store" } });
    }
    throw error;
  }
}

async function route(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                     upstream: Upstream | (() => Upstream), sources: Sources):
    Promise<Response> {
  const url = new URL(request.url);
  if (url.origin === EGRESS_ORIGIN) {
    return serveEgress(withoutCookie(request), env, ctx);
  }
  if (url.origin !== PUBLIC_ORIGIN) {
    return notFound();
  }
  const path = url.pathname;
  if (path === MCP_PATH || path.startsWith(`${MCP_PATH}/`)) {
    return mcpDoor(withoutCookie(request), env, ctx, sources);
  }
  if (path.startsWith("/api/")) {
    return serveApi(withoutCookie(request), env, sources);
  }
  if (path === E2B_HOOK_PATH) {
    return e2bLifecycle(withoutCookie(request), env);
  }
  if (path.startsWith(INBOUND_PATH)) {
    return serveInbound(withoutCookie(request), env, sources);
  }
  if (path.startsWith("/hooks/")) {
    return notFound();
  }
  if (path.startsWith(OWNER_SURFACE)) {
    return serveApp(request, env, sources);
  }
  if (appPath(path)) {
    return servePage(request, env);
  }
  if (path === CONNECT_CALLBACK_PATH || path.startsWith(`${CONNECT_CALLBACK_PATH}/`)) {
    return connectCallback(request, sources);
  }
  if (path === AUTHORIZE_PATH) {
    return authorize(request, env, upstream);
  }
  if (path === UPSTREAM_CALLBACK_PATH) {
    return callback(request, env, upstream);
  }
  if (url.href === WEB_CLIENT) {
    return webClient();
  }
  if (path.startsWith("/.well-known/oauth-protected-resource")) {
    return mcpResource(() => Promise.resolve(notFound())).fetch(request, env, ctx);
  }
  if (path.startsWith("/.well-known/") || path.startsWith("/oauth/")) {
    return authorizationServer(env).fetch(request, env, ctx);
  }
  return notFound();
}
