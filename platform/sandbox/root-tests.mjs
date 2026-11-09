// The runner's tests, as root (slice S): the in-VM runner runs as root and makes files of
// other users, gives each run a uid of its own (`setpriv`) and kills a uid's processes, so
// its tests need real root and real uids. Each test sandbox is a root of its own in mount,
// pid and uts namespaces of its own (`runner/test/harness.ts`), so the suite runs in a mount
// namespace of its own, which ends with it. Nothing is skipped: run as root, or as a user
// with passwordless `sudo` (GitHub's runner has it, so `platform-tests` runs them there),
// on a host that gives root those namespaces (checked first, `preflight`), or this fails
// loudly. Under `sudo`, what vitest writes in the package (its cache) is given back to the
// invoking user, so a later run as that user is not refused its own tree.
import { spawnSync } from "node:child_process";
import { chownSync, existsSync, lstatSync, mkdtempSync, readdirSync, readlinkSync,
         rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const vitest = join(dirname(createRequire(import.meta.url).resolve("vitest/package.json")),
                    "vitest.mjs");
// Arguments after the script go to vitest: the class filter, and a sizes shard.
const args = [vitest, "run", "--project", "runner", ...process.argv.slice(2)];

/**
 * What a test sandbox needs of the host, tried in a box of the preflight's own: mount, pid
 * and uts namespaces, a tmpfs, a read-only bind, an overlay of /etc whose writes stay above
 * it, a /proc of the namespace's own, `nsenter` into them, `pivot_root`, and perl (each box's
 * init). Why it cannot, or null.
 */
function preflight() {
  for (const [tool, probe] of [["unshare", ["--version"]], ["nsenter", ["--version"]],
                               ["mount", ["--version"]], ["pivot_root", ["--version"]],
                               ["setpriv", ["--version"]], ["perl", ["-e", "1"]]]) {
    const done = spawnSync(tool, probe, { stdio: "ignore" });
    if (done.error !== undefined || done.status !== 0) {
      return `\`${tool}\` does not run (${done.error?.message ?? `exit ${done.status}`})`;
    }
  }
  const dir = mkdtempSync(join(tmpdir(), "ta-preflight-"));
  try {
    const box = [
      'mount -t tmpfs -o size=1m,mode=0755 tmpfs "$1"',
      'mkdir "$1/proc" "$1/usr" "$1/old" "$1/upper" "$1/work" "$1/etc"',
      'mount --bind /usr "$1/usr"',
      'mount -o remount,bind,ro "$1/usr"',
      '{ ! touch "$1/usr/.ta-preflight" 2>/dev/null || ' +
        '{ echo "a read-only bind of /usr took a write" >&2; false; }; }',
      'mount -t overlay overlay -o "lowerdir=/etc,upperdir=$1/upper,workdir=$1/work,metacopy=on" ' +
        '"$1/etc"',
      'touch "$1/etc/.ta-preflight"',
      '{ [ ! -e /etc/.ta-preflight ] || ' +
        '{ echo "an overlay of /etc wrote to /etc" >&2; false; }; }',
      'mount -t proc proc "$1/proc"',
      // This shell, by the host's pid (the host's /proc is still at /proc), entered.
      "read -r self _ < /proc/self/stat",
      '[ "$(nsenter -t "$self" -m -p -u -- /bin/sh -c "echo \\$\\$")" -gt 1 ]',
      'cd "$1"',
      "pivot_root . old",
      "echo ready",
    ].join(" && ");
    const done = spawnSync("unshare", ["--mount", "--pid", "--fork", "--uts", "--propagation",
                                       "private", "--", "/bin/sh", "-c", box, "preflight", dir],
                           { encoding: "utf8" });
    if (done.status !== 0 || done.stdout.trim() !== "ready") {
      return "a box of the preflight's own (`unshare --mount --pid --fork --uts`, a tmpfs, a " +
        "read-only bind of /usr, an overlay of /etc, its own /proc, `nsenter`, `pivot_root`) " +
        "failed " +
        `(${done.error?.message ?? `exit ${done.status}`}): ${done.stdout}${done.stderr}`.trim();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  return null;
}

function giveBack(path, uid, gid) {
  const stat = lstatSync(path);
  if (stat.uid === 0) {
    chownSync(path, uid, gid);
  }
  if (stat.isDirectory()) {
    for (const entry of readdirSync(path)) {
      giveBack(join(path, entry), uid, gid);
    }
  }
}

if (process.getuid() === 0) {
  const refused = preflight();
  if (refused !== null) {
    process.stderr.write(
      "root-tests: the runner's test sandboxes cannot be made on this host: " +
      `${refused}. They need root's mount, pid and uts namespaces, tmpfs, read-only binds, ` +
      "overlayfs, nsenter and pivot_root (util-linux) and perl. They are not skipped: a skip is not a " +
      "pass.\n");
    process.exit(2);
  }
  // The suite's own mount namespace; the harness is told the one it left.
  const run = spawnSync("unshare", ["--mount", "--propagation", "private", "--",
                                    process.execPath, ...args],
                        { cwd: here, stdio: "inherit",
                          env: { ...process.env,
                                 TA_HOST_MOUNTS: readlinkSync("/proc/self/ns/mnt") } });
  const uid = Number(process.env.SUDO_UID);
  const gid = Number(process.env.SUDO_GID);
  if (Number.isInteger(uid) && uid > 0 && Number.isInteger(gid)) {
    // Vitest's cache, the workspace's (hoisted) and the package's own.
    for (const cache of [join(here, "..", "node_modules", ".vitest-cache"),
                         join(here, "node_modules")]) {
      if (existsSync(cache)) {
        giveBack(cache, uid, gid);
      }
    }
  }
  process.exit(run.status ?? 1);
}

if (spawnSync("sudo", ["-n", "true"], { stdio: "ignore" }).status !== 0) {
  process.stderr.write(
    "root-tests: the runner's tests need real root and real uids (they make files of other " +
    "users and run processes as them). Run them as root, or as a user with passwordless " +
    "sudo. They are not skipped: a skip is not a pass.\n");
  process.exit(2);
}
const run = spawnSync("sudo", ["-n", "--", process.execPath, join(here, "root-tests.mjs"),
                               ...process.argv.slice(2)],
                      { cwd: here, stdio: "inherit" });
process.exit(run.status ?? 1);
