import { defineConfig } from "vitest/config";

import { project } from "./project";

// The repository's own gates that outlive the Python suite: the platform's CI legs, the
// test-timeout ban, the production-environment rule and the guard kit's own invariants.
// Static, in Node.
export default defineConfig({
  test: {
    projects: [project("ci", "guards")],
  },
});
