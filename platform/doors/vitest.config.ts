import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { auxiliary, fake, fakeConfig, main, once, pool, workerConfig } from "../ci/workers";

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), made fresh for each run. */
const ROUTING_KEY = randomBytes(32).toString("base64url");

/** The shared inbound app's signing secret (`shared-apps.json`'s Slack), for the tests. */
const SLACK_SECRET = "8f742231b10e8888abcd99yyyzzz85a5";

/** A keyring made fresh for each run, the gateway's and the files Worker's. */
const KEYRING = keyring();

/** The fake providers the gateway's fetches reach, and the owner suite's fake sandbox. */
const FAKE_PROVIDERS = join(PLATFORM, "doors", "test", "fake-providers.ts");
const FAKE_SANDBOX = join(PLATFORM, "owner", "test", "fake-sandbox.ts");

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd, against the config's own KV namespaces, flags and GATEWAY
      // binding. The test entry serves the doors with a fake upstream sign-in and the
      // records with a test source beside them, and exports the real OwnerDO, so OWNER,
      // which the config binds by script_name, is bound to it here; the fake upstream's
      // issuer stands in for AuthKit's as the owner issuer. GATEWAY reaches the real
      // gateway Worker (its `ConnectGateway` entrypoint, as the config names it), whose
      // fetches reach the fake providers (`test/fake-providers.ts`); PROVIDERS is the
      // tests' own way to them, to read back what they received. ROUTING_MAC_KEY, the
      // config's one secret, is the owner Worker's too: here both are this Worker. The
      // gateway, codemode and files Workers are each bundled from their own config, as a
      // deploy bundles them. The doors' and each auxiliary Worker have every binding their
      // configs give them (`main`, `auxiliary`), and the tests state each secret: none is
      // read from the host. The owner object hosted in this script has only the three bindings
      // stated below, until it runs as an auxiliary of its own (T2). SANDBOX is the owner
      // suite's fake sandbox Worker (`owner/test/fake-sandbox.ts`), whose record a test
      // reads through SANDBOX_LOG.
      // The import consumer's tests take a bundle the slice-2 builder made in Node
      // (`test/import-setup.ts`), deleted at the cutover with the importer.
      project("doors", "workerd", {
        test: { globalSetup: ["./test/import-setup.ts"] },
        plugins: [
          cloudflareTest(once(() => pool(main(join(PLATFORM, "doors"), {
              durableObjects: { OWNER: { className: "OwnerDO", useSQLite: true } },
              bindings: { OWNER_ISSUER: "https://upstream.test", ROUTING_MAC_KEY: ROUTING_KEY,
                          E2B_WEBHOOK_SECRET: "test-e2b-webhook-signature-secret",
                          WORKOS_API_KEY: "test-client-secret",
                          INBOUND_SLACK_SIGNING_SECRET: SLACK_SECRET },
              // CODEMODE_VM, OWNER_FILES and SANDBOX: the owner object's (in this script for
              // the tests), to the real codemode and files Workers, as the owner config binds
              // them (the owner's FILES is the files Worker's whole service, the doors' its
              // upload entrypoint), and to the owner suite's fake sandbox Worker.
              serviceBindings: { PROVIDERS: fakeConfig(FAKE_PROVIDERS).name,
                                 CODEMODE_VM: workerConfig(join(PLATFORM, "codemode")).name,
                                 OWNER_FILES: workerConfig(join(PLATFORM, "files")).name,
                                 SANDBOX: fakeConfig(FAKE_SANDBOX).name,
                                 SANDBOX_LOG: { name: fakeConfig(FAKE_SANDBOX).name,
                                                entrypoint: "Log" } },
              workers: [
                auxiliary(join(PLATFORM, "gateway"), { bindings: { GATEWAY_KEYRING: KEYRING },
                                                       outboundService:
                                                         fakeConfig(FAKE_PROVIDERS).name }),
                auxiliary(join(PLATFORM, "codemode"), {}),
                fake(FAKE_PROVIDERS),
                fake(FAKE_SANDBOX),
                auxiliary(join(PLATFORM, "files"), { bindings: { FILES_KEYRING: KEYRING } }),
              ],
            }, "./test/entry.ts")))),
        ],
      }),
      // Static guards over the source and config, in Node.
      project("doors", "guards"),
    ],
  },
});
