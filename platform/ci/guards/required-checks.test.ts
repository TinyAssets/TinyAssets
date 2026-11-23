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
 * A job's check reports when the job ends, and the job starts once the jobs it needs have
 * ended, so it reports by its watchdog (`timeout-minutes`) added to the longest chain of its
 * needs' watchdogs. That sum is within the queue's timeout: a hung job is then killed by its
 * own watchdog, and the check fails naming it, before the queue fails the group as unreported
 * (`untangle-2026-11/dry-run-timeout.md`).
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
/** The watchdog GitHub gives a job that sets none, in minutes. */
const UNSET = 360;

type Check = { context: string; integration_id: number };
/** An event that starts a check's workflow, and the file whose trigger it is. */
type Start = { event: string; file: string };

function requiredChecks(ruleset: Data): Check[] {
  return ruleset.rules.filter((rule: Data) => rule.type === "required_status_checks")
    .flatMap((rule: Data) => rule.parameters.required_status_checks);
}

/** How long the queue waits on a required check before it fails the group, in minutes. */
function queueTimeout(ruleset: Data): number {
  return ruleset.rules.find((rule: Data) => rule.type === "merge_queue")
    .parameters.check_response_timeout_minutes;
}

type Chain = { minutes: number; chain: string[] };

/**
 * Each chain of jobs that ends in this one, through the jobs it needs, with the latest it can
 * end by their watchdogs, in minutes from its run's start, each job named as `key watchdog`.
 * A watchdog that is not a number of minutes (an expression) bounds nothing.
 */
function chains(jobs: Record<string, Data>, key: string): Chain[] {
  const watchdog = jobs[key]["timeout-minutes"] ?? UNSET;
  const own = Number.isInteger(watchdog) ? watchdog : Infinity;
  const needs = [jobs[key].needs ?? []].flat();
  const before: Chain[] = needs.length
    ? needs.flatMap((need: string) => chains(jobs, need)) : [{ minutes: 0, chain: [] }];
  return before.map(({ minutes, chain }) =>
    ({ minutes: minutes + own, chain: [...chain, `${key} ${watchdog}`] }));
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

/** Each job's check that can report later than the queue waits, by its chain of watchdogs. */
function lateProblems(required: Check[], workflows: Record<string, Data>,
                      timeout: number): string[] {
  return required.filter((check) => check.integration_id === ACTIONS).flatMap((check) => {
    const found = reporter(check, workflows);
    if (typeof found === "string") return [found];
    const { jobs } = workflows[found.file];
    return Object.keys(jobs).filter((key) => (jobs[key].name ?? key) === check.context)
      .flatMap((key) => chains(jobs, key))
      .filter(({ minutes }) => minutes > timeout)
      .map(({ minutes, chain }) =>
        `\`${check.context}\` can report ${minutes} minutes into its run ` +
        `(${chain.join(" + ")}), past the queue's ${timeout}`);
  });
}

describe("the merge queue's required checks", () => {
  const ruleset = JSON.parse(readFileSync(RULESET, "utf8"));
  const required = requiredChecks(ruleset);
  const timeout = queueTimeout(ruleset);
  const workflows = Object.fromEntries(workflowNames().map((file) => [file, workflow(file)]));

  it("each report, by their chain of watchdogs, within the queue's timeout", () => {
    expect(timeout).toBeGreaterThan(0);
    expect(lateProblems(required, workflows, timeout)).toEqual([]);
  });

  it("fail a watchdog that leaves a hung job unnamed by the queue: past its timeout, " +
     "unset, an expression, or a needed job's", () => {
    const tests = (change: (jobs: Data) => void): string[] => {
      const changed = structuredClone(workflows);
      change(changed["tests.yml"].jobs);
      return lateProblems(required, changed, timeout);
    };
    const leg = (jobs: Data) => jobs["platform-recorders"];
    expect(tests((jobs) => { leg(jobs)["timeout-minutes"] = timeout + 1; })).toHaveLength(1);
    expect(tests((jobs) => { delete leg(jobs)["timeout-minutes"]; })).toHaveLength(1);
    expect(tests((jobs) => { leg(jobs)["timeout-minutes"] = "${{ 30 }}"; })).toHaveLength(1);
    // The gate's own watchdog the whole timeout, and a job that only a needed job needs.
    expect(tests((jobs) => { jobs["required-tests"]["timeout-minutes"] = timeout; }))
      .not.toEqual([]);
    expect(tests((jobs) => {
      jobs.before = { "timeout-minutes": timeout };
      leg(jobs).needs = ["before"];
    })).toEqual([expect.stringContaining("before " + timeout + " + platform-recorders")]);
  });

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
