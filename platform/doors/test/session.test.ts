/**
 * The session cookie and CSRF at the doors (research §8; checklist 6, 7, 8): the
 * attach matrix, the cookie never forwarded on `/mcp` or `/api`, outside credentials
 * refused on the owner surface before any object call, and the cookie's attributes.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { authorizationServer } from "../src/oauth";
import { sessionOf, withoutCookie } from "../src/session";
import {
  appPost, authorizeUrl, call, connect, doorsEnv, legacyPost, OPEN, pkce, reached,
  registerClient, rpcBody, serveFirstPartyDocument, setControl, signInApp, tokenCall, url,
} from "./support";

const COOKIE = "c".repeat(43);

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

interface Probe {
  readonly path?: string;
  readonly method?: string;
  readonly type?: string | null;
  readonly origin?: string | null;
  readonly site?: string | null;
  readonly cookie?: string | null;
}

function probe({ path = "/app/api/keys", method = "POST", type = "application/json",
                 origin = "https://tinyassets.io", site = "same-origin",
                 cookie = `__Host-ta-owner=${COOKIE}` }: Probe): Request {
  const headers = new Headers();
  for (const [name, value] of [["Content-Type", type], ["Origin", origin],
                               ["Sec-Fetch-Site", site], ["Cookie", cookie]] as const) {
    if (value !== null) {
      headers.set(name, value);
    }
  }
  return new Request(url(path), { method, headers,
                                  ...(method === "GET" || method === "HEAD" ? {} : { body: "{}" }) });
}

beforeEach(async () => {
  await setControl(OPEN);
});

describe("the session is attached", () => {
  it("to a same-origin JSON POST on the owner surface", () => {
    expect(sessionOf(probe({}))).toBe(COOKIE);
  });

  it.each([
    ["no Sec-Fetch-Site", { site: null }],
    ["a charset parameter", { type: "application/json; charset=utf-8" }],
    ["an uppercase media type", { type: "Application/JSON" }],
    ["a PUT", { method: "PUT" }],
    ["a DELETE", { method: "DELETE" }],
    ["another cookie beside it", { cookie: `theme=dark; __Host-ta-owner=${COOKIE}` }],
    ["a 32-character cookie", { cookie: `__Host-ta-owner=${"a".repeat(32)}` }],
    ["a 512-character cookie", { cookie: `__Host-ta-owner=${"a".repeat(512)}` }],
  ] as const)("with %s", (_what, change) => {
    expect(sessionOf(probe(change))).toBeDefined();
  });
});

describe("the session is not attached", () => {
  it.each([
    ["to /mcp", { path: "/mcp" }],
    ["to /api", { path: "/api/tools/agents_list" }],
    ["to /hooks", { path: "/hooks/inbound/t" }],
    ["to an /app page", { path: "/app/settings" }],
    ["to a path that only starts like the surface", { path: "/app/apiary" }],
    ["to a GET", { method: "GET" }],
    ["to a HEAD", { method: "HEAD" }],
    ["to an OPTIONS", { method: "OPTIONS" }],
    ["to a form post", { type: "application/x-www-form-urlencoded" }],
    ["to a text post", { type: "text/plain" }],
    ["to a multipart post", { type: "multipart/form-data; boundary=x" }],
    ["to a JSON-like type", { type: "application/json-patch+json" }],
    ["to a suffixed JSON type", { type: "application/json+x" }],
    ["with no Content-Type", { type: null }],
    ["with no Origin", { origin: null }],
    ["with another site's Origin", { origin: "https://evil.example" }],
    ["with an http Origin", { origin: "http://tinyassets.io" }],
    ["with a subdomain's Origin", { origin: "https://mcp.tinyassets.io" }],
    ["with an explicit port", { origin: "https://tinyassets.io:443" }],
    ["with a trailing slash", { origin: "https://tinyassets.io/" }],
    ["with an opaque Origin", { origin: "null" }],
    ["from the same site", { site: "same-site" }],
    ["from another site", { site: "cross-site" }],
    ["from a typed address", { site: "none" }],
    ["with no cookie", { cookie: null }],
    ["with the cookie twice", { cookie: `__Host-ta-owner=${COOKIE}; __Host-ta-owner=${COOKIE}` }],
    ["with a 31-character cookie", { cookie: `__Host-ta-owner=${"a".repeat(31)}` }],
    ["with a 513-character cookie", { cookie: `__Host-ta-owner=${"a".repeat(513)}` }],
    ["with only the sign-in flow's cookie", { cookie: `__Host-ta-owner-login=${COOKIE}` }],
    ["with the name lowercased", { cookie: `__host-ta-owner=${COOKIE}` }],
  ] as const)("%s", (_what, change) => {
    expect(sessionOf(probe(change))).toBeUndefined();
  });
});

describe("the Cookie header", () => {
  it("is removed from what /mcp, /api and /hooks see", () => {
    const stripped = withoutCookie(probe({ path: "/mcp" }));
    expect(stripped.headers.has("Cookie")).toBe(false);
    expect(stripped.headers.get("Origin")).toBe("https://tinyassets.io");
  });

  it("never rides an MCP call into the object", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const alice = owner();
      const app = await signInApp({ owner: alice });
      const tokens = await connect(await registerClient(), { owner: alice });
      // The owner's live cookie, sent along: the object refuses an outside call carrying
      // a session, so a forwarded cookie would fail this call.
      const listed = await legacyPost(tokens.access_token, {
        jsonrpc: "2.0", id: 1, method: "tools/call",
        params: { name: "agents_list", arguments: {} } },
        { Cookie: `__Host-ta-owner=${app.session}` });
      expect(listed.status).toBe(200);
      expect(await rpcBody(listed)).toMatchObject({ result: {
        structuredContent: { agents: [] } } });
    } finally {
      restore();
    }
  });
});

describe("the owner surface", () => {
  it("refuses an MCP token 403, before any object call", async () => {
    const alice = owner();
    const tokens = await connect(await registerClient(), { owner: alice });
    const response = await appPost("keys", tokens.access_token, COOKIE,
                                   { name: "k", scopes: [] });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual(
      { error: "an outside credential never reaches the owner's surface" });
    expect(await reached(alice)).toBe(false);
  });

  it("refuses an /app token issued to another client 403, before any object call",
     async () => {
    // Our /authorize never issues one; the provider itself can, so the surface holds too.
    const alice = owner();
    const client = await registerClient();
    const { verifier, challenge } = await pkce();
    const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
    const request = await oauth.parseAuthRequest(new Request(
      authorizeUrl(client, challenge, "https://tinyassets.io/app")));
    const { redirectTo } = await oauth.completeAuthorization({
      request, userId: alice, metadata: {}, scope: [], props: { kind: "app", owner: alice } });
    const exchanged = await tokenCall({ grant_type: "authorization_code",
      code: new URL(redirectTo).searchParams.get("code")!, redirect_uri: client.redirectUri,
      client_id: client.clientId, code_verifier: verifier });
    const { access_token } = await exchanged.json<{ access_token: string }>();
    const response = await appPost("keys", access_token, COOKIE, { name: "k", scopes: [] });
    expect(response.status).toBe(403);
    expect(await reached(alice)).toBe(false);
  });

  it("refuses an API key 403", async () => {
    const response = await appPost("keys", `ta_key_${"0".repeat(16)}_${"a".repeat(43)}`, COOKIE,
                                   { name: "k", scopes: [] });
    expect(response.status).toBe(403);
  });

  it("refuses no credential, or a cookie alone, 401", async () => {
    const response = await call(new Request(url("/app/api/keys"), {
      method: "POST", headers: { "Content-Type": "application/json", Origin: "https://tinyassets.io",
                                 Cookie: `__Host-ta-owner=${COOKIE}` }, body: "{}" }));
    expect(response.status).toBe(401);
  });

  it("widens only with the session the rules attach", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const app = await signInApp({ owner: owner() });
      const scopes = [{ universe: "cc", agent: "main", capability: "read_graph" }];
      const crossSite = await appPost("keys", app.access_token, app.session,
                                      { name: "laptop", scopes }, { "Sec-Fetch-Site": "cross-site" });
      expect(crossSite.status).toBe(403);
      expect(await crossSite.json()).toEqual({ error: "interactive owner session required" });
      const minted = await appPost("keys", app.access_token, app.session,
                                   { name: "laptop", scopes });
      expect(minted.status).toBe(200);
    } finally {
      restore();
    }
  });

  it("sets the session cookie with its exact attributes at sign-in", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const app = await signInApp({ owner: owner() });
      expect(app.session).toMatch(/^[A-Za-z0-9_-]{43}$/);
      const extended = await appPost("session/extend", app.access_token, app.session, {});
      expect(extended.status).toBe(200);
      expect(extended.headers.get("Set-Cookie")).toBe(
        `__Host-ta-owner=${app.session}; Secure; HttpOnly; Path=/; SameSite=Strict; Max-Age=28800`);
      const closed = await appPost("session/close", app.access_token, app.session, {});
      expect(await closed.json()).toEqual({ closed: 1 });
      expect(closed.headers.get("Set-Cookie")).toBe(
        "__Host-ta-owner=; Secure; HttpOnly; Path=/; SameSite=Strict; Max-Age=0");
      const after = await appPost("session/extend", app.access_token, app.session, {});
      expect(after.status).toBe(403);
    } finally {
      restore();
    }
  });

  it("takes only a JSON POST", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const app = await signInApp({ owner: owner() });
      const form = await appPost("keys", app.access_token, app.session, {},
                                 { "Content-Type": "text/plain" });
      expect(form.status).toBe(415);
    } finally {
      restore();
    }
  });
});
