/**
 * The runner tests' sandboxes themselves (`harness.ts`, `Box`): no host path a test sandbox
 * sees is writable from it (its runner, as root, `chmod`s under `find /` at `prepare` and
 * deletes a run user's files under `/tmp`, `/var/tmp` and `/dev/shm`, so a writable host
 * path would be the host's files changed), nor does the making of one write through the
 * host's `/etc`; its teardown leaves nothing; and the suite refuses loudly, never skips, on a
 * host where a sandbox cannot be made (`root-tests.mjs`).
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

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
      .filter((target) => target !== "/proc/sysrq-trigger")
      .sort()).toEqual(["/", "/dev", "/dev/pts", "/dev/shm", "/proc", "/proc/sys", "/usr/local"]);
    expect(mounts.find((mount) => mount.target === "/")!.device).toBe(root.device);
    // Its /etc's files are its own: regular files, never a link out of the copy.
    for (const file of ["passwd", "group", "gshadow", "resolv.conf"]) {
      expect(box.exec(["/bin/sh", "-c", '[ -f "$0" ] && [ ! -L "$0" ]', `/etc/${file}`]).status,
             file).toBe(0);
    }
    // Its /proc is its own, but the kernel's knobs in it are the host's: read-only.
    for (const knob of mounts.filter((mount) => /^\/proc\/sys(rq-trigger)?$/.test(mount.target))) {
      expect(knob.options, knob.target).toContain("ro");
    }
    expect(box.exec(["/bin/sh", "-c", ": >> /proc/sys/kernel/printk"]).stderr)
      .toMatch(/Read-only file system/);
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

  // A host whose tools run but refuse one step of a box: the tool answers its probe, then
  // refuses its real use (or, for the read-only bind, does nothing and answers 0).
  it.each([
    ["pivot_root", "true", "exit 1"],
    ["mount", 'echo " $* " | grep -q -- "remount,bind,ro"', "exit 0"],
  ])("refuses loudly, exit 2, where %s refuses its step", (tool, when, then) => {
    const shims = mkdtempSync(join(tmpdir(), "ta-shims-"));
    try {
      const real = spawnSync("/bin/sh", ["-c", `command -v ${tool}`], { encoding: "utf8" })
        .stdout.trim();
      writeFileSync(join(shims, tool), `#!/bin/sh\ncase " $* " in *" --version "*) exec ${
        real} "$@" ;; esac\nif ${when}; then ${then}; fi\nexec ${real} "$@"\n`, { mode: 0o755 });
      const done = spawnSync(process.execPath, [
        join(import.meta.dirname, "..", "..", "root-tests.mjs"), "runner/no-such-file.test.ts",
      ], { env: { ...process.env, PATH: `${shims}:${process.env["PATH"]}` }, encoding: "utf8" });
      expect(done.status, done.stderr).toBe(2);
      expect(done.stderr).toMatch(/^root-tests: the runner's test sandboxes cannot be made on this host: a box of the preflight's own/);
    } finally {
      rmSync(shims, { recursive: true, force: true });
    }
  });
});

describe("a test sandbox's teardown", () => {
  it("leaves no process of the box, no mount and no root behind", () => {
    box = new Box();
    box.live();
    const { init, base, root } = box;
    const processes = box.processesOf(0);
    expect(processes.length).toBeGreaterThanOrEqual(2);  // its init and the live one
    box.remove();
    box = null;
    for (const pid of [init, ...processes]) {
      expect(existsSync(`/proc/${pid}`), String(pid)).toBe(false);
    }
    expect(mountsOf("self").some((mount) => mount.target === root)).toBe(false);
    expect(existsSync(base)).toBe(false);
  });

  it("is its init's own when the harness dies: the box ends as the harness's end of its " +
     "lifeline closes, as at a worker killed outright", async () => {
    box = new Box();
    box.live();
    const processes = box.processesOf(0);  // its init and the live one
    expect(processes).toContain(box.init);
    box.lifeline!.end();
    for (const until = Date.now() + 5_000; processes.some((pid) => existsSync(`/proc/${pid}`));) {
      expect(Date.now()).toBeLessThan(until);
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  });
});

describe("a test sandbox's /etc", () => {
  const SANDBOX = join(import.meta.dirname, "..", "..");
  const VITEST = join(dirname(createRequire(import.meta.url).resolve("vitest/package.json")),
                      "vitest.mjs");

  // A host whose /etc/resolv.conf is a link (systemd-resolved's relative one, as GitHub's
  // runner has, or an absolute one), simulated in a mount namespace of its own: the host's
  // /etc there is a copy with that link, the real one untouched. The guard's test, run in it,
  // makes a box: it must work, and write nothing through the link.
  it.each(["relative", "absolute"])("is the box's own where the host's resolv.conf is a %s link: " +
                                    "no write reaches the host, and the box works", (kind) => {
    const scratch = mkdtempSync(join(tmpdir(), "ta-etc-"));
    try {
      const victim = join(scratch, "victim");
      writeFileSync(victim, "nameserver 192.0.2.1\n");
      mkdirSync(join(scratch, "etc"));
      const target = kind === "relative" ? "../run/systemd/resolve/stub-resolv.conf" : victim;
      const simulated = [
        'mount -t tmpfs -o size=64m tmpfs "$1"',
        'cp -a /etc "$1/etc"',
        'ln -sfn "$2" "$1/etc/resolv.conf"',
        'mount --bind "$1/etc" /etc',
        '[ -L /etc/resolv.conf ]',
        'exec "$3" "$4" run --project runner runner/test/box.test.ts -t "sees no host path"',
      ].join(" && ");
      const done = spawnSync("unshare", ["--mount", "--propagation", "private", "--", "/bin/sh",
                                         "-c", simulated, "etc", join(scratch, "etc"), target,
                                         process.execPath, VITEST],
                             { cwd: SANDBOX, encoding: "utf8" });
      expect(done.status, `${done.stdout}${done.stderr}`).toBe(0);
      expect(done.stdout).toMatch(/1 passed/);
      expect(readFileSync(victim, "utf8")).toBe("nameserver 192.0.2.1\n");
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });
});
