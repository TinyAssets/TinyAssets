/**
 * Metering a sandbox's raw model call with pi-ai's own code (tasks 4 G4; Direction 2: no
 * provider code here). Two uses of pi-ai's adapter for the sealed source's API, each with
 * a fetch of ours in place of the network:
 *
 * The credential's own secret is handed to the adapter, as the loop's calls hand it, since
 * pi-ai shapes a call by it (OpenAI's Responses API omits the output cap for a Sign in with
 * ChatGPT token); it never leaves: each fetch here is ours, and reaches no network.
 *
 * - **Learning the call** (`learnMeter`): the adapter is asked once, with a placeholder
 *   model id and a placeholder output cap, and its fetch records the request it built and
 *   answers `400` (no retry). So pi-ai itself says, for this API at this base URL, which
 *   method and path a model call is, where the model id goes (a path segment, or a body
 *   field) and which body field caps the output tokens. A request on any other path is not
 *   a model call this module can meter (`unmetered`).
 * - **Reading the answer** (`meteredUsage`): the provider's answer, teed as it streams to
 *   the sandbox, is handed to the same adapter through its fetch, so pi-ai parses the
 *   stream exactly as it parses the loop's. Its final message's usage, priced by the
 *   catalog's records (`calculateCost`, tiers included) of both the model the request named
 *   and the model the answer says served it, the dearer, is the call's real cost. Anything
 *   short of that is `null`, which the owner's object settles `unknown`, keeping the whole
 *   reservation: a stream pi-ai cannot read to a `done` (not its stream format, a cut, an
 *   abort, a non-streaming JSON answer, which pi-ai's adapters do not parse); a `done` with
 *   no usage (zero tokens: a stream sent without its usage report); a served model the
 *   catalog does not price; and a router the catalog prices at nothing (`openrouter/auto`),
 *   unless the answer names the dearer model it served.
 *
 * Each API's rules (`api-rules.ts`) are held against pi-ai's learned call: the output cap
 * pi-ai sets must be one the rules list, and the usage-report fields the rules set must be
 * in pi-ai's own streaming call, at the same value; otherwise the API is not metered.
 */
import type { Api, AssistantMessageEvent, Context, FetchFunction, Model } from "@earendil-works/pi-ai";
import { calculateCost, createModels } from "@earendil-works/pi-ai/models";

import { apiRules, valueAt } from "./api-rules";

import { type Catalog, NO_AMBIENT, providerFor } from "./catalog";
import type { Meter, Upstream } from "./credential";

export type { Meter };
import { within } from "./egress";
import { GLOBAL_FETCH_APIS } from "./messages";

/** The model id and output cap the learning run asks with: neither is any real value. */
const MODEL_MARK = "ta-meter-model-0e7f";
const TOKENS_MARK = 4_321;
/** How many of a source's models the verify-time learning asks before it gives up. */
const LEARN_TRIES = 8;
/** The context each run asks with: one short user message, made afresh for each run. */
function context(): Context {
  return { messages: [{ role: "user", content: "x", timestamp: 0 }] } as unknown as Context;
}


/** The usage pi-ai read from the answer: tokens and dollars. */
export interface Metered {
  readonly tokens: number;
  readonly cost: number;
}

/** Every key path in `value` whose leaf is exactly `mark`. */
function pathsOf(value: unknown, mark: unknown, at: readonly string[] = []): string[][] {
  if (value === mark) {
    return [[...at]];
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return [];
  }
  return Object.entries(value).flatMap(([key, inner]) => pathsOf(inner, mark, [...at, key]));
}

/** The models pi-ai's provider for the source serves on its sealed API. */
function apiModels(catalog: Catalog, upstream: Upstream): Model<Api>[] {
  return providerFor(catalog, upstream).getModels()
    .filter((model): model is Model<Api> => model.api === upstream.api);
}

/** One run of pi-ai's adapter for `model`, its every request answered by `fetch`. */
async function run(catalog: Catalog, upstream: Upstream, apiKey: string, model: Model<Api>,
                   respond: FetchFunction, maxTokens: number): Promise<AssistantMessageEvent[]> {
  const models = createModels({ authContext: NO_AMBIENT });
  models.setProvider(providerFor(catalog, upstream));
  const takesFetch = !GLOBAL_FETCH_APIS.includes(upstream.api);
  const events: AssistantMessageEvent[] = [];
  await within(respond, async () => {
    const stream = models.streamSimple(model, context(), {
      apiKey, maxTokens, maxRetries: 0, ...(takesFetch ? { fetch: respond } : {}),
    } as never);
    for await (const event of stream) {
      events.push(event);
    }
  });
  return events;
}

/**
 * What pi-ai's adapter sends as a model call for the sealed source: learned by one run
 * that reaches no network. Null when the API's call cannot be read so (no output cap in its
 * body, or no model id where one can be found), which is then never metered.
 */
export async function learnMeter(catalog: Catalog, upstream: Upstream, apiKey: string,
                                 model?: Model<Api>): Promise<Meter | null> {
  if (model !== undefined) {
    return learned(catalog, upstream, apiKey, model);
  }
  // A model whose compatibility record lets pi-ai cap its output; the first few are asked.
  for (const candidate of apiModels(catalog, upstream).slice(0, LEARN_TRIES)) {
    const meter = await learned(catalog, upstream, apiKey, candidate);
    if (meter !== null) {
      return meter;
    }
  }
  return null;
}

async function learned(catalog: Catalog, upstream: Upstream, apiKey: string,
                       model: Model<Api>): Promise<Meter | null> {
  const probe = { ...model, id: MODEL_MARK, baseUrl: upstream.base_url, maxTokens: TOKENS_MARK };
  const seen: { first: { method: string; url: string; body: string } | null } = { first: null };
  const capture: FetchFunction = async (input, init) => {
    const request = new Request(input, init);
    seen.first ??= { method: request.method, url: request.url, body: await request.text() };
    return new Response("{}", { status: 400, headers: { "content-type": "application/json" } });
  };
  try {
    await run(catalog, upstream, apiKey, probe, capture, TOKENS_MARK);
  } catch {
    // The adapter reports the refusal as its stream's error; a throw is the same.
  }
  const sent = seen.first;
  if (sent === null) {
    return null;
  }
  let body: unknown;
  try {
    body = JSON.parse(sent.body);
  } catch {
    return null;
  }
  const url = new URL(sent.url);
  const segments = url.pathname.split(MODEL_MARK);
  const output = pathsOf(body, TOKENS_MARK);
  const named = pathsOf(body, MODEL_MARK);
  if (output.length !== 1 || segments.length > 2 || (segments.length === 1 && named.length !== 1)) {
    return null;
  }
  // The API's rules must describe pi-ai's own call: its output cap among the aliases, and
  // every usage-report field the gateway sets present in it, at the same value.
  const rules = apiRules(upstream.api);
  if (rules === null
      || !rules.outputs.some((path) => path.join("\0") === output[0]!.join("\0"))
      || rules.usage.some(([path, value]) => valueAt(body, path) !== value)) {
    return null;
  }
  return Object.freeze({
    method: sent.method,
    path: segments.join("{model}"),
    model_field: segments.length === 2 ? null : Object.freeze(named[0]!),
    output_field: Object.freeze(output[0]!),
  });
}

/** The model id `path` names under `meter`, or null when it is not the meter's call. */
export function meteredModel(meter: Meter, method: string, path: string,
                             body: unknown): string | null {
  if (method !== meter.method) {
    return null;
  }
  const [head, tail] = meter.path.split("{model}");
  if (tail === undefined) {
    if (path !== meter.path || meter.model_field === null) {
      return null;
    }
    const named = meter.model_field.reduce<unknown>((at, key) =>
      (typeof at === "object" && at !== null && !Array.isArray(at)
        ? (at as Record<string, unknown>)[key] : undefined), body);
    return typeof named === "string" && named.length > 0 ? named : null;
  }
  if (!path.startsWith(head!) || !path.endsWith(tail) || path.length <= head!.length + tail.length) {
    return null;
  }
  const named = decodeURIComponent(path.slice(head!.length, path.length - tail.length));
  return /^[^/:?#]+$/.test(named) ? named : null;
}

/** The object at `path` in `body`, made where it is missing or not an object. */
function objectAt(body: Record<string, unknown>, path: readonly string[]): Record<string, unknown> {
  let at = body;
  for (const key of path) {
    const inner = at[key];
    if (typeof inner !== "object" || inner === null || Array.isArray(inner)) {
      at[key] = {};
    }
    at = at[key] as Record<string, unknown>;
  }
  return at;
}

/**
 * `body` with every output-cap alias the API reads at most `cap` (the one pi-ai sets set to
 * `cap` when the request names none), and, when it streams, the API's usage-report fields
 * set, so its answer reports what it used.
 */
export function clamped(meter: Meter, body: Record<string, unknown>, cap: number,
                        api?: string): Record<string, unknown> {
  const copy = structuredClone(body);
  const rules = api === undefined ? null : apiRules(api);
  const aliases = [meter.output_field,
                   ...(rules?.outputs ?? []).filter((path) =>
                     path.join("\0") !== meter.output_field.join("\0"))];
  for (const [index, path] of aliases.entries()) {
    const owner = index === 0 ? objectAt(copy, path.slice(0, -1)) : valueAt(copy, path.slice(0, -1));
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
  return copy;
}

/** The catalog's record of the model `id`, on the sealed API, or null. */
export function meteredRecord(catalog: Catalog, upstream: Upstream, id: string):
    Model<Api> | null {
  const found = apiModels(catalog, upstream).find((model) => model.id === id);
  return found === undefined ? null : { ...found, baseUrl: upstream.base_url };
}

/** Dollars `usage` costs on `model`, by pi-ai's own `calculateCost`. */
function costOn(model: Model<Api>, usage: AssistantUsage): number {
  return calculateCost(model, structuredClone(usage)).total;
}

type AssistantUsage = Extract<AssistantMessageEvent, { type: "done" }>["message"]["usage"];

/**
 * The usage pi-ai reads from a 2xx answer's body (`status` and `headers` as the provider
 * sent them), priced at the dearer of `model` (the one the request named) and the model
 * the answer says served it: tokens and dollars, or null for anything pi-ai cannot read
 * to its end, a `done` with no usage, a served model the catalog does not price, or a
 * model priced at nothing that names no other model it served.
 */
export async function meteredUsage(catalog: Catalog, upstream: Upstream, apiKey: string,
                                   model: Model<Api>,
                                   status: number, headers: Headers,
                                   body: ReadableStream<Uint8Array>): Promise<Metered | null> {
  let handed = false;
  const replay: FetchFunction = async () => {
    if (handed) {
      throw new Error("the answer is replayed once");
    }
    handed = true;
    return new Response(body, { status, headers });
  };
  let events: AssistantMessageEvent[];
  try {
    events = await run(catalog, upstream, apiKey, model, replay, model.maxTokens);
  } catch {
    return null;
  }
  const last = events.at(-1);
  if (last === undefined || last.type !== "done" || !handed) {
    return null;
  }
  const usage = last.message.usage;
  if (!Number.isSafeInteger(usage.totalTokens) || usage.totalTokens <= 0) {
    return null;  // no usage reported: what it cost is unknown
  }
  const served = last.message.responseModel;
  const named = costOn(model, usage);
  let cost = named;
  if (typeof served === "string" && served !== "" && served !== model.id) {
    const record = meteredRecord(catalog, upstream, served);
    if (record === null) {
      return null;
    }
    cost = Math.max(named, costOn(record, usage));
  } else if (served !== model.id && model.cost.input === 0 && model.cost.output === 0) {
    return null;  // a router priced at nothing, with no word of what it served
  }
  if (!Number.isFinite(cost) || cost < 0) {
    return null;
  }
  return Object.freeze({ tokens: usage.totalTokens, cost });
}
