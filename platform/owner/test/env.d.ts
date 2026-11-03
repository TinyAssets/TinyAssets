// The test-only OWNER, AGENT, GATED, BOUNDED, SWITCHED, CACHED, BARE and RUNG bindings
// (vitest.config.ts; the last seven are the test Worker's classes, test/worker.ts), the test
// keyring (the one the gateway Worker run
// beside the tests holds), and the Worker's three bindings: FILES, to the files Worker,
// GATEWAY, to the gateway Worker, and HOOKS, to the hooks Worker, each run beside it; and
// its one secret, ROUTING_MAC_KEY.
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    AGENT: DurableObjectNamespace<import("./worker").FauxOwnerDO>;
    GATED: DurableObjectNamespace<import("./worker").GatedOwnerDO>;
    BARE: DurableObjectNamespace<import("./worker").UnboundOwnerDO>;
    BOUNDED: DurableObjectNamespace<import("./worker").BoundedOwnerDO>;
    SWITCHED: DurableObjectNamespace<import("./worker").SwitchedOwnerDO>;
    CACHED: DurableObjectNamespace<import("./worker").CachedOwnerDO>;
    SANDBOX: import("../src/sandbox").SandboxService;
    RUNG: DurableObjectNamespace<import("./worker").RungOwnerDO>;
    /** The owner object exactly as production builds it, starter package and all. */
    SEEDED: DurableObjectNamespace<import("./worker").SeededOwnerDO>;
    /** The gateway's connect entrypoint, test-only: a test seals a doorbell's token as the doors do. */
    CONNECT: import("../../doors/src/config").ConnectService;
    FILES: import("../src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    HOOKS: import("../src/codemode").HooksService;
    CONTROL: KVNamespace;
    TEST_KEYRING: string;
    ROUTING_MAC_KEY: string;
  }
}
