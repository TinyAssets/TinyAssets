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
 * (`"next"`) one placeholder, since a cursor names ids minted at random. Even a failed recorder
 * keeps its transcript and log for diagnosis. The attempt and its result attest which files
 * came from successful children; the comparison verifies those bytes before accepting them.
 */
import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createWriteStream, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { finished } from "node:stream/promises";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";

import { sha256Hex } from "../contract/src/digest.ts";
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

/** Only the selected attempt can rename a prepared file into the public output directory.
 * Taking over moves its whole source directory, so an earlier writer paused at rename
 * cannot overwrite a successor's transcript, log, or result. */
const STATE = ".record-state";
const NEXT = ".record-next-";

function selected(dir: string): string {
  const state = join(dir, STATE);
  if (!existsSync(state)) {
    throw new Error(`no recording outcome in ${dir}; older transcripts need their original run evidence`);
  }
  const entries = lstatSync(state).isDirectory() ? readdirSync(state, { withFileTypes: true }) : [];
  const entry = entries[0];
  if (entries.length !== 1 || !entry?.isDirectory() || !entry.name.startsWith("attempt-")) {
    throw new Error(`unexpected recorder state in ${state}`);
  }
  return join(state, entry.name);
}

function begin(dir: string): string {
  const setup = mkdtempSync(join(dir, NEXT));
  const id = `attempt-${randomUUID()}`;
  const next = join(setup, "next");
  try {
    mkdirSync(join(next, id), { recursive: true });
    const current = join(dir, STATE);
    if (existsSync(current)) {
      selected(dir);
      renameSync(current, join(setup, "previous"));
    }
    renameSync(next, current);
    return join(current, id);
  } finally {
    rmSync(setup, { recursive: true, force: true });
  }
}

/** One recorder run in its workspace: its lines, and whether its run passed. */
async function run(recorder: Recorder, out: string, stage: string): Promise<string | null> {
  const cwd = join(PLATFORM, recorder.workspace);
  const env = { ...process.env };
  delete env["CLOUDSDK_AUTH_ACCESS_TOKEN"];
  const child = spawn("npx", [
    "vitest", "run", "--config", `${RECORDS}/record.config.ts`, "--disableConsoleIntercept",
    ...recorder.files.map((file) => `${RECORDS}/${file}.record.ts`),
  ], { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  const exit = new Promise<number | null>((done) => child.on("close", done));
  child.on("error", (error) => console.error(error.message));
  const logName = `${recorder.name}.log`;
  const log = createWriteStream(join(stage, logName));
  const flushed = finished(log);
  // Attach immediately so an early disk error cannot become an unhandled rejection.
  void flushed.catch(() => {});
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
  const code = await exit;
  const passed = code === 0 && lines.length > 0;
  if (!passed) {
    log.write(`Recording failed with exit ${code} and ${lines.length} canonical lines\n`);
  }
  log.end();
  await flushed;
  renameSync(join(stage, logName), join(out, logName));
  lines.sort();
  const content = lines.map((line) => `${line}\n`).join("");
  const name = `${recorder.name}.txt`;
  writeFileSync(join(stage, name), content);
  renameSync(join(stage, name), join(out, name));
  console.log(`${recorder.name}: ${lines.length} lines, ${passed ? "passed" : `failed (exit ${code})`}`);
  return passed ? sha256Hex(content) : null;
}

function completed(dir: string) {
  const stage = selected(dir);
  const receipt = join(stage, "result.json");
  if (!existsSync(receipt)) {
    throw new Error(`the latest recording in ${dir} did not complete successfully`);
  }
  const hashes = JSON.parse(readFileSync(receipt, "utf8")) as Record<string, string | null>;
  for (const { name } of TABLE) {
    if (!hashes[name]) {
      throw new Error(`${name} did not record successfully in ${dir}; see ${name}.log`);
    }
    if (sha256Hex(readFileSync(join(dir, `${name}.txt`))) !== hashes[name]) {
      throw new Error(`${dir}/${name}.txt does not match its successful recording`);
    }
  }
  return { stage, hashes };
}

/** Compare verified captures; never turn identical partial transcripts into a pass. */
function differences(base: string, head: string): boolean {
  const left = completed(base);
  const right = completed(head);
  let same = true;
  for (const { name } of TABLE) {
    if (left.hashes[name] === right.hashes[name]) {
      console.log(`${name}: the same`);
      continue;
    }
    same = false;
    const [a, b] = [join(base, `${name}.txt`), join(head, `${name}.txt`)];
    const shown = spawnSync("diff", ["-u", a, b], { encoding: "utf8", maxBuffer: 1 << 30 });
    if (shown.status === 1) {
      console.log(`${name}: differs\n${shown.stdout}`);
    } else {
      throw new Error(`diff of ${name} failed: ${shown.stderr}`);
    }
  }
  if (selected(base) !== left.stage || selected(head) !== right.stage) {
    throw new Error("a recording restarted during comparison");
  }
  return same;
}

const [command, ...given] = process.argv.slice(2);
/** Each folder named, from where npm was run. */
const dirs = given.map((dir) => resolve(process.env["INIT_CWD"] ?? process.cwd(), dir));
if (command === "record" && dirs.length === 1) {
  const out = dirs[0]!;
  mkdirSync(out, { recursive: true });
  const stage = begin(out);
  const hashes: Record<string, string | null> = {};
  for (const recorder of TABLE) {
    hashes[recorder.name] = await run(recorder, out, stage);
  }
  writeFileSync(join(stage, "result.tmp"), JSON.stringify(hashes));
  renameSync(join(stage, "result.tmp"), join(stage, "result.json"));
  if (Object.values(hashes).includes(null)) {
    process.exitCode = 1;
  } else {
    completed(out);
  }
} else if (command === "diff" && dirs.length === 2) {
  process.exit(differences(dirs[0]!, dirs[1]!) ? 0 : 1);
} else {
  console.error("usage: npm run record -- <out-dir> | npm run record:diff -- <base> <head>");
  process.exit(2);
}
