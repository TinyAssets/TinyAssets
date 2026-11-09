/**
 * The runner tests' sandboxes themselves (`harness.ts`, `Box`): no host path a test sandbox
 * sees is writable from it (its runner, as root, `chmod`s under `find /` at `prepare` and
 * deletes a run user's files under `/tmp`, `/var/tmp` and `/dev/shm`, so a writable host
 * path would be the host's files changed), nor does the making of one write through the
 * host's `/etc`; its teardown leaves nothing; and the suite refuses loudly, never skips, on a
 * host where a sandbox cannot be made (`root-tests.mjs`).
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { Box, mountsOf } from "./harness";
import { NODE } from "./layout";

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
    // The host's mounts, as the suite sees them (the box's own aside: its root, its /etc).
    const own = (target: string) => target === box!.base || target.startsWith(`${box!.base}/`);
    const host = new Set(mountsOf("self").filter((mount) => !own(mount.target))
      .map((mount) => mount.device));
    const mounts = box.mounts();
    const hosts = mounts.filter((mount) => host.has(mount.device));
    expect(hosts.map((mount) => mount.target)).toEqual(expect.arrayContaining(
      ["/usr/local/bin/node"]));
    // Its Node is root's alone, as the template's is, read-only, the host's own bytes.
    expect(box.exec(["stat", "-c", "%u %g %a", NODE]).stdout.trim()).toBe("0 0 755");
    expect(box.exec(["sha256sum", NODE]).stdout.split(" ")[0])
      .toBe(createHash("sha256").update(readFileSync(process.execPath)).digest("hex"));
    expect(hosts.filter((mount) => !mount.options.includes("ro"))
      .map((mount) => mount.target)).toEqual([]);
    // What is not the host's is the box's own: its root (and so its /tmp, /var/tmp), its
    // /etc and /usr overlays, /usr/local, /dev, /dev/pts, /dev/shm and /proc.
    expect(mounts.filter((mount) => !host.has(mount.device)).map((mount) => mount.target)
      .filter((target) => target !== "/proc/sysrq-trigger")
      .sort()).toEqual(["/", "/dev", "/dev/pts", "/dev/shm", "/etc", "/proc", "/proc/sys",
                        "/usr", "/usr/local"]);
    expect(mounts.find((mount) => mount.target === "/")!.device).toBe(root.device);
    // Its /etc and /usr are overlays: the host's beneath, never written, its writes on a disk
    // of the box's own above, outside its root. A write there lands above, never in the
    // host's; the overlays copy up metadata alone (`metacopy`).
    for (const tree of ["/etc", "/usr"]) {
      const overlay = mounts.find((mount) => mount.target === tree)!;
      expect(overlay.type, tree).toBe("overlay");
      expect(overlay.super, tree).toEqual(expect.arrayContaining([
        `lowerdir=${tree}`, `upperdir=${box.upperOf(tree)}`, "metacopy=on"]));
      expect(box.exec(["touch", `${tree}/.ta-guard`]).status, tree).toBe(0);
      expect(existsSync(join(box.upperOf(tree), ".ta-guard")), tree).toBe(true);
      expect(existsSync(`${tree}/.ta-guard`), tree).toBe(false);
    }
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

  it("refuses loudly, exit 2 with its reason, where its boxes' root-owned Node cannot be made",
     () => {
    // A temp dir no one may write (a read-only bind of an empty one, in a namespace of its
    // own): the suite's copy of Node has nowhere to go.
    const done = spawnSync("unshare", ["--mount", "--propagation", "private", "--", "/bin/sh",
                                       "-c", 'd=$(mktemp -d) && mount --bind "$d" "$d" && ' +
                                       'mount -o remount,bind,ro "$d" && { TMPDIR="$d" "$0" ' +
                                       '"$1" runner/no-such-file.test.ts; s=$?; umount "$d"; ' +
                                       'rmdir "$d"; exit "$s"; }', process.execPath,
                                       join(import.meta.dirname, "..", "..", "root-tests.mjs")],
                           { encoding: "utf8" });
    expect(done.status, done.stderr).toBe(2);
    expect(done.stderr).toMatch(new RegExp("^root-tests: the runner's test sandboxes cannot " +
                                           "be made on this host: a root-owned copy of Node " +
                                           "could not be made"));
  });

  // A host whose tools run but refuse one step of a box: the tool answers its probe, then
  // refuses its real use (or, for the read-only bind, does nothing and answers 0).
  it.each([
    ["pivot_root", "the pivot", "true", "exit 1"],
    ["mount", "a read-only bind", 'echo " $* " | grep -q -- "remount,bind,ro"', "exit 0"],
    ["mount", "an overlay", 'echo " $* " | grep -q -- " overlay "', "exit 32"],
  ])("refuses loudly, exit 2, where %s refuses %s", (tool, _step, when, then) => {
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
      // However the host refused, the preflight's probes wrote nothing of the host's.
      for (const tree of ["/etc", "/usr"]) {
        expect(readdirSync(tree).filter((name) => name.startsWith(".ta-")), tree).toEqual([]);
      }
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

  /**
   * A host simulated in a mount namespace of its own: its /etc and /usr each an overlay of the
   * real one (so the simulation copies nothing, and writes nothing to it), `shape` making them
   * the host's to simulate (`$SIM/etc`, `$SIM/usr`; `$NODE_BIN` the host's Node, which it may
   * replace; `$ARG` the caller's), bound over /etc and /usr there alone. The suite's own
   * command (`root-tests.mjs`, by that Node) runs the test of `file` matching `title` (the
   * guard's, by default) there, which makes a box; `after` then runs there. The namespace's
   * run: the suite's, then `after`'s output.
   */
  function simulated(shape: string, { arg = "", file = "runner/test/box.test.ts",
                                      title = "sees no host path", after = "true" } = {}) {
    const scratch = mkdtempSync(join(tmpdir(), "ta-etc-"));
    try {
      const script = [
        'mount -t tmpfs -o size=1g tmpfs "$SIM"',
        'mkdir "$SIM/etc-upper" "$SIM/etc-work" "$SIM/etc" "$SIM/usr-upper" "$SIM/usr-work" ' +
          '"$SIM/usr"',
        'mount -t overlay overlay -o "lowerdir=/etc,upperdir=$SIM/etc-upper,' +
          'workdir=$SIM/etc-work" "$SIM/etc"',
        'mount -t overlay overlay -o "lowerdir=/usr,upperdir=$SIM/usr-upper,' +
          'workdir=$SIM/usr-work" "$SIM/usr"',
        shape,
        'mount --bind "$SIM/etc" /etc',
        'mount --bind "$SIM/usr" /usr',
        '{ "$NODE_BIN" root-tests.mjs "$FILE" -t "$TITLE"; ran=$?; }',
        `{ ${after}; }`,
        'exit "$ran"',
      ].join(" && ");
      mkdirSync(join(scratch, "sim"));
      return spawnSync("unshare", ["--mount", "--propagation", "private", "--", "/bin/sh", "-c",
                                   script], {
        cwd: SANDBOX, encoding: "utf8",
        env: { ...process.env, SIM: join(scratch, "sim"), NODE_BIN: process.execPath, ARG: arg,
               FILE: file, TITLE: title } });
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  }

  // A host whose /etc/resolv.conf is a link (systemd-resolved's relative one, as GitHub's
  // runner has, or an absolute one): the box must work, and write nothing through the link.
  it.each(["relative", "absolute"])("is the box's own where the host's resolv.conf is a %s link: " +
                                    "no write reaches the host, and the box works", (kind) => {
    const scratch = mkdtempSync(join(tmpdir(), "ta-victim-"));
    try {
      const victim = join(scratch, "victim");
      writeFileSync(victim, "nameserver 192.0.2.1\n");
      const target = kind === "relative" ? "../run/systemd/resolve/stub-resolv.conf" : victim;
      const done = simulated('ln -sfn "$ARG" "$SIM/etc/resolv.conf" && ' +
                             '[ -L "$SIM/etc/resolv.conf" ]', { arg: target });
      expect(done.status, `${done.stdout}${done.stderr}`).toBe(0);
      expect(done.stdout).toMatch(/1 passed/);
      expect(readFileSync(victim, "utf8")).toBe("nameserver 192.0.2.1\n");
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });

  // A host whose /etc is larger than a box's whole disk, as GitHub's runner's is (its
  // /etc/skel holds a Rust and a .NET toolchain, gigabytes): the box takes none of it.
  it("is the box's own where the host's /etc is larger than the box's disk, and the box works",
     () => {
    // 480 MiB of real (not sparse) bytes, past a box's 384 MiB.
    const done = simulated('mkdir -p "$SIM/etc/skel/.toolchain" && for n in 1 2 3; do ' +
                           'head -c 160M /dev/zero > "$SIM/etc/skel/.toolchain/$n" || exit 1; ' +
                           'done');
    expect(done.status, `${done.stdout}${done.stderr}`).toBe(0);
    expect(done.stdout).toMatch(/1 passed/);
  });

  // A host as GitHub's runner's is: its /etc holding a large file anyone may write (its
  // toolchains), its /usr a tree and a large file anyone may write (its JDKs, `/usr/lib/jvm`),
  // its Node another user's and writable by others (`setup-node`, in `/opt/hostedtoolcache`).
  // In one walk (`prepare`'s test, run there: its cost is the host's tree, so it is large, as
  // `runner.test.ts`'s own is), `prepare` takes every write away in the box alone, as it does
  // in a sandbox, copying no bytes (the overlays' `metacopy`, the suite's root-owned Node),
  // and the host's modes stay as they were.
  it("is the box's own where the host's /etc, /usr and Node are writable by others: prepare " +
     "takes every write away in the box alone", { tags: ["large"] }, () => {
    const done = simulated([
      'mkdir -p "$SIM/etc/skel/.toolchain" "$SIM/usr/lib/jvm-sim/lib" "$SIM/toolcache/bin"',
      'head -c 100M /dev/zero > "$SIM/etc/skel/.toolchain/big"',
      'head -c 100M /dev/zero > "$SIM/usr/lib/jvm-sim/lib/modules"',
      'chmod 0666 "$SIM/etc/skel/.toolchain/big" "$SIM/usr/lib/jvm-sim/lib/modules"',
      'chmod 0777 "$SIM/usr/lib/jvm-sim" "$SIM/usr/lib/jvm-sim/lib"',
      'cp "$NODE_BIN" "$SIM/toolcache/bin/node"',
      'chown 1001:1001 "$SIM/toolcache/bin/node"',
      'chmod 0777 "$SIM/toolcache/bin/node"',
      'NODE_BIN="$SIM/toolcache/bin/node"',
    ].join(" && "), { file: "runner/runner.test.ts", title: "prepare, at",
                      after: 'echo host modes $(stat -c %a /etc/skel/.toolchain/big ' +
                             '/usr/lib/jvm-sim /usr/lib/jvm-sim/lib ' +
                             '/usr/lib/jvm-sim/lib/modules) $(stat -c "%u %a" "$NODE_BIN")' });
    expect(done.status, `${done.stdout}${done.stderr}`).toBe(0);
    expect(done.stdout).toMatch(/1 passed/);
    expect(done.stdout).toMatch(/^host modes 666 777 777 666 1001 777$/m);
  });
});
