// The runner's tests, as root (slice S): the in-VM runner runs as root and makes files of
// other users, gives each run a uid of its own (`setpriv`) and kills a uid's processes, so
// its tests need real root and real uids. Nothing is skipped: run as root, or as a user with
// passwordless `sudo` (GitHub's runner has it, so `platform-tests` runs them there), or this
// fails loudly. Under `sudo`, what vitest writes in the package (its cache) is given back to
// the invoking user, so a later run as that user is not refused its own tree.
import { spawnSync } from "node:child_process";
import { chownSync, existsSync, lstatSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const vitest = join(dirname(createRequire(import.meta.url).resolve("vitest/package.json")),
                    "vitest.mjs");
// Arguments after the script go to vitest: the class filter, and a sizes shard.
const args = [vitest, "run", "--project", "runner", ...process.argv.slice(2)];

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
  const run = spawnSync(process.execPath, args, { cwd: here, stdio: "inherit" });
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
