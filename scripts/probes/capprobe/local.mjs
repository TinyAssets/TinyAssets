#!/usr/bin/env node
/**
 * The cap probe's harness check, in miniflare: both Workers load from their own wrangler
 * configs, `drive` runs every case with short waits, and `/results` must come back as
 * well-formed JSON with every case finished and every burst complete.
 *
 * It proves the harness, never the cap: workerd's local limit enforcer ignores
 * subrequests, so every call passes here. `cap-probe.yml` runs it before deploying.
 * Needs `npm ci` in `platform/` (it borrows that lockfile's wrangler and miniflare).
 *
 *   node scripts/probes/capprobe/local.mjs [--out results.json]
 */
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { CASES, drive, finished, summarize } from "./drive.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(HERE, "..", "..", "..", "platform");
const fromPlatform = createRequire(join(PLATFORM, "package.json"));
const load = async (name) => import(pathToFileURL(fromPlatform.resolve(name)).href);

const { unstable_getMiniflareWorkerOptions: workerOptions } = await load("wrangler");
const { Miniflare, convertV4MiniflareOptions } = await load("miniflare");

const KEY = "local-key";

/** The runtime and bindings each config declares; the rest is wrangler's dev defaults. */
function worker(config, extra) {
  const { workerOptions: options, main } = workerOptions(join(HERE, config));
  const { compatibilityDate, compatibilityFlags, durableObjects, serviceBindings } = options;
  return { compatibilityDate, compatibilityFlags, durableObjects, serviceBindings, ...extra,
           modules: true, scriptPath: main };
}

/** The global `fetch` stand-in: the callee's public URL pongs; everything else is a 502. */
function outbound(request) {
  return new URL(request.url).hostname === "callee.local"
    ? new Response("pong")
    : new Response("local stand-in", { status: 502 });
}

const mf = new Miniflare(convertV4MiniflareOptions({
  workers: [
    worker("caller.wrangler.jsonc", {
      name: "ta-capprobe",
      bindings: { RUN_KEY: KEY, CALLEE_URL: "https://callee.local" },
      outboundService: outbound,
    }),
    worker("callee.wrangler.jsonc", { name: "ta-capprobe-callee" }),
  ],
}));

const problems = [];
try {
  const request = (path, init) => mf.dispatchFetch(`http://capprobe.local${path}`, {
    ...init, headers: { ...(init.headers ?? {}), "x-probe-key": KEY },
  });
  const refused = await mf.dispatchFetch("http://capprobe.local/health");
  if (refused.status !== 403) {
    problems.push(`a request without the key got ${refused.status}, not 403`);
  }
  const report = await drive({
    request,
    params: { intervalMs: 20, holdMs: 300, beatMs: 50, messages: 5, waitMs: 5000, timeoutMs: 2000 },
    timing: { pollMs: 50, healthMs: 5000, alarmsFirstReadMs: 100, alarmsMs: 20_000,
              backgroundSlackMs: 200,
              m10FirstReadMs: 200, m10GraceMs: 10_000, planIters: 1e6 },
  });
  const raw = await (await request("/results", { method: "GET" })).text();
  const parsed = JSON.parse(raw);
  for (const id of CASES) {
    const stored = parsed.results[id];
    if (!finished(id, stored)) {
      problems.push(`${id} did not finish: ${JSON.stringify(stored).slice(0, 300)}`);
      continue;
    }
    const results = id === "M12" ? [stored.A, stored.B] : [stored.result];
    for (const result of results) {
      if (result.status === "failed") {
        problems.push(`${id} failed: ${result.error}`);
      }
      for (const [kind, value] of Object.entries(result)) {
        if (value && typeof value === "object" && "ok" in value && value.ok !== value.n) {
          problems.push(`${id} ${kind}: ${value.ok}/${value.n} (${value.firstError})`);
        }
      }
    }
  }
  const m6 = parsed.results.M6?.result;
  if (m6 && m6.ok !== m6.params.n) {
    problems.push(`M6 background: ${m6.ok}/${m6.params.n}`);
  }
  const m6a = parsed.results.M6a;
  if (!m6a?.alarm?.loopPresent || !m6a.result?.calls?.every((c) => c.inAlarm)) {
    problems.push(`M6a: the alarm did not await the loop: ${JSON.stringify(m6a?.alarm)}`);
  }
  const m12 = parsed.results.M12;
  if (!m12?.A?.waitedForB?.met || !m12?.B?.waitedForA?.met) {
    problems.push(`M12: the two invocations never met: ${JSON.stringify(m12).slice(0, 300)}`);
  }
  if (parsed.results.M10?.result?.outcome !== "finished") {
    problems.push(`M10: ${JSON.stringify(parsed.results.M10).slice(0, 300)}`);
  }
  if (parsed.results.M9?.result?.echoesAfter !== 5) {
    problems.push(`M9: ${JSON.stringify(parsed.results.M9?.result).slice(0, 300)}`);
  }
  if (parsed.results.M11?.result?.targets?.length !== 7) {
    problems.push("M11: not every target was tried");
  }
  if (!report.plan.survivedCpuBurn) {
    problems.push(`plan check failed locally: ${report.plan.status}`);
  }
  console.log(summarize(report));
  const out = process.argv.indexOf("--out");
  if (out > 0) {
    writeFileSync(process.argv[out + 1], `${JSON.stringify(parsed, null, 2)}\n`);
  }
} finally {
  await mf.dispose();
}

if (problems.length) {
  console.error(`::error::cap probe harness check failed:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log(`cap probe harness check passed: ${CASES.length} cases ran and /results is well-formed JSON`);
