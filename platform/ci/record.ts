/**
 * The recorders, run from `platform/`, so a refactor's reviewer diffs base against head:
 *
 *   npm run record -- <out-dir>               each recorder's lines, to <out-dir>/<name>.txt
 *   npm run record:diff -- <base-dir> <head-dir>   the lines that differ, by recorder
 *
 * Each recorder prints every answer of a fixed script of calls as one line under its prefix,
 * ids taken out: the doors' wire (`REC`) and MCP `tools/list` (`TOOLS`), the builder's RPC
 * (`RPC`) and the reviewer's (`RV`). They are the workspaces' `test/record/*.record.ts`, run
 * on each one's own workerd project (`project.ts` `recording`), and no test run includes
 * them: they are heavy, and CI is the one full run. Each is written sorted, every cursor
 * (`"next"`) one placeholder, since a cursor names ids minted at random. A recorder whose run
 * fails fails this, after its lines are written; what else it printed is its `.log`.
 */
import { spawn, spawnSync } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";

import { PLATFORM } from "./paths.ts";
import { RECORDS } from "./project.ts";

/** Each recorder: its workspace, its files under `test/record/`, and its lines' prefix. */
const TABLE = Object.freeze([
  { name: "wire", workspace: "doors", files: ["wire", "wire-reach"], prefix: "REC" },
  { name: "tools", workspace: "doors", files: ["tools"], prefix: "TOOLS" },
  { name: "rpc", workspace: "owner", files: ["rpc"], prefix: "RPC" },
  { name: "rv", workspace: "owner", files: ["rv"], prefix: "RV" },
]);

type Recorder = (typeof TABLE)[number];

/** One recorder run in its workspace: its lines, and whether its run passed. */
async function run(recorder: Recorder, out: string): Promise<boolean> {
  const cwd = join(PLATFORM, recorder.workspace);
  const env = { ...process.env };
  delete env["CLOUDSDK_AUTH_ACCESS_TOKEN"];
  const child = spawn("npx", [
    "vitest", "run", "--config", `${RECORDS}/record.config.ts`, "--disableConsoleIntercept",
    ...recorder.files.map((file) => `${RECORDS}/${file}.record.ts`),
  ], { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  const log = createWriteStream(join(out, `${recorder.name}.log`));
  child.stderr.pipe(log, { end: false });
  const lines: string[] = [];
  const at = `${recorder.prefix}\t`;
  for await (const line of createInterface({ input: child.stdout })) {
    if (line.startsWith(at)) {
      lines.push(line.replace(/"next":"[^"]*"/g, '"next":"<b64>"'));
    } else {
      log.write(`${line}\n`);
    }
  }
  const code = await new Promise<number | null>((done) => child.on("close", done));
  log.end();
  lines.sort();
  writeFileSync(join(out, `${recorder.name}.txt`), lines.map((line) => `${line}\n`).join(""));
  console.log(`${recorder.name}: ${lines.length} lines, ${code === 0 ? "passed" : `failed (${code})`}`);
  return code === 0;
}

/** Each recorder's lines in `base` and `head` that differ, as `diff -u` shows them. */
function differences(base: string, head: string): boolean {
  let same = true;
  for (const { name } of TABLE) {
    const [a, b] = [join(base, `${name}.txt`), join(head, `${name}.txt`)];
    for (const file of [a, b]) {
      if (!existsSync(file)) {
        throw new Error(`${file} is missing: record both trees with npm run record first`);
      }
    }
    const shown = spawnSync("diff", ["-u", a, b], { encoding: "utf8", maxBuffer: 1 << 30 });
    if (shown.status === 0) {
      console.log(`${name}: the same`);
    } else if (shown.status === 1) {
      same = false;
      console.log(`${name}: differs\n${shown.stdout}`);
    } else {
      throw new Error(`diff of ${name} failed: ${shown.stderr}`);
    }
  }
  return same;
}

const [command, ...given] = process.argv.slice(2);
/** Each folder named, from where npm was run. */
const dirs = given.map((dir) => resolve(process.env["INIT_CWD"] ?? process.cwd(), dir));
if (command === "record" && dirs.length === 1) {
  const out = dirs[0]!;
  mkdirSync(out, { recursive: true });
  let passed = true;
  for (const recorder of TABLE) {
    passed = await run(recorder, out) && passed;
  }
  process.exit(passed ? 0 : 1);
} else if (command === "diff" && dirs.length === 2) {
  process.exit(differences(dirs[0]!, dirs[1]!) ? 0 : 1);
} else {
  console.error("usage: npm run record -- <out-dir> | npm run record:diff -- <base> <head>");
  process.exit(2);
}
