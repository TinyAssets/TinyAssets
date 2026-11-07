/**
 * The scratch area and the runner's room on the disk (`scratch.ts`): old scratch goes by the
 * retention, then least recently used while the disk is low, never a live run's agent; what
 * went is told plainly, never with an upgrade; a full disk never leaves the runner's state
 * unwritten while its reserve can go.
 */
import { existsSync, mkdirSync, mkdtempSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { type Disk, keepReserve, markUsed, pruneScratch, readIndex, scratchCleared, scratchEnv,
         writeOrRelease } from "./scratch";

const DAY = 86_400_000;
const NOW = 1_800_000_000_000;

/** A scratch area of agents, and a disk whose free space grows as their directories go. */
function area(agents: readonly string[], total = 100, freeWith = (left: number) => 50 - 15 * left) {
  const dir = mkdtempSync(join(tmpdir(), "scratch-"));
  for (const agent of agents) {
    mkdirSync(join(dir, agent, "repo"), { recursive: true });
    writeFileSync(join(dir, agent, "repo", "file"), agent);
  }
  const disk = (): Disk => ({ free_bytes: freeWith(readdirSync(dir).length), total_bytes: total,
                             free_inodes: 1000, total_inodes: 1000 });
  return { dir, disk };
}

describe("old scratch let go", () => {
  it("deletes what went unused past the retention, whatever the disk holds", () => {
    const { dir, disk } = area(["old", "recent"], 100, () => 90);
    const index = { used: { old: NOW - 31 * DAY, recent: NOW - 10 * DAY } };
    const pruned = pruneScratch(dir, index, NOW, 30, new Set(), disk);
    expect(pruned.expired).toEqual(["old"]);
    expect(pruned.evicted).toEqual([]);
    expect(readdirSync(dir)).toEqual(["recent"]);
    expect(Object.keys(index.used)).toEqual(["recent"]);
  });

  it("keeps everything by age when the record names no retention", () => {
    const { dir, disk } = area(["old"], 100, () => 90);
    const pruned = pruneScratch(dir, { used: { old: NOW - 3650 * DAY } }, NOW, null,
                                new Set(), disk);
    expect([pruned.expired, readdirSync(dir)]).toEqual([[], ["old"]]);
  });

  it("frees the least recently used while the disk is low, never a live run's agent", () => {
    // Free space: 50 - 15 a directory left; the low water is a tenth of 100.
    const { dir, disk } = area(["a", "b", "c", "d"]);
    const index = { used: { a: NOW - 1, b: NOW - 4, c: NOW - 3, d: NOW - 2 } };
    const pruned = pruneScratch(dir, index, NOW, 30, new Set(["b"]), disk);
    // b is the oldest but live: c goes, then d (free back to 20, above 10).
    expect(pruned.evicted).toEqual(["c", "d"]);
    expect(readdirSync(dir).sort()).toEqual(["a", "b"]);
    expect(pruned.freed_bytes).toBe(30);
  });

  it("counts an agent the index never saw as used now", () => {
    const { dir, disk } = area(["new"], 100, () => 90);
    const index = { used: {} as Record<string, number> };
    pruneScratch(dir, index, NOW, 30, new Set(), disk);
    expect(index.used).toEqual({ new: NOW });
  });
});

describe("what the agent and the user are told", () => {
  // Its facts alone: the app says them in words (`doors/guards/app-words.test.ts`).
  it("names what went and the disk after, and nothing when none went", () => {
    expect(scratchCleared({ expired: ["old"], evicted: ["builder"], freed_bytes: 3_100_000_000,
                            disk: { free_bytes: 4_000_000_000, total_bytes: 15_000_000_000,
                                    free_inodes: 1, total_inodes: 1 } }))
      .toEqual({ type: "ta.scratch_cleared", evicted: ["builder"], expired: ["old"],
                 freed_bytes: 3_100_000_000, free_bytes: 4_000_000_000,
                 total_bytes: 15_000_000_000 });
    expect(scratchCleared({ expired: [], evicted: [], freed_bytes: 0,
                            disk: { free_bytes: 1, total_bytes: 1, free_inodes: 1,
                                    total_inodes: 1 } })).toBeNull();
  });

  it("points the run's caches into its scratch", () => {
    expect(scratchEnv("/scratch/main")).toMatchObject({ TA_SCRATCH: "/scratch/main",
                                                        XDG_CACHE_HOME: "/scratch/main/.cache" });
  });
});

describe("the runner's room on the disk", () => {
  const enospc = () => Object.assign(new Error("no space"), { code: "ENOSPC" });

  it("lets its reserve go and writes again when the disk is full", () => {
    const dir = mkdtempSync(join(tmpdir(), "reserve-"));
    const reserve = join(dir, "reserve");
    expect(keepReserve(reserve, 4096, () => ({ free_bytes: 1e9, total_bytes: 1e10,
                                               free_inodes: 1, total_inodes: 1 }))).toBe(true);
    expect(statSync(reserve).size).toBe(4096);
    let tries = 0;
    expect(writeOrRelease(() => {
      tries += 1;
      if (existsSync(reserve)) {
        throw enospc();
      }
    }, reserve)).toBe(true);
    expect([tries, existsSync(reserve)]).toEqual([2, false]);
  });

  it("goes on with its state in memory when even that is not enough", () => {
    expect(writeOrRelease(() => { throw enospc(); }, "/nonexistent/reserve")).toBe(false);
    expect(() => writeOrRelease(() => { throw new Error("EACCES"); }, "/nonexistent/reserve"))
      .toThrow("EACCES");
  });

  it("is made only when the disk has room for it and more", () => {
    const dir = mkdtempSync(join(tmpdir(), "reserve-"));
    const reserve = join(dir, "reserve");
    expect(keepReserve(reserve, 4096, () => ({ free_bytes: 8000, total_bytes: 1e10,
                                               free_inodes: 1, total_inodes: 1 }))).toBe(false);
    expect(existsSync(reserve)).toBe(false);
  });
});

describe("the scratch's last uses", () => {
  it("are one stamp an agent, so runners at once never lose each other's", () => {
    const dir = join(mkdtempSync(join(tmpdir(), "stamps-")), "used");
    const one = readIndex(dir);
    const other = readIndex(dir);
    markUsed(one, "a", NOW);
    markUsed(other, "b", NOW + 1_000);
    expect(readIndex(dir).used).toEqual({ a: NOW, b: NOW + 1_000 });
  });
});
