// The test-only namespaces of the owner object (`namespaces.ts`, each a class of the test
// Worker, `worker.ts`), the test keyring (the one the gateway Worker run beside the tests
// holds), and the Worker's own bindings: FILES, GATEWAY, HOOKS and SANDBOX, each to its
// Worker run beside it, CONTROL, and its one secret, ROUTING_MAC_KEY.
declare namespace Cloudflare {
  type TestNamespaces = typeof import("./namespaces").NAMESPACES;
  type TestNamespace<Binding extends keyof TestNamespaces> =
    DurableObjectNamespace<InstanceType<typeof import("./worker")[TestNamespaces[Binding]]>>;

  type TestNamespaceBindings = { [Binding in keyof TestNamespaces]: TestNamespace<Binding> };

  interface Env extends TestNamespaceBindings {
    SANDBOX: import("../../contract/src/sandbox").SandboxService;
    /** The gateway's connect entrypoint, test-only: a test seals a doorbell's token as the doors do. */
    CONNECT: import("../../contract/src/gateway").ConnectService;
    FILES: import("../../contract/src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    HOOKS: import("../src/codemode").CodemodeService;
    CONTROL: KVNamespace;
    TEST_KEYRING: string;
    /** The files Worker's ring, test-only: a test wraps a data key under its older kid. */
    FILES_TEST_RING: string;
    /** The files Worker's bucket, test-only: a test tampers a chunk's bytes. */
    BLOBS: R2Bucket;
    ROUTING_MAC_KEY: string;
  }
}
