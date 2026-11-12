/**
 * The sandbox Worker's bindings: written by `npm run generate` (`ci/kit/types.ts`)
 * from its `wrangler.jsonc`, each binding to another Worker or to a queue typed by its
 * contract (`contract/services.json`). Edit the config, never this file.
 */
import type { OwnerMessage } from "../../contract/src/queues";
import type { SandboxControl, SandboxSlots } from "./index";

export interface SandboxEnv {
  readonly CONTROL: KVNamespace;
  readonly RING: Queue<OwnerMessage>;
  readonly E2B_TEMPLATE: string;
  readonly E2B_API_KEY: string;
  readonly SANDBOXES: DurableObjectNamespace<SandboxControl>;
  readonly SLOTS: DurableObjectNamespace<SandboxSlots>;
}
