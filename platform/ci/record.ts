/**
 * The recorders, base against head, as CI runs them (`tests.yml` `platform-recorders`), from
 * `platform/`:
 *
 *   npm run record:check -- <base-commit> <k>/<n> <body-file>
 *
 * Each recorder prints every answer of a fixed script of calls as one line under its prefix,
 * ids taken out: the doors' wire (`REC`) and MCP `tools/list` (`TOOLS`), the builder's RPC
 * (`RPC`) and the reviewer's (`RV`). They are the workspaces' `test/record/*.record.ts`, run
 * on each one's own workerd project (`project.ts` `recording`), and no test run includes
 * them: they are heavy, and CI is the one full run.
 *
 * When the working tree's diff from `<base-commit>` touches a platform file other than prose
 * (`RECORDED`), the check records shard k of n of the base (a worktree of it,
 * installed by `npm ci`) and of this tree at once, by this runner, and prints each recorder's
 * lines that differ. It fails when a recorder differs that the body's one `Recorders-Differ:`
 * line does not name (`Recorders-Differ: rpc, rv -- why`), and when a run fails, after its
 * lines are written; what else a run printed is its workspace's `.log`. A shard's tests are
 * dealt from the names either tree lists, so a test is in the same shard of base and head. A
 * shard's lines are written sorted, every cursor (`"next"`) one placeholder, since a cursor
 * names ids minted at random.
 */
import { spawn, spawnSync } from "node:child_process";
import {
  createWriteStream, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";

import { PLATFORM, REPO } from "./paths.ts";
import { RECORDS } from "./project.ts";

/** Each recorder: its workspace, its files under `test/record/`, and its lines' prefix. */
const TABLE = Object.freeze([
  { name: "wire", workspace: "doors", files: ["wire", "wire-reach"], prefix: "REC" },
  { name: "tools", workspace: "doors", files: ["tools"], prefix: "TOOLS" },
  { name: "rpc", workspace: "owner", files: ["rpc"], prefix: "RPC" },
  { name: "rv", workspace: "owner", files: ["rv"], prefix: "RV" },
]);

/**
 * A diff the recorders can see: any platform file but prose. A recording reads a Worker's
 * source, its data and config outside `src` (`contract/*.json`, `wrangler.jsonc`), the kit and
 * test support it runs on, and the locked dependencies, so no narrower list holds.
 */
export const RECORDED = /^platform\/(?!.*\.md$)/;
const DECLARED = /^Recorders-Differ:(.*)$/gm;

export interface Shard {
  readonly index: number;
  readonly count: number;
}

export function shardOf(text: string): Shard {
  const match = /^([1-9]\d*)\/([1-9]\d*)$/.exec(text);
  const [index, count] = [Number(match?.[1]), Number(match?.[2])];
  if (match === null || index > count) {
    throw new Error(`not a shard, <k>/<n> with k at most n: ${text}`);
  }
  return { index, count };
}

/**
 * The full names of `shard`'s tests, out of `names`, every test either tree lists: every
 * count-th name in order, from the index-th. So each shard has as many tests as another, give
 * or take one, and as many of each block's, since a block's names sort together.
 */
export function dealt(names: Iterable<string>, shard: Shard): string[] {
  return [...new Set(names)].sort().filter((_, at) => at % shard.count === shard.index - 1);
}

/** The recorders a PR body says its change makes differ on purpose. Throws on a bad line. */
export function declared(body: string): Set<string> {
  const lines = [...body.matchAll(DECLARED)];
  if (lines.length > 1) {
    throw new Error("the body has more than one Recorders-Differ: line");
  }
  if (lines.length === 0) {
    return new Set();
  }
  const names = lines[0]![1]!.split(" -- ")[0]!.split(/[\s,]+/).filter(Boolean);
  const known = TABLE.map(({ name }) => name);
  const unknown = names.filter((name) => !known.includes(name));
  if (names.length === 0 || unknown.length > 0) {
    throw new Error(`Recorders-Differ: names ${unknown.join(", ") || "no recorder"}; ` +
                    `the recorders are ${known.join(", ")}`);
  }
  return new Set(names);
}

/** Each way a comparison fails: a run that failed, or a difference the body does not name. */
export function problems(differing: readonly string[], failed: readonly string[],
                         wanted: ReadonlySet<string>): string[] {
  return [
    ...failed.map((run) => `the ${run} recorders' run failed (its .log)`),
    ...differing.filter((name) => !wanted.has(name))
      .map((name) => `${name} differs, and the body's Recorders-Differ: line does not name it`),
  ];
}

/** `command` run in `cwd`, each line it prints to `line` and the rest to `log`: whether it passed. */
async function ran(command: string[], cwd: string, log: NodeJS.WritableStream,
                   line: (text: string) => void = (text) => log.write(`${text}\n`)):
    Promise<boolean> {
  const env = { ...process.env };
  delete env["CLOUDSDK_AUTH_ACCESS_TOKEN"];
  const child = spawn(command[0]!, command.slice(1), { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  child.stderr.pipe(log, { end: false });
  for await (const text of createInterface({ input: child.stdout })) {
    line(text);
  }
  return await new Promise<number | null>((done) => child.on("close", done)) === 0;
}

const escaped = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A tree the check records, base or head: its platform, its lines' folder, and its name. */
interface Side {
  readonly platform: string;
  readonly out: string;
  readonly label: string;
}

// A recorder file the base does not have yet records no line there, so its lines differ.
const CONFIG = ["--config", `${RECORDS}/record.config.ts`, "--passWithNoTests"];

/** The full names of the tests in `files`, `side`'s recorders of `workspace`: null if it failed. */
async function listed(side: Side, workspace: string, files: readonly string[],
                      log: NodeJS.WritableStream): Promise<string[] | null> {
  const json = join(side.out, `${workspace}.tests.json`);
  return await ran(["npx", "vitest", "list", "--staticParse=false", ...CONFIG, `--json=${json}`,
                    ...files], join(side.platform, workspace), log)
    ? (JSON.parse(readFileSync(json, "utf8")) as { name: string }[]).map(({ name }) => name)
    : null;
}

/**
 * Shard `shard` of one workspace's recorders, on every side at once and each side's in one
 * process, each recorder's lines to `<out>/<name>.txt`: the runs that failed.
 */
async function recordWorkspace(sides: readonly Side[], workspace: string, shard: Shard):
    Promise<string[]> {
  const recorders = TABLE.filter((recorder) => recorder.workspace === workspace);
  const files = recorders.flatMap((recorder) =>
    recorder.files.map((file) => `${RECORDS}/${file}.record.ts`));
  const logs = sides.map(({ out }) => createWriteStream(join(out, `${workspace}.log`)));
  const lists = await Promise.all(sides.map((side, at) =>
    listed(side, workspace, files, logs[at]!)));
  const names = dealt(lists.flatMap((list) => list ?? []), shard);
  const filter = ["-t", `^(?:${names.map(escaped).join("|")})$`];
  const passed = await Promise.all(sides.map(async (side, at) => {
    const log = logs[at]!;
    const kept = new Map(recorders.map(({ prefix }) => [`${prefix}\t`, [] as string[]]));
    const ok = lists[at] !== null &&
      await ran(["npx", "vitest", "run", ...CONFIG, "--disableConsoleIntercept", ...filter,
                 ...files], join(side.platform, workspace), log, (text) => {
        const lines = kept.get(text.slice(0, text.indexOf("\t") + 1));
        if (lines === undefined) {
          log.write(`${text}\n`);
        } else {
          lines.push(text.replace(/"next":"[^"]*"/g, '"next":"<b64>"'));
        }
      });
    log.end();
    for (const { name, prefix } of recorders) {
      const lines = kept.get(`${prefix}\t`)!.sort();
      writeFileSync(join(side.out, `${name}.txt`), lines.map((text) => `${text}\n`).join(""));
      console.log(`${side.label} ${name}: ${lines.length} lines`);
    }
    console.log(`${side.label} ${workspace}: ${ok ? "passed" : "failed"}`);
    return ok;
  }));
  return sides.filter((_, at) => !passed[at]).map(({ label }) => `${label} ${workspace}`);
}

/** Shard `shard` of every recorder, on every side: the runs that failed. */
async function record(sides: readonly Side[], shard: Shard): Promise<string[]> {
  for (const { out } of sides) {
    mkdirSync(out, { recursive: true });
  }
  const failed: string[] = [];
  for (const workspace of new Set(TABLE.map((recorder) => recorder.workspace))) {
    failed.push(...await recordWorkspace(sides, workspace, shard));
  }
  return failed;
}

/** The recorders whose lines in `base` and `head` differ, each shown as `diff -u` shows it. */
function differences(base: string, head: string): string[] {
  const differing: string[] = [];
  for (const { name } of TABLE) {
    const shown = spawnSync("diff", ["-u", join(base, `${name}.txt`), join(head, `${name}.txt`)],
                            { encoding: "utf8", maxBuffer: 1 << 30 });
    if (shown.status === 0) {
      console.log(`${name}: the same`);
    } else if (shown.status === 1) {
      differing.push(name);
      console.log(`${name}: differs\n${shown.stdout}`);
    } else {
      throw new Error(`diff of ${name} failed: ${shown.stderr}`);
    }
  }
  return differing;
}

function git(...args: string[]): string {
  const done = spawnSync("git", ["-C", REPO, ...args], { encoding: "utf8", maxBuffer: 1 << 30 });
  if (done.status !== 0) {
    throw new Error(`git ${args.join(" ")} failed: ${done.stderr}`);
  }
  return done.stdout;
}

/** The platform at `platform`, with its lockfile's dependencies installed if it has none. */
async function installed(platform: string): Promise<void> {
  if (!existsSync(join(platform, "node_modules")) &&
      !await ran(["npm", "ci"], platform, process.stdout)) {
    throw new Error(`npm ci failed in ${platform}`);
  }
}

async function check(base: string, shard: Shard, body: string): Promise<number> {
  if (!git("diff", "--name-only", base).split("\n").some((path) => RECORDED.test(path))) {
    console.log(`The diff from ${base} touches no platform file but prose: the recorders see nothing.`);
    return 0;
  }
  const wanted = declared(readFileSync(body, "utf8"));
  const work = mkdtempSync(join(tmpdir(), "recorders-"));
  const tree = join(work, "base");
  git("worktree", "add", "--detach", tree, base);
  try {
    const from = { platform: join(tree, "platform"), out: join(work, "base-rec"), label: "base" };
    const to = { platform: PLATFORM, out: join(work, "head-rec"), label: "head" };
    await Promise.all([installed(from.platform), installed(to.platform)]);
    const failed = await record([from, to], shard);
    const found = problems(differences(from.out, to.out), failed, wanted);
    for (const problem of found) {
      console.error(`::error::recorders ${shard.index}/${shard.count}: ${problem}`);
    }
    return found.length === 0 ? 0 : 1;
  } finally {
    git("worktree", "remove", "--force", tree);
    rmSync(work, { recursive: true, force: true });
  }
}

if (import.meta.main) {
  const [command, base, shard, body] = process.argv.slice(2);
  if (command !== "check" || base === undefined || shard === undefined || body === undefined) {
    console.error("usage: npm run record:check -- <base-commit> <k>/<n> <body-file>");
    process.exit(2);
  }
  process.exit(await check(base, shardOf(shard),
                           resolve(process.env["INIT_CWD"] ?? process.cwd(), body)));
}
