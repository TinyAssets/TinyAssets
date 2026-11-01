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
 *   (`preludeSource`), which runs before any package's module: it hardens the whole
 *   global environment (every global binding fixed, everything reachable from them
 *   frozen, prototypes' data properties made accessors as SES's override taming does, so
 *   a module's own `toString` still works), and hands the shim `JSON` and `Object.hasOwn`
 *   as they were. The shim hardens its own dispatch path before any hook runs (`HookShim`,
 *   its prototype and `call`, what they close over; its table frozen), so a package that
 *   imports the shim changes nothing of it. So one package cannot patch what another's
 *   guard relies on. A package may still import another's module namespace and mutate an
 *   object that module exports: the owner's packages share one isolate, the owner's own
 *   trust. The shim dispatches the point to the module's export of that name, takes the
 *   input as JSON, holds the reply to JSON within `JSON_MAX`, and catches what the hook
 *   throws.
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
 * The prelude, the bundle's first module to run: before any package's module evaluates, it
 * hardens the whole global environment. Every own property of `globalThis` becomes a
 * binding that cannot be written or redefined, and everything reachable from them is
 * frozen, transitively: each value, getter and setter, each prototype chain, the state a
 * host object keeps behind its prototype's getters (`crypto.subtle`), the shared iterator,
 * generator and async prototypes no global names, and `WorkerEntrypoint`. A prototype's
 * data properties become accessors that define an own property on the object written, as
 * SES's override taming does, so a module's own `toString` still works. It exports `harden`
 * for the shim's own dispatch path, and what the shim uses as it was. (SES's `lockdown()`
 * was measured instead: it runs in workerd, adds about 236 KB to every bundle and about
 * 30 ms to an isolate's first call, and leaves host globals such as `structuredClone`,
 * `setTimeout` and `crypto.subtle.digest` writable, so this walk would be needed anyway.)
 */
export function preludeSource(): string {
  return [
    "import { WorkerEntrypoint } from 'cloudflare:workers';",
    "const { freeze, getOwnPropertyNames, getOwnPropertySymbols, getOwnPropertyDescriptor,",
    "        defineProperty, getPrototypeOf, isFrozen } = Object;",
    "const ownKeys = (value) => [...getOwnPropertyNames(value), ...getOwnPropertySymbols(value)];",
    "const reference = (value) => (typeof value === 'object' && value !== null)",
    "  || typeof value === 'function';",
    "export const parse = JSON.parse;",
    "export const stringify = JSON.stringify;",
    "export const hasOwn = Object.hasOwn;",
    "export const freezeTable = freeze;",
    "function overridable(object) {",
    "  for (const key of ownKeys(object)) {",
    "    const held = getOwnPropertyDescriptor(object, key);",
    "    if (held === undefined || !('value' in held) || !held.configurable) continue;",
    "    const value = held.value;",
    "    const accessors = {",
    "      get() { return value; },",
    "      set(next) {",
    "        if (this === object) throw new TypeError(`${String(key)} is frozen`);",
    "        defineProperty(this, key, { value: next, writable: true, enumerable: true,",
    "                                    configurable: true });",
    "      },",
    "    };",
    "    freeze(accessors.get);",
    "    freeze(accessors.set);",
    "    defineProperty(object, key, { configurable: false, enumerable: held.enumerable,",
    "                                  get: accessors.get, set: accessors.set });",
    "  }",
    "}",
    "export function harden(roots, prototypes = []) {",
    "  const found = new Map();",
    "  const work = [...roots.map((root) => [root, false]),",
    "                ...prototypes.map((prototype) => [prototype, true])];",
    "  while (work.length > 0) {",
    "    const [value, prototype] = work.pop();",
    "    if (!reference(value) || value === globalThis) continue;",
    "    if (found.has(value)) {",
    "      if (prototype) found.set(value, true);",
    "      continue;",
    "    }",
    "    found.set(value, prototype);",
    "    work.push([getPrototypeOf(value), true]);",
    "    for (const key of ownKeys(value)) {",
    "      const held = getOwnPropertyDescriptor(value, key);",
    "      if (held === undefined) continue;",
    "      work.push([held.value, key === 'prototype'], [held.get, false], [held.set, false]);",
    "    }",
    "  }",
    "  for (const [value, prototype] of found) {",
    "    if (isFrozen(value)) continue;",
    "    if (prototype) overridable(value);",
    "    freeze(value);",
    "  }",
    "}",
    "const iterator = (value) => getPrototypeOf(value[Symbol.iterator]());",
    "const SHARED = [iterator([]), iterator(new Map()), iterator(new Set()), iterator(''),",
    "  iterator('a'.matchAll(/a/g)), getPrototypeOf(function* () {}),",
    "  getPrototypeOf(async function () {}), getPrototypeOf(async function* () {}),",
    "  getPrototypeOf(getPrototypeOf((function* () {})())),",
    "  getPrototypeOf(getPrototypeOf((async function* () {})()))];",
    "// The global object and its prototype chain (the runtime keeps `fetch`, `structuredClone`",
    "// and more on the global scope's prototype): each key becomes an own fixed binding.",
    "const SCOPES = [];",
    "for (let at = globalThis; at !== null && at !== Object.prototype; at = getPrototypeOf(at)) {",
    "  SCOPES.push(at);",
    "}",
    "const GETTERS = [];",
    "for (const key of new Set(SCOPES.flatMap(ownKeys))) {",
    "  const held = getOwnPropertyDescriptor(globalThis, key);",
    "  let value;",
    "  try {",
    "    value = held === undefined ? globalThis[key]",
    "      : held.get !== undefined ? held.get.call(globalThis) : held.value;",
    "  } catch {",
    "    value = undefined;",
    "  }",
    "  if (reference(value) && typeof value !== 'function') {",
    "    // A host object's own state behind its prototype's getters: `crypto.subtle`, ...",
    "    for (let at = getPrototypeOf(value); at !== null; at = getPrototypeOf(at)) {",
    "      for (const name of ownKeys(at)) {",
    "        const getter = getOwnPropertyDescriptor(at, name)?.get;",
    "        if (getter === undefined) continue;",
    "        try { GETTERS.push(getter.call(value)); } catch { /* not this object's */ }",
    "      }",
    "    }",
    "  }",
    "  GETTERS.push(value, held?.get, held?.set);",
    "  if (held === undefined || ('value' in held && held.configurable)) {",
    "    defineProperty(globalThis, key, { value, writable: false, configurable: false,",
    "                                      enumerable: held?.enumerable ?? false });",
    "  } else if (held.configurable) {",
    "    defineProperty(globalThis, key, { configurable: false });",
    "  }",
    "}",
    "harden([...GETTERS, WorkerEntrypoint], [...SHARED, ...SCOPES.slice(1)]);",
  ].join("\n");
}

/** The shim's source over modules named `m0.js`, `m1.js`, …, by their paths in `paths`. */
export function shimSource(paths: readonly string[]): string {
  const imports = paths.map((_path, at) => `import * as m${at} from "./m${at}.js";`);
  const table = paths.map((path, at) => `[${JSON.stringify(path)}]: m${at}`);
  return [
    // The prelude first: it runs before any package's module.
    `import { freezeTable, harden, hasOwn, parse, stringify } from "./prelude.js";`,
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
    // The dispatch path, frozen before any package's hook can run: the class, its
    // prototype and `call`, and what they close over (`said`; the table is frozen above).
    "harden([HookShim, said]);",
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
