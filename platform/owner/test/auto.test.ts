/**
 * `auto`, the owner's strongest connected model (`auto.ts`): the ranking data file, and
 * the order it gives the connected models (Direction 4). The runs slice (R) picks a run's
 * source from it.
 */
import { describe, expect, it } from "vitest";

import { physicalId, rankOf, ranked } from "../src/auto";
import type { ModelInfo, Usable } from "../src/connections";
import ranking from "../src/model-ranking.json" with { type: "json" };

function model(id: string, output = 1): ModelInfo {
  return { id, name: id, reasoning: true, input: ["text"], contextWindow: 100_000,
           maxTokens: 8_000, cost: { input: 1, output, cacheRead: 0, cacheWrite: 0 } };
}

function usable(connection: string, provider: string, created: number,
                ...models: ModelInfo[]): Usable {
  return { connection, source: provider, provider, display_name: connection, created_at: created,
           models };
}

const CONNECTED = [
  usable("c-a", "anthropic", 1, model("claude-sonnet-4-5"), model("claude-fable-5"),
         model("claude-fable-5-1")),
  usable("c-o", "openrouter", 2, model("anthropic/claude-opus-5"), model("vendor/unranked-x", 9),
         model("vendor/unranked-y", 20)),
  usable("c-g", "google", 3, model("gemini-4-argon")),
];

describe("the ranking", () => {
  it("is a data file of provider and model patterns", () => {
    expect(ranking.version).toBe(1);
    expect(ranking.ranks.length).toBeGreaterThan(5);
    expect(ranking.ranks.every((entry) => typeof entry.provider === "string"
                               && typeof entry.model === "string")).toBe(true);
  });

  it("orders the earliest entry first, newer ids first in an entry, then unmatched by " +
     "output price", () => {
    expect(ranked(CONNECTED).map(physicalId)).toEqual([
      "c-a/claude-fable-5-1", "c-a/claude-fable-5", "c-g/gemini-4-argon",
      "c-o/anthropic/claude-opus-5", "c-a/claude-sonnet-4-5", "c-o/vendor/unranked-y",
      "c-o/vendor/unranked-x"]);
  });

  it("matches a provider exactly or any, and a glob against the id or its last part", () => {
    const entries = [{ provider: "anthropic", model: "claude-*-5" }, { provider: "*", model: "x" }];
    expect(rankOf({ provider: "anthropic", model: model("claude-opus-5") }, entries)).toBe(0);
    expect(rankOf({ provider: "openrouter", model: model("anthropic/claude-opus-5") }, entries))
      .toBe(2);
    expect(rankOf({ provider: "openrouter", model: model("vendor/x") }, entries)).toBe(1);
    expect(rankOf({ provider: "openrouter", model: model("xx") }, entries)).toBe(2);
    expect(rankOf({ provider: "anthropic", model: model("claude.opus.5") },
                  [{ provider: "anthropic", model: "claude.opus.5" }])).toBe(0);
    expect(rankOf({ provider: "anthropic", model: model("claudeXopusX5") },
                  [{ provider: "anthropic", model: "claude.opus.5" }])).toBe(1);
  });

  it("breaks a tie by the older connection", () => {
    const twice = [usable("c-new", "anthropic", 9, model("claude-fable-5")),
                   usable("c-old", "anthropic", 1, model("claude-fable-5"))];
    expect(ranked(twice).map(physicalId)).toEqual(["c-old/claude-fable-5", "c-new/claude-fable-5"]);
  });
});

describe("the strongest connected model", () => {
  it("is the first ranked, and none with nothing connected", () => {
    expect(physicalId(ranked(CONNECTED)[0]!)).toBe("c-a/claude-fable-5-1");
    expect(ranked([])).toEqual([]);
    expect(ranked([usable("c-e", "anthropic", 1)])).toEqual([]);
  });
});
