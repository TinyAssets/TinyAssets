/**
 * Metering a sandbox's raw model call with pi-ai's own adapters (G4): what pi-ai sends as
 * a model call for each API, learned with no network, and the usage it reads back.
 */
import { describe, expect, it } from "vitest";

import { builtinCatalog } from "../src/catalog";
import { meteredCall } from "../src/credential";
import { clamped, learnMeter, meteredModel, meteredRecord, meteredUsage } from "../src/meter";
import { ANTHROPIC, GOOGLE, OPENAI } from "./support";

const GROQ = Object.freeze({
  provider: "groq", api: "openai-completions", base_url: "https://api.groq.com/openai/v1",
  auth: { header: "authorization", scheme: "Bearer" } });

function sse(frames: readonly (readonly [string | null, unknown])[]): string {
  return frames.map(([event, data]) =>
    `${event === null ? "" : `event: ${event}\n`}data: ${JSON.stringify(data)}\n\n`).join("");
}

function streamOf(text: string): ReadableStream<Uint8Array> {
  return new Response(text).body!;
}

describe("the meter pi-ai's adapter teaches", () => {
  it.each([
    ["Anthropic Messages", ANTHROPIC.upstream,
     { method: "POST", path: "/v1/messages", model_field: ["model"], output_field: ["max_tokens"] }],
    ["OpenAI Responses", OPENAI.upstream,
     { method: "POST", path: "/v1/responses", model_field: ["model"],
       output_field: ["max_output_tokens"] }],
    ["OpenAI chat completions (Groq)", GROQ,
     { method: "POST", path: "/openai/v1/chat/completions", model_field: ["model"] }],
    ["Google Generative AI", GOOGLE.upstream,
     { method: "POST", model_field: null, output_field: ["generationConfig", "maxOutputTokens"] }],
  ])("for %s", async (_what, upstream, expected) => {
    const meter = await learnMeter(builtinCatalog(), upstream, "sk-x");
    expect(meter).toMatchObject(expected);
    expect(meter!.output_field.length).toBeGreaterThan(0);
  });

  it("names Google's model in the path, and reads it back", async () => {
    const meter = (await learnMeter(builtinCatalog(), GOOGLE.upstream, "AIza-x"))!;
    expect(meter.path).toMatch(/^\/v1beta\/models\/\{model\}:streamGenerateContent$/);
    const path = "/v1beta/models/gemini-2.5-flash:streamGenerateContent";
    expect(meteredModel(meter, "POST", path, {})).toBe("gemini-2.5-flash");
    expect(meteredCall({ base_url: GOOGLE.upstream.base_url, endpoints: GOOGLE.endpoints, meter },
                       "POST", `${path}?alt=sse`)).toBe(true);
    expect(meteredModel(meter, "POST", "/v1beta/models/a/b:streamGenerateContent", {})).toBeNull();
    expect(meteredModel(meter, "GET", path, {})).toBeNull();
  });

  it("reads the model from the body, and clamps the output cap there", async () => {
    const meter = (await learnMeter(builtinCatalog(), ANTHROPIC.upstream, "sk-x"))!;
    expect(meteredModel(meter, "POST", "/v1/messages", { model: "claude-x" })).toBe("claude-x");
    expect(meteredModel(meter, "POST", "/v1/messages", {})).toBeNull();
    expect(meteredModel(meter, "POST", "/v1/models", { model: "claude-x" })).toBeNull();
    expect(clamped(meter, { model: "m", max_tokens: 50_000 }, 1000)).toEqual({ model: "m",
                                                                              max_tokens: 1000 });
    expect(clamped(meter, { model: "m", max_tokens: 10 }, 1000)).toEqual({ model: "m",
                                                                          max_tokens: 10 });
    expect(clamped(meter, { model: "m" }, 1000)).toEqual({ model: "m", max_tokens: 1000 });
    expect(clamped(meter, { model: "m", max_tokens: "lots" }, 1000))
      .toEqual({ model: "m", max_tokens: 1000 });
    const google = (await learnMeter(builtinCatalog(), GOOGLE.upstream, "AIza-x"))!;
    expect(clamped(google, { contents: [] }, 7)).toEqual({ contents: [],
                                                          generationConfig: { maxOutputTokens: 7 } });
  });

  it("reads an Anthropic stream's usage with pi-ai's parser, priced by the catalog", async () => {
    const catalog = builtinCatalog();
    const [first] = catalog.provider("anthropic")!.getModels()
      .filter((model) => model.api === "anthropic-messages");
    const model = meteredRecord(catalog, ANTHROPIC.upstream, first!.id)!;
    const body = sse([
      ["message_start", { type: "message_start", message: { id: "m1", type: "message",
        role: "assistant", model: first!.id, content: [], stop_reason: null,
        usage: { input_tokens: 1000, output_tokens: 1 } } }],
      ["content_block_start", { type: "content_block_start", index: 0,
                                content_block: { type: "text", text: "" } }],
      ["content_block_delta", { type: "content_block_delta", index: 0,
                                delta: { type: "text_delta", text: "hi" } }],
      ["content_block_stop", { type: "content_block_stop", index: 0 }],
      ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn" },
                          usage: { output_tokens: 500 } }],
      ["message_stop", { type: "message_stop" }],
    ]);
    const usage = await meteredUsage(catalog, ANTHROPIC.upstream, "sk-x", model, 200,
                                     new Headers({ "content-type": "text/event-stream" }),
                                     streamOf(body));
    expect(usage).not.toBeNull();
    expect(usage!.tokens).toBe(1500);
    const expected = (1000 * first!.cost.input + 500 * first!.cost.output) / 1_000_000;
    expect(usage!.cost).toBeCloseTo(expected, 9);
  });

  it("reads nothing from an answer that is not pi-ai's stream, or is cut", async () => {
    const catalog = builtinCatalog();
    const [first] = catalog.provider("anthropic")!.getModels()
      .filter((model) => model.api === "anthropic-messages");
    const model = meteredRecord(catalog, ANTHROPIC.upstream, first!.id)!;
    const json = await meteredUsage(catalog, ANTHROPIC.upstream, "sk-x", model, 200,
                                    new Headers({ "content-type": "application/json" }),
                                    streamOf(JSON.stringify({ usage: { input_tokens: 1 } })));
    expect(json).toBeNull();
    const cut = await meteredUsage(catalog, ANTHROPIC.upstream, "sk-x", model, 200,
                                   new Headers({ "content-type": "text/event-stream" }),
                                   new ReadableStream({ start(c) { c.error(new Error("cut")); } }));
    expect(cut).toBeNull();
    expect(meteredRecord(catalog, ANTHROPIC.upstream, "no-such-model")).toBeNull();
  });
});
