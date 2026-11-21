/**
 * `ci/budgets.mjs` reads the Vitest JSON reports the platform's test legs upload, which name
 * the CI runner's checkout: a test's project is read from its path below that checkout's
 * platform folder, not this one's. Each figure it writes names the run that measured it, and
 * counts each test as the budgets' runner would have measured it.
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { Budgets } from "../classes";
import { PLATFORM } from "../paths";

interface Row { project: string; test: string; ms: number; large: boolean;
              calibration_ms: number }
const { derive, measured } = await import(join(PLATFORM, "ci", "budgets.mjs")) as
  { derive: (budgets: Budgets, rows: Row[], where: string) => Budgets;
    measured: (reports: unknown[]) => Row[] };

describe("budgets.mjs", () => {
  it("reads a report a CI runner wrote", () => {
    const report = { testResults: [{
      name: "/home/runner/work/TinyAssets/TinyAssets/platform/owner/test/files.test.ts",
      assertionResults: [
        { status: "passed", duration: 1234.5, fullName: "files fills", tags: ["large"],
          meta: { calibration_ms: 30 } },
        { status: "skipped", fullName: "files skips", tags: [], meta: {} },
      ],
    }] };
    expect(measured([report])).toEqual([{ project: "owner/workerd",
                                          test: "owner/test/files.test.ts > files fills",
                                          ms: 1234.5, large: true, calibration_ms: 30 }]);
  });

  const reportWith = (duration: unknown, calibration_ms: unknown) => ({
    testResults: [{ name: "/ci/platform/owner/test/files.test.ts", assertionResults: [
      { status: "passed", fullName: "short", duration: 9, meta: { calibration_ms: 20 } },
      { status: "passed", fullName: "long", duration, meta: { calibration_ms } },
    ] }],
  });

  it("refuses to set a budget from the other passing tests when a duration is lost", () => {
    for (const duration of [undefined, null, "9", -1, Infinity]) {
      expect(() => measured([reportWith(duration, 20)]))
        .toThrow("owner/test/files.test.ts > long: expected a finite, nonnegative duration");
    }
    expect(measured([reportWith(0, 20)])[1]?.ms).toBe(0);
  });

  it("requires a usable runner calibration on every passed test", () => {
    for (const calibration of [undefined, null, "20", 0, -1, Infinity]) {
      expect(() => measured([reportWith(9, calibration)]))
        .toThrow("owner/test/files.test.ts > long: expected a finite, positive runner calibration");
    }
  });

  const old = { slowest_ms: 10, test: "a/guards/a.test.ts > a", measured: "run 1" };
  const budgets = { factor: 4, runner: { calibration_ms: 30, measured: "run 1" },
                    medium: { "a/guards": old, "b/guards": old }, large: old };
  const row = (test: string, ms: number, calibration_ms: number, project = "b/guards",
               large = false): Row =>
    ({ project, test: `${project}/x.test.ts > ${test}`, ms, large, calibration_ms });
  /** The figures besides `b/guards`, measured too: a whole set of reports. */
  const rest = [row("a", 7, 20, "a/guards"), row("big", 90, 20, "b/guards", true)];

  it("names, beside each figure, the run that measured it", () => {
    const rows = [row("b", 20.2, 20), row("c", 9, 20), ...rest];
    const at = (slowest_ms: number, test: string) => ({ slowest_ms, test, measured: "run 2" });
    expect(derive(budgets, rows, "run 2")).toEqual({
      factor: 4,
      runner: { calibration_ms: 20, measured: "run 2" },
      medium: { "a/guards": at(7, "a/guards/x.test.ts > a"),
                "b/guards": at(21, "b/guards/x.test.ts > b") },
      large: at(90, "b/guards/x.test.ts > big"),
    });
  });

  // The runner is every figure's unit: a figure kept from older reports would be read
  // against a runner that never measured it (#5615 set one figure from one report).
  it("refuses reports that leave a figure unmeasured", () => {
    expect(() => derive(budgets, [row("b", 20, 20), rest[1]!], "run 2"))
      .toThrow(/measure no a\/guards: read a run's whole set/);
    expect(() => derive(budgets, [row("b", 20, 20), rest[0]!], "run 2"))
      .toThrow(/measure no large/);
  });

  // #5610's contract leg ran on a runner about five times slower than usual, and its
  // 16,442 ms set `contract/guards` at 66 s, where the usual runners' 4,643 ms set 19 s.
  it("counts a slow runner's test as the budgets' runner measures it", () => {
    const rows = [row("usual", 4_643, 20), row("usual too", 4_000, 20),
                  row("slow", 16_442, 100), ...rest];
    const next = derive(budgets, rows, "run 2");
    expect(next.runner.calibration_ms).toBe(20);
    expect(next.medium["b/guards"]?.slowest_ms).toBe(4_643);
  });
});
