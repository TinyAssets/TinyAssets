import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "../ci/fixtures";
import { PLATFORM } from "../ci/paths";
import { project } from "../ci/project";
import { main, once, pool } from "../ci/workers";

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd, from the config's own entry, bucket and cipher (`main`), with a
      // keyring made fresh for each run as its one secret: none is read from the host.
      project("files", "workerd", {
        plugins: [cloudflareTest(once(() => pool(main(join(PLATFORM, "files"), {
          bindings: { FILES_KEYRING: keyring() } }))))],
      }),
      // Static guards over the source and config, in Node.
      project("files", "guards"),
    ],
  },
});
