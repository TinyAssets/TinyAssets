/**
 * The runner's loader (`template/ta-runner`), as it ships, byte for byte, at its real path in
 * a test sandbox (`test/harness.ts`, `Box`): a runner uploaded under
 * `/var/lib/ta/incoming/<sha>` is installed only when its bytes are its digest, root's alone
 * to write and readable by every user (the sync agent runs it); a command runs the runner of
 * its digest, re-hashed first, with the digest in its environment; a runner absent (24),
 * bytes that are not their digest (25) or a name that is not a digest (2) run nothing.
 */
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { EXIT, INCOMING, incoming, INSTALL, LOADER, RUNNERS } from "./protocol";
import { Box } from "./test/harness";

const SHIPPED = join(import.meta.dirname, "..", "template", "ta-runner");
const RUNNER = "console.log(JSON.stringify({ argv: process.argv.slice(2), " +
  "digest: process.env.TA_RUNNER_DIGEST }));\n";
const SHA = createHash("sha256").update(RUNNER).digest("hex");
/** A uid no one holds: an unprivileged user, as a run's or the sync agent's. */
const OTHER = 64_123;

let box: Box | null = null;

beforeAll(() => {
  if (process.getuid?.() !== 0) {
    throw new Error("the loader's tests need real root: run `npm test` in platform/sandbox " +
                    "(root-tests.mjs)");
  }
});

afterEach(() => {
  box?.remove();
  box = null;
});

function made(): Box {
  box = new Box();
  return box;
}

function loader(...args: string[]): { code: number; out: string } {
  const done = box!.exec([LOADER, ...args]);
  return { code: done.status ?? -1, out: done.stdout };
}

function upload(bytes: string, sha = SHA): void {
  writeFileSync(box!.path(incoming(sha)), bytes, { mode: 0o600 });
}

describe("the loader", () => {
  it("takes no path from its environment, and ships with LF line ends", () => {
    const text = readFileSync(SHIPPED, "utf8");
    expect(text).not.toMatch(/TA_RUNNER_ROOT|TA_RUNNER_NODE/);
    expect(text).not.toContain("\r");
    expect(readFileSync(join(import.meta.dirname, "..", "..", "..", ".gitattributes"), "utf8"))
      .toMatch(/^platform\/sandbox\/template\/ta-runner text eol=lf$/m);
  });

  it("answers the protocol's exit codes, from the protocol's paths", () => {
    // The loader is shell, so its text is read: each line the protocol names, once.
    const lines = readFileSync(SHIPPED, "utf8").split("\n");
    for (const line of [`ABSENT=${EXIT.absent}`, `MISMATCH=${EXIT.mismatch}`,
                        `RUNNERS=${RUNNERS}`, `INCOMING=${INCOMING}`,
                        `if [ "\${1:-}" = ${INSTALL} ]; then`]) {
      expect(lines.filter((each) => each === line), line).toHaveLength(1);
    }
  });

  it("is the loader as it ships, at the protocol's path", () => {
    made();
    expect(box!.exec(["cmp", LOADER, "-"], readFileSync(SHIPPED, "utf8")).status).toBe(0);
    expect(box!.exec(["stat", "-c", "%u %a", LOADER]).stdout.trim()).toBe("0 700");
  });

  it("installs a runner whose bytes are its digest, root's alone to write", () => {
    made();
    upload(RUNNER);
    expect(loader(INSTALL, SHA).code).toBe(0);
    const file = `${RUNNERS}/${SHA}/runner.mjs`;
    const [dir, file_] = [statSync(box!.path(`${RUNNERS}/${SHA}`)), statSync(box!.path(file))];
    expect([dir.uid, dir.mode & 0o777, file_.uid, file_.mode & 0o777])
      .toEqual([0, 0o755, 0, 0o644]);
    expect(readdirSync(box!.path(INCOMING))).toEqual([]);
    expect(readdirSync(box!.path(RUNNERS))).toEqual([SHA]);
    // Another user can read it and cannot change it.
    const write = box!.exec(["setpriv", `--reuid=${OTHER}`, `--regid=${OTHER}`,
                             "--clear-groups", "/bin/sh", "-c", `echo x >> ${file}`]);
    expect(write.status).not.toBe(0);
    const read = box!.exec(["setpriv", `--reuid=${OTHER}`, `--regid=${OTHER}`,
                            "--clear-groups", "cat", file]);
    expect(read.stdout).toBe(RUNNER);
  });

  it("refuses bytes that are not their digest, installing nothing", () => {
    made();
    upload(`${RUNNER}// changed\n`);
    expect(loader(INSTALL, SHA).code).toBe(EXIT.mismatch);
    expect(readdirSync(box!.path(RUNNERS))).toEqual([]);
    expect(readdirSync(box!.path(INCOMING))).toEqual([]);
  });

  it("answers absent for a runner never uploaded, and refuses a name that is no digest", () => {
    made();
    expect(loader(INSTALL, SHA).code).toBe(EXIT.absent);
    expect(loader(SHA, "state").code).toBe(EXIT.absent);
    expect(loader(INSTALL, "../../etc").code).toBe(EXIT.usage);
    expect(loader("0".repeat(63), "state").code).toBe(EXIT.usage);
    expect(loader(`${"0".repeat(63)}G`, "state").code).toBe(EXIT.usage);
  });

  it("runs the runner of its digest, hashed again first, its digest in its environment", () => {
    made();
    upload(RUNNER);
    loader(INSTALL, SHA);
    const ran = loader(SHA, "launch", "run_1");
    expect(ran.code).toBe(0);
    expect(JSON.parse(ran.out)).toEqual({ argv: ["launch", "run_1"], digest: SHA });
    // Its bytes changed after the install: nothing runs.
    writeFileSync(box!.path(`${RUNNERS}/${SHA}/runner.mjs`), `${RUNNER}process.exit(7);\n`);
    const refused = loader(SHA, "launch", "run_1");
    expect(refused).toEqual({ code: EXIT.mismatch, out: "" });
  });

  it("installs again over a runner of the same digest, never half of one", () => {
    made();
    upload(RUNNER);
    loader(INSTALL, SHA);
    upload(RUNNER);
    expect(loader(INSTALL, SHA).code).toBe(0);
    expect(readdirSync(box!.path(RUNNERS))).toEqual([SHA]);
    expect(existsSync(box!.path(`${RUNNERS}/${SHA}/runner.mjs`))).toBe(true);
  });
});
