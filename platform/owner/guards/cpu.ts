/**
 * The owner Worker's CPU limit an invocation, and a pure function's CPU measured in Node on
 * the thread that runs it (`process.threadCpuUsage`). An isolate's CPU is its own thread's,
 * so the process's other threads may not count: V8's collector and compiler helpers, and
 * any other worker thread. `process.cpuUsage` is the whole process's. It read a move's CPU
 * some 15% over its thread's on the build host and 4,354 ms on a CI runner;
 * `cpu.test.ts` holds the difference. The workerd the owner's tests run in has no CPU
 * clock: `process.cpuUsage` is not implemented there, and its clock is the wall's. Both are
 * V8, and what workerd itself spends is not measured.
 */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { readWrangler } from "../../ci/wrangler";

/**
 * Workers' CPU limit an invocation when the config sets none (30 s,
 * developers.cloudflare.com/workers/platform/limits/#cpu-time).
 */
const WORKERS_DEFAULT_CPU_MS = 30_000;
const OWNER = join(dirname(fileURLToPath(import.meta.url)), "..");
const config = readWrangler(OWNER) as { limits?: { cpu_ms?: number } };

/** The owner Worker's: its config's `limits.cpu_ms`, or Workers' default (it sets none). */
export const CPU_LIMIT_MS = config.limits?.cpu_ms ?? WORKERS_DEFAULT_CPU_MS;

/**
 * What one invocation's heaviest step may take on the host the guard runs on: a third of
 * the limit. Two thirds are left for a slower host (a CI runner among them) and for the
 * invocation's other work, as the memory leg holds the worst edit (`EDIT_BYTES_MAX`,
 * `limits-audit.md`). A step the code makes quadratic again passes it many times over.
 */
export const STEP_CPU_MS = CPU_LIMIT_MS / 3;

type ThreadClock = (previous?: NodeJS.CpuUsage) => NodeJS.CpuUsage;

/** This thread's CPU clock (the build host's Node 22.22 has it); none fails loudly. */
function threadClock(): ThreadClock {
  const clock = (process as unknown as { threadCpuUsage?: ThreadClock }).threadCpuUsage;
  if (typeof clock !== "function") {
    throw new Error("process.threadCpuUsage is missing: the CPU guards need a Node that has " +
                    "it, and never measure the whole process instead");
  }
  return clock.bind(process);
}

/** This thread's CPU so far, or since `start`, in ms: a synchronous act's own clock. */
export function threadMs(start = 0): number {
  const used = threadClock()();
  return (used.user + used.system) / 1000 - start;
}

/** The CPU `act` takes on this thread, in ms. */
export async function cpuMs(act: () => unknown): Promise<number> {
  const clock = threadClock();
  const start = clock();
  await act();
  const used = clock(start);
  return (used.user + used.system) / 1000;
}
