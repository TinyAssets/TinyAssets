/**
 * The merge queue's required checks have one source, `docs/ops/merge-queue-ruleset.json`, the
 * ruleset as the founder applies it (`untangle-2026-11/required-checks.md`). Each check names
 * the App that posts it (`integration_id`), and that says which workflow reports it:
 *   - GitHub Actions posts each job's check run under the job's name, on the commit its
 *     workflow ran for. So the check is exactly one job's, reported from its workflow's own
 *     triggers: a workflow started by `workflow_run` runs on main, never on the PR or group.
 *   - Another App posts under a name its own code picks, on the commit its code names
 *     (`review`: review.yml, as the review App; `untangle-2026-11/p2-review-required.md`). So
 *     the check is the workflow named for it, reported from its own triggers and those of the
 *     workflows it runs after. No job may carry the name: Actions would post a lookalike
 *     beside the App's run, which anything reading checks by name (`gh pr checks`, a person)
 *     could take for it.
 * The reporting workflow is started by a pull request (a pull request waits on the check) and
 * by the merge group (the queue waits on it there, then fails the group at its timeout), with
 * no path filter on either, which would leave the check unreported.
 * `scripts/ruleset_drift.py` reads the same file, and auto-enroll arms nothing while the live
 * ruleset differs.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { REPO } from "../paths";
import { type Data, workflow, workflowNames } from "../workflows";

const RULESET = join(REPO, "docs", "ops", "merge-queue-ruleset.json");
const PULL_REQUEST = ["pull_request", "pull_request_target"];
const REPORTING = ["merge_group", ...PULL_REQUEST];
/** GitHub Actions' App id: the `integration_id` of a job's check run. */
const ACTIONS = 15368;

type Check = { context: string; integration_id: number };
/** An event that starts a check's workflow, and the file whose trigger it is. */
type Start = { event: string; file: string };

function requiredChecks(ruleset: Data): Check[] {
  return ruleset.rules.filter((rule: Data) => rule.type === "required_status_checks")
    .flatMap((rule: Data) => rule.parameters.required_status_checks);
}

function triggers(data: Data): Record<string, Data> {
  const on = data.on;
  if (typeof on === "string") return { [on]: null };
  if (Array.isArray(on)) return Object.fromEntries(on.map((event) => [event, null]));
  return on ?? {};
}

/** The workflow that reports `check` and the events that start it, or why none does. */
function reporter(check: Check, workflows: Record<string, Data>):
    { file: string; starts: Start[] } | string {
  const jobs = Object.entries(workflows).flatMap(([file, data]) =>
    Object.entries<Data>(data.jobs ?? {})
      .filter(([key, job]) => (job.name ?? key) === check.context).map(() => file));
  const byApp = check.integration_id !== ACTIONS;
  const files = byApp
    ? Object.keys(workflows).filter((file) => workflows[file].name === check.context) : jobs;
  const [file] = files;
  if (files.length !== 1 || file === undefined || (byApp && jobs.length !== 0)) {
    return byApp
      ? `\`${check.context}\`, posted by App ${check.integration_id}, names ${files.length} ` +
        `workflows and ${jobs.length} jobs (${[...files, ...jobs].join(", ")}), ` +
        "not one workflow and no job"
      : `\`${check.context}\` is the name of ${jobs.length} jobs (${jobs.join(", ")}), ` +
        "not exactly one";
  }
  const starts: Start[] = [];
  const reached = new Set<string>();
  const start = (each: string): void => {
    if (reached.has(each)) return;
    reached.add(each);
    const on = triggers(workflows[each]);
    for (const event of Object.keys(on)) starts.push({ event, file: each });
    if (!byApp) return;
    for (const name of on.workflow_run?.workflows ?? []) {
      for (const [after, data] of Object.entries(workflows)) {
        if (data.name === name) start(after);
      }
    }
  };
  start(file);
  return { file, starts };
}

/** Every way the workflows (by file) fail the required checks. */
function requiredCheckProblems(required: Check[], workflows: Record<string, Data>): string[] {
  const problems: string[] = [];
  for (const check of required) {
    const found = reporter(check, workflows);
    if (typeof found === "string") {
      problems.push(found);
      continue;
    }
    const { file, starts } = found;
    const reporting = starts.filter(({ event }) => REPORTING.includes(event));
    const events = reporting.map(({ event }) => event);
    if (!events.includes("merge_group") || !events.some((event) => PULL_REQUEST.includes(event))) {
      problems.push(`${file} (\`${check.context}\`) is started by ` +
                    `${events.join(", ") || "neither"}, not by the merge group and a pull request`);
    }
    for (const { event, file: from } of reporting) {
      for (const filter of ["paths", "paths-ignore"]) {
        if (triggers(workflows[from])[event]?.[filter] !== undefined) {
          problems.push(`${from} (\`${check.context}\`) filters ${event} by \`${filter}\``);
        }
      }
    }
  }
  return problems;
}

describe("the merge queue's required checks", () => {
  const required = requiredChecks(JSON.parse(readFileSync(RULESET, "utf8")));
  const workflows = Object.fromEntries(workflowNames().map((file) => [file, workflow(file)]));

  it("are each reported by one workflow, started by the pull request and the merge group",
     () => {
    expect(required).not.toEqual([]);
    expect(requiredCheckProblems(required, workflows)).toEqual([]);
  });

  it("fail a renamed reporter, a doubled or lookalike job, a lost or filtered start, an " +
     "Actions job started only after another workflow", () => {
    for (const check of required) {
      const found = reporter(check, workflows);
      if (typeof found === "string") throw new Error(found);
      const { file, starts } = found;
      const renamed = structuredClone(workflows);
      if (renamed[file].name === check.context) renamed[file].name = `${check.context} renamed`;
      for (const [key, job] of Object.entries<Data>(renamed[file].jobs)) {
        if ((job.name ?? key) === check.context) job.name = `${check.context} renamed`;
      }
      expect(requiredCheckProblems(required, renamed)).not.toEqual([]);
      const doubled = structuredClone(workflows);
      doubled[file].jobs.twin = { name: check.context };
      expect(requiredCheckProblems(required, doubled)).not.toEqual([]);
      for (const { event, file: from } of starts.filter(({ event }) => REPORTING.includes(event))) {
        const lost = structuredClone(workflows);
        delete triggers(lost[from])[event];
        expect(requiredCheckProblems(required, lost)).not.toEqual([]);
        const filtered = structuredClone(workflows);
        const on = triggers(filtered[from]);
        on[event] = { ...(on[event] ?? {}), paths: ["docs/**"] };
        expect(requiredCheckProblems(required, filtered)).not.toEqual([]);
      }
      if (check.integration_id === ACTIONS) {
        const after = structuredClone(workflows);
        const on = triggers(after[file]);
        delete on.merge_group;
        on.workflow_run = { workflows: ["review (merge queue)"], types: ["completed"] };
        expect(requiredCheckProblems(required, after)).not.toEqual([]);
      }
    }
  });
});
