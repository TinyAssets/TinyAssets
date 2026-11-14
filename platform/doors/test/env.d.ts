// The doors Worker's bindings, as its generated env types them (`src/env.ts`), but OWNER: the
// test entry's OwnerDO (vitest.config.ts), whose doors outside the contract the tests still
// call, and which the owner's own tests bind as OWNER too.
declare namespace Cloudflare {
  type DoorsBound = import("../src/env").DoorsEnv;

  interface Env extends Pick<DoorsBound, "OAUTH_KV" | "DIRECTORY" | "CONTROL" | "ASSETS"
                                         | "DCR_ENABLED" | "OWNER_ISSUER" | "FOREIGN_ISSUERS"
                                         | "ROUTING_MAC_KEY"> {
    OWNER: DurableObjectNamespace<import("../../owner/src/owner-do").OwnerDO>;
    /** The fake providers (test/fake-providers.ts), for the tests to read back. */
    PROVIDERS: Fetcher;
    /**
     * The owner object's fake sandbox Worker's record, by owner: the object hosted here
     * reaches that Worker as its SANDBOX (`owner/test/fake-sandbox.ts`).
     */
    SANDBOX_LOG: Service<import("../../owner/test/fake-sandbox").Log>;
    /** What the owner object hosted here sent its erasure queue (`owner/test/fake-erasures.ts`). */
    ERASURES_SENT: Service<import("../../owner/test/fake-erasures").Sent>;
  }
}
