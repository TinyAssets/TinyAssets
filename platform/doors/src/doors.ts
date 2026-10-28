/**
 * The doors' router: every path the doors Worker serves on `tinyassets.io`.
 *
 * - `/mcp`: the `Cookie` header removed, a present `Origin` held to `MCP_ORIGINS`
 *   (`403` otherwise), then the resource host (the token), the kill switch, the binder
 *   and the MCP handler.
 * - `/api/*`: the `Cookie` header removed, then the key (`api.ts`).
 * - `/hooks/*`: the inbound door is owed (tasks 2), so `404`, and its request is never
 *   read; the inbound door strips the `Cookie` header as `/mcp` and `/api` do.
 * - `/app/api/*`: the owner surface (`app.ts`).
 * - `/authorize`, `/oauth/upstream/callback`: the sign-in (`authorize.ts`).
 * - `/oauth/clients/web.json`: the first-party app's Client ID Metadata Document.
 * - `/.well-known/oauth-protected-resource/mcp`: the resource's metadata.
 * - `/.well-known/oauth-authorization-server`, `/oauth/token`, `/oauth/register` (only
 *   with `DCR_ENABLED`): the provider's own endpoints.
 *
 * Any other origin or path is `404`. The upstream sign-in is the caller's: `index.ts`
 * passes `UNWIRED` until the AuthKit slice, and the tests a fake.
 */
import { serveApi } from "./api";
import { serveApp } from "./app";
import { authorize, callback } from "./authorize";
import { bindMcp } from "./binder";
import {
  type DoorsEnv, MCP_ORIGINS, PUBLIC_ORIGIN, WEB_CLIENT, WEB_REDIRECT,
} from "./config";
import { outsideGate } from "./control";
import { serveMcp } from "./mcp";
import { authorizationServer, mcpResource } from "./oauth";
import { OWNER_SURFACE, withoutCookie } from "./session";
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

async function mcpDoor(request: Request, env: DoorsEnv, ctx: ExecutionContext):
    Promise<Response> {
  const origin = request.headers.get("Origin");
  if (origin !== null && !MCP_ORIGINS.includes(origin)) {
    return new Response("origin not allowed", { status: 403 });
  }
  return mcpResource(async (req, environment, token) => {
    const bound = await bindMcp(environment, token);
    return await outsideGate(environment, bound.binding.owner) ?? serveMcp(req, bound);
  }).fetch(request, env, ctx);
}

/** Serve one request through the doors, with `upstream` as the owner's sign-in. */
export async function serve(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                            upstream: Upstream): Promise<Response> {
  const url = new URL(request.url);
  if (url.origin !== PUBLIC_ORIGIN) {
    return notFound();
  }
  const path = url.pathname;
  if (path === "/mcp" || path.startsWith("/mcp/")) {
    return mcpDoor(withoutCookie(request), env, ctx);
  }
  if (path.startsWith("/api/")) {
    return serveApi(withoutCookie(request), env);
  }
  if (path.startsWith("/hooks/")) {
    return notFound();
  }
  if (path.startsWith(OWNER_SURFACE)) {
    return serveApp(request, env);
  }
  if (path === "/authorize") {
    return authorize(request, env, upstream);
  }
  if (path === "/oauth/upstream/callback") {
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
