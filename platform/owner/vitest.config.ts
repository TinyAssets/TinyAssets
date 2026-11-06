import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { auxiliary, fake, once } from "../ci/workers";

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
      // config's one secret, the sandbox tokens' routing key. The sandbox Worker's
      // directory is a namespace of its own (no control record in it, so it starts
      // nothing: `sandbox_unconfigured`), and its key reaches no E2B: its behaviour against
      // E2B is its own package's tests (`sandbox/test`).
      // `test/admitted.ts` admits each owner a test names, as sign-up does (slice L).
      project("owner", "workerd", {
        plugins: [
          cloudflareTest(once(() => ({
            main: "./test/worker.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
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
                  bindings: { E2B_API_KEY: "e2b_unused_in_owner_tests", E2B_TEMPLATE: "none" },
                  kvNamespaces: ["DIRECTORY"], outboundService: "fake-upstream" }),
                fake(join(PLATFORM, "gateway", "test", "fake-upstream.ts")),
              ],
            },
          }))),
        ],
        test: { setupFiles: ["./test/admitted.ts"] },
      }),
      // Static guards over the source and config, in Node. The migrations guard imports
      // the schema, whose imports reach the Agents SDK's Lifecycle: Node takes a stub of it
      // (`guards/lifecycle-stub.ts`), since the real one loads `cloudflare:` modules.
      // `migrations-append-only` reads its base's history, which the legs' checkout
      // does not fetch yet: tests/test_migration_lock_append_only.py holds the lock.
      project("owner", "guards", {
        resolve: { alias: { "agents/lifecycle": join(PLATFORM, "owner", "guards",
                                                     "lifecycle-stub.ts") } },
        test: { exclude: ["guards/migrations-append-only.test.ts"] },
      }),
    ],
  },
});
