/**
 * A model's capability record (pi audit F6): pi-ai's own `Model`, carried whole to the
 * owner object, which holds no catalog of its own and lists, prices, compacts and offers
 * thinking levels by it, without what is the gateway's.
 *
 * Kept: the id, name, input types and limits (`inputLimits`: `read`'s image resize),
 * context window, output limit, reasoning flag, thinking-level map (pi's
 * `getSupportedThinkingLevels` offers `xhigh` and `max` only from it), prompt-cache
 * lifetimes, and prices with their long-context tiers (pi-ai's `calculateCost` reads
 * them). Left out: `provider`, `api` and `baseUrl` (the sealed upstream's), and `headers`,
 * `compat` and the sampling parameters, which shape the upstream request and which the
 * gateway's own adapter applies from its own copy of the record; the owner's pi-messages
 * client reads none of them.
 *
 * The same record is what a declared (`custom`) endpoint's source lists, checked whole
 * when it is sealed and again when it is opened (`gateway/src/credential.ts`), and what the
 * owner's object holds every model a connection serves to (`owner/src/connections.ts`): one
 * checker, so a record the gateway lists is one the object commits.
 */
import { count, shape } from "./checks";
import { Failure } from "./failure";

/** pi-ai's thinking levels (`ModelThinkingLevel`). */
export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

/** A price list, dollars per million tokens (pi-ai's `ModelCostRates`). */
export interface CostRates {
  readonly input: number;
  readonly output: number;
  readonly cacheRead: number;
  readonly cacheWrite: number;
}

/** The fields of pi-ai 1.4.0's `Model` the record keeps, as pi-ai types them. */
export interface ModelRecord {
  readonly id: string;
  readonly name: string;
  readonly reasoning: boolean;
  readonly thinkingLevelMap?: { readonly [level in ThinkingLevel]?: string | null };
  readonly promptCache?: { readonly short?: number; readonly long?: number };
  readonly input: ("text" | "image")[];
  readonly inputLimits?: {
    readonly maxRequestBytes?: number;
    readonly images?: {
      readonly resize?: { readonly maxWidth?: number; readonly maxHeight?: number;
                          readonly maxBytes?: number; readonly jpegQuality?: number };
      readonly maxPerMessage?: number;
      readonly maxPerRequest?: number;
    };
  };
  readonly contextWindow: number;
  readonly maxTokens: number;
  readonly cost: CostRates & {
    readonly tiers?: (CostRates & { readonly inputTokensAbove: number })[];
  };
}

/** pi-ai's thinking levels a map may name (`MODEL_THINKING_LEVELS`). */
const LEVELS: readonly string[] = Object.freeze(["off", "minimal", "low", "medium", "high",
                                                 "xhigh", "max"]);
/** The one rule for a model's id and name, the owner's object's and the gateway's alike. */
const MODEL_ID = /^[\x21-\x7e]{1,200}$/;
const NAME = /^[^\p{Cc}]{1,200}$/u;
/** Dollars per million tokens. */
const PRICE_MAX = 1_000_000;
/** A context window, an output limit and a tier's threshold, in tokens. */
const TOKENS_MAX = 100_000_000;
/** A provider's value for a thinking level: a word it takes. */
const LEVEL_VALUE = /^[\x21-\x7e]{1,64}$/;
const TIERS_MAX = 16;

function price(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > PRICE_MAX) {
    throw new Failure("invalid_source");
  }
  return value;
}

function rates(value: Record<string, unknown>) {
  return { input: price(value["input"]), output: price(value["output"]),
           cacheRead: price(value["cacheRead"]), cacheWrite: price(value["cacheWrite"]) };
}

const RATES: readonly string[] = Object.freeze(["input", "output", "cacheRead", "cacheWrite"]);

function cost(value: unknown): ModelRecord["cost"] {
  const fields = shape(value, RATES, ["tiers"]);
  const tiers = fields["tiers"];
  if (tiers === undefined) {
    return Object.freeze(rates(fields));
  }
  if (!Array.isArray(tiers) || tiers.length < 1 || tiers.length > TIERS_MAX) {
    throw new Failure("invalid_source");
  }
  return Object.freeze({
    ...rates(fields),
    tiers: Object.freeze(tiers.map((tier) => {
      const each = shape(tier, [...RATES, "inputTokensAbove"]);
      return Object.freeze({ inputTokensAbove: count(each["inputTokensAbove"], 0, TOKENS_MAX), ...rates(each) });
    })) as NonNullable<ModelRecord["cost"]["tiers"]>,
  });
}

function thinkingLevelMap(value: unknown): NonNullable<ModelRecord["thinkingLevelMap"]> {
  const fields = shape(value, [], LEVELS);
  for (const level of Object.values(fields)) {
    if (level !== null && (typeof level !== "string" || !LEVEL_VALUE.test(level))) {
      throw new Failure("invalid_source");
    }
  }
  return Object.freeze({ ...fields }) as NonNullable<ModelRecord["thinkingLevelMap"]>;
}

function promptCache(value: unknown): NonNullable<ModelRecord["promptCache"]> {
  const fields = shape(value, [], ["short", "long"]);
  for (const lifetime of Object.values(fields)) {
    if (typeof lifetime !== "number" || !Number.isFinite(lifetime) || lifetime <= 0) {
      throw new Failure("invalid_source");
    }
  }
  return Object.freeze({ ...fields }) as NonNullable<ModelRecord["promptCache"]>;
}

/** A record of the named fields, each a positive integer, at most `max`. */
function integers(value: unknown, named: readonly string[], max = Number.MAX_SAFE_INTEGER):
    Record<string, number> {
  return Object.fromEntries(Object.entries(shape(value, [], named)).map(([key, entry]) => {
    const checked = count(entry, 1);
    if (checked > max) {
      throw new Failure("invalid_source");
    }
    return [key, checked];
  }));
}

/** Only the named fields of a record. */
function only(value: Record<string, unknown>, named: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([key]) => named.includes(key)));
}

function inputLimits(value: unknown): NonNullable<ModelRecord["inputLimits"]> {
  const fields = shape(value, [], ["maxRequestBytes", "images"]);
  const images = fields["images"];
  const imageFields = images === undefined ? undefined
    : shape(images, [], ["resize", "maxPerMessage", "maxPerRequest"]);
  const resize = imageFields?.["resize"] === undefined ? undefined
    : shape(imageFields["resize"], [], ["maxWidth", "maxHeight", "maxBytes", "jpegQuality"]);
  return Object.freeze({
    ...integers(only(fields, ["maxRequestBytes"]), ["maxRequestBytes"]),
    ...(imageFields === undefined ? {} : { images: Object.freeze({
      ...integers(only(imageFields, ["maxPerMessage", "maxPerRequest"]),
                  ["maxPerMessage", "maxPerRequest"]),
      ...(resize === undefined ? {} : { resize: Object.freeze({
        ...integers(only(resize, ["maxWidth", "maxHeight", "maxBytes"]),
                    ["maxWidth", "maxHeight", "maxBytes"]),
        ...integers(only(resize, ["jpegQuality"]), ["jpegQuality"], 100),
      }) }),
    }) }),
  });
}

/** A declared model's record, checked whole: `invalid_source` for anything else. */
export function checkedRecord(value: unknown): ModelRecord {
  const fields = shape(value, ["id", "name", "reasoning", "input", "contextWindow", "maxTokens",
                               "cost"], ["thinkingLevelMap", "promptCache", "inputLimits"]);
  if (typeof fields["reasoning"] !== "boolean" || typeof fields["id"] !== "string"
      || !MODEL_ID.test(fields["id"]) || typeof fields["name"] !== "string"
      || !NAME.test(fields["name"])) {
    throw new Failure("invalid_source");
  }
  const input = fields["input"];
  if (!Array.isArray(input) || input.length < 1 || input.length > 2
      || new Set(input).size !== input.length
      || input.some((kind) => kind !== "text" && kind !== "image")) {
    throw new Failure("invalid_source");
  }
  return Object.freeze({
    id: fields["id"],
    name: fields["name"],
    reasoning: fields["reasoning"],
    ...(fields["thinkingLevelMap"] === undefined ? {}
      : { thinkingLevelMap: thinkingLevelMap(fields["thinkingLevelMap"]) }),
    ...(fields["promptCache"] === undefined ? {} : { promptCache: promptCache(fields["promptCache"]) }),
    input: [...input as ("text" | "image")[]],
    ...(fields["inputLimits"] === undefined ? {} : { inputLimits: inputLimits(fields["inputLimits"]) }),
    contextWindow: count(fields["contextWindow"], 1, TOKENS_MAX),
    maxTokens: count(fields["maxTokens"], 1, TOKENS_MAX),
    cost: cost(fields["cost"]),
  });
}
