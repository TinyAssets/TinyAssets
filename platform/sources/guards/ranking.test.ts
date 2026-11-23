/**
 * The model ranking (`src/ranking.ts`): the platform's file read whole, every engine that
 * signs in ranked by its plan's model (so no plan is ever unknown on the platform's
 * ranking), a model a run can call of every bundled key source's catalog ranked, what a
 * source marks as not interactive, where a model is placed, and a file that is no ranking
 * refused whole.
 */
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";
import { describe, expect, it } from "vitest";

import { bundledRecords } from "../src/catalog";
import { sourceRecord } from "../src/check";
import { interactive, rankingOf, shippedRanking } from "../src/ranking";
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
    expect(plans.map((record) => record.id)).toEqual(["claude-code", "codex", "gemini"]);
    const tiers = Object.fromEntries(plans.map((record) => [record.id, shippedRanking()
      .place(planProvider(record), record.cli.login.model.id)?.tier]));
    expect(tiers).toEqual({ "claude-code": 0, codex: 0, gemini: 1 });
  });

  it("ranks a model a run can call of every bundled key source's catalog, so none runs only " +
     "unknown", () => {
    const keyed = bundledRecords().map(sourceRecord).filter((record) =>
      record.upstream !== undefined);
    expect(keyed.map((record) => record.upstream!.provider))
      .toEqual(expect.arrayContaining(["mistral", "groq", "cerebras", "openrouter"]));
    for (const record of keyed) {
      const provider = record.upstream!.provider;
      expect(catalog(provider).some((id) => interactive(record, id)
        && shippedRanking().place(provider, id) !== null), record.id).toBe(true);
    }
  });

  it("marks as not interactive only what its source's catalog lists, its batch variants",
     () => {
    const marked = bundledRecords().map(sourceRecord).filter((record) =>
      record.not_interactive !== undefined);
    expect(marked.map((record) => record.id)).toEqual(["openrouter_user_models_v1"]);
    for (const record of marked) {
      const ids = catalog(record.upstream!.provider);
      for (const pattern of record.not_interactive!) {
        expect(ids.some((id) => !interactive({ ...record, not_interactive: [pattern] }, id)),
               pattern).toBe(true);
      }
    }
    const openrouter = marked[0]!;
    expect(interactive(openrouter, "anthropic/claude-fable-5.1:batch")).toBe(false);
    expect(interactive(openrouter, "~anthropic/claude-fable-latest")).toBe(true);
    expect(interactive(openrouter, "nvidia/nemotron-3-ultra-550b-a55b:free")).toBe(true);
    expect(interactive(undefined, "anthropic/claude-fable-5.1:batch")).toBe(true);
  });
});

/** The model ids pi-ai's catalog lists for `provider`. */
function catalog(provider: string): string[] {
  return builtinProviders().find((entry) => entry.id === provider)!.getModels()
    .map((model) => model.id);
}

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
