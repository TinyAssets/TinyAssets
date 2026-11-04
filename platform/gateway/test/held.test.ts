/**
 * The egress review's cases (rveg, REQUEST_CHANGES), as real tests of the fixed behaviour: a
 * metered call held by its weight (H1), its slots taken as its bytes arrive and let go at its
 * stop or its deadline (H2), keys past ASCII and a path-named model's body `model` refused
 * (M1, M2), any throw before the fetch settled `failed` (M3), and a body shorter than its
 * length refused (L1).
 */
import { createExecutionContext } from "cloudflare:test";
import { exports } from "cloudflare:workers";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { aliased, apiRules } from "../src/api-rules";
import { builtinCatalog } from "../src/catalog";
import { BODY_MAX, GATEWAY_POOL_SLOTS, type Reserve } from "../src/forward";
import { heldDeadlineMs } from "../src/held";
import Gateway, { EgressGateway } from "../src/index";
import { learnMeter, meteredRecord } from "../src/meter";
import { isolatePool } from "../../owner/src/slots";
import { GOOGLE, OPENAI } from "./support";

const gateway = (exports as unknown as { default: Service<Gateway> }).default;

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

async function until(check: () => boolean, tries = 1500): Promise<void> {
  for (let n = 0; n < tries; n++) {
    if (check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("timed out");
}

/** A reserve that grants each call as asked, recording each ask. */
function granting(asked: unknown[] = []): Reserve {
  return async (call) => {
    asked.push(call);
    return { metering: { max_output_tokens: 77, input_bytes: BODY_MAX, model: call.model,
                         tier: call.tier } };
  };
}

function posted(path: string, body: string, headers: Record<string, string> = {}): Request {
  return new Request(`https://egress${path}`, {
    method: "POST", body,
    headers: { "content-length": String(new TextEncoder().encode(body).byteLength),
               ...headers } });
}

async function outcome(answer: { outcome: ReadableStream<Uint8Array> }): Promise<unknown> {
  return JSON.parse(await new Response(answer.outcome).text());
}

describe("a key past ASCII, or a body model where the path names it (M1, M2)", () => {
  it("refuses max_tokenſ, ſtream and ſervice_tier, which Go's folding reads as ASCII", () => {
    const completions = apiRules("openai-completions")!;
    expect(aliased(completions, { model: "m", MAX_TOKENS: 9 })).toBe(true);
    expect(aliased(completions, { model: "m", max_tokens: 10, "max_tokenſ": 999_999 }))
      .toBe(true);
    expect(aliased(completions, { model: "m", "max_completion_tokenſ": 999_999 })).toBe(true);
    expect(aliased(completions, { model: "m", "ſervice_tier": "priority" })).toBe(true);
    expect(aliased(completions, { model: "m", stream: true, "ſtream_options":
      { include_usage: false } })).toBe(true);
  });

  it("never sends the long-s cap: the call is refused before any reserve", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi", max_output_tokens: 900_000,
                       ["max_output_tokenſ"]: 900_000 }), { "x-fake-mode": "echo" }),
      granting(asked), new ReadableStream());
    expect([answer.response.status, await answer.response.json()]).toEqual(
      [400, { error: { code: "egress_aliased_key", message: "egress_aliased_key" } }]);
    expect(asked).toEqual([]);
    expect(await outcome(answer)).toEqual({ state: "failed" });
  });

  it("refuses a Google call whose body names a model beside its path's", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", GOOGLE)).blob;
    const catalog = builtinCatalog();
    let path: string | null = null;
    for (const record of catalog.provider("google")!.getModels()
      .filter((candidate) => candidate.api === "google-generative-ai")) {
      const meter = await learnMeter(catalog, GOOGLE.upstream, "sk-fwd", meteredRecord(
        catalog, GOOGLE.upstream, record.id)!);
      if (meter !== null) {
        path = meter.path.replace("{model}", record.id);
        break;
      }
    }
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", "c1", 1, blob, posted(path!, JSON.stringify({
      model: "models/some-dearer-model", contents: [{ role: "user", parts: [{ text: "hi" }] }] }),
      { "x-fake-mode": "echo" }), granting(asked), new ReadableStream());
    expect(await answer.response.json()).toMatchObject({ error: { code: "egress_aliased_key" } });
    expect(asked).toEqual([]);
  });
});

describe("a held call's length, weight and holds (L1, H1, H2, M3)", () => {
  it("refuses a body shorter than its Content-Length, before any reserve", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const text = JSON.stringify({ model: model.id, input: "hi" });
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body: new Blob([text]).stream(),
                                        headers: { "content-length": String(text.length + 1000),
                                                   "x-fake-mode": "echo" } }),
      granting(asked), new ReadableStream());
    expect(await answer.response.json()).toMatchObject({ error: { code: "invalid_request" } });
    expect(asked).toEqual([]);
    expect(isolatePool(GATEWAY_POOL_SLOTS).state().held).toBe(0);
  });

  it("refuses a body whose tree would weigh more than the pool, before its parse", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    // 4 MiB of empty objects: 11.67 times its bytes once parsed, measured.
    const items = "{},".repeat(Math.floor(4 * 1024 * 1024 / 3));
    const text = `{"model":${JSON.stringify(model.id)},"input":[${items}{}]}`;
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses", text),
                                        granting(asked), new ReadableStream());
    expect([answer.response.status, await answer.response.json()]).toEqual(
      [413, { error: { code: "egress_too_large", message: "egress_too_large" } }]);
    expect(asked).toEqual([]);
    expect(isolatePool(GATEWAY_POOL_SLOTS).state().held).toBe(0);
  });

  it("lets every slot go at the caller's stop of a stalled body, another owner's call landing",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const mallory = (await gateway.seal("mallory", "c1", 1, "sk-fwd", OPENAI)).blob;
    const pool = isolatePool(GATEWAY_POOL_SLOTS);
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    // One sandbox declares the bound and sends 10 bytes, then nothing.
    const stalled = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('{"model":"'));
    } });
    const stop = new IdentityTransformStream();
    const first = egress.forward("mallory", "c1", 1, mallory, new Request(
      "https://egress/v1/responses", { method: "POST", body: stalled,
                                        headers: { "content-length": String(BODY_MAX) } }),
      granting(), stop.readable);
    // It holds only what its bytes need: one slot, not the pool.
    await until(() => pool.state().held === 1);
    const small = JSON.stringify({ model: model.id, input: "hi" });
    const other = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses", small,
      { "x-fake-mode": "echo" }), granting(), new ReadableStream());
    expect(other.response.status).toBe(200);
    await other.response.body?.cancel();
    // The caller (the door) stops the call: its read ends, and its slot goes.
    await stop.writable.getWriter().close();
    const stopped = await first;
    expect([stopped.response.status, await stopped.response.json()]).toEqual(
      [499, { error: { code: "aborted", message: "aborted" } }]);
    expect(await outcome(stopped)).toEqual({ state: "failed" });
    await until(() => pool.state().held === 0);
  }, 60_000);

  it("refuses a body stalled past its deadline, its slots let go", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const stalled = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('{"model":"'));
    } });
    const started = Date.now();
    const answer = await egress.forward("alice", "c1", 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body: stalled,
                                        headers: { "content-length": "100" } }),
      granting(), new ReadableStream());
    expect(await answer.response.json()).toMatchObject({ error: { code: "invalid_request" } });
    expect(Date.now() - started).toBeGreaterThanOrEqual(heldDeadlineMs(100) - 50);
    expect(await outcome(answer)).toEqual({ state: "failed" });
    expect(isolatePool(GATEWAY_POOL_SLOTS).state().held).toBe(0);
  }, 30_000);

  it("stops a reserve its caller stopped, its slots let go, nothing sent", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const stop = new IdentityTransformStream();
    let asked = false;
    const answer = egress.forward("alice", "c1", 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi" }), { "x-fake-mode": "echo" }),
      () => {
        asked = true;
        return new Promise(() => undefined);  // the owner's object never answers
      }, stop.readable);
    await until(() => asked);
    await stop.writable.getWriter().close();
    const stopped = await answer;
    expect(await stopped.response.json()).toMatchObject({ error: { code: "aborted" } });
    expect(await outcome(stopped)).toEqual({ state: "failed" });
    expect(isolatePool(GATEWAY_POOL_SLOTS).state().held).toBe(0);
  });

  it("settles `failed`, never `unknown`, a call it threw on after the reserve", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await gateway.seal("alice", "c1", 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    // The reserve's RPC failed after the owner's commit: the gateway sent nothing.
    const reserve: Reserve = async () => {
      throw new Error("the RPC to the owner's object failed after its commit");
    };
    const answer = await egress.forward("alice", "c1", 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi" })), reserve, new ReadableStream());
    expect(answer.response.status).toBe(500);
    expect(await outcome(answer)).toEqual({ state: "failed" });
  });
});
