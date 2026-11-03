/**
 * The latency probe's harness check, before any deploy (latency-probe.yml runs it): it proves
 * the harness, never a reading. Needs `npm ci` in `platform/`.
 *   1. every config is written, the owner's runtime is the owner Worker's own, and wrangler
 *      bundles each Worker (`wrangler deploy --dry-run`);
 *   2. the three Workers run in miniflare (the files Worker with a bucket, the owner-shaped
 *      Worker with its two SQLite classes, the door) and drive.mjs's every case runs through
 *      the door, with small n, every case answering samples and no failure;
 *   3. the door refuses a call without the run key, the cleanup leaves the bucket empty, and
 *      the percentile function is checked on known samples.
 *
 *   node scripts/probes/latencyprobe/local.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

import { configs, ownerRuntime } from "./config.mjs";
import { percentile, plan, stats } from "./drive.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(HERE, "..", "..", "..", "platform");
const require = createRequire(join(PLATFORM, "package.json"));
const { Miniflare, convertV4MiniflareOptions } = require("miniflare");
const WRANGLER = join(PLATFORM, "node_modules", ".bin", "wrangler");

function check(name, ok) {
  console.log(`${ok ? "ok" : "FAIL"}: ${name}`);
  assert.ok(ok, name);
}

// 3 first: the arithmetic.
check("percentile is nearest-rank", percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) === 5
      && percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) === 10
      && percentile(Array.from({ length: 300 }, (_, i) => i + 1), 99) === 297);
check("stats of nothing are empty", stats([]).p50 === null && stats([3]).p99 === 3);

// 1. Configs and bundles.
const runtime = ownerRuntime();
const written = configs("ta-latencyprobe-local");
check("the owner copy runs the owner Worker's date, flags and limits",
      JSON.stringify([written.owner.compatibility_date, written.owner.compatibility_flags,
                      written.owner.limits])
      === JSON.stringify([runtime.compatibility_date, runtime.compatibility_flags,
                          runtime.limits])
      && runtime.compatibility_flags.includes("disable_ctx_exports"));
check("only the door has a workers.dev URL",
      written.door.workers_dev === true && written.owner.workers_dev === false
      && written.files.workers_dev === false);
check("the shard binding is same-script (no script_name)",
      written.owner.durable_objects.bindings.every((b) => b.script_name === undefined));
check("without a bucket, no Worker binds R2", (() => {
  const none = configs(null);
  return none.owner.r2_buckets.length === 0 && none.files.r2_buckets.length === 0
    && none.owner.services.length === 0;
})());
const out = mkdtempSync(join(tmpdir(), "latencyprobe-"));
try {
  execFileSync(process.execPath, [join(HERE, "config.mjs"), "--out", out, "--bucket",
                                   "ta-latencyprobe-local"]);
  for (const name of ["files", "owner", "door"]) {
    execFileSync(WRANGLER, ["deploy", "--dry-run", "--outdir", join(out, `${name}-bundle`),
                            "--config", join(out, `${name}.wrangler.json`)],
                 { stdio: "pipe", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    check(`wrangler bundles ${name}`, true);
  }
  let refused = false;
  try {
    execFileSync(process.execPath, [join(HERE, "config.mjs"), "--out", out, "--bucket", "prod"],
                 { stdio: "pipe" });
  } catch {
    refused = true;
  }
  check("config.mjs refuses a bucket not named ta-latencyprobe-*", refused);
} catch (error) {
  rmSync(out, { recursive: true, force: true });
  throw error;
}

// 2. The Workers in miniflare, from the bundles wrangler made, driven case by case.
const date = runtime.compatibility_date;
const bundle = (name) => {
  const path = join(out, `${name}-bundle`, `${name}.js`);
  return { modulesRoot: join(out, `${name}-bundle`),
           modules: [{ type: "ESModule", path, contents: readFileSync(path, "utf8") }] };
};
const trace = () => new Response("colo=LOCAL\nloc=XX\n");
const mf = new Miniflare(convertV4MiniflareOptions({
  workers: [
    { name: "ta-latencyprobe-door", ...bundle("door"),
      compatibilityDate: date, compatibilityFlags: ["nodejs_compat"],
      bindings: { RUN_KEY: "local-key" },
      durableObjects: { OWNER: { className: "Owner", scriptName: "ta-latencyprobe" } } },
    { name: "ta-latencyprobe", ...bundle("owner"), compatibilityDate: date,
      compatibilityFlags: runtime.compatibility_flags,
      durableObjects: { SHARD: { className: "Shard", useSQLite: true },
                        OWNER_SELF: { className: "Owner", useSQLite: true } },
      serviceBindings: { FILES: "ta-latencyprobe-files" }, r2Buckets: ["BUCKET"],
      outboundService: trace },
    { name: "ta-latencyprobe-files", ...bundle("files"), compatibilityDate: date,
      compatibilityFlags: runtime.compatibility_flags, r2Buckets: ["BUCKET"],
      outboundService: trace },
  ],
}));
try {
  const call = async (method, body, key = "local-key") => {
    const response = await mf.dispatchFetch(`http://door.local/call/run-1-owner/${method}`, {
      method: "POST", body: JSON.stringify(body),
      headers: { "x-probe-key": key, "x-probe-run": "run-1", "content-type": "application/json" },
    });
    return { status: response.status, body: await response.json() };
  };
  check("the door refuses a wrong key", (await call("where", {}, "wrong")).status === 403);
  const where = await call("where", { count: 2 });
  check("where answers every object's colo", where.status === 200
        && where.body.result.owner.colo === "LOCAL" && where.body.result.files.colo === "LOCAL"
        && where.body.result.shards.length === 2);
  for (const step of plan({ r2: true, n: 12 }).slice(1)) {
    const answer = await call(step.method, step.body);
    const result = answer.body.result;
    check(`${step.name}: ${result?.samples?.length ?? 0} samples, ${result?.failed ?? "?"} failed`,
          answer.status === 200 && result.failed === 0 && result.samples.length === step.body.n);
  }
  const cleanup = await call("cleanup", {});
  check("the cleanup empties the bucket", cleanup.status === 200 && cleanup.body.result.left === 0
        && cleanup.body.result.deleted > 0);
} finally {
  await mf.dispose();
  rmSync(out, { recursive: true, force: true });
}
console.log("harness ok (no reading: latencies here are local)");
