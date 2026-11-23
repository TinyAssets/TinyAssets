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
 * owner object the runtime reports overloaded is the refusal `object_overloaded`
 * (`contract/src/refusal.ts`), answered by each door's surface as any refusal is, never
 * retried: a technical ceiling, not ours. Abuse at volume meets Cloudflare's own DDoS
 * protection, in front of every Worker.
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
import { presentedKey } from "./api-keys";
import { type Bound, bindMcp } from "./binder";
import {
  AUTHORIZE_PATH, CONNECT_CALLBACK_PATH, E2B_HOOK_PATH, INBOUND_PATH, MCP_PATH,
  UPSTREAM_CALLBACK_PATH,
} from "./config";
import {
  ORIGIN, VERSION_HEADER, WEB_CLIENT, WEB_REDIRECT,
} from "../../contract/src/identity";
import type { AccountBinding } from "../../contract/src/binding";
import type { DoorsEnv } from "./env";
import { EGRESS_ORIGIN } from "../../contract/src/egress";
import { outsideAccount } from "./control";
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

/**
 * `/mcp`, for both credentials an outside caller holds, served by one handler: an owner's
 * key (ADR-039 decision 2.18), checked as the `/api` door checks it (`presentedKey`) and
 * held to its own grant, or an OAuth token for `/mcp`. Any other bearer, a key that does not
 * verify included, is the OAuth resource's, so it is answered the one `401` naming the
 * resource's metadata.
 */
async function mcpDoor(request: Request, env: DoorsEnv,
                       ctx: ExecutionContext): Promise<Response> {
  // No browser origin may send to `/mcp`: a present `Origin` is refused (MCP 2026-07-28
  // Streamable HTTP), an absent one is allowed.
  if (request.headers.get("Origin") !== null) {
    return plain(403, "origin not allowed");
  }
  const key = await presentedKey(env, request);
  if (key !== null) {
    return outsideMcp(request, env, key);
  }
  return mcpResource(async (req, environment, token) =>
    outsideMcp(req, environment, await bindMcp(environment, token)))
    .fetch(request, env, ctx);
}

function outsideMcp(request: Request, env: DoorsEnv,
                    bound: Bound<AccountBinding>): Promise<Response> {
  return answered(asJson, async () => serveMcp(request, await outsideAccount(env, bound), env));
}

/**
 * `answer`, naming `version`: Cloudflare ignores a version override for a version outside
 * the deployment and still answers, so a check reads which version served it here.
 */
export function versioned(answer: Response, version: WorkerVersionMetadata): Response {
  const named = new Response(answer.body, answer);
  named.headers.set(VERSION_HEADER, version.id);
  return named;
}

/** Serve one request through the doors, over the source records `sources`. */
export async function serve(request: Request, env: DoorsEnv, ctx: ExecutionContext,
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
    return mcpDoor(withoutCookie(request), env, ctx);
  }
  if (path.startsWith("/api/")) {
    return serveApi(withoutCookie(request), env);
  }
  if (path === E2B_HOOK_PATH) {
    return e2bLifecycle(withoutCookie(request), env);
  }
  if (path.startsWith(INBOUND_PATH)) {
    return serveInbound(withoutCookie(request), env);
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
