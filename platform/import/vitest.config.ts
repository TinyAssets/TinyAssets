import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

// The cutover importer, a job run in Node, deleted with the old stack at the cutover: its
// isolation guard, and the census and bundle builder against old-stack-shaped fixtures.
export default defineConfig({
  test: {
    projects: [project("import", "guards")],
  },
});
