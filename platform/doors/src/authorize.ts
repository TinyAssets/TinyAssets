/**
 * The authorization endpoint and the upstream callback (research §1.5, §8; checklist 4,
 * 5, 17). All on `tinyassets.io`:
 *
 * 1. `GET /authorize` must be a top-level navigation, or it is refused `403`. The
 *    provider validates the request (client, exact redirect URI, resource, S256 PKCE).
 * 2. An outside client gets the consent page. Consent is never remembered: no approval
 *    cookie is written or read, so every authorization shows it, the first one after a
 *    revoke included, and only the owner's own consent brings a revoked client back.
 *    The owner's own app (`FIRST_PARTY_CLIENTS`) is ours, so it goes straight on.
 * 3. On approval, the upstream sign-in, with our own PKCE verifier kept in the
 *    provider's browser-bound, single-use upstream record and `prompt=login`, so the
 *    sign-in's time is always fresh, and after any revoke the owner made before.
 * 4. At the callback: the provider recovers the request in the same browser only, the
 *    upstream verifies the sign-in, and the doors take it only when at most 600 s old.
 * 5. The platform gates: an outside client's authorization is outside work (the kill
 *    switch and the maintenance gate); the owner's own sign-in, the maintenance gate.
 * 6. An outside client: the grant, its props holding the sign-in's time. The owner's
 *    app: `sessionOpen` at the sign-in's time, the session cookie set on the redirect,
 *    and the grant.
 *
 * A resource and its clients never cross: a token for `/app` goes only to a first-party
 * client, and a first-party client never gets one for `/mcp`.
 */
import {
  AuthorizationError, type AuthRequest, authorizationErrorRedirect, CimdFetchError,
  type ConsentDescription, type OAuthHelpers,
} from "@cloudflare/workers-oauth-provider";

import { principal } from "../../owner/src/text";

import { type AppProps, bindSignIn, type McpProps, mcpOrigin } from "./binder";
import {
  APP_RESOURCE, CLOCK_MARGIN_S, type DoorsEnv, FIRST_PARTY_CLIENTS, MCP_RESOURCE, now,
  SCOPES, SIGN_IN_MAX_AGE_S, UPSTREAM_CALLBACK,
} from "./config";
import { outsideOpen, ownerOpen } from "./control";
import { readControl } from "./directory";
import { authorizationServer } from "./oauth";
import { sessionCookie } from "./session";
import type { Upstream } from "./upstream";

interface Verifier {
  readonly verifier: string;
}

const escape = (value: string): string =>
  value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);

function text(status: number, message: string): Response {
  return new Response(message, {
    status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function redirect(location: string, headers: Headers = new Headers()): Response {
  headers.set("Location", location);
  headers.set("Cache-Control", "no-store");
  return new Response(null, { status: 302, headers });
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function s256(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier))));
}

function granted(scope: readonly string[]): string[] {
  return scope.filter((s) => SCOPES.includes(s));
}

function firstParty(request: AuthRequest): boolean {
  return FIRST_PARTY_CLIENTS.includes(request.clientId);
}

/** The consent page: every client-supplied string escaped, framing refused by its headers. */
export function consentPage(details: ConsentDescription, handle: string): string {
  const name = escape(details.clientName);
  const origin = details.clientDomain !== undefined
    ? `Published by <strong>${escape(details.clientDomain)}</strong>.`
    : "This app registered itself; its name is not verified.";
  const loopback = details.redirectIsLoopback
    ? "<p><strong>This sends access to an app on your computer.</strong> Continue only if " +
      "you just started signing in from it.</p>"
    : "";
  return `<!doctype html>
<meta charset="utf-8">
<title>Connect ${name} to TinyAssets</title>
<h1>Allow ${name} to use your TinyAssets account?</h1>
<p>${origin} Access will be sent to <strong>${escape(details.redirectHost)}</strong>.</p>
${loopback}
<form method="post">
  <input type="hidden" name="handle" value="${escape(handle)}">
  <p><button name="decision" value="approve">Allow</button>
     <button name="decision" value="deny">Deny</button></p>
</form>`;
}

/** A browser's top-level navigation, as `Sec-Fetch-*` reports it. */
function navigation(request: Request): boolean {
  return request.headers.get("Sec-Fetch-Mode") === "navigate"
    && request.headers.get("Sec-Fetch-Dest") === "document";
}

/** Start the upstream sign-in for an approved request, checking the URL it builds. */
async function signIn(oauth: OAuthHelpers, upstream: Upstream, request: AuthRequest,
                      headers?: Headers): Promise<Response> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = await s256(verifier);
  const data: Verifier = { verifier };
  const started = await oauth.beginUpstream(request, headers === undefined ? { data }
                                                                           : { data, headers });
  const location = upstream.authorizeUrl({ state: started.state, code_challenge: challenge,
                                           redirect_uri: UPSTREAM_CALLBACK });
  const url = new URL(location);
  const params = url.searchParams;
  if (url.protocol !== "https:" || params.get("prompt") !== "login"
      || params.get("state") !== started.state || params.get("code_challenge") !== challenge
      || params.get("code_challenge_method") !== "S256"
      || params.get("redirect_uri") !== UPSTREAM_CALLBACK) {
    throw new Error("the upstream's sign-in URL must ask for prompt=login with our S256 " +
                    "challenge, state and callback");
  }
  return redirect(location, started.headers);
}

/** A failure the provider types: redirect when the client is validated, else render it. */
function failed(error: unknown): Response {
  if (error instanceof AuthorizationError && error.redirectTo !== undefined) {
    return redirect(error.redirectTo);
  }
  if (error instanceof AuthorizationError) {
    return text(400, error.description);
  }
  if (error instanceof CimdFetchError) {
    return text(400, "This app could not be verified.");
  }
  throw error;
}

/** `GET` and `POST /authorize`. */
export async function authorize(request: Request, env: DoorsEnv, upstream: Upstream):
    Promise<Response> {
  const oauth = authorizationServer(env).getOAuthApi(env);
  try {
    if (request.method === "GET") {
      if (!navigation(request)) {
        return text(403, "Open this page in your browser to sign in.");
      }
      const parsed = await oauth.parseAuthRequest(request);
      const forApp = parsed.resource === APP_RESOURCE;
      if (forApp !== firstParty(parsed)
          || (parsed.resource !== APP_RESOURCE && parsed.resource !== MCP_RESOURCE)) {
        return redirect(authorizationErrorRedirect(parsed, "access_denied"));
      }
      if (forApp) {
        return await signIn(oauth, upstream, parsed);
      }
      const details = await oauth.describeConsent(parsed);
      const consent = await oauth.beginConsent(parsed);
      consent.headers.set("Content-Type", "text/html; charset=utf-8");
      return new Response(consentPage(details, consent.handle), { headers: consent.headers });
    }
    if (request.method === "POST") {
      const form = await request.clone().formData();
      const handle = String(form.get("handle") ?? "");
      if (form.get("decision") !== "approve") {
        const denied = await oauth.denyConsent(request, handle);
        return new Response(null, { status: 302, headers: denied.headers });
      }
      // No `remember`: the approval is this authorization's alone. The grant's scopes
      // are cut to the catalogue when it is made.
      const approved = await oauth.approveConsent(request, handle);
      return await signIn(oauth, upstream, approved.request, approved.headers);
    }
    return text(405, "method not allowed");
  } catch (error) {
    return failed(error);
  }
}

/** `GET /oauth/upstream/callback`: the verified sign-in becomes the grant. */
export async function callback(request: Request, env: DoorsEnv, upstream: Upstream):
    Promise<Response> {
  const oauth = authorizationServer(env).getOAuthApi(env);
  let resumed: Awaited<ReturnType<typeof oauth.finishUpstream<Verifier>>>;
  try {
    resumed = await oauth.finishUpstream<Verifier>(request);
  } catch (error) {
    return failed(error);
  }
  const { request: original, data, headers } = resumed;
  const params = new URL(request.url).searchParams;
  const code = params.get("code");
  if (params.get("error") !== null || code === null) {
    return redirect(authorizationErrorRedirect(original, "access_denied"), headers);
  }
  const signedIn = await upstream.finish({ code, code_verifier: data.verifier,
                                           redirect_uri: UPSTREAM_CALLBACK });
  const at = now();
  if (!Number.isSafeInteger(signedIn.authenticated_at)
      || signedIn.authenticated_at < at - SIGN_IN_MAX_AGE_S
      || signedIn.authenticated_at > at + CLOCK_MARGIN_S) {
    return redirect(authorizationErrorRedirect(original, "access_denied",
                                               "sign in again"), headers);
  }
  // The provider keys grants and tokens by user id, and refuses one holding a colon.
  const owner = principal(signedIn.subject);
  const control = await readControl(env);
  const scope = granted(original.scope);
  if (original.resource === MCP_RESOURCE && !firstParty(original)) {
    if (!outsideOpen(control, owner)) {
      return redirect(authorizationErrorRedirect(original, "temporarily_unavailable"), headers);
    }
    const props: McpProps = {
      kind: "mcp", owner, ...await mcpOrigin(original.clientId, original.redirectUri),
      source_ref: original.clientId, authenticated_at: signedIn.authenticated_at,
    };
    const { redirectTo } = await oauth.completeAuthorization({
      request: original, userId: owner, metadata: {}, scope, props });
    return redirect(redirectTo, headers);
  }
  if (original.resource === APP_RESOURCE && firstParty(original)) {
    if (!ownerOpen(control, owner)) {
      return redirect(authorizationErrorRedirect(original, "temporarily_unavailable"), headers);
    }
    const { binding, stub } = bindSignIn(env, owner, signedIn.authenticated_at);
    const { session } = await stub.sessionOpen(binding);
    const props: AppProps = { kind: "app", owner };
    const { redirectTo } = await oauth.completeAuthorization({
      request: original, userId: owner, metadata: {}, scope, props });
    headers.append("Set-Cookie", sessionCookie(session));
    return redirect(redirectTo, headers);
  }
  throw new Error("an authorization's resource and client crossed after consent");
}
