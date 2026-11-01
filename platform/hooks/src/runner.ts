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
 * - our trusted shim (`shimSource`) as its main module, which imports the owner's modules,
 *   dispatches the point to the module's export of that name, takes the input as JSON,
 *   holds the reply to JSON within `JSON_MAX`, and catches what the hook throws.
 *
 * A call past `deadline_ms` answers `timeout` here; the isolate may compute on until its
 * `cpuMs`, which costs this object, never the owner's (workerd enforces no CPU limit:
 * owed to H2's live probe).
 */
import { DurableObject } from "cloudflare:workers";

import {
  bundleId, CPU_MAX, type HookReply, type HookRequest, hookRequest, InvalidRequest, JSON_MAX,
} from "./protocol";

/** The owner Worker's compatibility date, which every hook bundle runs at. */
export const COMPATIBILITY_DATE = "2026-10-23";
/** The shim's version, in every loader name: a changed shim is a new Worker. */
export const SHIM_VERSION = 1;
/** The bundles one runner keeps in memory, newest last. */
export const CODES_KEPT = 4;

export interface RunnerEnv {
  readonly LOADER: WorkerLoader;
}

/** The loader name of `owner`'s bundle `id`: the owner, the id and the shim's version. */
export function loaderName(owner: string, id: string): string {
  return `${owner}:${id}:shim${SHIM_VERSION}`;
}

/** The shim's source over modules named `m0.js`, `m1.js`, …, by their paths in `paths`. */
export function shimSource(paths: readonly string[]): string {
  const imports = paths.map((_path, at) => `import * as m${at} from "./m${at}.js";`);
  const table = paths.map((path, at) => `[${JSON.stringify(path)}, m${at}]`);
  return [
    `import { WorkerEntrypoint } from "cloudflare:workers";`,
    ...imports,
    `const MODULES = new Map([${table.join(", ")}]);`,
    `const JSON_MAX = ${JSON_MAX};`,
    "function said(error) {",
    "  try { return String(error && error.message !== undefined ? error.message : error).slice(0, 500); }",
    "  catch { return \"the hook threw\"; }",
    "}",
    "export class HookShim extends WorkerEntrypoint {",
    "  async call(module, point, input, context) {",
    "    const found = MODULES.get(module);",
    "    const hook = found === undefined ? undefined : found[point];",
    "    if (typeof hook !== \"function\") {",
    "      return { ok: false, error: \"invalid\", message: `the module exports no ${point}` };",
    "    }",
    "    let result;",
    "    try {",
    "      result = await hook(JSON.parse(input), JSON.parse(context));",
    "    } catch (error) {",
    "      return { ok: false, error: \"threw\", message: said(error) };",
    "    }",
    "    let text;",
    "    try {",
    "      text = JSON.stringify(result === undefined ? null : result);",
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

/** The Worker a bundle loads as: the shim over the owner's modules, and no way out. */
export function workerCode(modules: Readonly<Record<string, string>>): WorkerLoaderWorkerCode {
  const paths = Object.keys(modules).sort();
  const code: Record<string, string> = { "shim.js": shimSource(paths) };
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

/** `run`'s answer past `ms`, or `"late"`. */
async function inTime<T>(run: Promise<T>, ms: number): Promise<T | "late"> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<"late">((resolve) => {
    timer = setTimeout(() => resolve("late"), ms);
  });
  try {
    return await Promise.race([run, late]);
  } finally {
    clearTimeout(timer);
  }
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

export class HookRunner extends DurableObject<RunnerEnv> {
  /** The bundles this instance was sent, by loader name, oldest first. */
  readonly #codes = new Map<string, Readonly<Record<string, string>>>();

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
    const worker = this.env.LOADER.get(name, () => workerCode(modules));
    const shim = worker.getEntrypoint("HookShim", {
      limits: { cpuMs: request.cpu_ms, subRequests: 0 } }) as unknown as {
      call(module: string, point: string, input: string, context: string): Promise<unknown>;
    };
    try {
      const answer = await inTime(shim.call(request.module, request.point,
                                            JSON.stringify(request.input),
                                            JSON.stringify(request.context)),
                                  request.deadline_ms);
      return answer === "late" ? { ok: false, error: "timeout" } : shimReply(answer);
    } catch (error) {
      if (error instanceof InvalidRequest) {
        return { ok: false, error: "invalid", message: said(error) };
      }
      // The isolate failed: a module that does not load, a CPU limit, a crash.
      return { ok: false, error: "threw", message: said(error) };
    }
  }
}
