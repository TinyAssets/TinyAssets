/**
 * The runner tests' sandboxes themselves (`harness.ts`, `Box`): no host path a test sandbox
 * sees is writable from it (its runner, as root, `chmod`s under `find /` at `prepare` and
 * deletes a run user's files under `/tmp`, `/var/tmp` and `/dev/shm`, so a writable host
 * path would be the host's files changed); and the suite refuses loudly, never skips, on a
 * host where a sandbox cannot be made (`root-tests.mjs`).
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { Box, mountsOf } from "./harness";

let box: Box | null = null;
afterEach(() => {
  box?.remove();
  box = null;
});

describe("a test sandbox", () => {
  it("sees no host path it can write: each host mount in it is read-only, the rest its own",
     () => {
    box = new Box();
    const root = mountsOf("self").find((mount) => mount.target === box!.root)!;
    // The host's mounts, as the suite sees them (the box's root, its own, aside).
    const host = new Set(mountsOf("self").filter((mount) => mount.target !== box!.root)
      .map((mount) => mount.device));
    const mounts = box.mounts();
    const hosts = mounts.filter((mount) => host.has(mount.device));
    expect(hosts.map((mount) => mount.target)).toEqual(expect.arrayContaining(
      ["/usr", "/usr/local/bin/node"]));
    expect(hosts.filter((mount) => !mount.options.includes("ro"))
      .map((mount) => mount.target)).toEqual([]);
    // What is not the host's is the box's own: its root (and so its /etc, /tmp, /var/tmp),
    // its /usr/local, /dev, /dev/pts, /dev/shm and /proc.
    expect(mounts.filter((mount) => !host.has(mount.device)).map((mount) => mount.target)
      .sort()).toEqual(["/", "/dev", "/dev/pts", "/dev/shm", "/proc", "/usr/local"]);
    expect(mounts.find((mount) => mount.target === "/")!.device).toBe(root.device);
    // Root in the box writes none of them.
    for (const mount of hosts) {
      const done = box.exec(["/bin/sh", "-c", `if [ -d "$0" ]; then touch "$0/.ta-guard"; ` +
                                              'else touch -c "$0"; fi', mount.target]);
      expect(done.status, mount.target).not.toBe(0);
      expect(done.stderr, mount.target).toMatch(/Read-only file system/);
    }
  });
});

describe("the suite", () => {
  it("refuses loudly, exit 2 with its reason, where a sandbox cannot be made: never a skip",
     () => {
    // A host with none of util-linux's tools: the preflight's first check refuses.
    const empty = mkdtempSync(join(tmpdir(), "ta-no-tools-"));
    try {
      const done = spawnSync(process.execPath, [
        join(import.meta.dirname, "..", "..", "root-tests.mjs"), "runner/no-such-file.test.ts",
      ], { env: { ...process.env, PATH: empty }, encoding: "utf8" });
      expect(done.status, done.stderr).toBe(2);
      expect(done.stderr).toMatch(new RegExp("^root-tests: the runner's test sandboxes " +
                                             "cannot be made on this host: `unshare` does not " +
                                             "run \\("));
      expect(done.stderr).toMatch(/They are not skipped: a skip is not a pass\.\n$/);
    } finally {
      rmSync(empty, { recursive: true, force: true });
    }
  });
});
