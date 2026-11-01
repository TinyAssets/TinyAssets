/**
 * What crosses the `HOOKS` service binding for a hook (agent research §6.4): one request,
 * one reply, JSON in and out. The owner object's side is `owner/src/hooks.ts`, which
 * builds the request and holds the reply to its point's shape; this side checks every
 * field before anything runs, since a request is the only thing the Worker trusts nothing
 * about.
 *
 * - **Request:** `{owner, bundle: {id, modules?}, point, extension, module, input,
 *   context: {conversation_id, task_id, call_id?, authority}, deadline_ms, cpu_ms}`.
 *   `modules` (the owner's hook modules, by their file path, prebundled ESM) rides only a
 *   resend after `need_code`.
 * - **Reply:** `{ok: true, result}` or `{ok: false, error, message?}`, the error one of
 *   `need_code` (the runner holds no code for this bundle: the owner resends it once),
 *   `timeout` (past `deadline_ms`), `threw` (the hook, or the isolate, failed) and
 *   `invalid` (a request or reply that is not what it says).
 * - **Bundle id:** `<owner>/<sha256 of the modules>`, over their canonical JSON (each
 *   `[path, source]`, sorted by path), so it names one owner's one code: two owners with
 *   identical code never share an id, nor an isolate. The runner recomputes it from the
 *   code it is sent and refuses one that differs, so an id always yields the same code,
 *   as the Worker Loader requires.
 */

/** The points a hook may run at, remotely (agent research §6.3); `beforeRequest` is not one. */
export const HOOK_POINTS: readonly string[] = Object.freeze([
  "beforeTool", "afterTool", "onYield", "beforeCompact", "afterResponse", "afterTools",
]);
/** The most one owner's hook modules hold, in bytes of their UTF-8 source. */
export const BUNDLE_MAX = 1_048_576;
/** The most a hook's JSON input or reply holds, in UTF-16 code units of its JSON text. */
export const JSON_MAX = 4_194_304;
/** The longest deadline a request may ask, in ms, and the CPU it may ask per call. */
export const DEADLINE_MAX = 30_000;
export const CPU_MIN = 50;
export const CPU_MAX = 1_000;
/** The longest owner name, module path and extension name a request carries. */
export const NAME_MAX = 512;

export type HookError = "need_code" | "timeout" | "threw" | "invalid";

export type HookReply =
  | { readonly ok: true; readonly result: unknown }
  | { readonly ok: false; readonly error: HookError; readonly message?: string };

export interface HookContext {
  readonly conversation_id: number;
  readonly task_id: string;
  readonly call_id?: string;
  readonly authority: string;
}

export interface HookRequest {
  readonly owner: string;
  readonly bundle: { readonly id: string; readonly modules?: Readonly<Record<string, string>> };
  readonly point: string;
  readonly extension: string;
  readonly module: string;
  readonly input: unknown;
  readonly context: HookContext;
  readonly deadline_ms: number;
  readonly cpu_ms: number;
}

/** A request refused before anything ran. */
export class InvalidRequest extends Error {}

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function name(value: unknown, what: string): string {
  if (typeof value !== "string" || value === "" || value.length > NAME_MAX) {
    throw new InvalidRequest(`${what} is a name of 1 to ${NAME_MAX} characters`);
  }
  return value;
}

function whole(value: unknown, low: number, high: number, what: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < low || value > high) {
    throw new InvalidRequest(`${what} is a whole number from ${low} to ${high}`);
  }
  return value;
}

/** `value` as JSON text within `JSON_MAX`, or a refusal: no function, cycle or non-finite. */
export function jsonText(value: unknown): string | null {
  let text: string | undefined;
  try {
    text = JSON.stringify(value === undefined ? null : value);
  } catch {
    return null;
  }
  return text === undefined || text.length > JSON_MAX ? null : text;
}

/** The size of `modules`, in bytes of UTF-8, and that each is a path and its source. */
function modulesOf(value: unknown): Record<string, string> {
  if (!plain(value)) {
    throw new InvalidRequest("modules are a table of paths to sources");
  }
  const encoder = new TextEncoder();
  let size = 0;
  for (const [path, source] of Object.entries(value)) {
    name(path, "a module's path");
    if (typeof source !== "string") {
      throw new InvalidRequest("a module's source is text");
    }
    size += encoder.encode(source).byteLength;
  }
  if (Object.keys(value).length === 0 || size > BUNDLE_MAX) {
    throw new InvalidRequest(`a bundle holds 1 module to ${BUNDLE_MAX} bytes of them`);
  }
  return value as Record<string, string>;
}

/** `value` as a request, every field checked, or `InvalidRequest`. */
export function hookRequest(value: unknown): HookRequest {
  if (!plain(value) || !plain(value["bundle"]) || !plain(value["context"])) {
    throw new InvalidRequest("a hook request is an object with a bundle and a context");
  }
  const bundle = value["bundle"];
  const context = value["context"];
  const point = value["point"];
  if (typeof point !== "string" || !HOOK_POINTS.includes(point)) {
    throw new InvalidRequest("the point is one a hook may run at");
  }
  const input = jsonText(value["input"]);
  if (input === null) {
    throw new InvalidRequest("the input is JSON");
  }
  const callId = context["call_id"];
  return {
    owner: name(value["owner"], "the owner"),
    bundle: { id: name(bundle["id"], "the bundle id"),
              ...(bundle["modules"] === undefined ? {}
                : { modules: modulesOf(bundle["modules"]) }) },
    point,
    extension: name(value["extension"], "the extension"),
    module: name(value["module"], "the module"),
    input: JSON.parse(input) as unknown,
    context: {
      conversation_id: whole(context["conversation_id"], 0, Number.MAX_SAFE_INTEGER,
                             "the conversation"),
      task_id: name(context["task_id"], "the task"),
      ...(callId === undefined ? {} : { call_id: name(callId, "the call") }),
      authority: name(context["authority"], "the authority"),
    },
    deadline_ms: whole(value["deadline_ms"], 1, DEADLINE_MAX, "deadline_ms"),
    cpu_ms: whole(value["cpu_ms"], CPU_MIN, CPU_MAX, "cpu_ms"),
  };
}

/** The canonical JSON of a bundle's modules: each `[path, source]`, sorted by path. */
export function canonicalModules(modules: Readonly<Record<string, string>>): string {
  return JSON.stringify(Object.keys(modules).sort().map((path) => [path, modules[path]]));
}

/** The bundle id of `owner`'s `modules`: `<owner>/<sha256 hex of their canonical JSON>`. */
export async function bundleId(owner: string, modules: Readonly<Record<string, string>>):
    Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256",
                                            new TextEncoder().encode(canonicalModules(modules)));
  const hex = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `${owner}/${hex}`;
}
