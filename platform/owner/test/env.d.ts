// The test-only OWNER binding (vitest.config.ts), and the Worker's one binding, FILES, to
// the files Worker run beside it.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    FILES: import("../src/files").ChunkStore;
  }
}
