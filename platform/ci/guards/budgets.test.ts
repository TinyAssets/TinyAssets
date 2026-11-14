/**
 * `ci/budgets.mjs` reads the Vitest JSON reports the platform's test legs upload, which name
 * the CI runner's checkout: a test's project is read from its path below that checkout's
 * platform folder, not this one's. Each figure it writes names the run that measured it.
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { Budgets } from "../classes";
import { PLATFORM } from "../paths";

interface Row { project: string; test: string; ms: number; large: boolean }
const { derive, measured } = await import(join(PLATFORM, "ci", "budgets.mjs")) as
  { derive: (budgets: Budgets, rows: Row[], where: string) => Budgets;
    measured: (reports: unknown[]) => Row[] };

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

  it("names, beside each figure, the run that measured it, and keeps the rest's own", () => {
    const old = { slowest_ms: 10, test: "a/guards/a.test.ts > a", measured: "run 1" };
    const budgets = { factor: 4, medium: { "a/guards": old, "b/guards": old }, large: old };
    const rows = [{ project: "b/guards", test: "b/guards/b.test.ts > b", ms: 20.2, large: false },
                  { project: "b/guards", test: "b/guards/b.test.ts > c", ms: 9, large: false }];
    expect(derive(budgets, rows, "run 2")).toEqual({
      factor: 4,
      medium: { "a/guards": old,
                "b/guards": { slowest_ms: 21, test: "b/guards/b.test.ts > b", measured: "run 2" } },
      large: old,
    });
  });
});
