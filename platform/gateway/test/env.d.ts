// The gateway Worker's one binding, as its wrangler.jsonc declares it (the value is the
// tests', from vitest.config.ts).
declare namespace Cloudflare {
  interface Env {
    GATEWAY_KEYRING: string;
  }
}
