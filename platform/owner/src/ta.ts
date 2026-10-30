/**
 * `ta`: the model's one way to every ability beyond read, write, edit and bash (ADR-032 §4;
 * agent research §7; tasks 5, sub-slice A3). The model is offered exactly those five tools
 * (`MODEL_TOOLS`, fixed on every conversation the object creates, `agent.ts`); `ta` reaches
 * the rest:
 * - `{op: "catalog", query?, limit?, offset?}` lists what this conversation may call: the
 *   platform capabilities its authority holds, then the tools registered as callable by
 *   tools (`callers` naming "tools": MCP tools, package tools and, later, code mode), the
 *   deferred ones among them, each with its JSON Schema. A query ranks them as pi's own
 *   `tool_search` does (BM25 over the name, the name with `_` as spaces, the description
 *   and the parameters' names and descriptions; `rank` and `searchText` are its copy, held
 *   to it by the ta guard). Pages of `limit` (at most `CATALOG_MAX`) from `offset`.
 * - `{op: "call", name, arguments}` runs a platform capability in the object, or reaches a
 *   tool through pi's `executeTool`: its own `pi.tool` task, nested in this call, with
 *   validation, the `ToolTask` hooks (`ta.authority` sees `call.parent`, so a client
 *   reaches through `ta` nothing it could not reach directly), output limits and the tool's
 *   replay policy; its `structuredOutput`, `diagnostics` and `isError` come back as `ta`'s
 *   result. Any other name is refused loudly, the file tools' and the `ta:` names included.
 *
 * The input is one object, `op` an enum and each op's fields optional, its shape checked
 * here (`request`): a root union would reach providers as no schema at all (pi-ai's
 * Anthropic adapter keeps only a root's `properties`, and strict sampling refuses object
 * unions). An unknown field is refused, as the Python's was: the body is never a grant.
 *
 * Authority: `ta.authority`'s `beforeTool` is the early gate, by `TA_CAPABILITIES`; a later
 * hook may rewrite a call's arguments after it approved them, so `execute` re-derives the
 * need from the arguments it actually received and re-checks the conversation's authority
 * (the stamp must still stand) before anything else, a refusal kept as a receipt. Every
 * capability is a read or an effect, and one not classified a read is an effect (the
 * Python's rule): a read writes nothing (`write` refuses it); an effect re-checks the
 * authority at its effect, in its own unit of the write gate, as the environment's
 * changes do, or, for a model call, in its reservation and dispatch.
 *
 * The platform capabilities (v1): `policy:show` (read, the owner's alone), `policy:propose`
 * (effect: a proposal held for the owner's card, as `policyPropose` holds it),
 * `wake:list` (read: the conversation's authority's scheduled work), `files:usage`
 * (read), and `model` (effect: a nested model call through the object's model source, the
 * `gateway` provider in production, reserved and settled like any turn's call and charged
 * to the turn at the origin of this call's causal chain, which pi's telemetry context
 * carries). `agent:run` is A4's.
 *
 * `ta` is `callers: ["model"]` (no tool reaches it, so a nested call never nests `ta`) and
 * `replay: "unsafe"`: an eviction mid-call comes back interrupted, never run twice. Its
 * results never carry a `control`, so nothing it returns widens the model's tools.
 */
import type { Context } from "@earendil-works/chord/context";
import {
  type AssistantMessage, type Message, StringEnum, type TSchema, Type, type Usage as PiUsage,
} from "@earendil-works/pi-ai";
import { validateToolArguments } from "@earendil-works/pi-ai/utils/validation";
import {
  defineTool, type JsonObject, ProviderDoc, type ToolExecutionApi, type ToolExecutionResult,
  type ToolRegistration,
} from "@earendil-works/pi-durable";

import {
  type Authority, type AuthorityState, type Need, stampOf, TA_CAPABILITIES,
  TA_OP, TA_TOOL,
} from "./authority";
import { Invalid, Refused } from "./errors";
import type { Usage as FileUsage } from "./files";
import type { Write } from "./gate";
import { AUTO_MODEL, GATEWAY_PROVIDER } from "./gateway-provider";

/** The platform capabilities' names, which no package tool may take. */
export const PLATFORM_NAMES: readonly string[] = Object.freeze(["policy:show", "policy:propose",
                                                                "wake:list", "files:usage",
                                                                "model"]);

/** The capabilities classified as reads; any other is an effect. */
export const READS: readonly string[] = Object.freeze(["ta:catalog", "policy:show",
                                                       "wake:list", "files:usage"]);

/** A capability's class: a read, or an effect unless explicitly classified a read. */
export function kindOf(name: string): "read" | "effect" {
  return READS.includes(name) ? "read" : "effect";
}

/** A catalog page's default and largest size. */
export const CATALOG_PAGE = 20;
export const CATALOG_MAX = 100;
/** Rows `wake:list` shows by default, and at most. */
export const WORK_SHOWN = 50;
export const WORK_MAX = 100;
/** Messages a nested model call takes at most. */
export const MODEL_MESSAGES = 64;

export const INVALID_REQUEST = "invalid ta request";
export const UNKNOWN_CAPABILITY = "unknown capability";
export const RESERVED_NAME = "the ta: names are the ops' own";
export const READ_WRITES = "a read capability writes nothing";

/** What the object's platform capabilities run on: its own modules, read or in a unit. */
export interface Platform {
  /** The owner's policy, as the owner reads it (`Policy.projection`). */
  policy(): unknown;
  /** A unit's body: hold `change` for the owner's card, proposed by `proposer`. */
  propose(change: unknown, requestId: unknown, proposer: string):
    { request_digest: string; generation: number };
  /**
   * The newest `limit` rows of the scheduled work made under `client` (null: the owner's),
   * as `Scheduled.of` shows them.
   */
  work(client: string | null, limit: number): unknown[];
  /** The owner's file usage and limits. */
  fileUsage(): FileUsage;
}

/** What `ta` is built from. */
export interface TaParts {
  readonly authority: Authority;
  /** The authority the call's conversation runs under, as its `ta.authority` document holds it. */
  readonly state: (api: ToolExecutionApi, context: Context) =>
    Promise<Readonly<AuthorityState> | undefined>;
  readonly platform: Platform;
  /** The object's write gate. */
  readonly write: Write;
}

/** What one platform capability's run is handed. */
interface Scope {
  readonly state: Readonly<AuthorityState> | undefined;
  readonly api: ToolExecutionApi;
  readonly context: Context;
  /** An effect's unit, its authority re-checked in it; a read's refuses (`READ_WRITES`). */
  readonly write: Write;
}

interface PlatformCapability {
  readonly name: string;
  readonly description: string;
  readonly parameters: TSchema;
  run(args: Record<string, unknown>, scope: Scope, parts: TaParts):
    Promise<{ value: unknown; usage?: PiUsage }>;
}

/** The parameters of a capability that takes none. */
function none(): TSchema {
  return Type.Object({}, { additionalProperties: false });
}

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The text of a model's answer. */
function textOf(message: AssistantMessage): string {
  return message.content.map((part) => (part.type === "text" ? part.text : "")).join("");
}

const ZERO: PiUsage = Object.freeze({
  input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: Object.freeze({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 }),
});

/** The v1 platform capabilities, in the catalog's order: made per `ta`, never at load. */
function platformCapabilities(): readonly PlatformCapability[] {
  return [
    {
      name: "policy:show",
      description: "Read the owner's current policy document, its generation and digest.",
      parameters: none(),
      run: async (_args, _scope, parts) => ({ value: parts.platform.policy() }),
    },
    {
      name: "policy:propose",
      description: "Propose a policy change for the owner to review and apply; never applied " +
        "by the agent. Returns the proposal's digest, which the owner's card names.",
      parameters: Type.Object({
        change: Type.Record(Type.String(), Type.Unknown(),
                            { description: "The proposed change, as the owner's card shows it." }),
        request_id: Type.Optional(Type.String({ description: "A card id to hold it under." })),
      }, { additionalProperties: false }),
      run: async (args, scope, parts) => {
        const proposer = stampOf(scope.state)?.client ?? "owner";
        const value = await scope.write(() => parts.platform.propose(
          args["change"], args["request_id"] ?? null, proposer));
        return { value };
      },
    },
    {
      name: "wake:list",
      description: "List my scheduled follow-ups, newest first, and how each stands.",
      parameters: Type.Object({
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: WORK_MAX })),
      }, { additionalProperties: false }),
      run: async (args, scope, parts) => ({
        value: { work: parts.platform.work(stampOf(scope.state)?.client ?? null,
                                           (args["limit"] as number | undefined) ?? WORK_SHOWN) },
      }),
    },
    {
      name: "files:usage",
      description: "Read the owner's file usage (entries, history, stored bytes, uploads) and " +
        "its limits.",
      parameters: none(),
      run: async (_args, _scope, parts) => ({ value: parts.platform.fileUsage() }),
    },
    {
      name: "model",
      description: "Ask a connected model, within this turn's budget. The default model is the " +
        "owner's strongest connected one (auto).",
      parameters: Type.Object({
        messages: Type.Array(Type.Object({
          role: StringEnum(["user", "assistant"] as const),
          text: Type.String(),
        }, { additionalProperties: false }), { minItems: 1, maxItems: MODEL_MESSAGES }),
        system: Type.Optional(Type.String()),
        model: Type.Optional(Type.String({
          description: "A connected model's id; auto when omitted." })),
        max_tokens: Type.Optional(Type.Integer({ minimum: 1 })),
      }, { additionalProperties: false }),
      run: async (args, scope) => modelCall(args, scope),
    },
  ];
}

/**
 * A nested model call: the object's model source, as pi's own generation calls it, on the
 * conversation's provider session (the provider finds the conversation's authority by it)
 * and this call's telemetry context (the provider finds the turn by it), so it is reserved,
 * dispatched and settled as any of the turn's calls, and refused as they are.
 */
async function modelCall(args: Record<string, unknown>, scope: Scope):
    Promise<{ value: unknown; usage: PiUsage }> {
  const { api, context } = scope;
  const id = (args["model"] as string | undefined) ?? AUTO_MODEL;
  const model = api.models.getModel(GATEWAY_PROVIDER, id);
  if (model === undefined) {
    throw new Refused(`no connected model ${id}`);
  }
  const session = (await api.snapshot(ProviderDoc, api.conversationId, context))?.sessionId;
  if (session === undefined) {
    throw new Error("this conversation has no provider session");
  }
  const now = Date.now();
  const messages = (args["messages"] as { role: string; text: string }[]).map(
    (message): Message => message.role === "user"
      ? { role: "user", content: message.text, timestamp: now }
      : { role: "assistant", content: [{ type: "text", text: message.text }], api: model.api,
          provider: model.provider, model: model.id, usage: ZERO, stopReason: "stop",
          timestamp: now });
  const system = args["system"] as string | undefined;
  const maxTokens = args["max_tokens"] as number | undefined;
  const answer = await api.models.completeSimple(model, {
    ...(system === undefined ? {} : { systemPrompt: system }), messages,
  }, {
    sessionId: session, telemetry: context.telemetry,
    ...(context.abortSignal === undefined ? {} : { signal: context.abortSignal }),
    ...(maxTokens === undefined ? {} : { maxTokens }),
  });
  if (answer.stopReason === "error" || answer.stopReason === "aborted") {
    throw new Error(`the model call failed: ${answer.errorMessage ?? answer.stopReason}`);
  }
  return {
    value: { text: textOf(answer), model: answer.model, stop_reason: answer.stopReason,
             usage: { input: answer.usage.input, output: answer.usage.output,
                      total_tokens: answer.usage.totalTokens } },
    usage: answer.usage,
  };
}

/** A request `ta` takes: a catalog page, or a call. */
export type TaRequest =
  | { readonly op: "catalog"; readonly query?: string; readonly limit: number;
      readonly offset: number }
  | { readonly op: "call"; readonly name: string; readonly arguments: JsonObject };

const CATALOG_KEYS: readonly string[] = Object.freeze(["op", "query", "limit", "offset"]);

/** `args` as exactly one op's request, else `INVALID_REQUEST`. */
export function request(args: unknown): TaRequest {
  if (!plain(args)) {
    throw new Invalid(INVALID_REQUEST);
  }
  const keys = Object.keys(args);
  if (args["op"] === "catalog" && keys.every((key) => CATALOG_KEYS.includes(key))) {
    const { query, limit, offset } = args;
    const count = (value: unknown, least: number, most: number) =>
      typeof value === "number" && Number.isSafeInteger(value) && value >= least
        && value <= most;
    if ((query !== undefined && typeof query !== "string")
        || (limit !== undefined && !count(limit, 1, CATALOG_MAX))
        || (offset !== undefined && !count(offset, 0, Number.MAX_SAFE_INTEGER))) {
      throw new Invalid(INVALID_REQUEST);
    }
    return { op: "catalog", ...(query === undefined ? {} : { query }),
             limit: (limit as number | undefined) ?? CATALOG_PAGE,
             offset: (offset as number | undefined) ?? 0 };
  }
  if (args["op"] === "call" && keys.length === 3 && typeof args["name"] === "string"
      && plain(args["arguments"])) {
    return { op: "call", name: args["name"], arguments: args["arguments"] as JsonObject };
  }
  throw new Invalid(INVALID_REQUEST);
}

// -- The ranking: a copy of pi-durable 1.4.0's tool search (`dist/search.js`, the
// `searchText` of `dist/tool-search/tool-search.js`), which pi does not export. The ta
// guard checks it against pi's own on every run.

const STOP_WORDS: readonly string[] = Object.freeze([
  "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of",
  "on", "or", "that", "the", "this", "to", "with",
]);

function stem(term: string): string {
  if (term.length > 4 && term.endsWith("ies")) {
    return `${term.slice(0, -3)}y`;
  }
  if (term.length > 4 && /(ches|shes|sses|xes|zes)$/.test(term)) {
    return term.slice(0, -2);
  }
  if (term.length > 3 && term.endsWith("s") && !term.endsWith("ss")) {
    return term.slice(0, -1);
  }
  return term;
}

/** Lowercase terms, split at camelCase boundaries and non-alphanumerics, without stop words. */
export function tokenize(text: string): string[] {
  return text
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((term) => term.length > 0 && !STOP_WORDS.includes(term))
    .map(stem);
}

/** Okapi BM25 (k1 1.2, b 0.75): the names of the best matches, best first; ties keep order. */
export function rank(query: string, documents: readonly { name: string; text: string }[],
                     limit: number): string[] {
  const k1 = 1.2;
  const b = 0.75;
  const queryTerms = [...new Set(tokenize(query))];
  if (queryTerms.length === 0 || documents.length === 0 || limit <= 0) {
    return [];
  }
  const termCounts = documents.map((document) => {
    const counts = new Map<string, number>();
    for (const term of tokenize(document.text)) {
      counts.set(term, (counts.get(term) ?? 0) + 1);
    }
    return counts;
  });
  const lengths = termCounts.map((counts) => [...counts.values()]
    .reduce((sum, count) => sum + count, 0));
  const averageLength = lengths.reduce((sum, length) => sum + length, 0) / documents.length || 1;
  const idf = new Map(queryTerms.map((term) => {
    const frequency = termCounts.filter((counts) => counts.has(term)).length;
    return [term, Math.log(1 + (documents.length - frequency + 0.5) / (frequency + 0.5))];
  }));
  const matches: { name: string; score: number }[] = [];
  documents.forEach((document, index) => {
    let score = 0;
    for (const term of queryTerms) {
      const count = termCounts[index]!.get(term);
      if (!count) {
        continue;
      }
      const norm = k1 * (1 - b + (b * lengths[index]!) / averageLength);
      score += (idf.get(term) ?? 0) * ((count * (k1 + 1)) / (count + norm));
    }
    if (score > 0) {
      matches.push({ name: document.name, score });
    }
  });
  return matches.sort((left, right) => right.score - left.score).slice(0, limit)
    .map((match) => match.name);
}

/** Schema descriptions and property names, recursively. */
function schemaText(schema: unknown, parts: string[]): void {
  if (!plain(schema)) {
    return;
  }
  if (typeof schema["description"] === "string") {
    parts.push(schema["description"]);
  }
  const properties = schema["properties"];
  if (plain(properties)) {
    for (const [name, property] of Object.entries(properties)) {
      parts.push(name);
      schemaText(property, parts);
    }
  }
  schemaText(schema["items"], parts);
  for (const key of ["anyOf", "oneOf", "allOf"]) {
    const variants = schema[key];
    if (Array.isArray(variants)) {
      for (const variant of variants) {
        schemaText(variant, parts);
      }
    }
  }
}

/** A tool's search text: its name, its name with `_` as spaces, its description, its schema's. */
export function searchText(tool: { name: string; description: string; parameters: unknown }):
    string {
  const parts = [tool.name, tool.name.replaceAll("_", " "), tool.description];
  schemaText(tool.parameters, parts);
  return parts.filter((part) => part.trim() !== "").join(" ");
}

// -- The catalog.

/** One entry the catalog lists. */
export interface Listed {
  readonly name: string;
  readonly description: string;
  /** A platform capability's class; a tool is an effect. */
  readonly effect: boolean;
  /** A deferred tool's mark. */
  readonly deferred?: true;
  readonly parameters: unknown;
}

/** Whether a tool is registered callable by tools: what `ta` reaches through `executeTool`. */
export function reachable(tool: { callers?: readonly string[] }): boolean {
  return tool.callers?.includes("tools") === true;
}

/**
 * The tools `ta` reaches for a conversation's agent, by name: its callable ones registered
 * callable by tools, the deferred ones marked. pi's deferred tools (`agent.deferred`: the
 * model-callable ones not offered, which with `modelTools` fixed is every deferred one) a
 * tool may call are among `agent.callable` already, so the deferred list only marks them.
 */
async function tools(api: ToolExecutionApi, context: Context):
    Promise<Map<string, { tool: ToolRegistration; deferred: boolean }>> {
  const agent = await api.agent(context);
  const deferred = new Set(agent.deferred.map((tool) => tool.name));
  const found = new Map<string, { tool: ToolRegistration; deferred: boolean }>();
  for (const tool of agent.callable) {
    if (reachable(tool) && (PLATFORM_NAMES.includes(tool.name) || tool.name.startsWith(TA_OP))) {
      // A package may not take one (`packageRefusal`); one of the object's own that does is
      // a fault, never a shadow.
      throw new Error(`the tool ${tool.name} takes a platform capability's name`);
    }
    if (reachable(tool) && !found.has(tool.name)) {
      found.set(tool.name, { tool, deferred: deferred.has(tool.name) });
    }
  }
  return found;
}

/** A JSON answer for the model. */
function answer(value: unknown, usage?: PiUsage): ToolExecutionResult {
  return { output: [{ type: "text", text: JSON.stringify(value) }],
           ...(usage === undefined ? {} : { usage }) };
}

/** The `ta` tool, over the object's parts. */
export function taTool(parts: TaParts): ToolRegistration {
  const platform = platformCapabilities();
  const byName = new Map(platform.map((capability) => [capability.name, capability]));
  const need = (name: string): Need | undefined =>
    Object.hasOwn(TA_CAPABILITIES, name) ? TA_CAPABILITIES[name] : undefined;

  const catalog = async (page: Extract<TaRequest, { op: "catalog" }>, scope: Scope) => {
    const listed: Listed[] = [];
    for (const capability of platform) {
      if (parts.authority.refusal(scope.state, need(capability.name)) === undefined) {
        listed.push({ name: capability.name, description: capability.description,
                      effect: kindOf(capability.name) === "effect",
                      parameters: capability.parameters });
      }
    }
    // A tool has no need of its own in `ta`'s map: a client's grant reaches it only whole.
    if (parts.authority.refusal(scope.state, undefined) === undefined) {
      for (const { tool, deferred } of (await tools(scope.api, scope.context)).values()) {
        listed.push({ name: tool.name, description: tool.description, effect: true,
                      ...(deferred ? { deferred: true as const } : {}),
                      parameters: tool.parameters });
      }
    }
    const ordered = page.query === undefined || page.query.trim() === "" ? listed
      : rank(page.query, listed.map((entry) => ({ name: entry.name, text: searchText(entry) })),
             listed.length).map((name) => listed.find((entry) => entry.name === name)!);
    const shown = ordered.slice(page.offset, page.offset + page.limit);
    const next = page.offset + shown.length;
    return answer({ capabilities: shown, total: ordered.length,
                    ...(next < ordered.length ? { next_offset: next } : {}) });
  };

  const call = async (called: Extract<TaRequest, { op: "call" }>, scope: Scope) => {
    if (called.name.startsWith(TA_OP)) {
      throw new Refused(RESERVED_NAME);
    }
    const capability = byName.get(called.name);
    if (capability !== undefined) {
      const args = validateToolArguments(
        { name: capability.name, description: capability.description,
          parameters: capability.parameters },
        { type: "toolCall", id: scope.api.callId, name: capability.name,
          arguments: called.arguments }) as Record<string, unknown>;
      const required = need(capability.name);
      // A read writes nothing; an effect's unit re-checks the authority it acts under.
      const write: Write = kindOf(capability.name) === "read"
        ? () => { throw new Error(READ_WRITES); }
        : (unit) => parts.write(() => {
          parts.authority.require(scope.state, required);
          return unit();
        });
      const { value, usage } = await capability.run(args, { ...scope, write }, parts);
      return answer(value, usage);
    }
    if (!(await tools(scope.api, scope.context)).has(called.name)) {
      throw new Refused(`${UNKNOWN_CAPABILITY}: ${called.name}`);
    }
    const nested = await scope.api.executeTool(called.name, called.arguments, scope.context);
    const out = nested.structuredOutput;
    const text = out === undefined ? "" : typeof out === "string" ? out : JSON.stringify(out);
    return {
      output: text === "" ? [] : [{ type: "text" as const, text }],
      isError: nested.isError,
      diagnostics: nested.diagnostics,
    };
  };

  return defineTool({
    name: TA_TOOL,
    description: "Reach every ability beyond read, write, edit and bash. " +
      "op catalog lists what you may call (query ranks it; limit and offset page it), each " +
      "with its JSON Schema; op call runs one by name with its arguments.",
    parameters: Type.Object({
      op: StringEnum(["catalog", "call"] as const),
      query: Type.Optional(Type.String({ description: "catalog: words to rank by" })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: CATALOG_MAX,
                                          description: "catalog: a page's size" })),
      offset: Type.Optional(Type.Integer({ minimum: 0,
                                           description: "catalog: where the page starts" })),
      name: Type.Optional(Type.String({ description: "call: the capability or tool" })),
      arguments: Type.Optional(Type.Record(Type.String(), Type.Unknown(),
                                           { description: "call: its arguments" })),
    }, { additionalProperties: false }),
    callers: ["model"],
    replay: "unsafe",
    execute: async (args, api, context) => {
      const state = await parts.state(api, context);
      // The early gate approved some arguments; these are the ones this call runs with.
      const blocked = await parts.authority.decide(state, { name: TA_TOOL, arguments: args });
      if (blocked !== undefined) {
        throw new Refused(blocked.block);
      }
      const asked = request(args);
      const scope: Scope = { state, api, context, write: parts.write };
      return asked.op === "catalog" ? catalog(asked, scope) : call(asked, scope);
    },
  });
}
