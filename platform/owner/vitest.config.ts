import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { control, gate, methodsOf } from "../ci/gate";
import { auxiliary, fake, fakeConfig, main, once, pool, workerConfig } from "../ci/workers";
import { QUEUES } from "../contract/src/queue-names";

import { NAMESPACES } from "./test/namespaces";

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), made fresh for each run. */
const ROUTING_KEY = randomBytes(32).toString("base64url");
/** The gateway's keyring, made fresh for each run: a rotated ring, so `k1` is an older kid. */
const KEYRING = keyring("k2", ["k1", "k2"]);
/** The files Worker's data keys' ring, made fresh for each run: rotated, `k1` the older kid. */
const FILES_RING = keyring("k2", ["k1", "k2"]);
/** The sandbox Worker the owner's object reaches in these tests: a fake, by its file. */
const FAKE_SANDBOX = join(PLATFORM, "owner", "test", "fake-sandbox.ts");
/** The gate in front of it, `SANDBOX`. */
const SANDBOX_GATE = "sandbox-gate";
/** The gates in front of the files and gateway Workers, `FILES` and `GATEWAY`. */
const FILES_GATE = "files-gate";
const GATEWAY_GATE = "gateway-gate";
/** The erasure queue's consumer in the doors' place: it records what each object sent. */
const FAKE_ERASURES = join(PLATFORM, "owner", "test", "fake-erasures.ts");
/** The gateway's fake outside world, which every fetch of the gateway and the tests reaches. */
const FAKE_UPSTREAM = join(PLATFORM, "gateway", "test", "fake-upstream.ts");

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd. The owner object's namespaces are bound for the tests only
      // (`test/namespaces.ts`, each a class of the test Worker, `test/worker.ts`):
      // wrangler.jsonc gives the Worker no binding to its own namespace. FILES, GATEWAY and
      // CODEMODE_VM are the config's own bindings, to the real files, gateway and codemode
      // Workers run beside it, each bundled from its own config as a deploy bundles it; FILES
      // and GATEWAY reach theirs through a gate each (`ci/gate.ts`), which forwards every call
      // unchanged until a test arms it, through FILES_GATE or GATEWAY_GATE (bound to the test
      // Worker alone), to hold one owner's call, change its bytes or refuse it, and records
      // what crossed by owner (`gates.ts` disarms them after each test). SANDBOX is a gate
      // in front of a fake sandbox Worker (`test/fake-sandbox.ts`), which records what each
      // owner's object asked and answers as a test scripts it: a test reads and scripts it through SANDBOX_LOG, and holds a
      // call at the gate through SANDBOX_GATE, both bound to the test Worker alone. The real
      // sandbox Worker is tested against a fake E2B in its own package (`sandbox/test`).
      // TEST_KEYRING is the gateway's keyring, so a test can seal under an older kid, and
      // FILES_TEST_RING the files Worker's, so a test can wrap a data key under its older
      // kid; every fetch of the gateway and the tests reaches the gateway's fake outside
      // world (`gateway/test/fake-upstream.ts`). ROUTING_MAC_KEY is the config's one secret,
      // the sandbox tokens' routing key. The test Worker and each Worker beside it have every
      // binding their configs give them (`main`, `auxiliary`), and a test states only their
      // secrets and what it changes; nothing is read from the host (`pool`).
      // A test admits each owner it names by the platform's door, as sign-up does (slice L,
      // `test/support.ts` `admitted`). ERASURES is the config's queue, consumed by a fake
      // Worker (`test/fake-erasures.ts`) a test reads through ERASURES_SENT.
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
              serviceBindings: { CONNECT: { name: workerConfig(join(PLATFORM, "gateway")).name,
                                            entrypoint: "ConnectGateway" },
                                 SANDBOX: SANDBOX_GATE, SANDBOX_GATE: control(SANDBOX_GATE),
                                 FILES: FILES_GATE, FILES_GATE: control(FILES_GATE),
                                 GATEWAY: GATEWAY_GATE, GATEWAY_GATE: control(GATEWAY_GATE),
                                 SANDBOX_LOG: { name: fakeConfig(FAKE_SANDBOX).name,
                                                entrypoint: "Log" },
                                 ERASURES_SENT: { name: fakeConfig(FAKE_ERASURES).name,
                                                  entrypoint: "Sent" } },
              outboundService: fakeConfig(FAKE_UPSTREAM).name,
              workers: [
                await auxiliary(join(PLATFORM, "files"),
                                { bindings: { FILES_KEYRING: FILES_RING } }),
                await auxiliary(join(PLATFORM, "gateway"),
                                { bindings: { GATEWAY_KEYRING: KEYRING },
                                  outboundService: fakeConfig(FAKE_UPSTREAM).name }),
                await auxiliary(join(PLATFORM, "codemode"), {}),
                gate(SANDBOX_GATE, methodsOf(join(PLATFORM, "contract", "src", "sandbox.ts"),
                                                "SandboxService"), { fake: FAKE_SANDBOX }),
                gate(FILES_GATE, methodsOf(join(PLATFORM, "contract", "src", "files.ts"),
                                          "ChunkStore"), { worker: join(PLATFORM, "files") }),
                gate(GATEWAY_GATE, methodsOf(join(PLATFORM, "contract", "src", "gateway.ts"),
                                            "GatewayService"),
                     { worker: join(PLATFORM, "gateway") }),
                fake(FAKE_SANDBOX),
                // The erasure queue (the config's own ERASURES) consumed at once, each message
                // recorded by owner: a test confirms the outside record itself, as the doors do.
                fake(FAKE_ERASURES, { queueConsumers: {
                  [QUEUES.ERASURES]: { maxBatchSize: 1, maxBatchTimeout: 0 } } }),
                fake(FAKE_UPSTREAM),
              ],
            }, "./test/worker.ts")))),
        ],
        test: { setupFiles: ["./test/gates.ts"] },
      }),
      // Static guards over the source and config, in Node.
      project("owner", "guards"),
    ],
  },
});
