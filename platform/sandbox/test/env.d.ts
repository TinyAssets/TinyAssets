// The sandbox Worker's bindings, as its generated env types them (`src/env.ts`), and the fake
// E2B the tests reach to script it (vitest.config.ts).
declare namespace Cloudflare {
  type SandboxBound = import("../src/env").SandboxEnv;

  interface Env extends Pick<SandboxBound, "SANDBOXES" | "SLOTS" | "CONTROL" | "RING"
                                           | "E2B_API_KEY" | "E2B_TEMPLATE"> {
    E2B: Fetcher;
  }
}
