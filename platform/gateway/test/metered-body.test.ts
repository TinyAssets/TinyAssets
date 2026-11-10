/**
 * A metered call's body as the gateway holds it once (egress shape research, A′): the one
 * parse's keys spelled only one way (`aliased`), the clamp made in place, and its weight.
 */
import { describe, expect, it } from "vitest";

import { aliased, apiRules, ruledApis, valueAt } from "../../contract/src/api-rules";
import type { Meter } from "../../contract/src/egress-scope";
import {
  GATEWAY_POOL_SLOTS, heldDeadlineMs, parseWeight, Tokens, weightSlots,
} from "../src/held";
import { EGRESS_BODY_MAX, SLOT_WAIT_MS } from "../../contract/src/limits";
import { clamped } from "../src/meter";
import { UNIT_BYTES } from "../../contract/src/slots";

describe("a key the rules read, spelled another way (egress shape research §4)", () => {
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
    // A key past ASCII at a read level: Go folds U+017F to s and the Kelvin sign to k (review
    // M1), and no provider's schema has one.
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

describe("the clamp in place", () => {
  it("clamps the parse itself, never a copy, for every API", () => {
    for (const api of ruledApis()) {
      const rules = apiRules(api)!;
      const meter: Meter = { method: "POST", path: "/x",
                             model_field: rules.model === null ? null : [rules.model],
                             output_field: rules.outputs[0]! };
      const body: Record<string, unknown> = { model: "m", messages: [{ text: "hi" }] };
      const after = clamped(meter, body, 10, api);
      expect(after, api).toBe(body);
      expect(valueAt(body, rules.outputs[0]!), api).toBe(10);
      expect(body["messages"], api).toEqual([{ text: "hi" }]);
    }
  });
});

describe("the gateway's weight of a held call (review H1)", () => {
  it("counts the structural tokens as the bytes stream in, strings and escapes kept apart",
     () => {
    const counted = (...pieces: string[]) => {
      const tokens = new Tokens();
      for (const piece of pieces) {
        tokens.feed(new TextEncoder().encode(piece));
      }
      return [tokens.count, tokens.ascii];
    };
    expect(counted('{"a":[1,2,{}],"b":"x"}')).toEqual([11, true]);
    // A string's own brackets, commas, colons and escaped quotes are not structure.
    expect(counted('{"a":"{[,:\\"]}"}')).toEqual([4, true]);
    // A chunk boundary inside an escape or a string changes nothing; non-ASCII is seen.
    expect(counted('{"a":"x\\', '",[]', '"}', ',"\u2014":1}')).toEqual(counted(
      '{"a":"x\\",[]"}', ',"\u2014":1}'));
    expect(counted('{"k":"\u2014"}')).toEqual([4, false]);
    expect(counted("[]".repeat(3))).toEqual([3, true]);
  });

  it("weighs a call by its bytes and its tokens, refusing one past the pool", () => {
    expect(weightSlots(0)).toBe(1);
    expect(weightSlots(UNIT_BYTES)).toBe(1);
    expect(weightSlots(UNIT_BYTES + 1)).toBe(2);
    // The ceilings the costs derive: an ASCII body of the door's 16 MiB fits; a 16 MiB body
    // with a non-ASCII character, or a 4 MiB one of empty objects, does not.
    const pool = GATEWAY_POOL_SLOTS * UNIT_BYTES;
    expect(parseWeight(EGRESS_BODY_MAX, 0, true)).toBeLessThanOrEqual(pool);
    expect(parseWeight(15 * 1024 * 1024, 0, false)).toBeLessThanOrEqual(pool);
    expect(parseWeight(EGRESS_BODY_MAX, 0, false)).toBeGreaterThan(pool);
    expect(parseWeight(4 * 1024 * 1024, 4 * 1024 * 1024 * 2 / 3, true)).toBeGreaterThan(pool);
  });

  it("holds a slot no longer than the pool's wait plus its body at the slowest rate", () => {
    // The pool's wait for an empty upload, about 100 s for the door's largest body.
    expect(heldDeadlineMs(0)).toBe(SLOT_WAIT_MS);
    expect(heldDeadlineMs(EGRESS_BODY_MAX)).toBeGreaterThan(90_000);
    expect(heldDeadlineMs(EGRESS_BODY_MAX)).toBeLessThan(120_000);
  });
});
