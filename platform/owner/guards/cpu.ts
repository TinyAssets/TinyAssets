/**
 * The owner Worker's CPU limit an invocation, and a pure function's CPU measured in Node
 * (`process.cpuUsage`): the workerd the owner's tests run in has no `process.cpuUsage`, and
 * its clock there is the wall's. Both are V8; what workerd itself spends is not measured.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";

/**
 * Workers' CPU limit an invocation when the config sets none (30 s,
 * developers.cloudflare.com/workers/platform/limits/#cpu-time).
 */
const WORKERS_DEFAULT_CPU_MS = 30_000;
const OWNER = join(dirname(fileURLToPath(import.meta.url)), "..");
const config = ts.parseConfigFileTextToJson(
  "wrangler.jsonc", readFileSync(join(OWNER, "wrangler.jsonc"), "utf8")).config as
  { limits?: { cpu_ms?: number } };

/** The owner Worker's: its config's `limits.cpu_ms`, or Workers' default (it sets none). */
export const CPU_LIMIT_MS = config.limits?.cpu_ms ?? WORKERS_DEFAULT_CPU_MS;

/** The CPU `act` takes, in ms. */
export async function cpuMs(act: () => unknown): Promise<number> {
  const start = process.cpuUsage();
  await act();
  const used = process.cpuUsage(start);
  return (used.user + used.system) / 1000;
}
