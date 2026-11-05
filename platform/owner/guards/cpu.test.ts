/**
 * The CPU guards' clock (`cpu.ts`) is the measuring thread's own: CPU that another thread
 * of the process spends while a step is measured (V8's helpers, another worker thread)
 * never counts. `process.cpuUsage`, the whole process's, counted it, so the move's guard
 * read 4,354 ms on a CI runner where its own thread spent less.
 */
import { Worker } from "node:worker_threads";

import { describe, expect, it } from "vitest";

import { cpuMs, threadMs } from "./cpu";

/** A worker thread that spins for `ms` of wall time, then ends. */
const spinning = (ms: number) => new Worker(
  `const end = Date.now() + ${ms}; while (Date.now() < end) {}`, { eval: true });

describe("the CPU guards' clock", () => {
  it("counts only its own thread: another thread spinning beside it adds nothing", async () => {
    let wall = 0;
    const ms = await cpuMs(async () => {
      const from = performance.now();
      const worker = spinning(500);
      await new Promise((resolve) => worker.once("exit", resolve));
      wall = performance.now() - from;
    });
    expect(wall).toBeGreaterThanOrEqual(450);
    // The thread only waited: well under the half-second the other thread spun.
    expect(ms).toBeLessThan(150);
  });

  it("counts its own thread's work", () => {
    // Spun until the process has spent 200 ms of CPU, not for 200 ms of wall time: on a
    // loaded host a wall-time spin gets less CPU than its length, which says nothing about
    // the clock. Only this thread is busy here, so nearly all the process's CPU is its own.
    const process0 = process.cpuUsage();
    const start = threadMs();
    let spins = 0;
    const spent = () => {
      const used = process.cpuUsage(process0);
      return (used.user + used.system) / 1000;
    };
    while (spent() < 200) {
      spins += 1;
    }
    expect(spins).toBeGreaterThan(0);
    expect(threadMs(start)).toBeGreaterThan(spent() / 2);
  });
});
