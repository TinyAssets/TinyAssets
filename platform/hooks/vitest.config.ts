import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

// The hooks Worker's code mode runs in workerd from the owner's tests, behind the owner's
// `HOOKS` binding (`owner/test/codemode.test.ts`); here, its static guards.
export default defineConfig({
  test: {
    projects: [project("hooks", "guards")],
  },
});
