/**
 * `ci/budgets.mjs` reads the Vitest JSON reports the `platform-sizes` legs upload, which name
 * the CI runner's checkout: a test's project is read from its path below that checkout's
 * platform folder, not this one's.
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../paths";

interface Row { project: string; test: string; ms: number; large: boolean }
const { measured } = await import(join(PLATFORM, "ci", "budgets.mjs")) as
  { measured: (reports: unknown[]) => Row[] };

describe("budgets.mjs", () => {
  it("reads a report a CI runner wrote", () => {
    const report = { testResults: [{
      name: "/home/runner/work/TinyAssets/TinyAssets/platform/owner/test/files.test.ts",
      assertionResults: [
        { status: "passed", duration: 1234.5, fullName: "files fills", tags: ["large"] },
        { status: "skipped", fullName: "files skips", tags: [] },
      ],
    }] };
    expect(measured([report])).toEqual([{ project: "owner/workerd",
                                          test: "owner/test/files.test.ts > files fills",
                                          ms: 1234.5, large: true }]);
  });
});
