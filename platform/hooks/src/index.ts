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
 * - `codemode(name)`: the `Codemode` remote of one code mode execution, whose
 *   `execute(request, exchange)` is pi's `CodemodeRemote`, served by the
 *   `CodemodeSandbox` instance of that name (`codemode.ts`); the owner names one per
 *   execution, so scripts never share a thread.
 */
import { RpcTarget, WorkerEntrypoint } from "cloudflare:workers";
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

/** One code mode execution's remote: pi's `CodemodeRemote`, over its sandbox object. */
export class Codemode extends RpcTarget {
  readonly #sandbox: DurableObjectStub<CodemodeSandbox>;

  constructor(sandbox: DurableObjectStub<CodemodeSandbox>) {
    super();
    this.#sandbox = sandbox;
  }

  /** Run one script's VM; every tool call goes back through `exchange`. Never rejects. */
  execute(request: CodemodeRemoteRequest, exchange: CodemodeRemoteExchange): Promise<void> {
    return this.#sandbox.execute(request, exchange);
  }
}

export class Hooks extends WorkerEntrypoint<HooksEnv> {
  /** One hook call, at the owner's own runner: the reply, never a throw. */
  async run(request: unknown): Promise<HookReply> {
    const owner = typeof request === "object" && request !== null
      ? (request as Record<string, unknown>)["owner"] : undefined;
    if (typeof owner !== "string" || owner === "" || owner.length > NAME_MAX) {
      return { ok: false, error: "invalid", message: "a hook request names its owner" };
    }
    return this.env.RUNNER.getByName(owner).run(request);
  }

  /** The remote of one code mode execution, named by the owner. */
  codemode(name: string): Codemode {
    if (typeof name !== "string" || name === "" || name.length > NAME_MAX) {
      throw new Error("a code mode execution is named");
    }
    return new Codemode(this.env.CODEMODE.getByName(name));
  }
}

export default Hooks;
