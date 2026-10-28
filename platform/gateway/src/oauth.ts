/**
 * OAuth for connections (research §2.6, §7.2, §7.3): the code exchange, the refresh and
 * the provider-side revoke, each with the tokens only ever inside the gateway and the
 * sealed blob.
 *
 * - `exchange`: the authorization-code grant with PKCE (RFC 6749 §4.1.3, RFC 7636 §4.5),
 *   with the RFC 8707 resource when the source names one, sealed at once.
 * - `refresh`: a source whose record says `refresh_with: "provider"` refreshes through
 *   pi-ai's own OAuth for its provider (Sign in with ChatGPT: pi-ai's
 *   `openaiChatGPTOAuth.refresh`, which keeps OpenAI's rotation rules), so that provider
 *   code stays in the library; any other source uses the generic refresh-token grant
 *   (RFC 6749 §6). The result is resealed at the next version, which the owner object
 *   stores by compare-and-swap under its refresh lease, so a rotating refresh token is
 *   used once.
 * - `revokeAtProvider`: RFC 7009 token revocation of the refresh token (which revokes its
 *   access tokens, §2.1), or of the access token when there is none.
 *
 * Every token endpoint is the one sealed or named by the source record; a redirect is
 * never followed; an answer's body is read only as the token response it must be, and
 * nothing from it reaches an error (`failure.ts`).
 */
import type { OAuthCredential } from "@earendil-works/pi-ai";
import { registerBunOAuthFlows } from "@earendil-works/pi-ai/bun-oauth";

import { type Catalog, providerFor } from "./catalog";
import { type Credential, type OAuthClient, type OAuthGrant, type Source, source } from "./credential";
import { type Code, Failure } from "./failure";
import { type Binding, type KeyringEnv, open, reseal, type Resealed, seal, type Sealed } from "./keyring";

/** How long one token-endpoint call may take (pi-ai's own refresh bound). */
export const TOKEN_DEADLINE_MS = 15 * 1000;

const TOKEN = /^[\x21-\x7e]{1,16384}$/;
const CODE = /^[\x21-\x7e]{1,2048}$/;
/** RFC 7636 §4.1. */
const VERIFIER = /^[A-Za-z0-9._~-]{43,128}$/;
const SCOPE = /^[\x20-\x7e]{0,4096}$/;

/** What the browser's callback brought back, and the flow's PKCE verifier. */
export interface AuthorizationGrant {
  readonly code: string;
  readonly verifier: string;
  readonly redirect_uri: string;
}

export interface Exchanged extends Sealed {
  readonly expires_at: number | null;
  readonly scope?: string;
}

export interface Refreshed extends Resealed {
  readonly expires_at: number | null;
}

interface TokenAnswer {
  readonly access: string;
  readonly refresh?: string;
  readonly expires_at: number | null;
  readonly scope?: string;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** RFC 6749 §2.3.1: the client id and secret are form-encoded before Basic auth. */
function basic(id: string, secret: string): string {
  const encode = (value: string) => encodeURIComponent(value).replace(/%20/g, "+");
  return `Basic ${btoa(`${encode(id)}:${encode(secret)}`)}`;
}

/** The code for a token endpoint's refusal: `invalid_grant` is the grant, else the call. */
async function refused(answer: Response): Promise<Code> {
  if (answer.status >= 500) {
    await answer.body?.cancel();
    return "oauth_unavailable";
  }
  let error: unknown;
  try {
    const body: unknown = await answer.json();
    error = object(body) ? body["error"] : undefined;
  } catch {
    error = undefined;
  }
  return error === "invalid_grant" ? "oauth_invalid_grant" : "oauth_rejected";
}

/** One POST to an OAuth endpoint, authenticated as the client. */
async function post(client: OAuthClient, url: string, params: Record<string, string>):
    Promise<Response> {
  const body = new URLSearchParams(params);
  const headers: Record<string, string> = {
    accept: "application/json", "content-type": "application/x-www-form-urlencoded",
  };
  const auth = client.client_auth;
  if (auth.method === "client_secret_basic") {
    headers["authorization"] = basic(client.client_id, auth.secret);
  } else {
    body.set("client_id", client.client_id);
    if (auth.method === "client_secret_post") {
      body.set("client_secret", auth.secret);
    }
  }
  try {
    return await fetch(url, { method: "POST", headers, body, redirect: "manual",
                              signal: AbortSignal.timeout(TOKEN_DEADLINE_MS) });
  } catch {
    throw new Failure("oauth_unreachable");
  }
}

/** A token endpoint's answer (RFC 6749 §5.1), checked; anything else is refused. */
async function token(client: OAuthClient, params: Record<string, string>): Promise<TokenAnswer> {
  const answer = await post(client, client.token_endpoint, {
    ...params, ...(client.resource === undefined ? {} : { resource: client.resource }),
  });
  if (answer.status !== 200) {
    throw new Failure(answer.status >= 300 && answer.status < 400
      ? "oauth_rejected" : await refused(answer));
  }
  let body: unknown;
  try {
    body = await answer.json();
  } catch {
    throw new Failure("oauth_malformed");
  }
  if (!object(body)) {
    throw new Failure("oauth_malformed");
  }
  const { access_token: access, token_type: type, expires_in: expires,
          refresh_token: refresh, scope } = body;
  if (typeof access !== "string" || !TOKEN.test(access)
      || typeof type !== "string" || type.toLowerCase() !== "bearer"
      || (expires !== undefined && (typeof expires !== "number" || !Number.isFinite(expires)
                                    || expires <= 0))
      || (refresh !== undefined && (typeof refresh !== "string" || !TOKEN.test(refresh)))
      || (scope !== undefined && (typeof scope !== "string" || !SCOPE.test(scope)))) {
    throw new Failure("oauth_malformed");
  }
  return {
    access,
    expires_at: expires === undefined ? null : Date.now() + Math.floor(expires * 1000),
    ...(refresh === undefined ? {} : { refresh }),
    ...(scope === undefined ? {} : { scope }),
  };
}

function oauthCredential(base: Source, grant: OAuthGrant): Credential {
  return { kind: "oauth2", upstream: base.upstream, endpoints: base.endpoints, oauth: grant,
           sealed_at: Date.now() };
}

/** Exchanges an authorization code for tokens and seals them for `bound`. */
export async function exchange(env: KeyringEnv, bound: Binding, grant: unknown,
                               record: unknown): Promise<Exchanged> {
  const checked = source(record, true);
  const client = checked.oauth;
  if (client === undefined || !object(grant)
      || Object.keys(grant).sort().join() !== "code,redirect_uri,verifier"
      || typeof grant["code"] !== "string" || !CODE.test(grant["code"])
      || typeof grant["verifier"] !== "string" || !VERIFIER.test(grant["verifier"])
      || typeof grant["redirect_uri"] !== "string") {
    throw new Failure("invalid_request");
  }
  let redirect: URL;
  try {
    redirect = new URL(grant["redirect_uri"]);
  } catch {
    throw new Failure("invalid_request");
  }
  if (redirect.protocol !== "https:" || redirect.hash !== "") {
    throw new Failure("invalid_request");
  }
  const answer = await token(client, {
    grant_type: "authorization_code", code: grant["code"], redirect_uri: redirect.href,
    code_verifier: grant["verifier"],
  });
  const sealed = await seal(env, bound, oauthCredential(checked, { ...client, ...answer }));
  return Object.freeze({ ...sealed, expires_at: answer.expires_at,
                         ...(answer.scope === undefined ? {} : { scope: answer.scope }) });
}

/** pi-ai's own refresh for the credential's provider (`refresh_with: "provider"`). */
async function providerRefresh(catalog: Catalog, credential: Extract<Credential, { kind: "oauth2" }>):
    Promise<TokenAnswer> {
  const auth = providerFor(catalog, credential.upstream).auth.oauth;
  const refresh = credential.oauth.refresh;
  if (auth === undefined || refresh === undefined) {
    throw new Failure("not_refreshable");
  }
  // pi-ai loads each provider's OAuth flow through an import a bundler cannot follow;
  // this registers the statically bundled flows instead. Each call registers the same
  // fixed table of pi-ai's flows: no owner's data is kept by it.
  registerBunOAuthFlows();
  const current: OAuthCredential = {
    type: "oauth", access: credential.oauth.access, refresh,
    expires: credential.oauth.expires_at ?? 0, clientId: credential.oauth.client_id,
    ...(credential.oauth.scope === undefined ? {} : { scopes: credential.oauth.scope.split(" ") }),
  };
  let next: OAuthCredential;
  try {
    next = await auth.refresh(current, AbortSignal.timeout(TOKEN_DEADLINE_MS));
  } catch {
    // pi-ai's message quotes the provider's body; only the fixed code goes on.
    throw new Failure("oauth_rejected");
  }
  if (typeof next.access !== "string" || !TOKEN.test(next.access)
      || typeof next.refresh !== "string" || !TOKEN.test(next.refresh)
      || typeof next.expires !== "number" || !Number.isFinite(next.expires)) {
    throw new Failure("oauth_malformed");
  }
  const scopes = next["scopes"];
  return {
    access: next.access, refresh: next.refresh, expires_at: Math.floor(next.expires),
    ...(Array.isArray(scopes) && scopes.every((scope) => typeof scope === "string")
      ? { scope: scopes.join(" ") } : {}),
  };
}

/**
 * Refreshes an OAuth credential and reseals it at `bound.version + 1`. The owner object
 * holds the refresh lease, so a rotating refresh token is sent once.
 */
export async function refresh(env: KeyringEnv, bound: Binding, blob: unknown,
                              catalog: Catalog): Promise<Refreshed> {
  const { credential } = await open(env, bound, blob);
  if (credential.kind !== "oauth2" || credential.oauth.refresh === undefined) {
    throw new Failure("not_refreshable");
  }
  const answer = credential.oauth.refresh_with === "provider"
    ? await providerRefresh(catalog, credential)
    : await token(credential.oauth, { grant_type: "refresh_token",
                                      refresh_token: credential.oauth.refresh });
  // RFC 6749 §6: a server that issues no new refresh token keeps the old one valid.
  const next: OAuthGrant = {
    ...credential.oauth, ...answer,
    refresh: answer.refresh ?? credential.oauth.refresh,
  };
  const resealed = await reseal(env, bound, { ...credential, oauth: next });
  return Object.freeze({ ...resealed, expires_at: next.expires_at });
}

/** Whether the provider was asked to revoke: false for an API key or a source with no endpoint. */
export interface Revoked {
  readonly revoked: boolean;
}

/** RFC 7009 revocation at the provider, for a connection being removed. */
export async function revokeAtProvider(env: KeyringEnv, bound: Binding,
                                       blob: unknown): Promise<Revoked> {
  const { credential } = await open(env, bound, blob);
  if (credential.kind !== "oauth2" || credential.oauth.revocation_endpoint === undefined) {
    return Object.freeze({ revoked: false });
  }
  const { refresh: refreshToken, access, revocation_endpoint: endpoint } = credential.oauth;
  const answer = await post(credential.oauth, endpoint, refreshToken === undefined
    ? { token: access, token_type_hint: "access_token" }
    : { token: refreshToken, token_type_hint: "refresh_token" });
  // RFC 7009 §2.2: 200 whether or not the token was still valid.
  if (answer.status !== 200) {
    throw new Failure(answer.status >= 300 && answer.status < 400
      ? "oauth_rejected" : await refused(answer));
  }
  await answer.body?.cancel();
  return Object.freeze({ revoked: true });
}
