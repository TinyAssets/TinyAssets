/**
 * The model APIs' rules (`src/api-rules.ts`) on hand-written calls and answers: the free
 * calls' citations, the paid tools, the unpriced options, a key spelled another way, and
 * the tier an answer's stream says it was served at. The rules held to pi-ai's own calls and
 * the source records are the gateway's (`gateway/test/meter.test.ts`), where pi-ai is.
 */
import { describe, expect, it } from "vitest";

import {
  aliased, apiRules, paidTool, servedPriced, supportedApis, unpricedOption,
} from "../src/api-rules";

function sse(frames: readonly (readonly [string | null, unknown])[]): string {
  return frames.map(([event, data]) =>
    `${event === null ? "" : `event: ${event}\n`}data: ${JSON.stringify(data)}\n\n`).join("");
}

function streamOf(text: string): ReadableStream<Uint8Array> {
  return new Response(text).body!;
}

describe("the free calls", () => {
  it("cites a page and the day it was read for every free call", () => {
    expect(supportedApis()).toContain("anthropic-messages");
    for (const api of supportedApis()) {
      for (const call of apiRules(api)!.free) {
        expect(call.cite, api).toMatch(/^https:\/\/[^/]+\//);
        expect(call.read, api).toMatch(/^20\d\d-\d\d-\d\d$/);
        expect(call.says.length, api).toBeGreaterThan(0);
        expect(["POST", "PUT", "PATCH", "DELETE"], api).toContain(call.method);
      }
    }
  });
});

describe("the price beyond tokens", () => {
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
    // OpenRouter's, on both its APIs: a plugin, and a router-run tool loop of several calls.
    ["openai-completions", {}, { deferred_tools: { protocol: "v1", profile: "portable" } }],
    ["anthropic-messages", {}, { plugins: [{ id: "web" }] }],
    ["anthropic-messages", {}, { deferred_tools: { protocol: "v1", profile: "portable" } }],
    ["google-generative-ai", { tools: [{ functionDeclarations: [] }] },
     { tools: [{ googleSearch: {} }] }],
    ["google-generative-ai", {}, { tools: [{ codeExecution: {} }] }],
  ])("on %s, lets a client's own tools through and refuses a server-side paid one",
     (api, client, paid) => {
    const rules = apiRules(api)!;
    expect(paidTool(rules, client)).toBe(false);
    expect(paidTool(rules, paid)).toBe(true);
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
});

describe("a key the rules read, spelled another way", () => {
  it("is refused at every level the rules read: Google's snake_case, any case-folded alias",
     () => {
    const google = apiRules("google-generative-ai")!;
    for (const body of [{ generation_config: { max_output_tokens: 99_999 } },
                        { generationConfig: { max_output_tokens: 99_999 } },
                        { generationConfig: { candidate_count: 4 } },
                        { generationConfig: { response_modalities: ["AUDIO"] } },
                        { GenerationConfig: {} }]) {
      expect(aliased(google, body)).toBe(true);
    }
    // A path-named model (Google's) is never also named in the body, in any spelling.
    for (const body of [{ model: "models/dearer", contents: [] }, { Model: "x" },
                        { "mod\u0435l": "x" }]) {
      expect(aliased(google, body)).toBe(true);
    }
    const completions = apiRules("openai-completions")!;
    // A key past ASCII at a read level: Go folds U+017F to s and the Kelvin sign to k, and no
    // provider's schema has one.
    for (const body of [{ model: "m", max_tokens: 10, "max_token\u017f": 999_999 },
                        { model: "m", "max_completion_token\u017f": 999_999 },
                        { model: "m", "\u017fervice_tier": "priority" },
                        { model: "m", stream: true, "\u017ftream_options": {} },
                        { model: "m", "tool\u017f": [{ type: "browser_search" }] },
                        { model: "m", "\u212aey": 1 },
                        { model: "m", tools: [{ "typ\u0435": "browser_search" }] }]) {
      expect(aliased(completions, body)).toBe(true);
    }
    for (const body of [{ model: "m", MAX_TOKENS: 999_999 }, { model: "m", Model: "other" },
                        { model: "m", maxCompletionTokens: 9 }, { model: "m", N: 3 },
                        { model: "m", streamOptions: { include_usage: false } },
                        { model: "m", stream_options: { includeUsage: false } },
                        { model: "m", tools: [{ type: "function", Type: "browser_search" }] },
                        { model: "m", Service_Tier: "flex" }]) {
      expect(aliased(completions, body)).toBe(true);
    }
  });

  it("lets every body pi-ai and the engines send through, and keys the rules never read", () => {
    expect(aliased(apiRules("google-generative-ai")!, {
      contents: [{ role: "user", parts: [{ text: "hi" }] }],
      generationConfig: { maxOutputTokens: 10, temperature: 0.2, thinking_config: {} },
      tools: [{ functionDeclarations: [] }], system_instruction: {} })).toBe(false);
    expect(aliased(apiRules("anthropic-messages")!, {
      model: "m", max_tokens: 10, stream: true, system: "s", messages: [],
      tools: [{ name: "f", input_schema: {} }], metadata: { user_id: "u" } })).toBe(false);
    expect(aliased(apiRules("openai-responses")!, {
      model: "m", input: "hi", max_output_tokens: 10, service_tier: "auto",
      tools: [{ type: "function", name: "f" }] })).toBe(false);
    expect(aliased(apiRules("openai-completions")!, "not an object")).toBe(false);
  });
});

describe("the tier an answer was served at", () => {
  it("settles an answer served at a tier pi-ai does not price unknown", async () => {
    const rules = apiRules("anthropic-messages")!;
    const answer = (start: unknown, delta: unknown) => streamOf(sse([
      ["message_start", { type: "message_start", message: { id: "m1", usage: start } }],
      ["message_delta", { type: "message_delta", usage: delta }],
      ["message_stop", { type: "message_stop" }]]));
    const priced = async (stream: ReadableStream<Uint8Array>, api = rules) =>
      (await servedPriced(api, stream)).priced;
    expect(await priced(answer({ input_tokens: 1 }, { output_tokens: 1 }))).toBe(true);
    expect(await priced(answer({ input_tokens: 1, service_tier: "standard" },
                               { output_tokens: 1 }))).toBe(true);
    expect(await priced(answer({ input_tokens: 1, service_tier: "priority" },
                               { output_tokens: 1 }))).toBe(false);
    expect(await priced(answer({ input_tokens: 1 }, { output_tokens: 1, service_tier: "batch" })))
      .toBe(false);
    expect(await priced(answer({ input_tokens: 1, service_tier: 2 }, {}))).toBe(false);
    // An API with no such rule: whatever its answer says.
    expect(await priced(answer({ service_tier: "priority" }, {}),
                        apiRules("google-generative-ai")!)).toBe(true);
  });

  it("reads a Responses answer's tier from its terminal event alone, as pi-ai does", async () => {
    const rules = apiRules("openai-responses")!;
    const events = (created: string | null, completed: string | null, terminal =
      "response.completed") => streamOf(sse([
      ["response.created", { type: "response.created", response: {
        id: "r1", ...(created === null ? {} : { service_tier: created }) } }],
      [terminal, { type: terminal, response: {
        id: "r1", ...(completed === null ? {} : { service_tier: completed }) } }]]));
    // An echoed `auto` at the start is what was asked, not what served.
    expect(await servedPriced(rules, events("auto", "default")))
      .toEqual({ priced: true, seen: true });
    expect(await servedPriced(rules, events("auto", "priority")))
      .toEqual({ priced: true, seen: true });
    expect(await servedPriced(rules, events("auto", "priority", "response.incomplete")))
      .toEqual({ priced: true, seen: true });
    expect(await servedPriced(rules, events("auto", "scale"))).toEqual({ priced: false, seen: true });
    // No tier at the end: unseen, so settled at the dearest the API allows.
    expect(await servedPriced(rules, events("priority", null)))
      .toEqual({ priced: true, seen: false });
  });

  it("decodes SSE as its specification does: CR, LF and CRLF endings, multi-line data",
     async () => {
    const rules = apiRules("anthropic-messages")!;
    const event = { type: "message_delta", usage: { output_tokens: 1, service_tier: "priority" } };
    const json = JSON.stringify(event, null, 1);
    const multi = json.split("\n").map((part) => `data: ${part}`);
    for (const ending of ["\n", "\r\n", "\r"]) {
      const one = `event: message_delta${ending}data: ${JSON.stringify(event)}${ending}${ending}`;
      const split = `event: message_delta${ending}${multi.join(ending)}${ending}${ending}`;
      for (const text of [one, split]) {
        // Whole, and split at every byte boundary, as chunks may fall.
        for (let at = 0; at <= text.length; at++) {
          const chunks = [text.slice(0, at), text.slice(at)];
          const stream = new ReadableStream<Uint8Array>({
            start(controller) {
              for (const chunk of chunks) {
                controller.enqueue(new TextEncoder().encode(chunk));
              }
              controller.close();
            },
          });
          expect(await servedPriced(rules, stream)).toEqual({ priced: false, seen: true });
        }
      }
    }
    // A stream that names no tier: priced, unseen; and an event the stream ended without
    // its blank line is still read.
    expect(await servedPriced(rules, streamOf(`data: ${JSON.stringify({ usage: {} })}\n\n`)))
      .toEqual({ priced: true, seen: false });
    expect(await servedPriced(rules, streamOf(`data: ${JSON.stringify(event)}`)))
      .toEqual({ priced: false, seen: true });
    // A field with no space after its colon, as the specification allows.
    expect(await servedPriced(rules, streamOf(`data:${JSON.stringify(event)}\n\n`)))
      .toEqual({ priced: false, seen: true });
  });

  it("settles a chat-completions answer served at a tier other than the standard unknown",
     async () => {
    const rules = apiRules("openai-completions")!;
    const answer = (tier: unknown) => streamOf(
      `data: ${JSON.stringify({ id: "c1", choices: [],
                                ...(tier === undefined ? {} : { service_tier: tier }) })}\n\n` +
      "data: [DONE]\n\n");
    for (const tier of [undefined, "default", "on_demand"]) {
      expect((await servedPriced(rules, answer(tier))).priced).toBe(true);
    }
    for (const tier of ["priority", "flex", "scale", "performance", 1]) {
      expect((await servedPriced(rules, answer(tier))).priced).toBe(false);
    }
  });
});
