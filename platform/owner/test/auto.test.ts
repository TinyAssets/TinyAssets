/**
 * `auto`, the owner's strongest connected source (`auto.ts`): the order it gives the owner's
 * keys and plans (Direction 4; founder call 4), on the platform's ranking and on an owner's
 * own, for the owner setups it decides between, over the platform's source records. The
 * ranking's file and its reader are the sources guard's (`sources/guards/ranking.test.ts`).
 */
import { describe, expect, it } from "vitest";

import type { ModelRecord } from "../../contract/src/model-record";
import { type Candidate, type Plan, ranked, type Usable } from "../src/auto";
import { bundledSources } from "../../sources/src/catalog";
import { rankingOf, shippedRanking } from "../../sources/src/ranking";

const SOURCES = bundledSources();

/** A candidate as `connection/model`, a plan's as `plan:engine/model`. */
const named = (candidate: Candidate) =>
  `${candidate.connection ?? `plan:${candidate.source}`}/${candidate.model}`;

function model(id: string, output = 1): ModelRecord {
  return { id, name: id, reasoning: true, input: ["text"], contextWindow: 100_000,
           maxTokens: 8_000, cost: { input: 1, output, cacheRead: 0, cacheWrite: 0 } };
}

function key(connection: string, provider: string, created: number,
             ...models: ModelRecord[]): Usable {
  return { connection, source: provider, provider, display_name: connection, created_at: created,
           models };
}

const CLAUDE_PLAN: Plan = { engine: "claude-code", provider: "anthropic",
                            model: "claude-opus-5-5", since: 5 };
const GEMINI_PLAN: Plan = { engine: "gemini", provider: "google",
                            model: "gemini-3.1-pro-preview", since: 5 };

/** The strongest of `keys` and `plans`, on the platform's ranking unless one is given. */
const first = (keys: readonly Usable[], plans: readonly Plan[], ranking = shippedRanking()) =>
  named(ranked(keys, plans, ranking, SOURCES)[0]!);

describe("the strongest source, on the platform's ranking", () => {
  it("is a plan in the top tier over every metered key, one ranked above it in that tier too",
     () => {
    const keys = [key("c-o", "openai", 1, model("gpt-6.1-sol"), model("gpt-5.1-codex"))];
    expect(ranked(keys, [CLAUDE_PLAN], shippedRanking(), SOURCES).map(named)).toEqual([
      "plan:claude-code/claude-opus-5-5", "c-o/gpt-6.1-sol", "c-o/gpt-5.1-codex"]);
  });

  it("is a key a tier above a plan lower down, and the plan over a key of its own tier or " +
     "below", () => {
    expect(first([key("c-o", "openai", 1, model("gpt-6.1-sol"))], [GEMINI_PLAN]))
      .toBe("c-o/gpt-6.1-sol");
    expect(first([key("c-o", "openai", 1, model("gpt-5.6-sol"))], [GEMINI_PLAN]))
      .toBe("plan:gemini/gemini-3.1-pro-preview");
    expect(first([key("c-o", "openai", 1, model("gpt-5.1-codex"))], [GEMINI_PLAN]))
      .toBe("plan:gemini/gemini-3.1-pro-preview");
  });

  it("is, between two plans, the one whose model ranks higher", () => {
    expect(first([], [GEMINI_PLAN, CLAUDE_PLAN])).toBe("plan:claude-code/claude-opus-5-5");
  });

  it("is, between keys, by tier, then entry, newer ids first within an entry, then unknown " +
     "models by their catalog output price", () => {
    const keys = [
      key("c-a", "anthropic", 1, model("claude-sonnet-4-5"), model("claude-fable-5"),
          model("claude-fable-5-1")),
      key("c-o", "openrouter", 2, model("anthropic/claude-opus-5"), model("vendor/unranked-x", 9),
          model("vendor/unranked-y", 20)),
      key("c-g", "google", 3, model("gemini-4-argon")),
    ];
    const order = ranked(keys, [], shippedRanking(), SOURCES);
    expect(order.map(named)).toEqual([
      "c-a/claude-fable-5-1", "c-a/claude-fable-5", "c-g/gemini-4-argon",
      "c-o/anthropic/claude-opus-5", "c-a/claude-sonnet-4-5", "c-o/vendor/unranked-y",
      "c-o/vendor/unranked-x"]);
    expect(order.filter((one) => one.place === null).map(named))
      .toEqual(["c-o/vendor/unranked-y", "c-o/vendor/unranked-x"]);
  });

  it("breaks a tie by the older connection, and is none with nothing connected", () => {
    const twice = [key("c-new", "anthropic", 9, model("claude-fable-5")),
                   key("c-old", "anthropic", 1, model("claude-fable-5"))];
    expect(ranked(twice, [], shippedRanking(), SOURCES).map(named))
      .toEqual(["c-old/claude-fable-5", "c-new/claude-fable-5"]);
    expect(ranked([], [], shippedRanking(), SOURCES)).toEqual([]);
    expect(ranked([key("c-e", "anthropic", 1)], [], shippedRanking(), SOURCES)).toEqual([]);
  });

  it("is never a model the key's source record marks as answering no interactive call",
     () => {
    // The stage finding of 2026-11-23: an OpenRouter key whose catalog lists Claude Fable
    // only as its batch variants and as `~…-latest`.
    const openrouter = (...models: ModelRecord[]): Usable =>
      ({ ...key("c-or", "openrouter", 1, ...models), source: "openrouter_user_models_v1" });
    expect(ranked([openrouter(model("anthropic/claude-fable-5.1:batch", 25),
                              model("~anthropic/claude-fable-latest", 50),
                              model("anthropic/claude-opus-5.5:batch", 10))],
                  [], shippedRanking(), SOURCES).map(named))
      .toEqual(["c-or/~anthropic/claude-fable-latest"]);
    expect(ranked([openrouter(model("openai/gpt-6.1-sol:batch"))], [], shippedRanking(),
                  SOURCES)).toEqual([]);
    // The same id on a source whose record marks nothing (Anthropic's) is a candidate.
    expect(first([key("c-a", "anthropic", 1, model("anthropic/claude-fable-5.1:batch"))], []))
      .toBe("c-a/anthropic/claude-fable-5.1:batch");
  });
});

describe("the strongest source, on an owner's own ranking", () => {
  const keys = [key("c-o", "openai", 1, model("gpt-6.1-sol")),
                key("c-x", "xai", 2, model("grok-5", 50))];

  it("follows the owner's tiers in place of the platform's", () => {
    const own = rankingOf({ version: 2, tiers: [[{ provider: "*", model: "gpt-*" }],
                                                [{ provider: "*", model: "claude-*" }]] }, "own");
    expect(first(keys, [CLAUDE_PLAN], own)).toBe("c-o/gpt-6.1-sol");
  });

  it("puts a plan whose model it does not rank after every ranked key, before unknown keys",
     () => {
    const own = rankingOf({ version: 2, tiers: [[{ provider: "*", model: "gpt-*" }]] }, "own");
    expect(ranked(keys, [CLAUDE_PLAN], own, SOURCES).map(named))
      .toEqual(["c-o/gpt-6.1-sol", "plan:claude-code/claude-opus-5-5", "c-x/grok-5"]);
  });
});
