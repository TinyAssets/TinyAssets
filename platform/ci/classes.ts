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
 * The budgets are derived, never chosen: `node ci/budgets.mjs --write <vitest JSON reports>`
 * recomputes `budgets.json` from a leg's report, and without `--write` it names each test
 * over half its class's budget (Bazel's `--test_verbose_timeout_warnings`), blocking nothing.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** One measurement: the slowest test found, and where it was read. */
export interface Slowest {
  readonly slowest_ms: number;
  readonly test: string;
}

export interface Budgets {
  readonly factor: number;
  readonly measured: string;
  readonly medium: Readonly<Record<string, Slowest>>;
  readonly large: Slowest;
}

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
    throw new Error(`no measured budget for ${project}: run node ci/budgets.mjs --write`);
  }
  return derived(slowest);
}

/** Vitest's default hook timeout: a hook's floor, since a report measures no hook. */
export const HOOK_FLOOR_MS = 10_000;

/** A project's test options for its class: its budget for its tests and its hooks. */
export function medium(project: string): { testTimeout: number; hookTimeout: number } {
  const ms = budget(project);
  return { testTimeout: ms, hookTimeout: Math.max(ms, HOOK_FLOOR_MS) };
}

/** The large tag, shared by every config (`tags: [LARGE]`; `strictTags` stays on). */
export const LARGE = Object.freeze({
  name: "large",
  description: "production sizes and anything slower than its project's medium budget; " +
    "the sizes leg runs these",
  timeout: derived(BUDGETS.large),
});
