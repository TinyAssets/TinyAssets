/**
 * The codemode Worker's bindings: written by `npm run generate` (`ci/kit/types.ts`)
 * from its `wrangler.jsonc`, each binding to another Worker or to a queue typed by its
 * contract (`contract/services.json`). Edit the config, never this file.
 */
import type { CodemodeSandbox } from "./index";

export interface CodemodeEnv {
  readonly CODEMODE: DurableObjectNamespace<CodemodeSandbox>;
}
