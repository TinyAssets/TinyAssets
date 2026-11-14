// The test-only namespaces of the owner object (`namespaces.ts`, each a class of the test
// Worker, `worker.ts`), the test keyring (the one the gateway Worker run beside the tests
// holds), and the Worker's own bindings: FILES, GATEWAY and CODEMODE_VM, each to its Worker
// run beside it (FILES and GATEWAY through a gate each), SANDBOX to the gate in front of the fake
// sandbox Worker, CONTROL, and its one secret, ROUTING_MAC_KEY; and the test's handles on the
// fake sandbox and the gates.
declare namespace Cloudflare {
  type TestNamespaces = typeof import("./namespaces").NAMESPACES;
  type TestNamespace<Binding extends keyof TestNamespaces> =
    DurableObjectNamespace<InstanceType<typeof import("./worker")[TestNamespaces[Binding]]>>;

  type TestNamespaceBindings = { [Binding in keyof TestNamespaces]: TestNamespace<Binding> };
  /** The owner Worker's own bindings, as its generated env types them (`src/env.ts`). */
  type OwnerBound = Pick<import("../src/env").OwnerEnv, "SANDBOX" | "FILES" | "GATEWAY"
                                                       | "CODEMODE_VM" | "CONTROL" | "ROUTING_MAC_KEY">;

  interface Env extends TestNamespaceBindings, OwnerBound {
    /** The fake sandbox's record and answers, by owner (`fake-sandbox.ts` `Log`). */
    SANDBOX_LOG: Service<import("./fake-sandbox").Log>;
    /** The gates in front of SANDBOX, FILES and GATEWAY (`ci/gate.ts`): a test holds a call there. */
    SANDBOX_GATE: import("../../ci/gate").GateControl;
    FILES_GATE: import("../../ci/gate").GateControl;
    GATEWAY_GATE: import("../../ci/gate").GateControl;
    /** The gateway's connect entrypoint, test-only: a test seals a doorbell's token as the doors do. */
    CONNECT: import("../../contract/src/gateway").ConnectService;
    TEST_KEYRING: string;
    /** The files Worker's ring, test-only: a test wraps a data key under its older kid. */
    FILES_TEST_RING: string;
    /** The files Worker's bucket, test-only: a test tampers a chunk's bytes. */
    BLOBS: R2Bucket;
  }
}
