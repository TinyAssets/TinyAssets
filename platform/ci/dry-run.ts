/**
 * The cutover's dry run (untangle B24; d3 §A10), on the checkout it runs in, which it changes
 * and never pushes (`.github/workflows/cutover-dry-run.yml`):
 *
 *   node --experimental-strip-types platform/ci/dry-run.ts apply
 *     the manifest's tree (`ci/kit/cutover.ts` `dryRun`): carry-outs moved, deletes removed,
 *     edited tests set aside, and each workspace whose `package.json` it deletes out of
 *     `platform/package.json`, with its installed deps, and npm's lock rewritten to match
 *     (`npm install --package-lock-only`), since npm installs no workspace without its manifest;
 *   node --experimental-strip-types platform/ci/dry-run.ts legs
 *     `platform-tests`' legs (`tests.yml`) as a matrix, less each leg of a workspace it deletes.
 *
 * It reaches nothing live: no secret, no deploy, no push.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { dryRun, dryRunLegs, type Leg, pythonLoads } from "./kit/cutover.ts";
import { relativeLoads } from "./kit/imports.ts";
import { PLATFORM, REPO } from "./paths.ts";
import { workflow } from "./workflows.ts";

const git = (...args: string[]) => execFileSync("git", args, { cwd: REPO, encoding: "utf8" });

/** The checkout made the manifest's tree; what it did, a line each. */
function apply(): string[] {
  const tracked = git("ls-files", "-z").split("\0").filter((path) => path !== "");
  const has = new Set(tracked);
  const text = (path: string) => readFileSync(join(REPO, path), "utf8");
  const loads = (path: string) => !has.has(path) ? []
    : (path.endsWith(".py") ? pythonLoads : relativeLoads)(path, text(path),
                                                         (each) => has.has(each));
  const run = dryRun(tracked, loads);
  for (const [from, to] of run.carried) {
    mkdirSync(dirname(join(REPO, to)), { recursive: true });
    git("mv", from, to);
  }
  for (const list of [run.removed, run.setAside]) {
    for (let at = 0; at < list.length; at += 500) {
      git("rm", "-q", "--", ...list.slice(at, at + 500));
    }
  }
  const pkg = join(PLATFORM, "package.json");
  const data = JSON.parse(readFileSync(pkg, "utf8")) as { workspaces: string[] };
  const left = data.workspaces.filter((each) => existsSync(join(PLATFORM, each, "package.json")));
  // What npm installed for a workspace that leaves, which no lock names any more.
  for (const each of data.workspaces.filter((name) => !left.includes(name))) {
    rmSync(join(PLATFORM, each, "node_modules"), { recursive: true, force: true });
  }
  writeFileSync(pkg, `${JSON.stringify({ ...data, workspaces: left }, null, 2)}\n`);
  execFileSync("npm", ["install", "--package-lock-only", "--ignore-scripts", "--no-audit",
                       "--no-fund"], { cwd: PLATFORM, stdio: "inherit" });
  return [`carried out: ${run.carried.map(([from, to]) => `${from} to ${to}`).join(", ")}`,
          `removed: ${run.removed.length} files`,
          `kept for an edit that loads them: ${run.kept.join(", ")}`,
          `set aside, the tests the cutover edits: ${run.setAside.join(", ")}`,
          `workspaces: ${data.workspaces.filter((each) => !left.includes(each)).join(", ")} gone`];
}

/** `platform-tests`' legs, less each one of a workspace the manifest deletes. */
function legs(): { include: Leg[] } {
  return { include: dryRunLegs(workflow("tests.yml").jobs["platform-tests"].strategy.matrix
    .include as Leg[]) };
}

const [verb] = process.argv.slice(2);
if (verb === "apply") {
  process.stdout.write(`${apply().join("\n")}\n`);
} else if (verb === "legs") {
  process.stdout.write(`${JSON.stringify(legs())}\n`);
} else {
  process.stderr.write("usage: dry-run.ts apply | legs\n");
  process.exit(2);
}
