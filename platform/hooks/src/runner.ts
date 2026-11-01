/**
 * `HookRunner`: one Durable Object per owner (`getByName(owner)`), which loads that owner's
 * hook bundle with the Worker Loader and calls one hook (agent research §6.4).
 *
 * It never uses its storage. The code it is sent lives in memory only, by loader name, a
 * few bundles at most (`CODES_KEPT`), so the owner's files stay its one durable place: an
 * instance that holds none answers `need_code`, and the owner object resends it once.
 *
 * Each bundle is loaded under `<owner>:<bundle id>:shim<SHIM_VERSION>`, so two owners with
 * identical code never share an isolate's module state, and a new shim is a new Worker.
 * Its code:
 * - `compatibilityDate` pinned to the owner Worker's (`COMPATIBILITY_DATE`; the config
 *   guard holds the two equal), and no flags;
 * - `globalOutbound: null`: `fetch()` and `connect()` throw, so a hook has no network;
 * - `env: {}`: no binding, so a hook reaches no ledger, policy or other owner;
 * - `limits: {cpuMs: CPU_MAX, subRequests: 0}`, the ceiling, since one name must always
 *   yield the same code; each call asks its own `cpu_ms` (the owner's policy, 50 by
 *   default), and the lower applies;
 * - our trusted shim (`shimSource`) as its main module. Its first import is our prelude
 *   (`preludeSource`), which runs before any package's module: it freezes the shared
 *   intrinsics (each constructor, its prototype and the global binding naming it), each
 *   prototype's data properties made accessors that define an own property on the object
 *   written, as SES does, so a module's own `toString` still works; and it hands the shim
 *   `JSON` and `Object.hasOwn` as they were. So one package cannot patch what another's
 *   guard relies on. The shim's dispatch table is frozen too; it dispatches the point to
 *   the module's export of that name, takes the input as JSON, holds the reply to JSON
 *   within `JSON_MAX`, and catches what the hook throws.
 *
 * A call past `deadline_ms` answers `timeout` here and is dropped: its entrypoint stub is
 * disposed, and at most `INFLIGHT_MAX` calls are pending at once, past which a call is
 * refused `timeout` at once, so calls the owner gave up on never pile up. The isolate may
 * compute on until its `cpuMs`, which costs this object, never the owner's (workerd
 * enforces no CPU limit: owed to H2's live probe).
 */
import { DurableObject } from "cloudflare:workers";

import {
  bundleId, CPU_MAX, type HookReply, type HookRequest, hookRequest, InvalidRequest, JSON_MAX,
} from "./protocol";

/** The owner Worker's compatibility date, which every hook bundle runs at. */
export const COMPATIBILITY_DATE = "2026-10-23";
/** The shim's version, in every loader name: a changed shim is a new Worker. */
export const SHIM_VERSION = 2;
/** The bundles one runner keeps in memory, newest last. */
export const CODES_KEPT = 4;
/** The hook calls one runner keeps pending at once. */
export const INFLIGHT_MAX = 16;

export interface RunnerEnv {
  readonly LOADER: WorkerLoader;
}

/** The loader name of `owner`'s bundle `id`: the owner, the id and the shim's version. */
export function loaderName(owner: string, id: string): string {
  return `${owner}:${id}:shim${SHIM_VERSION}`;
}

/**
 * The prelude, the bundle's first module to run: it freezes the shared intrinsics before
 * any package's module evaluates, and exports what the shim uses as it was.
 */
export function preludeSource(): string {
  return [
    "const { freeze, getOwnPropertyNames, getOwnPropertySymbols, getOwnPropertyDescriptor,",
    "        defineProperty, getPrototypeOf } = Object;",
    "export const parse = JSON.parse;",
    "export const stringify = JSON.stringify;",
    "export const hasOwn = Object.hasOwn;",
    "export const freezeTable = freeze;",
    "const NAMES = ['Object', 'Function', 'Array', 'String', 'Number', 'Boolean', 'Symbol',",
    "  'BigInt', 'Date', 'RegExp', 'Error', 'TypeError', 'RangeError', 'SyntaxError',",
    "  'ReferenceError', 'EvalError', 'URIError', 'AggregateError', 'Map', 'Set', 'WeakMap',",
    "  'WeakSet', 'WeakRef', 'Promise', 'JSON', 'Math', 'Reflect', 'Proxy', 'ArrayBuffer',",
    "  'DataView', 'Int8Array', 'Uint8Array', 'Uint8ClampedArray', 'Int16Array', 'Uint16Array',",
    "  'Int32Array', 'Uint32Array', 'Float32Array', 'Float64Array', 'BigInt64Array',",
    "  'BigUint64Array', 'TextEncoder', 'TextDecoder', 'URL', 'URLSearchParams'];",
    "const iterator = (value) => getPrototypeOf(value[Symbol.iterator]());",
    "const SHARED = [getPrototypeOf(Uint8Array), getPrototypeOf(Uint8Array.prototype),",
    "  iterator([]), getPrototypeOf(iterator([])), iterator(new Map()), iterator(new Set()),",
    "  iterator(''), getPrototypeOf(function* () {}), getPrototypeOf(async function () {}),",
    "  getPrototypeOf(async function* () {})];",
    "function overridable(object) {",
    "  for (const key of [...getOwnPropertyNames(object), ...getOwnPropertySymbols(object)]) {",
    "    const held = getOwnPropertyDescriptor(object, key);",
    "    if (held === undefined || !('value' in held) || !held.configurable) continue;",
    "    const value = held.value;",
    "    defineProperty(object, key, {",
    "      configurable: false, enumerable: held.enumerable,",
    "      get() { return value; },",
    "      set(next) {",
    "        if (this === object) throw new TypeError(`${String(key)} is frozen`);",
    "        defineProperty(this, key, { value: next, writable: true, enumerable: true,",
    "                                    configurable: true });",
    "      },",
    "    });",
    "  }",
    "  freeze(object);",
    "}",
    "function harden(value) {",
    "  if ((typeof value !== 'object' && typeof value !== 'function') || value === null) return;",
    "  for (const key of getOwnPropertyNames(value)) {",
    "    const held = getOwnPropertyDescriptor(value, key);",
    "    if (key !== 'prototype' && typeof held?.value === 'function') freeze(held.value);",
    "  }",
    "  freeze(value);",
    "  if (typeof value === 'function' && value.prototype !== undefined) {",
    "    overridable(value.prototype);",
    "  }",
    "}",
    "for (const name of NAMES) {",
    "  const value = globalThis[name];",
    "  if (value === undefined) continue;",
    "  harden(value);",
    "  const held = getOwnPropertyDescriptor(globalThis, name);",
    "  if (held !== undefined && held.configurable) {",
    "    defineProperty(globalThis, name, { value, writable: false, configurable: false,",
    "                                       enumerable: held.enumerable });",
    "  }",
    "}",
    "for (const shared of SHARED) overridable(shared);",
  ].join("\n");
}

/** The shim's source over modules named `m0.js`, `m1.js`, …, by their paths in `paths`. */
export function shimSource(paths: readonly string[]): string {
  const imports = paths.map((_path, at) => `import * as m${at} from "./m${at}.js";`);
  const table = paths.map((path, at) => `[${JSON.stringify(path)}]: m${at}`);
  return [
    // The prelude first: it runs before any package's module.
    `import { freezeTable, hasOwn, parse, stringify } from "./prelude.js";`,
    `import { WorkerEntrypoint } from "cloudflare:workers";`,
    ...imports,
    `const MODULES = freezeTable({ __proto__: null, ${table.join(", ")} });`,
    `const JSON_MAX = ${JSON_MAX};`,
    "function said(error) {",
    "  try { return String(error && error.message !== undefined ? error.message : error).slice(0, 500); }",
    "  catch { return \"the hook threw\"; }",
    "}",
    "export class HookShim extends WorkerEntrypoint {",
    "  async call(module, point, input, context) {",
    "    const found = hasOwn(MODULES, module) ? MODULES[module] : undefined;",
    "    const hook = found === undefined ? undefined : found[point];",
    "    if (typeof hook !== \"function\") {",
    "      return { ok: false, error: \"invalid\", message: `the module exports no ${point}` };",
    "    }",
    "    let result;",
    "    try {",
    "      result = await hook(parse(input), parse(context));",
    "    } catch (error) {",
    "      return { ok: false, error: \"threw\", message: said(error) };",
    "    }",
    "    let text;",
    "    try {",
    "      text = stringify(result === undefined ? null : result);",
    "    } catch {",
    "      return { ok: false, error: \"invalid\", message: \"the reply is not JSON\" };",
    "    }",
    "    if (typeof text !== \"string\" || text.length > JSON_MAX) {",
    "      return { ok: false, error: \"invalid\", message: \"the reply is not JSON within its bound\" };",
    "    }",
    "    return { ok: true, result: text };",
    "  }",
    "}",
  ].join("\n");
}

/** The Worker a bundle loads as: the prelude and the shim over the owner's modules. */
export function workerCode(modules: Readonly<Record<string, string>>): WorkerLoaderWorkerCode {
  const paths = Object.keys(modules).sort();
  const code: Record<string, string> = { "shim.js": shimSource(paths),
                                         "prelude.js": preludeSource() };
  paths.forEach((path, at) => {
    code[`m${at}.js`] = modules[path]!;
  });
  return {
    compatibilityDate: COMPATIBILITY_DATE,
    compatibilityFlags: [],
    mainModule: "shim.js",
    modules: code,
    env: {},
    globalOutbound: null,
    limits: { cpuMs: CPU_MAX, subRequests: 0 },
  };
}

function said(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 500);
}

/** The shim's own answer, held to its shape: anything else is the isolate's fault. */
function shimReply(value: unknown): HookReply {
  if (typeof value === "object" && value !== null) {
    const reply = value as Record<string, unknown>;
    if (reply["ok"] === true && typeof reply["result"] === "string") {
      try {
        return { ok: true, result: JSON.parse(reply["result"]) as unknown };
      } catch {
        return { ok: false, error: "invalid", message: "the reply is not JSON" };
      }
    }
    if (reply["ok"] === false && (reply["error"] === "threw" || reply["error"] === "invalid")) {
      return { ok: false, error: reply["error"],
               ...(typeof reply["message"] === "string" ? { message: reply["message"] } : {}) };
    }
  }
  return { ok: false, error: "invalid", message: "the shim answered out of shape" };
}

/** The shim's entrypoint as a call sees it. */
type Shim = {
  call(module: string, point: string, input: string, context: string): Promise<unknown>;
} & Partial<Disposable>;

/**
 * One call of a loaded bundle's shim: at the request's own CPU, past its deadline
 * `timeout` and the stub disposed, so the call is dropped; `onSettled` once the call
 * settles or is dropped (it may be called twice; the caller counts it once).
 */
export async function callShim(loader: WorkerLoader, name: string,
                               modules: Readonly<Record<string, string>>, request: HookRequest,
                               onSettled: () => void = () => undefined): Promise<HookReply> {
  const worker = loader.get(name, () => workerCode(modules));
  const shim = worker.getEntrypoint("HookShim", {
    limits: { cpuMs: request.cpu_ms, subRequests: 0 } }) as unknown as Shim;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let call: Promise<unknown>;
  try {
    call = shim.call(request.module, request.point, JSON.stringify(request.input),
                     JSON.stringify(request.context));
  } catch (error) {
    onSettled();
    return { ok: false, error: "threw", message: said(error) };
  }
  void call.then(onSettled, onSettled);
  const late = new Promise<"late">((resolve) => {
    timer = setTimeout(() => resolve("late"), request.deadline_ms);
  });
  try {
    const answer = await Promise.race([call, late]);
    if (answer === "late") {
      onSettled();  // dropped: no longer pending, whatever the isolate does with it
      try {
        shim[Symbol.dispose]?.();  // dropped: the owner has given up on it
      } catch {
        // a stub that cannot be disposed is dropped all the same
      }
      return { ok: false, error: "timeout" };
    }
    return shimReply(answer);
  } catch (error) {
    if (error instanceof InvalidRequest) {
      return { ok: false, error: "invalid", message: said(error) };
    }
    // The isolate failed: a module that does not load, a CPU limit, a crash.
    return { ok: false, error: "threw", message: said(error) };
  } finally {
    clearTimeout(timer);
  }
}

export class HookRunner extends DurableObject<RunnerEnv> {
  /** The bundles this instance was sent, by loader name, oldest first. */
  readonly #codes = new Map<string, Readonly<Record<string, string>>>();
  /** The calls of this instance not yet settled, answered or dropped. */
  #pending = 0;

  /** How many calls are pending: a test's view of the bound. */
  pending(): number {
    return this.#pending;
  }

  /** One hook call: the reply, never a throw. */
  async run(value: unknown): Promise<HookReply> {
    let request: HookRequest;
    try {
      request = hookRequest(value);
    } catch (error) {
      return { ok: false, error: "invalid", message: said(error) };
    }
    if (this.ctx.id.name !== request.owner) {
      return { ok: false, error: "invalid", message: "this runner is another owner's" };
    }
    const name = loaderName(request.owner, request.bundle.id);
    const sent = request.bundle.modules;
    if (sent !== undefined) {
      if (await bundleId(request.owner, sent) !== request.bundle.id) {
        return { ok: false, error: "invalid", message: "the bundle id is not its code's" };
      }
      this.#codes.delete(name);
      this.#codes.set(name, sent);
      while (this.#codes.size > CODES_KEPT) {
        this.#codes.delete(this.#codes.keys().next().value!);
      }
    }
    const modules = this.#codes.get(name);
    if (modules === undefined) {
      return { ok: false, error: "need_code" };
    }
    if (!Object.hasOwn(modules, request.module)) {
      return { ok: false, error: "invalid", message: "the bundle has no such module" };
    }
    if (this.#pending >= INFLIGHT_MAX) {
      return { ok: false, error: "timeout", message: `${INFLIGHT_MAX} calls are pending` };
    }
    this.#pending++;
    let settled = false;
    return callShim(this.env.LOADER, name, modules, request, () => {
      if (!settled) {
        settled = true;
        this.#pending--;
      }
    });
  }
}
