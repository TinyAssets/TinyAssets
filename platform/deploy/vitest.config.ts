import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

// The deploy kit, run in Node: its steps and modes against fake Cloudflare, WorkOS and E2B
// answers, the workflow that runs it, and the configs it stages bundled as wrangler deploys
// them (large).
export default defineConfig({
  test: {
    projects: [project("deploy", "guards")],
  },
});
