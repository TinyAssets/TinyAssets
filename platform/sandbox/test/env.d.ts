// The sandbox Worker's bindings, as its wrangler.jsonc declares them, and the fake E2B the
// tests reach to script it (vitest.config.ts).
declare namespace Cloudflare {
  interface Env {
    SANDBOXES: DurableObjectNamespace<import("../src/control").SandboxControl>;
    SLOTS: DurableObjectNamespace<import("../src/slots").SandboxSlots>;
    CONTROL: KVNamespace;
    RING: Queue<{ owner: string }>;
    E2B_API_KEY: string;
    E2B_TEMPLATE: string;
    E2B: Fetcher;
  }
}
