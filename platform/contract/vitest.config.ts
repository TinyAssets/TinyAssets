import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

// The vocabulary every Worker speaks (`src/`): its static guards, in Node. Its modules run
// inside each Worker's own tests.
export default defineConfig({
  test: {
    projects: [project("contract", "guards")],
  },
});
