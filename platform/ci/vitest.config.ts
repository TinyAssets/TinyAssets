import { defineConfig } from "vitest/config";

import { LARGE, medium, REPORTERS } from "./classes";

// The repository's own gates that outlive the Python suite: the platform's CI legs, the
// test-timeout ban and the production-environment rule. Static, in Node.
export default defineConfig({
  test: {
    tags: [LARGE],
    reporters: [...REPORTERS],
    projects: [
      {
        test: { name: "guards", ...medium("ci/guards"), include: ["guards/**/*.test.ts"],
                environment: "node" },
      },
    ],
  },
});
