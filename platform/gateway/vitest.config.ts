import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { fake, main, once, pool } from "../ci/workers";

/** A keyring made fresh for each run: a rotated ring, so `k1` is an older kid. */
const KEYRING = keyring("k2", ["k1", "k2"]);

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd, under the config's own entry, runtime and flags (`main`). The
      // keyring is the tests' (the config declares the secret and holds no value, and none
      // is read from the host), and every global fetch goes to the fake outside world
      // (`test/fake-upstream.ts`).
      project("gateway", "workerd", {
        plugins: [
          cloudflareTest(once(async () => pool(main(join(PLATFORM, "gateway"), {
            bindings: { GATEWAY_KEYRING: KEYRING },
            outboundService: "fake-upstream",
            workers: [fake(join(PLATFORM, "gateway", "test", "fake-upstream.ts"))],
          })))),
        ],
      }),
      // Static guards over the source and config, in Node.
      project("gateway", "guards"),
    ],
  },
});
