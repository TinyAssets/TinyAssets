/**
 * The run-time timeout rule, with no Node or Workers dependency so it runs in every pool:
 * a test's resolved timeout is its class's, the project's medium budget or the large tag's.
 * `class-check.ts` applies it before every test; `classes.ts` provides the figures.
 */

/** What a project's tests are held to, provided to the test pool (`inject`). */
export interface ClassTimeouts {
  readonly project: string;
  readonly medium: number;
  readonly large: number;
}

/** What is wrong with one test's resolved timeout, or null. */
export function classProblem(task: { name: string; timeout?: number; tags?: string[] },
                             want: ClassTimeouts | undefined): string | null {
  if (want === undefined) {
    return "this project provides no class timeouts: spread medium() into its test options";
  }
  const large = (task.tags ?? []).includes("large");
  const expected = large ? want.large : want.medium;
  if (task.timeout === expected) {
    return null;
  }
  return `"${task.name}" has a timeout of ${task.timeout} ms, not its class's ${expected} ms ` +
    `(${large ? "large" : want.project}); a test's timeout is its size class's ` +
    "(platform/ci/classes.ts): tag a slow test large rather than setting one";
}
