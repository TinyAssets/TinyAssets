/**
 * The cutover importer's job, run in Node 22 (`npm run build -w import`, then
 * `node dist/import.mjs <command> <work> ...`) against a backup copy (`backup.ts`):
 *
 * - `copy <plan|digests|stores|release> <work> ...`: the backup copy's own checks
 *   (`copy.ts`), for the window's host script (`window.sh`), which makes the copy and mounts
 *   each pinned image read-only at `mnt/<uid>` on the host, which has no Node;
 * - `census <work> --tiers <control.json> --table <file>`: the counts to stdout, the
 *   per-owner table to `<file>` (0600); exit 0 when the backup is ready to import, 3 when not;
 * - `bundle <work> --tiers <control.json> --exclude <list|-> --out <dir> --private <dir>`
 *   (or `--dry-run`, which writes nothing): the bundles, the digests and billing ids in 0600
 *   files of the private folder, and counts alone on stdout;
 *   (`--rehearsal <run>`: each bundle under its rehearsal's name, `rehearsalName`);
 * - `stage <out> --run <id> --facts <facts.json>` (or `--dry-run`, which sends nothing and
 *   needs no credential; `--rehearsal`, for a rehearsal's bundles): every bundle checked
 *   whole, uploaded to the staging bucket and its message put on the import queue
 *   (`stage.ts`), the window's credentials read once from the job's stdin, one a line in
 *   `STAGE_INPUTS`' order, never from a file, an argument or the environment; counts and
 *   ordinals on stdout;
 * - `verify <out> --run <id> --record <file>`, `admit <out> --run <id> --record <file>`,
 *   `wipe <out> --run <id> --record <file>` and `dlq <out> --run <id> [--delete]`, each with
 *   `--facts <facts.json>`: the window's steps after staging (`window.ts`), with the same
 *   credentials; exit 0 when the step holds (every owner clean, every listing empty, no dead
 *   letter), 3 when not.
 *
 * `--tiers` is the control record's tiers (`deploy/control.json`'s, by the window's `jq`),
 * read by the contract's `parseTiers`; `--facts` is the live names the steps that reach the
 * account take (`deploy/facts.ts --import`, the contract's `ImportNames`).
 *
 * The images are read only through mounts the kernel says are read-only XFS
 * (`/proc/self/mountinfo`), so a live or writable volume is never read. stdout holds counts
 * only (the lesson of `restore_credentials.py` and `diagnose_connected_sources.py`).
 */
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { parseTiers, type Tiers } from "../contract/src/control";
import type { ImportNames } from "../contract/src/import-names";

import { layout, loopBacking, manifestOf, mountProblem } from "./backup";
import { buildBundles, exclusionList } from "./bundle";
import { census } from "./census";
import { copyMain } from "./copy";
import { type Fetcher, type Inputs, namesOf, NETWORK, stage } from "./stage";
import type { Copy } from "./survey";
import { admit, CLOCK, type Clock, deadLetters, verify, wipe } from "./window";

/**
 * A staging run's credentials, by input, each named as its stdin hands it: one a line, in this
 * order (`credentialsFrom`). The host script pipes them to the job's container (`window.sh`),
 * so they sit in no container config, environment or file.
 */
export const STAGE_INPUTS = Object.freeze({
  account: "CLOUDFLARE_ACCOUNT_ID", accessKeyId: "R2_ACCESS_KEY_ID",
  secretAccessKey: "R2_SECRET_ACCESS_KEY", apiToken: "CLOUDFLARE_API_TOKEN",
  importKey: "IMPORT_KEY",
} as const);

/** The credentials as stdin hands them, by name: one a line, in `STAGE_INPUTS`' order. */
export function credentialsFrom(text: string): Record<string, string> {
  const lines = text.split("\n");
  return Object.fromEntries(Object.values(STAGE_INPUTS)
    .map((name, n) => [name, (lines[n] ?? "").replace(/\r$/, "")]));
}

/** A staging run's credentials from `given` (`credentialsFrom`), each required, none printed. */
export function stageInputs(given: Readonly<Record<string, string | undefined>>): Inputs {
  const missing = Object.values(STAGE_INPUTS).filter((name) => (given[name] ?? "") === "");
  if (missing.length > 0) {
    throw new Error(`a staging run takes ${missing.join(", ")} from its stdin`);
  }
  return Object.fromEntries(Object.entries(STAGE_INPUTS)
    .map(([input, name]) => [input, given[name]!])) as unknown as Inputs;
}

/** `stage <out> --run <id> --facts <file> [--dry-run]`: its exit code once every bundle is sent. */
export async function stageMain(argv: readonly string[],
                                env: Readonly<Record<string, string | undefined>>,
                                print: (line: string) => void, fetcher: Fetcher = NETWORK):
    Promise<number> {
  const [, given] = argv;
  if (given === undefined) {
    throw new Error("usage: stage <out> --run <id> --facts <file> [--dry-run]");
  }
  const dry = argv.includes("--dry-run");
  const summary = await stage(resolve(given), required(argv, "--run"), namesIn(argv),
                              dry ? null : stageInputs(env), fetcher, print, undefined,
                              argv.includes("--rehearsal"));
  print(JSON.stringify(summary, null, 2));
  return 0;
}

/** The window's steps after staging: `verify`, `admit`, `wipe` and `dlq` (`window.ts`). */
export const WINDOW_STEPS = Object.freeze(["verify", "admit", "wipe", "dlq"] as const);

/** `<step> <out> --run <id> ...`: its exit code, 0 when the step holds, 3 when not. */
export async function windowMain(argv: readonly string[],
                                 env: Readonly<Record<string, string | undefined>>,
                                 print: (line: string) => void, fetcher: Fetcher = NETWORK,
                                 clock: Clock = CLOCK): Promise<number> {
  const [step, given] = argv;
  if (given === undefined) {
    throw new Error(`usage: ${step} <out> --run <id> --facts <file> --record <file>`);
  }
  const out = resolve(given);
  const run = required(argv, "--run");
  const window = { names: namesIn(argv), inputs: stageInputs(env), fetcher, clock };
  if (step === "dlq") {
    const read = await deadLetters(window, out, run, argv.includes("--delete"), print);
    return read.messages === 0 ? 0 : 3;
  }
  const record = resolve(required(argv, "--record"));
  if (step === "verify") {
    const summary = await verify(out, run, window, record, print);
    return summary.clean === summary.bundles ? 0 : 3;
  }
  if (step === "admit") {
    await admit(out, run, window, record, print);
    return 0;
  }
  if (step === "wipe") {
    const proof = await wipe(out, run, window, record, print);
    return proof.records === 0 && proof.staged === 0 && proof.objects === 0 ? 0 : 3;
  }
  throw new Error(`no step ${step}`);
}

function option(argv: readonly string[], name: string): string | null {
  const at = argv.indexOf(name);
  return at < 0 || at + 1 >= argv.length ? null : argv[at + 1]!;
}

function required(argv: readonly string[], name: string): string {
  const value = option(argv, name);
  if (value === null) {
    throw new Error(`${name} is required`);
  }
  return value;
}

/**
 * Where the exclusion list is read: its file, or `-`, the job's stdin (fd 0, a pipe or a
 * socket alike), so the window's list, a secret, never touches a file (`window.sh`).
 */
function exclusionFrom(given: string): string | number {
  return given === "-" ? 0 : given;
}

/** `--tiers`' file: the control record's tiers, as the contract parses them. */
function tiersIn(argv: readonly string[]): Tiers {
  return parseTiers((JSON.parse(readFileSync(required(argv, "--tiers"), "utf8")) as
                     { tiers?: unknown }).tiers);
}

/** `--facts`' file: the live names the job reaches. */
function namesIn(argv: readonly string[]): ImportNames {
  return namesOf(readFileSync(required(argv, "--facts"), "utf8"));
}

/** What reads a loop device's backing file: the kernel's, or a test's. */
export type Backing = (device: string) => string | null;

/**
 * The backup copy at `work`, each image read only where it is mounted read-only XFS, from
 * the loop device whose backing file is that image (`<uid>.img`, whose hash is its pin).
 */
export function copyAt(work: string, mountinfo: string, backing: Backing = loopBacking): Copy {
  const where = layout(work);
  return {
    work,
    manifest: manifestOf(readFileSync(where.manifest, "utf8")),
    volume: (uid) => {
      let at: string;
      let image: string;
      try {
        at = realpathSync(where.mount(uid));
        image = realpathSync(where.image(uid));
      } catch {
        return { problem: "has no mount point or no image" };
      }
      const problem = mountProblem(mountinfo, at, backing, image);
      return problem === null ? { root: at } : { problem };
    },
  };
}

/** The job: one command; returns the process's exit code. */
export function main(argv: readonly string[], mountinfo: () => string,
                     backing: Backing = loopBacking): number {
  const [command, given] = argv;
  if (given === undefined) {
    throw new Error("usage: <copy|census|bundle|stage> <work> [options]");
  }
  if (command === "copy") {
    return copyMain(argv, (work) => copyAt(resolve(work), mountinfo(), backing),
                    (line) => process.stdout.write(`${line}\n`));
  }
  const work = resolve(given);
  const tiers = tiersIn(argv);
  const copy = copyAt(work, mountinfo(), backing);
  if (command === "census") {
    const table = required(argv, "--table");
    const result = census(copy, tiers);
    writeFileSync(table, result.table, { flag: "wx", mode: 0o600 });
    process.stdout.write(`${JSON.stringify(result.report, null, 2)}\n`);
    return result.ready ? 0 : 3;
  }
  if (command === "bundle") {
    const dry = argv.includes("--dry-run");
    const out = dry ? null : required(argv, "--out");
    const privateDir = dry ? null : required(argv, "--private");
    for (const dir of [out, privateDir]) {
      if (dir !== null) {
        mkdirSync(dir, { recursive: true, mode: 0o700 });
      }
    }
    const summary = buildBundles(copy, {
      out, private: privateDir, tiers,
      exclude: exclusionList(readFileSync(exclusionFrom(required(argv, "--exclude")), "utf8")),
      rehearsal: option(argv, "--rehearsal"),
    });
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
    return Object.keys(summary.refused).length === 0 ? 0 : 3;
  }
  throw new Error(`no command ${command}`);
}

if (process.argv[1] !== undefined && /import\.mjs$|cli\.ts$/.test(process.argv[1])) {
  const refuse = (error: unknown) => {
    process.stderr.write(`refused: ${(error as Error).message}\n`);
    process.exitCode = 2;
  };
  // The credentials, read once from stdin; a dry run needs none, so reads nothing.
  const credentials = () => credentialsFrom(readFileSync(0, "utf8"));
  if (process.argv[2] === "stage") {
    stageMain(process.argv.slice(2), process.argv.includes("--dry-run") ? {} : credentials(),
              (line) => process.stdout.write(`${line}\n`)).then((code) => {
      process.exitCode = code;
    }, refuse);
  } else if ((WINDOW_STEPS as readonly string[]).includes(process.argv[2] ?? "")) {
    windowMain(process.argv.slice(2), credentials(),
               (line) => process.stdout.write(`${line}\n`)).then((code) => {
      process.exitCode = code;
    }, refuse);
  } else {
    try {
      process.exitCode = main(process.argv.slice(2),
                              () => readFileSync("/proc/self/mountinfo", "utf8"));
    } catch (error) {
      refuse(error);
    }
  }
}
