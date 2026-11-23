/**
 * The Worker as its callers reach it: the RPC methods and `fetch`, over a real service
 * binding to the configured Worker (so only what RPC carries crosses), with the config's
 * keyring.
 */
import { createExecutionContext } from "cloudflare:test";
import { exports } from "cloudflare:workers";
import { env } from "cloudflare:workers";
import { describe, expect, it, vi } from "vitest";

import { Refusal } from "../../contract/src/refusal";
import { guarded } from "../src/failure";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { GATEWAY_POOL_SLOTS, gatewaySlots } from "../src/held";
import Gateway, { ConnectGateway, EgressGateway } from "../src/index";
import { doorbellSecret, sealDoorbell } from "../src/doorbell";
import { open, openValue } from "../src/keyring";
import { until } from "../../ci/workerd";
import {
  ALICE, ANTHROPIC, C1, C2, granting, meterable, OPENAI, outcome, posted, sealed,
} from "./support";
import { steer } from "./fake-upstream";
import type { Reserve } from "../../contract/src/gateway";

const gateway = (exports as unknown as { default: Service<Gateway> }).default;
/** The doors' connect entrypoint (the doors and owner suites reach it over a real binding). */
const connect = new ConnectGateway(createExecutionContext(), env);

/** An OpenAI Responses model whose output cap pi-ai sets, so its calls can be metered. */
const responsesModel = () => meterable(OPENAI, "sk-fwd");
/** An Anthropic Messages model whose output cap pi-ai sets, so its calls can be metered. */
const anthropicModel = () => meterable(ANTHROPIC, "sk-good");

/** Whether the provider's request with the word `id` (`steer`) was cancelled. */
const cut = (id: string) => until(async () => (await (await fetch(
  `https://status.test/cancelled?id=${id}`)).json() as { cancelled: boolean | null })
  .cancelled === true, `the request ${id} cut`);

/** Once the provider has the request with the word `id` (`steer`). */
const arrived = (id: string) => until(async () => (await (await fetch(
  `https://status.test/cancelled?id=${id}`)).json() as { arrived: boolean }).arrived,
  `the request ${id}`);

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
    const { blob, kid } = await connect.seal("alice", C1, 1, "sk-rpc", ANTHROPIC);
    expect(kid).toBe("k2");
    const opened = await open(env, ALICE, blob);
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-rpc");
    expect(await rejected(() => open(env, { ...ALICE, owner: "bob" }, blob))).toBe("credential_refused");
  });

  it("refuses a malformed binding, key or source with only a fixed code", async () => {
    expect(await rejected(() => connect.seal("", C1, 1, "sk", ANTHROPIC))).toBe("invalid_binding");
    expect(await rejected(() => connect.seal("alice", C1, 0, "sk", ANTHROPIC))).toBe("invalid_binding");
    expect(await rejected(() => connect.seal("alice", C1, 1, "sk\r\nx-evil: 1", ANTHROPIC)))
      .toBe("invalid_source");
    expect(await rejected(() => connect.seal("alice", C1, 1, "sk",
      { ...ANTHROPIC, upstream: { ...ANTHROPIC.upstream, base_url: "https://api.openai.com" } })))
      .toBe("invalid_source");
  });

  it("reseals a blob under an older kid at the next version", async () => {
    const old = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
    const k1 = { GATEWAY_KEYRING: JSON.stringify({ active: "k1", keys: old.keys }) };
    const blob = await sealed(k1, ALICE, "sk-old", ANTHROPIC);
    const resealed = await gateway.reseal("alice", C1, 1, blob);
    expect(resealed).toMatchObject({ kid: "k2", version: 2 });
    const opened = await open(env, { ...ALICE, version: 2 }, resealed.blob);
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-old");
  });

  it("reseals a doorbell's blob under an older kid at the next version, for the sweep",
     async () => {
    const old = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
    const k1 = { GATEWAY_KEYRING: JSON.stringify({ active: "k1", keys: old.keys }) };
    const subscription = "sub_" + "a".repeat(32);
    const bell = { record: "claude_routine_fire", secret: "sk-ant-oat01-" + "x".repeat(40),
                   url: "https://api.anthropic.com/v1/claude_code/routines/trig_01ABC/fire" };
    const { blob, kid } = await sealDoorbell(k1, "alice", subscription, 1, bell);
    expect(kid).toBe("k1");
    const resealed = await gateway.resealDoorbell("alice", subscription, 1, blob);
    expect(resealed).toMatchObject({ kid: "k2", version: 2 });
    const at = (version: number) => ({ owner: "alice", connection: `wake.${subscription}`,
                                       version });
    expect((await openValue(env, at(2), resealed.blob, doorbellSecret)).value)
      .toEqual((await openValue(env, at(1), blob, doorbellSecret)).value);
    // A blob presented at a version it was not sealed for, or another subscription's.
    expect(await rejected(() => gateway.resealDoorbell("alice", subscription, 2, blob)))
      .toBe("credential_refused");
    expect(await rejected(() => gateway.resealDoorbell("alice", "sub_" + "b".repeat(32), 1,
                                                       blob))).toBe("credential_refused");
  });

  it("verifies, refreshes and revokes over RPC with fixed codes", async () => {
    const blob = (await connect.seal("alice", C1, 1, "sk-good", ANTHROPIC)).blob;
    expect((await connect.verify("alice", C1, 1, blob)).models.length).toBeGreaterThan(0);
    expect(await rejected(() => gateway.refresh("alice", C1, 1, blob))).toBe("not_refreshable");
    expect(await gateway.revokeAtProvider("alice", C1, 1, blob)).toEqual({ revoked: false });
    expect(await rejected(() => connect.verify("bob", C1, 1, blob))).toBe("credential_refused");
  });

  it("exchanges an OAuth code over RPC", async () => {
    const exchanged = await connect.exchange("alice", C1, 1, {
      code: "good-code", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, { ...OPENAI, oauth: { client_id: "client-1", client_auth: { method: "none" },
                             token_endpoint: "https://auth.test/token" } });
    expect(exchanged.kid).toBe("k2");
    expect(await rejected(() => connect.exchange("alice", C1, 1, {
      code: "bad", verifier: "v".repeat(43), redirect_uri: "https://tinyassets.io/cb",
    }, { ...OPENAI, oauth: { client_id: "client-1", client_auth: { method: "none" },
                             token_endpoint: "https://auth.test/token" } })))
      .toBe("oauth_invalid_grant");
  });

  it("forwards a cited free call unmetered, as a read, and no other unmetered call", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const counted = { ...ANTHROPIC, endpoints: [...ANTHROPIC.endpoints, {
      origin: "https://api.anthropic.com", paths: ["/v1/messages/count_tokens"],
      methods: ["POST"] }] };
    const blob = (await connect.seal("alice", C1, 1, "sk-count", counted)).blob;
    // Claude Code's token count, which Anthropic does not bill: sent with the sealed key.
    const count = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/messages/count_tokens", { method: "POST", body: "{}",
                                                   headers: steer({ mode: "hdr" }) }),
      null, new ReadableStream());
    expect(count.response.status).toBe(200);
    // The sealed key reached the provider (the gateway redacts it from the answer).
    expect(count.response.headers.get("x-echo-key")).toBe("[redacted]");
    await count.response.body?.cancel();
    // A model call is never free, nor is a token count on a source that does not seal it.
    const message = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/messages", { method: "POST", body: "{}",
                                      headers: steer({ mode: "hdr" }) }),
      null, new ReadableStream());
    expect(await message.response.json()).toMatchObject({ error: { code: "unmetered" } });
    const unsealed = (await connect.seal("alice", C1, 1, "sk-count", ANTHROPIC)).blob;
    const elsewhere = await egress.forward("alice", C1, 1, unsealed, new Request(
      "https://egress/v1/messages/count_tokens", { method: "POST", body: "{}" }),
      null, new ReadableStream());
    expect(await elsewhere.response.json()).toMatchObject({ error: { code: "endpoint_refused" } });
    // Another provider on the same API, sealing the same path: Anthropic's page is not its.
    const other = (await connect.seal("alice", C1, 1, "sk-count", {
      upstream: { provider: "vercel-ai-gateway", api: "anthropic-messages",
                  base_url: "https://ai-gateway.vercel.sh",
                  auth: { header: "x-api-key", scheme: "" } },
      endpoints: [{ origin: "https://ai-gateway.vercel.sh",
                    paths: ["/v1/messages", "/v1/messages/count_tokens"], methods: ["POST"] }] })).blob;
    const theirs = await egress.forward("alice", C1, 1, other, new Request(
      "https://egress/v1/messages/count_tokens", { method: "POST", body: "{}",
                                                   headers: steer({ mode: "hdr" }) }),
      null, new ReadableStream());
    expect(await theirs.response.json()).toMatchObject({ error: { code: "unmetered" } });
  });

  it("forwards a sandbox request on the egress entrypoint alone, a Request in and a Response out",
     async () => {
    // The doors' egress door reaches it over their real `EGRESS` binding; here, its surface.
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    // A sealed endpoint that is not pi-ai's model call is never metered, so never sent.
    const listing = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/models", { headers: steer({ mode: "echo" }) }), granting(),
      new ReadableStream());
    expect(listing.response.status).toBe(403);
    expect(await outcome(listing)).toEqual({ state: "failed" });
    // With no reserve, a read of a sealed endpoint goes, and nothing else does.
    const read = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/models", { headers: steer({ mode: "echo" }) }), null,
      new ReadableStream());
    expect(read.response.status).toBe(200);
    await read.response.body?.cancel();
    const unmetered = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body: "{}" }), null, new ReadableStream());
    expect(unmetered.response.status).toBe(403);
    expect(await unmetered.response.json()).toMatchObject({ error: { code: "unmetered" } });
    // The caller's stop aborts a provider request that has not answered yet.
    const started = Date.now();
    const stop = new IdentityTransformStream();
    const held = egress.forward("alice", C1, 1, blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "hang" }) }), null, stop.readable);
    await stop.writable.getWriter().close();
    const stopped = await held;
    expect(await stopped.response.json()).toMatchObject({ error: { code: "upstream_unreachable" } });
    expect(Date.now() - started).toBeLessThan(5000);
    // A metered call cut before any answer may have reached the provider: never free.
    const model = await responsesModel();
    const cutStop = new IdentityTransformStream();
    const hung = egress.forward("alice", C1, 1, blob, posted("/v1/responses", JSON.stringify({
      model: model.id, input: "hi", stream: true }),
      steer({ mode: "hang", id: "cut-metered" })), granting(), cutStop.readable);
    await arrived("cut-metered");
    await cutStop.writable.getWriter().close();
    expect(await outcome(await hung)).toEqual({ state: "unknown" });
    // A priority call is priced as pi-ai prices it, above the standard call's price.
    const sent = (fields: Record<string, unknown>) => egress.forward("alice", C1, 1, blob,
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
    const anthropic = (await connect.seal("alice", C2, 1, "sk-good", ANTHROPIC)).blob;
    const named = await anthropicModel();
    // A provider's beta, which can widen a call past its reservation, is not sent on a metered
    // call, though its API's rules take it on a free one; its own headers are.
    const beta = await egress.forward("alice", C2, 1, anthropic, posted("/v1/messages",
      JSON.stringify({ model: named.id, max_tokens: 50,
                       messages: [{ role: "user", content: "hi" }] }), {
        ...steer({ mode: "echo" }), "anthropic-beta": "context-1m",
        "anthropic-version": "2023-06-01", "content-type": "application/json" }), granting(),
      new ReadableStream());
    const echoed = await beta.response.json() as { headers: Record<string, string> };
    expect(echoed.headers["anthropic-beta"]).toBeUndefined();
    expect(echoed.headers).toMatchObject({ "anthropic-version": "2023-06-01",
                                           "content-type": "application/json" });
    // Anthropic: a call naming no tier is sent at the one pi-ai prices, and an answer served
    // at another is unknown.
    const message = async (served: string | null) => {
      const answer = await egress.forward("alice", C2, 1, anthropic, posted("/v1/messages",
        JSON.stringify({ model: named.id, max_tokens: 50, stream: true,
                         messages: [{ role: "user", content: "cmd:echo" }] }),
        served === null ? {} : steer({ tier: served })), granting(),
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
    const refused = await egress.forward("", C1, 1, blob, new Request("https://egress/v1/models"),
                                         granting(), new ReadableStream());
    expect(refused.response.status).toBe(400);
  });

  it("reserves a model call for the model and tier its one parse read, wherever the keys lie",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const asked: unknown[] = [];
    const refusing: Reserve = async (call) => {
      asked.push(call);
      return { refused: "egress_spend_refused" };
    };
    const call = async (text: string, reserve: Reserve = refusing) => egress.forward(
      "alice", C1, 1, blob, posted("/v1/responses", text, steer({ mode: "echo" })),
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
      "alice", C1, 1, blob, new Request("https://egress/v1/responses", {
        method: "POST", body: new Blob([body]).stream(),
        headers: length === null ? {} : { "content-length": length } }),
      granting(asked), new ReadableStream());
    for (const length of [String(text.length - 1), null, String(EGRESS_BODY_MAX + 1), "-1"]) {
      expect(await (await declared(length)).response.json())
        .toMatchObject({ error: { code: "invalid_request" } });
    }
    expect(asked).toEqual([]);
    // A coded body is not what the parse reads: refused before it is read.
    const coded = await egress.forward("alice", C1, 1, blob, posted("/v1/responses", text, {
      "content-encoding": "gzip", ...steer({ mode: "echo" }) }), granting(asked),
      new ReadableStream());
    expect([coded.response.status, await coded.response.json()]).toEqual(
      [415, { error: { code: "egress_body_encoded", message: "egress_body_encoded" } }]);
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

  it("leaves a metered answer its provider goes silent on to its caller, whose leaving ends it",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C2, 1, "sk-good", ANTHROPIC)).blob;
    const model = await anthropicModel();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const answer = await egress.forward("alice", C2, 1, blob, posted("/v1/messages",
        JSON.stringify({ model: model.id, max_tokens: 50, stream: true,
                         messages: [{ role: "user", content: "hi" }] }),
        steer({ mode: "stall", id: "idle-metered" })), granting(),
        new ReadableStream());
      const reader = answer.response.body!.getReader();
      expect((await reader.read()).done).toBe(false);
      let ended: string | undefined;
      void reader.read().then(() => "read on", (error: Error) => error.message)
        .then((how) => {
          ended = how;
        });
      // An hour of silence, past every engine's own cut: the gateway cuts nothing.
      await vi.advanceTimersByTimeAsync(60 * 60 * 1000);
      expect(ended).toBeUndefined();
      await reader.cancel();
    } finally {
      vi.useRealTimers();
    }
    // The meter's own reads of the answer end with it: the provider's request is cut.
    await cut("idle-metered");
  });

  it("ends an answer its caller stopped mid-stream cleanly, the provider's request cut",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C2, 1, "sk-good", ANTHROPIC)).blob;
    const named = await anthropicModel();
    const stop = new IdentityTransformStream();
    const answer = await egress.forward("alice", C2, 1, blob, posted("/v1/messages",
      JSON.stringify({ model: named.id, max_tokens: 50, stream: true,
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
    expect(await outcome(answer)).toEqual({ state: "unknown" });
    await cut("stopped");
  });

  it("sends the provider the clamped parse as bytes, with its Content-Length", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const asked = { model: model.id, input: [{ role: "user", content: "— every 😀" }],
                    max_output_tokens: 900_000_000, temperature: 0.5 };
    const answer = await egress.forward("alice", C1, 1, blob, posted("/v1/responses",
      JSON.stringify(asked), steer({ mode: "echo" })), granting([], { max_output_tokens: 77 }),
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
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const pool = gatewaySlots();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    // A call that weighs the whole pool (15 MiB, a non-ASCII character in it: `held.ts`) takes
    // every slot while its reserve is pending.
    let grant: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => { grant = resolve; });
    const whole = JSON.stringify({ model: model.id,
                                   input: `${"x".repeat(15 * 1024 * 1024 - 300)}é` });
    const first = egress.forward("alice", C1, 1, blob, posted("/v1/responses", whole,
      steer({ mode: "held", id: "held-whole" })), async (call) => {
      await pending;
      return granting()(call);
    }, new ReadableStream());
    await until(() => pool.state().held === GATEWAY_POOL_SLOTS, "the pool held");
    // The next waits, first come first served, as many as may wait; one more is `busy`.
    const small = JSON.stringify({ model: model.id, input: "hi" });
    const refusing: Reserve = async () => ({ refused: "egress_spend_refused" });
    const waiting = Array.from({ length: 4 * GATEWAY_POOL_SLOTS }, () => egress.forward(
      "alice", C1, 1, blob, posted("/v1/responses", small), refusing,
      new ReadableStream()));
    await until(() => pool.state().waiting === 4 * GATEWAY_POOL_SLOTS, "the waiters");
    const busy = await egress.forward("alice", C1, 1, blob, posted("/v1/responses", small),
                                      refusing, new ReadableStream());
    expect([busy.response.status, busy.response.headers.get("retry-after"),
            await busy.response.json()])
      .toEqual([503, "1", { error: { code: "busy", message: "busy" } }]);
    // Reserved, the first uploads and lets its slots go at the upload's end: the waiting
    // calls land while the provider still holds its answer, until the test lets it go.
    let answeredYet = false;
    void first.then(() => { answeredYet = true; });
    grant();
    for (const answer of await Promise.all(waiting)) {
      expect(await answer.response.json()).toMatchObject({
        error: { code: "egress_reserve_refused" } });
    }
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    expect(answeredYet).toBe(false);
    await fetch("https://status.test/release?id=held-whole");
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
      .toEqual(["constructor", "fetch", "refresh", "reseal", "resealDoorbell",
                "revokeAtProvider", "ringDoorbell"]);
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
