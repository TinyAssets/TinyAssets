/**
 * The sandbox's users (slice S review H-4, M-1): every run runs as a user of its own, taken
 * from the template's pool (`ta-run-<n>`, each with a group of its own, in group `ta` for
 * the shared workspace) and given back when the run is over, so no two live runs share a
 * uid: neither reads the other's environment or files (`/proc` is `hidepid=2`), and every
 * process a run left behind is killed by its uid before the user is taken again. The sync
 * agent is `ta-sync`. Nothing here creates a user: the pool is the template's, as large as
 * the most live runs an owner may hold (`RUNS_CEILING`, the owner database's own costing).
 *
 * A process is started as one of them through `setpriv` (its real and effective uid and
 * gid, its supplementary groups exactly, its capabilities dropped, no new privileges), from
 * the root runner, which Node's own `uid` option cannot do (it drops every supplementary
 * group).
 */
import { mkdirSync, readdirSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

import { tryLock, unlock } from "./pid-lock";

export interface Identity {
  readonly name: string;
  readonly uid: number;
  readonly gid: number;
}

/** The template's users, by name, from its `/etc/passwd`. */
export function users(): Identity[] {
  return readFileSync("/etc/passwd", "utf8").split("\n").flatMap((line) => {
    const [name, , uid, gid] = line.split(":");
    return name && uid && gid ? [{ name, uid: Number(uid), gid: Number(gid) }] : [];
  });
}

export function group(name: string): number {
  for (const line of readFileSync("/etc/group", "utf8").split("\n")) {
    const [found, , gid] = line.split(":");
    if (found === name) {
      return Number(gid);
    }
  }
  throw new Error(`the template has no group ${name}`);
}

export function user(name: string): Identity {
  const found = users().find((each) => each.name === name);
  if (found === undefined) {
    throw new Error(`the template has no user ${name}`);
  }
  return found;
}

/** The pool's users' names: this and the user's number, from 0. */
export const POOL_PREFIX = "ta-run-";
/** The group every pool user is in, which the shared workspace is of. */
export const SHARED_GROUP = "ta";
/** The sync agent's user, and its own directory (0700): its mirror, sessions in and out. */
export const SYNC_USER = "ta-sync";
export const SYNC_HOME = "/var/lib/ta-sync";
/** E2B's own default user, whom its provisioning puts back in `sudo` at every boot. */
export const E2B_USER = "user";

/**
 * A pool user taken by this process: its lock (`pid-lock.ts`) in the root-only state
 * directory, one a pool user; a lock whose holder is gone is taken over (its holder died).
 * Every process the user still has is killed first.
 */
export function take(state: string): Identity {
  const dir = `${state}/pool`;
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  for (const candidate of users().filter((each) => each.name.startsWith(POOL_PREFIX))) {
    if (!tryLock(`${dir}/${candidate.name}`)) {
      continue;  // a live run's
    }
    killAll(candidate.uid);
    return candidate;
  }
  throw new Error("every run user of the template is taken");
}

/** The user given back, once every process of it is gone. */
export function give(state: string, taken: Identity): void {
  killAll(taken.uid);
  unlock(`${state}/pool/${taken.name}`);
}

/** The pids whose real uid is `uid`, read from `/proc` (root sees every one). */
export function processesOf(uid: number): number[] {
  return readdirSync("/proc").filter((name) => /^[0-9]+$/.test(name)).flatMap((name) => {
    try {
      const status = readFileSync(`/proc/${name}/status`, "utf8");
      const real = /^Uid:\s+([0-9]+)/m.exec(status)?.[1];
      return Number(real) === uid ? [Number(name)] : [];
    } catch {
      return [];
    }
  });
}

/**
 * The longest a user's processes may outlive their SIGKILL: the runner's heartbeat
 * (`HEARTBEAT_MS`, 20 s, which `guards/config.test.ts` holds equal), so a run's end is
 * held no longer than one more heartbeat. A process still there after it is stuck in the
 * kernel (uninterruptible sleep): the user is not given back, loudly.
 */
export const KILL_WITHIN_MS = 20_000;

/**
 * Every process of `uid` killed, until none is left: `kill -9 -1` run as that user (which
 * signals all of its processes at once, so a process forking as it dies is caught too),
 * then each one `/proc` still shows; any left past `KILL_WITHIN_MS` fails.
 */
export function killAll(uid: number): void {
  if (uid === 0) {
    throw new Error("root's processes are never killed by uid");
  }
  const deadline = Date.now() + KILL_WITHIN_MS;
  for (;;) {
    const left = processesOf(uid);
    if (left.length === 0) {
      return;
    }
    if (Date.now() > deadline) {
      throw new Error(`processes of uid ${uid} outlived their kill: ${left.join(" ")}`);
    }
    spawnSync("setpriv", [`--reuid=${uid}`, `--regid=${uid}`, "--clear-groups", "--",
                          "/bin/kill", "-9", "-1"], { stdio: "ignore" });
    for (const pid of left) {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        // gone already
      }
    }
  }
}

/** `argv` run as `as`, in `groups`, through `setpriv`, with no new privileges. */
export function asUser(as: { uid: number; gid: number }, groups: readonly number[],
                       argv: readonly string[]): string[] {
  return ["setpriv", `--reuid=${as.uid}`, `--regid=${as.gid}`,
          groups.length === 0 ? "--clear-groups" : `--groups=${groups.join(",")}`,
          "--no-new-privs", "--", ...argv];
}
