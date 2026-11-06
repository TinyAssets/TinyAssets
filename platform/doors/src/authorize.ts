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
 *    upstream verifies the sign-in, and the doors take it only when at most 600 s old. A
 *    failed verification is a `502` naming the check, logged the same.
 * 5. The owner directory's record of the owner (`o:`): an erased owner is refused (and its
 *    identity's deletion tried again), whatever the issuer still says.
 * 6. The platform gates: an outside client's authorization is outside work (the kill
 *    switch and the maintenance gate); the owner's own sign-in, the maintenance gate.
 * 7. Sign-up: an owner the directory has no record of is admitted by the platform, its
 *    explicit admit (`bindPlatform`, `admissionSet`), and then recorded; a fresh object
 *    is pending until then and refuses every door (`owner/src/admission.ts`).
 * 8. An outside client: the grant, its props holding the sign-in's time. The owner's
 *    app: `sessionOpen` at the sign-in's time, the session cookie set on the redirect,
 *    and the grant.
 *
 * No number of ours bounds a sign-in (slice L's review). A metadata document the provider
 * fetches for a `client_id` is bounded by the provider itself: public https URLs only,
 * 5 KiB, 10 s, one fetch out for one request in.
 *
 * A resource and its clients never cross: a token for `/app` goes only to a first-party
 * client, and a first-party client never gets one for `/mcp`. An outside client may not
 * name a redirect on our own origin, however it registered. The consent POST is a form
 * of at most 4 KiB.
 */
import {
  AuthorizationError, type AuthRequest, authorizationErrorRedirect, CimdFetchError,
  type ConsentDescription, type OAuthHelpers,
} from "@cloudflare/workers-oauth-provider";

import {
  type AppProps, bindPlatform, bindSignIn, type McpProps, mcpOrigin, ownerOf,
} from "./binder";
import { type Vendor, vendorOf } from "./apps";
import { boundedText, TOO_LARGE } from "./body";
import {
  APP_RESOURCE, AUTHORIZE_BODY_MAX, CLOCK_MARGIN_S, type DoorsEnv, FIRST_PARTY_CLIENTS,
  MCP_RESOURCE, now, PUBLIC_HOST, SCOPES, SIGN_IN_MAX_AGE_S, UPSTREAM_CALLBACK,
} from "./config";
import { outsideOpen, ownerOpen } from "./control";
import { putOwner, readControl, readOwner } from "./directory";
import { ERASING } from "../../contract/src/errors";
import { authorizationServer } from "./oauth";
import { replaceInstallation } from "./revoke";
import { mediaType, sessionCookie } from "./session";
import type { SignIn, Upstream } from "./upstream";

interface Verifier {
  readonly verifier: string;
}

const escape = (value: string): string =>
  value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);

function text(status: number, message: string, headers: Headers = new Headers()): Response {
  headers.set("Content-Type", "text/plain; charset=utf-8");
  headers.set("Cache-Control", "no-store");
  return new Response(message, { status, headers });
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

/**
 * Whether a redirect URI is on one of our hosts: the apex or any subdomain, as DNS names
 * them, so a fully qualified spelling with its trailing dot (`tinyassets.io.`) is ours
 * too. The URL parser has already lowercased the host, dropped the port, decoded `%2e`
 * and turned a homoglyph into punycode, so only the dot is left to strip.
 */
export function ours(redirectUri: string): boolean {
  const host = new URL(redirectUri).hostname.replace(/\.$/, "");
  return host === PUBLIC_HOST || host.endsWith(`.${PUBLIC_HOST}`);
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
export function navigation(request: Request): boolean {
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

/**
 * The owner's sign-in, built only where a sign-in needs it: a bad `UPSTREAM` breaks
 * sign-in alone, never another door.
 */
function upstreamOf(source: Upstream | (() => Upstream)): Upstream {
  return typeof source === "function" ? source() : source;
}

/** `GET` and `POST /authorize`. */
export async function authorize(request: Request, env: DoorsEnv,
                                upstream: Upstream | (() => Upstream)): Promise<Response> {
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
      // Only our own app is sent access on our own hosts (`tinyassets.io` and every
      // subdomain, `mcp.` among them): an outside client naming a redirect there (a CIMD
      // document or a registration may) would have the consent page say the access goes
      // to TinyAssets.
      if (!forApp && ours(parsed.redirectUri)) {
        return text(400, "This app may not send access to TinyAssets itself.");
      }
      if (forApp) {
        return await signIn(oauth, upstreamOf(upstream), parsed);
      }
      const details = await oauth.describeConsent(parsed);
      const consent = await oauth.beginConsent(parsed);
      consent.headers.set("Content-Type", "text/html; charset=utf-8");
      return new Response(consentPage(details, consent.handle), { headers: consent.headers });
    }
    if (request.method === "POST") {
      // The consent form, and nothing larger: the provider reads only the binding
      // cookie from the request, so its body is ours to read once.
      if (mediaType(request) !== "application/x-www-form-urlencoded") {
        return text(415, "The consent form is posted as a form.");
      }
      const body = await boundedText(request, AUTHORIZE_BODY_MAX);
      if (body === null) {
        return text(413, TOO_LARGE);
      }
      const form = new URLSearchParams(body);
      const handle = form.get("handle") ?? "";
      if (form.get("decision") !== "approve") {
        const denied = await oauth.denyConsent(request, handle);
        return new Response(null, { status: 302, headers: denied.headers });
      }
      // No `remember`: the approval is this authorization's alone. The grant's scopes
      // are cut to the catalogue when it is made.
      const approved = await oauth.approveConsent(request, handle);
      return await signIn(oauth, upstreamOf(upstream), approved.request, approved.headers);
    }
    return text(405, "method not allowed");
  } catch (error) {
    return failed(error);
  }
}

/**
 * The platform's explicit admit of a new owner at sign-up: the owner's object first (a
 * fresh one is pending), then the directory's record, so a failure between them admits it
 * again at the next sign-in (the admit is idempotent) and a record never names an owner
 * its object did not admit.
 */
async function admit(env: DoorsEnv, signedIn: SignIn, owner: string): Promise<boolean> {
  const platform = bindPlatform(env, await bindSignIn(env, signedIn));
  let state: string;
  try {
    ({ state } = await platform.stub.admissionSet(platform.binding, { admit: true }));
  } catch (error) {
    if (erasedRefusal(error)) {
      return false;  // marked erasing, or erased: never admitted again
    }
    throw error;
  }
  if (state === "pending") {
    throw new Error("the platform's admit left the owner pending");
  }
  await putOwner(env, owner, { s: "admitted", t: now() });
  return true;
}

/** The object's refusal of an owner being erased, or erased. */
function erasedRefusal(error: unknown): boolean {
  return error instanceof Error && error.name === "Refused"
    && (error.message === ERASING || error.message.startsWith("owner_erased:"));
}

/**
 * An erased owner's sign-in: refused, and its identity's deletion at the issuer tried again
 * (the erasure tried it first; a failure there leaves the identity, which this finishes).
 */
async function erasedSignIn(upstream: Upstream, env: DoorsEnv, signedIn: SignIn):
    Promise<void> {
  if (signedIn.issuer !== env.OWNER_ISSUER) {
    return;  // a foreign issuer's identity is that provider's slice's to delete
  }
  try {
    await upstream.forget(signedIn.subject);
  } catch {
    console.error("an erased owner's identity deletion failed again");  // no ids
  }
}

/**
 * Which vendor a signing-in client is (`apps.ts`): a CIMD client by its document's host,
 * which the provider fetched, verified only when every redirect it registered is that app's;
 * any other by the name it registered, which only it vouches for. Recorded in the grant's props, and by the owner's object at the client's
 * first call, so every agent can tell which of the owner's apps are connected.
 */
async function signedInVendor(oauth: OAuthHelpers, clientId: string, redirectUri: string):
    Promise<Vendor | null> {
  let client: Awaited<ReturnType<OAuthHelpers["lookupClient"]>>;
  try {
    client = await oauth.lookupClient(clientId);
  } catch (error) {
    if (!(error instanceof CimdFetchError)) {
      throw error;
    }
    // The document fetched at /authorize is gone now: only this sign-in's redirect is known,
    // which the provider checked against it then.
    client = null;
  }
  // Every redirect the client registered, the one this sign-in sends access to among them.
  const redirects = [...new Set([...(client?.redirectUris ?? []), redirectUri])];
  return vendorOf(clientId, clientId.startsWith("https://") ? null : client?.clientName ?? null,
                  redirects);
}

/** `GET /oauth/upstream/callback`: the verified sign-in becomes the grant. */
export async function callback(request: Request, env: DoorsEnv,
                               source: Upstream | (() => Upstream)): Promise<Response> {
  const oauth = authorizationServer(env).getOAuthApi(env);
  let resumed: Awaited<ReturnType<typeof oauth.finishUpstream<Verifier>>>;
  try {
    resumed = await oauth.finishUpstream<Verifier>(request);
  } catch (error) {
    return failed(error);
  }
  const upstream = upstreamOf(source);
  const { request: original, data, headers } = resumed;
  const params = new URL(request.url).searchParams;
  const code = params.get("code");
  if (params.get("error") !== null || code === null) {
    return redirect(authorizationErrorRedirect(original, "access_denied"), headers);
  }
  let signedIn: SignIn;
  try {
    signedIn = await upstream.finish({ code, code_verifier: data.verifier,
                                       redirect_uri: UPSTREAM_CALLBACK });
  } catch (error) {
    // The check that failed, by name, on the page and in the Worker's log: an upstream's
    // message names a check or a status, never a token or a secret (`upstream.ts`).
    const reason = error instanceof Error ? error.message : "the upstream sign-in failed";
    console.error(`the upstream sign-in failed: ${reason}`);
    return text(502, `The sign-in could not be completed: ${reason}.`, headers);
  }
  const at = now();
  if (!Number.isSafeInteger(signedIn.authenticated_at)
      || signedIn.authenticated_at < at - SIGN_IN_MAX_AGE_S
      || signedIn.authenticated_at > at + CLOCK_MARGIN_S) {
    return redirect(authorizationErrorRedirect(original, "access_denied",
                                               "sign in again"), headers);
  }
  const owner = await ownerOf(env, signedIn.issuer, signedIn.subject);
  const record = await readOwner(env, owner);
  if (record?.s === "erased") {
    await erasedSignIn(upstream, env, signedIn);
    return redirect(authorizationErrorRedirect(original, "access_denied",
                                               "this account was erased"), headers);
  }
  const control = await readControl(env.CONTROL);
  const scope = granted(original.scope);
  if (original.resource === MCP_RESOURCE && !firstParty(original)) {
    if (!outsideOpen(control, owner)) {
      return redirect(authorizationErrorRedirect(original, "temporarily_unavailable"), headers);
    }
    if (record === null && !await admit(env, signedIn, owner)) {
      return redirect(authorizationErrorRedirect(original, "access_denied",
                                                 "this account was erased"), headers);
    }
    const props: McpProps = {
      kind: "mcp", owner, ...await mcpOrigin(original.clientId, original.redirectUri),
      source_ref: original.clientId, authenticated_at: signedIn.authenticated_at,
      vendor: await signedInVendor(oauth, original.clientId, original.redirectUri),
    };
    // A re-authorization replaces the client's earlier grant from the same installation,
    // so one installation's sign-in never revokes another's and stale grants never pile
    // up. The provider's default does it per exact redirect URI for a CIMD client; a
    // loopback client's port changes per sign-in, so its earlier grants from any port of
    // the same installation go first.
    await replaceInstallation(env, owner, original.clientId, original.redirectUri);
    const { redirectTo } = await oauth.completeAuthorization({
      request: original, userId: owner, metadata: {}, scope, props });
    return redirect(redirectTo, headers);
  }
  if (original.resource === APP_RESOURCE && firstParty(original)) {
    if (!ownerOpen(control, owner)) {
      return redirect(authorizationErrorRedirect(original, "temporarily_unavailable"), headers);
    }
    if (record === null && !await admit(env, signedIn, owner)) {
      return redirect(authorizationErrorRedirect(original, "access_denied",
                                                 "this account was erased"), headers);
    }
    const { binding, stub } = await bindSignIn(env, signedIn);
    let session: string;
    try {
      ({ session } = await stub.sessionOpen(binding));
    } catch (error) {
      if (erasedRefusal(error)) {
        return redirect(authorizationErrorRedirect(original, "access_denied",
                                                   "this account was erased"), headers);
      }
      throw error;
    }
    const props: AppProps = { kind: "app", owner };
    // Each device keeps its own grant: the app on a phone and in a browser stay signed in
    // together, and sign-out revokes the presented token's grant alone (`app.ts`).
    const { redirectTo } = await oauth.completeAuthorization({
      request: original, userId: owner, metadata: {}, scope, props,
      revokeExistingGrants: false });
    headers.append("Set-Cookie", sessionCookie(session));
    return redirect(redirectTo, headers);
  }
  throw new Error("an authorization's resource and client crossed after consent");
}
