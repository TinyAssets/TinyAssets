// The doors Worker's bindings, as its wrangler.jsonc declares them (OWNER bound to the
// test entry's OwnerDO by vitest.config.ts).
declare namespace Cloudflare {
  interface Env {
    OWNER: DurableObjectNamespace<import("../../owner/src/owner-do").OwnerDO>;
    OAUTH_KV: KVNamespace;
    DIRECTORY: KVNamespace;
    DCR_ENABLED: string;
    OWNER_ISSUER: string;
    FOREIGN_ISSUERS: string;
    GATEWAY: import("../src/config").ConnectService;
    EGRESS: import("../../owner/src/network").EgressService;
    /** The one secret, the sandbox tokens' routing key; the owner Worker's too. */
    ROUTING_MAC_KEY: string;
    /** The fake providers (test/fake-providers.ts), for the tests to read back. */
    PROVIDERS: Fetcher;
  }
}
