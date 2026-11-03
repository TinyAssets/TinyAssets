// The test-only OWNER, AGENT, GATED, BOUNDED, SWITCHED and CACHED bindings
// (vitest.config.ts; the last five are the test Worker's classes, test/worker.ts), the test
// keyring (the one the gateway Worker run
// beside the tests holds), and the Worker's three bindings: FILES, to the files Worker,
// GATEWAY, to the gateway Worker, and HOOKS, to the hooks Worker, each run beside it; and
// its one secret, ROUTING_MAC_KEY.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    AGENT: DurableObjectNamespace<import("./worker").FauxOwnerDO>;
    GATED: DurableObjectNamespace<import("./worker").GatedOwnerDO>;
    BOUNDED: DurableObjectNamespace<import("./worker").BoundedOwnerDO>;
    SWITCHED: DurableObjectNamespace<import("./worker").SwitchedOwnerDO>;
    CACHED: DurableObjectNamespace<import("./worker").CachedOwnerDO>;
    FILES: import("../src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    HOOKS: import("../src/codemode").HooksService;
    CONTROL: KVNamespace;
    TEST_KEYRING: string;
    ROUTING_MAC_KEY: string;
  }
}
