import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { project } from "../ci/project";
import { auxiliary, once, workerConfig } from "../ci/workers";

const CODEMODE = dirname(fileURLToPath(import.meta.url));

// The codemode Worker's own door, in workerd: the Worker bundled from its own config as a
// deploy bundles it, reached as the owner reaches it, by its default entrypoint
// (`CODEMODE_VM`). Its VMs' compute bound is tested here; the owner's tests run the same
// Worker behind their own `CODEMODE_VM` (`owner/test/codemode.test.ts`). And its static
// guards.
export default defineConfig({
  test: {
    projects: [
      project("codemode", "workerd", {
        plugins: [
          cloudflareTest(once(() => ({
            miniflare: {
              compatibilityDate: workerConfig(CODEMODE).compatibility_date,
              compatibilityFlags: ["nodejs_compat"],
              serviceBindings: { CODEMODE_VM: workerConfig(CODEMODE).name },
              workers: [auxiliary(CODEMODE, {})],
            },
          }))),
        ],
      }),
      project("codemode", "guards"),
    ],
  },
});
