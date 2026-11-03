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
 * - `/mcp`: the `Cookie` header removed, a present `Origin` held to `MCP_ORIGINS`
 *   (`403` otherwise), then the resource host (the token), the kill switch, the binder
 *   and the MCP handler.
 * - `/api/*`: the `Cookie` header removed, then the key (`api.ts`).
 * - `/hooks/inbound/<token>`: the inbound door (`inbound.ts`), the `Cookie` header removed;
 *   any other `/hooks/` path is `404`, its request never read.
 * - `/oauth/token`, `/oauth/revoke`: a failure counts against the caller's address and
 *   client, and one naming a metadata document the provider must fetch against the
 *   caller's metadata fetches; past either, `429` before the provider runs (`throttle.ts`).
 *
 * Unauthenticated failures (a bearer or key the doors cannot validate, an inbound hook
 * refused at its check) count against the caller (`throttle.ts`), and a caller past the
 * bound is refused `429` before any work. An owner object the runtime reports overloaded
 * (its own soft limit of about 1,000 requests a second) is answered `429` with
 * `Retry-After`, never retried: a technical ceiling, not ours.
 * - `/app/api/*`: the owner surface (`app.ts`).
 * - `/authorize`, `/oauth/upstream/callback`: the sign-in (`authorize.ts`).
 * - `/oauth/connect/callback/<source>[/<state>]`: a model source's OAuth redirect
 *   (`connect.ts`).
 * - `/oauth/clients/web.json`: the first-party app's Client ID Metadata Document.
 * - `/.well-known/oauth-protected-resource/mcp`: the resource's metadata.
 * - `/.well-known/oauth-authorization-server`, `/oauth/token`, `/oauth/register` (only
 *   with `DCR_ENABLED`): the provider's own endpoints.
 *
 * Any other origin or path is `404`. The upstream sign-in and the source records are the
 * caller's: `index.ts` passes `UNWIRED` until the AuthKit slice and the bundled records,
 * and the tests a fake sign-in and the records with a test source beside them.
 */
import { serveApi } from "./api";
import { serveApp } from "./app";
import { serveEgress } from "./egress";
import { authorize, callback } from "./authorize";
import { connectCallback } from "./connect";
import { bindMcp, withTiers } from "./binder";
import {
  CONNECT_CALLBACK_PATH, type DoorsEnv, EGRESS_ORIGIN, INBOUND_PATH, MCP_ORIGINS, PUBLIC_ORIGIN,
  WEB_CLIENT, WEB_REDIRECT,
} from "./config";
import { outsideRead } from "./control";
import { boundedText } from "./body";
import { serveInbound } from "./inbound";
import {
  blocked, cached, callerKey, clientIdOf, count, metadataUrl, OAUTH_FORM_MAX, overloaded,
  tooMany,
} from "./throttle";
import { serveMcp } from "./mcp";
import { authorizationServer, mcpResource } from "./oauth";
import { OWNER_SURFACE, withoutCookie } from "./session";
import type { Sources } from "./sources";
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
    const read = await outsideRead(environment, bound.binding.owner);
    return read instanceof Response ? read : serveMcp(req, withTiers(bound, read.tiers),
                                                      environment);
  }).fetch(request, env, ctx);
}

/**
 * `act`, from a caller not past the failure bound; its answer counted as one failure when
 * `failed` says so.
 */
async function failures(request: Request, env: DoorsEnv, failed: (answer: Response) => boolean,
                        act: () => Promise<Response>): Promise<Response> {
  const key = callerKey(request);
  if (await blocked("fail", key)) {
    return tooMany();
  }
  const answer = await act();
  if (failed(answer)) {
    await count(env, "fail", key);
  }
  return answer;
}

/** A credential the caller presented and the door refused as no credential. */
function badBearer(request: Request): (answer: Response) => boolean {
  const presented = request.headers.has("Authorization");
  return (answer) => presented && answer.status === 401;
}

async function digest(text: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(bytes)].slice(0, 16)
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * The token and revoke endpoints: the provider's own, after the bounds on what their
 * unauthenticated failures and metadata fetches may cost.
 */
async function oauthEndpoint(request: Request, env: DoorsEnv, ctx: ExecutionContext):
    Promise<Response> {
  // The form is read from a copy, bounded, before the provider reads the request itself.
  const form = await boundedText(request.clone(), OAUTH_FORM_MAX);
  if (form === null) {
    return new Response("the request body is too large", { status: 413 });
  }
  const clientId = clientIdOf(request, new URLSearchParams(form));
  const key = callerKey(request);
  const fetching = metadataUrl(clientId) && !await cached(clientId);
  const client = `${key}:${await digest(clientId ?? "")}`;
  if ((fetching && await blocked("cimd", key)) || await blocked("fail", client)) {
    return tooMany();
  }
  const answer = await authorizationServer(env).fetch(request, env, ctx);
  if (answer.status >= 400) {
    if (fetching) {
      await count(env, "cimd", key);
    }
    await count(env, "fail", client);
  }
  return answer;
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
  if (path === "/mcp" || path.startsWith("/mcp/")) {
    return failures(request, env, badBearer(request),
                    () => mcpDoor(withoutCookie(request), env, ctx));
  }
  if (path.startsWith("/api/")) {
    return failures(request, env, badBearer(request),
                    () => serveApi(withoutCookie(request), env));
  }
  if (path.startsWith(INBOUND_PATH)) {
    // An inbound hook refused at its check: an unknown route, a bad signature or proof.
    return failures(request, env, (answer) => answer.status === 401 || answer.status === 404,
                    () => serveInbound(withoutCookie(request), env));
  }
  if (path.startsWith("/hooks/")) {
    return notFound();
  }
  if (path.startsWith(OWNER_SURFACE)) {
    return failures(request, env, badBearer(request), () => serveApp(request, env, sources));
  }
  if (path === CONNECT_CALLBACK_PATH || path.startsWith(`${CONNECT_CALLBACK_PATH}/`)) {
    return connectCallback(request, sources);
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
  if ((path === "/oauth/token" || path === "/oauth/revoke") && request.method === "POST") {
    return oauthEndpoint(request, env, ctx);
  }
  if (path.startsWith("/.well-known/") || path.startsWith("/oauth/")) {
    return authorizationServer(env).fetch(request, env, ctx);
  }
  return notFound();
}
