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
 *   it there), or null when the path names it. The gateway reads it from its one parse of
 *   the body and asks the owner's object to reserve the call for that model (`reserve`).
 * - `tier`: a top-level option that multiplies the price and that pi-ai prices from the
 *   answer (so the settle is right): the call is reserved at its value's multiplier, the
 *   highest pi-ai can apply for it, and a value not listed is refused. Absent, the provider
 *   applies the account's own default, which may be dear: the call is reserved at `absent`,
 *   the dearest value the API allows (the settle still charges what pi-ai prices).
 * - `unpriced`: options that change the price beyond tokens and that pi-ai does not price
 *   (a premium speed or geography, another tier, a non-text output): refused
 *   `egress_unpriced_option` unless absent or each value among `allowed`. One whose
 *   absence leaves the price to the account's own default, which pi-ai cannot price, has
 *   `default_when_absent`: the gateway sets that value (one pi-ai prices) when the call
 *   leaves it out, which changes only how the provider serves the call, never its price.
 * - `served`: where an answer reports the tier it was served at (an SSE event's JSON, at
 *   any of `paths`), and the values pi-ai prices: an answer served at any other settles
 *   `unknown`, keeping its whole charge; one that reports none settles at `absent` times
 *   pi-ai's price, where the account's own default may be dear.
 * - `betas`: the provider beta headers (`anthropic-beta`, `openai-beta`, any `*-beta`) a
 *   call may carry; every other is stripped, since a beta can widen a call past what it
 *   was reserved for (a longer context). None today.
 * - `free`: calls beyond a read (`GET`, `HEAD`) that a provider does not bill, each keyed
 *   by that provider (pi-ai's id) as well as the API, so a citation covers its own
 *   provider's endpoint and no other source that speaks the same API, each with the
 *   provider's page that says so and the day it was read (a token count): forwarded
 *   unmetered, as a read is, but only on an endpoint the source seals. A call the API's
 *   page does not say is free is never here, so it is refused `unmetered`. Gemini's
 *   `countTokens` is not here: its page (ai.google.dev) could not be read when this list
 *   was made, so no page says it is free.
 *
 * Every key the rules read is read under its one spelling, so a body that spells one another
 * way is refused `egress_aliased_key` (`aliased`): ProtoJSON parsers take
 * `generation_config.max_output_tokens` as `generationConfig.maxOutputTokens` ("Parsers
 * accept both the lowerCamelCase name ... and the original proto field name"), and Go's
 * `encoding/json` v1 matches a key case-blind, so an unread spelling the gateway forwards
 * could set a cap it never clamped. Go folds Unicode case as well (U+017F, `ſ`, is `s`), and
 * every provider's schema is ASCII, so a key past ASCII at a level the rules read is refused
 * too; so is a body's `model` where the path names the model. Derived, not invented: the
 * gateway sends its own parse, so an alias is the one way a body could say what the rules did
 * not read.
 */

/** A call the provider does not bill, with the page that says so (`free`). */
export interface FreeCall {
  /** The pi-ai provider whose page says the call is free. */
  readonly provider: string;
  readonly method: string;
  /** The path, from the base URL's origin, exactly. */
  readonly path: string;
  /** The provider's page that says the call is free, and the day it was read. */
  readonly cite: string;
  readonly read: string;
  /** What the page says, quoted. */
  readonly says: string;
}

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
                   readonly multipliers: Readonly<Record<string, number>>;
                   readonly absent: number } | null;
  readonly unpriced: readonly { readonly path: readonly string[];
                                readonly allowed: readonly string[];
                                readonly default_when_absent?: string }[];
  readonly served: { readonly paths: readonly (readonly string[])[];
                     readonly priced: readonly string[];
                     /** The multiplier an answer that reports no tier settles at. */
                     readonly absent?: number;
                     /**
                      * The event types (each event's JSON `type`) whose tier is the served
                      * one, as pi-ai reads it; every event when absent.
                      */
                     readonly events?: readonly string[] } | null;
  /** The beta headers a call may carry, each one its reservation accounts for. */
  readonly betas: readonly string[];
  /** Calls the provider does not bill, each on its cited page: forwarded unmetered. */
  readonly free: readonly FreeCall[];
}

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
  // The tier an answer says it served at: `default` (OpenAI's) or `on_demand` (Groq's
  // name for the same standard tier) are what pi-ai prices; any other is unknown.
  served: Object.freeze({ paths: Object.freeze([Object.freeze(["service_tier"])]),
                          priced: Object.freeze(["default", "on_demand"]) }),
  betas: Object.freeze([]),
  free: Object.freeze([]),
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
                                                     priority: 2.5 }),
                        absent: 2.5 }),
  unpriced: Object.freeze([]),
  // The tier the answer was served at, which pi-ai prices; an answer naming none was
  // served at the account's own default, which may be priority: settled at the dearest.
  // Read from the terminal event alone, as pi-ai does: `response.created` echoes what was
  // asked (`auto`), not what served.
  served: Object.freeze({ paths: Object.freeze([Object.freeze(["response", "service_tier"])]),
                          priced: Object.freeze(["default", "flex", "priority"]),
                          absent: 2.5,
                          events: Object.freeze(["response.completed",
                                                 "response.incomplete"]) }),
  betas: Object.freeze([]),
  free: Object.freeze([]),
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
  served: null,
  betas: Object.freeze([]),
  free: Object.freeze([]),
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
                    allowed: Object.freeze(["standard_only"]),
                    default_when_absent: "standard_only" }),
  ]),
  served: Object.freeze({ paths: Object.freeze([Object.freeze(["usage", "service_tier"]),
                                                Object.freeze(["message", "usage",
                                                               "service_tier"])]),
                          priced: Object.freeze(["standard"]) }),
  betas: Object.freeze([]),
  // Claude Code counts tokens before it sends; Anthropic does not bill the count.
  free: Object.freeze([Object.freeze({
    provider: "anthropic", method: "POST", path: "/v1/messages/count_tokens",
    cite: "https://platform.claude.com/docs/en/build-with-claude/token-counting",
    read: "2026-11-02",
    says: "Token counting is free to use but subject to requests per minute rate limits",
  })]),
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
  served: null,
  betas: Object.freeze([]),
  free: Object.freeze([]),
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

/** Every API a sandbox call may be metered on: the ids the rules are keyed by. */
export function ruledApis(): readonly string[] {
  return Object.keys(RULES);
}

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

/**
 * Whether a call is one its provider does not bill (`free`): `provider`, `method` and the
 * bare `path` (from the origin, its query dropped) exactly a listed call's. A read (`GET`,
 * `HEAD`) is free by its method, as the egress door has it; this is every other free call.
 */
export function freeCall(rules: ApiRules | null, provider: string, method: string,
                         path: string): boolean {
  const bare = path.split("?")[0]!;
  return rules !== null && rules.free.some((call) => call.provider === provider
    && call.method === method && call.path === bare);
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

/** A key with any character past ASCII. */
const NOT_ASCII = /[^\x00-\x7f]/;

/** A key as a spelling-blind parser matches it: ASCII case and snake/camel folded together. */
function folded(key: string): string {
  return key.replace(/_/g, "").toLowerCase();
}

/** Every body path the rules read, the call's own (`model`, `tier`) among them. */
function readPaths(rules: ApiRules): (readonly string[])[] {
  return [...rules.outputs, ...rules.counts, ...(rules.stream === null ? [] : [rules.stream]),
          ...rules.usage.map(([path]) => path), rules.tools.list, ...rules.paid,
          ...rules.fallbacks, ...(rules.model === null ? [] : [[rules.model]]),
          ...(rules.tier === null ? [] : [[rules.tier.key]]),
          ...rules.unpriced.map(({ path }) => path)];
}

/**
 * Whether the body spells a key the rules read another way (see the header): at each level a
 * read path passes through, and in each tool entry, a key that is not a read key but folds
 * to one (`folded`), or any key that is not ASCII (every provider's schema is ASCII, and a
 * parser that folds Unicode case reads `max_tokenſ`, U+017F, as `max_tokens`); and, for an
 * API whose path names the model, a body key that names one, in any spelling (a transcoder
 * that binds the path's field may read the body's).
 */
export function aliased(rules: ApiRules, body: unknown): boolean {
  if (rules.model === null && typeof body === "object" && body !== null && !Array.isArray(body)
      && Object.keys(body).some((key) => NOT_ASCII.test(key) || folded(key) === "model")) {
    return true;
  }
  const levels = new Map<string, { at: readonly string[]; names: Set<string> }>();
  for (const path of readPaths(rules)) {
    for (let depth = 0; depth < path.length; depth++) {
      const at = path.slice(0, depth);
      const level = levels.get(at.join("\0")) ?? { at, names: new Set<string>() };
      level.names.add(path[depth]!);
      levels.set(at.join("\0"), level);
    }
  }
  const spelledOtherwise = (value: unknown, names: ReadonlySet<string>) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return false;
    }
    const read = new Set([...names].map(folded));
    return Object.keys(value).some((key) => NOT_ASCII.test(key)
      || (!names.has(key) && read.has(folded(key))));
  };
  if ([...levels.values()].some(({ at, names }) => spelledOtherwise(valueAt(body, at), names))) {
    return true;
  }
  const kind = rules.tools.kind;
  const tools = valueAt(body, rules.tools.list);
  return kind !== undefined && Array.isArray(tools)
    && tools.some((tool) => spelledOtherwise(tool, new Set([kind.field])));
}

/**
 * The multiplier the call's tier (`value`, its tier option's value; null when absent)
 * is reserved at: the tier's `absent` (the dearest the API allows) when absent, 1 when the
 * API has no priced tier (its `unpriced` rules hold the key), null for a value not listed
 * (refused).
 */
export function tierMultiplier(rules: ApiRules, value: string | null): number | null {
  if (rules.tier === null) {
    return 1;
  }
  if (value === null) {
    return rules.tier.absent;
  }
  return Object.hasOwn(rules.tier.multipliers, value) ? rules.tier.multipliers[value]! : null;
}

/**
 * What the answer's stream reports of the tier it was served at, read from each SSE
 * event's data (its `data:` lines joined, as the SSE specification decodes them: lines
 * ended by CR, LF or CRLF, one leading space after the colon dropped, an event dispatched
 * at a blank line) at the rules' `served` paths: `priced`, whether every tier reported is
 * one pi-ai prices, and `seen`, whether any was. An API with no such rule: priced, seen.
 */
export async function servedPriced(rules: ApiRules, body: ReadableStream<Uint8Array>):
    Promise<{ readonly priced: boolean; readonly seen: boolean }> {
  const served = rules.served;
  if (served === null) {
    await body.cancel();
    return { priced: true, seen: true };
  }
  const decoder = new TextDecoder();
  const reader = body.getReader();
  let pending = "";
  let data: string[] = [];
  let priced = true;
  let seen = false;
  const dispatch = () => {
    if (data.length === 0) {
      return;
    }
    const text = data.join("\n");
    data = [];
    let event: unknown;
    try {
      event = JSON.parse(text);
    } catch {
      return;  // not JSON (`[DONE]`): no tier in it
    }
    if (served.events !== undefined && !served.events.includes(valueAt(event, ["type"]) as string)) {
      return;  // not an event the served tier is read from
    }
    for (const path of served.paths) {
      const tier = valueAt(event, path);
      if (tier !== undefined) {
        seen = true;
        if (typeof tier !== "string" || !served.priced.includes(tier)) {
          priced = false;
        }
      }
    }
  };
  const line = (text: string) => {
    if (text === "") {
      dispatch();
      return;
    }
    const colon = text.indexOf(":");
    const field = colon === -1 ? text : text.slice(0, colon);
    if (field === "data") {
      const value = colon === -1 ? "" : text.slice(colon + 1);
      data.push(value.startsWith(" ") ? value.slice(1) : value);
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    pending += done ? decoder.decode() : decoder.decode(value, { stream: true });
    // A line ends at CRLF, LF or CR; a CR at the chunk's end may be a CRLF's first half.
    const lines = pending.split(/\r\n|\n|\r(?!$)/);
    pending = done ? "" : lines.pop()!;
    if (done && lines.at(-1)?.endsWith("\r")) {
      lines[lines.length - 1] = lines.at(-1)!.slice(0, -1);
    }
    for (const text of lines) {
      line(text);
    }
    if (done) {
      dispatch();  // an event the stream ended without its blank line: read, to fail closed
      return { priced, seen };
    }
  }
}
