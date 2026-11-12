/**
 * The platform's CI legs, held where they survive the cutover (moved from the Python suite's
 * `tests/test_tests_workflow.py`, audit D4).
 *
 * `platform-tests` runs every workspace's tests as parallel legs and `platform-sizes` runs
 * the large ones, sharded (`ci/classes.ts`). A matrix job's result is every leg's, and
 * `required-tests` (the protected context) fails unless both, the owner app's browser job
 * and the `process` leg succeeded. Those four are the gate's lasting shape: the old stack's
 * Python jobs beside them leave at the cutover, and this guard does not read them. `process`
 * runs the process's own tests with plain pytest on the runner, with nothing of the old
 * stack installed. Held here: every workspace `platform/package.json` lists has its tests run
 * by a leg, its `test` script whole or each `npm run` part of it; every workspace with a
 * large test has its `test:sizes` run, every shard of it, each writing its Vitest JSON report
 * and its leg uploading it (what `ci/budgets.mjs` reads); the typecheck has its leg; no leg
 * is excluded, conditional or allowed to fail; and each leg runs exactly its command.
 * Nothing may change how a command runs (round 8, R8-2): the jobs and their steps carry only
 * the keys listed below, no `shell` (`shell: true {0}` makes every leg a no-op that
 * succeeds), no `env` (`npm_config_script_shell` does the same through npm), no
 * `working-directory` of a step's own; `defaults.run` is exactly the platform folder.
 *
 * A leg's `timeout-minutes` is a watchdog derived from its measured time, recorded beside it
 * in `tests.yml`. No figure is pinned here: a time limit is never a design input.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { largeTests } from "../timeouts";
import { PLATFORM, REPO } from "../paths";
import { type Data, json, workflow } from "../workflows";

const STEPS = [
  // Full history, no file contents until read: the migration lock's guard reads its base.
  { uses: "actions/checkout@v4", with: { "fetch-depth": 0, filter: "blob:none" } },
  { uses: "actions/setup-node@v4",
    with: { "node-version": "22", cache: "npm",
            "cache-dependency-path": "platform/package-lock.json" } },
  { run: "npm ci" },
  { run: "${{ matrix.command }}" },
];
/** The report each `test:sizes` writes in its workspace, for `ci/budgets.mjs`. */
export const SIZES_REPORT = "sizes-report.json";
/** A sizes leg's steps: a leg's, then its report uploaded, one artifact a leg. */
const SIZES_STEPS = [
  ...STEPS,
  { uses: "actions/upload-artifact@v4",
    with: { name: "sizes-report-${{ strategy.job-index }}",
            path: `platform/*/${SIZES_REPORT}`, "if-no-files-found": "error" } },
];
const JOB_KEYS = ["defaults", "name", "runs-on", "steps", "strategy", "timeout-minutes"];

function tests(): Data {
  return workflow("tests.yml");
}

/** Each way a leg job could run something other than its legs' commands, whole. */
export function legJobProblems(job: Data, name: string, steps: readonly Data[]): string[] {
  const found: string[] = [];
  const keys = Object.keys(job ?? {}).sort();
  if (JSON.stringify(keys) !== JSON.stringify(JOB_KEYS)) {
    found.push(`${name} carries keys that may change its legs: ${keys.join(", ")}`);
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
  if (Object.keys(strategy).some((key) => !["fail-fast", "matrix"].includes(key))) {
    found.push(`${name}'s strategy carries ${Object.keys(strategy).join(", ")}`);
  }
  if (JSON.stringify(Object.keys(strategy.matrix ?? {})) !== JSON.stringify(["include"])) {
    found.push(`${name}'s legs are listed whole in include: no axes, no exclude to drop one`);
  }
  const legs: Data[] = strategy.matrix?.include ?? [];
  if (!legs.every((leg) => JSON.stringify(Object.keys(leg).sort()) === '["command","leg"]')) {
    found.push(`${name}'s legs carry keys other than leg and command`);
  }
  if (new Set(legs.map((leg) => leg.leg)).size !== legs.length) {
    found.push(`${name} names a leg twice`);
  }
  if (new Set(legs.map((leg) => leg.command)).size !== legs.length) {
    found.push(`two of ${name}'s legs run the same command`);
  }
  if (JSON.stringify(job?.steps) !== JSON.stringify(steps)) {
    found.push(`${name}'s steps are ${steps.map((step) => step["uses"] ?? step["run"]).join(", ")
      }, each alone`);
  }
  return found;
}

function commands(job: Data): Set<string> {
  return new Set((job?.strategy?.matrix?.include ?? []).map((leg: Data) => leg.command));
}

/** Workspaces whose tests no `platform-tests` leg runs, or legs no workspace's tests are. */
export function coverageProblems(job: Data): string[] {
  const found: string[] = [];
  const manifest = json("package.json");
  const have = commands(job);
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
  const found: string[] = [];
  const shards = new Map<string, { n: number; seen: Set<number> }>();
  for (const command of commands(job)) {
    const match = /^npm run test:sizes -w ([\w-]+)(?: -- --shard=(\d+)\/(\d+))?$/.exec(command);
    if (match === null) {
      found.push(`a sizes leg runs something other than a workspace's test:sizes: ${command}`);
      continue;
    }
    const [, ws, i = "1", n = "1"] = match;
    const entry = shards.get(ws!) ?? { n: Number(n), seen: new Set<number>() };
    if (entry.n !== Number(n)) {
      found.push(`${ws}'s sizes legs disagree on their shard count`);
    }
    entry.seen.add(Number(i));
    shards.set(ws!, entry);
  }
  for (const [ws, { n, seen }] of shards) {
    for (let i = 1; i <= n; i++) {
      if (!seen.has(i)) {
        found.push(`${ws}'s sizes shard ${i}/${n} has no leg`);
      }
    }
  }
  for (const ws of withLarge) {
    if (!shards.has(ws)) {
      found.push(`${ws} holds a large test that no sizes leg runs`);
    }
  }
  return found;
}

/**
 * A workspace's test script's problems: each leaves the large tests out, and its sizes
 * script runs them and writes its JSON report.
 */
export function scriptProblems(ws: string, name: string, script: string): string[] {
  if (!/\bvitest run\b|root-tests\.mjs/.test(script)) {
    return [];
  }
  const wanted = name === "test:sizes"
    ? ["--tags-filter=large", "--reporter=default", "--reporter=json",
       `--outputFile.json=${SIZES_REPORT}`]
    : ["--tags-filter=!large"];
  return wanted.filter((flag) => !script.split(" ").includes(flag))
    .map((flag) => `${ws}'s ${name} must run ${flag}: ${script}`);
}

/** Every workspace's test scripts' problems (`scriptProblems`). */
export function tagFilterProblems(): string[] {
  return (json("package.json").workspaces as string[]).flatMap((ws) =>
    Object.entries(json(`${ws}/package.json`).scripts as Record<string, string>)
      .flatMap(([name, script]) => scriptProblems(ws, name, script)));
}

describe("platform-tests and platform-sizes", () => {
  it("run every workspace's tests, whole, each leg its command alone", () => {
    const jobs = tests().jobs;
    expect(legJobProblems(jobs["platform-tests"], "platform-tests", STEPS)).toEqual([]);
    expect(legJobProblems(jobs["platform-sizes"], "platform-sizes", SIZES_STEPS)).toEqual([]);
    expect(coverageProblems(jobs["platform-tests"])).toEqual([]);
    expect(sizesProblems(jobs["platform-sizes"], workspacesWithLarge())).toEqual([]);
    expect(tagFilterProblems()).toEqual([]);
  });

  it("run the owner's workerd memory leg, with its control", () => {
    // ADR-037: the differential workerd check that each chunk-wise path is O(1) in its
    // data's size, with a control that must read as growing.
    const legs = tests().jobs["platform-tests"].strategy.matrix.include;
    expect(legs).toContainEqual({ leg: "memory", command: "npm run test:memory -w owner" });
    expect(json("owner/package.json").scripts["test:memory"]).toBe("node memory/leg.mjs");
    const leg = readFileSync(join(PLATFORM, "owner", "memory", "leg.mjs"), "utf8");
    expect(leg).toContain("the control kept");
    expect(leg).toContain("--expose-gc");
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
  // The reviewer's mutations (round 8), each a job that must fail.
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
    "drop codemode leg": drop("codemode"),
    "drop runner leg": drop("runner"),
    "drop typecheck leg": drop("typecheck"),
    "drop ci leg": drop("ci"),
    "runner leg runs workerd twice": command("runner", "npm run test:workerd -w sandbox --silent"),
    "a leg's command swallowed": command("owner", "npm run test -w owner || true"),
    "no watchdog": (job) => { delete job["timeout-minutes"]; },
    "an exclude": (job) => { job.strategy.matrix.exclude = [{ leg: "runner" }]; },
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    it(`catch the mutation: ${name}`, () => {
      const job = structuredClone(tests().jobs["platform-tests"]);
      mutate(job);
      expect([...legJobProblems(job, "platform-tests", STEPS), ...coverageProblems(job)])
        .not.toEqual([]);
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

  it("catch a sizes leg that uploads no report, or another file, and a sizes script that " +
     "writes none", () => {
    const job = tests().jobs["platform-sizes"];
    const unsent = structuredClone(job);
    unsent.steps.pop();
    const elsewhere = structuredClone(job);
    elsewhere.steps.at(-1).with.path = "platform/*/report.json";
    for (const broken of [unsent, elsewhere]) {
      expect(legJobProblems(broken, "platform-sizes", SIZES_STEPS)).not.toEqual([]);
    }
    const sizes = json("owner/package.json").scripts["test:sizes"] as string;
    expect(scriptProblems("owner", "test:sizes", sizes)).toEqual([]);
    for (const flag of ["--reporter=json", `--outputFile.json=${SIZES_REPORT}`]) {
      expect(scriptProblems("owner", "test:sizes", sizes.replace(flag, "")))
        .not.toEqual([]);
    }
  });
});

describe("required-tests", () => {
  const RESULTS = { PLATFORM_RESULT: "${{ needs.platform-tests.result }}",
                    APP_RESULT: "${{ needs.platform-app.result }}",
                    SIZES_RESULT: "${{ needs.platform-sizes.result }}",
                    PROCESS_RESULT: "${{ needs.process.result }}" };

  it("needs the platform's jobs and the process leg, each run on every event and never " +
     "allowed to fail", () => {
    const jobs = tests().jobs;
    const agg = jobs["required-tests"];
    expect(agg.if).toBe("always()");
    expect(agg["continue-on-error"]).toBeUndefined();
    for (const name of ["platform-tests", "platform-app", "platform-sizes", "process"]) {
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
    // Every pair of results at every value, the rest succeeding: 150 runs of the script.
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
    const keys = Object.keys(job ?? {}).sort();
    if (JSON.stringify(keys) !== JSON.stringify(["name", "runs-on", "steps", "timeout-minutes"])) {
      found.push(`process carries keys that may change what it runs: ${keys.join(", ")}`);
    }
    if (job?.name !== "process" || job?.["runs-on"] !== "ubuntu-latest") {
      found.push("process is one job named process, on the plain runner");
    }
    const steps: Data[] = job?.steps ?? [];
    const shape = steps.map((each) => Object.keys(each).sort().join(","));
    if (JSON.stringify(shape) !== JSON.stringify(["uses", "uses,with", "run", "run"])) {
      found.push("process's steps are checkout, setup-python, the install and pytest, each " +
                 `alone: ${shape.join(" | ")}`);
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
    const targets = words.slice(flags);
    if (targets[0] !== "scripts/tests" || targets.length < 2) {
      found.push("process runs scripts/tests and the process's tests still under tests/");
    }
    for (const target of targets) {
      if (!/^(scripts\/tests|tests\/test_\w+\.py)$/.test(target) ||
          !existsSync(join(REPO, target))) {
        found.push(`process names something that is not a test file here: ${target}`);
      }
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
    "a test file that is gone": (job) => { job.steps[3].run += " tests/test_gone_file.py"; },
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
