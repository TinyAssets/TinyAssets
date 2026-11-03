/**
 * The owner's upstream sign-in, live (tasks 2, "Live AuthKit wiring"; slice L): an OpenID
 * Connect authorization-code flow with PKCE against the owner issuer, WorkOS AuthKit, as
 * data. Nothing here names a provider: the issuer is `OWNER_ISSUER`, and its endpoints,
 * client and identity-deletion URL are the `UPSTREAM` record, which the deploy fills from
 * the issuer's live discovery document after checking its `issuer` is `OWNER_ISSUER`
 * exactly (`deploy/deploy.mjs`). The client secret is the Worker secret `WORKOS_API_KEY`.
 *
 * - `authorizeUrl`: `response_type=code`, `scope=openid`, our `state`, the S256 challenge,
 *   `prompt=login`, and the challenge again as the ID token's `nonce`, so a token is bound
 *   to this flow as well as its code is; the record's `authorize_params` may add a
 *   provider's own parameter, never one of these.
 * - `finish`: the code exchanged at the token endpoint with the verifier and the client
 *   secret; then the ID token checked whole: RS256 under a key the issuer's JWKS names by
 *   `kid` (fetched for each sign-in: no module state), `iss` exactly `OWNER_ISSUER`, `aud`
 *   our client (with `azp` our client when it names several), `exp` and `iat` within the
 *   clock margin, `nonce` our challenge, `sub` present, and `auth_time` present as whole
 *   seconds. A token without `auth_time` is refused: there is no clock fallback, since the
 *   fence and the session's 600 s bound rest on the sign-in's real time.
 * - `forget`: the person's identity deleted at the issuer (the owner's erasure,
 *   `erase.ts`), by the record's `user_delete` URL with the subject in place of `{sub}`.
 *
 * Any failure throws: a sign-in is never taken on less than every check.
 */
import { CLOCK_MARGIN_S, now } from "./config";
import type { SignIn, Upstream } from "./upstream";

/** The `UPSTREAM` record: the issuer's endpoints and our client at it. */
export interface UpstreamConfig {
  readonly client_id: string;
  readonly authorization_endpoint: string;
  readonly token_endpoint: string;
  readonly jwks_uri: string;
  /** The identity-deletion URL, `{sub}` in place of the subject. */
  readonly user_delete: string;
  /** A provider's own authorize parameters (AuthKit's `provider`), never a flow one. */
  readonly authorize_params?: Readonly<Record<string, string>>;
}

/** The parameters our flow sets, which a record may never set. */
const FLOW_PARAMS: readonly string[] = Object.freeze([
  "response_type", "client_id", "redirect_uri", "scope", "state", "code_challenge",
  "code_challenge_method", "prompt", "nonce"]);
const HTTPS = /^https:\/\/[^\s/?#]+(?:\/[^\s?#]*)?$/;
/** An ID token's largest size we read, and a JWKS's. */
const TOKEN_MAX = 16 * 1024;
const JWKS_MAX = 64 * 1024;

function https(value: unknown, what: string): string {
  if (typeof value !== "string" || !HTTPS.test(value)) {
    throw new Error(`UPSTREAM's ${what} is an https URL`);
  }
  return value;
}

/** The `UPSTREAM` record checked whole, or a throw naming what is wrong. */
export function upstreamConfig(text: string): UpstreamConfig {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("UPSTREAM is a JSON object");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("UPSTREAM is a JSON object");
  }
  const record = value as Record<string, unknown>;
  const allowed = ["client_id", "authorization_endpoint", "token_endpoint", "jwks_uri",
                   "user_delete", "authorize_params"];
  if (Object.keys(record).some((key) => !allowed.includes(key))) {
    throw new Error(`UPSTREAM holds only ${allowed.join(", ")}`);
  }
  const clientId = record["client_id"];
  if (typeof clientId !== "string" || !/^[\x21-\x7e]{1,256}$/.test(clientId)) {
    throw new Error("UPSTREAM's client_id is visible ASCII");
  }
  const userDelete = https(record["user_delete"], "user_delete");
  if (userDelete.split("{sub}").length !== 2) {
    throw new Error("UPSTREAM's user_delete names {sub} once");
  }
  const params = record["authorize_params"] ?? {};
  if (typeof params !== "object" || params === null || Array.isArray(params)
      || Object.entries(params).some(([key, val]) => FLOW_PARAMS.includes(key)
                                                    || typeof val !== "string")) {
    throw new Error("UPSTREAM's authorize_params are strings, never a flow parameter");
  }
  return Object.freeze({
    client_id: clientId,
    authorization_endpoint: https(record["authorization_endpoint"], "authorization_endpoint"),
    token_endpoint: https(record["token_endpoint"], "token_endpoint"),
    jwks_uri: https(record["jwks_uri"], "jwks_uri"),
    user_delete: userDelete,
    authorize_params: Object.freeze({ ...(params as Record<string, string>) }),
  });
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_")
    .replace(/=+$/, "");
}

function unbase64url(text: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) {
    throw new Error("the ID token is not base64url");
  }
  const padded = text.replace(/-/g, "+").replace(/_/g, "/")
    + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

function json(bytes: Uint8Array, what: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    value = null;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`the ID token's ${what} is not a JSON object`);
  }
  return value as Record<string, unknown>;
}

/** A response's JSON, read no further than `max` bytes: a larger one is refused while read. */
async function bounded(response: Response, max: number, what: string): Promise<unknown> {
  const reader = (response.body ?? new ReadableStream<Uint8Array>({ start: (c) => c.close() }))
    .getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    size += value.byteLength;
    if (size > max) {
      await reader.cancel().catch(() => undefined);
      throw new Error(`the upstream's ${what} is too large`);
    }
    parts.push(value);
  }
  const whole = new Uint8Array(size);
  let at = 0;
  for (const part of parts) {
    whole.set(part, at);
    at += part.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(whole));
  } catch {
    throw new Error(`the upstream's ${what} is not JSON`);
  }
}

function whole(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

/**
 * The verified sign-in an ID token is, under `issuer` and `config`, for `nonce`, its RS256
 * signature checked against the JWKS `keys` holds. Throws on anything short of every check.
 */
export async function verifiedIdToken(token: unknown, issuer: string, config: UpstreamConfig,
                                      nonce: string, keys: unknown, at: number = now()):
    Promise<SignIn> {
  if (typeof token !== "string" || token.length > TOKEN_MAX) {
    throw new Error("the upstream answered no ID token");
  }
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new Error("the ID token is not a signed JWT");
  }
  const [head, body, signature] = parts as [string, string, string];
  const header = json(unbase64url(head), "header");
  if (header["alg"] !== "RS256" || typeof header["kid"] !== "string") {
    throw new Error("the ID token is not RS256 under a named key");
  }
  const listed = (keys as { keys?: unknown } | null)?.keys;
  const matching = Array.isArray(listed)
    ? listed.filter((key) => typeof key === "object" && key !== null
                             && (key as Record<string, unknown>)["kid"] === header["kid"]) : [];
  if (matching.length !== 1) {
    throw new Error("the issuer's JWKS holds no one key the ID token names");
  }
  const jwk = matching[0] as Record<string, unknown>;
  if (jwk["kty"] !== "RSA" || typeof jwk["n"] !== "string" || typeof jwk["e"] !== "string"
      || (jwk["use"] !== undefined && jwk["use"] !== "sig")
      || (jwk["alg"] !== undefined && jwk["alg"] !== "RS256")) {
    throw new Error("the ID token's key is not an RS256 signing key");
  }
  const key = await crypto.subtle.importKey(
    "jwk", { kty: "RSA", n: jwk["n"], e: jwk["e"], alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const signed = new TextEncoder().encode(`${head}.${body}`);
  if (!await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, unbase64url(signature), signed)) {
    throw new Error("the ID token's signature does not verify");
  }
  const claims = json(unbase64url(body), "claims");
  if (claims["iss"] !== issuer) {
    throw new Error("the ID token's issuer is not the owner issuer");
  }
  const audience = claims["aud"];
  const audiences = typeof audience === "string" ? [audience]
    : Array.isArray(audience) ? audience : [];
  if (!audiences.includes(config.client_id)
      || ((audiences.length > 1 || claims["azp"] !== undefined)
          && claims["azp"] !== config.client_id)) {
    throw new Error("the ID token is not for our client");
  }
  if (!whole(claims["exp"]) || claims["exp"] <= at - CLOCK_MARGIN_S) {
    throw new Error("the ID token is expired");
  }
  if (!whole(claims["iat"]) || claims["iat"] > at + CLOCK_MARGIN_S) {
    throw new Error("the ID token was issued in the future");
  }
  if (claims["nonce"] !== nonce) {
    throw new Error("the ID token is not this sign-in's (nonce)");
  }
  const subject = claims["sub"];
  if (typeof subject !== "string" || subject === "") {
    throw new Error("the ID token names no subject");
  }
  if (!whole(claims["auth_time"])) {
    // No clock fallback: the fence and the session's bound rest on the real sign-in time.
    throw new Error("the ID token carries no auth_time");
  }
  return { issuer, subject, authenticated_at: claims["auth_time"] };
}

async function challengeOf(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier))));
}

/**
 * The live upstream for `issuer` (`OWNER_ISSUER`) and `config` (`UPSTREAM`), with the
 * client secret; `fetcher` reaches the issuer (the global fetch, or a test's fake).
 */
export function oidcUpstream(issuer: string, config: UpstreamConfig, secret: string,
                             fake?: typeof fetch): Upstream {
  const fetcher = (url: string, init: RequestInit): Promise<Response> =>
    fake === undefined ? fetch(url, init) : fake(url, init);
  if (typeof secret !== "string" || secret === "") {
    throw new Error("WORKOS_API_KEY, the upstream's client secret, is not set");
  }
  return Object.freeze({
    authorizeUrl: ({ state, code_challenge, redirect_uri }: {
      state: string; code_challenge: string; redirect_uri: string;
    }): string => {
      const url = new URL(config.authorization_endpoint);
      for (const [name, value] of Object.entries(config.authorize_params ?? {})) {
        url.searchParams.set(name, value);
      }
      for (const [name, value] of Object.entries({
        response_type: "code", client_id: config.client_id, redirect_uri, scope: "openid",
        state, code_challenge, code_challenge_method: "S256", prompt: "login",
        nonce: code_challenge,
      })) {
        url.searchParams.set(name, value);
      }
      return url.href;
    },
    finish: async ({ code, code_verifier, redirect_uri }: {
      code: string; code_verifier: string; redirect_uri: string;
    }): Promise<SignIn> => {
      const exchanged = await fetcher(config.token_endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded",
                   Accept: "application/json" },
        body: new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri,
                                    code_verifier, client_id: config.client_id,
                                    client_secret: secret }),
        redirect: "manual",
      });
      if (exchanged.status !== 200) {
        throw new Error(`the upstream's token endpoint answered ${exchanged.status}`);
      }
      const answer = await bounded(exchanged, TOKEN_MAX * 4, "token answer");
      const keys = await fetcher(config.jwks_uri, { headers: { Accept: "application/json" },
                                                    redirect: "manual" });
      if (keys.status !== 200) {
        throw new Error(`the issuer's JWKS answered ${keys.status}`);
      }
      return verifiedIdToken((answer as { id_token?: unknown } | null)?.id_token, issuer,
                             config, await challengeOf(code_verifier),
                             await bounded(keys, JWKS_MAX, "JWKS"));
    },
    forget: async (subject: string): Promise<void> => {
      const url = config.user_delete.replace("{sub}", encodeURIComponent(subject));
      const done = await fetcher(url, { method: "DELETE", redirect: "manual",
                                        headers: { Authorization: `Bearer ${secret}` } });
      if (done.status !== 404 && (done.status < 200 || done.status > 299)) {
        throw new Error(`the issuer's identity deletion answered ${done.status}`);
      }
    },
  });
}
