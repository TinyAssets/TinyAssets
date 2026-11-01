/**
 * The hooks Worker (`tinyassets-hooks`; ADR-032 §4, agent research §6, pi audit F8): owners'
 * executable hooks and pi's code mode VMs, out of the owner's object. Not deployed: no
 * workflow deploys platform/.
 *
 * The owner Worker binds its default entrypoint as `HOOKS`, and nothing else reaches it:
 * no route, no `workers.dev` or preview URL. It binds only its Worker Loader and the
 * namespaces of its own two classes, so it reaches no owner's object, no store and no
 * other Worker, and holds no owner's ledger or policy (`guards/config.test.ts`).
 *
 * - `run(request)`: one hook call (`protocol.ts`), at the owner's own `HookRunner`
 *   (`getByName(owner)`), which loads the owner's bundle and calls it (`runner.ts`).
 * - `codemodeExecute(name, request, exchange)`: one code mode execution, pi's
 *   `CodemodeRemote.execute`, run by the `CodemodeSandbox` instance of that name
 *   (`codemode.ts`); the owner names one per execution, so scripts never share a thread.
 *   It hands out no stub: the call resolves when the VM has stopped, and nothing it made
 *   outlives it.
 */
import { WorkerEntrypoint } from "cloudflare:workers";
import type {
  CodemodeRemoteExchange, CodemodeRemoteRequest,
} from "@earendil-works/pi-codemode/portable";

import type { CodemodeSandbox } from "./codemode";
import { type HookReply, NAME_MAX } from "./protocol";
import type { HookRunner, RunnerEnv } from "./runner";

export { CodemodeSandbox } from "./codemode";
export { HookRunner } from "./runner";

export interface HooksEnv extends RunnerEnv {
  readonly RUNNER: DurableObjectNamespace<HookRunner>;
  readonly CODEMODE: DurableObjectNamespace<CodemodeSandbox>;
}

function named(value: unknown): value is string {
  return typeof value === "string" && value !== "" && value.length <= NAME_MAX;
}

export class Hooks extends WorkerEntrypoint<HooksEnv> {
  /** One hook call, at the owner's own runner: the reply, never a throw. */
  async run(request: unknown): Promise<HookReply> {
    const owner = typeof request === "object" && request !== null
      ? (request as Record<string, unknown>)["owner"] : undefined;
    if (!named(owner)) {
      return { ok: false, error: "invalid", message: "a hook request names its owner" };
    }
    return this.env.RUNNER.getByName(owner).run(request);
  }

  /**
   * One code mode execution, named by the owner: the VM runs in that sandbox object and
   * every tool call goes back through `exchange`. Resolves once the VM has stopped.
   */
  async codemodeExecute(name: string, request: CodemodeRemoteRequest,
                        exchange: CodemodeRemoteExchange): Promise<void> {
    if (!named(name)) {
      throw new Error("a code mode execution is named");
    }
    await this.env.CODEMODE.getByName(name).execute(request, exchange);
  }
}

export default Hooks;
