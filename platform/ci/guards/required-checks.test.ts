/**
 * The merge queue's required checks have one source, `docs/ops/merge-queue-ruleset.json`, the
 * ruleset as the founder applies it (`untangle-2026-11/required-checks.md`). Each check it
 * requires is a workflow job's check run, named by the job: so the name is exactly one job's,
 * in a workflow that runs on the pull request (a pull request waits on it) and on the merge
 * group (the queue waits on it there, then fails the group at its timeout), with no path
 * filter on either, which would leave the check unreported. `enqueue-merge.yml` runs when
 * one of those workflows completes, so it listens to exactly them. `scripts/enqueue_merge.py`
 * reads the same file, and fails while the live ruleset differs from it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { REPO } from "../paths";
import { type Data, workflow, workflowNames } from "../workflows";

const RULESET = join(REPO, "docs", "ops", "merge-queue-ruleset.json");
const ENQUEUE = "enqueue-merge.yml";
const PULL_REQUEST = ["pull_request", "pull_request_target"];

function requiredChecks(ruleset: Data): string[] {
  return ruleset.rules.filter((rule: Data) => rule.type === "required_status_checks")
    .flatMap((rule: Data) => rule.parameters.required_status_checks)
    .map((check: Data) => check.context);
}

function triggers(data: Data): Record<string, Data> {
  const on = data.on;
  if (typeof on === "string") return { [on]: null };
  if (Array.isArray(on)) return Object.fromEntries(on.map((event) => [event, null]));
  return on ?? {};
}

/** Every way the workflows (by file) fail the required checks. */
function requiredCheckProblems(required: string[],
                                      workflows: Record<string, Data>): string[] {
  const problems: string[] = [];
  const owners = new Set<string>();
  for (const check of required) {
    const jobs = Object.entries(workflows).flatMap(([file, data]) =>
      Object.entries<Data>(data.jobs ?? {})
        .filter(([key, job]) => (job.name ?? key) === check).map(() => file));
    if (jobs.length !== 1) {
      problems.push(`\`${check}\` is the name of ${jobs.length} jobs (${jobs.join(", ")}), ` +
                    "not exactly one");
      continue;
    }
    const data = workflows[jobs[0]];
    owners.add(String(data.name));
    const on = triggers(data);
    const events = ["merge_group", ...PULL_REQUEST].filter((event) => event in on);
    if (!events.includes("merge_group") || events.length < 2) {
      problems.push(`${jobs[0]} (\`${check}\`) runs on ${events.join(", ") || "neither"}, ` +
                    "not on the merge group and a pull request");
    }
    for (const event of events) {
      for (const filter of ["paths", "paths-ignore"]) {
        if (on[event]?.[filter] !== undefined) {
          problems.push(`${jobs[0]} (\`${check}\`) filters ${event} by \`${filter}\``);
        }
      }
    }
  }
  const listened = [...(triggers(workflows[ENQUEUE]).workflow_run?.workflows ?? [])].sort();
  const expected = [...owners].sort();
  if (JSON.stringify(listened) !== JSON.stringify(expected)) {
    problems.push(`${ENQUEUE} runs after ${JSON.stringify(listened)}, ` +
                  `not the workflows of the required checks, ${JSON.stringify(expected)}`);
  }
  return problems;
}

describe("the merge queue's required checks", () => {
  const required = requiredChecks(JSON.parse(readFileSync(RULESET, "utf8")));
  const workflows = Object.fromEntries(workflowNames().map((file) => [file, workflow(file)]));

  it("are each one job's, reported on the pull request and the merge group, and " +
     "enqueue-merge runs after exactly their workflows", () => {
    expect(required).not.toEqual([]);
    expect(requiredCheckProblems(required, workflows)).toEqual([]);
  });

  it("fail a renamed or doubled job, a lost trigger, a path filter and a stray listener", () => {
    const owner = (check: string): string => Object.keys(workflows).find((file) =>
      Object.entries<Data>(workflows[file].jobs ?? {})
        .some(([key, job]) => (job.name ?? key) === check))!;
    for (const check of required) {
      const file = owner(check);
      const renamed = structuredClone(workflows);
      for (const [key, job] of Object.entries<Data>(renamed[file].jobs)) {
        if ((job.name ?? key) === check) job.name = `${check} renamed`;
      }
      expect(requiredCheckProblems(required, renamed)).not.toEqual([]);
      const doubled = structuredClone(workflows);
      doubled[file].jobs.twin = { name: check };
      expect(requiredCheckProblems(required, doubled)).not.toEqual([]);
      const unqueued = structuredClone(workflows);
      delete triggers(unqueued[file]).merge_group;
      expect(requiredCheckProblems(required, unqueued)).not.toEqual([]);
      const filtered = structuredClone(workflows);
      const on = triggers(filtered[file]);
      const event = PULL_REQUEST.find((each) => each in on)!;
      on[event] = { ...(on[event] ?? {}), paths: ["docs/**"] };
      expect(requiredCheckProblems(required, filtered)).not.toEqual([]);
    }
    const stray = structuredClone(workflows);
    triggers(stray[ENQUEUE]).workflow_run.workflows.push("heavy-tests");
    expect(requiredCheckProblems(required, stray)).not.toEqual([]);
  });
});
