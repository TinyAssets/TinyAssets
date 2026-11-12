/**
 * The Worker as its callers reach it: the RPC methods and `fetch`, over a real service
 * binding to the configured Worker (so only what RPC carries crosses), with the config's
 * keyring.
 */
import { createExecutionContext } from "cloudflare:test";
import { exports } from "cloudflare:workers";
import { env } from "cloudflare:workers";
import { describe, expect, it, vi } from "vitest";

import { builtinCatalog } from "../src/catalog";
import { learnMeter, meteredRecord } from "../src/meter";
import { Refusal } from "../../contract/src/refusal";
import { guarded } from "../src/failure";
import { IDLE_CUT_MS } from "../src/forward";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { GATEWAY_POOL_SLOTS, gatewaySlots } from "../src/held";
import Gateway, { ConnectGateway, EgressGateway } from "../src/index";
import { open } from "../src/keyring";
import { ALICE, ANTHROPIC, OPENAI, sealed } from "./support";
import type { Metering, Reserve } from "../../contract/src/gateway";

const gateway = (exports as unknown as { default: Service<Gateway> }).default;
/** The doors' connect entrypoint (the doors and owner suites reach it over a real binding). */
const connect = new ConnectGateway(createExecutionContext(), env);

/** A reserve that grants each call it is asked for, as asked, recording each ask. */
function granting(asked: unknown[] = [], fields: Partial<Metering> = {}): Reserve {
  return async (call) => {
    asked.push(call);
    return { metering: { max_output_tokens: 100, input_bytes: EGRESS_BODY_MAX, model: call.model,
                         tier: call.tier, ...fields } };
  };
}

/** A model call's `POST`, its `Content-Length` declared as the egress door hands it on. */
function posted(path: string, body: string, headers: Record<string, string> = {}): Request {
  return new Request(`https://egress${path}`, {
    method: "POST", body,
    headers: { "content-length": String(new TextEncoder().encode(body).byteLength),
               ...headers } });
}

/** An OpenAI Responses model whose output cap pi-ai sets, so its calls can be metered. */
async function responsesModel(): Promise<{ id: string }> {
  const catalog = builtinCatalog();
  for (const record of catalog.provider("openai")!.getModels()
    .filter((candidate) => candidate.api === "openai-responses")) {
    if (await learnMeter(catalog, OPENAI.upstream, "sk-fwd", meteredRecord(
      catalog, OPENAI.upstream, record.id)!) !== null) {
      return record;
    }
  }
  throw new Error("no metered Responses model in the catalog");
}

/** An Anthropic Messages model whose output cap pi-ai sets, so its calls can be metered. */
async function anthropicModel(): Promise<{ id: string }> {
  const catalog = builtinCatalog();
  for (const record of catalog.provider("anthropic")!.getModels()
    .filter((candidate) => candidate.api === "anthropic-messages")) {
    if (await learnMeter(catalog, ANTHROPIC.upstream, "sk-good", meteredRecord(
      catalog, ANTHROPIC.upstream, record.id)!) !== null) {
      return record;
    }
  }
  throw new Error("no metered Messages model in the catalog");
}

async function until(check: () => boolean): Promise<void> {
  for (let tries = 0; tries < 500; tries++) {
    if (check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("timed out");
}

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
    expect(error).toBeInstanceOf(Refusal);
    expect((error as Error).message).toBe("internal");
    const refused = await guarded(async () => {
      throw new Refusal("endpoint_refused");
    }).catch((caught: unknown) => caught);
    expect((refused as Error).message).toBe("endpoint_refused");
  });
});

describe("the gateway Worker", () => {
  it("seals a pasted key over RPC, bound to the owner, connection and version", async () => {
    const { blob, kid } = await connect.seal("alice", "c1", 1, "sk-rpc", ANTHROPIC);
    expect(kid).toBe("k2");
    const opened = await open(env, ALICE, blob);
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-rpc");
    expect(await rejected(() => open(env, { ...ALICE, owner: "bob" }, blob))).toBe("credential_refused");
  });

  it("refuses a malformed binding, key or source with only a fixed code", async () => {
    expect(await rejected(() => connect.seal("", "c1", 1, "sk", ANTHROPIC))).toBe("invalid_binding");
    expect(await rejected(() => connect.seal("alice", "c1", 0, "sk", ANTHROPIC))).toBe("invalid_binding");
    expect(await rejected(() => connect.seal("alice", "c1", 1, "sk\r\nx-evil: 1", ANTHROPIC)))
      .toBe("invalid_source");
    expect(await rejected(() => connect.seal("alice", "c1", 1, "sk",
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
    const blob = (await connect.seal("alice", "c1", 1, "sk-good", ANTHROPIC)).blob;
    expect((await connect.verify("alice", "c1", 1, blob)).models.length).toBeGreaterThan(0);
    expect(await rejected(() => gateway.refresh("alice", "c1", 1, blob))).toBe("not_refreshable");
    expect(await gateway.revokeAtProvider("alice", "c1", 1, blob)).toEqual({ revoked: false });
    expect(await rejected(() => connect.verify("bob", "c1", 1, blob))).toBe("credential_refused");
  });

  it("exchanges an OAuth code over RPC", async () => {
    const exchanged = await connect.exchange("alice", "c1", 1, {
      code: "good-code", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, { ...OPENAI, oauth: { client_id: "client-1", client_auth: { method: "none" },
                             token_endpoint: "https://auth.test/token", refresh_with: "rfc6749" } });
    expect(exchanged.kid).toBe("k2");
    expect(await rejected(() => connect.exchange("alice", "c1", 1, {
      code: "bad", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, { ...OPENAI, oauth: { client_id: "client-1", client_auth: { method: "none" },
                             token_endpoint: "https://auth.test/token", refresh_with: "rfc6749" } })))
      .toBe("oauth_invalid_grant");
  });

  it("forwards a cited free call unmetered, as a read, and no other unmetered call", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const counted = { ...ANTHROPIC, endpoints: [...ANTHROPIC.endpoints, {
      origin: "https://api.anthropic.com", paths: ["/v1/messages/count_tokens"],
      methods: ["POST"] }] };
    const blob = (await connect.seal("alice", "c1", 1, "sk-count", counted)).blob;
    // Claude Code's token count, which Anthropic does not bill: sent with the sealed key.
    const count = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/messages/count_tokens", { method: "POST", body: "{}",
                                                   headers: { "x-fake-mode": "hdr" } }),
      null, new ReadableStream());
    expect(count.response.status).toBe(200);
    // The sealed key reached the provider (the gateway redacts it from the answer).
    expect(count.response.headers.get("x-echo-key")).toBe("[redacted]");
    await count.response.body?.cancel();
    // A model call is never free, nor is a token count on a source that does not seal it.
    const message = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/messages", { method: "POST", body: "{}",
                                      headers: { "x-fake-mode": "hdr" } }),
      null, new ReadableStream());
    expect(await message.response.json()).toMatchObject({ error: { code: "unmetered" } });
    const unsealed = (await connect.seal("alice", "c1", 1, "sk-count", ANTHROPIC)).blob;
    const elsewhere = await egress.forward("alice", "c1", 1, unsealed, new Request(
      "https://egress/v1/messages/count_tokens", { method: "POST", body: "{}" }),
      null, new ReadableStream());
    expect(await elsewhere.response.json()).toMatchObject({ error: { code: "endpoint_refused" } });
    // Another provider on the same API, sealing the same path: Anthropic's page is not its.
    const other = (await connect.seal("alice", "c1", 1, "sk-count", {
      upstream: { provider: "vercel-ai-gateway", api: "anthropic-messages",
                  base_url: "https://ai-gateway.vercel.sh",
                  auth: { header: "x-api-key", scheme: "" } },
      endpoints: [{ origin: "https://ai-gateway.vercel.sh",
                    paths: ["/v1/messages", "/v1/messages/count_tokens"], methods: ["POST"] }] })).blob;
    const theirs = await egress.forward("alice", "c1", 1, other, new Request(
      "https://egress/v1/messages/count_tokens", { method: "POST", body: "{}",
                                                   headers: { "x-fake-mode": "hdr" } }),
      null, new ReadableStream());
    expect(await theirs.response.json()).toMatchObject({ error: { code: "unmetered" } });
  });

  it("forwards a sandbox request on the egress entrypoint alone, a Request in and a Response out",
     async () => {
    // The doors' egress door reaches it over their real `EGRESS` binding; here, its surface.
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const outcome = async (answer: { outcome: ReadableStream<Uint8Array> }) =>
      JSON.parse(await new Response(answer.outcome).text()) as { state: string };
    // A sealed endpoint that is not pi-ai's model call is never metered, so never sent.
    const listing = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/models", { headers: { "x-fake-mode": "echo" } }), granting(),
      new ReadableStream());
    expect(listing.response.status).toBe(403);
    expect(await outcome(listing)).toEqual({ state: "failed" });
    // With no reserve, a read of a sealed endpoint goes, and nothing else does.
    const read = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/models", { headers: { "x-fake-mode": "echo" } }), null,
      new ReadableStream());
    expect(read.response.status).toBe(200);
    await read.response.body?.cancel();
    const unmetered = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body: "{}" }), null, new ReadableStream());
    expect(unmetered.response.status).toBe(403);
    expect(await unmetered.response.json()).toMatchObject({ error: { code: "unmetered" } });
    // The caller's stop aborts a provider request that has not answered yet.
    const started = Date.now();
    const stop = new IdentityTransformStream();
    const held = egress.forward("alice", "c1", 1, blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "hang" } }), null, stop.readable);
    await stop.writable.getWriter().close();
    const stopped = await held;
    expect(await stopped.response.json()).toMatchObject({ error: { code: "upstream_unreachable" } });
    expect(Date.now() - started).toBeLessThan(5000);
    // A metered call cut before any answer may have reached the provider: never free.
    const model = await responsesModel();
    const cutStop = new IdentityTransformStream();
    const cut = egress.forward("alice", "c1", 1, blob, posted("/v1/responses", JSON.stringify({
      model: model.id, input: "hi", stream: true }), { "x-fake-mode": "hang" }),
      granting(), cutStop.readable);
    await new Promise((resolve) => setTimeout(resolve, 50));
    await cutStop.writable.getWriter().close();
    expect(await outcome(await cut)).toEqual({ state: "unknown" });
    // A priority call is priced as pi-ai prices it, above the standard call's price.
    const sent = (fields: Record<string, unknown>) => egress.forward("alice", "c1", 1, blob,
      posted("/v1/responses", JSON.stringify({ model: model.id, input: "hi", stream: true,
                                                ...fields })), granting(),
      new ReadableStream());
    const settledAt = async (answer: Awaited<ReturnType<typeof sent>>) => {
      await answer.response.text();
      return JSON.parse(await new Response(answer.outcome).text()) as
        { state: string; cost: number };
    };
    const standard = await settledAt(await sent({ service_tier: "default" }));
    const priority = await settledAt(await sent({ service_tier: "priority" }));
    expect(standard.state).toBe("succeeded");
    expect(priority.state).toBe("succeeded");
    expect(priority.cost).toBeGreaterThanOrEqual(standard.cost * 2 - 1e-12);
    // An answer naming no tier was served at the account's default, maybe priority: it
    // settles at the dearest the API allows.
    const unnamed = await settledAt(await sent({}));
    expect(unnamed.state).toBe("succeeded");
    expect(unnamed.cost).toBeCloseTo(standard.cost * 2.5, 12);
    // The first event's echoed `auto` is not the served tier: `default` at the end is ×1.
    const auto = await settledAt(await sent({ service_tier: "auto" }));
    expect(auto.state).toBe("succeeded");
    expect(auto.cost).toBeCloseTo(standard.cost, 12);
    // Provider beta headers, which can widen a call past its reservation, are stripped.
    const beta = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi" }), {
        "x-fake-mode": "echo", "anthropic-beta": "context-1m", "openai-beta": "assistants=v2",
        "x-vendor-beta": "on", "x-kept": "yes" }), granting(), new ReadableStream());
    const echoed = await beta.response.json() as { headers: Record<string, string> };
    expect(echoed.headers["x-kept"]).toBe("yes");
    for (const name of ["anthropic-beta", "openai-beta", "x-vendor-beta"]) {
      expect(echoed.headers[name]).toBeUndefined();
    }
    // Anthropic: a call naming no tier is sent at the one pi-ai prices, and an answer served
    // at another is unknown.
    const anthropic = (await connect.seal("alice", "c2", 1, "sk-good", ANTHROPIC)).blob;
    const claude = builtinCatalog();
    let named: { id: string } | undefined;
    for (const record of claude.provider("anthropic")!.getModels()
      .filter((candidate) => candidate.api === "anthropic-messages")) {
      if (await learnMeter(claude, ANTHROPIC.upstream, "sk-good", meteredRecord(
        claude, ANTHROPIC.upstream, record.id)!) !== null) {
        named = record;
        break;
      }
    }
    const message = async (served: string | null) => {
      const answer = await egress.forward("alice", "c2", 1, anthropic, posted("/v1/messages",
        JSON.stringify({ model: named!.id, max_tokens: 50, stream: true,
                         messages: [{ role: "user", content: "cmd:echo" }] }),
        served === null ? {} : { "x-fake-served-tier": served }), granting(),
        new ReadableStream());
      const streamed = await answer.response.text();
      return { streamed, outcome: JSON.parse(await new Response(answer.outcome).text()) as
        { state: string } };
    };
    const plain = await message(null);
    expect(plain.streamed).toContain("standard_only");
    expect(plain.outcome.state).toBe("succeeded");
    expect((await message("standard")).outcome.state).toBe("succeeded");
    expect((await message("priority")).outcome).toEqual({ state: "unknown" });
    const refused = await egress.forward("", "c1", 1, blob, new Request("https://egress/v1/models"),
                                         granting(), new ReadableStream());
    expect(refused.response.status).toBe(400);
    // The owner's entrypoint forwards nothing: egress is the egress door's, metered or a read.
    expect(Object.hasOwn(Gateway.prototype, "forward")).toBe(false);
  });

  it("reserves a model call for the model and tier its one parse read, wherever the keys lie",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const asked: unknown[] = [];
    const refusing: Reserve = async (call) => {
      asked.push(call);
      return { refused: "egress_spend_refused" };
    };
    const call = async (text: string, reserve: Reserve = refusing) => egress.forward(
      "alice", "c1", 1, blob, posted("/v1/responses", text, { "x-fake-mode": "echo" }),
      reserve, new ReadableStream());
    // A tier the body names, or none (null); the last of duplicate keys, as JSON.parse reads
    // them and as the provider receives the gateway's own parse; a model after a field of
    // almost the whole bound.
    const large = "x".repeat(EGRESS_BODY_MAX - 200);
    for (const [text, expected] of [
      [JSON.stringify({ model: model.id, input: "hi" }), { model: model.id, tier: null }],
      [JSON.stringify({ model: model.id, input: "hi", service_tier: "priority" }),
       { model: model.id, tier: "priority" }],
      [`{"model":"another-model","service_tier":"priority","input":"hi","model":${
        JSON.stringify(model.id)},"service_tier":"flex"}`, { model: model.id, tier: "flex" }],
      [`{"input":"${large}","service_tier":"default","model":${JSON.stringify(model.id)}}`,
       { model: model.id, tier: "default" }]] as const) {
      asked.length = 0;
      const answer = await call(text);
      // The object's refusal reaches the caller, and nothing is sent.
      expect([answer.response.status, await answer.response.json()]).toEqual(
        [403, { error: { code: "egress_reserve_refused", message: "egress_reserve_refused" } }]);
      expect(asked).toEqual([expected]);
    }
    // What the reservation cannot bound, or what is not the call, is refused before any
    // reserve: none is asked for, so none is charged.
    asked.length = 0;
    for (const [text, code] of [
      ["not json", "invalid_request"],
      [JSON.stringify({ model: "no-such-model", input: "hi" }), "unknown_model"],
      [JSON.stringify({ model: model.id, input: "hi", tools: [{ type: "web_search" }] }),
       "egress_unmetered_tool"],
      [JSON.stringify({ model: model.id, input: "hi", service_tier: 2 }),
       "egress_unpriced_option"],
      [JSON.stringify({ model: model.id, input: "hi", service_tier: "scale" }),
       "egress_unpriced_option"],
      [JSON.stringify({ model: model.id, input: "hi", MAX_OUTPUT_TOKENS: 999_999 }),
       "egress_aliased_key"],
      [JSON.stringify({ model: model.id, input: "hi", maxOutputTokens: 999_999 }),
       "egress_aliased_key"]] as const) {
      expect(await (await call(text)).response.json()).toMatchObject({ error: { code } });
    }
    expect(asked).toEqual([]);
    // A body past its declared length, or one with none, or one past the bound, is refused
    // as it is read; so is one past the length the object reserved it at.
    const text = JSON.stringify({ model: model.id, input: "hi" });
    const declared = (length: string | null, body: string = text) => egress.forward(
      "alice", "c1", 1, blob, new Request("https://egress/v1/responses", {
        method: "POST", body: new Blob([body]).stream(),
        headers: length === null ? {} : { "content-length": length } }),
      granting(asked), new ReadableStream());
    for (const length of [String(text.length - 1), null, String(EGRESS_BODY_MAX + 1), "-1"]) {
      expect(await (await declared(length)).response.json())
        .toMatchObject({ error: { code: "invalid_request" } });
    }
    expect(asked).toEqual([]);
    expect(await (await call(text, granting([], { input_bytes: text.length - 1 }))).response
      .json()).toMatchObject({ error: { code: "invalid_request" } });
    // An object that reserved another call than it was asked is the platform's bug: loud.
    const other: Reserve = async (asking) => ({ metering: {
      max_output_tokens: 10, input_bytes: EGRESS_BODY_MAX, model: "another-model",
      tier: asking.tier } });
    const wrong = await call(text, other);
    expect([wrong.response.status, await wrong.response.json()])
      .toEqual([500, { error: { code: "internal", message: "internal" } }]);
  });

  it("cuts a metered answer its provider goes silent on, the provider's request ended",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c2", 1, "sk-good", ANTHROPIC)).blob;
    const model = await anthropicModel();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const answer = await egress.forward("alice", "c2", 1, blob, posted("/v1/messages",
        JSON.stringify({ model: model.id, max_tokens: 50, stream: true,
                         messages: [{ role: "user", content: "hi" }] }),
        { "x-fake-mode": "stall", "x-fake-id": "idle-metered" }), granting(),
        new ReadableStream());
      const reader = answer.response.body!.getReader();
      expect((await reader.read()).done).toBe(false);
      const next = reader.read().then(() => "read on", (error: Error) => error.message);
      await vi.advanceTimersByTimeAsync(IDLE_CUT_MS);
      expect(await next).toBe("deadline");
    } finally {
      vi.useRealTimers();
    }
    // The meter's own reads of the answer end with it: the provider's request is cut.
    for (let tries = 0; tries < 100; tries++) {
      const seen = await (await fetch("https://status.test/cancelled?id=idle-metered"))
        .json() as { cancelled: boolean | null };
      if (seen.cancelled === true) {
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    throw new Error("the provider's request was never cut");
  });

  it("never cuts a metered answer whose every byte comes inside the idle cut", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c2", 1, "sk-good", ANTHROPIC)).blob;
    const model = await anthropicModel();
    const stop = new IdentityTransformStream();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const answer = await egress.forward("alice", "c2", 1, blob, posted("/v1/messages",
        JSON.stringify({ model: model.id, max_tokens: 50, stream: true,
                         messages: [{ role: "user", content: "cmd:slow-steady" }] })),
        granting(), stop.readable);
      const reader = answer.response.body!.getReader();
      expect((await reader.read()).done).toBe(false);
      // Three gaps of just under the cut each: past it in all, never within one gap.
      for (let gap = 0; gap < 3; gap++) {
        const next = reader.read().then((chunk) => chunk.done ? "done" : "byte",
                                        (error: Error) => error.message);
        await vi.advanceTimersByTimeAsync(IDLE_CUT_MS - 1_000);
        expect(await next).toBe("byte");
      }
      await stop.writable.getWriter().close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("ends an answer its caller stopped mid-stream cleanly, the provider's request cut",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c2", 1, "sk-good", ANTHROPIC)).blob;
    const claude = builtinCatalog();
    let named: { id: string } | undefined;
    for (const record of claude.provider("anthropic")!.getModels()
      .filter((candidate) => candidate.api === "anthropic-messages")) {
      if (await learnMeter(claude, ANTHROPIC.upstream, "sk-good", meteredRecord(
        claude, ANTHROPIC.upstream, record.id)!) !== null) {
        named = record;
        break;
      }
    }
    const stop = new IdentityTransformStream();
    const answer = await egress.forward("alice", "c2", 1, blob, posted("/v1/messages",
      JSON.stringify({ model: named!.id, max_tokens: 50, stream: true,
                       messages: [{ role: "user", content: "cmd:slow-stopped" }] })),
      granting(), stop.readable);
    const reader = answer.response.body!.getReader();
    expect((await reader.read()).done).toBe(false);
    await stop.writable.getWriter().close();
    // The stop's abort is not carried back as the answer's error: the caller knows why.
    for (;;) {
      if ((await reader.read()).done) {
        break;
      }
    }
    expect(JSON.parse(await new Response(answer.outcome).text())).toEqual({ state: "unknown" });
    for (let tries = 0; tries < 100; tries++) {
      const seen = await (await fetch("https://status.test/cancelled?id=stopped"))
        .json() as { cancelled: boolean | null };
      if (seen.cancelled === true) {
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    throw new Error("the provider's request was never cut");
  });

  it("sends the provider the clamped parse as bytes, with its Content-Length", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const asked = { model: model.id, input: [{ role: "user", content: "— every 😀" }],
                    max_output_tokens: 900_000_000, temperature: 0.5 };
    const answer = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses",
      JSON.stringify(asked), { "x-fake-mode": "echo" }), granting([], { max_output_tokens: 77 }),
      new ReadableStream());
    const echoed = await answer.response.json() as { body: string;
                                                    headers: Record<string, string> };
    const expected = JSON.stringify({ ...asked, max_output_tokens: 77 });
    expect(echoed.body).toBe(expected);
    expect(echoed.headers["content-length"])
      .toBe(String(new TextEncoder().encode(expected).byteLength));
    expect(echoed.headers["transfer-encoding"]).toBeUndefined();
  });

  it("holds its slots while it reads, rewrites and uploads, never across the provider's wait",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const pool = gatewaySlots();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    // A call that weighs the whole pool (15 MiB, a non-ASCII character in it: `held.ts`) takes
    // every slot while its reserve is pending.
    let grant: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => { grant = resolve; });
    const whole = JSON.stringify({ model: model.id,
                                   input: `${"x".repeat(15 * 1024 * 1024 - 300)}é` });
    const first = egress.forward("alice", "c1", 1, blob, posted("/v1/responses", whole,
      { "x-fake-mode": "held" }), async (call) => {
      await pending;
      return granting()(call);
    }, new ReadableStream());
    await until(() => pool.state().held === GATEWAY_POOL_SLOTS);
    // The next waits, first come first served, as many as may wait; one more is `busy`.
    const small = JSON.stringify({ model: model.id, input: "hi" });
    const refusing: Reserve = async () => ({ refused: "egress_spend_refused" });
    const waiting = Array.from({ length: 4 * GATEWAY_POOL_SLOTS }, () => egress.forward(
      "alice", "c1", 1, blob, posted("/v1/responses", small), refusing,
      new ReadableStream()));
    await until(() => pool.state().waiting === 4 * GATEWAY_POOL_SLOTS);
    const busy = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses", small),
                                      refusing, new ReadableStream());
    expect([busy.response.status, busy.response.headers.get("retry-after"),
            await busy.response.json()])
      .toEqual([503, "1", { error: { code: "busy", message: "busy" } }]);
    // Reserved, the first uploads and lets its slots go at the upload's end: the waiting
    // calls land while the provider still holds its answer.
    let answeredYet = false;
    void first.then(() => { answeredYet = true; });
    grant();
    for (const answer of await Promise.all(waiting)) {
      expect(await answer.response.json()).toMatchObject({
        error: { code: "egress_reserve_refused" } });
    }
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    expect(answeredYet).toBe(false);
    const answered = await first;
    // The provider read the whole upload: the call as clamped, its output cap added.
    expect(await answered.response.json()).toEqual({ length: new TextEncoder().encode(
      JSON.stringify({ ...JSON.parse(whole), max_output_tokens: 100 })).byteLength });
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("gives each caller's entrypoint only its own methods", async () => {
    // The doors' own tests reach it over their real `GATEWAY` binding; here, its surface.
    expect(Object.getOwnPropertyNames(ConnectGateway.prototype).sort())
      .toEqual(["constructor", "exchange", "seal", "sealDoorbell", "verify", "verifyDoorbell"]);
    expect(Object.getOwnPropertyNames(EgressGateway.prototype).sort())
      .toEqual(["constructor", "forward"]);
    // The owner's object: its connections' upkeep and its wakes, and no seal or forward.
    expect(Object.getOwnPropertyNames(Gateway.prototype).sort())
      .toEqual(["constructor", "fetch", "refresh", "reseal", "revokeAtProvider",
                "ringDoorbell"]);
    const { blob } = await connect.seal("alice", "c1", 1, "sk-good", ANTHROPIC);
    expect((await connect.verify("alice", "c1", 1, blob)).models.length).toBeGreaterThan(0);
    expect(await rejected(() => connect.verify("bob", "c1", 1, blob))).toBe("credential_refused");
    expect(await rejected(() => connect.exchange("alice", "c1", 1, {
      code: "bad", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, ANTHROPIC))).toBe("invalid_source");
  });

  it("serves no model call over fetch: /v1/messages is gone with the in-object loop",
     async () => {
    for (const path of ["/v1/messages", "/v1/other"]) {
      const missing = await gateway.fetch(`https://gateway.internal${path}`, {
        method: "POST", body: JSON.stringify({ model: "m", context: { messages: [] } }) });
      expect(missing.status).toBe(404);
      expect(await missing.json()).toEqual({ error: { code: "not_found", message: "not_found" } });
    }
  });
});
