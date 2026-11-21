/**
 * The model ranking (`src/ranking.ts`): the platform's file read whole, every engine that
 * signs in ranked by its plan's model (so no plan is ever unknown on the platform's
 * ranking), a model of every bundled key source's catalog ranked, where a model is placed, and a file that is no ranking refused whole.
 */
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";
import { describe, expect, it } from "vitest";

import { bundledRecords } from "../src/catalog";
import { sourceRecord } from "../src/check";
import { rankingOf, shippedRanking } from "../src/ranking";
import { planProvider, served, signsIn } from "../src/served";
import shipped from "../src/model-ranking.json" with { type: "json" };

const FILE = "/workspace/model-ranking.json";

describe("the platform's ranking", () => {
  it("is tiers of entries, each a provider and a model pattern", () => {
    expect(() => shippedRanking()).not.toThrow();
    expect(shipped.tiers.length).toBeGreaterThan(1);
  });

  it("ranks every engine that signs in by its plan's model, the Claude and Codex plans in " +
     "the top tier", () => {
    const plans = [...served().values()].filter(signsIn);
    expect(plans.map((record) => record.id)).toEqual(["claude-code", "codex", "gemini", "kimi"]);
    const tiers = Object.fromEntries(plans.map((record) => [record.id, shippedRanking()
      .place(planProvider(record), record.cli.login.model.id)?.tier]));
    expect(tiers).toEqual({ "claude-code": 0, codex: 0, gemini: 1, kimi: 3 });
  });

  it("ranks a model of every bundled key source's catalog, so none runs only unknown", () => {
    const providers = builtinProviders();
    const keyed = bundledRecords().map(sourceRecord).flatMap((record) =>
      record.upstream === undefined ? [] : [record.upstream.provider]);
    expect(keyed).toEqual(expect.arrayContaining(["mistral", "groq", "cerebras"]));
    for (const provider of keyed) {
      expect(providers.find((entry) => entry.id === provider)!.getModels()
        .some((model) => shippedRanking().place(provider, model.id) !== null), provider).toBe(true);
    }
  });
});

describe("a ranking", () => {
  const ranking = rankingOf({ version: 2, tiers: [
    [{ provider: "anthropic", model: "claude-*-5" }, { provider: "*", model: "x" }],
    [{ provider: "*", model: "claude.opus.5" }]] }, FILE);

  it("places a model at its first entry, by provider exactly or any, and a pattern against " +
     "the id or its part after the last /", () => {
    expect(ranking.place("anthropic", "claude-opus-5")).toEqual({ tier: 0, entry: 0 });
    expect(ranking.place("openrouter", "vendor/x")).toEqual({ tier: 0, entry: 1 });
    expect(ranking.place("openrouter", "anthropic/claude.opus.5")).toEqual({ tier: 1, entry: 2 });
    // Only * is special: a dot is a dot.
    expect(ranking.place("anthropic", "claudeXopusX5")).toBeNull();
    // An unmatched model, and a provider the entry does not name, are unknown.
    expect(ranking.place("openrouter", "anthropic/claude-opus-5")).toBeNull();
    expect(ranking.place("openrouter", "xx")).toBeNull();
  });

  it("is refused whole when it is no ranking, its file named", () => {
    const entry = { provider: "*", model: "m" };
    for (const bad of [null, [], { version: 1, tiers: [[entry]] }, { version: 2 },
                       { version: 2, tiers: [] }, { version: 2, tiers: [[]] },
                       { version: 2, tiers: [entry] }, { version: 2, tiers: [[entry]], x: 1 },
                       { version: 2, tiers: [[entry]], about: 1 },
                       { version: 2, tiers: [[{ ...entry, extra: 1 }]] },
                       { version: 2, tiers: [[{ provider: "*" }]] },
                       { version: 2, tiers: [[{ ...entry, provider: "A B" }]] },
                       { version: 2, tiers: [[{ ...entry, model: "" }]] },
                       { version: 2, tiers: [[{ ...entry, model: "m".repeat(257) }]] },
                       { version: 2, tiers: [[{ ...entry, model: "a\u0000b" }]] }]) {
      expect(() => rankingOf(bad, FILE), JSON.stringify(bad))
        .toThrow(expect.objectContaining({ code: "invalid",
                                           message: expect.stringMatching(/^\/workspace\//) }));
    }
    expect(rankingOf({ version: 2, about: "mine", tiers: [[entry]] }, FILE).place("x", "m"))
      .toEqual({ tier: 0, entry: 0 });
  });
});
