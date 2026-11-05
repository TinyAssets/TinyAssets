#!/usr/bin/env node
/**
 * Read Vitest JSON reports (`--reporter=json --outputFile=<f>`) and either name each test
 * over half its class's budget (the default, which blocks nothing), or, with `--write`,
 * recompute `budgets.json` from them: each project's slowest medium test, and the slowest
 * large one (`classes.ts` has the rule).
 *
 *   node ci/budgets.mjs [--write] [--measured "<where and when>"] <report.json>...
 *
 * A test's project is read from its path: `<workspace>/test/` is `workerd`,
 * `<workspace>/guards/` is `guards`, `sandbox/runner/` is `runner`, as the configs name
 * them. Only passed tests count; a failed or skipped one measures nothing.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const CI = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(CI, "..");
const BUDGETS = join(CI, "budgets.json");
const SMALL_MS = 5_000;
const PROJECTS = { test: "workerd", guards: "guards", runner: "runner" };

function derived(slowest, factor) {
  return Math.max(SMALL_MS, Math.ceil((factor * slowest.slowest_ms) / 1000) * 1000);
}

/** Every passed test in `reports` as { project, test, ms, large }. */
export function measured(reports) {
  const out = [];
  for (const report of reports) {
    for (const file of report.testResults ?? []) {
      const rel = relative(PLATFORM, file.name).split("\\").join("/");
      const [workspace, dir] = rel.split("/");
      const project = PROJECTS[dir];
      if (project === undefined) {
        throw new Error(`${rel}: not in a test, guards or runner folder`);
      }
      for (const result of file.assertionResults ?? []) {
        if (result.status !== "passed" || typeof result.duration !== "number") {
          continue;
        }
        out.push({ project: `${workspace}/${project}`, test: `${rel} > ${result.fullName}`,
                   ms: result.duration, large: (result.tags ?? []).includes("large") });
      }
    }
  }
  return out;
}

function slowest(rows) {
  const top = rows.reduce((a, b) => (b.ms > a.ms ? b : a));
  return { slowest_ms: Math.ceil(top.ms), test: top.test };
}

function main(argv) {
  const write = argv.includes("--write");
  const at = argv.indexOf("--measured");
  const where = at === -1 ? null : argv[at + 1];
  const files = argv.filter((arg, i) => !arg.startsWith("--") && (at === -1 || i !== at + 1));
  const rows = measured(files.map((file) => JSON.parse(readFileSync(file, "utf8"))));
  const budgets = JSON.parse(readFileSync(BUDGETS, "utf8"));
  if (write) {
    if (where === null) {
      throw new Error("--write needs --measured \"<where and when>\"");
    }
    const medium = { ...budgets.medium };
    for (const project of new Set(rows.filter((r) => !r.large).map((r) => r.project))) {
      medium[project] = slowest(rows.filter((r) => !r.large && r.project === project));
    }
    const large = rows.filter((r) => r.large);
    const next = { ...budgets, measured: where,
                   medium: Object.fromEntries(Object.entries(medium).sort()),
                   large: large.length ? slowest(large) : budgets.large };
    writeFileSync(BUDGETS, `${JSON.stringify(next, null, 2)}\n`);
    return 0;
  }
  const lines = [];
  for (const row of rows) {
    const limit = row.large ? derived(budgets.large, budgets.factor)
      : budgets.medium[row.project] && derived(budgets.medium[row.project], budgets.factor);
    if (limit !== undefined && row.ms > limit / 2) {
      lines.push(`- ${row.test}: ${Math.round(row.ms)} ms, over half its ` +
                 `${row.large ? "large" : row.project} budget of ${limit} ms`);
    }
  }
  console.log(lines.length ? ["Tests over half their class's budget:", ...lines].join("\n")
    : "No test is over half its class's budget.");
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
