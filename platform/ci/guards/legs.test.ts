/**
 * The platform's CI legs, held where they survive the cutover.
 *
 * `platform-tests` runs every workspace's Vitest tests as parallel legs, `platform-checks`
 * the legs that are not Vitest runs (the typecheck, the owner's and the gateway's memory
 * legs), and `platform-sizes` the large tests, sharded (`ci/classes.ts`). A matrix job's
 * result is every leg's, and `required-tests` (the protected context) fails unless all three,
 * the owner app's browser job and the `process` leg succeeded. Those five are the gate's
 * lasting shape: the old stack's Python jobs beside them leave at the cutover, and this guard
 * does not read them. `process` runs the process's own tests, `scripts/tests` alone, with
 * plain pytest on the runner and nothing of the old stack installed. Held here: every workspace `platform/package.json`
 * lists has its tests run by a leg, its `test` script whole or each `npm run` part of it;
 * every workspace with a large test has its `test:sizes` run; every Vitest leg names its
 * shard, `1/1` when whole, and every shard of a command has its leg; the typecheck has its
 * leg; every Vitest leg uploads its report (the input of `ci/budgets.mjs`) and no other leg
 * takes the reporters; no leg is excluded, conditional or allowed to fail; and each leg runs
 * exactly its command.
 *
 * Nothing may change how a command runs: the jobs and their steps carry only the keys listed
 * below, no `shell` (`shell: true {0}` makes every leg a no-op that succeeds), no `env`
 * (`npm_config_script_shell` does the same through npm), no `working-directory` of a step's
 * own; `defaults.run` is exactly the platform folder.
 *
 * A leg's `timeout-minutes` is a watchdog derived from its measured time, recorded beside it
 * in `tests.yml`. No figure is pinned here: a time limit is never a design input.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { keyed } from "../../contract/src/checks";
import { largeTests } from "../timeouts";
import { PLATFORM, REPO } from "../paths";
import { type Data, json, workflow } from "../workflows";

const SETUP = [
  // Full history, no file contents until read: the migration lock's guard reads its base.
  { uses: "actions/checkout@v4", with: { "fetch-depth": 0, filter: "blob:none" } },
  { uses: "actions/setup-node@v4",
    with: { "node-version": "22", cache: "npm",
            "cache-dependency-path": "platform/package-lock.json" } },
  { run: "npm ci" },
];
const COMMAND = "${{ matrix.command }}";
/** A Vitest leg's reporters: Vitest's two on a GitHub runner, and its JSON report. */
const REPORTERS = "--reporter=default --reporter=github-actions --reporter=json " +
  "--outputFile.json=${{ runner.temp }}/report.json";
/** The upload of a Vitest leg's JSON report, the input `ci/budgets.mjs` reads. */
function upload(name: string): Data {
  return { uses: "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
           with: { name: name + "-${{ strategy.job-index }}",
                   path: "${{ runner.temp }}/report.json", "if-no-files-found": "error",
                   overwrite: true } };
}
/**
 * Each leg job's steps. The Vitest legs also upload their JSON report, once their tests have
 * passed. A Vitest leg's command ends in its shard, after its own `--`, so the step's
 * reporter flags reach Vitest too.
 */
const STEPS: Record<string, Data[]> = {
  "platform-tests": [...SETUP, { run: COMMAND + " " + REPORTERS }, upload("tests-report")],
  "platform-checks": [...SETUP, { run: COMMAND }],
  "platform-sizes": [...SETUP, { run: COMMAND + " " + REPORTERS }, upload("sizes-report")],
};
/** The jobs whose legs run, between them, every workspace's tests and the typecheck. */
const LEG_JOBS = ["platform-tests", "platform-checks"];
const JOB_KEYS = ["defaults", "name", "runs-on", "steps", "strategy", "timeout-minutes"];

function tests(): Data {
  return workflow("tests.yml");
}

/** Each way a leg job could run something other than its legs' commands, whole. */
export function legJobProblems(job: Data, name: string): string[] {
  const found: string[] = [];
  if (!keyed(job, JOB_KEYS)) {
    found.push(`${name} carries keys that may change its legs: ` +
               Object.keys(job ?? {}).sort().join(", "));
  }
  if (job?.name !== name) {
    found.push(`${name} is named ${job?.name}`);
  }
  if (job?.["runs-on"] !== "ubuntu-latest") {
    found.push(`${name} runs on ${job?.["runs-on"]}`);
  }
  if (!Number.isInteger(job?.["timeout-minutes"]) || job["timeout-minutes"] < 1) {
    found.push(`${name} has no watchdog timeout-minutes`);
  }
  if (JSON.stringify(job?.defaults) !== JSON.stringify({ run: { "working-directory": "platform" } })) {
    found.push(`${name}'s defaults.run must be the platform folder alone (no shell)`);
  }
  const strategy = job?.strategy ?? {};
  if (!keyed(strategy, [], ["fail-fast", "matrix"])) {
    found.push(`${name}'s strategy carries ${Object.keys(strategy).join(", ")}`);
  }
  if (!keyed(strategy.matrix, ["include"])) {
    found.push(`${name}'s legs are listed whole in include: no axes, no exclude to drop one`);
  }
  const legs: Data[] = strategy.matrix?.include ?? [];
  if (!legs.every((leg) => keyed(leg, ["leg", "command"]))) {
    found.push(`${name}'s legs carry keys other than leg and command`);
  }
  if (new Set(legs.map((leg) => leg.leg)).size !== legs.length) {
    found.push(`${name} names a leg twice`);
  }
  if (new Set(legs.map((leg) => leg.command)).size !== legs.length) {
    found.push(`two of ${name}'s legs run the same command`);
  }
  if (JSON.stringify(job?.steps) !== JSON.stringify(STEPS[name])) {
    found.push(`${name}'s steps are checkout, setup-node, npm ci and the leg's command, ` +
               "each alone, and for the sizes the report's upload");
  }
  return found;
}

function commands(...jobs: Data[]): Set<string> {
  return new Set(jobs.flatMap((job) => (job?.strategy?.matrix?.include ?? [])
    .map((leg: Data) => leg.command)));
}

/**
 * The commands a Vitest job's legs run, each whole across its shards, and each way the legs
 * fall short of that: a leg that names no shard, legs that disagree on a command's shard
 * count, and a shard no leg runs.
 */
export function sharded(job: Data): { whole: Set<string>; found: string[] } {
  const found: string[] = [];
  const shards = new Map<string, { n: number; seen: Set<number> }>();
  for (const command of commands(job)) {
    const match = /^(npm run [\w:-]+ -w [\w-]+) -- --shard=(\d+)\/(\d+)$/.exec(command);
    if (match === null) {
      found.push(`a ${job?.name} leg names no shard (1/1 when whole): ${command}`);
      continue;
    }
    const [, whole, i, n] = match;
    const entry = shards.get(whole!) ?? { n: Number(n), seen: new Set<number>() };
    if (entry.n !== Number(n)) {
      found.push(`${whole}'s legs disagree on their shard count`);
    }
    entry.seen.add(Number(i));
    shards.set(whole!, entry);
  }
  for (const [whole, { n, seen }] of shards) {
    for (let i = 1; i <= n; i++) {
      if (!seen.has(i)) {
        found.push(`${whole}'s shard ${i}/${n} has no leg`);
      }
    }
  }
  return { whole: new Set(shards.keys()), found };
}

/** Workspaces whose tests no leg of `jobs`' leg jobs runs, or legs no workspace's tests are. */
export function coverageProblems(jobs: Data): string[] {
  const tests = sharded(jobs["platform-tests"]);
  const found: string[] = [...tests.found];
  const manifest = json("package.json");
  const have = new Set([...tests.whole, ...commands(jobs["platform-checks"])]);
  if (manifest.scripts.typecheck !== "tsc --noEmit" || !have.has("npm run typecheck")) {
    found.push("the typecheck has no leg");
  }
  if (manifest.scripts.test !== "npm run test --workspaces") {
    found.push("platform's test script is not every workspace's");
  }
  const covered = new Set(["npm run typecheck"]);
  for (const ws of manifest.workspaces as string[]) {
    const scripts = json(`${ws}/package.json`).scripts;
    if (have.has(`npm run test -w ${ws}`)) {
      covered.add(`npm run test -w ${ws}`);
      continue;
    }
    const parts = String(scripts.test).split("&&").map((part) => part.trim());
    if (!parts.every((part) => /^npm run [\w:-]+$/.test(part))) {
      found.push(`${ws}'s tests have no leg: its test script is not run by a leg, nor only ` +
                 `npm run parts each a leg could run: ${scripts.test}`);
      continue;
    }
    for (const part of parts) {
      const command = `${part} -w ${ws}`;
      if (!have.has(command)) {
        found.push(`${ws}'s ${part} has no leg`);
      }
      covered.add(command);
    }
  }
  for (const command of have) {
    if (!covered.has(command)) {
      found.push(`a leg runs what no workspace's tests are: ${command}`);
    }
  }
  return found;
}

/** A script that runs Vitest: `vitest run`, or the runner's root wrapper around it. */
const VITEST = /\bvitest run\b|root-tests\.mjs/;

/** The script an `npm run <script>[ -w <workspace>]` leg runs. */
function script(command: string): string {
  const match = /^npm run ([\w:-]+)(?: -w ([\w-]+))?$/.exec(command);
  const manifest = match?.[2] === undefined ? "package.json" : `${match[2]}/package.json`;
  return String(match === null ? "" : json(manifest).scripts[match[1]!]);
}

/**
 * Every Vitest leg is in `platform-tests`, which uploads its report, and no other leg is
 * there: a Vitest leg among the checks would measure nothing, and the reporters break any
 * other leg.
 */
export function reportProblems(jobs: Data): string[] {
  const found: string[] = [];
  for (const command of sharded(jobs["platform-tests"]).whole) {
    if (!VITEST.test(script(command))) {
      found.push(`a platform-tests leg is not a Vitest run, so takes no reporter: ${command}`);
    }
  }
  for (const command of commands(jobs["platform-checks"])) {
    if (VITEST.test(script(command))) {
      found.push(`a Vitest leg uploads no report from platform-checks: ${command}`);
    }
  }
  return found;
}

/** The workspaces that hold a large test, from their test files' text. */
export function workspacesWithLarge(): string[] {
  const files = execFileSync("git", ["-C", REPO, "ls-files", "platform/*.test.ts"],
                             { encoding: "utf8" }).split("\n").filter(Boolean);
  const found = new Set<string>();
  for (const file of files) {
    if (largeTests(readFileSync(join(REPO, file), "utf8"), file).length > 0) {
      found.add(file.split("/")[1]!);
    }
  }
  return [...found].sort();
}

/** Every workspace with a large test has every shard of its `test:sizes` run, nothing else. */
export function sizesProblems(job: Data, withLarge: string[]): string[] {
  const { whole, found } = sharded(job);
  const sized = new Set<string>();
  for (const command of whole) {
    const match = /^npm run test:sizes -w ([\w-]+)$/.exec(command);
    if (match === null) {
      found.push(`a sizes leg runs something other than a workspace's test:sizes: ${command}`);
    } else {
      sized.add(match[1]!);
    }
  }
  for (const ws of withLarge) {
    if (!sized.has(ws)) {
      found.push(`${ws} holds a large test that no sizes leg runs`);
    }
  }
  return found;
}

/** Each workspace's test scripts leave the large tests out, and its sizes script runs them. */
export function tagFilterProblems(): string[] {
  const found: string[] = [];
  for (const ws of json("package.json").workspaces as string[]) {
    const scripts: Record<string, string> = json(`${ws}/package.json`).scripts;
    for (const [name, script] of Object.entries(scripts)) {
      if (!VITEST.test(script)) {
        continue;
      }
      const wanted = name === "test:sizes" ? "--tags-filter=large" : "--tags-filter=!large";
      if (!script.includes(wanted)) {
        found.push(`${ws}'s ${name} must run ${wanted}: ${script}`);
      }
    }
  }
  return found;
}

describe("platform-tests, platform-checks and platform-sizes", () => {
  it("run every workspace's tests, whole, each leg its command alone", () => {
    const jobs = tests().jobs;
    for (const name of [...LEG_JOBS, "platform-sizes"]) {
      expect(legJobProblems(jobs[name], name)).toEqual([]);
    }
    expect(coverageProblems(jobs)).toEqual([]);
    expect(reportProblems(jobs)).toEqual([]);
    expect(sizesProblems(jobs["platform-sizes"], workspacesWithLarge())).toEqual([]);
    expect(tagFilterProblems()).toEqual([]);
  });

  it("run the owner's and the gateway's workerd memory legs, each with its control", () => {
    // ADR-037: the differential workerd checks that each chunk-wise path is O(1) in its
    // data's size, and the egress door's and the gateway's, each with a control that must
    // read as growing. Each leg's own run fails without its `--expose-gc` (its `gc()` throws).
    const legs = tests().jobs["platform-checks"].strategy.matrix.include;
    for (const [ws, script, control] of [["owner", "leg.mjs", "the control kept"],
                                         ["gateway", "egress.mjs", "control held"]]) {
      expect(legs).toContainEqual({ leg: `${ws} memory`,
                                    command: `npm run test:memory -w ${ws}` });
      expect(json(`${ws}/package.json`).scripts["test:memory"]).toBe(`node memory/${script}`);
      const leg = readFileSync(join(PLATFORM, ws!, "memory", script!), "utf8");
      expect(leg).toContain(control);
    }
  });

  type Mutation = (job: Data) => void;
  const step = (index: number, extra: object): Mutation => (job) => {
    Object.assign(job.steps[index], extra);
  };
  const legs = (job: Data): Data[] => job.strategy.matrix.include;
  const drop = (name: string): Mutation => (job) => {
    job.strategy.matrix.include = legs(job).filter((leg) => leg.leg !== name);
  };
  const command = (name: string, text: string): Mutation => (job) => {
    for (const leg of legs(job)) {
      if (leg.leg === name) {
        leg.command = text;
      }
    }
  };
  const move = (name: string, to: string): ((jobs: Data) => void) => (jobs) => {
    const from = LEG_JOBS.find((job) => legs(jobs[job]).some((leg) => leg.leg === name))!;
    legs(jobs[to]).push(...legs(jobs[from]).filter((leg) => leg.leg === name));
    drop(name)(jobs[from]);
  };
  // Each a job that must fail, in each leg job.
  const mutations: Record<string, Mutation> = {
    "job continue-on-error": (job) => { job["continue-on-error"] = true; },
    "job if": (job) => { job.if = "github.event_name != 'pull_request'"; },
    "job env makes npm scripts no-ops": (job) => { job.env = { npm_config_script_shell: "true" }; },
    "step if skips runner": step(3, { if: "matrix.leg != 'runner'" }),
    "step continue-on-error": step(3, { "continue-on-error": true }),
    "step shell that swallows failure": step(3, { shell: "bash {0}" }),
    "step shell that runs nothing (true {0})": step(3, { shell: "true {0}" }),
    "npm ci step shell that runs nothing": step(2, { shell: "true {0}" }),
    "step env makes npm scripts no-ops": step(3, { env: { npm_config_script_shell: "true" } }),
    "step working-directory elsewhere": step(3, { "working-directory": "platform/owner" }),
    "job default shell that runs nothing": (job) => { job.defaults.run.shell = "true {0}"; },
    "job default working-directory elsewhere": (job) => {
      job.defaults.run["working-directory"] = ".";
    },
    "an extra step after the command": (job) => { job.steps.push({ run: "true" }); },
    "a shallow checkout": (job) => { job.steps[0].with["fetch-depth"] = 1; },
    "no watchdog": (job) => { delete job["timeout-minutes"]; },
    "an exclude": (job) => { job.strategy.matrix.exclude = [{ leg: "runner" }]; },
  };
  // Each a leg dropped, changed or moved to the wrong job, which must fail.
  const legMutations: Record<string, (jobs: Data) => void> = {
    "drop codemode leg": (jobs) => drop("codemode")(jobs["platform-tests"]),
    "drop runner leg": (jobs) => drop("runner")(jobs["platform-tests"]),
    "drop ci leg": (jobs) => drop("ci")(jobs["platform-tests"]),
    "drop typecheck leg": (jobs) => drop("typecheck")(jobs["platform-checks"]),
    "drop the owner's memory leg": (jobs) => drop("owner memory")(jobs["platform-checks"]),
    "drop the gateway's memory leg": (jobs) =>
      drop("gateway memory")(jobs["platform-checks"]),
    "runner leg runs workerd twice": (jobs) =>
      command("runner", "npm run test:workerd -w sandbox --silent")(jobs["platform-tests"]),
    "a leg's command swallowed": (jobs) =>
      command("doors", "npm run test -w doors -- --shard=1/1 || true")(jobs["platform-tests"]),
    "drop an owner shard": (jobs) => drop("owner 2/2")(jobs["platform-tests"]),
    "a leg that names no shard": (jobs) =>
      command("doors", "npm run test -w doors")(jobs["platform-tests"]),
    "the typecheck given the reporters": move("typecheck", "platform-tests"),
    "the memory leg given the reporters": move("gateway memory", "platform-tests"),
    "a Vitest leg that uploads no report": move("doors", "platform-checks"),
  };
  const problems = (jobs: Data): string[] => [
    ...LEG_JOBS.flatMap((name) => legJobProblems(jobs[name], name)),
    ...coverageProblems(jobs), ...reportProblems(jobs)];
  for (const job of LEG_JOBS) {
    for (const [name, mutate] of Object.entries(mutations)) {
      it(`catch the mutation in ${job}: ${name}`, () => {
        const jobs = structuredClone(tests().jobs);
        mutate(jobs[job]);
        expect(problems(jobs)).not.toEqual([]);
      });
    }
  }
  for (const [name, mutate] of Object.entries(legMutations)) {
    it(`catch the mutation: ${name}`, () => {
      const jobs = structuredClone(tests().jobs);
      mutate(jobs);
      expect(problems(jobs)).not.toEqual([]);
    });
  }

  it("catch a dropped or stray sizes shard and an unrun large test", () => {
    const job = structuredClone(tests().jobs["platform-sizes"]);
    const withLarge = workspacesWithLarge();
    expect(withLarge.length).toBeGreaterThan(0);
    const dropped = structuredClone(job);
    dropped.strategy.matrix.include = legs(dropped).slice(1);
    expect(sizesProblems(dropped, withLarge)).not.toEqual([]);
    const stray = structuredClone(job);
    legs(stray)[0].command = "npm run test -w owner";
    expect(sizesProblems(stray, withLarge)).not.toEqual([]);
    expect(sizesProblems(job, [...withLarge, "doors-not-in-sizes"])).not.toEqual([]);
  });
});

describe("required-tests", () => {
  const RESULTS = { PLATFORM_RESULT: "${{ needs.platform-tests.result }}",
                    CHECKS_RESULT: "${{ needs.platform-checks.result }}",
                    APP_RESULT: "${{ needs.platform-app.result }}",
                    SIZES_RESULT: "${{ needs.platform-sizes.result }}",
                    PROCESS_RESULT: "${{ needs.process.result }}" };

  it("needs the platform's jobs and the process leg, each run on every event and never " +
     "allowed to fail", () => {
    const jobs = tests().jobs;
    const agg = jobs["required-tests"];
    expect(agg.if).toBe("always()");
    expect(agg["continue-on-error"]).toBeUndefined();
    for (const name of [...LEG_JOBS, "platform-app", "platform-sizes", "process"]) {
      expect(agg.needs).toContain(name);
      expect(jobs[name].if, `${name} must run on every event`).toBeUndefined();
      expect(jobs[name].needs, `${name} must wait on no other job`).toBeUndefined();
      expect(jobs[name]["continue-on-error"]).toBeUndefined();
      for (const each of jobs[name].steps) {
        expect(each.if, `a step of ${name} is conditional`).toBeUndefined();
        expect(each["continue-on-error"]).toBeUndefined();
      }
    }
  });

  it("fails unless every one of them succeeded, by running the gate step's own script", () => {
    const steps: Data[] = tests().jobs["required-tests"].steps;
    const gate = steps.filter((each) => "PLATFORM_RESULT" in (each.env ?? {}));
    expect(gate).toHaveLength(1);
    expect(gate[0].if).toBeUndefined();
    expect(gate[0]["continue-on-error"]).toBeUndefined();
    expect(gate[0].env).toEqual(RESULTS);
    const names = Object.keys(RESULTS);
    const results = ["success", "failure", "skipped", "cancelled", ""];
    const status = (values: string[]): number | null => spawnSync("bash", ["-s"], {
      input: names.map((name, i) => `${name}=${values[i]}\n`).join("") + gate[0].run,
    }).status;
    // Every pair of results at every value, the rest succeeding: 250 runs of the script.
    for (let a = 0; a < names.length; a++) {
      for (let b = a + 1; b < names.length; b++) {
        for (const first of results) {
          for (const second of results) {
            const values = names.map((_, i) => (i === a ? first : i === b ? second : "success"));
            const passed = first === "success" && second === "success";
            expect(status(values), `${names[a]}=${first} ${names[b]}=${second}`)
              .toBe(passed ? 0 : 1);
          }
        }
      }
    }
    expect(status(names.map(() => "failure"))).toBe(1);
  });
});

describe("process", () => {
  const INSTALL = "pip install -r scripts/process-requirements.txt";
  const PYTEST = "python -m pytest --noconftest -q -p no:cacheprovider";

  /** Each way the leg could run something other than the process's tests, whole. */
  function processProblems(job: Data): string[] {
    const found: string[] = [];
    if (!keyed(job, ["name", "runs-on", "steps", "timeout-minutes"])) {
      found.push("process carries keys that may change what it runs: " +
                 Object.keys(job ?? {}).sort().join(", "));
    }
    if (job?.name !== "process" || job?.["runs-on"] !== "ubuntu-latest") {
      found.push("process is one job named process, on the plain runner");
    }
    const steps: Data[] = job?.steps ?? [];
    const shapes = [["uses"], ["uses", "with"], ["run"], ["run"]];
    if (steps.length !== shapes.length || !shapes.every((shape, at) => keyed(steps[at], shape))) {
      found.push("process's steps are checkout, setup-python, the install and pytest, each " +
                 `alone: ${steps.map((each) => Object.keys(each).sort().join(",")).join(" | ")}`);
      return found;
    }
    if (steps[2].run !== INSTALL) {
      found.push(`process installs its own requirements and nothing else: ${steps[2].run}`);
    }
    const words = String(steps[3].run).trim().split(/\s+/);
    const flags = PYTEST.split(" ").length;
    if (words.slice(0, flags).join(" ") !== PYTEST) {
      found.push(`process runs plain pytest with no conftest: ${words.slice(0, flags).join(" ")}`);
    }
    const targets = words.slice(flags).join(" ");
    if (targets !== "scripts/tests") {
      found.push(`process runs scripts/tests alone: ${targets}`);
    }
    return found;
  }

  it("runs the process's own tests with plain pytest and nothing of the old stack", () => {
    expect(processProblems(tests().jobs.process)).toEqual([]);
    // pytest and a YAML reader: nothing that installs the old stack's package.
    const wanted = readFileSync(join(REPO, "scripts", "process-requirements.txt"), "utf8")
      .split("\n").filter((line) => line.trim() !== "" && !line.startsWith("#"));
    expect(wanted.map((line) => line.split(/[<>=!~ ]/)[0])).toEqual(["pytest", "pyyaml"]);
  });

  type Mutation = (job: Data) => void;
  const mutations: Record<string, Mutation> = {
    "job if": (job) => { job.if = "github.event_name != 'pull_request'"; },
    "job continue-on-error": (job) => { job["continue-on-error"] = true; },
    "waits on the selection": (job) => { job.needs = "select"; },
    "installs the old stack": (job) => { job.steps[2].run = "pip install -e '.[dev]'"; },
    "pytest swallowed": (job) => { job.steps[3].run += " || true"; },
    "loads the old stack's conftest": (job) => {
      job.steps[3].run = job.steps[3].run.replace("--noconftest ", "");
    },
    "a step shell that runs nothing": (job) => { job.steps[3].shell = "true {0}"; },
    "a test from elsewhere": (job) => { job.steps[3].run += " scripts/other_test.py"; },
    "no test of its own": (job) => { job.steps[3].run = PYTEST; },
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    it(`catch the mutation: ${name}`, () => {
      const job = structuredClone(tests().jobs.process);
      mutate(job);
      expect(processProblems(job)).not.toEqual([]);
    });
  }
});
