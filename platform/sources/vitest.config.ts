import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

// The source records (`*.json`, `src/`): their static guards, in Node. Their modules run
// inside the tests of each Worker that reads records.
export default defineConfig({
  test: {
    projects: [project("sources", "guards")],
  },
});
