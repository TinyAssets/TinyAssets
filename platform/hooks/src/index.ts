/**
 * The hooks Worker (`tinyassets-hooks`; ADR-032 §4, pi audit F8, narrowed to code mode by
 * ADR-034 decision 12): code mode's VMs, out of the owner's object. Not deployed: no
 * workflow deploys platform/.
 *
 * The owner Worker binds its default entrypoint as `HOOKS`, and nothing else reaches it:
 * no route, no `workers.dev` or preview URL. It binds only the namespace of its own class,
 * so it reaches no owner's object, no store and no other Worker, and holds no owner's
 * ledger or policy (`guards/config.test.ts`).
 *
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

export { CodemodeSandbox } from "./codemode";

/** The most characters an execution's name takes: the owner's name, a slash and a UUID. */
const NAME_MAX = 512;

export interface HooksEnv {
  readonly CODEMODE: DurableObjectNamespace<CodemodeSandbox>;
}

function named(value: unknown): value is string {
  return typeof value === "string" && value !== "" && value.length <= NAME_MAX;
}

export class Hooks extends WorkerEntrypoint<HooksEnv> {
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
