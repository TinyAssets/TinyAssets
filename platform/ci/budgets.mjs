#!/usr/bin/env node
/**
 * Recompute `budgets.json` from Vitest JSON reports (`--reporter=json --outputFile=<f>`):
 * each project's slowest medium test, and the slowest large one (`classes.ts` has the rule).
 *
 *   node ci/budgets.mjs --measured "<where and when>" <report.json>...
 *
 * A test's project is read from its path in the platform folder of the checkout that ran it
 * (a CI leg's or this one's): the folder each project's tests are in (`classes.ts`'s
 * `PROJECTS`, which every config's projects are made from). Only passed tests count; a
 * failed or skipped one measures nothing. The `platform-sizes` legs upload their reports.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { PROJECTS as FOLDERS } from "./classes.ts";
import { PLATFORM } from "./paths.ts";

const CI = dirname(fileURLToPath(import.meta.url));
const BUDGETS = join(CI, "budgets.json");
/** Each project's name, by the folder its tests are in. */
const PROJECTS = Object.fromEntries(Object.entries(FOLDERS).map(([name, folder]) => [folder, name]));
/** The platform folder's own name, as every checkout has it. */
const PLATFORM_DIR = `/${basename(PLATFORM)}/`;

/** Every passed test in `reports` as { project, test, ms, large }. */
export function measured(reports) {
  const out = [];
  for (const report of reports) {
    for (const file of report.testResults ?? []) {
      const path = file.name.split("\\").join("/");
      const rel = path.slice(path.lastIndexOf(PLATFORM_DIR) + PLATFORM_DIR.length);
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
