/**
 * Test size classes, and each project's one time budget (Google's and Bazel's test sizes).
 *
 * A test's timeout is its class's, never a number beside the test
 * (`guards/timeouts.test.ts` refuses one):
 * - **medium** is every project's own class: its `testTimeout` (and `hookTimeout`, never under
 *   Vitest's own 10 s, since a report measures no hook) is
 *   `FACTOR` times the slowest test that project's measurement (`budgets.json`) found,
 *   rounded up to the second, and never under Vitest's own 5 s (the small class: pure
 *   logic, one process). A test slow enough to need more is a large test.
 * - **large** is a tag (`{ tags: ["large"] }`): production sizes, real-size data, and
 *   anything slower than its project's medium budget. Its timeout is `FACTOR` times the
 *   slowest large test measured. The workspace legs run `--tags-filter='!large'`; the
 *   `sizes` leg runs `--tags-filter=large`, sharded (`.github/workflows/tests.yml`).
 *
 * Every project's tests are held to this at run time by a setup file (`class-check.ts`), so
 * no CLI flag can drop the check; a measuring run with `--reporter=json` keeps it.
 *
 * The budgets are derived, never chosen: `node ci/budgets.mjs` recomputes `budgets.json`
 * from a leg's Vitest JSON report.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ClassTimeouts } from "./class-rule.ts";

/** One measurement: the slowest test found, and where it was read. */
export interface Slowest {
  readonly slowest_ms: number;
  readonly test: string;
}

interface Budgets {
  readonly factor: number;
  readonly measured: string;
  readonly medium: Readonly<Record<string, Slowest>>;
  readonly large: Slowest;
}

/**
 * Each project a workspace may hold, by its name, and the folder its tests are in: a
 * project's budget is `<workspace>/<name>` (`project.ts` makes every project from it, and
 * `budgets.mjs` reads a test's project from its folder).
 */
export const PROJECTS = Object.freeze({ workerd: "test", guards: "guards", runner: "runner" });
export type ProjectName = keyof typeof PROJECTS;

/** Vitest's default timeout: the small class's, and every budget's floor. */
export const SMALL_MS = 5_000;

export const BUDGETS: Budgets = JSON.parse(readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "budgets.json"), "utf8")) as Budgets;

/** `FACTOR` times the slowest measured, up to the second, at least `SMALL_MS`. */
export function derived(slowest: Slowest, factor = BUDGETS.factor): number {
  return Math.max(SMALL_MS, Math.ceil((factor * slowest.slowest_ms) / 1000) * 1000);
}

/**
 * A project's medium budget, by its `<workspace>/<project>` name. A project with no
 * measurement fails here, at config load, rather than running under a guessed number.
 */
export function budget(project: string): number {
  const slowest = BUDGETS.medium[project];
  if (slowest === undefined) {
    throw new Error(`no measured budget for ${project}: run node ci/budgets.mjs`);
  }
  return derived(slowest);
}

/** Vitest's default hook timeout: a hook's floor, since a report measures no hook. */
const HOOK_FLOOR_MS = 10_000;

/** The setup file that holds each test to its class at run time (`class-check.ts`). */
export const CLASS_CHECK = join(dirname(fileURLToPath(import.meta.url)), "class-check.ts");

/**
 * A project's test options for its class: its budget for its tests and its hooks, the
 * figures its tests are held to at run time, and the setup file that holds them. A project
 * with setup files of its own lists `CLASS_CHECK` first among them.
 */
export function medium(project: string): {
  testTimeout: number; hookTimeout: number; setupFiles: string[];
  provide: { classTimeouts: ClassTimeouts };
} {
  const ms = budget(project);
  return { testTimeout: ms, hookTimeout: Math.max(ms, HOOK_FLOOR_MS), setupFiles: [CLASS_CHECK],
           provide: { classTimeouts: { project, medium: ms, large: derived(BUDGETS.large) } } };
}

/** The large tag, carried by every project (`project.ts`; `strictTags` stays on). */
export const LARGE = Object.freeze({
  name: "large",
  description: "production sizes and anything slower than its project's medium budget; " +
    "the sizes leg runs these",
  timeout: derived(BUDGETS.large),
});
