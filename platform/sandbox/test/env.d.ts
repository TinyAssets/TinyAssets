// The sandbox Worker's bindings, as its generated env types them (`src/env.ts`), and the
// tests' own (vitest.config.ts): the fake E2B they script, the gate in front of the line, and
// a line of the test Worker's own.
declare namespace Cloudflare {
  type SandboxBound = import("../src/env").SandboxEnv;

  interface Env extends Pick<SandboxBound, "SANDBOXES" | "SLOTS" | "CONTROL" | "RING"
                                           | "E2B_API_KEY" | "E2B_TEMPLATE"> {
    E2B: Fetcher;
    SLOTS_GATE: import("../../ci/gate").GateControl;
    LINE: DurableObjectNamespace<import("../src/slots").SandboxSlots>;
  }
}
