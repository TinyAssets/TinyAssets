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
import { createHash } from "node:crypto";
import { chmodSync, chownSync, copyFileSync, existsSync, lstatSync, mkdtempSync, readdirSync,
         readFileSync, readlinkSync, rmSync, statSync } from "node:fs";
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
 * and uts namespaces, a tmpfs, a read-only bind, an overlay of /etc and of /usr whose writes
 * stay above them, a /proc of the namespace's own, `nsenter` into them, `pivot_root`, and perl (each box's
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
      'mkdir "$1/proc" "$1/own" "$1/ro" "$1/old" "$1/etc" "$1/usr"',
      'mkdir -p "$1/w/etc/upper" "$1/w/etc/work" "$1/w/usr/upper" "$1/w/usr/work"',
      // A read-only bind, of a directory of its own: a bind that takes a write anyway never
      // writes the host's.
      'mount --bind "$1/own" "$1/ro"',
      'mount -o remount,bind,ro "$1/ro"',
      '{ ! touch "$1/ro/.ta-preflight" 2>/dev/null || ' +
        '{ echo "a read-only bind took a write" >&2; false; }; }',
      // An overlay of /etc and of /usr, each written above, never beneath.
      'mount -t overlay overlay -o "lowerdir=/etc,upperdir=$1/w/etc/upper,' +
        'workdir=$1/w/etc/work,metacopy=on" "$1/etc"',
      'mount -t overlay overlay -o "lowerdir=/usr,upperdir=$1/w/usr/upper,' +
        'workdir=$1/w/usr/work,metacopy=on" "$1/usr"',
      // Its write, by a name of this run's own, is above each, and not in the host's.
      'mark=".ta-preflight.$$.$(date +%s%N)"',
      'touch "$1/etc/$mark" "$1/usr/$mark"',
      '{ [ -e "$1/w/etc/upper/$mark" ] && [ -e "$1/w/usr/upper/$mark" ] && ' +
        '[ ! -e "/etc/$mark" ] && [ ! -e "/usr/$mark" ] || ' +
        '{ echo "an overlay of /etc or /usr wrote to the host" >&2; false; }; }',
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
        "read-only bind, overlays of /etc and /usr, its own /proc, `nsenter`, `pivot_root`) " +
        "failed " +
        `(${done.error?.message ?? `exit ${done.status}`}): ${done.stdout}${done.stderr}`.trim();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  return null;
}

/**
 * The suite's one Node for its boxes, made before any box: a copy of this Node, root's alone
 * (`root:root`, 0755), its bytes checked the same, in the suite's own temp dir on the disk.
 * Every box binds it read-only at the template's path, as the template's own Node is root's
 * (the host's may be another user's, writable by others: GitHub's `setup-node`). The host's
 * own Node is never changed. Its path, or why it cannot be made.
 */
function boxNode() {
  let suite = null;
  let node = "a temp dir";
  try {
    suite = mkdtempSync(join(tmpdir(), `ta-suite-${process.pid}-`));
    node = join(suite, "node");
    copyFileSync(process.execPath, node);
    chownSync(node, 0, 0);
    chmodSync(node, 0o755);
    const digest = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
    const made = statSync(node);
    if (made.uid !== 0 || made.gid !== 0 || (made.mode & 0o7777) !== 0o755
        || digest(node) !== digest(process.execPath)) {
      return { suite, refused: `a root-owned copy of Node (${node}) is not root's 0755 copy ` +
                               `of ${process.execPath}, byte for byte` };
    }
  } catch (error) {
    return { suite, refused: `a root-owned copy of Node could not be made in ${node}: ${error}` };
  }
  return { suite, node };
}

/**
 * What suites killed outright left (their temp dir outlives them, its Node copy about 118 MB),
 * removed before this one makes its own: each is named by its suite's pid (`boxNode`), and one
 * whose suite no longer runs is gone. Another suite's that still runs is kept.
 */
function sweep() {
  for (const name of readdirSync(tmpdir())) {
    const found = /^ta-suite-([0-9]+)-[A-Za-z0-9]{6}$/.exec(name);
    if (found === null || Number(found[1]) === process.pid) {
      continue;
    }
    try {
      process.kill(Number(found[1]), 0);
      continue;  // its suite runs
    } catch (error) {
      if (error.code !== "ESRCH") {
        continue;
      }
    }
    rmSync(join(tmpdir(), name), { recursive: true, force: true });
  }
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
  sweep();
  const made = boxNode();
  const refused = made.refused ?? preflight();
  const suite = made.suite;
  if (refused !== null) {
    if (suite !== null) {
      rmSync(suite, { recursive: true, force: true });
    }
    process.stderr.write(
      "root-tests: the runner's test sandboxes cannot be made on this host: " +
      `${refused}. They need root's mount, pid and uts namespaces, tmpfs, read-only binds, ` +
      "overlayfs, nsenter and pivot_root (util-linux), perl, and a root-owned copy of Node. " +
      "They are not skipped: a skip is not a pass.\n");
    process.exit(2);
  }
  // The suite's own mount namespace; the harness is told the one it left, and its boxes' Node.
  const run = spawnSync("unshare", ["--mount", "--propagation", "private", "--",
                                    process.execPath, ...args],
                        { cwd: here, stdio: "inherit",
                          env: { ...process.env, TA_BOX_NODE: made.node,
                                 TA_HOST_MOUNTS: readlinkSync("/proc/self/ns/mnt") } });
  rmSync(suite, { recursive: true, force: true });
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
