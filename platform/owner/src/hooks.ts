/**
 * A package's executable hooks, run out of the owner's object (ADR-032 §4; agent research
 * §6.3-6.5; tasks 5, H1): each selected package's `tinyassets.hooks` entries become remote
 * wrappers on its `pkg:<name>` extension, which call the hooks Worker (`HOOKS`, the
 * `tinyassets-hooks` Worker of `platform/hooks`) over RPC, with JSON in and out and a
 * deadline. No package code runs in the object: a wrapper is ours, and only its JSON
 * crosses.
 *
 * - **The bundle:** one per owner, every selected package's hook modules (prebundled ESM,
 *   read from the owner's own files, at most `BUNDLE_MAX` bytes together), its id
 *   `<owner>/<sha256 of the modules>` (`bundleOf`), so two owners never share an isolate,
 *   and a changed module is a new Worker. The hooks Worker keeps the code in memory only;
 *   a call is sent without it, and on `need_code` once more with it, so the owner's files
 *   stay its one durable place.
 * - **Points:** `beforeTool`, `afterTool`, `onYield`, `beforeCompact`, and, observe-only,
 *   `afterResponse` and `afterTools`. `beforeRequest` stays trusted and in-process: it runs
 *   before every attempt, cannot refuse, and is the whole context. A point no selected
 *   package declares gets no wrapper, so no call is made; a tool hook whose entry names
 *   `tools` is called only for those.
 * - **The deadline:** each call waits `inTime(HOOKS.run(...), deadline_ms + 250)`, the
 *   hook's `timeout_ms` (its manifest's, within the owner policy's `hooks.timeout_ms`,
 *   2,000 by default; an observe-only point's within `OBSERVE_DEADLINE_MS`, 250); the call
 *   asks `cpu_ms` from policy (`hooks.cpu_ms`, 50 to 1,000).
 * - **Failure rules (§6.3):** a `beforeTool` timeout or error blocks the call; an
 *   `afterTool` failure fails closed, the result replaced by a redacted error (pi's own rule
 *   would keep the unredacted result); an `onYield` failure continues nothing; a
 *   `beforeCompact` failure leaves pi's own summary; an observe-only failure is reported
 *   only. A reply out of its point's shape, or a decision past `HOOK_DECISION_MAX`, is a
 *   failure (`invalid`). Every wrapper is total: any error, the memo's read or store
 *   included, is its point's failure, never a throw pi would skip. Each failure is an
 *   owner notice (`hook_failed`, once while it is kept).
 * - **Crash survival:** each wrapper reads `api.memo("ta.hook/<ext>/<point>/<key>")`
 *   first and returns a decision found there without calling the hook; otherwise it calls,
 *   and stores the decision (a failure's included) with `api.memo`, whose durable winner
 *   it returns. The key is the call's id for `beforeTool`, plus a digest of the result for
 *   `afterTool`; a digest of what the point sees for the others. So a rerun after a crash
 *   between the decision and the tool's intent (pi reruns the call phase from the top)
 *   gets the same decision, and the hook is not asked again.
 *
 * Network: none in v1 (`globalOutbound: null` in the hooks Worker); a hook reaches no
 * ledger, policy or file but the JSON it is handed.
 */
import { createHash } from "node:crypto";

import type { JsonValue } from "@earendil-works/chord";
import type { Context } from "@earendil-works/chord/context";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import type {
  CodemodeRemoteExchange, CodemodeRemoteRequest,
} from "@earendil-works/pi-codemode/portable";
import {
  CompactionTask, defineExtension, type EntryId, type Extension, GenerationTask, hook,
  type HookApi, type HookRegistration, type JsonObject, type ToolExecutionResult,
  type ToolHookCall, ToolTask,
} from "@earendil-works/pi-durable";

import { Invalid } from "./errors";
import { inTime } from "./schedule";
import { codePoints } from "./text";

/** The points a hook may run at remotely, the tool points, and the observe-only points. */
export const HOOK_POINTS: readonly string[] = Object.freeze([
  "beforeTool", "afterTool", "onYield", "beforeCompact", "afterResponse", "afterTools",
]);
export const TOOL_POINTS: readonly string[] = Object.freeze(["beforeTool", "afterTool"]);
export const OBSERVE_POINTS: readonly string[] = Object.freeze(["afterResponse", "afterTools"]);
/** What the owner waits past a hook's own deadline, for the RPC itself. */
export const HOOK_SLACK_MS = 250;
/** An observe-only point's deadline: it only reports, so it never holds a response long. */
export const OBSERVE_DEADLINE_MS = 250;
/**
 * The most a decision holds, in UTF-16 code units of its JSON (256 KiB): a replaced result
 * or rewritten arguments past it fail as `invalid`, so every memo fits the store's row.
 */
export const HOOK_DECISION_MAX = 262_144;
/** The most one owner's hook modules hold, in bytes (the hooks Worker's bound too). */
export const BUNDLE_MAX = 1_048_576;
/** The most a hook's JSON input holds, in code units (the hooks Worker's `JSON_MAX`). */
export const HOOK_JSON_MAX = 4_194_304;
/** The hook entries one package declares at most, and the tools one entry names. */
export const HOOK_ENTRIES = 16;
export const HOOK_TOOLS = 64;
/** A block's or a continuation's most characters. */
export const HOOK_TEXT_MAX = 32_000;
/** The notice of a hook that failed, and the memo names' prefix. */
export const HOOK_FAILED = "hook_failed";
export const HOOK_MEMO = "ta.hook";
/** The code of a call a hook blocked by failing, and of a result it failed closed. */
export const HOOK_FAILED_CODE = "hook_failed";
/** The prefix of the extension a package's hooks make. */
export const PACKAGE_EXTENSION = "pkg:";

/** The owner policy's hook settings (`policy.ts`, the `hooks` table). */
export interface HookSettings {
  /** The longest a hook is waited for, in ms: the default and the ceiling of a manifest's. */
  readonly timeout_ms: number;
  /** The CPU each call asks the hooks Worker for, in ms. */
  readonly cpu_ms: number;
}

/** What the owner's `HOOKS` binding serves (`platform/hooks/src/index.ts`'s `Hooks`). */
export interface HooksService {
  run(request: HookRequest): Promise<HookReply>;
  /** One code mode execution in the sandbox object `name`: pi's `CodemodeRemote.execute`. */
  codemodeExecute(name: string, request: CodemodeRemoteRequest,
                  exchange: CodemodeRemoteExchange): Promise<void>;
}

export type HookError = "need_code" | "timeout" | "threw" | "invalid";
export type HookReply =
  | { readonly ok: true; readonly result: unknown }
  | { readonly ok: false; readonly error: HookError; readonly message?: string };

/** One hook call, as `protocol.ts` of the hooks Worker takes it. */
export interface HookRequest {
  readonly owner: string;
  readonly bundle: { readonly id: string; readonly modules?: Readonly<Record<string, string>> };
  readonly point: string;
  readonly extension: string;
  readonly module: string;
  readonly input: JsonValue;
  readonly context: {
    readonly conversation_id: number;
    readonly task_id: string;
    readonly call_id?: string;
    readonly authority: string;
  };
  readonly deadline_ms: number;
  readonly cpu_ms: number;
}

/** One `tinyassets.hooks` entry, its module resolved to the owner file it is. */
export interface HookEntry {
  readonly module: string;
  readonly points: readonly string[];
  readonly tools?: readonly string[];
  readonly timeout_ms?: number;
}

/** A package's hooks: its extension's name, and its entries. */
export interface PackageHooks {
  readonly extension: string;
  readonly entries: readonly HookEntry[];
}

/** One owner's hook bundle: its id, and its modules by file path. */
export interface Bundle {
  readonly id: string;
  readonly modules: Readonly<Record<string, string>>;
}

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const ENTRY_KEYS: readonly string[] = Object.freeze(["module", "points", "tools", "timeout_ms"]);

/**
 * A manifest's `tinyassets.hooks` list, its modules resolved by `resolveModule` (which
 * refuses one outside the package), or `Invalid`: at most `HOOK_ENTRIES` entries of known
 * keys, each a `.js` or `.mjs` module, points it may run at (each once in the package; a
 * tool list only with a tool point), and a whole `timeout_ms` from 1 to 30,000.
 */
export function hookEntries(value: unknown, resolveModule: (path: string) => string):
    HookEntry[] {
  if (!Array.isArray(value) || value.length > HOOK_ENTRIES) {
    throw new Invalid(`tinyassets.hooks is a list of at most ${HOOK_ENTRIES} entries`);
  }
  const declared = new Set<string>();
  return value.map((entry): HookEntry => {
    if (!plain(entry) || Object.keys(entry).some((key) => !ENTRY_KEYS.includes(key))) {
      throw new Invalid("a hook entry takes module, points, tools and timeout_ms");
    }
    const { module, points, tools, timeout_ms: timeout } = entry;
    if (typeof module !== "string" || !/\.m?js$/.test(module)) {
      throw new Invalid("a hook's module is a prebundled .js or .mjs file");
    }
    if (!Array.isArray(points) || points.length === 0
        || points.some((point) => typeof point !== "string" || !HOOK_POINTS.includes(point))) {
      throw new Invalid(`a hook's points are among ${HOOK_POINTS.join(", ")} (beforeRequest ` +
                        "stays in-process)");
    }
    for (const point of points as string[]) {
      if (declared.has(point)) {
        throw new Invalid(`a package declares ${point} once`);
      }
      declared.add(point);
    }
    if (tools !== undefined && (!Array.isArray(tools) || tools.length === 0
        || tools.length > HOOK_TOOLS
        || tools.some((tool) => typeof tool !== "string" || tool === "")
        || !(points as string[]).every((point) => TOOL_POINTS.includes(point)))) {
      throw new Invalid(`a hook's tools are 1 to ${HOOK_TOOLS} names, for tool points only`);
    }
    if (timeout !== undefined && (typeof timeout !== "number" || !Number.isSafeInteger(timeout)
                                  || timeout < 1 || timeout > 30_000)) {
      throw new Invalid("a hook's timeout_ms is a whole number of ms, 1 to 30,000");
    }
    return {
      module: resolveModule(module),
      points: [...points as string[]],
      ...(tools === undefined ? {} : { tools: [...tools as string[]] }),
      ...(timeout === undefined ? {} : { timeout_ms: timeout }),
    };
  });
}

/** The canonical JSON of a bundle's modules: each `[path, source]`, sorted by path. */
export function canonicalModules(modules: Readonly<Record<string, string>>): string {
  return JSON.stringify(Object.keys(modules).sort().map((path) => [path, modules[path]]));
}

/** `owner`'s bundle of `modules`: its id is the owner and a digest of the code. */
export function bundleOf(owner: string, modules: Readonly<Record<string, string>>): Bundle {
  const digest = createHash("sha256").update(canonicalModules(modules), "utf8").digest("hex");
  return { id: `${owner}/${digest}`, modules };
}

/** A short digest of a JSON value, for a memo key. */
function digestOf(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value) ?? "null", "utf8").digest("hex")
    .slice(0, 32);
}

/** What a package's wrappers call through. */
export interface HookParts {
  /** The `HOOKS` binding; none: every hook fails, by its point's rule. */
  readonly service: () => HooksService | undefined;
  /** The owner the object is named by. */
  readonly owner: () => string;
  /** The owner policy's hook settings, read at each call. */
  readonly settings: () => HookSettings;
  /** Keep a notice, once while it is kept. */
  readonly notice: (type: string, detail: string) => void;
  /** Who the asking conversation acts as, for the hook's context: owner, client:<id>, none. */
  readonly authority: (api: HookApi, context: Context) => Promise<string>;
}

/** A failure, as its decision records it. */
class HookFailure extends Error {
  constructor(readonly code: HookError | "unbound", message?: string) {
    super(message === undefined ? code : `${code}: ${message}`);
  }
}

/**
 * One remote call: sent without the code, and once more with it on `need_code`, all
 * within `deadline_ms + HOOK_SLACK_MS`; the reply's result, or a `HookFailure`.
 */
export async function callHook(parts: HookParts, bundle: Bundle, request: Omit<HookRequest,
                               "owner" | "bundle" | "cpu_ms">): Promise<unknown> {
  const service = parts.service();
  if (service === undefined) {
    throw new HookFailure("unbound", "no hooks Worker is bound");
  }
  const owner = parts.owner();
  const asked = { ...request, owner, cpu_ms: parts.settings().cpu_ms };
  const end = Date.now() + request.deadline_ms + HOOK_SLACK_MS;
  const once = async (modules: boolean): Promise<HookReply> => {
    let reply: HookReply | "late";
    try {
      reply = await inTime(service.run({
        ...asked, bundle: modules ? bundle : { id: bundle.id } }), end - Date.now());
    } catch (error) {
      throw new HookFailure("threw", error instanceof Error ? error.message : String(error));
    }
    if (reply === "late") {
      throw new HookFailure("timeout");
    }
    return reply;
  };
  let reply = await once(false);
  if (!reply.ok && reply.error === "need_code") {
    reply = await once(true);
  }
  if (!reply.ok) {
    throw new HookFailure(reply.error, reply.message);
  }
  return reply.result;
}

/** A JSON value's text within `HOOK_JSON_MAX`, or `invalid`. */
function bounded(value: unknown): JsonValue {
  const text = JSON.stringify(value);
  if (text === undefined || text.length > HOOK_JSON_MAX) {
    throw new HookFailure("invalid", "the hook's input is past its bound");
  }
  return JSON.parse(text) as JsonValue;
}

/** Text of at most `HOOK_TEXT_MAX` code points, else `invalid`. */
function text(value: unknown, what: string): string {
  if (typeof value !== "string" || value === "" || codePoints(value) > HOOK_TEXT_MAX) {
    throw new HookFailure("invalid", `${what} is text of 1 to ${HOOK_TEXT_MAX} characters`);
  }
  return value;
}

/** Whether `value` holds only `keys`. */
function only(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}

/** A call as a hook sees it. */
function callJson(call: ToolHookCall): JsonValue {
  return { id: call.id, name: call.name, arguments: call.arguments as JsonObject,
           ...(call.parent === undefined ? {}
             : { parent: { task_id: String(call.parent.taskId),
                           call_id: call.parent.callId } }) };
}

/** A result as a hook sees it: its text, error and structured value; images by type only. */
function resultJson(result: ToolExecutionResult): JsonValue {
  return {
    output: (result.output ?? []).map((part) => (part.type === "text"
      ? { type: "text", text: part.text } : { type: "image", mimeType: part.mimeType })),
    isError: result.isError === true,
    diagnostics: (result.diagnostics ?? []).map((diagnostic) => ({ ...diagnostic })),
    ...(result.structuredOutput === undefined ? {}
      : { structuredOutput: result.structuredOutput }),
  };
}

/** `beforeTool`'s reply as its decision: a block, new arguments, or nothing. */
function beforeToolDecision(reply: unknown): JsonObject {
  if (reply === null || (plain(reply) && Object.keys(reply).length === 0)) {
    return {};
  }
  if (plain(reply) && only(reply, ["block"]) && reply["block"] !== undefined) {
    return { block: text(reply["block"], "a block") };
  }
  if (plain(reply) && only(reply, ["arguments"]) && plain(reply["arguments"])) {
    return { arguments: reply["arguments"] as JsonObject };
  }
  throw new HookFailure("invalid", "beforeTool answers null, {block} or {arguments}");
}

/** `afterTool`'s reply as its decision: a replaced result (text output only), or keep. */
function afterToolDecision(reply: unknown): JsonObject {
  if (reply === null) {
    return { keep: true };
  }
  if (!plain(reply) || !only(reply, ["output", "isError", "diagnostics", "structuredOutput"])) {
    throw new HookFailure("invalid", "afterTool answers null or a result's output, isError, " +
                                     "diagnostics and structuredOutput");
  }
  const { output, isError, diagnostics, structuredOutput } = reply;
  const parts = output === undefined ? [] : output;
  if (!Array.isArray(parts) || parts.some((part) => !plain(part) || part["type"] !== "text"
                                          || typeof part["text"] !== "string"
                                          || !only(part, ["type", "text"]))) {
    throw new HookFailure("invalid", "a replaced result's output is text");
  }
  const notes = diagnostics === undefined ? [] : diagnostics;
  if (!Array.isArray(notes) || notes.some((note) => !plain(note)
      || !only(note, ["severity", "message", "code"])
      || !["info", "warn", "error"].includes(String(note["severity"]))
      || typeof note["message"] !== "string"
      || (note["code"] !== undefined && typeof note["code"] !== "string"))) {
    throw new HookFailure("invalid", "a replaced result's diagnostics are pi's");
  }
  if (isError !== undefined && typeof isError !== "boolean") {
    throw new HookFailure("invalid", "a replaced result's isError is a boolean");
  }
  return { result: {
    output: parts as JsonValue, diagnostics: notes as JsonValue, isError: isError === true,
    ...(structuredOutput === undefined ? {} : { structuredOutput: structuredOutput as JsonValue }),
  } };
}

/** `onYield`'s reply as its decision: a continuation's text, or nothing. */
function onYieldDecision(reply: unknown): JsonObject {
  if (reply === null) {
    return {};
  }
  if (plain(reply) && only(reply, ["continue"])) {
    return { continue: text(reply["continue"], "a continuation") };
  }
  throw new HookFailure("invalid", "onYield answers null or {continue: text}");
}

/** `beforeCompact`'s reply as its decision: decline, a summary, or nothing. */
function beforeCompactDecision(reply: unknown): JsonObject {
  if (reply === null) {
    return {};
  }
  if (plain(reply) && only(reply, ["decline"]) && reply["decline"] === true) {
    return { decline: true };
  }
  if (plain(reply) && only(reply, ["summary"])) {
    return { summary: text(reply["summary"], "a summary") };
  }
  throw new HookFailure("invalid", "beforeCompact answers null, {decline: true} or {summary}");
}

/** The result a failed `afterTool` hook leaves: an error, the tool's output withheld. */
export function failedClosed(extension: string): ToolExecutionResult {
  return { output: [{ type: "text", text: "hook failed" }], isError: true,
           diagnostics: [{ severity: "error", code: HOOK_FAILED_CODE,
                           message: `${extension}'s afterTool hook failed, so the tool's ` +
                             "result is withheld" }] };
}

/** The memo name of a decision: `ta.hook/<ext>/<point>/<key>`. */
export function memoName(extension: string, point: string, key: string): string {
  return `${HOOK_MEMO}/${extension}/${point}/${key}`;
}

/**
 * The `pkg:<name>` extension of a package's hooks: one wrapper per declared point, each
 * calling the hooks Worker under its point's failure rule, its decision memoized.
 *
 * Every wrapper is total: pi skips a hook that throws, which for `afterTool` would commit
 * the unredacted result, so nothing a wrapper does throws. Any error (the hook's, a reply
 * out of shape, a decision past `HOOK_DECISION_MAX`, the memo's read or store, a notice)
 * is the point's failure: `beforeTool` blocks, `afterTool` fails closed, `onYield`
 * continues nothing, `beforeCompact` leaves pi's summary, an observe-only point reports.
 * Only a bounded decision is memoized, so a memo never passes the store's row size.
 */
export function hookExtension(parts: HookParts, bundle: Bundle, hooks: PackageHooks): Extension {
  const { extension } = hooks;
  const entryOf = (point: string) => hooks.entries.find((entry) => entry.points.includes(point));
  const deadline = (entry: HookEntry, point: string) => {
    const ceiling = OBSERVE_POINTS.includes(point) ? OBSERVE_DEADLINE_MS
      : parts.settings().timeout_ms;
    return Math.min(entry.timeout_ms ?? ceiling, ceiling);
  };
  /** A failure's notice, kept once; its message. Never throws. */
  const failed = (point: string, error: unknown): string => {
    const code = error instanceof HookFailure ? error.code : "threw";
    try {
      parts.notice(HOOK_FAILED, `${extension} ${point}: ${code}`);
    } catch {
      console.error("a hook's failure notice could not be asked for");
    }
    try {
      return error instanceof Error ? error.message : String(error);
    } catch {
      return code;
    }
  };
  /**
   * The decision for `point` at `key`: the memo's, else the hook's reply as `decide` reads
   * it, memoized, the durable winner. An error before the store (the memo's read among them)
   * is `failure`'s decision, memoized when the memo still takes it; a store that fails
   * rejects, which the point's `total` makes its failure.
   */
  const decision = async (point: string, key: string, entry: HookEntry, input: unknown,
                          api: HookApi, context: Context, callId: string | undefined,
                          decide: (reply: unknown) => JsonObject,
                          failure: (message: string) => JsonObject): Promise<JsonObject> => {
    const name = memoName(extension, point, key);
    let made: JsonObject;
    try {
      const found = await api.memo<JsonObject>(name, context);
      if (found !== undefined) {
        return found;
      }
      const reply = await callHook(parts, bundle, {
        point, extension, module: entry.module, input: bounded(input),
        context: { conversation_id: Number(api.conversationId), task_id: String(api.taskId),
                   ...(callId === undefined ? {} : { call_id: callId }),
                   authority: await parts.authority(api, context) },
        deadline_ms: deadline(entry, point),
      });
      made = decide(reply);
      if ((JSON.stringify(made) ?? "").length > HOOK_DECISION_MAX) {
        throw new HookFailure("invalid", `the decision is past ${HOOK_DECISION_MAX} characters`);
      }
    } catch (error) {
      made = failure(failed(point, error));
      try {
        return await api.memo<JsonObject>(name, made, context);
      } catch {
        return made;  // the failure's decision stands, kept or not
      }
    }
    // A store that fails rejects here, which the point's `total` makes its failure.
    return api.memo<JsonObject>(name, made, context);
  };
  /** `run`'s answer, or `fallback`'s on any error, with its notice: never a throw. */
  const total = async <T>(point: string, fallback: (message: string) => T,
                          run: () => Promise<T>): Promise<T> => {
    try {
      return await run();
    } catch (error) {
      return fallback(failed(point, error));
    }
  };
  const blocked = (message: string) => ({ block: `${HOOK_FAILED_CODE}: ${extension}: ${message}` });
  const registrations: HookRegistration[] = [];
  const before = entryOf("beforeTool");
  const after = entryOf("afterTool");
  const applies = (entry: HookEntry, call: ToolHookCall) =>
    entry.tools === undefined || entry.tools.includes(call.name);
  if (before !== undefined || after !== undefined) {
    registrations.push(hook(ToolTask, {
      ...(before === undefined ? {} : {
        beforeTool: (call: ToolHookCall, api: HookApi, context: Context) =>
          total<{ block: string } | { arguments: JsonObject } | undefined>("beforeTool", blocked,
                                                                          async () => {
            if (!applies(before, call)) {
              return undefined;
            }
            const made = await decision("beforeTool", call.id, before, { call: callJson(call) },
              api, context, call.id, beforeToolDecision, blocked);
            return typeof made["block"] === "string" ? { block: made["block"] }
              : plain(made["arguments"]) ? { arguments: made["arguments"] as JsonObject }
                : undefined;
          }),
      }),
      ...(after === undefined ? {} : {
        afterTool: (call: ToolHookCall, result: ToolExecutionResult, api: HookApi,
                    context: Context) =>
          total<ToolExecutionResult | undefined>("afterTool", () => failedClosed(extension),
                                                 async () => {
            if (!applies(after, call)) {
              return undefined;
            }
            const seen = resultJson(result);
            const made = await decision("afterTool", `${call.id}/${digestOf(seen)}`, after,
              { call: callJson(call), result: seen }, api, context, call.id, afterToolDecision,
              () => ({ result: failedClosed(extension) as unknown as JsonValue }));
            if (made["keep"] === true) {
              return undefined;
            }
            return plain(made["result"]) ? made["result"] as unknown as ToolExecutionResult
              : failedClosed(extension);
          }),
      }),
    }));
  }
  const yielded = entryOf("onYield");
  const response = entryOf("afterResponse");
  const round = entryOf("afterTools");
  if (yielded !== undefined || response !== undefined || round !== undefined) {
    registrations.push(hook(GenerationTask, {
      ...(yielded === undefined ? {} : {
        onYield: (answer: AssistantMessage, api: HookApi, context: Context) =>
          total<{ continue: string } | undefined>("onYield", () => undefined, async () => {
            const seen = answer as unknown as JsonValue;
            const made = await decision("onYield", digestOf(seen), yielded, { answer: seen },
                                        api, context, undefined, onYieldDecision, () => ({}));
            return typeof made["continue"] === "string"
              ? { continue: made["continue"] } : undefined;
          }),
      }),
      ...(response === undefined ? {} : {
        afterResponse: (message: AssistantMessage, api: HookApi, context: Context) =>
          total<void>("afterResponse", () => undefined, async () => {
            const seen = message as unknown as JsonValue;
            await decision("afterResponse", digestOf(seen), response, { message: seen }, api,
                           context, undefined, () => ({}), () => ({}));
          }),
      }),
      ...(round === undefined ? {} : {
        afterTools: (assistant: EntryId, results: readonly EntryId[], api: HookApi,
                     context: Context) =>
          total<void>("afterTools", () => undefined, async () => {
            const seen = { assistant: String(assistant), results: results.map(String) };
            await decision("afterTools", String(assistant), round, seen, api, context,
                           undefined, () => ({}), () => ({}));
          }),
      }),
    }));
  }
  const compact = entryOf("beforeCompact");
  if (compact !== undefined) {
    registrations.push(hook(CompactionTask, {
      beforeCompact: (compaction, api: HookApi, context: Context) =>
        total<{ decline: true } | { summary: string } | undefined>("beforeCompact",
                                                                   () => undefined, async () => {
          const seen = {
            reason: compaction.reason as unknown as JsonValue,
            first_kept: String(compaction.firstKept),
            entries: compaction.entries.map((entry) => String(entry.id)),
            messages: compaction.messages as unknown as JsonValue,
            ...(compaction.instructions === undefined ? {}
              : { instructions: compaction.instructions }),
          };
          const made = await decision("beforeCompact",
                                      `${seen.first_kept}/${digestOf(seen.entries)}`, compact,
                                      seen, api, context, undefined, beforeCompactDecision,
                                      () => ({}));
          return made["decline"] === true ? { decline: true as const }
            : typeof made["summary"] === "string" ? { summary: made["summary"] } : undefined;
        }),
    }));
  }
  return Object.freeze(defineExtension({ name: extension, hooks: registrations }));
}
