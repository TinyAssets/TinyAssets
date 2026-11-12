import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { control, gate, methodsOf } from "../ci/gate";
import { project } from "../ci/project";
import {
  auxiliary, configBindings, fake, fakeConfig, main, once, pool, workerConfig,
} from "../ci/workers";

import { PROTOCOL } from "./runner/protocol";
import { PROTOCOLS } from "./runner/test/state-shape";

const SANDBOX = dirname(fileURLToPath(import.meta.url));
/** Builds the runner the Worker ships before the tests that need it (`runner-build.ts`). */
const RUNNER_BUILD = join(SANDBOX, "runner-build.ts");
/** The ring queue the Worker's `RING` produces to, as its config names it. */
const RING = (configBindings(workerConfig(SANDBOX))["queueProducers"] as
  Record<string, { queueName: string }>)["RING"]!.queueName;
/** The fake E2B, E2B's two hosts in tests. */
const FAKE_E2B = join(SANDBOX, "test", "fake-e2b.ts");
/** The tests' E2B key, the Worker's one secret: never the host's. */
const E2B_API_KEY = "e2b_test_key_0000";
/** The gate in front of the line (`SLOTS`). */
const SLOTS_GATE = "slots-gate";

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd, from the config's own entry and bindings (`main`): its classes, the
      // control record, the ring and its template var; its key is the tests', and none is
      // read from the host. Every global fetch the Worker makes reaches the fake E2B
      // (`test/fake-e2b.ts`: E2B's two hosts, in tests only), which the tests also reach
      // (`E2B`) to script it and read back what it received. SLOTS is a gate (`ci/gate.ts`)
      // in front of the line of the real sandbox Worker, run beside the test's
      // (`auxiliary`) with the same key and fake, so the control reaches the line across a
      // Worker, as the kit's own suite reaches the owner's objects: a test refuses or holds a
      // call to it through SLOTS_GATE, bound to the test Worker alone. LINE, bound for the
      // tests only, is a line of the test Worker's own, for the tests of the line alone (its
      // back-off alarm, its schema).
      project("sandbox", "workerd", {
        plugins: [
          cloudflareTest(once(() => pool(main(SANDBOX, {
            bindings: { E2B_API_KEY },
            durableObjects: {
              SLOTS: { className: "SandboxSlots", scriptName: SLOTS_GATE, useSQLite: true },
              LINE: { className: "SandboxSlots", useSQLite: true },
            },
            serviceBindings: { E2B: fakeConfig(FAKE_E2B).name, SLOTS_GATE: control(SLOTS_GATE) },
            outboundService: fakeConfig(FAKE_E2B).name,
            workers: [
              auxiliary(SANDBOX, { bindings: { E2B_API_KEY },
                                   outboundService: fakeConfig(FAKE_E2B).name }),
              gate(SLOTS_GATE, methodsOf(join(SANDBOX, "src", "slots.ts"), "SlotLine"),
                   { worker: SANDBOX, className: "SandboxSlots" }),
              // The ring queue's consumer in tests: each ring recorded at once. The fake's
              // loader and runner answer by the runner protocol's record (`PROTOCOL`).
              fake(FAKE_E2B, {
                bindings: { PROTOCOL: PROTOCOLS[String(PROTOCOL)]! },
                queueConsumers: { [RING]: { maxBatchSize: 1, maxBatchTimeout: 0 } } }),
            ],
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
