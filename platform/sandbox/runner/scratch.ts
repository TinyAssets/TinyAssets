/**
 * The sandbox's scratch area and its disk (the scratch-versus-storage design, 2026-11-03):
 * where an agent clones, installs and builds, apart from the owner's files.
 *
 * - **Scratch** is `/scratch/<agent>`, one directory an agent, root's at the top and group
 *   `ta`'s below (setgid, group-writable, as `/workspace`), so every run user of the sandbox
 *   may work in it. It is never synced and never counted in the owner's storage: it is
 *   bounded only by the sandbox's own disk, a vendor limit (E2B's per plan, the same on every
 *   tier). It is kept between runs in the paused sandbox as a cache. A run's engine is told
 *   where it is (`TA_SCRATCH`, and the MCP door's instructions), and its tools' caches go
 *   there (`SCRATCH_ENV`).
 * - **Pressure.** Before a run starts, the disk's free space is read (`statfs`). Below the
 *   low water (a tenth of the disk, past the runner's reserve), the least recently used
 *   agents' scratch goes first, never a live run's agent, until the free space is back above
 *   it; and any agent's scratch unused for `scratch_retention_days` (the control record's,
 *   handed in the start document) goes whatever the disk holds. What went is told to the
 *   agent and the user in plain words, and their storage is unaffected: a full working disk
 *   never offers an upgrade, since every tier has the same disk.
 * - **The reserve.** The runner keeps `RESERVE_BYTES` of the disk as a ballast file in its own
 *   state directory. When a write of the runner's own state meets `ENOSPC`, the ballast goes
 *   and the write is tried again (`writeOrRelease`), so a full disk never leaves a run's state
 *   unwritten, nor the run `unknown`; the ballast is made again once there is room.
 */
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, renameSync, rmSync,
         statfsSync, statSync, utimesSync, writeFileSync, writeSync } from "node:fs";
import { dirname, join } from "node:path";

export const SCRATCH = "/scratch";
/**
 * The runner's own room on the disk: its run state and the sessions staged for sync-out at a
 * run's end. A technical ceiling: what a run stages is the sessions the owner's files do not
 * hold yet (never the engine's whole history, `runner.ts`), in the common case the one it
 * changed, at most the largest engine transcript measured (86 MB); the reserve goes before
 * that staging whenever the disk has less free than it. Staging happens before the sync
 * agent's lock, so runs of one sandbox that end together stage together; beyond this
 * reserve, ext4 keeps its blocks for root (320 MB on our template), where root stages.
 */
export const RESERVE_BYTES = 256 * 1024 * 1024;
/**
 * The free share of the disk below which old scratch is let go before a run: a tenth. A
 * chosen threshold (`limits-audit.md`), refusing nothing: room for a run's working files
 * (a tenth of 14.4 GB, about 1.4 GB, holds a large clone and its build) before it starts.
 */
export const LOW_WATER_SHARE = 0.1;
const DAY_MS = 86_400_000;

/** The tools' caches, pointed into an agent's scratch (their own names, as data). */
export const SCRATCH_ENV: Readonly<Record<string, string>> = Object.freeze({
  TA_SCRATCH: "{scratch}",
  XDG_CACHE_HOME: "{scratch}/.cache",
  npm_config_cache: "{scratch}/.cache/npm",
  PIP_CACHE_DIR: "{scratch}/.cache/pip",
});

/** The run's environment additions for `agent`'s scratch at `dir`. */
export function scratchEnv(dir: string): Record<string, string> {
  return Object.fromEntries(Object.entries(SCRATCH_ENV)
    .map(([name, value]) => [name, value.replaceAll("{scratch}", dir)]));
}

export interface Disk {
  readonly free_bytes: number;
  readonly total_bytes: number;
  readonly free_inodes: number;
  readonly total_inodes: number;
}

/** The disk `path` is on, as `statfs` reads it (space an unprivileged user may take). */
export function diskOf(path: string): Disk {
  const fs = statfsSync(path);
  return { free_bytes: fs.bavail * fs.bsize, total_bytes: fs.blocks * fs.bsize,
           free_inodes: fs.ffree, total_inodes: fs.files };
}

/**
 * When each agent's scratch was last used: one stamp file an agent in the runner's own state
 * directory (`dir`), its modification time the last use. Each runner writes its own agent's
 * stamp alone, so runners at once never lose each other's (no file read, changed and written
 * back). Without a `dir` (a test's) it lives in memory.
 */
export interface ScratchIndex {
  readonly dir?: string;
  used: Record<string, number>;
}

/** The stamps under `dir`, made when missing. */
export function readIndex(dir: string): ScratchIndex {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const used: Record<string, number> = {};
  for (const agent of readdirSync(dir)) {
    try {
      used[agent] = statSync(join(dir, agent)).mtimeMs;
    } catch {
      // gone meanwhile: another runner let that scratch go
    }
  }
  return { dir, used };
}

/** `agent`'s scratch used at `now`: its stamp alone written. */
export function markUsed(index: ScratchIndex, agent: string, now: number): void {
  index.used[agent] = now;
  if (index.dir !== undefined) {
    const stamp = join(index.dir, agent);
    writeFileSync(stamp, "", { mode: 0o600 });
    utimesSync(stamp, now / 1000, now / 1000);
  }
}

function forget(index: ScratchIndex, agent: string): void {
  delete index.used[agent];
  if (index.dir !== undefined) {
    rmSync(join(index.dir, agent), { force: true });
  }
}

export interface Pruned {
  /** Agents whose scratch went for being unused past the retention. */
  readonly expired: readonly string[];
  /** Agents whose scratch went, least recently used first, to free the disk. */
  readonly evicted: readonly string[];
  readonly freed_bytes: number;
  readonly disk: Disk;
}

/**
 * Old scratch let go under `scratch` (the area's local path): first every agent's unused
 * for `retentionDays` (none when the record names none), then, while the disk's free space
 * is under the low water, the least recently used, never one in `live`. An agent the index
 * has never seen counts as used now (its scratch came before the index did).
 */
export function pruneScratch(scratch: string, index: ScratchIndex, now: number,
                             retentionDays: number | null, live: ReadonlySet<string>,
                             disk: (path: string) => Disk = diskOf): Pruned {
  const agents = existsSync(scratch) ? readdirSync(scratch) : [];
  for (const agent of agents) {
    if (index.used[agent] === undefined) {
      markUsed(index, agent, now);
    }
  }
  for (const known of Object.keys(index.used)) {
    if (!agents.includes(known)) {
      forget(index, known);
    }
  }
  const before = disk(scratch);
  const expired: string[] = [];
  const evicted: string[] = [];
  const drop = (agent: string) => {
    rmSync(join(scratch, agent), { recursive: true, force: true });
    forget(index, agent);
  };
  if (retentionDays !== null) {
    for (const [agent, used] of Object.entries(index.used)) {
      if (!live.has(agent) && now - used > retentionDays * DAY_MS) {
        drop(agent);
        expired.push(agent);
      }
    }
  }
  const low = (d: Disk) => d.free_bytes < d.total_bytes * LOW_WATER_SHARE;
  const oldest = Object.entries(index.used).filter(([agent]) => !live.has(agent))
    .sort(([, a], [, b]) => a - b).map(([agent]) => agent);
  for (const agent of oldest) {
    if (!low(disk(scratch))) {
      break;
    }
    drop(agent);
    evicted.push(agent);
  }
  const after = disk(scratch);
  return { expired, evicted, freed_bytes: Math.max(0, after.free_bytes - before.free_bytes),
           disk: after };
}

/** When scratch went: which agents' and why, and the disk after, which the app says in words. */
export interface ScratchCleared {
  readonly type: "ta.scratch_cleared";
  readonly evicted: readonly string[];
  readonly expired: readonly string[];
  readonly freed_bytes: number;
  readonly free_bytes: number;
  readonly total_bytes: number;
}

/**
 * What the agent and the user are told when scratch went, as its facts: the app's words
 * (`doors/app/view.js`) say the working disk is temporary and the same on every plan, so
 * they never offer an upgrade, and that the owner's storage is unaffected. Null when none
 * went.
 */
export function scratchCleared(pruned: Pruned): ScratchCleared | null {
  if (pruned.evicted.length + pruned.expired.length === 0) {
    return null;
  }
  return { type: "ta.scratch_cleared", evicted: pruned.evicted, expired: pruned.expired,
           freed_bytes: pruned.freed_bytes, free_bytes: pruned.disk.free_bytes,
           total_bytes: pruned.disk.total_bytes };
}

/** The ballast that keeps the runner's own room: made when there is room for it and more. */
export function keepReserve(path: string, bytes = RESERVE_BYTES,
                            disk: (path: string) => Disk = diskOf): boolean {
  if (existsSync(path)) {
    return true;
  }
  if (disk(dirname(path)).free_bytes < 2 * bytes) {
    return false;
  }
  const block = new Uint8Array(1024 * 1024);
  const temp = `${path}.${process.pid}.tmp`;  // its own: two runners may make it at once
  const fd = openSync(temp, "w", 0o600);
  try {
    for (let written = 0; written < bytes; written += block.byteLength) {
      writeSync(fd, block, 0, Math.min(block.byteLength, bytes - written));
    }
  } catch (error) {
    closeSync(fd);
    rmSync(temp, { force: true });
    if ((error as NodeJS.ErrnoException).code === "ENOSPC") {
      return false;
    }
    throw error;
  }
  closeSync(fd);
  renameSync(temp, path);
  return true;
}

/**
 * `write` done; on `ENOSPC`, the reserve let go and `write` tried once more. True when it
 * landed; false when the disk is full even so (the caller keeps its state in memory and
 * goes on). Any other error is thrown.
 */
export function writeOrRelease(write: () => void, reserve: string): boolean {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      write();
      return true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOSPC") {
        throw error;
      }
      if (attempt === 0) {
        rmSync(reserve, { force: true });
      }
    }
  }
  return false;
}
