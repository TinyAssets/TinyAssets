/**
 * The scratch area and the runner's room on the disk (`scratch.ts`), on real disks (a tmpfs
 * each, in the suite's own mount namespace: `statfs` reads it exactly, and a write past it
 * meets a real `ENOSPC`): old scratch goes least recently used while the disk is low, never
 * by age alone and never a live run's agent; what went is told plainly, never with an
 * upgrade; a full disk never leaves the runner's state unwritten while its reserve can go.
 */
import { existsSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { diskLow, diskOf, keepReserve, letGoSessions, markUsed, pruneScratch, readIndex,
         RESERVE_BYTES, scratchCleared, scratchEnv, writeOrRelease } from "./scratch";
import { disk } from "./test/harness";

const DAY = 86_400_000;
const NOW = 1_800_000_000_000;
const PAGE = 4096;

const disks: { remove(): void }[] = [];
afterEach(() => {
  for (const each of disks.splice(0)) {
    each.remove();
  }
});

/** A disk of `size` with a scratch area on it (each agent `pages` of files) and its index. */
function area(agents: Readonly<Record<string, number>>, size = "8m") {
  const made = disk(size);
  disks.push(made);
  const dir = join(made.dir, "scratch");
  for (const [agent, pages] of Object.entries(agents)) {
    mkdirSync(join(dir, agent, "repo"), { recursive: true });
    writeFileSync(join(dir, agent, "repo", "file"), Buffer.alloc(pages * PAGE, 1));
  }
  mkdirSync(dir, { recursive: true });
  const index = readIndex(join(made.dir, "used"));
  return { root: made.dir, dir, index };
}

/** The index's stamps, as the agents' last uses. */
function used(index: ReturnType<typeof readIndex>, at: Readonly<Record<string, number>>): void {
  for (const [agent, when] of Object.entries(at)) {
    markUsed(index, agent, when);
  }
}

describe("old scratch let go", () => {
  it("keeps scratch of any age while the disk has room", () => {
    const { dir, index } = area({ old: 1 });
    used(index, { old: NOW - 3650 * DAY });
    const pruned = pruneScratch(dir, index, NOW, new Set());
    expect([pruned.evicted, readdirSync(dir)]).toEqual([[], ["old"]]);
  });

  it("frees the least recently used while the disk is low, never a live run's agent", () => {
    // 8 MiB, its low water a tenth: 2048 pages, under 204.8 free is low. Four agents of 77
    // pages and a 1650-page file beside the area leave 90 free: one agent gone leaves 167
    // (still low), two leave 244.
    const { root, dir, index } = area({ a: 77, b: 77, c: 77, d: 77 });
    writeFileSync(join(root, "other"), Buffer.alloc(1650 * PAGE, 2));
    used(index, { a: NOW - 1, b: NOW - 4, c: NOW - 3, d: NOW - 2 });
    const pruned = pruneScratch(dir, index, NOW, new Set(["b"]));
    // b is the oldest but live: c goes, then d.
    expect(pruned.evicted).toEqual(["c", "d"]);
    expect(readdirSync(dir).sort()).toEqual(["a", "b"]);
    expect(readdirSync(index.dir).sort()).toEqual(["a", "b"]);  // their stamps gone with them
    expect(pruned.freed_bytes).toBe(2 * 77 * PAGE);
    expect(pruned.disk.total_bytes).toBe(2048 * PAGE);
  });

  it("counts an agent the index never saw as used now", () => {
    const { dir, index } = area({ new: 1 });
    pruneScratch(dir, index, NOW, new Set());
    expect(index.used).toEqual({ new: NOW });
    expect(statSync(join(index.dir, "new")).mtimeMs).toBe(NOW);
  });
});

describe("store copies let go", () => {
  /** Four held copies of 77 pages beside a file, leaving 90 pages free, as above. */
  function held() {
    const { root } = area({});
    writeFileSync(join(root, "other"), Buffer.alloc(1650 * PAGE, 2));
    const copies = ([["old", 1], ["older", 0], ["new", 2], ["newer", 3]] as const).map(([name, mtime]) => {
      const local = join(root, name);
      writeFileSync(local, Buffer.alloc(77 * PAGE, 3));
      return { local, size: 77 * PAGE, mtime_ms: mtime };
    });
    return { root, copies };
  }

  it("lets the least recently changed go while the disk is low, and stops once it is not", () => {
    const { root, copies } = held();
    expect(letGoSessions(root, copies)).toBe(2 * 77 * PAGE);
    expect(copies.map((copy) => existsSync(copy.local))).toEqual([false, false, true, true]);
    expect(diskLow(0, diskOf(root))).toBeNull();
  });

  it("lets none go while the disk has room, and says a disk still low with the store's bytes",
     () => {
    const { root, copies } = held();
    expect(letGoSessions(root, [])).toBe(0);
    expect(diskLow(9, diskOf(root))).toEqual({ type: "ta.disk_low", store_bytes: 9,
                                               free_bytes: diskOf(root).free_bytes,
                                               total_bytes: 2048 * PAGE });
    rmSync(join(root, "other"));
    expect(letGoSessions(root, copies)).toBe(0);
    expect(copies.every((copy) => existsSync(copy.local))).toBe(true);
  });
});

describe("what the agent and the user are told", () => {
  // Its facts alone: the app says them in words (`doors/guards/app-words.test.ts`).
  it("names what went and the disk after, and nothing when none went", () => {
    expect(scratchCleared({ evicted: ["builder"], freed_bytes: 3_100_000_000,
                            disk: { free_bytes: 4_000_000_000, total_bytes: 15_000_000_000,
                                    free_inodes: 1, total_inodes: 1 } }))
      .toEqual({ type: "ta.scratch_cleared", evicted: ["builder"],
                 freed_bytes: 3_100_000_000, free_bytes: 4_000_000_000,
                 total_bytes: 15_000_000_000 });
    expect(scratchCleared({ evicted: [], freed_bytes: 0,
                            disk: { free_bytes: 1, total_bytes: 1, free_inodes: 1,
                                    total_inodes: 1 } })).toBeNull();
  });

  it("points the run's caches into its scratch", () => {
    expect(scratchEnv("/scratch/main")).toMatchObject({ TA_SCRATCH: "/scratch/main",
                                                        XDG_CACHE_HOME: "/scratch/main/.cache" });
  });
});

describe("the runner's room on the disk", () => {
  /** The disk filled to its last byte, past what is already on it. */
  const fill = (dir: string) => {
    for (let n = 0; ; n++) {
      try {
        writeFileSync(join(dir, `fill-${n}`), Buffer.alloc(1024 * 1024));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOSPC") {
          throw error;
        }
        return;
      }
    }
  };

  it("lets its reserve go and writes again when the disk is full", () => {
    // Room for the reserve and as much again: it is made, at its whole size.
    const { root } = area({}, "520m");
    const reserve = join(root, "reserve");
    expect(keepReserve(reserve)).toBe(true);
    expect(statSync(reserve).size).toBe(RESERVE_BYTES);
    fill(root);
    let tries = 0;
    expect(writeOrRelease(() => {
      tries += 1;
      writeFileSync(join(root, "state.json"), Buffer.alloc(64 * 1024));
    }, reserve)).toBe(true);
    expect([tries, existsSync(reserve), statSync(join(root, "state.json")).size])
      .toEqual([2, false, 64 * 1024]);
  });

  it("goes on with its state in memory when even that is not enough", () => {
    const { root } = area({});
    fill(root);
    expect(writeOrRelease(() => {
      writeFileSync(join(root, "state.json"), Buffer.alloc(64 * 1024));
    }, join(root, "reserve"))).toBe(false);
    expect(() => writeOrRelease(() => { throw new Error("EACCES"); }, join(root, "reserve")))
      .toThrow("EACCES");
  });

  it("is made only when the disk has room for it and more", () => {
    // Under twice the reserve: none is made, and nothing is written.
    const { root } = area({}, "500m");
    const reserve = join(root, "reserve");
    expect(keepReserve(reserve)).toBe(false);
    expect(existsSync(reserve)).toBe(false);
    expect(readdirSync(root).sort()).toEqual(["scratch", "used"]);
  });
});

describe("the scratch's last uses", () => {
  it("are one stamp an agent, so runners at once never lose each other's", () => {
    const { root } = area({});
    const dir = join(root, "stamps");
    const one = readIndex(dir);
    const other = readIndex(dir);
    markUsed(one, "a", NOW);
    markUsed(other, "b", NOW + 1_000);
    expect(readIndex(dir).used).toEqual({ a: NOW, b: NOW + 1_000 });
  });
});
