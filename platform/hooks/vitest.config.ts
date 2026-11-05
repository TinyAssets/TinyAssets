import { defineConfig } from "vitest/config";

// The hooks Worker's code mode runs in workerd from the owner's tests, behind the owner's
// `HOOKS` binding (`owner/test/codemode.test.ts`); here, its static guards.
export default defineConfig({
  test: {
    projects: [
      {
        // Static guards over the source and config, in Node.
        test: { name: "guards", include: ["guards/**/*.test.ts"], environment: "node",
                testTimeout: 30_000 },
      },
    ],
  },
});
