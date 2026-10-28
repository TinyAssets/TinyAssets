/**
 * The binder (research checklist 1, 2, 9, 10): every binding field from the credential,
 * a forged owner, caller or origin in the request ignored, and one owner's credential
 * never reaching another owner's object.
 */
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { apiKeyOrigin } from "../../owner/src/ledger";
import {
  apiKeyOrigin as doorsKeyOrigin, bindApiKey, bindKeyRegistration, bindMcp, bindOwner, bindSignIn, mcpClient, mcpOrigin,
} from "../src/binder";
import { APP_RESOURCE, MCP_RESOURCE, WEB_CLIENT } from "../src/config";
import { mintKeyFor } from "./keys";
import {
  appPost, call, connect, doorsEnv, legacyPost, OPEN, reached, registerClient, rpcBody,
  serveFirstPartyDocument, setControl, sqlIn, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

const FORGED = Object.freeze({ "X-Owner": "bob", "X-Caller": "bob", "X-Origin": "a".repeat(64),
                               "X-Authenticated-At": "9999999999" });

async function mcpToken(clientId: string, redirectUri: string, props: Record<string, unknown>) {
  const origin = await mcpOrigin(clientId, redirectUri);
  return {
    props: { kind: "mcp", owner: "alice", ...origin, source_ref: clientId,
             authenticated_at: 1_700_000_000, ...props },
    audience: MCP_RESOURCE, expiresAt: 2_000_000_000, scope: [], userId: "alice", clientId,
  };
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

describe("an MCP binding", () => {
  it("is the grant's owner, the client's origin and source, and the original sign-in",
     async () => {
    const token = await mcpToken("muse-client", "https://muse.test/cb", {});
    const { binding } = await bindMcp(doorsEnv, token);
    expect(binding).toEqual({
      owner: "alice", caller: "alice",
      origin: await mcpOrigin("muse-client", "https://muse.test/cb"),
      source: { kind: "mcp", ref: "muse-client" }, authenticated_at: 1_700_000_000,
    });
  });

  it.each([
    ["props naming another owner", { owner: "bob" }],
    ["props naming another client", { source_ref: "other-client" }],
    ["an origin that is not the client's", { client: "a".repeat(64) }],
    ["a malformed family", { family: "x" }],
    ["no sign-in time", { authenticated_at: undefined }],
    ["a first-party grant", { kind: "app" }],
  ])("refuses %s", async (_what, change) => {
    const token = await mcpToken("muse-client", "https://muse.test/cb", change);
    await expect(bindMcp(doorsEnv, token)).rejects.toThrow();
  });

  it("refuses a token for the app", async () => {
    const token = { ...await mcpToken("muse-client", "https://muse.test/cb", {}),
                    audience: APP_RESOURCE };
    await expect(bindMcp(doorsEnv, token)).rejects.toThrow();
  });

  it("derives the client and family with their domain, our issuer and the client id", async () => {
    const sha = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
    expect(await mcpOrigin("c", "https://a.test/cb")).toEqual({
      client: sha("tinyassets-mcp-client\0https://tinyassets.io\0c"),
      family: sha("tinyassets-mcp-family\0https://tinyassets.io\0c\0https://a.test/cb"),
    });
  });

  it("derives the client from the issuer and client id alone", async () => {
    const one = await mcpOrigin("c", "https://a.test/cb");
    const two = await mcpOrigin("c", "https://b.test/cb");
    expect(one.client).toBe(two.client);
    expect(one.client).toBe(await mcpClient("c"));
    expect(one.family).not.toBe(two.family);
  });
});

describe("the other bindings", () => {
  it("derives an API key's origin exactly as the object does", () => {
    for (let i = 0; i < 50; i++) {
      const keyId = [...crypto.getRandomValues(new Uint8Array(8))]
        .map((b) => b.toString(16).padStart(2, "0")).join("");
      expect(doorsKeyOrigin(keyId)).toEqual(apiKeyOrigin(keyId));
    }
  });

  it("an API key: the entry's owner, the key's origin and its mint time", () => {
    const { binding } = bindApiKey(doorsEnv, "0123456789abcdef",
                                   { o: "alice", h: "f".repeat(64), c: 1_700_000_000 });
    expect(binding).toEqual({ owner: "alice", caller: "alice",
                              origin: apiKeyOrigin("0123456789abcdef"),
                              source: { kind: "api_key", ref: "0123456789abcdef" },
                              authenticated_at: 1_700_000_000 });
  });

  const app = { props: { kind: "app", owner: "alice" }, audience: APP_RESOURCE,
                expiresAt: 2_000_000_000, scope: [], userId: "alice", clientId: WEB_CLIENT };

  it("the owner: its own account, with the session only when one was attached", () => {
    expect(bindOwner(doorsEnv, app, undefined).binding)
      .toEqual({ owner: "alice", caller: "alice", origin: null });
    expect(bindOwner(doorsEnv, app, "s".repeat(43)).binding)
      .toEqual({ owner: "alice", caller: "alice", origin: null, session: "s".repeat(43) });
  });

  it.each([
    ["another client", { clientId: "https://evil.test/client.json" }],
    ["another user", { userId: "bob" }],
    ["the MCP resource", { audience: MCP_RESOURCE }],
  ])("the owner: never from a token of %s", (_what, change) => {
    expect(() => bindOwner(doorsEnv, { ...app, ...change }, undefined)).toThrow();
  });

  it("a key registration: the owner's own key only", () => {
    const entry = { o: "alice", h: "f".repeat(64), c: 1 };
    expect(bindKeyRegistration(doorsEnv, app, "0123456789abcdef", entry, "s".repeat(43)).binding)
      .toEqual({ owner: "alice", caller: "alice", origin: apiKeyOrigin("0123456789abcdef"),
                 source: { kind: "api_key", ref: "0123456789abcdef" }, authenticated_at: 1,
                 session: "s".repeat(43) });
    expect(() => bindKeyRegistration(doorsEnv, app, "0123456789abcdef",
                                     { ...entry, o: "bob" }, undefined)).toThrow();
  });

  it("a sign-in: the subject at its time", () => {
    expect(bindSignIn(doorsEnv, "alice", 5).binding)
      .toEqual({ owner: "alice", caller: "alice", origin: null, authenticated_at: 5 });
    expect(() => bindSignIn(doorsEnv, " alice", 5)).toThrow();
  });
});

describe("a forged owner, caller or origin in the request", () => {
  it("is ignored on /mcp: the call is the token's owner's alone", async () => {
    const alice = owner();
    const bob = owner();
    const tokens = await connect(await registerClient(), { owner: alice });
    const response = await call(new Request(url(`/mcp?owner=${bob}&caller=${bob}`), {
      method: "POST",
      headers: { ...FORGED, "X-Owner": bob, Authorization: `Bearer ${tokens.access_token}`,
                 "Content-Type": "application/json", Accept: "application/json, text/event-stream",
                 "MCP-Protocol-Version": "2025-11-25" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: {
        name: "agents_list", arguments: {}, owner: bob, _meta: { owner: bob } } }),
    }));
    expect(await rpcBody(response)).toMatchObject({ result: { structuredContent: { agents: [] } } });
    expect(await reached(alice)).toBe(true);
    expect(await reached(bob)).toBe(false);
  });

  it("is refused as an argument, never taken", async () => {
    const alice = owner();
    const bob = owner();
    const tokens = await connect(await registerClient(), { owner: alice });
    for (const field of ["owner", "caller", "origin"]) {
      const response = await legacyPost(tokens.access_token, {
        jsonrpc: "2.0", id: 1, method: "tools/call",
        params: { name: "collection_get", arguments: { collection: "c", row_id: "r",
                                                       [field]: bob } } });
      expect(await rpcBody(response)).toMatchObject({ result: { isError: true } });
    }
    expect(await reached(bob)).toBe(false);
  });

  it("is ignored on /api", async () => {
    const alice = owner();
    const bob = owner();
    const { key } = await mintKeyFor(alice);
    const response = await call(new Request(url(`/api/tools/agents_list?owner=${bob}`), {
      method: "POST", headers: { ...FORGED, "X-Owner": bob, Authorization: `Bearer ${key}`,
                                 "Content-Type": "application/json" }, body: "{}" }));
    expect(response.status).toBe(200);
    expect(await reached(bob)).toBe(false);
  });

  it("is ignored on the owner surface", async () => {
    const alice = owner();
    const bob = owner();
    const { token, session } = await mintKeyFor(alice);
    const response = await appPost("keys", token, session, {
      name: "forged", owner: bob, caller: bob,
      scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] },
      { ...FORGED, "X-Owner": bob });
    expect(response.status).toBe(200);
    const { key_id } = await response.json<{ key_id: string }>();
    expect(await env.DIRECTORY.get(`k:${key_id}`, { type: "json" }))
      .toMatchObject({ o: alice });
    expect(await reached(bob)).toBe(false);
  });
});

describe("one owner's credential", () => {
  it("never reaches another owner's object", async () => {
    const alice = owner();
    const bob = owner();
    const client = await registerClient();
    const aliceTokens = await connect(client, { owner: alice });
    const bobTokens = await connect(client, { owner: bob });
    for (const [tokens, who] of [[aliceTokens, alice], [bobTokens, bob]] as const) {
      const response = await legacyPost(tokens.access_token, { jsonrpc: "2.0", id: 1,
        method: "tools/call", params: { name: "agent_register", arguments: {
          command_center_id: "cc", display_name: who, delivery_method: "pull",
          recipe_id: null } } });
      expect(await rpcBody(response)).toMatchObject({ result: { structuredContent: {} } });
    }
    const cards = async (who: string) =>
      (await sqlIn(who, "SELECT request FROM agent_cards")).map((row) =>
        (JSON.parse(row["request"] as string) as { display_name: string }).display_name);
    expect(await cards(alice)).toEqual([alice]);
    expect(await cards(bob)).toEqual([bob]);
    expect(await sqlIn(alice, "SELECT owner FROM object_owner")).toEqual([{ owner: alice }]);
  });
});
