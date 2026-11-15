/**
 * The runner's link-safe file operations (`fsafe.ts`) when processes race: two runs of one
 * sandbox start at once and make the same directories (the run homes' parent, the store's
 * locks, an agent's scratch). One that another process made between the look and the make
 * is the directory it asked for, never a failure (the two-users runner test's flake: the
 * second run died on `EEXIST` and never started its CLI).
 */
import { fork } from "node:child_process";
import { lstatSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { LinkRefused, safeDirs } from "./fsafe";

const HERE = dirname(fileURLToPath(import.meta.url));
const PROCESSES = 8;
const ROUNDS = 10;

/**
 * `fsafe.ts` bundled, and a child that says it is ready, then makes `dir` under `base` at
 * the moment its parent sends once every child is ready.
 */
function racer(): string {
  const out = mkdtempSync(join(tmpdir(), "ta-fsafe-race-"));
  const { buildSync } = createRequire(import.meta.url)("esbuild") as typeof import("esbuild");
  buildSync({ entryPoints: [join(HERE, "fsafe.ts")], bundle: true, platform: "node",
              format: "esm", outfile: join(out, "fsafe.mjs"), logLevel: "error" });
  writeFileSync(join(out, "race.mjs"), `import { safeDirs } from "./fsafe.mjs";
const [base, dir] = JSON.parse(process.argv[2]);
process.once("message", (at) => {
  while (Date.now() < at) {}
  try { safeDirs(base, dir, 0o755); process.exit(0); }
  catch (error) { console.error(error.message); process.exit(1); }
});
process.send("ready");
`);
  return join(out, "race.mjs");
}

/** The moment every ready racer is sent, a little ahead so each has it before it comes. */
const START_AHEAD_MS = 50;

describe("safeDirs among processes", () => {
  it("makes one tree that several processes make at once, none of them failing",
     async () => {
    const race = racer();
    try {
      for (let round = 0; round < ROUNDS; round++) {
        const base = mkdtempSync(join(tmpdir(), "ta-fsafe-"));
        const dir = join(base, "a", "b", "c");
        // Every racer forked and ready (its message) before any is told when to begin.
        const racers = Array.from({ length: PROCESSES }, () => {
          const child = fork(race, [JSON.stringify([base, dir])], { stdio: "inherit" });
          return { child, ready: new Promise((resolve) => child.once("message", resolve)),
                   exit: new Promise<number | null>((resolve) => child.on("exit", resolve)) };
        });
        await Promise.all(racers.map((each) => each.ready));
        const at = Date.now() + START_AHEAD_MS;
        for (const each of racers) {
          each.child.send(at);
        }
        const codes = await Promise.all(racers.map((each) => each.exit));
        expect(codes).toEqual(Array(PROCESSES).fill(0));
        expect(lstatSync(dir).isDirectory()).toBe(true);
        rmSync(base, { recursive: true, force: true });
      }
    } finally {
      rmSync(dirname(race), { recursive: true, force: true });
    }
  });

  it("still refuses a link where a directory is made", () => {
    const base = mkdtempSync(join(tmpdir(), "ta-fsafe-"));
    try {
      symlinkSync(tmpdir(), join(base, "a"));
      expect(() => safeDirs(base, join(base, "a", "b"), 0o755)).toThrow(LinkRefused);
    } finally {
      rmSync(base, { recursive: true, force: true });
    }
  });
});
