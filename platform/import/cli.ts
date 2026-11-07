/**
 * The cutover importer's job, run in Node 22 (`npm run build -w import`, then
 * `node dist/import.mjs <command> <work> ...`) against a backup copy (`backup.ts`):
 *
 * - `mount <work>` / `unmount <work>`: as root on the host, each pinned image mounted
 *   read-only at `mnt/<uid>` (refused when a loop device already holds it), or let go;
 * - `census <work> --tiers <control.json> --table <file>`: the counts to stdout, the
 *   per-owner table to `<file>` (0600); exit 0 when the backup is ready to import, 3 when not;
 * - `bundle <work> --tiers <control.json> --exclude <list> --out <dir> --private <dir>`
 *   (or `--dry-run`, which writes nothing): the bundles, the digests and billing ids in 0600
 *   files of the private folder, and counts alone on stdout.
 *
 * The images are read only through mounts the kernel says are read-only XFS
 * (`/proc/self/mountinfo`), so a live or writable volume is never read. stdout holds counts
 * only (the lesson of `restore_credentials.py` and `diagnose_connected_sources.py`).
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { layout, loopBacking, manifestOf, mountArgs, mountProblem } from "./backup";
import { buildBundles, exclusionList } from "./bundle";
import { census, type Tiers } from "./census";
import type { Copy } from "./survey";

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

/** The control record's tiers (`c:control`), each with its storage in bytes. */
export function tiersOf(text: string): Tiers {
  const record = JSON.parse(text) as { tiers?: unknown };
  const tiers = record.tiers;
  if (typeof tiers !== "object" || tiers === null || Object.keys(tiers).length === 0
      || Object.values(tiers).some((tier: { storage_bytes?: unknown }) =>
        typeof tier?.storage_bytes !== "number" || !Number.isSafeInteger(tier.storage_bytes))) {
    throw new Error("the control record holds no tiers with their storage in bytes");
  }
  return tiers as Tiers;
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

function run(command: string, args: readonly string[]): string {
  const result = spawnSync(command, args, { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`${command} failed (${result.status}): ${result.stderr.trim()}`);
  }
  return result.stdout;
}

function mount(work: string): void {
  const where = layout(work);
  const manifest = manifestOf(readFileSync(where.manifest, "utf8"));
  for (const pin of manifest.snapshots) {
    if (run("losetup", ["-j", where.image(pin.uid)]).trim() !== "") {
      throw new Error(`image ${pin.uid} is already held by a loop device: refused`);
    }
    mkdirSync(where.mount(pin.uid), { recursive: true, mode: 0o700 });
    run("mount", mountArgs(where.image(pin.uid), where.mount(pin.uid)));
    const problem = mountProblem(readFileSync("/proc/self/mountinfo", "utf8"),
                                 realpathSync(where.mount(pin.uid)), loopBacking,
                                 realpathSync(where.image(pin.uid)));
    if (problem !== null) {
      throw new Error(`image ${pin.uid}'s mount ${problem}`);
    }
  }
}

function unmount(work: string): void {
  const where = layout(work);
  const manifest = manifestOf(readFileSync(where.manifest, "utf8"));
  for (const pin of manifest.snapshots) {
    run("umount", [where.mount(pin.uid)]);
  }
}

/** The job: one command; returns the process's exit code. */
export function main(argv: readonly string[], mountinfo: () => string,
                     backing: Backing = loopBacking): number {
  const [command, given] = argv;
  if (given === undefined) {
    throw new Error("usage: <mount|unmount|census|bundle> <work> [options]");
  }
  const work = resolve(given);
  if (command === "mount") {
    mount(work);
    return 0;
  }
  if (command === "unmount") {
    unmount(work);
    return 0;
  }
  const tiers = tiersOf(readFileSync(required(argv, "--tiers"), "utf8"));
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
      exclude: exclusionList(readFileSync(required(argv, "--exclude"), "utf8")),
    });
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
    return Object.keys(summary.refused).length === 0 ? 0 : 3;
  }
  throw new Error(`no command ${command}`);
}

if (process.argv[1] !== undefined && /import\.mjs$|cli\.ts$/.test(process.argv[1])) {
  try {
    process.exitCode = main(process.argv.slice(2),
                            () => readFileSync("/proc/self/mountinfo", "utf8"));
  } catch (error) {
    process.stderr.write(`refused: ${(error as Error).message}\n`);
    process.exitCode = 2;
  }
}
