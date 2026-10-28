// The test-only OWNER, AGENT and MALFORMED bindings (vitest.config.ts; the last two are
// the test Worker's classes, test/worker.ts), and the Worker's one binding, FILES, to the
// files Worker run beside it.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    AGENT: DurableObjectNamespace<import("./worker").FauxOwnerDO>;
    MALFORMED: DurableObjectNamespace<import("./worker").MalformedOwnerDO>;
    FILES: import("../src/files").ChunkStore;
  }
}
