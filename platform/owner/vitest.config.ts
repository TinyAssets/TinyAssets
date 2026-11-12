import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { auxiliary, fake, main, once, pool } from "../ci/workers";

import { NAMESPACES } from "./test/namespaces";

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), made fresh for each run. */
const ROUTING_KEY = randomBytes(32).toString("base64url");
/** The gateway's keyring, made fresh for each run: a rotated ring, so `k1` is an older kid. */
const KEYRING = keyring("k2", ["k1", "k2"]);
/** The files Worker's data keys' ring, made fresh for each run: rotated, `k1` the older kid. */
const FILES_RING = keyring("k2", ["k1", "k2"]);

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd. The owner object's namespaces are bound for the tests only
      // (`test/namespaces.ts`, each a class of the test Worker, `test/worker.ts`):
      // wrangler.jsonc gives the Worker no binding to its own namespace. FILES, GATEWAY,
      // HOOKS and SANDBOX are the config's own bindings, to the real files, gateway, hooks
      // and sandbox Workers run beside it, each bundled from its own config as a deploy
      // bundles it. TEST_KEYRING is the gateway's keyring, so a test can seal under an older
      // kid, and FILES_TEST_RING the files Worker's, so a test can wrap a data key under its
      // older kid; every fetch of the gateway, the sandbox and the tests reaches the gateway's
      // fake outside world (`gateway/test/fake-upstream.ts`). ROUTING_MAC_KEY is the
      // config's one secret, the sandbox tokens' routing key. The test Worker and each
      // Worker beside it have every binding their configs give them (`main`, `auxiliary`),
      // and a test states only their secrets and what it changes; nothing is read from the
      // host (`pool`). The sandbox Worker's CONTROL is the config's namespace, the
      // owner's too, as at deploy. The tests write control records there, and none holds
      // sandbox numbers, so it starts nothing (`sandbox_unconfigured`); its key reaches no
      // E2B (its fetches reach the fake outside world): its behaviour against
      // E2B is its own package's tests (`sandbox/test`).
      // `test/admitted.ts` admits each owner a test names, as sign-up does (slice L).
      project("owner", "workerd", {
        plugins: [
          cloudflareTest(once(() => pool(main(join(PLATFORM, "owner"), {
              durableObjects: Object.fromEntries(Object.entries(NAMESPACES).map(
                ([binding, className]) => [binding, { className, useSQLite: true }])),
              bindings: { TEST_KEYRING: KEYRING, FILES_TEST_RING: FILES_RING,
                          ROUTING_MAC_KEY: ROUTING_KEY },
              // The files Worker's bucket, test-only: a test changes a chunk's bytes under
              // its key, as storage failing would, and sees a verified read refuse them.
              r2Buckets: ["BLOBS"],
              serviceBindings: { CONNECT: { name: "tinyassets-gateway",
                                            entrypoint: "ConnectGateway" } },
              outboundService: "fake-upstream",
              workers: [
                auxiliary(join(PLATFORM, "files"), { bindings: { FILES_KEYRING: FILES_RING } }),
                auxiliary(join(PLATFORM, "gateway"), { bindings: { GATEWAY_KEYRING: KEYRING },
                                                       outboundService: "fake-upstream" }),
                auxiliary(join(PLATFORM, "hooks"), {}),
                auxiliary(join(PLATFORM, "sandbox"), {
                  bindings: { E2B_API_KEY: "e2b_unused_in_owner_tests" },
                  outboundService: "fake-upstream" }),
                fake(join(PLATFORM, "gateway", "test", "fake-upstream.ts")),
              ],
            }, "./test/worker.ts")))),
        ],
        test: { setupFiles: ["./test/admitted.ts"] },
      }),
      // Static guards over the source and config, in Node.
      project("owner", "guards"),
    ],
  },
});
