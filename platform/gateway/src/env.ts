/**
 * The gateway Worker's bindings: written by `npm run generate` (`ci/kit/types.ts`)
 * from its `wrangler.jsonc`, each binding to another Worker or to a queue typed by its
 * contract (`contract/services.json`). Edit the config, never this file.
 */

export interface GatewayEnv {
  readonly GATEWAY_KEYRING: string;
}
