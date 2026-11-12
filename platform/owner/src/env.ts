/**
 * The owner Worker's bindings: written by `npm run generate` (`ci/kit/types.ts`)
 * from its `wrangler.jsonc`, each binding to another Worker or to a queue typed by its
 * contract (`contract/services.json`). Edit the config, never this file.
 */
import type { CodemodeWorker } from "../../contract/src/codemode";
import type { ChunkStore } from "../../contract/src/files";
import type { GatewayService } from "../../contract/src/gateway";
import type { OwnerMessage } from "../../contract/src/queues";
import type { SandboxService } from "../../contract/src/sandbox";
import type {
  CodemodeRemoteExchange,
  CodemodeRemoteRequest,
} from "@earendil-works/pi-codemode/portable";

export interface OwnerEnv {
  readonly CONTROL: KVNamespace;
  readonly ERASURES: Queue<OwnerMessage>;
  readonly ROUTING_MAC_KEY: string;
  readonly FILES: ChunkStore;
  readonly GATEWAY: GatewayService;
  readonly CODEMODE_VM: CodemodeWorker<CodemodeRemoteRequest, CodemodeRemoteExchange>;
  readonly SANDBOX: SandboxService;
}
