// The gateway Worker's one binding, as its generated env types it (`src/env.ts`; the value is
// the tests', from vitest.config.ts).
declare namespace Cloudflare {
  interface Env extends Pick<import("../src/env").GatewayEnv, "GATEWAY_KEYRING"> {}
}
