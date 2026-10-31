// The test-only OWNER, AGENT, MALFORMED and GATED bindings (vitest.config.ts; the last
// three are the test Worker's classes, test/worker.ts), the test keyring (the one the
// gateway Worker run beside the tests holds), and the Worker's three bindings: FILES, to
// the files Worker, and GATEWAY, to the gateway Worker, each run beside it, and PISTORE,
// its own thread stores.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    AGENT: DurableObjectNamespace<import("./worker").FauxOwnerDO>;
    MALFORMED: DurableObjectNamespace<import("./worker").MalformedOwnerDO>;
    GATED: DurableObjectNamespace<import("./worker").GatedOwnerDO>;
    FILES: import("../src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    PISTORE: DurableObjectNamespace<import("../src/thread-store").ThreadStore>;
    TEST_KEYRING: string;
  }
}
