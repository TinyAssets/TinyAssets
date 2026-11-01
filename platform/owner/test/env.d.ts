// The test-only OWNER, AGENT, MALFORMED and GATED bindings (vitest.config.ts; the last
// three are the test Worker's classes, test/worker.ts), the test keyring (the one the
// gateway Worker run beside the tests holds), and the Worker's four bindings: FILES, to
// the files Worker, GATEWAY, to the gateway Worker, and HOOKS, to the hooks Worker, each run
// beside it, and PISTORE, its own thread stores; and its one secret, ROUTING_MAC_KEY.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    AGENT: DurableObjectNamespace<import("./worker").FauxOwnerDO>;
    MALFORMED: DurableObjectNamespace<import("./worker").MalformedOwnerDO>;
    GATED: DurableObjectNamespace<import("./worker").GatedOwnerDO>;
    FILES: import("../src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    HOOKS: import("../src/hooks").HooksService;
    PISTORE: DurableObjectNamespace<import("../src/thread-store").ThreadStore>;
    TEST_KEYRING: string;
    ROUTING_MAC_KEY: string;
  }
}
