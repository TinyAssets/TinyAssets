#!/usr/bin/env node
/**
 * Recompute `budgets.json` from Vitest JSON reports (`--reporter=json --outputFile=<f>`):
 * each project's slowest medium test, and the slowest large one (`classes.ts` has the rule).
 *
 *   node ci/budgets.mjs --measured "<where and when>" <report.json>...
 *
 * A test's project is read from its path: the deepest folder of a project's that holds it
 * (`classes.ts`'s `projectOf`, from `PROJECTS`, which every config's projects are made from). Only passed
 * tests count; a failed or skipped one measures nothing.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { projectOf } from "./classes.ts";
import { PLATFORM } from "./paths.ts";

const CI = dirname(fileURLToPath(import.meta.url));
const BUDGETS = join(CI, "budgets.json");
const BELOW = `/${basename(PLATFORM)}/`;

/**
 * A test file's path below the platform folder of the checkout that ran it: a report from CI
 * (the `platform-sizes` legs' artifacts) names the runner's checkout, not this one.
 */
function fromPlatform(name) {
  const path = name.split("\\").join("/");
  const at = path.lastIndexOf(BELOW);
  if (at === -1) {
    throw new Error(`${name}: not under a ${BELOW} folder`);
  }
  return path.slice(at + BELOW.length);
}

/** Every passed test in `reports` as { project, test, ms, large }. */
export function measured(reports) {
  const out = [];
  for (const report of reports) {
    for (const file of report.testResults ?? []) {
      const rel = fromPlatform(file.name);
      const [workspace, ...below] = rel.split("/");
      const project = projectOf(below.join("/"));
      if (project === undefined) {
        throw new Error(`${rel}: not in a project's folder`);
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
  const at = argv.indexOf("--measured");
  const where = at === -1 ? undefined : argv[at + 1];
  if (where === undefined) {
    throw new Error("budgets.mjs needs --measured \"<where and when>\"");
  }
  const files = argv.filter((_, i) => i !== at && i !== at + 1);
  const rows = measured(files.map((file) => JSON.parse(readFileSync(file, "utf8"))));
  const budgets = JSON.parse(readFileSync(BUDGETS, "utf8"));
  const medium = { ...budgets.medium };
  for (const project of new Set(rows.filter((r) => !r.large).map((r) => r.project))) {
    medium[project] = slowest(rows.filter((r) => !r.large && r.project === project));
  }
  const large = rows.filter((r) => r.large);
  const next = { ...budgets, measured: where,
                 medium: Object.fromEntries(Object.entries(medium).sort()),
                 large: large.length ? slowest(large) : budgets.large };
  writeFileSync(BUDGETS, `${JSON.stringify(next, null, 2)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
