/**
 * The deploy kit as a GitHub Actions job runs it (`platform-deploy.yml`): the job's summary, the
 * masks in its log and the run's id, each read from a variable Actions sets (GitHub's
 * "Variables reference" and "Workflow commands"). Run by hand, outside Actions, the kit writes
 * no summary and masks nothing, and its run is named by the time.
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
import { writeFileSync } from "node:fs";

type Env = Readonly<Record<string, string | undefined>>;

/** `name`'s value in `env`, as a job hands it: an empty one is unset. */
export function set(env: Env, name: string): string | undefined {
  const value = env[name];
  return value === undefined || value === "" ? undefined : value;
}

/** `text` appended as it is to the job's summary (`GITHUB_STEP_SUMMARY`); by hand, nowhere. */
export function summarize(env: Env, text: string): void {
  const file = set(env, "GITHUB_STEP_SUMMARY");
  if (file !== undefined) {
    writeFileSync(file, text, { flag: "a" });
  }
}

/**
 * The workflow command that masks `value` in the job's log from the next line on
 * (`::add-mask::`), or null outside Actions, where nothing is masked.
 */
export function maskLine(env: Env, value: string): string | null {
  return set(env, "GITHUB_ACTIONS") === undefined ? null : `::add-mask::${value}`;
}

/** A run of the kit: its id, and the attempt of it. */
export interface JobRun {
  readonly id: string;
  readonly attempt: string | null;
}

/**
 * The run: the workflow run's id (`GITHUB_RUN_ID`), which a re-run attempt keeps, and its
 * attempt; run by hand, `local-` and the time `now`, and no attempt.
 */
export function runOf(env: Env, now: Date): JobRun {
  return { id: set(env, "GITHUB_RUN_ID") ?? `local-${now.toISOString().replace(/[-:]|\.\d+/g, "")}`,
           attempt: set(env, "GITHUB_RUN_ATTEMPT") ?? null };
}
