// The test-only OWNER, AGENT, GATED, BOUNDED and RUNG bindings (vitest.config.ts; the last four
// are the test Worker's classes, test/worker.ts), the test keyring (the one the gateway Worker run
// beside the tests holds), the directory the doorbell Worker reads (DIRECTORY, test-only), and
// the Worker's four bindings: FILES, to the files Worker, GATEWAY, to the gateway Worker, HOOKS,
// to the hooks Worker, and DOORBELL, to the doorbell Worker, each run beside it; and its one
// secret, ROUTING_MAC_KEY.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    AGENT: DurableObjectNamespace<import("./worker").FauxOwnerDO>;
    GATED: DurableObjectNamespace<import("./worker").GatedOwnerDO>;
    BOUNDED: DurableObjectNamespace<import("./worker").BoundedOwnerDO>;
    RUNG: DurableObjectNamespace<import("./worker").RungOwnerDO>;
    DOORBELL: import("../src/wakes").DoorbellService;
    DIRECTORY: KVNamespace;
    FILES: import("../src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    HOOKS: import("../src/codemode").HooksService;
    TEST_KEYRING: string;
    ROUTING_MAC_KEY: string;
  }
}
