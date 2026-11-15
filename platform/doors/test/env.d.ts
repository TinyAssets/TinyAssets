// The doors Worker's bindings, as its generated env types them (`src/env.ts`), and the tests'
// own: the owner gate's control, and the fakes' read-backs (vitest.config.ts). OWNER is not
// here: a test reaches it as `doorsEnv.OWNER` (`support.ts`), typed as the doors' env types it,
// since one tsc program merges every workspace's `Cloudflare.Env` and the owner suite's
// declares its own OWNER, its object's class.
declare namespace Cloudflare {
  type DoorsBound = import("../src/env").DoorsEnv;

  interface Env extends Pick<DoorsBound, "OAUTH_KV" | "DIRECTORY" | "CONTROL" | "ASSETS"
                                         | "DCR_ENABLED" | "OWNER_ISSUER" | "FOREIGN_ISSUERS"
                                         | "ROUTING_MAC_KEY"> {
    /** The gate in front of the owner Worker's objects, which OWNER reaches (`ci/gate.ts`). */
    OWNER_GATE: import("../../ci/gate").GateControl;
    /** The fake providers (test/fake-providers.ts), for the tests to read back. */
    PROVIDERS: Fetcher;
    /**
     * The owner Worker's fake sandbox Worker's record, by owner: the owner reaches that
     * Worker as its SANDBOX (`owner/test/fake-sandbox.ts`).
     */
    SANDBOX_LOG: Service<import("../../owner/test/fake-sandbox").Log>;
    /** What the owner Worker sent its erasure queue (`owner/test/fake-erasures.ts`). */
    ERASURES_SENT: Service<import("../../owner/test/fake-erasures").Sent>;
  }
}
