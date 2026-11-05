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
  }, 30_000);

  it("counts its own thread's work", () => {
    const start = threadMs();
    const end = performance.now() + 200;
    let spins = 0;
    while (performance.now() < end) {
      spins += 1;
    }
    expect(spins).toBeGreaterThan(0);
    expect(threadMs(start)).toBeGreaterThan(150);
  });
});
