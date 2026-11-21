/**
 * Our authorization server (research §1; checklist 4, 10, 16, 17, 25): metadata, the
 * consent flow, `authenticated_at` fixed across refreshes, consent after a revoke, and
 * `invalid_grant` once revoked.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { mcpOrigin } from "../src/binder";
import { ACCESS_TOKEN_TTL_S, now, REFRESH_TOKEN_TTL_S } from "../src/config";
import {
  APP_RESOURCE, MCP_RESOURCE, ORIGIN, UPSTREAM_CALLBACK, WEB_REDIRECT,
} from "../../contract/src/identity";
import { authorizationServer } from "../src/oauth";
import {
  appPost, authorizeMcp, authorizeUrl, call, connect, consent, crossed, DARK, decide, doorsEnv,
  handleOf, Jar, legacyPost, legacyTool, NAVIGATE, OPEN, pkce, refresh, refuseOnce,
  registerClient, serveFirstPartyDocument, setControl, signInApp, type ToolResult,
  upstreamReturns, url,
} from "./support";
import { Refusal } from "../../contract/src/refusal";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** An MCP call that files the client's agent card, which records the binding it came with. */
function fileCard(token: string): Promise<ToolResult> {
  return legacyTool(token, "agent_register", { command_center_id: "cc", display_name: "Muse",
                                               delivery_method: "pull", recipe_id: null });
}

beforeEach(async () => {
  await setControl(OPEN);
});

describe("authorization server metadata", () => {
  it("advertises CIMD, public clients, S256 and the issuer parameter", async () => {
    const response = await call(new Request(url("/.well-known/oauth-authorization-server")));
    expect(response.status).toBe(200);
    const metadata = await response.json<Record<string, unknown>>();
    expect(metadata["issuer"]).toBe(ORIGIN);
    expect(metadata["client_id_metadata_document_supported"]).toBe(true);
    expect(metadata["token_endpoint_auth_methods_supported"]).toContain("none");
    expect(metadata["code_challenge_methods_supported"]).toEqual(["S256"]);
    expect(metadata["authorization_response_iss_parameter_supported"]).toBe(true);
    expect(metadata["authorization_endpoint"]).toBe(`${ORIGIN}/authorize`);
    expect(metadata["registration_endpoint"]).toBeUndefined();
  });

  it("serves the /mcp resource's metadata, and a 401 naming it", async () => {
    const meta = await call(new Request(url("/.well-known/oauth-protected-resource/mcp")));
    expect(await meta.json()).toMatchObject({ resource: MCP_RESOURCE,
                                              authorization_servers: [ORIGIN] });
    const challenge = await call(new Request(url("/mcp"), { method: "POST" }));
    expect(challenge.status).toBe(401);
    expect(challenge.headers.get("WWW-Authenticate")).toContain(
      `resource_metadata="${ORIGIN}/.well-known/oauth-protected-resource/mcp"`);
    // A Bearer challenge, as the handles canary's new side holds it.
    expect(challenge.headers.get("WWW-Authenticate")).toMatch(/^Bearer /);
  });

  it("challenges an anonymous /mcp with no body, and serves its document, behind a closed " +
     "control record", async () => {
    // deploy/canary.ts tells the sides apart by this empty body (the old
    // daemon's carries `authentication_required`), and the deploy kit's routes runs it
    // while the dark control record admits no one.
    await setControl(DARK);
    const challenge = await call(new Request(url("/mcp"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) }));
    expect(challenge.status).toBe(401);
    expect(await challenge.text()).toBe("");
    expect(challenge.headers.get("WWW-Authenticate")).toContain(
      `resource_metadata="${ORIGIN}/.well-known/oauth-protected-resource/mcp"`);
    const meta = await call(new Request(url("/.well-known/oauth-protected-resource/mcp")));
    expect(await meta.json()).toMatchObject({ resource: MCP_RESOURCE,
                                              authorization_servers: [ORIGIN] });
  });
});

describe("dynamic client registration", () => {
  const registration = (environment = doorsEnv) => call(new Request(url("/oauth/register"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_name: "Grok", redirect_uris: ["https://grok.test/cb"],
                           token_endpoint_auth_method: "none" }),
  }), environment);

  it("is off by default", async () => {
    expect((await registration()).status).toBe(404);
  });

  it("is on with the flag, taking only a vendor's own redirects (`brain.test.ts`); a client " +
     "registered for our own origin is refused at consent", async () => {
    const on = { ...doorsEnv, DCR_ENABLED: "true" };
    // Grok's redirects are not recorded yet, so its registration is refused, with our own
    // origin as with any other.
    expect((await registration(on)).status).toBe(400);
    const metadata = await (await call(new Request(
      url("/.well-known/oauth-authorization-server")), on)).json<Record<string, unknown>>();
    expect(metadata["registration_endpoint"]).toBe(`${ORIGIN}/oauth/register`);
    const { clientId: client_id } = await registerClient("Grok", WEB_REDIRECT);
    const { challenge } = await pkce();
    const page = await call(new Request(authorizeUrl(
      { clientId: client_id, redirectUri: WEB_REDIRECT }, challenge), { headers: { ...NAVIGATE } }),
      on);
    expect(page.status).toBe(400);
    expect(await page.text()).toBe("This app may not send access to TinyAssets itself.");
  });

  it("refuses a flag that is neither spelling", async () => {
    await expect(registration({ ...doorsEnv, DCR_ENABLED: "yes" })).rejects.toThrow(
      "DCR_ENABLED");
  });
});

describe("the authorization flow", () => {
  it("refuses an /authorize that is not a top-level navigation", async () => {
    const client = await registerClient();
    const { challenge } = await pkce();
    const response = await call(new Request(authorizeUrl(client, challenge)));
    expect(response.status).toBe(403);
  });

  it("shows consent, signs in upstream with max_age=0, and issues a code", async () => {
    const client = await registerClient("Muse <script>");
    const jar = new Jar();
    const { challenge } = await pkce();
    const page = await consent(jar, client, challenge);
    expect(page.status).toBe(200);
    expect(page.headers.get("X-Frame-Options")).toBe("DENY");
    const html = await page.text();
    expect(html).toContain("Muse &#60;script&#62;");
    expect(html).not.toContain("<script>");
    const approved = await decide(jar, handleOf(html), "approve");
    expect(approved.status).toBe(302);
    const upstream = new URL(approved.headers.get("Location")!);
    expect(upstream.searchParams.get("max_age")).toBe("0");
    expect(upstream.searchParams.has("prompt")).toBe(false);
    expect(upstream.searchParams.get("code_challenge_method")).toBe("S256");
    expect(upstream.searchParams.get("redirect_uri")).toBe(
      UPSTREAM_CALLBACK);
    const back = await upstreamReturns(jar, upstream.href, { owner: owner() });
    const redirect = new URL(back.headers.get("Location")!);
    expect(`${redirect.origin}${redirect.pathname}`).toBe(client.redirectUri);
    expect(redirect.searchParams.get("state")).toBe("client-state");
    expect(redirect.searchParams.get("iss")).toBe(ORIGIN);
    expect(redirect.searchParams.get("code")).toBeTruthy();
  });

  it("answers a denied consent with access_denied", async () => {
    const client = await registerClient();
    const jar = new Jar();
    const { challenge } = await pkce();
    const page = await consent(jar, client, challenge);
    const denied = await decide(jar, handleOf(await page.text()), "deny");
    expect(new URL(denied.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
  });

  it("refuses a consent form posted from another browser", async () => {
    const client = await registerClient();
    const { challenge } = await pkce();
    const page = await consent(new Jar(), client, challenge);
    const forged = await decide(new Jar(), handleOf(await page.text()), "approve");
    expect(forged.status).toBe(400);
  });

  it("refuses an upstream callback in another browser", async () => {
    const client = await registerClient();
    const jar = new Jar();
    const { challenge } = await pkce();
    const page = await consent(jar, client, challenge);
    const approved = await decide(jar, handleOf(await page.text()), "approve");
    const elsewhere = await upstreamReturns(new Jar(), approved.headers.get("Location")!,
                                            { owner: owner() });
    expect(elsewhere.status).toBe(400);
  });

  it("refuses a sign-in older than ten minutes", async () => {
    const client = await registerClient();
    const jar = new Jar();
    const { challenge } = await pkce();
    const page = await consent(jar, client, challenge);
    const approved = await decide(jar, handleOf(await page.text()), "approve");
    const stale = await upstreamReturns(jar, approved.headers.get("Location")!,
                                        { owner: owner(), authenticatedAt: now() - 601 });
    expect(new URL(stale.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
  });

  it("names a failed upstream check at the callback, on its page and in the log", async () => {
    // The issuer's token carries no auth_time, which the live upstream refuses (`oidc.ts`).
    const reason = "the access token carries no auth_time";
    const client = await registerClient();
    const jar = new Jar();
    const { challenge } = await pkce();
    const page = await consent(jar, client, challenge);
    const approved = await decide(jar, handleOf(await page.text()), "approve");
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const failed = await upstreamReturns(jar, approved.headers.get("Location")!,
                                           { owner: owner(), authenticatedAt: null });
      expect(failed.status).toBe(502);
      expect(await failed.text()).toBe(`The sign-in could not be completed: ${reason}.`);
      expect(logged).toHaveBeenCalledWith(`the upstream sign-in failed: ${reason}`);
    } finally {
      logged.mockRestore();
    }
  });

  it("never gives an outside client a first-party token, nor the app an MCP one", async () => {
    const client = await registerClient();
    const { challenge } = await pkce();
    const response = await call(new Request(
      authorizeUrl(client, challenge, APP_RESOURCE), { headers: { ...NAVIGATE } }));
    expect(new URL(response.headers.get("Location")!).searchParams.get("error"))
      .toBe("access_denied");
  });
});

describe("authenticated_at", () => {
  it("is the original sign-in, unmoved by two refreshes", async () => {
    const client = await registerClient();
    const alice = owner();
    const signedIn = now() - 200;
    const first = await connect(client, { owner: alice, authenticatedAt: signedIn });
    const second = await (await refresh(client, first.refresh_token)).json<{
      access_token: string; refresh_token: string }>();
    const third = await (await refresh(client, second.refresh_token)).json<{
      access_token: string; refresh_token: string }>();
    expect(third.access_token).not.toBe(first.access_token);
    expect((await fileCard(third.access_token)).isError).toBeUndefined();
    const origin = await mcpOrigin(client.clientId, client.redirectUri);
    expect((await crossed(alice, "registerPropose")).map((call) => call.binding)).toMatchObject([{
      origin, source: { kind: "mcp", ref: client.clientId }, authenticated_at: signedIn }]);
  });
});

describe("a revoke", () => {
  it("reaches the object first, then the provider: refresh is invalid_grant", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const client = await registerClient();
      const alice = owner();
      const tokens = await connect(client, { owner: alice });
      expect((await fileCard(tokens.access_token)).isError).toBeUndefined();
      const app = await signInApp({ owner: alice });
      const origin = await mcpOrigin(client.clientId, client.redirectUri);
      const revoked = await appPost("grants/revoke", app.access_token, null,
                                    { client: origin.client });
      expect(revoked.status).toBe(200);
      expect(await revoked.json()).toMatchObject({ client: origin.client, revoked: true,
                                                   grants: 1 });
      const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
      const left = (await oauth.listUserGrants(alice)).items
        .filter((grant) => grant.clientId === client.clientId);
      expect(left).toEqual([]);
      const refreshed = await refresh(client, tokens.refresh_token);
      expect(refreshed.status).toBe(400);
      expect(await refreshed.json()).toMatchObject({ error: "invalid_grant" });
      await expect(fileCard(tokens.access_token)).rejects.toThrow("401");
    } finally {
      restore();
    }
  });

  it("that the object refuses revokes nothing in the provider", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const client = await registerClient();
      const alice = owner();
      const tokens = await connect(client, { owner: alice });
      const app = await signInApp({ owner: alice });
      const refused = await appPost("grants/revoke", app.access_token, null,
                                    { client: "not a client" });
      expect(refused.status).toBe(400);
      expect((await refresh(client, tokens.refresh_token)).status).toBe(200);
    } finally {
      restore();
    }
  });

  it("is followed by the consent screen at the client's next authorization", async () => {
    const restore = serveFirstPartyDocument();
    try {
      const client = await registerClient();
      const alice = owner();
      await connect(client, { owner: alice });
      const app = await signInApp({ owner: alice });
      const origin = await mcpOrigin(client.clientId, client.redirectUri);
      await appPost("grants/revoke", app.access_token, null, { client: origin.client });
      const { challenge } = await pkce();
      const again = await consent(new Jar(), client, challenge);
      expect(again.status).toBe(200);
      expect(await again.text()).toContain("name=\"decision\" value=\"approve\"");
    } finally {
      restore();
    }
  });

  // That a revoke fences every sign-in before it, and a sign-in after lifts it, is the owner's
  // test on its clock (`owner/test/grants.test.ts`); here, the doors' answer to its refusal.
  it("is answered before any tool is listed, the profile the object's first answer",
     async () => {
    const client = await registerClient();
    const alice = owner();
    const before = await connect(client, { owner: alice, authenticatedAt: now() - 120 });
    await refuseOnce("toolProfile", alice,
                     new Refusal("refused", "a fresh sign-in is required after a revoke"));
    const refused = await legacyPost(before.access_token,
                                     { jsonrpc: "2.0", id: 1, method: "tools/list" });
    expect([refused.status, await refused.json()])
      .toEqual([403, { error: "a fresh sign-in is required after a revoke" }]);
    const after = await connect(client, { owner: alice, authenticatedAt: now() });
    expect((await fileCard(after.access_token)).isError).toBeUndefined();
  });
});

describe("authorizeMcp", () => {
  it("binds the grant to the user the upstream signed in", async () => {
    const client = await registerClient();
    const alice = owner();
    await authorizeMcp(client, { owner: alice });
    const oauth = authorizationServer(doorsEnv).getOAuthApi(doorsEnv);
    const grants = (await oauth.listUserGrants(alice)).items;
    expect(grants.map((grant) => [grant.clientId, grant.resource, grant.metadata]))
      .toEqual([[client.clientId, MCP_RESOURCE, {}]]);
  });
});
