/**
 * Test size classes, and each project's one time budget (Google's and Bazel's test sizes).
 *
 * A test's timeout is its class's, never a number beside the test
 * (`guards/timeouts.test.ts` refuses one):
 * - **medium** is every project's own class: its `testTimeout` (and `hookTimeout`, never under
 *   Vitest's own 10 s on the budgets' runners, since a report measures no hook) is
 *   `FACTOR` times the slowest test that project's measurement (`budgets.json`) found, times
 *   the runner's speed, rounded up to the second, and never under Vitest's own 5 s (the
 *   small class: pure logic, one process). A test slow enough to need more is a large test.
 * - **large** is a tag (`{ tags: ["large"] }`): production sizes, real-size data, and
 *   anything slower than its project's medium budget. Its timeout is `FACTOR` times the
 *   slowest large test measured. The workspace legs run `--tags-filter='!large'`; the
 *   `sizes` leg runs `--tags-filter=large`, sharded (`.github/workflows/tests.yml`).
 *
 * Every project's tests are held to this at run time by a setup file (`class-check.ts`), so
 * no CLI flag can drop the check; a measuring run with `--reporter=json` keeps it.
 *
 * The budgets are derived, never chosen: `node ci/budgets.mjs` recomputes `budgets.json` from
 * one run's whole set of Vitest JSON reports. They are measured on CI's usual runners, and a
 * runner slower than those has every budget scaled by how much slower it is (`RUNNER`): about
 * one leg in two thousand lands on a runner four to five times slower than usual, from its
 * first step (`docs/decisions/untangle-2026-11/budgets-variance.md`).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ClassTimeouts, Runner } from "./class-rule.ts";

/** One measurement: the slowest test found, and where and when it was measured. */
export interface Slowest {
  readonly slowest_ms: number;
  readonly test: string;
  readonly measured: string;
}

/** The calibration's least round on the runners the budgets were measured on. */
export interface Reference {
  readonly calibration_ms: number;
  readonly measured: string;
}

export interface Budgets {
  readonly factor: number;
  readonly runner: Reference;
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

/**
 * A fixed workload of the kind the tests do (V8's strings, maps, sorts and JSON), in rounds:
 * the least round's milliseconds, so a stray collection does not count. A slow runner is slow
 * from its first step to its last (`budgets-variance.md`), so this measures it. Fifteen
 * rounds, about half a second on CI, outlast a neighbour's burst: on the loaded build host,
 * five read over 1.2 times their median in 13 loads of 80, fifteen in 2.
 */
export function calibrate(): number {
  const round = (): number => {
    const words = new Map<string, number>();
    let text = "";
    for (let i = 0; i < 120_000; i++) {
      const word = `w${((i * 7_919) % 10_007).toString(36)}`;
      words.set(word, (words.get(word) ?? 0) + 1);
      text += `${word} `;
    }
    const rows = [...words].map(([word, n]) => ({ word, n, key: [...word].reverse().join("") }));
    rows.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.n - b.n));
    return (JSON.parse(JSON.stringify(rows)) as unknown[]).length + text.length;
  };
  round();
  let least = Number.POSITIVE_INFINITY;
  for (let at = 0; at < 15; at++) {
    const start = performance.now();
    round();
    least = Math.min(least, performance.now() - start);
  }
  return least;
}

/**
 * How many times slower a runner whose calibration took `calibration_ms` is than the runners
 * the budgets were measured on, and never under 1: a faster host keeps the measured budgets,
 * so a test that passes on CI's usual runner passes on any faster one.
 */
export function speedOf(calibration_ms: number, reference: Reference): number {
  return Math.max(1, calibration_ms / reference.calibration_ms);
}

/** This runner: its calibration, measured once as this module loads, and its speed. */
export const RUNNER: Runner = (() => {
  const calibration_ms = calibrate();
  return { calibration_ms, speed: speedOf(calibration_ms, BUDGETS.runner) };
})();

/**
 * A budget: `factor` times the slowest measured, times the runner's `speed`, up to the
 * second, at least `SMALL_MS`.
 */
export function limit(slowest_ms: number, factor: number, speed: number): number {
  return Math.max(SMALL_MS, Math.ceil((factor * slowest_ms * speed) / 1000) * 1000);
}

/** A measurement's budget on this runner. */
export function derived(slowest: Slowest): number {
  return limit(slowest.slowest_ms, BUDGETS.factor, RUNNER.speed);
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

/**
 * Vitest's default hook timeout: a hook's floor on the budgets' runners, since a report
 * measures no hook.
 */
const HOOK_FLOOR_MS = 10_000;

/** The setup file that holds each test to its class at run time (`class-check.ts`). */
export const CLASS_CHECK = join(dirname(fileURLToPath(import.meta.url)), "class-check.ts");

/**
 * A project's test options for its class: its budget for its tests and its hooks, the
 * figures its tests are held to at run time and the runner they were scaled for, and the
 * setup file that holds them. A project with setup files of its own lists `CLASS_CHECK`
 * first among them.
 */
export function medium(project: string): {
  testTimeout: number; hookTimeout: number; setupFiles: string[];
  provide: { classTimeouts: ClassTimeouts };
} {
  const ms = budget(project);
  return { testTimeout: ms, hookTimeout: Math.max(ms, Math.ceil(HOOK_FLOOR_MS * RUNNER.speed)),
           setupFiles: [CLASS_CHECK],
           provide: { classTimeouts: { project, medium: ms, large: derived(BUDGETS.large),
                                       runner: RUNNER } } };
}

/** The large tag, carried by every project (`project.ts`; `strictTags` stays on). */
export const LARGE = Object.freeze({
  name: "large",
  description: "production sizes and anything slower than its project's medium budget; " +
    "the sizes leg runs these",
  timeout: derived(BUDGETS.large),
});
