/**
 * The slice 7 review's findings (L1-L7), each held by a test: an outside client never
 * sent access on our origin, sign-out ending the token, devices signed in together,
 * bounded bodies, and every property the review's mutants found untested.
 */
import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseKey } from "../src/api-keys";
import { mcpClient } from "../src/binder";
import {
  ACCESS_TOKEN_TTL_S, APP_RESOURCE, type DoorsEnv, MCP_RESOURCE, now, REFRESH_TOKEN_TTL_S,
  WEB_CLIENT, WEB_REDIRECT,
} from "../src/config";
import { authorizationServer } from "../src/oauth";
import { revokeClient } from "../src/revoke";
import { mintKeyFor } from "./keys";
import {
  appPost, authorizeMcp, authorizeUrl, call, connect, consent, decide, doorsEnv, handleOf, Jar, legacyPost,
  NAVIGATE, OPEN, pkce, reached, refresh, registerClient, rpcBody, serveFirstPartyDocument,
  setControl, signInApp, tokenCall, upstreamReturns, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

const AGENTS_LIST = Object.freeze({ jsonrpc: "2.0", id: 1, method: "tools/call",
                                    params: { name: "agents_list", arguments: {} } });

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore = serveFirstPartyDocument();
});

afterEach(() => {
  restore();
  vi.restoreAllMocks();
});

/** Serve a CIMD document at `clientId` when the provider fetches it. */
function serveDocument(clientId: string, document: Record<string, unknown>): void {
  const real = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = new Request(input, init);
    if (request.url === clientId) {
      return Response.json(document);
    }
    if (request.url === WEB_CLIENT) {
      return call(new Request(WEB_CLIENT));
    }
    return real(input, init);
  });
}

/** Through consent and approval to the upstream's URL. */
async function approved(): Promise<{ jar: Jar; location: string }> {
  const client = await registerClient();
  const jar = new Jar();
  const { challenge } = await pkce();
  const page = await consent(jar, client, challenge);
  const decided = await decide(jar, handleOf(await page.text()), "approve");
  return { jar, location: decided.headers.get("Location")! };
}

describe("L1: an outside client is never sent access on our own origin", () => {
  it("refuses a CIMD client naming a redirect on tinyassets.io, before consent", async () => {
    const evil = "https://evil.example/client.json";
    serveDocument(evil, { client_id: evil, client_name: "TinyAssets",
                          redirect_uris: [WEB_REDIRECT], token_endpoint_auth_method: "none" });
    const { challenge } = await pkce();
    const page = await call(new Request(authorizeUrl({ clientId: evil, redirectUri: WEB_REDIRECT },
                                                     challenge), { headers: { ...NAVIGATE } }));
    expect(page.status).toBe(400);
    const text = await page.text();
    expect(text).toBe("This app may not send access to TinyAssets itself.");
    expect(page.headers.getSetCookie()).toEqual([]);
  });

  it.each([
    ["another path on the apex", "https://tinyassets.io/elsewhere"],
    ["the mcp. subdomain", "https://mcp.tinyassets.io/cb"],
    ["a deeper subdomain", "https://a.b.tinyassets.io/cb"],
  ])("refuses a client naming a redirect on %s", async (_what, redirect) => {
    const evil = "https://evil.example/client.json";
    serveDocument(evil, { client_id: evil, client_name: "TinyAssets", redirect_uris: [redirect],
                          token_endpoint_auth_method: "none" });
    const { challenge } = await pkce();
    const page = await call(new Request(authorizeUrl({ clientId: evil, redirectUri: redirect },
                                                     challenge), { headers: { ...NAVIGATE } }));
    expect(page.status).toBe(400);
  });

  it("serves a client on a host that only ends like ours", async () => {
    const client = await registerClient("Look-alike", "https://nottinyassets.io/cb");
    const { challenge } = await pkce();
    const page = await call(new Request(authorizeUrl(client, challenge),
                                        { headers: { ...NAVIGATE } }));
    expect(page.status).toBe(200);
  });

  it("still serves a CIMD client on its own domain", async () => {
    const real = "https://claude.example/client.json";
    serveDocument(real, { client_id: real, client_name: "Claude",
                          redirect_uris: ["https://claude.example/cb"],
                          token_endpoint_auth_method: "none" });
    const { challenge } = await pkce();
    const page = await call(new Request(authorizeUrl(
      { clientId: real, redirectUri: "https://claude.example/cb" }, challenge),
      { headers: { ...NAVIGATE } }));
    expect(page.status).toBe(200);
    expect(await page.text()).toContain("Published by <strong>claude.example</strong>");
  });
});

describe("L2: sign-out ends the presented token", () => {
  it.each([
    ["keys/delete", { key_id: "0123456789abcdef" }],
    ["keys/revoke", { key_id: "0123456789abcdef" }],
    ["grants/revoke", { client: "a".repeat(64) }],
  ])("so the same token is 401 at %s", async (path, body) => {
    const app = await signInApp({ owner: owner() });
    const out = await appPost("session/close", app.access_token, app.session, {});
    expect(out.status).toBe(200);
    expect((await appPost(path, app.access_token, null, body)).status).toBe(401);
  });

  it("revokes the token even without the cookie, and leaves other devices signed in",
     async () => {
    const alice = owner();
    const phone = await signInApp({ owner: alice });
    const laptop = await signInApp({ owner: alice });
    const out = await appPost("session/close", phone.access_token, null, {});
    expect(await out.json()).toEqual({ closed: 0, signed_out: true });
    expect((await appPost("session/extend", phone.access_token, phone.session, {})).status)
      .toBe(401);
    expect((await appPost("session/extend", laptop.access_token, laptop.session, {})).status)
      .toBe(200);
  });
});

describe("sign-out at the edges", () => {
  /** The doors with token records that vanish on their second read, as at expiry. */
  function expiringAfterValidation(): DoorsEnv {
    const real = env.OAUTH_KV;
    const reads = new Map<string, number>();
    const kv = {
      get: (name: string, options: never) => {
        const seen = (reads.get(name) ?? 0) + 1;
        reads.set(name, seen);
        return name.startsWith("token:") && seen > 1 ? Promise.resolve(null)
                                                      : real.get(name, options);
      },
      put: real.put.bind(real), delete: real.delete.bind(real), list: real.list.bind(real),
      getWithMetadata: real.getWithMetadata.bind(real),
    } as unknown as KVNamespace;
    return { ...doorsEnv, OAUTH_KV: kv };
  }

  it("answers a token that expires after validation 401, not 500", async () => {
    const app = await signInApp({ owner: owner() });
    const response = await call(new Request(url("/app/api/session/close"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
                                 Authorization: `Bearer ${app.access_token}`,
                                 Cookie: `__Host-ta-owner=${app.session}` }, body: "{}" }),
      expiringAfterValidation());
    expect(response.status).toBe(401);
  });

  it("stays behind the maintenance gate", async () => {
    const app = await signInApp({ owner: owner() });
    await setControl({ ...OPEN, ordinary: "none" });
    expect((await appPost("session/close", app.access_token, app.session, {})).status).toBe(503);
  });
});

describe("loopback clients' grants", () => {
  const CLI = "https://cli.example/client.json";

  /** Connect a CIMD client for `who` on `redirect`. */
  async function connectCimd(who: string, redirect: string) {
    return connect({ clientId: CLI, redirectUri: redirect }, { owner: who });
  }

  async function grantsOf(who: string): Promise<string[]> {
    const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
    return (await oauth.listUserGrants(who)).items
      .filter((g) => g.clientId === CLI).map((g) => g.redirectUri ?? "");
  }

  it("are replaced per installation, whatever port each sign-in used", async () => {
    serveDocument(CLI, { client_id: CLI, client_name: "CLI",
                         redirect_uris: ["http://127.0.0.1/callback"],
                         token_endpoint_auth_method: "none" });
    const alice = owner();
    const first = await connectCimd(alice, "http://127.0.0.1:5555/callback");
    await connectCimd(alice, "http://127.0.0.1:6666/callback");
    expect(await grantsOf(alice)).toEqual(["http://127.0.0.1:6666/callback"]);
    expect((await refresh({ clientId: CLI, redirectUri: "" }, first.refresh_token)).status)
      .toBe(400);
  });

  it("keep each non-loopback installation's own grant", async () => {
    serveDocument(CLI, { client_id: CLI, client_name: "CLI",
                         redirect_uris: ["https://a.example/cb", "https://b.example/cb"],
                         token_endpoint_auth_method: "none" });
    const alice = owner();
    await connectCimd(alice, "https://a.example/cb");
    await connectCimd(alice, "https://b.example/cb");
    expect((await grantsOf(alice)).sort()).toEqual(["https://a.example/cb",
                                                    "https://b.example/cb"]);
  });
});

describe("L3: two devices stay signed in together", () => {
  it("keeps the first device's grant when a second signs in", async () => {
    const alice = owner();
    const first = await signInApp({ owner: alice });
    const second = await signInApp({ owner: alice });
    for (const device of [first, second]) {
      expect((await appPost("session/extend", device.access_token, device.session, {})).status)
        .toBe(200);
    }
  });

  it("keeps the provider's default for an outside client: a re-authorization replaces it",
     async () => {
    const client = await registerClient();
    const alice = owner();
    const first = await connect(client, { owner: alice });
    await connect(client, { owner: alice });
    expect((await refresh(client, first.refresh_token)).status).toBe(400);
  });
});

describe("L5: bounded bodies", () => {
  it("takes the consent POST only as a form", async () => {
    const response = await call(new Request(url("/authorize"), {
      method: "POST", headers: { "Content-Type": "text/plain" }, body: "x" }));
    expect(response.status).toBe(415);
    const json = await call(new Request(url("/authorize"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }));
    expect(json.status).toBe(415);
  });

  it("refuses a consent POST over 4 KiB 413", async () => {
    const response = await call(new Request(url("/authorize"), {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ handle: "h", decision: "approve", pad: "x".repeat(4096) }) }));
    expect(response.status).toBe(413);
  });

  it("refuses an /api body over 1 MiB 413, and a non-object 400", async () => {
    const { key } = await mintKeyFor(owner());
    const post = (body: string) => call(new Request(url("/api/tools/agents_list"), {
      method: "POST", headers: { Authorization: `Bearer ${key}`,
                                 "Content-Type": "application/json" }, body }));
    expect((await post(`{"p":"${"x".repeat(1024 * 1024)}"}`)).status).toBe(413);
    expect((await post("[1]")).status).toBe(400);
    expect((await post("{")).status).toBe(400);
  });

  it("refuses an /app/api body over 64 KiB 413, and a non-object 400", async () => {
    const app = await signInApp({ owner: owner() });
    const big = await appPost("keys", app.access_token, app.session,
                              { name: "k", pad: "x".repeat(64 * 1024) });
    expect(big.status).toBe(413);
    const list = await appPost("keys", app.access_token, app.session, ["k"]);
    expect(list.status).toBe(400);
  });

  it("refuses a declared length over the bound before reading", async () => {
    const response = await call(new Request(url("/authorize"), {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded",
                                 "Content-Length": "999999" }, body: "handle=h" }));
    expect(response.status).toBe(413);
  });
});

describe("L6 and the sign-in's bounds", () => {
  it.each([
    ["in the future past the margin", () => now() + 6],
    ["not whole seconds", () => now() - 10.5],
    ["older than ten minutes", () => now() - 601],
  ])("refuses a sign-in time %s", async (_what, at) => {
    const { jar, location } = await approved();
    const back = await upstreamReturns(jar, location, { owner: owner(), authenticatedAt: at() });
    expect(new URL(back.headers.get("Location")!).searchParams.get("error")).toBe("access_denied");
  });

  it("takes a sign-in inside the margin", async () => {
    const { jar, location } = await approved();
    const back = await upstreamReturns(jar, location, { owner: owner(), authenticatedAt: now() + 4 });
    expect(new URL(back.headers.get("Location")!).searchParams.get("code")).toBeTruthy();
  });
});

describe("L7: the sign-in flow", () => {
  it.each([
    ["only Sec-Fetch-Mode", { "Sec-Fetch-Mode": "navigate" }],
    ["only Sec-Fetch-Dest", { "Sec-Fetch-Dest": "document" }],
    ["a frame's navigation", { "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe" }],
  ])("refuses /authorize with %s", async (_what, headers) => {
    const client = await registerClient();
    const { challenge } = await pkce();
    expect((await call(new Request(authorizeUrl(client, challenge), { headers }))).status)
      .toBe(403);
  });

  it("answers a consent POST with no decision as a denial", async () => {
    const client = await registerClient();
    const jar = new Jar();
    const { challenge } = await pkce();
    const page = await consent(jar, client, challenge);
    const response = jar.take(await call(new Request(url("/authorize"), {
      method: "POST", headers: { Cookie: jar.header() },
      body: new URLSearchParams({ handle: handleOf(await page.text()) }) })));
    const location = new URL(response.headers.get("Location")!);
    expect(location.searchParams.get("error")).toBe("access_denied");
    expect(location.origin).toBe("https://client.example");
  });

  it("refuses an upstream error even when a code comes with it", async () => {
    const { jar, location } = await approved();
    const back = await upstreamReturns(jar, location, { owner: owner() }, doorsEnv,
                                       { error: "access_denied" });
    expect(new URL(back.headers.get("Location")!).searchParams.get("error")).toBe("access_denied");
    expect(new URL(back.headers.get("Location")!).searchParams.get("code")).toBeNull();
  });

  it("never gives a first-party client an MCP token", async () => {
    const { challenge } = await pkce();
    const response = await call(new Request(authorizeUrl(
      { clientId: WEB_CLIENT, redirectUri: WEB_REDIRECT }, challenge, MCP_RESOURCE),
      { headers: { ...NAVIGATE } }));
    expect(new URL(response.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
  });

  it("cuts a grant's scopes to the catalogue", async () => {
    const client = await registerClient();
    const jar = new Jar();
    const { verifier, challenge } = await pkce();
    const page = jar.take(await call(new Request(
      `${authorizeUrl(client, challenge)}&scope=${encodeURIComponent("admin offline_access")}`,
      { headers: { ...NAVIGATE } })));
    const decided = await decide(jar, handleOf(await page.text()), "approve");
    const back = await upstreamReturns(jar, decided.headers.get("Location")!, { owner: owner() });
    const code = new URL(back.headers.get("Location")!).searchParams.get("code")!;
    const tokens = await (await tokenCall({ grant_type: "authorization_code", code,
      redirect_uri: client.redirectUri, client_id: client.clientId, code_verifier: verifier }))
      .json<{ scope: string }>();
    expect(tokens.scope).toBe("offline_access");
  });

  it("serves the first-party document with exactly its one redirect", async () => {
    const response = await call(new Request(WEB_CLIENT));
    expect(await response.json()).toEqual({
      client_id: WEB_CLIENT, client_name: "TinyAssets", client_uri: "https://tinyassets.io",
      redirect_uris: [WEB_REDIRECT], token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"], response_types: ["code"] });
  });
});

describe("L7: the provider's pins", () => {
  it("advertises one scope and no token exchange, and refuses an exchange", async () => {
    const metadata = await (await call(new Request(
      url("/.well-known/oauth-authorization-server")))).json<Record<string, string[]>>();
    expect(metadata["scopes_supported"]).toEqual(["offline_access"]);
    expect(metadata["grant_types_supported"]).not.toContain(
      "urn:ietf:params:oauth:grant-type:token-exchange");
    const client = await registerClient();
    const tokens = await connect(client, { owner: owner() });
    const exchanged = await tokenCall({
      grant_type: "urn:ietf:params:oauth:grant-type:token-exchange", client_id: client.clientId,
      subject_token: tokens.access_token,
      subject_token_type: "urn:ietf:params:oauth:token-type:access_token" });
    expect(exchanged.status).toBe(400);
  });

  it("issues hour-long access tokens on a 30-day grant that slides when refreshed",
     async () => {
    const client = await registerClient();
    const alice = owner();
    const { code, verifier } = await authorizeMcp(client, { owner: alice });
    const issued = await (await tokenCall({ grant_type: "authorization_code", code,
      redirect_uri: client.redirectUri, client_id: client.clientId, code_verifier: verifier }))
      .json<{ expires_in: number; refresh_token: string }>();
    expect(issued.expires_in).toBe(ACCESS_TOKEN_TTL_S);
    expect(ACCESS_TOKEN_TTL_S).toBe(3_600);
    expect(REFRESH_TOKEN_TTL_S).toBe(30 * 86_400);
    const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
    const before = (await oauth.listUserGrants(alice)).items[0]!;
    expect(before.expiresAt! - before.createdAt).toBe(30 * 86_400);
    await new Promise((resolve) => setTimeout(resolve, 1_100));
    expect((await refresh(client, issued.refresh_token)).status).toBe(200);
    const after = (await oauth.listUserGrants(alice)).items[0]!;
    expect(after.expiresAt!).toBeGreaterThan(before.expiresAt!);
  });
});

describe("L7: the doors' routes and gates", () => {
  it.each(["null", "https://tinyassets.io", "https://evil.example"])(
    "refuses Origin %s on /mcp 403, with a valid token", async (origin) => {
      const alice = owner();
      const tokens = await connect(await registerClient(), { owner: alice });
      expect((await legacyPost(tokens.access_token, AGENTS_LIST, { Origin: origin })).status)
        .toBe(403);
      expect(await reached(alice)).toBe(false);
    });

  /** A sign-out POST to `target`, with the owner's live app token and cookie. */
  function signOutAt(target: string, app: { access_token: string; session: string }) {
    return call(new Request(target, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
                                 Authorization: `Bearer ${app.access_token}`,
                                 Cookie: `__Host-ta-owner=${app.session}` }, body: "{}" }));
  }

  it("answers every /hooks path 404, never the owner surface", async () => {
    const app = await signInApp({ owner: owner() });
    // As long as the owner surface's prefix, so a /hooks path read as it would name a route.
    expect((await signOutAt(url("/hooks/xxsession/close"), app)).status).toBe(404);
    expect((await appPost("session/extend", app.access_token, app.session, {})).status).toBe(200);
  });

  it("answers another host 404, whatever the path", async () => {
    const app = await signInApp({ owner: owner() });
    expect((await signOutAt("https://evil.example/app/api/session/close", app)).status).toBe(404);
    expect((await signOutAt("https://mcp.tinyassets.io/app/api/session/close", app)).status)
      .toBe(404);
    expect((await appPost("session/extend", app.access_token, app.session, {})).status).toBe(200);
  });

  it("takes only POST on the owner surface", async () => {
    const app = await signInApp({ owner: owner() });
    const response = await call(new Request(url("/app/api/keys"), {
      method: "PUT", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
                                Authorization: `Bearer ${app.access_token}` }, body: "{}" }));
    expect(response.status).toBe(405);
  });

  it("never sends a bug's detail to the client", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const broken = { getByName: () => ({ outsideAgents: () =>
      Promise.reject(new Error("db at 10.0.0.7 is down")) }) };
    const environment = { ...doorsEnv, OWNER: broken } as unknown as DoorsEnv;
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await legacyPost(tokens.access_token, AGENTS_LIST, {}, environment);
    const body = JSON.stringify(await rpcBody(response));
    expect(body).toContain("internal error");
    expect(body).not.toContain("10.0.0.7");
  });
});

describe("L7: the provider revoke reads every page", () => {
  it("revokes the client's grants on every page of the owner's grants", async () => {
    const alice = owner();
    const client = await registerClient();
    const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
    // Three grants of the client and two of another, as concurrent authorizations leave.
    for (const registered of [client, client, await registerClient("Other"), client]) {
      const { challenge } = await pkce();
      const request = await oauth.parseAuthRequest(new Request(authorizeUrl(registered, challenge)));
      await oauth.completeAuthorization({ request, userId: alice, metadata: {}, scope: [],
                                          props: { kind: "mcp" }, revokeExistingGrants: false });
    }
    const bound = { binding: { owner: alice, caller: alice, origin: null },
                    stub: env.OWNER.getByName(alice) };
    // The provider's grant listing, observed: each page holds the one grant asked for.
    const real = env.OAUTH_KV;
    const limits: unknown[] = [];
    const observed = { ...doorsEnv, OAUTH_KV: {
      get: real.get.bind(real), put: real.put.bind(real), delete: real.delete.bind(real),
      getWithMetadata: real.getWithMetadata.bind(real),
      list: (options: KVNamespaceListOptions) => {
        if (options.prefix?.startsWith("grant:")) {
          limits.push(options.limit);
        }
        return real.list(options);
      },
    } as unknown as KVNamespace };
    const done = await revokeClient(observed, bound, await mcpClient(client.clientId), 1);
    expect(done.grants).toBe(3);
    expect(limits.length).toBeGreaterThanOrEqual(4);
    expect(new Set(limits)).toEqual(new Set([1]));
    const left = (await oauth.listUserGrants(alice)).items.filter((g) => g.resource !== APP_RESOURCE);
    expect(left.map((g) => g.clientId)).not.toContain(client.clientId);
    expect(left).toHaveLength(1);
  });
});

describe("L7: the key store's edges", () => {
  it("keeps the key's end anchor", () => {
    const key = `ta_key_${"0".repeat(16)}_${"a".repeat(43)}`;
    expect(parseKey(key)).not.toBeNull();
    expect(parseKey(`${key}x`)).toBeNull();
    expect(parseKey(`${key}\n`)).toBeNull();
  });

  it.each([
    ["a hash that is not 64 hex", (e: Record<string, unknown>) => ({ ...e, h: "x" })],
    ["an extra field", (e: Record<string, unknown>) => ({ ...e, extra: 1 })],
  ])("fails loudly on a directory entry with %s", async (_what, alter) => {
    const { key, key_id } = await mintKeyFor(owner());
    const entry = await env.DIRECTORY.get<Record<string, unknown>>(`k:${key_id}`, { type: "json" });
    await env.DIRECTORY.put(`k:${key_id}`, JSON.stringify(alter(entry!)));
    await expect(call(new Request(url("/api/tools/agents_list"), {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: "{}" }))).rejects.toThrow("malformed");
  });

  it("revokes a live key whose entry this location cannot see yet", async () => {
    const minted = await mintKeyFor(owner());
    await env.DIRECTORY.delete(`k:${minted.key_id}`);
    const response = await appPost("keys/revoke", minted.token, null, { key_id: minted.key_id });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ key_id: minted.key_id, revoked: true });
  });

  it("frees a key's place when it is revoked", async () => {
    const minted = await mintKeyFor(owner());
    for (let i = 1; i < 25; i++) {
      expect((await appPost("keys", minted.token, minted.session, {
        name: `k${i}`, scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] }))
        .status).toBe(200);
    }
    await appPost("keys/revoke", minted.token, null, { key_id: minted.key_id });
    const again = await appPost("keys", minted.token, minted.session, {
      name: "again", scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] });
    expect(again.status).toBe(200);
  });
});
