/**
 * The runner's loader (`template/ta-runner`), as root under a test's root: a runner uploaded
 * under `/var/lib/ta/incoming/<sha>` is installed only when its bytes are its digest, root's
 * alone to write and readable by every user (the sync agent runs it); a command runs the
 * runner of its digest, re-hashed first, with the digest in its environment; a runner absent
 * (24), bytes that are not their digest (25) or a name that is not a digest (2) run nothing.
 * The loader takes its paths from no environment: these run a copy of it whose fixed paths
 * are the test's root's and whose Node is the test's, every other line as it ships.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync,
         statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { EXIT, INCOMING, INSTALL, RUNNERS } from "./protocol";

const LOADER = join(import.meta.dirname, "..", "template", "ta-runner");
const RUNNER = "console.log(JSON.stringify({ argv: process.argv.slice(2), " +
  "digest: process.env.TA_RUNNER_DIGEST }));\n";
const SHA = createHash("sha256").update(RUNNER).digest("hex");
/** A uid no one holds: an unprivileged user, as a run's or the sync agent's. */
const OTHER = 64_123;

let root = "";

beforeAll(() => {
  if (process.getuid?.() !== 0) {
    throw new Error("the loader's tests need real root: run `npm test` in platform/sandbox " +
                    "(root-tests.mjs)");
  }
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

/** The loader as it ships, its three fixed paths the test's: each found exactly once. */
function copied(): string {
  let text = readFileSync(LOADER, "utf8");
  for (const [line, under] of [["RUNNERS=/opt/ta/runner", `RUNNERS=${root}/opt/ta/runner`],
                               ["INCOMING=/var/lib/ta/incoming",
                                `INCOMING=${root}/var/lib/ta/incoming`],
                               ["NODE=/usr/local/bin/node", `NODE=${process.execPath}`]]) {
    expect(text.split("\n").filter((each) => each === line)).toHaveLength(1);
    text = text.replace(`\n${line}\n`, `\n${under}\n`);
  }
  const path = join(root, "ta-runner");
  writeFileSync(path, text, { mode: 0o700 });
  return path;
}

let copy = "";

function box(): void {
  root = mkdtempSync(join(tmpdir(), "ta-loader-"));
  chmodSync(root, 0o755);  // as `/` is: every user passes through it
  mkdirSync(join(root, "opt/ta/runner"), { recursive: true, mode: 0o755 });
  mkdirSync(join(root, "var/lib/ta/incoming"), { recursive: true, mode: 0o700 });
  copy = copied();
}

function loader(...args: string[]): { code: number; out: string } {
  const done = spawnSync("/bin/sh", [copy, ...args], {
    encoding: "utf8", env: { PATH: process.env["PATH"] } });
  return { code: done.status ?? -1, out: done.stdout };
}

function upload(bytes: string, sha = SHA): void {
  writeFileSync(join(root, "var/lib/ta/incoming", sha), bytes, { mode: 0o600 });
}

describe("the loader", () => {
  it("takes no path from its environment, and ships with LF line ends", () => {
    const text = readFileSync(LOADER, "utf8");
    expect(text).not.toMatch(/TA_RUNNER_ROOT|TA_RUNNER_NODE/);
    expect(text).not.toContain("\r");
    expect(readFileSync(join(import.meta.dirname, "..", "..", "..", ".gitattributes"), "utf8"))
      .toMatch(/^platform\/sandbox\/template\/ta-runner text eol=lf$/m);
  });

  it("answers the protocol's exit codes, from the protocol's paths", () => {
    // The loader is shell, so its text is read: each line the protocol names, once.
    const lines = readFileSync(LOADER, "utf8").split("\n");
    for (const line of [`ABSENT=${EXIT.absent}`, `MISMATCH=${EXIT.mismatch}`,
                        `RUNNERS=${RUNNERS}`, `INCOMING=${INCOMING}`,
                        `if [ "\${1:-}" = ${INSTALL} ]; then`]) {
      expect(lines.filter((each) => each === line), line).toHaveLength(1);
    }
  });

  it("installs a runner whose bytes are its digest, root's alone to write", () => {
    box();
    upload(RUNNER);
    expect(loader("install", SHA).code).toBe(0);
    const file = join(root, "opt/ta/runner", SHA, "runner.mjs");
    const [dir, made] = [statSync(join(root, "opt/ta/runner", SHA)), statSync(file)];
    expect([dir.uid, dir.mode & 0o777, made.uid, made.mode & 0o777]).toEqual([0, 0o755, 0, 0o644]);
    expect(readdirSync(join(root, "var/lib/ta/incoming"))).toEqual([]);
    expect(readdirSync(join(root, "opt/ta/runner"))).toEqual([SHA]);
    // Another user can read it and cannot change it.
    const write = spawnSync("setpriv", [`--reuid=${OTHER}`, `--regid=${OTHER}`, "--clear-groups",
                                        "/bin/sh", "-c", `echo x >> ${file}`]);
    expect(write.status).not.toBe(0);
    const read = spawnSync("setpriv", [`--reuid=${OTHER}`, `--regid=${OTHER}`, "--clear-groups",
                                       "cat", file], { encoding: "utf8" });
    expect(read.stdout).toBe(RUNNER);
  });

  it("refuses bytes that are not their digest, installing nothing", () => {
    box();
    upload(`${RUNNER}// changed\n`);
    expect(loader("install", SHA).code).toBe(EXIT.mismatch);
    expect(readdirSync(join(root, "opt/ta/runner"))).toEqual([]);
    expect(readdirSync(join(root, "var/lib/ta/incoming"))).toEqual([]);
  });

  it("answers absent for a runner never uploaded, and refuses a name that is no digest", () => {
    box();
    expect(loader("install", SHA).code).toBe(EXIT.absent);
    expect(loader(SHA, "state").code).toBe(EXIT.absent);
    expect(loader("install", "../../etc").code).toBe(EXIT.usage);
    expect(loader("0".repeat(63), "state").code).toBe(EXIT.usage);
    expect(loader(`${"0".repeat(63)}G`, "state").code).toBe(EXIT.usage);
  });

  it("runs the runner of its digest, hashed again first, its digest in its environment", () => {
    box();
    upload(RUNNER);
    loader("install", SHA);
    const ran = loader(SHA, "launch", "run_1");
    expect(ran.code).toBe(0);
    expect(JSON.parse(ran.out)).toEqual({ argv: ["launch", "run_1"], digest: SHA });
    // Its bytes changed after the install: nothing runs.
    writeFileSync(join(root, "opt/ta/runner", SHA, "runner.mjs"), `${RUNNER}process.exit(7);\n`);
    const refused = loader(SHA, "launch", "run_1");
    expect(refused).toEqual({ code: EXIT.mismatch, out: "" });
  });

  it("installs again over a runner of the same digest, never half of one", () => {
    box();
    upload(RUNNER);
    loader("install", SHA);
    upload(RUNNER);
    expect(loader("install", SHA).code).toBe(0);
    expect(readdirSync(join(root, "opt/ta/runner"))).toEqual([SHA]);
    expect(existsSync(join(root, "opt/ta/runner", SHA, "runner.mjs"))).toBe(true);
  });
});
