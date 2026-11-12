/**
 * The sandbox Worker (`tinyassets-sandbox`; ADR-034 decision 4.2, `engines-replan.md` §2.1
 * step 3): the only holder of the E2B API key, and what stands behind the owner object's
 * `SANDBOX` interface (`contract/src/sandbox.ts`). Not deployed: no workflow deploys
 * platform/.
 *
 * The owner Worker binds its default entrypoint as `SANDBOX`, and nothing else reaches it:
 * no route, no `workers.dev` or preview URL. It reaches nothing but E2B (`e2b.ts`, the one
 * module that fetches, held to E2B's two hosts by `guards/config.test.ts`) and the ring
 * queue; it reads one record (the control record's sandbox numbers) and binds the
 * namespaces of its own two classes: `SandboxControl`, one per owner, which serializes that
 * owner's E2B calls and renews its sandboxes before their runtime ceiling (`control.ts`),
 * and `SandboxSlots`, the platform's one line for one more running sandbox, whose doorbell
 * is the ring queue (`slots.ts`). The queue's consumer is the doors Worker, which rings
 * only the owner a message names, and a ring only starts that owner's waiting runs again
 * through their whole admission. It binds no `OWNER`, no other Worker and no bucket, so it
 * reaches no owner's object.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { principal } from "../../contract/src/text";
import type {
  MeterEvent, SandboxService, SandboxSignin, SandboxStart, Started,
} from "../../contract/src/sandbox";

import type { SandboxControl, SandboxEnv } from "./control";
import { SLOTS } from "./slots";

export { SandboxControl } from "./control";
export { SandboxSlots } from "./slots";

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
    return this.#owner(owner).cancel(request);
  }

  async status(owner: string, request: { run: string; lease_id: string }):
      Promise<{ state: "running" | "exited" | "gone" }> {
    return this.#owner(owner).status(request);
  }

  async release(owner: string, request: { lease_id: string }): Promise<{ paused: boolean }> {
    return this.#owner(owner).release(request);
  }

  async signin(owner: string, signin: SandboxSignin): Promise<Started> {
    return this.#owner(owner).signin(owner, signin);
  }

  async signinInput(owner: string, request: { authority: string; id: string; code: string }):
      Promise<{ delivered: boolean }> {
    return this.#owner(owner).signinInput(request);
  }

  async signinCancel(owner: string, request: { authority: string; id: string }):
      Promise<{ cancelled: boolean }> {
    return this.#owner(owner).signinCancel(request);
  }

  async unwait(owner: string): Promise<void> {
    await this.env.SLOTS.getByName(SLOTS).leave(principal(owner));
  }

  async erase(owner: string): Promise<{ killed: number }> {
    return this.#owner(owner).erase(principal(owner));
  }

  async meter(owner: string, request: { after: number }):
      Promise<{ events: MeterEvent[]; next: number }> {
    return this.#owner(owner).meter(request);
  }
}

export default Sandbox;
