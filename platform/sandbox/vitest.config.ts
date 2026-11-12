import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { project } from "../ci/project";
import { configBindings, fake, main, once, pool, workerConfig } from "../ci/workers";

import { PROTOCOL } from "./runner/protocol";
import { PROTOCOLS } from "./runner/test/state-shape";

const SANDBOX = dirname(fileURLToPath(import.meta.url));
/** Builds the runner the Worker ships before the tests that need it (`runner-build.ts`). */
const RUNNER_BUILD = join(SANDBOX, "runner-build.ts");
/** The ring queue the Worker's `RING` produces to, as its config names it. */
const RING = (configBindings(workerConfig(SANDBOX))["queueProducers"] as
  Record<string, { queueName: string }>)["RING"]!.queueName;

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd, from the config's own entry and bindings (`main`): its classes, the
      // control record, the ring and its template var; its key is the tests', and none is
      // read from the host. Every global fetch the Worker makes reaches the fake E2B
      // (`test/fake-e2b.ts`: E2B's two hosts, in tests only), which the tests also reach
      // (`E2B`) to script it and read back what it received.
      project("sandbox", "workerd", {
        plugins: [
          cloudflareTest(once(() => pool(main(SANDBOX, {
            bindings: { E2B_API_KEY: "e2b_test_key_0000" },
            serviceBindings: { E2B: "fake-e2b" },
            outboundService: "fake-e2b",
            // The ring queue's consumer in tests: each ring recorded at once. The fake's loader
            // and runner answer by the runner protocol's record (`PROTOCOL`).
            workers: [fake(join(SANDBOX, "test", "fake-e2b.ts"), {
              bindings: { PROTOCOL: PROTOCOLS[String(PROTOCOL)]! },
              queueConsumers: { [RING]: { maxBatchSize: 1, maxBatchTimeout: 0 } } })],
          })))),
        ],
        test: { globalSetup: [RUNNER_BUILD] },
      }),
      // The runner, in Node, against fake CLIs and a fake doors server.
      project("sandbox", "runner"),
      // Static guards over the source and config, in Node.
      project("sandbox", "guards", { test: { globalSetup: [RUNNER_BUILD] } }),
    ],
  },
});
