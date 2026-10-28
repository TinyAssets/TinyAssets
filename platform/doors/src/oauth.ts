/**
 * Our authorization server and the `/mcp` resource (research §1), on Workers OAuth
 * Provider 1.2.4, pinned exactly. Both roles run in this one Worker, so validating a
 * token is a local call and spends no service-binding call.
 *
 * - The issuer is `https://tinyassets.io`, not the identity vendor: AuthKit is only the
 *   upstream sign-in (`authorize.ts`), so a later vendor change never breaks a
 *   connector. Two resources: `/mcp` for outside clients and `/app` for the owner's own
 *   app (`FIRST_PARTY_CLIENTS`); each grant and token is bound to exactly one, and a new
 *   authorization must name it (no default resource is guessed).
 * - Client ID Metadata Documents are on, which needs the `global_fetch_strictly_public`
 *   compatibility flag (the config guard). Dynamic registration is the deprecated
 *   fallback, on only with `DCR_ENABLED`. An outside client naming a redirect on our own
 *   origin is refused at `/authorize`, however it registered (`authorize.ts`).
 * - `authenticated_at` is in the grant's encrypted props from the authorization on. No
 *   token exchange callback is installed, so a refresh carries the props unchanged and
 *   never moves it; token exchange (RFC 8693) is off, so no other client can take them.
 * - Consent is never remembered (`authorize.ts`): every authorization shows the consent
 *   screen, the first one after a revoke included.
 * - A revoke goes to the owner's object first, then here (`revoke.ts`); a refresh of a
 *   revoked grant is `invalid_grant`, the code Claude requires.
 *
 * The provider is built per request: a module may keep no state (the module-state
 * guard), and the provider's options are fixed facts, so nothing is lost.
 *
 * All of its state is in `OAUTH_KV`, apart from the directory: clients, grants, token
 * hashes and transactions. Grant `metadata` is plaintext there, so we put nothing in it;
 * `props` hold only the binding facts (`McpProps`, `AppProps`).
 */
import {
  OAuthAuthorizationServer, type OAuthResourceContext, OAuthResourceServer,
  type ValidatedAccessToken,
} from "@cloudflare/workers-oauth-provider";

import {
  ACCESS_TOKEN_TTL_S, APP_RESOURCE, dcrEnabled, type DoorsEnv, ISSUER, MCP_RESOURCE,
  REFRESH_TOKEN_TTL_S, SCOPES,
} from "./config";

export const REGISTRATION_ENDPOINT = "/oauth/register";

export function authorizationServer(env: DoorsEnv): OAuthAuthorizationServer<DoorsEnv> {
  return new OAuthAuthorizationServer<DoorsEnv>({
    issuer: ISSUER,
    resources: [MCP_RESOURCE, APP_RESOURCE],
    scopesSupported: [...SCOPES],
    clientIdMetadataDocumentEnabled: true,
    ...(dcrEnabled(env) ? { clientRegistrationEndpoint: REGISTRATION_ENDPOINT } : {}),
    accessTokenTTL: ACCESS_TOKEN_TTL_S,
    refreshTokenTTL: REFRESH_TOKEN_TTL_S,
    refreshTokenIdleTTL: REFRESH_TOKEN_TTL_S,
    allowTokenExchangeGrant: false,
  });
}

/** What the resource host verified, as the binder takes it. */
export function validated(ctx: OAuthResourceContext<unknown>): ValidatedAccessToken<unknown> {
  const { audience, expiresAt, scope, userId, clientId } = ctx.auth;
  if (userId === undefined || clientId === undefined || expiresAt === undefined) {
    throw new Error("the authorization server reports every validated token's subject");
  }
  return { props: ctx.props, audience, expiresAt, scope, userId, clientId };
}

/**
 * The `/mcp` resource: RFC 9728 metadata, the `401` challenge naming it, and only a
 * token validated for `/mcp` reaching `handler`.
 */
export function mcpResource(
  handler: (request: Request, env: DoorsEnv, token: ValidatedAccessToken<unknown>) =>
    Promise<Response>,
): OAuthResourceServer<DoorsEnv, unknown> {
  return new OAuthResourceServer<DoorsEnv, unknown>({
    resourceMetadata: { resource: MCP_RESOURCE, authorization_servers: [ISSUER],
                        resource_name: "TinyAssets" },
    validateToken: (env) => (resource, token) =>
      authorizationServer(env).validateToken(resource, token, env),
    handler: { fetch: (request, env, ctx) => handler(request, env, validated(ctx)) },
  });
}
