/**
 * The codemode Worker (`platform/codemode`; ADR-032 §4, narrowed to code mode by ADR-034
 * decision 12): code mode's VMs, out of the owner's object.
 *
 * The owner Worker binds its default entrypoint as `CODEMODE_VM`, and nothing else reaches it:
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

import { type CodemodeWorker, executionName } from "../../contract/src/codemode";
import { Refusal } from "../../contract/src/refusal";

import type { CodemodeEnv } from "./env";

export { CodemodeSandbox } from "./codemode";

export default class Codemode extends WorkerEntrypoint<CodemodeEnv>
    implements CodemodeWorker<CodemodeRemoteRequest, CodemodeRemoteExchange> {
  async codemodeExecute(name: string, request: CodemodeRemoteRequest,
                        exchange: CodemodeRemoteExchange): Promise<void> {
    if (!executionName(name)) {
      throw new Refusal("invalid", "a code mode execution is named");
    }
    await this.env.CODEMODE.getByName(name).execute(request, exchange);
  }
}
