/**
 * OAuth connections against fake token endpoints: the code exchange (RFC 6749 §4.1.3 with
 * PKCE), the refresh (generic, and pi-ai's own for Sign in with ChatGPT), and RFC 7009
 * revocation; each refusal a fixed code.
 */
import { describe, expect, it } from "vitest";

import { builtinCatalog } from "../src/catalog";
import type { Credential, OAuthGrant, Source } from "../src/credential";
import { Failure } from "../src/failure";
import { open, seal } from "../src/keyring";
import { exchange, refresh, revokeAtProvider, tokenGuard } from "../src/oauth";
import { ALICE, OPENAI, ring, sealed } from "./support";

const VERIFIER = "v".repeat(43);
const GRANT = Object.freeze({ code: "good-code", verifier: VERIFIER,
                              redirect_uri: "https://tinyassets.io/oauth/connect/callback" });

function oauthSource(oauth: Partial<Source["oauth"] & object> = {}): Source {
  return {
    upstream: OPENAI.upstream, endpoints: OPENAI.endpoints,
    oauth: { client_id: "client-1", client_auth: { method: "none" },
             token_endpoint: "https://auth.test/token",
             revocation_endpoint: "https://auth.test/revoke",
             resource: "https://api.openai.com/v1", refresh_with: "rfc6749", ...oauth },
  };
}

async function sealedGrant(env: ReturnType<typeof ring>, grant: Partial<OAuthGrant>,
                           record: Source = oauthSource()): Promise<string> {
  const plaintext: Credential = {
    kind: "oauth2", upstream: record.upstream, endpoints: record.endpoints, sealed_at: 1,
    oauth: { ...record.oauth!, access: "at-0", refresh: "rt-1", expires_at: 1, ...grant },
  };
  return (await seal(env, ALICE, plaintext)).blob;
}

async function code(run: () => Promise<unknown>): Promise<string> {
  const error = await run().then(() => undefined, (caught: unknown) => caught);
  expect(error).toBeInstanceOf(Failure);
  return (error as Failure).message;
}

/** Everything the fake's `evil.test` has received. */
async function evil(): Promise<unknown[]> {
  return (await (await fetch("https://status.test/evil")).json()) as unknown[];
}

function oauthOf(credential: Credential): OAuthGrant {
  if (credential.kind !== "oauth2") {
    throw new Error("not an OAuth credential");
  }
  return credential.oauth;
}

describe("exchange", () => {
  it("exchanges a code with PKCE and the resource, and seals the tokens", async () => {
    const env = ring("k1");
    const before = Date.now();
    const exchanged = await exchange(env, ALICE, GRANT, oauthSource());
    expect(exchanged.kid).toBe("k1");
    expect(exchanged.scope).toBe("models.read");
    expect(exchanged.expires_at).toBeGreaterThanOrEqual(before + 3_600_000);
    expect(exchanged.blob).not.toContain("at-1");
    const grant = oauthOf((await open(env, ALICE, exchanged.blob)).credential);
    expect(grant.access).toBe("at-1|none:client-1|https://api.openai.com/v1");
    expect(grant.refresh).toBe("rt-1");
    expect(grant.token_endpoint).toBe("https://auth.test/token");
  });

  it.each([
    ["client_secret_basic", "at-1|basic:client-1:s3cret|https://api.openai.com/v1"],
    ["client_secret_post", "at-1|post:client-1:s3cret|https://api.openai.com/v1"],
  ] as const)("authenticates a confidential client by %s", async (method, access) => {
    const env = ring("k1");
    const exchanged = await exchange(env, ALICE, GRANT,
                                     oauthSource({ client_auth: { method, secret: "s3cret" } }));
    expect(oauthOf((await open(env, ALICE, exchanged.blob)).credential).access).toBe(access);
  });

  it("answers a refused code with a fixed code, never the server's text", async () => {
    const env = ring("k1");
    const got = await code(() => exchange(env, ALICE, { ...GRANT, code: "stolen-code" }, oauthSource()));
    expect(got).toBe("oauth_invalid_grant");
    expect(got).not.toContain("stolen-code");
  });

  it.each([
    ["a short verifier", { ...GRANT, verifier: "short" }],
    ["an http redirect", { ...GRANT, redirect_uri: "http://tinyassets.io/cb" }],
    ["an extra field", { ...GRANT, state: "s" }],
    ["a code with a space", { ...GRANT, code: "a b" }],
  ])("refuses %s before any call", async (_what, grant) => {
    expect(await code(() => exchange(ring("k1"), ALICE, grant, oauthSource()))).toBe("invalid_request");
  });

  it("refuses a source with no OAuth client, or an http token endpoint", async () => {
    expect(await code(() => exchange(ring("k1"), ALICE, GRANT, OPENAI))).toBe("invalid_source");
    expect(await code(() => exchange(ring("k1"), ALICE, GRANT,
                                     oauthSource({ token_endpoint: "http://auth.test/token" }))))
      .toBe("invalid_source");
  });
});

describe("a key exchange (pkce_user_key_v1)", () => {
  function keySource(endpoint = "https://keys.test/api/v1/auth/keys"): Source {
    return { upstream: OPENAI.upstream, endpoints: OPENAI.endpoints,
             key_exchange: { endpoint } };
  }

  it("posts the code and verifier once as JSON, and seals the key it buys as an API key", async () => {
    const env = ring("k1");
    const exchanged = await exchange(env, ALICE, { ...GRANT, code: "key-code" }, keySource());
    expect(exchanged).toMatchObject({ kid: "k1", expires_at: null });
    expect(exchanged.blob).not.toContain("sk-bought");
    const opened = (await open(env, ALICE, exchanged.blob)).credential;
    expect(opened.kind === "api_key" && opened.secret).toBe(`sk-bought-${VERIFIER}`);
    expect(opened.upstream).toEqual(OPENAI.upstream);
  });

  it.each([
    ["a refused code", "bad-code", "oauth_rejected"],
    ["an answer with no key", "nokey-code", "oauth_malformed"],
    ["a redirect, never followed", "redirect-code", "oauth_rejected"],
    ["a server failure", "down-code", "oauth_unavailable"],
  ])("answers %s with a fixed code", async (_what, value, expected) => {
    const got = await code(() => exchange(ring("k1"), ALICE, { ...GRANT, code: value }, keySource()));
    expect(got).toBe(expected);
    expect(JSON.stringify(await evil())).not.toContain("/keys");
  });

  it("refuses an http or non-canonical exchange endpoint, or one beside an OAuth client", async () => {
    expect(await code(() => exchange(ring("k1"), ALICE, GRANT, keySource("http://keys.test/x"))))
      .toBe("invalid_source");
    expect(await code(() => exchange(ring("k1"), ALICE, GRANT, keySource("https://KEYS.test/x"))))
      .toBe("invalid_source");
    expect(await code(() => exchange(ring("k1"), ALICE, GRANT,
                                     { ...oauthSource(), key_exchange: { endpoint: "https://keys.test/x" } })))
      .toBe("invalid_source");
    expect(await code(() => exchange(ring("k1"), ALICE, { ...GRANT, verifier: "short" }, keySource())))
      .toBe("invalid_request");
  });
});

describe("the token endpoint's answer", () => {
  it("never follows a redirect from the token endpoint", async () => {
    const env = ring("k1");
    expect(await code(() => exchange(env, ALICE, { ...GRANT, code: "redirect-code" }, oauthSource())))
      .toBe("oauth_rejected");
    expect(JSON.stringify(await evil())).not.toContain("redirect-code");
  });

  it("refuses a token that is not a bearer token", async () => {
    expect(await code(() => exchange(ring("k1"), ALICE, { ...GRANT, code: "mac-code" }, oauthSource())))
      .toBe("oauth_malformed");
  });

  it("reads no more of an answer than its bound", async () => {
    expect(await code(() => exchange(ring("k1"), ALICE, { ...GRANT, code: "huge-code" }, oauthSource())))
      .toBe("oauth_malformed");
  });
});

describe("the scope pi-ai's own refresh runs in", () => {
  it("sends only a POST to the sealed token endpoint, never following a redirect", async () => {
    const guard = tokenGuard("https://auth.test/token");
    await expect(guard("https://auth.test/other", { method: "POST" })).rejects.toThrow("endpoint_refused");
    await expect(guard("https://evil.test/token", { method: "POST" })).rejects.toThrow("endpoint_refused");
    await expect(guard("https://auth.test/token", { method: "GET" })).rejects.toThrow("endpoint_refused");
    const answer = await guard("https://auth.test/token", {
      method: "POST", body: new URLSearchParams({ grant_type: "authorization_code",
                                                  code: "redirect-code", code_verifier: VERIFIER }) });
    expect(answer.status).toBe(307);
    expect(JSON.stringify(await evil())).not.toContain("/token");
  });
});

describe("pi-ai's own refresh, for Sign in with ChatGPT alone", () => {
  it.each([
    ["Anthropic's subscription", "anthropic", "https://console.anthropic.com/v1/oauth/token"],
    ["GitHub Copilot", "github-copilot", "https://github.com/login/oauth/access_token"],
    ["the legacy Codex source", "openai-codex", "https://auth.openai.com/oauth/token"],
    ["OpenAI with another token endpoint", "openai", "https://auth.test/token"],
  ])("refuses %s", async (_what, provider, endpoint) => {
    const record = oauthSource({ token_endpoint: endpoint, refresh_with: "provider" });
    const upstream = { ...record, upstream: { ...record.upstream, provider } };
    expect(await code(() => exchange(ring("k1"), ALICE, GRANT, upstream))).toBe("invalid_source");
    await expect(sealedGrant(ring("k1"), {}, upstream)).rejects.toThrow("credential_refused");
  });
});

describe("refresh", () => {
  it("refreshes by the refresh-token grant and reseals at the next version", async () => {
    const env = ring("k1");
    const blob = await sealedGrant(env, { refresh: "rt-1", scope: "models.read" });
    const refreshed = await refresh(env, ALICE, blob, builtinCatalog());
    expect(refreshed.version).toBe(2);
    expect(refreshed.expires_at).toBeGreaterThan(Date.now() + 1_700_000);
    const grant = oauthOf((await open(env, { ...ALICE, version: 2 }, refreshed.blob)).credential);
    expect(grant.access).toBe("at-2|none:client-1");
    expect(grant.refresh).toBe("rt-2");
    expect(grant.scope).toBe("openid offline_access chatgpt.tokens.use.direct");
    // The new blob is bound to version 2 only.
    expect(await code(() => open(env, ALICE, refreshed.blob))).toBe("credential_refused");
  });

  it("keeps the refresh token when the server issues no new one", async () => {
    const env = ring("k1");
    const blob = await sealedGrant(env, { refresh: "rt-keep" });
    const refreshed = await refresh(env, ALICE, blob, builtinCatalog());
    const grant = oauthOf((await open(env, { ...ALICE, version: 2 }, refreshed.blob)).credential);
    expect(grant.refresh).toBe("rt-keep");
  });

  it.each([
    ["a revoked refresh token", "rt-dead", "oauth_invalid_grant"],
    ["a server that is down", "rt-down", "oauth_unavailable"],
  ])("answers %s with a fixed code", async (_what, token, expected) => {
    const env = ring("k1");
    const blob = await sealedGrant(env, { refresh: token });
    const got = await code(() => refresh(env, ALICE, blob, builtinCatalog()));
    expect(got).toBe(expected);
    expect(got).not.toContain(token);
  });

  it("refreshes Sign in with ChatGPT through pi-ai's own refresh for the provider", async () => {
    const env = ring("k1");
    const record = oauthSource({ token_endpoint: "https://auth.openai.com/api/accounts/oauth/token",
                                 refresh_with: "provider" });
    const blob = await sealedGrant(env, { refresh: "rt-5" }, record);
    const before = Date.now();
    const refreshed = await refresh(env, ALICE, blob, builtinCatalog());
    const grant = oauthOf((await open(env, { ...ALICE, version: 2 }, refreshed.blob)).credential);
    // pi-ai sends the issued client id in the body (a public client) and keeps its own
    // three-minute expiry margin.
    expect(grant.access).toBe("at-6|none:client-1");
    expect(grant.refresh).toBe("rt-6");
    expect(refreshed.expires_at).toBeLessThan(before + 1_800_000 - 170_000);
    expect(refreshed.expires_at).toBeGreaterThan(before + 1_800_000 - 190_000);
    expect(grant.scope).toContain("chatgpt.tokens.use.direct");
  });

  it("holds pi-ai's own refresh to the sealed token endpoint: a redirect is refused, unfollowed", async () => {
    const env = ring("k1");
    const record = oauthSource({ token_endpoint: "https://auth.openai.com/api/accounts/oauth/token",
                                 refresh_with: "provider" });
    const blob = await sealedGrant(env, { refresh: "rt-hop" }, record);
    expect(await code(() => refresh(env, ALICE, blob, builtinCatalog()))).toBe("oauth_rejected");
    // Followed, the POST (the refresh token in its body) would have reached evil.test.
    expect(JSON.stringify(await evil())).not.toContain("evil.test/refresh");
  });

  it("answers pi-ai's own refresh failing with a fixed code", async () => {
    const env = ring("k1");
    const record = oauthSource({ token_endpoint: "https://auth.openai.com/api/accounts/oauth/token",
                                 refresh_with: "provider" });
    const blob = await sealedGrant(env, { refresh: "rt-dead" }, record);
    const got = await code(() => refresh(env, ALICE, blob, builtinCatalog()));
    expect(got).toBe("oauth_rejected");
  });

  it("refuses to refresh an API key, or a grant with no refresh token", async () => {
    const env = ring("k1");
    const key = await sealed(env, ALICE, "sk-1", OPENAI);
    expect(await code(() => refresh(env, ALICE, key, builtinCatalog()))).toBe("not_refreshable");
    const record = oauthSource();
    const plaintext: Credential = {
      kind: "oauth2", upstream: record.upstream, endpoints: record.endpoints, sealed_at: 1,
      oauth: { ...record.oauth!, access: "at-0", expires_at: null },
    };
    const blob = (await seal(env, ALICE, plaintext)).blob;
    expect(await code(() => refresh(env, ALICE, blob, builtinCatalog()))).toBe("not_refreshable");
  });
});

describe("revokeAtProvider", () => {
  it("revokes the refresh token at the sealed revocation endpoint", async () => {
    const env = ring("k1");
    const blob = await sealedGrant(env, { refresh: "rt-1" });
    expect(await revokeAtProvider(env, ALICE, blob)).toEqual({ revoked: true });
  });

  it("revokes the access token when there is no refresh token", async () => {
    const env = ring("k1");
    const record = oauthSource();
    const plaintext: Credential = {
      kind: "oauth2", upstream: record.upstream, endpoints: record.endpoints, sealed_at: 1,
      oauth: { ...record.oauth!, access: "at-0", expires_at: null },
    };
    const blob = (await seal(env, ALICE, plaintext)).blob;
    expect(await revokeAtProvider(env, ALICE, blob)).toEqual({ revoked: true });
  });

  it("asks nothing for an API key or a source with no revocation endpoint", async () => {
    const env = ring("k1");
    expect(await revokeAtProvider(env, ALICE, await sealed(env, ALICE, "sk-1", OPENAI)))
      .toEqual({ revoked: false });
    const record = oauthSource();
    const { revocation_endpoint: _r, ...client } = record.oauth!;
    const blob = await sealedGrant(env, {}, { ...record, oauth: client });
    expect(await revokeAtProvider(env, ALICE, blob)).toEqual({ revoked: false });
  });

  it("never follows a redirect from the revocation endpoint", async () => {
    const env = ring("k1");
    const blob = await sealedGrant(env, { refresh: "rt-redirect" });
    expect(await code(() => revokeAtProvider(env, ALICE, blob))).toBe("oauth_rejected");
    expect(JSON.stringify(await evil())).not.toContain("rt-redirect");
  });

  it("answers a revocation endpoint that is down with a fixed code", async () => {
    const env = ring("k1");
    const blob = await sealedGrant(env, { refresh: "rt-down" });
    expect(await code(() => revokeAtProvider(env, ALICE, blob))).toBe("oauth_unavailable");
  });

  it("refuses the blob under another owner", async () => {
    const env = ring("k1");
    const blob = await sealedGrant(env, {});
    expect(await code(() => revokeAtProvider(env, { ...ALICE, owner: "bob" }, blob)))
      .toBe("credential_refused");
  });
});
