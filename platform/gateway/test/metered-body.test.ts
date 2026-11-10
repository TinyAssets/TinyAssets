/**
 * A metered call's body as the gateway holds it once (egress shape research, A′): the one
 * parse's keys spelled only one way (`aliased`), and the clamp made in place, which must
 * send the bytes today's copying clamp sent (its oracle, kept here as main had it).
 */
import { describe, expect, it } from "vitest";

import { aliased, apiRules, ruledApis, valueAt } from "../../contract/src/api-rules";
import type { Meter } from "../../contract/src/egress-scope";
import {
  ASCII_BYTE_COST, BYTE_COST, GATEWAY_POOL_SLOTS, heldDeadlineMs, MIN_RATE, parseWeight, TEXT_COST,
  TOKEN_COST, Tokens, weightSlots,
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

/** Today's clamp, as main had it before it went in place: the in-place clamp's oracle. */
function todayClamped(meter: Meter, body: Record<string, unknown>, cap: number,
                      api: string): Record<string, unknown> {
  const objectAt = (at: Record<string, unknown>, path: readonly string[]) => {
    let here = at;
    for (const key of path) {
      const inner = here[key];
      if (typeof inner !== "object" || inner === null || Array.isArray(inner)) {
        here[key] = {};
      }
      here = here[key] as Record<string, unknown>;
    }
    return here;
  };
  const copy = structuredClone(body);
  const rules = apiRules(api);
  const aliases = [meter.output_field,
                   ...(rules?.outputs ?? []).filter((path) =>
                     path.join("\0") !== meter.output_field.join("\0"))];
  for (const [index, path] of aliases.entries()) {
    const owner = index === 0 ? objectAt(copy, path.slice(0, -1))
      : valueAt(copy, path.slice(0, -1));
    if (typeof owner !== "object" || owner === null || Array.isArray(owner)) {
      continue;
    }
    const at = owner as Record<string, unknown>;
    const last = path.at(-1)!;
    if (index > 0 && !Object.hasOwn(at, last)) {
      continue;
    }
    const asked = at[last];
    at[last] = typeof asked === "number" && Number.isSafeInteger(asked) && asked >= 1
      ? Math.min(asked, cap) : cap;
  }
  if (rules !== null && (rules.stream === null || valueAt(copy, rules.stream) === true)) {
    for (const [path, value] of rules.usage) {
      objectAt(copy, path.slice(0, -1))[path.at(-1)!] = value;
    }
  }
  for (const { path, default_when_absent: value } of rules?.unpriced ?? []) {
    if (value !== undefined && valueAt(copy, path) === undefined) {
      objectAt(copy, path.slice(0, -1))[path.at(-1)!] = value;
    }
  }
  return copy;
}

describe("the clamp in place", () => {
  it("sends the bytes today's copying clamp sent, on a fuzzed corpus of every API's bodies",
     () => {
    let seed = 0x5eed;
    const random = () => {
      seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
      return seed / 2_147_483_648;
    };
    const pick = <T>(items: readonly T[]): T => items[Math.floor(random() * items.length)]!;
    const texts = ["", "hi", "— em", "😀", "q\"uote", "back\\slash", "\u0000",
                   "line\nbreak", "x".repeat(300)];
    let cases = 0;
    for (const api of ruledApis()) {
      const rules = apiRules(api)!;
      const meter: Meter = { method: "POST", path: "/x",
                             model_field: rules.model === null ? null : [rules.model],
                             output_field: rules.outputs[0]! };
      const keys = [...new Set([...rules.outputs, ...rules.counts,
                                ...rules.usage.map(([path]) => path),
                                ...rules.unpriced.map(({ path }) => path),
                                ...(rules.stream === null ? [] : [rules.stream])].flat()),
                    "messages", "input", "contents", "temperature", "metadata"];
      const value = (depth: number): unknown => {
        switch (Math.floor(random() * (depth > 2 ? 6 : 8))) {
          case 0: return pick([0, 1, 7, -3, 2.5, 1e300, 900_000_000, Number.MAX_SAFE_INTEGER + 2]);
          case 1: return pick(texts);
          case 2: return pick([true, false]);
          case 3: return null;
          case 4: return pick(["standard_only", "flex", "text", "TEXT"]);
          case 5: return pick([[], ["text"], ["TEXT", "AUDIO"]]);
          case 6: return Array.from({ length: Math.floor(random() * 3) }, () => value(depth + 1));
          default: return object(depth + 1);
        }
      };
      const object = (depth: number): Record<string, unknown> => {
        const out: Record<string, unknown> = {};
        for (let n = Math.floor(random() * 6); n > 0; n--) {
          out[pick(keys)] = value(depth);
        }
        return out;
      };
      for (let i = 0; i < 1500; i++) {
        const body = { model: "m", ...object(0) };
        const cap = pick([1, 10, 1024, 999_999]);
        const before = JSON.stringify(todayClamped(meter, body, cap, api));
        const input = structuredClone(body);
        const after = clamped(meter, input, cap, api);
        expect(after).toBe(input);  // in place: the parse itself, never a copy
        expect(JSON.stringify(after)).toBe(before);
        cases++;
      }
    }
    expect(cases).toBe(ruledApis().length * 1500);
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
    expect(GATEWAY_POOL_SLOTS).toBe(16);
    expect(parseWeight(100, 0, true)).toBe(ASCII_BYTE_COST * 100);
    expect(parseWeight(100, 0, false)).toBe(BYTE_COST * 100);
    expect(parseWeight(0, 10, true)).toBe(TOKEN_COST * 10);
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
    expect(MIN_RATE).toBe(UNIT_BYTES / TEXT_COST / (SLOT_WAIT_MS / 1000));
    expect(heldDeadlineMs(0)).toBe(SLOT_WAIT_MS);
    expect(heldDeadlineMs(EGRESS_BODY_MAX))
      .toBe(SLOT_WAIT_MS + Math.ceil(1000 * EGRESS_BODY_MAX / MIN_RATE));
    expect(heldDeadlineMs(EGRESS_BODY_MAX)).toBeLessThan(120_000);
  });
});
