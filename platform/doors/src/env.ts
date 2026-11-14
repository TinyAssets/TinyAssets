/**
 * The doors Worker's bindings: written by `npm run generate` (`ci/kit/types.ts`)
 * from its `wrangler.jsonc`, each binding to another Worker or to a queue typed by its
 * contract (`contract/services.json`). Edit the config, never this file.
 */
import type { ChunkUpload } from "../../contract/src/files";
import type { ConnectService, EgressService } from "../../contract/src/gateway";
import type { ImportMessage } from "../../contract/src/import-message";
import type { OperatorMessage } from "../../contract/src/operator-message";
import type { OwnerDoors } from "../../contract/src/owner";

export interface DoorsEnv {
  readonly OAUTH_KV: KVNamespace;
  readonly DIRECTORY: KVNamespace;
  readonly CONTROL: KVNamespace;
  readonly IMPORT_STAGING: R2Bucket;
  readonly OPERATOR_LOG: R2Bucket;
  readonly IMPORTS: Queue<ImportMessage>;
  readonly OPERATOR: Queue<OperatorMessage>;
  readonly ASSETS: Fetcher;
  readonly DCR_ENABLED: string;
  readonly OWNER_ISSUER: string;
  readonly FOREIGN_ISSUERS: string;
  readonly UPSTREAM: string;
  readonly ROUTING_MAC_KEY: string;
  readonly WORKOS_API_KEY: string;
  readonly E2B_WEBHOOK_SECRET: string;
  readonly OPERATOR_KEYRING: string;
  readonly OWNER: DurableObjectNamespace<OwnerDoors>;
  readonly GATEWAY: ConnectService;
  readonly EGRESS: EgressService;
  readonly FILES: ChunkUpload;
}
