// The test-only OWNER binding (vitest.config.ts). The Worker itself binds nothing.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
  }
}
