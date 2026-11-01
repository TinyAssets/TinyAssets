/**
 * The Worker as its callers reach it: the RPC methods and `fetch`, over a real service
 * binding to the configured Worker (so only what RPC carries crosses), with the config's
 * keyring.
 */
import { createExecutionContext } from "cloudflare:test";
import { exports } from "cloudflare:workers";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { Failure, guarded } from "../src/failure";
import Gateway, { ConnectGateway, EgressGateway } from "../src/index";
import { open } from "../src/keyring";
import { ALICE, ANTHROPIC, envelopeOf, OPENAI, sealed } from "./support";

const gateway = (exports as unknown as { default: Service<Gateway> }).default;

async function rejected(run: () => Promise<unknown>): Promise<string> {
  const error = await run().then(() => undefined, (caught: unknown) => caught);
  expect(error).toBeInstanceOf(Error);
  return (error as Error).message;
}

describe("guarded", () => {
  it("lets only a fixed code out of an RPC method, never a raw error's text", async () => {
    const error = await guarded(async () => {
      throw new TypeError("provider prose quoting sk-raw-0123");
    }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(Failure);
    expect((error as Error).message).toBe("internal");
    const refused = await guarded(async () => {
      throw new Failure("endpoint_refused");
    }).catch((caught: unknown) => caught);
    expect((refused as Error).message).toBe("endpoint_refused");
  });
});

describe("the gateway Worker", () => {
  it("seals a pasted key over RPC, bound to the owner, connection and version", async () => {
    const { blob, kid } = await gateway.seal("alice", "c1", 1, "sk-rpc", ANTHROPIC);
    expect(kid).toBe("k2");
    const opened = await open(env, ALICE, blob);
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-rpc");
    expect(await rejected(() => open(env, { ...ALICE, owner: "bob" }, blob))).toBe("credential_refused");
  });

  it("refuses a malformed binding, key or source with only a fixed code", async () => {
    expect(await rejected(() => gateway.seal("", "c1", 1, "sk", ANTHROPIC))).toBe("invalid_binding");
    expect(await rejected(() => gateway.seal("alice", "c1", 0, "sk", ANTHROPIC))).toBe("invalid_binding");
    expect(await rejected(() => gateway.seal("alice", "c1", 1, "sk\r\nx-evil: 1", ANTHROPIC)))
      .toBe("invalid_source");
    expect(await rejected(() => gateway.seal("alice", "c1", 1, "sk",
      { ...ANTHROPIC, upstream: { ...ANTHROPIC.upstream, base_url: "https://api.openai.com" } })))
      .toBe("invalid_source");
  });

  it("reseals a blob under an older kid at the next version", async () => {
    const old = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
    const k1 = { GATEWAY_KEYRING: JSON.stringify({ active: "k1", keys: old.keys }) };
    const blob = await sealed(k1, ALICE, "sk-old", ANTHROPIC);
    const resealed = await gateway.reseal("alice", "c1", 1, blob);
    expect(resealed).toMatchObject({ kid: "k2", version: 2 });
    const opened = await open(env, { ...ALICE, version: 2 }, resealed.blob);
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-old");
  });

  it("verifies, refreshes and revokes over RPC with fixed codes", async () => {
    const blob = (await gateway.seal("alice", "c1", 1, "sk-good", ANTHROPIC)).blob;
    expect((await gateway.verify("alice", "c1", 1, blob)).models.length).toBeGreaterThan(0);
    expect(await rejected(() => gateway.refresh("alice", "c1", 1, blob))).toBe("not_refreshable");
    expect(await gateway.revokeAtProvider("alice", "c1", 1, blob)).toEqual({ revoked: false });
    expect(await rejected(() => gateway.verify("bob", "c1", 1, blob))).toBe("credential_refused");
  });

  it("exchanges an OAuth code over RPC", async () => {
    const exchanged = await gateway.exchange("alice", "c1", 1, {
      code: "good-code", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, { ...OPENAI, oauth: { client_id: "client-1", client_auth: { method: "none" },
                             token_endpoint: "https://auth.test/token", refresh_with: "rfc6749" } });
    expect(exchanged.kid).toBe("k2");
    expect(await rejected(() => gateway.exchange("alice", "c1", 1, {
      code: "bad", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, { ...OPENAI, oauth: { client_id: "client-1", client_auth: { method: "none" },
                             token_endpoint: "https://auth.test/token", refresh_with: "rfc6749" } })))
      .toBe("oauth_invalid_grant");
  });

  it("forwards a sandbox request on the egress entrypoint alone, a Request in and a Response out",
     async () => {
    // The doors' egress door reaches it over their real `EGRESS` binding; here, its surface.
    expect(Object.getOwnPropertyNames(EgressGateway.prototype).sort())
      .toEqual(["constructor", "forward"]);
    // The owner's entrypoint keeps it, for the owner's own MCP client (tasks 4 M).
    expect(Object.getOwnPropertyNames(Gateway.prototype)).toContain("forward");
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const metering = { max_output_tokens: 100, input_bytes: 10_000 };
    const outcome = async (answer: { outcome: ReadableStream<Uint8Array> }) =>
      JSON.parse(await new Response(answer.outcome).text()) as { state: string };
    // A sealed endpoint that is not pi-ai's model call is never metered, so never sent.
    const listing = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/models", { headers: { "x-fake-mode": "echo" } }), metering,
      new ReadableStream());
    expect(listing.response.status).toBe(403);
    expect(await outcome(listing)).toEqual({ state: "failed" });
    // With no metering, a read of a sealed endpoint goes, and nothing else does.
    const read = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/models", { headers: { "x-fake-mode": "echo" } }), null,
      new ReadableStream());
    expect(read.response.status).toBe(200);
    await read.response.body?.cancel();
    const unmetered = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body: "{}" }), null, new ReadableStream());
    expect(unmetered.response.status).toBe(403);
    expect(await unmetered.response.json()).toMatchObject({ error: { code: "unmetered" } });
    const refused = await egress.forward("", "c1", 1, blob, new Request("https://egress/v1/models"),
                                         metering, new ReadableStream());
    expect(refused.response.status).toBe(400);
    await expect(egress.forward("alice", "c1", 1, blob, new Request("https://egress/v1/models"),
                                { max_output_tokens: 0, input_bytes: 1 }, new ReadableStream()))
      .resolves.toMatchObject({ response: expect.any(Response) });
    // The owner's own entrypoint forwards unmetered (its MCP client's, tasks 4 M).
    const owned = await gateway.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/models", { headers: { "x-fake-mode": "echo" } }));
    expect(owned.status).toBe(200);
  });

  it("gives the doors' entrypoint only the connect steps", async () => {
    // The doors' own tests reach it over their real `GATEWAY` binding; here, its surface.
    expect(Object.getOwnPropertyNames(ConnectGateway.prototype).sort())
      .toEqual(["constructor", "exchange", "seal", "verify"]);
    const connect = new ConnectGateway(createExecutionContext(), env);
    const { blob } = await connect.seal("alice", "c1", 1, "sk-good", ANTHROPIC);
    expect((await connect.verify("alice", "c1", 1, blob)).models.length).toBeGreaterThan(0);
    expect(await rejected(() => connect.verify("bob", "c1", 1, blob))).toBe("credential_refused");
    expect(await rejected(() => connect.exchange("alice", "c1", 1, {
      code: "bad", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, ANTHROPIC))).toBe("invalid_source");
  });

  it("serves /v1/messages and nothing else over fetch", async () => {
    const blob = (await gateway.seal("alice", "c1", 1, "sk-msg", ANTHROPIC)).blob;
    const missing = await gateway.fetch("https://gateway.internal/v1/other");
    expect(missing.status).toBe(404);
    const refused = await gateway.fetch("https://gateway.internal/v1/messages", {
      method: "POST", headers: { "x-ta-envelope": envelopeOf({ blob, bound: { ...ALICE, owner: "bob" } }) },
      body: JSON.stringify({ model: "m", context: { messages: [] } }),
    });
    expect(refused.status).toBe(403);
    expect(await refused.text()).not.toContain("sk-msg");
  });
});
