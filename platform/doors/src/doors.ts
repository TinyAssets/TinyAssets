/**
 * The doors' router: every path the doors Worker serves on its public host (`HOST`), and the
 * egress door on the egress host (`egress.ts`), which the deploy kit's mode `routes`
 * attaches. Nothing else is served on that origin, and the egress door is served on no other,
 * so a sandbox token reaches no other door. Any other origin or path is `404`.
 *
 * Every door a token, a key or a capability opens (`/mcp`, `/api/*`, `/hooks/*` and the
 * egress door) sees the request without its `Cookie` header; the owner surface, the app's
 * pages and the sign-in keep it.
 *
 * No caller is counted or capped by the doors (the founder's rule: no invented limits). An
 * owner object the runtime reports overloaded (Cloudflare's soft limit of 1,000 requests a
 * second an object) is answered `429` with `Retry-After`, never retried: a technical
 * ceiling, not ours. Abuse at volume meets Cloudflare's own DDoS protection, in front of
 * every Worker.
 *
 * The source records are the caller's: `index.ts` passes the bundled records, and a test of a
 * record the bundle lacks its own catalog.
 */
import { e2bLifecycle } from "./e2b-hooks";
import { serveApi } from "./api";
import { serveApp } from "./app";
import { serveEgress } from "./egress";
import { authorize, callback } from "./authorize";
import { connectCallback } from "./connect";
import { bindMcp, withAccount } from "./binder";
import {
  AUTHORIZE_PATH, CONNECT_CALLBACK_PATH, E2B_HOOK_PATH, INBOUND_PATH, MCP_PATH,
  UPSTREAM_CALLBACK_PATH,
} from "./config";
import { ORIGIN, WEB_CLIENT, WEB_REDIRECT } from "../../contract/src/identity";
import type { DoorsEnv } from "./env";
import { EGRESS_ORIGIN } from "../../contract/src/egress";
import { outsideRead } from "./control";
import { serveInbound } from "./inbound";
import { plain } from "./kit";
import { serveMcp } from "./mcp";
import { authorizationServer, mcpResource } from "./oauth";
import { appPath, servePage } from "./pages";
import { answered, asJson } from "./refused";
import { OWNER_SURFACE, withoutCookie } from "./session";
import type { Sources } from "../../sources/src/schema";

function notFound(): Response {
  return plain(404, "not found");
}

/** The first-party app's metadata document, at its own `client_id` URL. */
function webClient(): Response {
  return Response.json({
    client_id: WEB_CLIENT, client_name: "TinyAssets", client_uri: ORIGIN,
    redirect_uris: [WEB_REDIRECT], token_endpoint_auth_method: "none",
    grant_types: ["authorization_code", "refresh_token"], response_types: ["code"],
  }, { headers: { "Cache-Control": "public, max-age=3600" } });
}

async function mcpDoor(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                       sources: Sources): Promise<Response> {
  // No browser origin may send to `/mcp`: a present `Origin` is refused (MCP 2026-07-28
  // Streamable HTTP), an absent one is allowed.
  if (request.headers.get("Origin") !== null) {
    return plain(403, "origin not allowed");
  }
  return mcpResource(async (req, environment, token) => {
    const bound = await bindMcp(environment, token);
    return answered(asJson, async () => serveMcp(
      req, withAccount(bound, await outsideRead(environment, bound.binding.owner), sources),
      environment));
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

/** Serve one request through the doors, over the source records `sources`. */
export async function serve(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                            sources: Sources): Promise<Response> {
  try {
    return await route(request, env, ctx, sources);
  } catch (error) {
    if (overloaded(error)) {
      return plain(429, "this account is busy; try again shortly", { "Retry-After": "1" });
    }
    throw error;
  }
}

async function route(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                     sources: Sources): Promise<Response> {
  const url = new URL(request.url);
  if (url.origin === EGRESS_ORIGIN) {
    return serveEgress(withoutCookie(request), env, ctx);
  }
  if (url.origin !== ORIGIN) {
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
    return authorize(request, env);
  }
  if (path === UPSTREAM_CALLBACK_PATH) {
    return callback(request, env);
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
