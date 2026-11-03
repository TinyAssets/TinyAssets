/**
 * The sandbox Worker (`tinyassets-sandbox`; ADR-034 decision 4.2, `engines-replan.md` §2.1
 * step 3): the only holder of the E2B API key, and what stands behind the owner object's
 * `SANDBOX` interface (`owner/src/sandbox.ts`). Not deployed: no workflow deploys
 * platform/.
 *
 * The owner Worker binds its default entrypoint as `SANDBOX`, and nothing else reaches it:
 * no route, no `workers.dev` or preview URL. It reaches nothing but E2B (`e2b.ts`, the one
 * module that fetches, held to E2B's two hosts by `guards/config.test.ts`), reads one
 * record of the platform's directory (the control record's sandbox numbers) and binds the
 * namespace of its own class, `SandboxControl`, one per owner, which serializes that
 * owner's E2B calls and renews its sandboxes before their runtime ceiling (`control.ts`).
 * It binds no `OWNER`, no other Worker and no bucket, so it reaches no owner's object.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { principal } from "../../owner/src/text";
import type { SandboxService, SandboxStart, Started } from "../../owner/src/sandbox";

import type { SandboxControl, SandboxEnv } from "./control";

export { SandboxControl } from "./control";

export interface Env extends SandboxEnv {
  readonly SANDBOXES: DurableObjectNamespace<SandboxControl>;
}

export class Sandbox extends WorkerEntrypoint<Env> implements SandboxService {
  #owner(owner: string): DurableObjectStub<SandboxControl> {
    return this.env.SANDBOXES.getByName(principal(owner));
  }

  async start(owner: string, start: SandboxStart): Promise<Started> {
    return this.#owner(owner).start(owner, start);
  }

  async cancel(owner: string, request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    return this.#owner(owner).cancel(owner, request);
  }

  async status(owner: string, request: { run: string; lease_id: string }):
      Promise<{ state: "running" | "exited" | "gone" }> {
    return this.#owner(owner).status(owner, request);
  }

  async release(owner: string, request: { lease_id: string }): Promise<{ paused: boolean }> {
    return this.#owner(owner).release(owner, request);
  }
}

export default Sandbox;
