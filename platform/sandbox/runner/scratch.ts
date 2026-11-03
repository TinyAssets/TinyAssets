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
import { closeSync, existsSync, openSync, readdirSync, readFileSync, renameSync, rmSync,
         statfsSync, writeFileSync, writeSync } from "node:fs";
import { dirname, join } from "node:path";

export const SCRATCH = "/scratch";
/**
 * The runner's own room on the disk: its run state, the outbox and the staged sessions it
 * syncs out at a run's end. A technical ceiling: the largest engine transcript the scratch
 * research measured (86 MB) staged once, with its state and room to spare.
 */
export const RESERVE_BYTES = 256 * 1024 * 1024;
/** The free share of the disk below which old scratch is let go before a run: a tenth. */
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

/** When each agent's scratch was last used, kept in the runner's own state directory. */
export interface ScratchIndex {
  used: Record<string, number>;
}

export function readIndex(path: string): ScratchIndex {
  try {
    const read = JSON.parse(readFileSync(path, "utf8")) as ScratchIndex;
    return { used: { ...read.used } };
  } catch {
    return { used: {} };
  }
}

export function writeIndex(path: string, index: ScratchIndex): void {
  const temp = `${path}.${process.pid}.tmp`;  // its own: two runners may write at once
  writeFileSync(temp, JSON.stringify(index), { mode: 0o600 });
  renameSync(temp, path);
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
    index.used[agent] ??= now;
  }
  for (const known of Object.keys(index.used)) {
    if (!agents.includes(known)) {
      delete index.used[known];
    }
  }
  const before = disk(scratch);
  const expired: string[] = [];
  const evicted: string[] = [];
  const drop = (agent: string) => {
    rmSync(join(scratch, agent), { recursive: true, force: true });
    delete index.used[agent];
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

/** Bytes as a person reads them, decimal (as storage is sold). */
function shown(bytes: number): string {
  const gb = bytes / 1e9;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;
}

/**
 * What the agent and the user are told when scratch went: the working disk is temporary
 * and the same on every plan, so this never offers an upgrade; the owner's storage is
 * unaffected.
 */
export function scratchNotice(pruned: Pruned): string | null {
  const gone = [...pruned.evicted, ...pruned.expired];
  if (gone.length === 0) {
    return null;
  }
  const why = pruned.evicted.length > 0
    ? "The sandbox's working disk was running low"
    : "Some working files had not been used for a long time";
  return `${why}, so old scratch was cleared (${gone.join(", ")}; ${shown(pruned.freed_bytes)} ` +
    `freed; ${shown(pruned.disk.free_bytes)} of ${shown(pruned.disk.total_bytes)} free now). ` +
    "Scratch is a temporary cache: a cleared repository is cloned again when needed. Your " +
    "storage is unaffected.";
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
