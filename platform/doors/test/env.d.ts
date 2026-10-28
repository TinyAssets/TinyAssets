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
  }
}
