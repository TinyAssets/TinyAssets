/**
 * Every project's setup file (`medium()` adds it): before each test, its resolved timeout must
 * be its class's. A setup file runs in the test pool itself, so no CLI flag (`--reporter`,
 * `--silent`) drops it, whatever the test's syntax: a function passed by name, options from
 * a variable or a spread, `test.extend`, an alias.
 */
import { beforeEach, inject } from "vitest";

import { classProblem, type ClassTimeouts } from "./class-rule";

declare module "vitest" {
  export interface ProvidedContext {
    classTimeouts: ClassTimeouts;
  }
}

beforeEach((context) => {
  const problem = classProblem(context.task as { name: string; timeout?: number; tags?: string[] },
                               inject("classTimeouts"));
  if (problem !== null) {
    throw new Error(problem);
  }
});
