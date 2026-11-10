/**
 * The live upstream sign-in (`oidc.ts`): User Management's code exchange and its access
 * token's checks against the issuer's JWKS, against a fake issuer that signs with a real RSA
 * key. Every refusal the brief names fails closed: a wrong `iss` (named with the value seen),
 * a wrong `client_id`, no `auth_time`, a `sub` that is not the answer's `user.id`, a bad
 * signature; and the rest of the checks (`sid` required, a user's token, one key per `kid`,
 * the key's own `alg` and `use`, algorithm, time), the authorize URL's exact parameters, the
 * exchange's exact body sent to `api.workos.com` and never through a redirect, the record's
 * grammar (the secret's one origin among it), the
 * half-configured Worker, and the identity's deletion. An `auth_time` past the 600 s window
 * is the callback's to refuse (`oauth.test.ts`, "older than ten minutes").
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { type DoorsEnv, now, UPSTREAM_CALLBACK } from "../src/config";
import { upstreamOf } from "../src/index";
import { oidcUpstream, upstreamConfig, type UpstreamConfig, verifiedAccessToken } from "../src/oidc";
import { UNWIRED } from "../src/upstream";
import { doorsEnv } from "./support";

const ISSUER = "https://auth.tinyassets.test";
/** The one origin the secret may go to (`oidc.ts`); every fetch here is a fake. */
const API = "https://api.workos.com";
const CONFIG: UpstreamConfig = upstreamConfig(JSON.stringify({
  client_id: "client_01TEST",
  authorization_endpoint: `${API}/user_management/authorize`,
  token_endpoint: `${API}/user_management/authenticate`,
  jwks_uri: `${ISSUER}/oauth2/jwks`,
  token_issuer: `${API}/user_management/client_01TEST`,
  user_delete: `${API}/user_management/users/{sub}`,
  authorize_params: { provider: "authkit" },
}));
const SECRET = "sk_test_secret";

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_")
    .replace(/=+$/, "");
}

function text(value: unknown): string {
  return b64url(new TextEncoder().encode(JSON.stringify(value)));
}

async function rsa(): Promise<{ keys: CryptoKeyPair; jwk: JsonWebKey }> {
  const keys = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  const jwk = await crypto.subtle.exportKey("jwk", keys.publicKey) as JsonWebKey;
  return { keys, jwk };
}

async function sign(key: CryptoKey, header: unknown, claims: unknown): Promise<string> {
  const head = `${text(header)}.${text(claims)}`;
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key,
                                             new TextEncoder().encode(head));
  return `${head}.${b64url(new Uint8Array(signature))}`;
}

interface Issued {
  /** The token endpoint's answer: the access token and the user. */
  readonly answer: { access_token: string; user: Record<string, string> };
  readonly jwks: unknown;
}

/** An answer the fake issuer signs, `change` applied to its token's claims or header. */
async function issued(change: { claims?: Record<string, unknown>;
                                header?: Record<string, unknown>;
                                drop?: string[]; user?: string | null } = {}):
    Promise<Issued> {
  const { keys, jwk } = await rsa();
  const claims: Record<string, unknown> = {
    iss: CONFIG.token_issuer, client_id: CONFIG.client_id, sub: "user_01ALICE",
    sid: "session_01ALICE", iat: now() - 10, exp: now() + 300, auth_time: now() - 20,
    ...change.claims };
  for (const key of change.drop ?? []) {
    delete claims[key];
  }
  const token = await sign(keys.privateKey, { alg: "RS256", kid: "k1", typ: "JWT",
                                              ...change.header }, claims);
  // `user: null` answers a user with no id.
  const user = change.user === null ? {} : { id: change.user ?? "user_01ALICE" };
  return { answer: { access_token: token, user },
           jwks: { keys: [{ ...jwk, kid: "k1", use: "sig", alg: "RS256" }] } };
}

describe("the access token", () => {
  it("is taken with every check: the subject, the owner issuer and the sign-in's own time",
     async () => {
    const { answer, jwks } = await issued();
    const signed = await verifiedAccessToken(answer, ISSUER, CONFIG, jwks);
    expect(signed).toEqual({ issuer: ISSUER, subject: "user_01ALICE",
                             authenticated_at: expect.any(Number) });
    expect(signed.authenticated_at).toBeLessThan(now());
  });

  it("is taken as a user's token (sub_profile user)", async () => {
    const { answer, jwks } = await issued({ claims: { sub_profile: "user" } });
    expect((await verifiedAccessToken(answer, ISSUER, CONFIG, jwks)).subject)
      .toBe("user_01ALICE");
  });

  it("is refused under a key the JWKS lists twice, or one not for RS256 signing", async () => {
    const { answer, jwks } = await issued();
    const [key] = (jwks as { keys: Record<string, unknown>[] }).keys;
    const under = (keys: Record<string, unknown>[]) =>
      verifiedAccessToken(answer, ISSUER, CONFIG, { keys });
    // The right key listed twice under its kid: ambiguous, never the first one taken.
    await expect(under([key!, key!])).rejects.toThrow(/no one key/);
    await expect(under([key!, { ...key!, n: "AQAB" }])).rejects.toThrow(/no one key/);
    await expect(under([{ ...key!, alg: "RS512" }])).rejects.toThrow(/RS256 signing key/);
    await expect(under([{ ...key!, use: "enc" }])).rejects.toThrow(/RS256 signing key/);
    // Neither field is required: a key that names neither is taken.
    const { alg: _alg, use: _use, ...bare } = key!;
    expect((await under([bare])).subject).toBe("user_01ALICE");
  });

  it.each([
    ["no auth_time (there is no clock fallback)", { drop: ["auth_time"] }, /auth_time/],
    ["an auth_time that is not whole seconds", { claims: { auth_time: 1.5 } }, /auth_time/],
    ["the owner issuer as iss", { claims: { iss: ISSUER } }, /issuer/],
    ["the issuer with a trailing slash", { claims: { iss: `${CONFIG.token_issuer}/` } },
     /issuer/],
    ["no iss", { drop: ["iss"] }, /issuer is no plain string/],
    ["another client", { claims: { client_id: "client_other" } }, /client_id/],
    ["no client_id, only our aud", { drop: ["client_id"], claims: { aud: CONFIG.client_id } },
     /client_id/],
    ["an expired token", { claims: { exp: now() - 60 } }, /expired/],
    ["a token issued in the future", { claims: { iat: now() + 600 } }, /future/],
    ["no subject", { drop: ["sub"] }, /names no subject/],
    ["no subject, and no user.id either", { drop: ["sub"], user: null }, /names no subject/],
    ["a subject that is not the answer's user.id", { user: "user_01MALLORY" }, /user\.id/],
    ["no sid (a user's token always carries one)", { drop: ["sid"] }, /\(sid\)/],
    ["an empty sid", { claims: { sid: "" } }, /\(sid\)/],
    ["a sid that is no string", { claims: { sid: 7 } }, /\(sid\)/],
    ["a machine's token", { claims: { sub_profile: "m2m" } }, /not a user's/],
    ["a sub_type", { claims: { sub_type: "application" } }, /not a user's/],
    ["HS256", { header: { alg: "HS256" } }, /RS256/],
    ["alg none", { header: { alg: "none" } }, /RS256/],
    ["an unknown key", { header: { kid: "k2" } }, /no one key/],
  ])("is refused with %s", async (_what, change, message) => {
    const { answer, jwks } = await issued(change);
    await expect(verifiedAccessToken(answer, ISSUER, CONFIG, jwks)).rejects.toThrow(message);
  });

  it("names the issuer it saw, and the one expected, when they differ", async () => {
    const { answer, jwks } = await issued({ claims: { iss: "https://api.issuer.test/" } });
    await expect(verifiedAccessToken(answer, ISSUER, CONFIG, jwks)).rejects.toThrow(
      `the access token's issuer is "https://api.issuer.test/", not "${CONFIG.token_issuer}"`);
  });

  it("is refused when another key signed it", async () => {
    const { answer } = await issued();
    const other = await issued();
    await expect(verifiedAccessToken(answer, ISSUER, CONFIG, other.jwks))
      .rejects.toThrow(/signature/);
  });

  it("is refused when there is none, it is no JWT, or it was tampered with", async () => {
    const { answer, jwks } = await issued();
    await expect(verifiedAccessToken(undefined, ISSUER, CONFIG, jwks)).rejects.toThrow(
      /no access token/);
    await expect(verifiedAccessToken({ id_token: answer.access_token, user: answer.user },
                                     ISSUER, CONFIG, jwks)).rejects.toThrow(/no access token/);
    await expect(verifiedAccessToken({ ...answer, access_token: "a.b" }, ISSUER, CONFIG, jwks))
      .rejects.toThrow();
    const [head, , signature] = answer.access_token.split(".");
    const forged = `${head}.${text({ iss: CONFIG.token_issuer, client_id: CONFIG.client_id,
                                     sub: "user_01MALLORY", iat: now(), exp: now() + 60,
                                     auth_time: now() })}.${signature}`;
    await expect(verifiedAccessToken({ access_token: forged, user: { id: "user_01MALLORY" } },
                                     ISSUER, CONFIG, jwks)).rejects.toThrow(/signature/);
  });
});

describe("the live upstream", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** The live upstream, its issuer answered by `fetcher` in place of the global fetch. */
  const answeredBy = (fetcher: typeof fetch) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(fetcher);
    return oidcUpstream(ISSUER, CONFIG, SECRET);
  };

  it("asks for a fresh sign-in (max_age=0) with exactly our challenge, state and callback",
     () => {
    const upstream = oidcUpstream(ISSUER, CONFIG, SECRET);
    const sent = new URL(upstream.authorizeUrl({ state: "s1", code_challenge: "c1",
                                                 redirect_uri: UPSTREAM_CALLBACK }));
    expect(`${sent.origin}${sent.pathname}`).toBe(CONFIG.authorization_endpoint);
    expect([...sent.searchParams].sort()).toEqual(Object.entries({
      provider: "authkit", response_type: "code", client_id: CONFIG.client_id,
      redirect_uri: UPSTREAM_CALLBACK, state: "s1", code_challenge: "c1",
      code_challenge_method: "S256", max_age: "0" }).sort());
  });

  it("exchanges the code as JSON with exactly the verifier and the secret, then checks the " +
     "access token", async () => {
    const { answer, jwks } = await issued();
    const seen: Request[] = [];
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      seen.push(request.clone());
      if (request.url === CONFIG.token_endpoint) {
        return Response.json({ ...answer, refresh_token: "rt" });
      }
      if (request.url === CONFIG.jwks_uri) {
        return Response.json(jwks);
      }
      return new Response("no", { status: 404 });
    }) as typeof fetch;
    const upstream = answeredBy(fetcher);
    const signed = await upstream.finish({ code: "code-1", code_verifier: "verifier-1",
                                           redirect_uri: UPSTREAM_CALLBACK });
    expect(signed).toEqual({ issuer: ISSUER, subject: "user_01ALICE",
                             authenticated_at: expect.any(Number) });
    expect(seen.map((request) => `${request.method} ${request.url}`)).toEqual([
      `POST ${CONFIG.token_endpoint}`, `GET ${CONFIG.jwks_uri}`]);
    expect(seen[0]!.headers.get("Content-Type")).toBe("application/json");
    // The secret-bearing request never follows a redirect.
    expect(seen[0]!.redirect).toBe("manual");
    expect(JSON.parse(await seen[0]!.text())).toEqual({
      grant_type: "authorization_code", client_id: CONFIG.client_id, client_secret: SECRET,
      code: "code-1", code_verifier: "verifier-1" });
  });

  it("refuses an exchange the issuer refused, and an answer for another user", async () => {
    const { answer, jwks } = await issued({ user: "user_01MALLORY" });
    const answering = (status: number) => (async (input: RequestInfo | URL) =>
      String(input) === CONFIG.jwks_uri ? Response.json(jwks)
        : Response.json(answer, { status })) as typeof fetch;
    await expect(answeredBy(answering(400)).finish({
      code: "c", code_verifier: "verifier-1", redirect_uri: UPSTREAM_CALLBACK }))
      .rejects.toThrow(/400/);
    await expect(answeredBy(answering(200)).finish({
      code: "c", code_verifier: "verifier-1", redirect_uri: UPSTREAM_CALLBACK }))
      .rejects.toThrow(/user\.id/);
  });

  it("refuses a redirect from the token endpoint, sending the secret nowhere else", async () => {
    const seen: string[] = [];
    for (const status of [301, 302, 307, 308]) {
      const redirecting = (async (input: RequestInfo | URL) => {
        seen.push(String(input));
        return new Response(null, { status, headers: { Location: "https://evil.test/t" } });
      }) as typeof fetch;
      await expect(answeredBy(redirecting).finish({
        code: "c", code_verifier: "verifier-1", redirect_uri: UPSTREAM_CALLBACK }))
        .rejects.toThrow(new RegExp(`answered ${status}$`));
    }
    expect(seen).toEqual(Array(4).fill(CONFIG.token_endpoint));
  });

  it("names a refused exchange's error code, and nothing else of its answer", async () => {
    const refusing = (body: unknown) => (async () =>
      Response.json(body, { status: 400 })) as unknown as typeof fetch;
    const finish = (body: unknown) => answeredBy(refusing(body))
      .finish({ code: "c", code_verifier: "verifier-1", redirect_uri: UPSTREAM_CALLBACK });
    await expect(finish({ error: "invalid_client", error_description: `bad ${SECRET}` }))
      .rejects.toThrow(/^the upstream's token endpoint answered 400 \(invalid_client\)$/);
    await expect(finish({ code: "invalid_credentials", message: `bad ${SECRET}` }))
      .rejects.toThrow(/^the upstream's token endpoint answered 400 \(invalid_credentials\)$/);
    await expect(finish({ error: SECRET })).rejects.toThrow(/^[^(]*answered 400$/);
    await expect(finish({ code: SECRET })).rejects.toThrow(/^[^(]*answered 400$/);
  });

  it("deletes an identity by its subject, with the secret; gone already is done", async () => {
    const seen: Request[] = [];
    const answering = (status: number) => (async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Request(input, init));
      return new Response(null, { status });
    }) as typeof fetch;
    await answeredBy(answering(202)).forget("user_01ALICE");
    await answeredBy(answering(404)).forget("user_01ALICE");
    await expect(answeredBy(answering(500)).forget("user_01ALICE"))
      .rejects.toThrow(/500/);
    expect(seen[0]!.method).toBe("DELETE");
    expect(seen[0]!.url).toBe("https://api.workos.com/user_management/users/user_01ALICE");
    expect(seen[0]!.headers.get("Authorization")).toBe(`Bearer ${SECRET}`);
  });

  it("is never built without its secret", () => {
    expect(() => oidcUpstream(ISSUER, CONFIG, "")).toThrow(/WORKOS_API_KEY/);
  });
});

describe("the UPSTREAM record", () => {
  const base = { client_id: "c", authorization_endpoint: `${API}/a`,
                 token_endpoint: `${API}/t`, jwks_uri: `${ISSUER}/j`,
                 token_issuer: `${API}/user_management/c`,
                 user_delete: `${API}/users/{sub}` };
  it.each([
    ["not JSON", "{"],
    ["an http endpoint", JSON.stringify({ ...base, token_endpoint: "http://auth.test/t" })],
    ["a token endpoint off api.workos.com", JSON.stringify({ ...base,
                                                            token_endpoint: `${ISSUER}/t` })],
    ["a token endpoint on a lookalike host", JSON.stringify({
      ...base, token_endpoint: "https://api.workos.com.evil.test/t" })],
    ["a deletion URL off api.workos.com", JSON.stringify({ ...base,
                                                          user_delete: `${ISSUER}/u/{sub}` })],
    ["no token issuer", JSON.stringify({ ...base, token_issuer: undefined })],
    ["an http token issuer", JSON.stringify({ ...base, token_issuer: "http://auth.test" })],
    ["a flow parameter among its own", JSON.stringify({ ...base,
                                                        authorize_params: { prompt: "none" } })],
    ["a max_age of its own", JSON.stringify({ ...base, authorize_params: { max_age: "600" } })],
    ["a nonce of its own", JSON.stringify({ ...base, authorize_params: { nonce: "x" } })],
    ["a deletion URL without the subject", JSON.stringify({ ...base,
                                                           user_delete: `${API}/u` })],
    ["another key", JSON.stringify({ ...base, client_secret: "s" })],
  ])("is refused with %s", (_what, value) => {
    expect(() => upstreamConfig(value)).toThrow();
  });

  it("makes the Worker unwired while both halves are unset, and refuses half of them", () => {
    const unset = { ...doorsEnv, OWNER_ISSUER: "not-configured", UPSTREAM: "not-configured" };
    expect(upstreamOf(unset as DoorsEnv)).toBe(UNWIRED);
    expect(() => upstreamOf({ ...unset, OWNER_ISSUER: ISSUER } as DoorsEnv)).toThrow(/together/);
    expect(() => upstreamOf({ ...unset, UPSTREAM: JSON.stringify(base) } as DoorsEnv))
      .toThrow(/together/);
    const live = upstreamOf({ ...unset, OWNER_ISSUER: ISSUER, UPSTREAM: JSON.stringify(base),
                              WORKOS_API_KEY: SECRET } as DoorsEnv);
    expect(live).not.toBe(UNWIRED);
    expect(() => upstreamOf({ ...unset, OWNER_ISSUER: ISSUER, UPSTREAM: JSON.stringify(base),
                              WORKOS_API_KEY: "" } as DoorsEnv)).toThrow(/WORKOS_API_KEY/);
  });
});
