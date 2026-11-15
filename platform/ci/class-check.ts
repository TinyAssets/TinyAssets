/**
 * Every project's setup file (`medium()` adds it): before each test, its resolved timeout must
 * be its class's. A setup file runs in the test pool itself, so no CLI flag (`--reporter`,
 * `--silent`) drops it, whatever the test's syntax: a function passed by name, options from
 * a variable or a spread, `test.extend`, an alias. Each test also carries its runner's
 * calibration in its meta, which a JSON report keeps, so `budgets.mjs` measures each test as
 * the budgets' runners would.
 */
import { beforeEach, inject } from "vitest";

import { classProblem, type ClassTimeouts } from "./class-rule";

declare module "vitest" {
  export interface ProvidedContext {
    classTimeouts: ClassTimeouts;
  }
  export interface TaskMeta {
    calibration_ms?: number;
  }
}

beforeEach((context) => {
  const want = inject("classTimeouts");
  const problem = classProblem(context.task as { name: string; timeout?: number; tags?: string[] },
                               want);
  if (problem !== null) {
    throw new Error(problem);
  }
  context.task.meta.calibration_ms = want.runner.calibration_ms;
});
