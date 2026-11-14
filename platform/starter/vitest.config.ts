import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

// The starter package's own guards (its vendored OKF spec and its watcher), in Node. The
// owner's hold on what it seeds is the owner's (`owner/guards/starter.test.ts`).
export default defineConfig({
  test: {
    projects: [project("starter", "guards")],
  },
});
