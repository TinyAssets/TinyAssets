/**
 * The live upstream sign-in (`oidc.ts`, slice L): the code exchange and the ID token's
 * checks against the issuer's JWKS, against a fake issuer that signs with a real RSA key.
 * Every refusal the brief names: a token with no `auth_time`, a wrong issuer; and the rest
 * of the checks (audience, nonce, signature, key, algorithm, time), the record's grammar,
 * the half-configured Worker, and the identity's deletion.
 */
import { describe, expect, it } from "vitest";

import { type DoorsEnv, now, UPSTREAM_CALLBACK } from "../src/config";
import { upstreamOf } from "../src/index";
import { oidcUpstream, upstreamConfig, type UpstreamConfig, verifiedIdToken } from "../src/oidc";
import { UNWIRED } from "../src/upstream";
import { doorsEnv } from "./support";

const ISSUER = "https://auth.tinyassets.test";
const CONFIG: UpstreamConfig = upstreamConfig(JSON.stringify({
  client_id: "client_01TEST",
  authorization_endpoint: `${ISSUER}/oauth2/authorize`,
  token_endpoint: `${ISSUER}/oauth2/token`,
  jwks_uri: `${ISSUER}/oauth2/jwks`,
  user_delete: "https://api.issuer.test/user_management/users/{sub}",
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

async function challengeOf(verifier: string): Promise<string> {
  return b64url(new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier))));
}

interface Issued {
  readonly token: string;
  readonly jwks: unknown;
  readonly nonce: string;
}

/** A token the fake issuer signs, `change` applied to its claims or header. */
async function issued(change: { claims?: Record<string, unknown>;
                                header?: Record<string, unknown>;
                                drop?: string[] } = {}): Promise<Issued> {
  const { keys, jwk } = await rsa();
  const nonce = await challengeOf("verifier-1");
  const claims: Record<string, unknown> = {
    iss: ISSUER, aud: CONFIG.client_id, sub: "user_01ALICE", nonce,
    iat: now() - 10, exp: now() + 300, auth_time: now() - 20, ...change.claims };
  for (const key of change.drop ?? []) {
    delete claims[key];
  }
  const token = await sign(keys.privateKey, { alg: "RS256", kid: "k1", typ: "JWT",
                                              ...change.header }, claims);
  return { token, nonce, jwks: { keys: [{ ...jwk, kid: "k1", use: "sig", alg: "RS256" }] } };
}

describe("the ID token", () => {
  it("is taken with every check: the subject, the issuer and the sign-in's own time", async () => {
    const { token, jwks, nonce } = await issued();
    const signed = await verifiedIdToken(token, ISSUER, CONFIG, nonce, jwks);
    expect(signed).toEqual({ issuer: ISSUER, subject: "user_01ALICE",
                             authenticated_at: expect.any(Number) });
    expect(signed.authenticated_at).toBeLessThan(now());
  });

  it.each([
    ["no auth_time (there is no clock fallback)", { drop: ["auth_time"] }, /auth_time/],
    ["an auth_time that is not whole seconds", { claims: { auth_time: 1.5 } }, /auth_time/],
    ["a wrong issuer", { claims: { iss: "https://evil.test" } }, /issuer/],
    ["the issuer with a trailing slash", { claims: { iss: `${ISSUER}/` } }, /issuer/],
    ["another audience", { claims: { aud: "client_other" } }, /client/],
    ["several audiences without our azp", { claims: { aud: [CONFIG.client_id, "x"] } }, /client/],
    ["another flow's nonce", { claims: { nonce: "other" } }, /nonce/],
    ["no nonce", { drop: ["nonce"] }, /nonce/],
    ["an expired token", { claims: { exp: now() - 60 } }, /expired/],
    ["a token issued in the future", { claims: { iat: now() + 600 } }, /future/],
    ["no subject", { drop: ["sub"] }, /subject/],
    ["HS256", { header: { alg: "HS256" } }, /RS256/],
    ["alg none", { header: { alg: "none" } }, /RS256/],
    ["an unknown key", { header: { kid: "k2" } }, /no one key/],
  ])("is refused with %s", async (_what, change, message) => {
    const { token, jwks, nonce } = await issued(change);
    await expect(verifiedIdToken(token, ISSUER, CONFIG, nonce, jwks)).rejects.toThrow(message);
  });

  it("is refused when another key signed it", async () => {
    const { token, nonce } = await issued();
    const other = await issued();
    await expect(verifiedIdToken(token, ISSUER, CONFIG, nonce, other.jwks))
      .rejects.toThrow(/signature/);
  });

  it("is refused when it is no JWT, or tampered", async () => {
    const { token, jwks, nonce } = await issued();
    await expect(verifiedIdToken(undefined, ISSUER, CONFIG, nonce, jwks)).rejects.toThrow();
    await expect(verifiedIdToken("a.b", ISSUER, CONFIG, nonce, jwks)).rejects.toThrow();
    const [head, , signature] = token.split(".");
    const forged = `${head}.${text({ iss: ISSUER, aud: CONFIG.client_id, sub: "user_01MALLORY",
                                     nonce, iat: now(), exp: now() + 60,
                                     auth_time: now() })}.${signature}`;
    await expect(verifiedIdToken(forged, ISSUER, CONFIG, nonce, jwks))
      .rejects.toThrow(/signature/);
  });
});

describe("the live upstream", () => {
  it("asks for a fresh sign-in with our challenge, state and callback, and the challenge as nonce",
     () => {
    const upstream = oidcUpstream(ISSUER, CONFIG, SECRET);
    const sent = new URL(upstream.authorizeUrl({ state: "s1", code_challenge: "c1",
                                                 redirect_uri: UPSTREAM_CALLBACK }));
    expect(`${sent.origin}${sent.pathname}`).toBe(CONFIG.authorization_endpoint);
    expect(Object.fromEntries(sent.searchParams)).toEqual({
      provider: "authkit", response_type: "code", client_id: CONFIG.client_id,
      redirect_uri: UPSTREAM_CALLBACK, scope: "openid", state: "s1", code_challenge: "c1",
      code_challenge_method: "S256", prompt: "login", nonce: "c1" });
  });

  it("exchanges the code with the verifier and the secret, then checks the ID token", async () => {
    const { token, jwks } = await issued();
    const seen: Request[] = [];
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      seen.push(request.clone());
      if (request.url === CONFIG.token_endpoint) {
        return Response.json({ access_token: "at", id_token: token });
      }
      if (request.url === CONFIG.jwks_uri) {
        return Response.json(jwks);
      }
      return new Response("no", { status: 404 });
    }) as typeof fetch;
    const upstream = oidcUpstream(ISSUER, CONFIG, SECRET, fetcher);
    const signed = await upstream.finish({ code: "code-1", code_verifier: "verifier-1",
                                           redirect_uri: UPSTREAM_CALLBACK });
    expect(signed.subject).toBe("user_01ALICE");
    const form = new URLSearchParams(await seen[0]!.text());
    expect(Object.fromEntries(form)).toEqual({
      grant_type: "authorization_code", code: "code-1", redirect_uri: UPSTREAM_CALLBACK,
      code_verifier: "verifier-1", client_id: CONFIG.client_id, client_secret: SECRET });
  });

  it("refuses an exchange the issuer refused, and a token for another verifier", async () => {
    const { token, jwks } = await issued();
    const answering = (status: number) => (async (input: RequestInfo | URL) =>
      String(input) === CONFIG.jwks_uri ? Response.json(jwks)
        : Response.json({ id_token: token }, { status })) as typeof fetch;
    await expect(oidcUpstream(ISSUER, CONFIG, SECRET, answering(400)).finish({
      code: "c", code_verifier: "verifier-1", redirect_uri: UPSTREAM_CALLBACK }))
      .rejects.toThrow(/400/);
    await expect(oidcUpstream(ISSUER, CONFIG, SECRET, answering(200)).finish({
      code: "c", code_verifier: "verifier-2", redirect_uri: UPSTREAM_CALLBACK }))
      .rejects.toThrow(/nonce/);
  });

  it("names a refused exchange's OAuth error code, and nothing else of its answer", async () => {
    const refusing = (body: unknown) => (async () =>
      Response.json(body, { status: 401 })) as unknown as typeof fetch;
    const finish = (body: unknown) => oidcUpstream(ISSUER, CONFIG, SECRET, refusing(body))
      .finish({ code: "c", code_verifier: "verifier-1", redirect_uri: UPSTREAM_CALLBACK });
    await expect(finish({ error: "invalid_client", error_description: `bad ${SECRET}` }))
      .rejects.toThrow(/^the upstream's token endpoint answered 401 \(invalid_client\)$/);
    await expect(finish({ error: SECRET })).rejects.toThrow(/^[^(]*answered 401$/);
  });

  it("deletes an identity by its subject, with the secret; gone already is done", async () => {
    const seen: Request[] = [];
    const answering = (status: number) => (async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Request(input, init));
      return new Response(null, { status });
    }) as typeof fetch;
    await oidcUpstream(ISSUER, CONFIG, SECRET, answering(202)).forget("user_01ALICE");
    await oidcUpstream(ISSUER, CONFIG, SECRET, answering(404)).forget("user_01ALICE");
    await expect(oidcUpstream(ISSUER, CONFIG, SECRET, answering(500)).forget("user_01ALICE"))
      .rejects.toThrow(/500/);
    expect(seen[0]!.method).toBe("DELETE");
    expect(seen[0]!.url).toBe("https://api.issuer.test/user_management/users/user_01ALICE");
    expect(seen[0]!.headers.get("Authorization")).toBe(`Bearer ${SECRET}`);
  });

  it("is never built without its secret", () => {
    expect(() => oidcUpstream(ISSUER, CONFIG, "")).toThrow(/WORKOS_API_KEY/);
  });
});

describe("the UPSTREAM record", () => {
  const base = { client_id: "c", authorization_endpoint: `${ISSUER}/a`,
                 token_endpoint: `${ISSUER}/t`, jwks_uri: `${ISSUER}/j`,
                 user_delete: "https://api.issuer.test/users/{sub}" };
  it.each([
    ["not JSON", "{"],
    ["an http endpoint", JSON.stringify({ ...base, token_endpoint: "http://auth.test/t" })],
    ["a flow parameter among its own", JSON.stringify({ ...base,
                                                        authorize_params: { prompt: "none" } })],
    ["a nonce of its own", JSON.stringify({ ...base, authorize_params: { nonce: "x" } })],
    ["a deletion URL without the subject", JSON.stringify({ ...base,
                                                           user_delete: "https://a.test/u" })],
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
