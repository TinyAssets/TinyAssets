import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { control, gate, methodsOf } from "../ci/gate";
import { auxiliary, fake, main, once, pool } from "../ci/workers";

import { NAMESPACES } from "./test/namespaces";

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), made fresh for each run. */
const ROUTING_KEY = randomBytes(32).toString("base64url");
/** The gateway's keyring, made fresh for each run: a rotated ring, so `k1` is an older kid. */
const KEYRING = keyring("k2", ["k1", "k2"]);
/** The files Worker's data keys' ring, made fresh for each run: rotated, `k1` the older kid. */
const FILES_RING = keyring("k2", ["k1", "k2"]);
/** The sandbox Worker the owner's object reaches in these tests: a fake, by its file. */
const FAKE_SANDBOX = join(PLATFORM, "owner", "test", "fake-sandbox.ts");

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd. The owner object's namespaces are bound for the tests only
      // (`test/namespaces.ts`, each a class of the test Worker, `test/worker.ts`):
      // wrangler.jsonc gives the Worker no binding to its own namespace. FILES, GATEWAY and
      // CODEMODE_VM are the config's own bindings, to the real files, gateway and codemode
      // Workers run beside it, each bundled from its own config as a deploy bundles it.
      // SANDBOX is a gate (`ci/gate.ts`) in front of a fake sandbox Worker
      // (`test/fake-sandbox.ts`), which records what each owner's object asked and answers
      // as a test scripts it: a test reads and scripts it through SANDBOX_LOG, and holds a
      // call at the gate through SANDBOX_GATE, both bound to the test Worker alone. The real
      // sandbox Worker is tested against a fake E2B in its own package (`sandbox/test`).
      // TEST_KEYRING is the gateway's keyring, so a test can seal under an older kid, and
      // FILES_TEST_RING the files Worker's, so a test can wrap a data key under its older
      // kid; every fetch of the gateway and the tests reaches the gateway's fake outside
      // world (`gateway/test/fake-upstream.ts`). ROUTING_MAC_KEY is the config's one secret,
      // the sandbox tokens' routing key. The test Worker and each Worker beside it have every
      // binding their configs give them (`main`, `auxiliary`), and a test states only their
      // secrets and what it changes; nothing is read from the host (`pool`).
      // `test/admitted.ts` admits each owner a test names, as sign-up does (slice L).
      project("owner", "workerd", {
        plugins: [
          cloudflareTest(once(async () => pool(main(join(PLATFORM, "owner"), {
              durableObjects: Object.fromEntries(Object.entries(NAMESPACES).map(
                ([binding, className]) => [binding, { className, useSQLite: true }])),
              bindings: { TEST_KEYRING: KEYRING, FILES_TEST_RING: FILES_RING,
                          ROUTING_MAC_KEY: ROUTING_KEY },
              // The files Worker's bucket, test-only: a test changes a chunk's bytes under
              // its key, as storage failing would, and sees a verified read refuse them.
              r2Buckets: ["BLOBS"],
              serviceBindings: { CONNECT: { name: "tinyassets-gateway",
                                            entrypoint: "ConnectGateway" },
                                 SANDBOX: "sandbox-gate", SANDBOX_GATE: control("sandbox-gate"),
                                 SANDBOX_LOG: { name: "fake-sandbox", entrypoint: "Log" } },
              outboundService: "fake-upstream",
              workers: [
                await auxiliary(join(PLATFORM, "files"),
                                { bindings: { FILES_KEYRING: FILES_RING } }),
                await auxiliary(join(PLATFORM, "gateway"),
                                { bindings: { GATEWAY_KEYRING: KEYRING },
                                  outboundService: "fake-upstream" }),
                await auxiliary(join(PLATFORM, "codemode"), {}),
                gate("sandbox-gate", methodsOf(join(PLATFORM, "contract", "src", "sandbox.ts"),
                                                "SandboxService"), { fake: FAKE_SANDBOX }),
                fake(FAKE_SANDBOX),
                fake(join(PLATFORM, "gateway", "test", "fake-upstream.ts")),
              ],
            }, "./test/worker.ts")))),
        ],
        test: { setupFiles: ["./test/admitted.ts", "./test/gates.ts"] },
      }),
      // Static guards over the source and config, in Node.
      project("owner", "guards"),
    ],
  },
});
