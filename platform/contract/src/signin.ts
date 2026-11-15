/**
 * The owner's upstream sign-in, as the doors and the deploy kit both hold it: WorkOS User
 * Management's authorization-code flow with PKCE, for the environment's own client (ADR-032,
 * "the owner's sign-in"). The deploy kit writes the `UPSTREAM` record (`deploy/deploy.ts`
 * `upstreamFrom`) and checks it here before any deploy; the doors read it here at every
 * sign-in (`doors/src/index.ts`), and run the flow on it (`doors/src/oidc.ts`).
 *
 * - `UpstreamConfig` and `upstreamConfig`: the record and its one parser.
 * - `SECRET_ORIGIN`: the one origin the client secret (WorkOS's API key) is sent to.
 * - `authorizeUrl`: the upstream authorize URL the doors send a browser to, which the kit's
 *   early sign-in check fetches.
 * - `SIGN_IN_MAX_AGE_S`: how old a sign-in the doors take, which the kit's probe names.
 */
import { object } from "./checks.ts";

/** The `UPSTREAM` record: the issuer's endpoints and our client at it. */
export interface UpstreamConfig {
  readonly client_id: string;
  readonly authorization_endpoint: string;
  readonly token_endpoint: string;
  readonly jwks_uri: string;
  /** The access token's `iss`, exactly: the deploy derives it, in one place. */
  readonly token_issuer: string;
  /** The identity-deletion URL, `{sub}` in place of the subject. */
  readonly user_delete: string;
  /** A provider's own authorize parameters (AuthKit's `provider`), never a flow one. */
  readonly authorize_params?: Readonly<Record<string, string>>;
}

/**
 * The only origin the client secret (WorkOS's API key) is ever sent to: the record's token
 * endpoint and identity-deletion URL must be on it, so a mis-edited `UPSTREAM` cannot send
 * the key anywhere else.
 */
export const SECRET_ORIGIN = "https://api.workos.com";

/**
 * How old an upstream sign-in may be when the doors take it: the object opens a session
 * only for one at most this old, and a forced fresh sign-in is at most ten minutes long.
 */
export const SIGN_IN_MAX_AGE_S = 600;

/** The parameters our flow sets, or that would change what it asks: never a record's. */
const FLOW_PARAMS: readonly string[] = Object.freeze([
  "response_type", "client_id", "redirect_uri", "state", "code_challenge",
  "code_challenge_method", "max_age", "prompt", "scope", "nonce"]);
const HTTPS = /^https:\/\/[^\s/?#]+(?:\/[^\s?#]*)?$/;
const KEYS: readonly string[] = Object.freeze([
  "client_id", "authorization_endpoint", "token_endpoint", "jwks_uri", "token_issuer",
  "user_delete", "authorize_params"]);

function https(value: unknown, what: string): string {
  if (typeof value !== "string" || !HTTPS.test(value)) {
    throw new Error(`UPSTREAM's ${what} is an https URL`);
  }
  return value;
}

/** An https URL on `SECRET_ORIGIN`, the one origin the secret may go to. */
function secretBearing(value: unknown, what: string): string {
  const url = https(value, what);
  if (new URL(url).origin !== SECRET_ORIGIN) {
    throw new Error(`UPSTREAM's ${what} is on ${SECRET_ORIGIN}, the only origin the secret ` +
                    "is sent to");
  }
  return url;
}

/** The `UPSTREAM` record checked whole, or a throw naming what is wrong. */
export function upstreamConfig(text: string): UpstreamConfig {
  let record: unknown;
  try {
    record = JSON.parse(text);
  } catch {
    throw new Error("UPSTREAM is a JSON object");
  }
  if (!object(record)) {
    throw new Error("UPSTREAM is a JSON object");
  }
  if (Object.keys(record).some((key) => !KEYS.includes(key))) {
    throw new Error(`UPSTREAM holds only ${KEYS.join(", ")}`);
  }
  const clientId = record["client_id"];
  if (typeof clientId !== "string" || !/^[\x21-\x7e]{1,256}$/.test(clientId)) {
    throw new Error("UPSTREAM's client_id is visible ASCII");
  }
  const userDelete = secretBearing(record["user_delete"], "user_delete");
  if (userDelete.split("{sub}").length !== 2) {
    throw new Error("UPSTREAM's user_delete names {sub} once");
  }
  const params = record["authorize_params"] ?? {};
  if (!object(params) || Object.entries(params).some(([key, val]) => FLOW_PARAMS.includes(key)
                                                                     || typeof val !== "string")) {
    throw new Error("UPSTREAM's authorize_params are strings, never a flow parameter");
  }
  return Object.freeze({
    client_id: clientId,
    authorization_endpoint: https(record["authorization_endpoint"], "authorization_endpoint"),
    token_endpoint: secretBearing(record["token_endpoint"], "token_endpoint"),
    jwks_uri: https(record["jwks_uri"], "jwks_uri"),
    token_issuer: https(record["token_issuer"], "token_issuer"),
    user_delete: userDelete,
    authorize_params: Object.freeze({ ...(params as Record<string, string>) }),
  });
}

/**
 * The upstream authorize URL for `config` (`UPSTREAM`): `response_type=code`, our `state`,
 * the S256 challenge, `redirect_uri` and `max_age=0`, which forces a fresh sign-in; the
 * record's `authorize_params` add the provider's own (`provider=authkit`), never one of ours.
 */
export function authorizeUrl(config: UpstreamConfig, { state, code_challenge, redirect_uri }: {
  state: string; code_challenge: string; redirect_uri: string;
}): string {
  const url = new URL(config.authorization_endpoint);
  for (const [name, value] of Object.entries(config.authorize_params ?? {})) {
    url.searchParams.set(name, value);
  }
  for (const [name, value] of Object.entries({
    response_type: "code", client_id: config.client_id, redirect_uri, state,
    code_challenge, code_challenge_method: "S256", max_age: "0",
  })) {
    url.searchParams.set(name, value);
  }
  return url.href;
}
