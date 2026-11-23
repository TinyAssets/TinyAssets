/**
 * Which connected model is strongest (Direction 4), as data: the ranking the virtual engine
 * `auto` ranks the owner's sources by (`owner/src/auto.ts`). The platform ships one
 * (`model-ranking.json`, beside the engine records whose plans' models it must rank); an owner
 * replaces it whole with its own file of the same shape (`owner/src/launch.ts`).
 *
 * A ranking is tiers, strongest first, each a list of entries in order. An entry names a
 * pi-ai provider id, or `*` for any, and a model id in which `*` matches any run of
 * characters, matched against the id and against its part after the last `/` (an
 * aggregator's `vendor/model`). A model ranks at the first entry it matches (`place`); one no
 * entry matches is unknown (`null`), never placed by a guess.
 */
import { keyed } from "../../contract/src/checks";
import { Refusal } from "../../contract/src/refusal";
import { isPrintable, SOURCE_ID } from "../../contract/src/text";

import shipped from "./model-ranking.json" with { type: "json" };

/** Where a model ranks: its tier (0, the top, first) and its entry, counted across tiers. */
export interface Place {
  readonly tier: number;
  readonly entry: number;
}

export interface Ranking {
  /** Where a model of `provider` ranks, or null for one no entry matches. */
  place(provider: string, id: string): Place | null;
}

/** A model pattern as a whole-string matcher: `*` is any run of characters, nothing else special. */
function matcher(pattern: string): RegExp {
  return new RegExp(`^${pattern.split("*").map((part) =>
    part.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`);
}

/** `value`, checked whole as a ranking; `name` is the file it was read from, for the refusal. */
export function rankingOf(value: unknown, name: string): Ranking {
  const invalid = (why: string) => new Refusal("invalid", `${name}: ${why}`);
  if (!keyed(value, ["version", "tiers"], ["about"]) || value["version"] !== 2
      || (value["about"] !== undefined && typeof value["about"] !== "string")) {
    throw invalid("a model ranking holds only version 2, tiers and an optional about text");
  }
  const tiers = value["tiers"];
  if (!Array.isArray(tiers) || tiers.length === 0
      || !tiers.every((tier) => Array.isArray(tier) && tier.length > 0)) {
    throw invalid("its tiers are a list of tiers, strongest first, each a list of entries");
  }
  const entries = (tiers as unknown[][]).flatMap((tier, index) => tier.map((entry) => {
    if (!keyed(entry, ["provider", "model"]) || typeof entry["provider"] !== "string"
        || (entry["provider"] !== "*" && !SOURCE_ID.test(entry["provider"]))
        || typeof entry["model"] !== "string" || entry["model"].length < 1
        || entry["model"].length > 256 || !isPrintable(entry["model"])) {
      throw invalid("each entry is a provider (a provider id, or *) and a model pattern");
    }
    return { tier: index, provider: entry["provider"], model: matcher(entry["model"]) };
  }));
  return {
    place: (provider, id) => {
      const tail = id.slice(id.lastIndexOf("/") + 1);
      const entry = entries.findIndex((one) =>
        (one.provider === "*" || one.provider === provider)
        && (one.model.test(id) || one.model.test(tail)));
      return entry === -1 ? null : { tier: entries[entry]!.tier, entry };
    },
  };
}

/** The platform's own ranking, `model-ranking.json`. */
export function shippedRanking(): Ranking {
  return rankingOf(shipped, "sources/src/model-ranking.json");
}
