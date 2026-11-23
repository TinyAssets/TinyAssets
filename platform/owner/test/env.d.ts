// The owner object's namespace, bound for the tests only (the real class, the config's
// `main`), the gateway and files Workers with their rings' older kid active and the gateway
// once that kid is retired, and the Worker's own bindings: FILES, GATEWAY and CODEMODE_VM,
// each to its Worker run beside it (FILES and GATEWAY through a gate each), SANDBOX to the
// gate in front of the fake sandbox Worker, CONTROL, ERASURES (consumed by the fake erasure
// Worker), and its one secret, ROUTING_KEYRING; and the test's handles on the fakes and the
// gates.
declare namespace Cloudflare {
  /** The owner Worker's own bindings, as its generated env types them (`src/env.ts`). */
  type OwnerBound = Pick<import("../src/env").OwnerEnv, "SANDBOX" | "FILES" | "GATEWAY"
                                                       | "CODEMODE_VM" | "CONTROL" | "ERASURES"
                                                       | "ROUTING_KEYRING">;

  interface Env extends OwnerBound {
    /** The owner's objects, of the Worker's own class (`src/index.ts`). */
    OWNER: DurableObjectNamespace<import("../src/owner-do").OwnerDO>;
    /** The fake sandbox's record and answers, by owner (`ci/fake-sandbox.ts` `Log`). */
    SANDBOX_LOG: Service<import("../../ci/fake-sandbox").Log>;
    /** What each owner's object sent the erasure queue (`fake-erasures.ts` `Sent`). */
    ERASURES_SENT: Service<import("./fake-erasures").Sent>;
    /** The gates in front of SANDBOX, FILES and GATEWAY (`ci/gate.ts`): a test holds a call there. */
    SANDBOX_GATE: import("../../ci/gate").GateControl;
    FILES_GATE: import("../../ci/gate").GateControl;
    GATEWAY_GATE: import("../../ci/gate").GateControl;
    /** The gateway's connect entrypoint, test-only: a test seals a doorbell's token as the doors do. */
    CONNECT: import("../../contract/src/gateway").ConnectService;
    /** The gateway's connect steps on its ring with the older kid `k1` active. */
    CONNECT_OLDER: import("../../contract/src/gateway").ConnectService;
    /** The gateway once `k1` is retired from its ring: what still opens without it. */
    GATEWAY_RETIRED: import("../../contract/src/gateway").GatewayService;
    /** The files Worker on its ring with the older kid `k1` active: a key wrapped under it. */
    FILES_OLDER: import("../../contract/src/files").ChunkStore;
    /** The files Worker's bucket, test-only: a test copies, deletes or plants its objects. */
    BLOBS: R2Bucket;
  }
}
