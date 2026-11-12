// The test-only namespaces of the owner object (`namespaces.ts`, each a class of the test
// Worker, `worker.ts`), the test keyring (the one the gateway Worker run beside the tests
// holds), and the Worker's own bindings: FILES, GATEWAY and CODEMODE_VM, each to its Worker
// run beside it, SANDBOX to the gate in front of the fake sandbox Worker, CONTROL, and its one
// secret, ROUTING_MAC_KEY; and the test's handles on the fake sandbox and its gate.
declare namespace Cloudflare {
  type TestNamespaces = typeof import("./namespaces").NAMESPACES;
  type TestNamespace<Binding extends keyof TestNamespaces> =
    DurableObjectNamespace<InstanceType<typeof import("./worker")[TestNamespaces[Binding]]>>;

  type TestNamespaceBindings = { [Binding in keyof TestNamespaces]: TestNamespace<Binding> };

  interface Env extends TestNamespaceBindings {
    SANDBOX: import("../../contract/src/sandbox").SandboxService;
    /** The fake sandbox's record and answers, by owner (`fake-sandbox.ts` `Log`). */
    SANDBOX_LOG: Service<import("./fake-sandbox").Log>;
    /** The gate in front of SANDBOX (`ci/gate.ts`): a test holds a call there. */
    SANDBOX_GATE: import("../../ci/gate").GateControl;
    /** The gateway's connect entrypoint, test-only: a test seals a doorbell's token as the doors do. */
    CONNECT: import("../../contract/src/gateway").ConnectService;
    FILES: import("../../contract/src/files").ChunkStore;
    GATEWAY: Service<import("../../gateway/src/index").default>;
    CODEMODE_VM: import("../src/codemode").CodemodeService;
    CONTROL: KVNamespace;
    TEST_KEYRING: string;
    /** The files Worker's ring, test-only: a test wraps a data key under its older kid. */
    FILES_TEST_RING: string;
    /** The files Worker's bucket, test-only: a test tampers a chunk's bytes. */
    BLOBS: R2Bucket;
    ROUTING_MAC_KEY: string;
  }
}
