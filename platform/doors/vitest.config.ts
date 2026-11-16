import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { control, ownerGate } from "../ci/gate";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { auxiliary, fake, fakeConfig, main, once, pool } from "../ci/workers";
import { QUEUES } from "../contract/src/queue-names";
import { AUTHKIT } from "./test/fake-providers";

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), the doors' and the owner's. */
const ROUTING_KEY = randomBytes(32).toString("base64url");

/** The shared inbound app's signing secret (`shared-apps.json`'s Slack), for the tests. */
const SLACK_SECRET = "8f742231b10e8888abcd99yyyzzz85a5";

/** A keyring made fresh for each run, the gateway's and the files Worker's. */
const KEYRING = keyring();
/** The operator channel's keyring, its own, made fresh for each run. */
const OPERATOR_KEYRING = keyring();

/**
 * The outside world the doors' and the gateway's fetches reach (the fake AuthKit and model
 * providers), and the owner suite's fake sandbox.
 */
const FAKE_PROVIDERS = join(PLATFORM, "doors", "test", "fake-providers.ts");
const FAKE_SANDBOX = join(PLATFORM, "ci", "fake-sandbox.ts");
/** The gate in front of the owner Worker's objects, which the doors' OWNER reaches. */
const OWNER_GATE = "owner-gate";

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd: the doors' real entry (`src/index.ts`, the config's `main`), against
      // the config's own KV namespaces, flags, queues and service bindings. The owner's
      // sign-in is the live one, configured as the deploy kit configures it (the fake
      // `AUTHKIT`): every fetch of the doors, as of the gateway, reaches the fake outside
      // world (`test/fake-providers.ts`), which plays AuthKit too; PROVIDERS is the tests' own
      // way to it, to read back what it received. OWNER, which the config binds by
      // script_name, is the owner gate (`ci/gate.ts`), in front of the owner Worker's objects:
      // the owner Worker bundled from its real entry as a deploy bundles it, with every binding
      // its config gives it (its FILES, GATEWAY and CODEMODE_VM the real Workers' beside it,
      // its ERASURES the config's queue, which the doors' own entry consumes, a message a batch
      // and at once). The gate forwards every call unchanged until a test arms it, through
      // OWNER_GATE (bound to the test Worker alone), to hold, refuse or answer one owner's
      // door, and records what crossed by owner (`test/gates.ts` disarms it after each test).
      // The owner's SANDBOX is the owner suite's fake sandbox Worker
      // (`ci/fake-sandbox.ts`), whose record a test reads through SANDBOX_LOG. GATEWAY
      // reaches the real gateway Worker (its `ConnectGateway` entrypoint, as the config names
      // it). ROUTING_MAC_KEY is both the doors' and the owner's, as their configs share it.
      // The doors' and each auxiliary Worker have every binding their configs give them
      // (`main`, `auxiliary`), and the tests state each secret: none is read from the host.
      // The import consumer's tests take a bundle the slice-2 builder made in Node
      // (`test/import-setup.ts`), deleted at the cutover with the importer.
      project("doors", "workerd", {
        test: { globalSetup: ["./test/import-setup.ts"], setupFiles: ["./test/gates.ts"] },
        plugins: [
          cloudflareTest(once(async () => pool(main(join(PLATFORM, "doors"), {
              durableObjects: { OWNER: { className: "OwnerDO", scriptName: OWNER_GATE,
                                         useSQLite: true } },
              bindings: { ...AUTHKIT, ROUTING_MAC_KEY: ROUTING_KEY,
                          E2B_WEBHOOK_SECRET: "test-e2b-webhook-signature-secret",
                          WORKOS_API_KEY: "test-client-secret", OPERATOR_KEYRING,
                          INBOUND_SLACK_SIGNING_SECRET: SLACK_SECRET },
              serviceBindings: { PROVIDERS: fakeConfig(FAKE_PROVIDERS).name,
                                 OWNER_GATE: control(OWNER_GATE),
                                 SANDBOX_LOG: { name: fakeConfig(FAKE_SANDBOX).name,
                                                entrypoint: "Log" } },
              queueConsumers: { [QUEUES.ERASURES]: { maxBatchSize: 1, maxBatchTimeout: 0 } },
              outboundService: fakeConfig(FAKE_PROVIDERS).name,
              workers: [
                await auxiliary(join(PLATFORM, "owner"), {
                  bindings: { ROUTING_MAC_KEY: ROUTING_KEY },
                  serviceBindings: { SANDBOX: fakeConfig(FAKE_SANDBOX).name } }),
                ownerGate(OWNER_GATE),
                await auxiliary(join(PLATFORM, "gateway"),
                                { bindings: { GATEWAY_KEYRING: KEYRING },
                                  outboundService: fakeConfig(FAKE_PROVIDERS).name }),
                await auxiliary(join(PLATFORM, "codemode"), {}),
                fake(FAKE_PROVIDERS),
                fake(FAKE_SANDBOX),
                await auxiliary(join(PLATFORM, "files"), { bindings: { FILES_KEYRING: KEYRING } }),
              ],
            })))),
        ],
      }),
      // Static guards over the source and config, in Node.
      project("doors", "guards"),
    ],
  },
});
