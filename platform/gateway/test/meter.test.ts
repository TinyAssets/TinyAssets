/**
 * Metering a sandbox's raw model call with pi-ai's own adapters (G4): what pi-ai sends as
 * a model call for each API, learned with no network, and the usage it reads back.
 */
import { describe, expect, it } from "vitest";

import { calculateCost } from "@earendil-works/pi-ai/models";

import { apiRules, fallbackList, HINT_KEYS, manyChoices, paidTool, tierMultiplier,
         unpricedOption } from "../src/api-rules";
import { builtinCatalog } from "../src/catalog";
import { meteredCall } from "../src/credential";
import { clamped, describes, learnMeter, meteredModel, meteredRecord, meteredUsage,
         snapshotOf } from "../src/meter";
import { ANTHROPIC, GOOGLE, OPENAI } from "./support";

const GROQ = Object.freeze({
  provider: "groq", api: "openai-completions", base_url: "https://api.groq.com/openai/v1",
  auth: { header: "authorization", scheme: "Bearer" } });
const OPENROUTER = Object.freeze({
  provider: "openrouter", api: "openai-completions", base_url: "https://openrouter.ai/api/v1",
  auth: { header: "authorization", scheme: "Bearer" } });
const SSE = new Headers({ "content-type": "text/event-stream" });

function chunk(data: unknown): string {
  return `data: ${JSON.stringify(data)}\n\n`;
}

/** A chat-completions stream from `model` (none named when null), with `usage` or none. */
function completion(model: string | null, usage: unknown | null): string {
  const named = model === null ? {} : { model };
  return [
    chunk({ id: "c1", object: "chat.completion.chunk", ...named, choices: [
      { index: 0, delta: { role: "assistant", content: "lots of words" }, finish_reason: null }] }),
    chunk({ id: "c1", object: "chat.completion.chunk", ...named, choices: [
      { index: 0, delta: {}, finish_reason: "stop" }] }),
    ...(usage === null ? [] : [chunk({ id: "c1", object: "chat.completion.chunk", ...named,
                                       choices: [], usage })]),
    "data: [DONE]\n\n",
  ].join("");
}

const BIG = { prompt_tokens: 200_000, completion_tokens: 50_000, total_tokens: 250_000 };

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
    const path = "/v1beta/models/model-a:streamGenerateContent";
    expect(meteredModel(meter, "POST", path, {})).toBe("model-a");
    expect(meteredCall({ api: GOOGLE.upstream.api, base_url: GOOGLE.upstream.base_url, endpoints: GOOGLE.endpoints, meter },
                       "POST", `${path}?alt=sse`)).toBe(true);
    expect(meteredModel(meter, "POST", "/v1beta/models/a/b:streamGenerateContent", {})).toBeNull();
    // The owner's open holds the path to one model segment too, as the gateway reads it.
    const scope = { api: GOOGLE.upstream.api, base_url: GOOGLE.upstream.base_url, endpoints: GOOGLE.endpoints, meter };
    for (const other of ["/v1beta/models/a/b:streamGenerateContent",
                         "/v1beta/models/a:b:streamGenerateContent",
                         "/v1beta/models/:streamGenerateContent"]) {
      expect(meteredCall(scope, "POST", other)).toBe(false);
    }
    expect(meteredModel(meter, "GET", path, {})).toBeNull();
  });

  it("reads the model from the body, and clamps the output cap there", async () => {
    const meter = (await learnMeter(builtinCatalog(), ANTHROPIC.upstream, "sk-x"))!;
    expect(meteredModel(meter, "POST", "/v1/messages", { model: "model-x" })).toBe("model-x");
    expect(meteredModel(meter, "POST", "/v1/messages", {})).toBeNull();
    expect(meteredModel(meter, "POST", "/v1/models", { model: "model-x" })).toBeNull();
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

describe("the reviewer's escapes, closed (review round 2)", () => {
  it("settles a stream that reports no usage unknown, and makes a streamed call ask for it",
     async () => {
    const catalog = builtinCatalog();
    const [first] = catalog.provider("groq")!.getModels()
      .filter((model) => model.api === "openai-completions" && model.cost.output > 0);
    const model = meteredRecord(catalog, GROQ, first!.id)!;
    // What a CLI that did not ask for `stream_options.include_usage` gets back.
    expect(await meteredUsage(catalog, GROQ, "gsk-x", model, 200, SSE,
                              streamOf(completion(first!.id, null)))).toBeNull();
    const zero = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
    expect(await meteredUsage(catalog, GROQ, "gsk-x", model, 200, SSE,
                              streamOf(completion(first!.id, zero)))).toBeNull();
    const reported = await meteredUsage(catalog, GROQ, "gsk-x", model, 200, SSE,
                                        streamOf(completion(first!.id, BIG)));
    expect(reported!.tokens).toBe(250_000);
    expect(reported!.cost).toBeGreaterThan(0);
    // The gateway asks for it, as pi-ai's own streaming call does; a call that does not
    // stream is left as it is.
    const meter = (await learnMeter(catalog, GROQ, "gsk-x"))!;
    expect(clamped(meter, { model: "m", stream: true }, 10, "openai-completions"))
      .toMatchObject({ stream: true, stream_options: { include_usage: true } });
    expect(clamped(meter, { model: "m", stream: true, stream_options: { include_usage: false } },
                   10, "openai-completions"))
      .toMatchObject({ stream_options: { include_usage: true } });
    expect(clamped(meter, { model: "m" }, 10, "openai-completions")["stream_options"])
      .toBeUndefined();
  });

  it("prices a call by the dearer of the model it named and the model that served it",
     async () => {
    const catalog = builtinCatalog();
    const models = catalog.provider("openrouter")!.getModels()
      .filter((model) => model.api === "openai-completions");
    const router = meteredRecord(catalog, OPENROUTER, "openrouter/auto")!;
    expect(router.cost.input + router.cost.output).toBe(0);
    const paid = models.find((model) => model.cost.output > 0)!;
    // Routed to a paid model: priced as that model, never at the router's nothing.
    const routed = await meteredUsage(catalog, OPENROUTER, "sk-or-x", router, 200, SSE,
                                      streamOf(completion(paid.id, BIG)));
    const usage = { input: 200_000, output: 50_000, cacheRead: 0, cacheWrite: 0,
                    totalTokens: 250_000,
                    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
    expect(routed!.cost).toBeCloseTo(calculateCost(meteredRecord(catalog, OPENROUTER,
                                                                 paid.id)!, usage).total, 9);
    expect(routed!.cost).toBeGreaterThan(0);
    // Served by a model the catalog does not price, or with no word of what served it.
    expect(await meteredUsage(catalog, OPENROUTER, "sk-or-x", router, 200, SSE,
                              streamOf(completion("someone/unlisted", BIG)))).toBeNull();
    expect(await meteredUsage(catalog, OPENROUTER, "sk-or-x", router, 200, SSE,
                              streamOf(completion(null, BIG)))).toBeNull();
    // A cheap name that a dearer fallback served is priced as the dearer.
    const [cheap, dear] = [...models].filter((model) => model.cost.output > 0)
      .sort((a, b) => a.cost.output - b.cost.output).filter((_m, i, all) =>
        i === 0 || i === all.length - 1);
    const fallback = await meteredUsage(catalog, OPENROUTER, "sk-or-x",
                                        meteredRecord(catalog, OPENROUTER, cheap!.id)!, 200, SSE,
                                        streamOf(completion(dear!.id, BIG)));
    expect(fallback!.cost).toBeCloseTo(calculateCost(meteredRecord(catalog, OPENROUTER,
                                                                   dear!.id)!, usage).total, 9);
  });

  it("meters an API only while its rules describe pi-ai's own call", () => {
    const rules = apiRules("openai-completions")!;
    const pi = { model: "m", stream: true, stream_options: { include_usage: true },
                 max_completion_tokens: 4321 };
    expect(describes(rules, ["max_completion_tokens"], pi)).toBe(true);
    expect(describes(rules, ["max_tokens"], pi)).toBe(true);
    // pi-ai stopped asking for usage, or caps the output in a field the rules do not clamp.
    expect(describes(rules, ["max_completion_tokens"], { ...pi, stream_options: {} })).toBe(false);
    expect(describes(rules, ["max_output"], pi)).toBe(false);
  });

  it("prices a dated snapshot the catalog does not list as the concrete model it was asked " +
     "for, and records it; a router's or a fallback list's stays unknown", async () => {
    const catalog = builtinCatalog();
    const usage = { input: 200_000, output: 50_000, cacheRead: 0, cacheWrite: 0,
                    totalTokens: 250_000,
                    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
    const [named] = catalog.provider("groq")!.getModels()
      .filter((model) => model.api === "openai-completions" && model.cost.output > 0);
    const record = meteredRecord(catalog, GROQ, named!.id)!;
    const snapshot = `${named!.id}-2026-01-01`;
    expect(meteredRecord(catalog, GROQ, snapshot)).toBeNull();
    // A concrete, priced model, answered by its dated snapshot: the named model's price.
    const dated = await meteredUsage(catalog, GROQ, "gsk-x", record, 200, SSE,
                                     streamOf(completion(snapshot, BIG)));
    expect(dated).toEqual({ tokens: 250_000, cost: calculateCost(record, usage).total,
                            served: snapshot });
    // The same answer to a call that listed fallbacks: what served it is not known to cost
    // what the named model costs.
    expect(await meteredUsage(catalog, GROQ, "gsk-x", record, 200, SSE,
                              streamOf(completion(snapshot, BIG)), true)).toBeNull();
    // A zero-priced router served by an unlisted model stays unknown.
    const router = meteredRecord(catalog, OPENROUTER, "openrouter/auto")!;
    expect(await meteredUsage(catalog, OPENROUTER, "sk-or-x", router, 200, SSE,
                              streamOf(completion("someone/unlisted", BIG)), false)).toBeNull();
    // Even one shaped as its own snapshot: a router's price is no model's.
    expect(await meteredUsage(catalog, OPENROUTER, "sk-or-x", router, 200, SSE,
                              streamOf(completion(`${router.id}-2026-01-01`, BIG)))).toBeNull();
    // A served model the catalog lists: the dearer of the two, its id recorded.
    const listed = catalog.provider("openrouter")!.getModels()
      .filter((model) => model.api === "openai-completions" && model.cost.output > 0)
      .sort((a, b) => a.cost.output - b.cost.output);
    const [cheap, dear] = [listed[0]!, listed.at(-1)!];
    const priced = await meteredUsage(catalog, OPENROUTER, "sk-or-x",
                                      meteredRecord(catalog, OPENROUTER, cheap.id)!, 200, SSE,
                                      streamOf(completion(dear.id, BIG)), true);
    expect(priced).toMatchObject({ served: dear.id });
    expect(priced!.cost).toBeCloseTo(calculateCost(meteredRecord(catalog, OPENROUTER, dear.id)!,
                                                   usage).total, 9);
    // The named model answering as itself records nothing.
    expect(await meteredUsage(catalog, GROQ, "gsk-x", record, 200, SSE,
                              streamOf(completion(named!.id, BIG))))
      .toEqual({ tokens: 250_000, cost: calculateCost(record, usage).total });
  });

  it("clamps every output-cap alias the API reads, and refuses several choices", async () => {
    const catalog = builtinCatalog();
    const meter = (await learnMeter(catalog, GROQ, "gsk-x"))!;
    const field = meter.output_field.join(".");
    const other = field === "max_tokens" ? "max_completion_tokens" : "max_tokens";
    const sent = clamped(meter, { model: "m", [other]: 100_000 }, 1_000, "openai-completions");
    expect(sent[field]).toBe(1_000);
    expect(sent[other]).toBe(1_000);
    expect(clamped(meter, { model: "m" }, 1_000, "openai-completions")[other]).toBeUndefined();
    const completions = apiRules("openai-completions")!;
    expect(fallbackList(completions, { models: ["a", "b"] })).toBe(true);
    expect(fallbackList(completions, { models: [] })).toBe(false);
    expect(fallbackList(completions, { model: "a" })).toBe(false);
    expect(fallbackList(apiRules("anthropic-messages")!, { models: ["a"] })).toBe(false);
    expect(manyChoices(completions, { n: 8 })).toBe(true);
    expect(manyChoices(completions, { n: 1 })).toBe(false);
    expect(manyChoices(completions, {})).toBe(false);
    const google = apiRules("google-generative-ai")!;
    expect(manyChoices(google, { generationConfig: { candidateCount: 2 } })).toBe(true);
    expect(manyChoices(google, { generationConfig: { candidateCount: 1 } })).toBe(false);
  });

  it.each([
    ["anthropic-messages", { tools: [{ name: "f", input_schema: {} }] },
     { tools: [{ type: "web_search_20250305", name: "web_search" }] }],
    ["anthropic-messages", { tools: [{ type: "custom", name: "f", input_schema: {} }] },
     { mcp_servers: [{ url: "https://x" }] }],
    ["openai-responses", { tools: [{ type: "function", name: "f" }] },
     { tools: [{ type: "web_search" }] }],
    ["openai-responses", { tools: [{ type: "custom", name: "f" }] },
     { tools: [{ type: "code_interpreter" }] }],
    ["openai-completions", { tools: [{ type: "function", function: { name: "f" } }] },
     { web_search_options: {} }],
    ["openai-completions", {}, { plugins: [{ id: "web" }] }],
    ["google-generative-ai", { tools: [{ functionDeclarations: [] }] },
     { tools: [{ googleSearch: {} }] }],
    ["google-generative-ai", {}, { tools: [{ codeExecution: {} }] }],
  ])("on %s, lets a client's own tools through and refuses a server-side paid one",
     (api, client, paid) => {
    const rules = apiRules(api)!;
    expect(paidTool(rules, client)).toBe(false);
    expect(paidTool(rules, paid)).toBe(true);
  });

  it("reads no usage from a non-streaming JSON answer, which pi-ai's adapters do not parse",
     async () => {
    const catalog = builtinCatalog();
    const [first] = catalog.provider("anthropic")!.getModels()
      .filter((model) => model.api === "anthropic-messages");
    const model = meteredRecord(catalog, ANTHROPIC.upstream, first!.id)!;
    const message = { id: "m1", type: "message", role: "assistant", model: first!.id,
                      content: [{ type: "text", text: "hi" }], stop_reason: "end_turn",
                      usage: { input_tokens: 1000, output_tokens: 500 } };
    expect(await meteredUsage(catalog, ANTHROPIC.upstream, "sk-x", model, 200,
                              new Headers({ "content-type": "application/json" }),
                              streamOf(JSON.stringify(message)))).toBeNull();
  });
});

describe("the price beyond tokens (review round 3)", () => {
  it("takes an uncatalogued served id only as the named model's dated or versioned snapshot",
     async () => {
    for (const [served, named, ok] of [
      ["model-a-2024-08-06", "model-a", true], ["model-a-20250514", "model-a", true],
      ["model-a@001", "model-a", true], ["model-a-v2", "model-a", true],
      ["model-a.1", "model-a", true], ["model-ab", "model-a", false],
      ["model-a-turbo", "model-a", false], ["model-a-", "model-a", false],
      ["other/model-a-2024", "model-a", false], ["model-a-2024-big", "model-a", false]] as const) {
      expect(snapshotOf(served, named)).toBe(ok);
    }
    const catalog = builtinCatalog();
    const models = catalog.provider("openrouter")!.getModels()
      .filter((model) => model.api === "openai-completions" && model.cost.output > 0)
      .sort((a, b) => a.cost.output - b.cost.output);
    const cheap = meteredRecord(catalog, OPENROUTER, models[0]!.id)!;
    // A cheap model named alone, answered as an unrelated dear id the catalog lacks.
    expect(await meteredUsage(catalog, OPENROUTER, "k", cheap, 200, SSE,
                              streamOf(completion("someone/dear-uncatalogued-9", BIG))))
      .toBeNull();
  });

  it("prices a priority-tier call as pi-ai does, and a dearer served model in proportion",
     async () => {
    const catalog = builtinCatalog();
    const [first] = catalog.provider("openai")!.getModels()
      .filter((model) => model.api === "openai-responses" && model.cost.output > 0);
    const record = meteredRecord(catalog, OPENAI.upstream, first!.id)!;
    const usage = { input_tokens: 10_000, output_tokens: 5_000, total_tokens: 15_000,
                    input_tokens_details: { cached_tokens: 0 },
                    output_tokens_details: { reasoning_tokens: 0 } };
    const body = (tier: string) => [
      `event: response.created\ndata: ${JSON.stringify({ type: "response.created",
        response: { id: "r1", status: "in_progress", model: first!.id, output: [] } })}\n\n`,
      `event: response.completed\ndata: ${JSON.stringify({ type: "response.completed",
        response: { id: "r1", status: "completed", model: first!.id, output: [], usage,
                    service_tier: tier } })}\n\n`,
    ].join("");
    const standard = await meteredUsage(catalog, OPENAI.upstream, "sk-x", record, 200, SSE,
                                        streamOf(body("default")));
    const priority = await meteredUsage(catalog, OPENAI.upstream, "sk-x", record, 200, SSE,
                                        streamOf(body("priority")));
    expect(priority!.cost).toBeGreaterThanOrEqual(standard!.cost * 2 - 1e-12);
    // The reservation holds the most pi-ai applies for it.
    const rules = apiRules("openai-responses")!;
    expect(tierMultiplier(rules, "priority")).toBeGreaterThanOrEqual(priority!.cost / standard!.cost);
    expect(tierMultiplier(rules, null)).toBe(1);
    expect(tierMultiplier(rules, "scale")).toBeNull();
    expect(tierMultiplier(apiRules("anthropic-messages")!, "standard_only")).toBe(1);
  });

  it.each([
    ["openai-completions", { service_tier: "priority" }],
    ["openai-completions", { modalities: ["text", "audio"] }],
    ["openai-completions", { audio: { voice: "x" } }],
    ["anthropic-messages", { speed: "fast" }],
    ["anthropic-messages", { inference_geo: "us" }],
    ["anthropic-messages", { service_tier: "auto" }],
    ["google-generative-ai", { generationConfig: { responseModalities: ["TEXT", "IMAGE"] } }],
    ["google-generative-ai", { generationConfig: { speechConfig: {} } }],
  ])("on %s refuses %j, which pi-ai does not price", (api, body) => {
    expect(unpricedOption(apiRules(api)!, body)).toBe(true);
  });

  it.each([
    ["openai-completions", { modalities: ["text"] }],
    ["anthropic-messages", { speed: "standard", inference_geo: "global",
                             service_tier: "standard_only" }],
    ["google-generative-ai", { generationConfig: { responseModalities: ["TEXT"] } }],
    ["openai-responses", { service_tier: "priority" }],
  ])("on %s lets %j through, priced at standard or by pi-ai", (api, body) => {
    expect(unpricedOption(apiRules(api)!, body)).toBe(false);
  });

  it("reads the model where every API's rules say, and the door reads the same keys", () => {
    for (const api of ["anthropic-messages", "openai-completions", "openai-responses",
                       "mistral-conversations", "google-generative-ai"]) {
      const rules = apiRules(api)!;
      if (rules.model !== null) {
        expect(HINT_KEYS).toContain(rules.model);
      }
      if (rules.tier !== null) {
        expect(HINT_KEYS).toContain(rules.tier.key);
      }
    }
    const completions = apiRules("openai-completions")!;
    const pi = { stream_options: { include_usage: true } };
    expect(describes(completions, ["max_tokens"], pi, ["model"])).toBe(true);
    expect(describes(completions, ["max_tokens"], pi, ["engine"])).toBe(false);
    expect(describes(completions, ["max_tokens"], pi, null)).toBe(false);
    const google = apiRules("google-generative-ai")!;
    expect(describes(google, ["generationConfig", "maxOutputTokens"], {}, null)).toBe(true);
    expect(describes(google, ["generationConfig", "maxOutputTokens"], {}, ["model"])).toBe(false);
  });
});
