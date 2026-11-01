/**
 * What a metered sandbox call must look like for each model API, as data keyed by pi-ai's
 * API id (tasks 4 G4, review round 2): no provider is named here and no code branches on
 * one. `meter.ts` holds every rule against pi-ai's own call, learned with no network, so the
 * data cannot drift from what pi-ai sends:
 *
 * - `outputs`: every body field the API reads as the output-token cap. The one pi-ai sets
 *   must be among them (else the call is not metered), and the gateway clamps each one the
 *   request carries, so no alias escapes the reservation.
 * - `counts`: a field that multiplies the output (several choices or candidates); a call
 *   asking for more than one is refused `egress_multiple_choices`.
 * - `usage`: the fields that make a streamed answer report its usage, set when the call
 *   streams (`stream`, the body's own flag; none for an API whose path streams). pi-ai's own
 *   streaming call must carry each, at the same value.
 * - `fallbacks`: where a request lists other models that may serve it (a fallback list),
 *   so its answer's served model is the only word of what it cost.
 * - `tools` and `paid`: what a client-side tool looks like (its list, and its kind field's
 *   allowed values, or the keys a tool entry may hold), and the top-level fields that enable
 *   a server-side tool the provider bills beyond tokens. A call enabling one, which pi-ai's
 *   price does not cover, is refused `egress_unmetered_tool`.
 * - `model`: the top-level body key the call names its model in (pi-ai's own call must name
 *   it there), or null when the path names it. The egress door reads it (`HINT_KEYS`) so
 *   the owner's object reserves the call for that model; the gateway refuses a body naming
 *   another.
 * - `tier`: a top-level option that multiplies the price and that pi-ai prices from the
 *   answer (so the settle is right): the call is reserved at its value's multiplier, the
 *   highest pi-ai can apply for it, and a value not listed is refused. Absent, the price is
 *   standard.
 * - `unpriced`: options that change the price beyond tokens and that pi-ai does not price
 *   (a premium speed or geography, another tier, a non-text output): refused
 *   `egress_unpriced_option` unless absent or each value among `allowed`.
 */

export interface ApiRules {
  readonly outputs: readonly (readonly string[])[];
  readonly counts: readonly (readonly string[])[];
  readonly stream: readonly string[] | null;
  readonly usage: readonly (readonly [readonly string[], unknown])[];
  readonly tools: {
    readonly list: readonly string[];
    /** The field naming a tool's kind, and the kinds a client runs (null: no kind field). */
    readonly kind?: { readonly field: string; readonly allowed: readonly (string | null)[] };
    /** The only keys a tool entry may hold. */
    readonly keys?: readonly string[];
  };
  readonly paid: readonly (readonly string[])[];
  /** The fields a request lists fallback models in, any one of which may serve it. */
  readonly fallbacks: readonly (readonly string[])[];
  readonly model: string | null;
  readonly tier: { readonly key: string;
                   readonly multipliers: Readonly<Record<string, number>> } | null;
  readonly unpriced: readonly { readonly path: readonly string[];
                                readonly allowed: readonly string[] }[];
}

/** The top-level body keys the egress door reads for the owner's object: model and tier. */
export const HINT_KEYS: readonly string[] = Object.freeze(["model", "service_tier"]);

const COMPLETIONS: ApiRules = Object.freeze({
  outputs: Object.freeze([Object.freeze(["max_tokens"]), Object.freeze(["max_completion_tokens"])]),
  counts: Object.freeze([Object.freeze(["n"])]),
  stream: Object.freeze(["stream"]),
  usage: Object.freeze([Object.freeze([Object.freeze(["stream_options", "include_usage"]), true] as const)]),
  tools: Object.freeze({ list: Object.freeze(["tools"]),
                         kind: Object.freeze({ field: "type", allowed: Object.freeze(["function"]) }) }),
  paid: Object.freeze([Object.freeze(["web_search_options"]), Object.freeze(["plugins"])]),
  fallbacks: Object.freeze([Object.freeze(["models"])]),
  model: "model",
  tier: null,
  unpriced: Object.freeze([
    Object.freeze({ path: Object.freeze(["service_tier"]), allowed: Object.freeze([]) }),
    Object.freeze({ path: Object.freeze(["modalities"]), allowed: Object.freeze(["text"]) }),
    Object.freeze({ path: Object.freeze(["audio"]), allowed: Object.freeze([]) }),
  ]),
});

const RESPONSES: ApiRules = Object.freeze({
  outputs: Object.freeze([Object.freeze(["max_output_tokens"])]),
  counts: Object.freeze([]),
  stream: Object.freeze(["stream"]),
  usage: Object.freeze([]),
  tools: Object.freeze({ list: Object.freeze(["tools"]),
                         kind: Object.freeze({ field: "type",
                                               allowed: Object.freeze(["function", "custom"]) }) }),
  paid: Object.freeze([]),
  fallbacks: Object.freeze([]),
  model: "model",
  // pi-ai prices the answer's tier (twice the standard rate for priority, more for some
  // models): reserved at the most it applies; flex, half price, is reserved at standard.
  tier: Object.freeze({ key: "service_tier",
                        multipliers: Object.freeze({ default: 1, flex: 1, auto: 2.5,
                                                     priority: 2.5 }) }),
  unpriced: Object.freeze([]),
});

const GOOGLE: ApiRules = Object.freeze({
  outputs: Object.freeze([Object.freeze(["generationConfig", "maxOutputTokens"])]),
  counts: Object.freeze([Object.freeze(["generationConfig", "candidateCount"])]),
  stream: null,
  usage: Object.freeze([]),
  tools: Object.freeze({ list: Object.freeze(["tools"]),
                         keys: Object.freeze(["functionDeclarations"]) }),
  paid: Object.freeze([]),
  fallbacks: Object.freeze([]),
  model: null,
  tier: null,
  unpriced: Object.freeze([
    Object.freeze({ path: Object.freeze(["generationConfig", "responseModalities"]),
                    allowed: Object.freeze(["TEXT", "text"]) }),
    Object.freeze({ path: Object.freeze(["generationConfig", "speechConfig"]),
                    allowed: Object.freeze([]) }),
    Object.freeze({ path: Object.freeze(["generationConfig", "imageConfig"]),
                    allowed: Object.freeze([]) }),
  ]),
});

const ANTHROPIC: ApiRules = Object.freeze({
  outputs: Object.freeze([Object.freeze(["max_tokens"])]),
  counts: Object.freeze([]),
  stream: Object.freeze(["stream"]),
  usage: Object.freeze([]),
  tools: Object.freeze({ list: Object.freeze(["tools"]),
                         kind: Object.freeze({ field: "type",
                                               allowed: Object.freeze([null, "custom"]) }) }),
  paid: Object.freeze([Object.freeze(["mcp_servers"])]),
  fallbacks: Object.freeze([]),
  model: "model",
  tier: null,
  unpriced: Object.freeze([
    Object.freeze({ path: Object.freeze(["speed"]), allowed: Object.freeze(["standard"]) }),
    Object.freeze({ path: Object.freeze(["inference_geo"]), allowed: Object.freeze(["global"]) }),
    Object.freeze({ path: Object.freeze(["service_tier"]),
                    allowed: Object.freeze(["standard_only"]) }),
  ]),
});

/** Mistral's chat completions: as OpenAI's, but its stream reports its usage unasked. */
const MISTRAL: ApiRules = Object.freeze({
  outputs: Object.freeze([Object.freeze(["max_tokens"]), Object.freeze(["max_completion_tokens"])]),
  counts: Object.freeze([Object.freeze(["n"])]),
  stream: Object.freeze(["stream"]),
  usage: Object.freeze([]),
  tools: Object.freeze({ list: Object.freeze(["tools"]),
                         kind: Object.freeze({ field: "type", allowed: Object.freeze(["function"]) }) }),
  paid: Object.freeze([Object.freeze(["web_search_options"]), Object.freeze(["plugins"])]),
  fallbacks: Object.freeze([Object.freeze(["models"])]),
  model: "model",
  tier: null,
  unpriced: Object.freeze([
    Object.freeze({ path: Object.freeze(["service_tier"]), allowed: Object.freeze([]) }),
    Object.freeze({ path: Object.freeze(["modalities"]), allowed: Object.freeze(["text"]) }),
    Object.freeze({ path: Object.freeze(["audio"]), allowed: Object.freeze([]) }),
  ]),
});

/** The rules for each API a sandbox call may be metered on; any other is never metered. */
const RULES: Readonly<Record<string, ApiRules>> = Object.freeze({
  "anthropic-messages": ANTHROPIC,
  "openai-completions": COMPLETIONS,
  "mistral-conversations": MISTRAL,
  "openai-responses": RESPONSES,
  "azure-openai-responses": RESPONSES,
  "google-generative-ai": GOOGLE,
  "google-vertex": GOOGLE,
});

/** The API's rules, or null: a call on it is never metered. */
export function apiRules(api: string): ApiRules | null {
  return Object.hasOwn(RULES, api) ? RULES[api]! : null;
}

/** The value at `path` in `body`, or undefined. */
export function valueAt(body: unknown, path: readonly string[]): unknown {
  return path.reduce<unknown>((at, key) =>
    (typeof at === "object" && at !== null && !Array.isArray(at)
      ? (at as Record<string, unknown>)[key] : undefined), body);
}

/** Whether the request asks for more than one choice. */
export function manyChoices(rules: ApiRules, body: unknown): boolean {
  return rules.counts.some((path) => {
    const count = valueAt(body, path);
    return count !== undefined && count !== null && count !== 1;
  });
}

/** Whether the request enables a server-side tool the provider bills beyond tokens. */
export function paidTool(rules: ApiRules, body: unknown): boolean {
  if (rules.paid.some((path) => valueAt(body, path) !== undefined)) {
    return true;
  }
  const tools = valueAt(body, rules.tools.list);
  if (tools === undefined || tools === null) {
    return false;
  }
  if (!Array.isArray(tools)) {
    return true;
  }
  return tools.some((tool) => {
    if (typeof tool !== "object" || tool === null || Array.isArray(tool)) {
      return true;
    }
    const { kind, keys } = rules.tools;
    if (keys !== undefined && Object.keys(tool).some((key) => !keys.includes(key))) {
      return true;
    }
    if (kind !== undefined) {
      const value = (tool as Record<string, unknown>)[kind.field];
      return !kind.allowed.includes(value === undefined ? null : value as string);
    }
    return false;
  });
}

/** Whether the request streams its answer: its flag, or the API's streaming path. */
export function streams(rules: ApiRules, body: unknown): boolean {
  return rules.stream === null || valueAt(body, rules.stream) === true;
}

/** Whether the request lists fallback models, any of which may serve it. */
export function fallbackList(rules: ApiRules, body: unknown): boolean {
  return rules.fallbacks.some((path) => {
    const listed = valueAt(body, path);
    return Array.isArray(listed) && listed.length > 0;
  });
}

/**
 * Whether the request sets an option the provider prices beyond tokens that pi-ai does not
 * price: a value, or an element of a list, outside the option's `allowed`.
 */
export function unpricedOption(rules: ApiRules, body: unknown): boolean {
  return rules.unpriced.some(({ path, allowed }) => {
    const value = valueAt(body, path);
    if (value === undefined) {
      return false;
    }
    const values = Array.isArray(value) ? value : [value];
    return values.some((item) => typeof item !== "string" || !allowed.includes(item));
  });
}

/**
 * The multiplier the call's tier (`value`, its tier option's value; null when absent)
 * prices it at: 1 when absent, or when the API has no priced tier (its `unpriced` rules
 * hold the key); null for a value not listed (refused).
 */
export function tierMultiplier(rules: ApiRules, value: string | null): number | null {
  if (rules.tier === null || value === null) {
    return 1;
  }
  return Object.hasOwn(rules.tier.multipliers, value) ? rules.tier.multipliers[value]! : null;
}
