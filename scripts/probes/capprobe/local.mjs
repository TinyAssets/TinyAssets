#!/usr/bin/env node
/**
 * The cap probe's harness check, in miniflare: both Workers load from their own wrangler
 * configs and `drive` runs every case with short waits. It checks that every case
 * finished with every burst complete, that each pre-spend ran at its size, that M12's B
 * started after A's first phase, and that `/results` is well-formed JSON. A second run on
 * the same objects then has one case refused with a Cloudflare-style 1101: the driver
 * must record it, carry on, and refuse that case's records from the first run as stale.
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

import { CASES, MAX_HOLD_MINUTES, drive, finished, summarize, worstCaseMs } from "./drive.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(HERE, "..", "..", "..", "platform");
const fromPlatform = createRequire(join(PLATFORM, "package.json"));
const load = async (name) => import(pathToFileURL(fromPlatform.resolve(name)).href);

const { unstable_getMiniflareWorkerOptions: workerOptions } = await load("wrangler");
const { Miniflare, convertV4MiniflareOptions } = await load("miniflare");

const KEY = "local-key";
/** cap-probe.yml's "Run every case" timeout-minutes; the driver's worst case must fit. */
const STEP_TIMEOUT_MINUTES = 55;
const PARAMS = Object.freeze({ intervalMs: 20, holdMs: 300, beatMs: 50, messages: 5,
                               waitMs: 5000, settleMs: 50, timeoutMs: 2000 });
const TIMING = Object.freeze({
  pollMs: 50, requestMs: 20_000, healthMs: 5000, planIters: 1e6, alarmsFirstReadMs: 100,
  alarmsGraceMs: 20_000, backgroundSlackMs: 200, backgroundGraceMs: 20_000, m12DelayMs: 300,
  m10FirstReadMs: 200, m10GraceMs: 10_000,
});

/** The runtime and bindings each config declares; the rest is wrangler's dev defaults. */
function worker(config, extra) {
  const { workerOptions: options, main } = workerOptions(join(HERE, config));
  const { compatibilityDate, compatibilityFlags, durableObjects, serviceBindings } = options;
  return { compatibilityDate, compatibilityFlags, durableObjects, serviceBindings, ...extra,
           modules: true, scriptPath: main };
}

/** The global `fetch` stand-in: the callee's public URL pongs for the key; else a 502. */
function outbound(request) {
  if (new URL(request.url).hostname !== "callee.local") {
    return new Response("local stand-in", { status: 502 });
  }
  return request.headers.get("x-probe-key") === KEY
    ? new Response("pong") : new Response("forbidden", { status: 403 });
}

const problems = [];
const expect = (ok, what) => {
  if (!ok) {
    problems.push(what);
  }
};
const show = (value) => JSON.stringify(value)?.slice(0, 300);

/** Every burst in a record (one level down), with where it sits. */
function bursts(record) {
  return Object.entries(record ?? {}).filter(([, v]) => v && typeof v === "object" && "kind" in v && "ok" in v);
}

function checkFirstRun(report, parsed) {
  expect(Object.keys(report.driverErrors).length === 0, `driver errors: ${show(report.driverErrors)}`);
  expect(!report.notes.some((note) => note.includes("another run")), `stale notes: ${show(report.notes)}`);
  for (const id of CASES) {
    const stored = parsed.results[id];
    expect(finished(id, stored), `${id} did not finish: ${show(stored)}`);
    for (const record of id === "M12" ? [stored?.A, stored?.B] : [stored?.result, stored?.alarm]) {
      expect(!record || record.run === report.runId, `${id}: a record without this run's id`);
      expect(record?.status !== "failed", `${id} failed: ${record?.error}`);
      for (const [kind, b] of bursts(record)) {
        expect(b.ok === b.n, `${id} ${kind}: ${b.ok}/${b.n} (${b.firstError})`);
      }
    }
  }
  const r = parsed.results;
  expect(r.M4?.result?.workerPre?.n === 16, `M4: the Worker's pre-spend: ${show(r.M4?.result?.workerPre)}`);
  expect(r.M5?.result?.armPre?.n === 8, `M5: arm()'s pre-spend: ${show(r.M5?.result?.armPre)}`);
  for (const id of ["M6", "M6a"]) {
    const loop = r[id]?.result;
    expect(loop?.startPre?.n === 8, `${id}: the starting RPC's pre-spend: ${show(loop?.startPre)}`);
    expect(loop?.ok === loop?.params?.n, `${id} loop: ${loop?.ok}/${loop?.params?.n}`);
    expect(loop?.calls?.every((c) => typeof c.inAlarmBefore === "boolean"
                                     && typeof c.inAlarmAfter === "boolean"), `${id}: inAlarm samples`);
  }
  expect(r.M6?.result?.calls?.every((c) => !c.inAlarmBefore && !c.inAlarmAfter), "M6: a call in an alarm");
  const m6a = r.M6a;
  expect(m6a?.alarm?.loopPresent && m6a.alarm.alarmPre?.n === 4, `M6a alarm: ${show(m6a?.alarm)}`);
  expect(m6a?.result?.calls?.length === m6a?.result?.params?.n
         && m6a.result.calls.every((c) => c.inAlarmBefore && c.inAlarmAfter),
         "M6a: the alarm was not awaiting every loop call");
  expect(report.checks.m6aOrder?.ok, `M6a order: ${show(report.checks.m6aOrder)}`);
  const ws = r.M9?.result;
  expect(ws?.open === "ok" && ws.bridgeEcho && ws.rpc1?.n === 20 && ws.rpc2?.n === 20
         && ws.echoesBefore === PARAMS.messages && ws.echoesAfter === PARAMS.messages, `M9: ${show(ws)}`);
  const { A, B } = r.M12 ?? {};
  expect(report.checks.m12Order?.ok, `M12 order: ${show(report.checks.m12Order)}`);
  expect(B?.startedAt > A?.startedAt + A?.phase1?.totalMs, "M12: B started before A's phase 1 ended");
  expect(A?.waitedForB?.met && A?.waitedForBEnd?.met && B?.waitedForA?.met, `M12 waits: ${show({ A, B })}`);
  expect(A?.phase3?.n === 10 && B?.phase3 === undefined, "M12: only A makes the after-B calls");
  expect(r.M10?.result?.outcome === "finished" && r.M10?.attempts?.list?.length === 1, `M10: ${show(r.M10)}`);
  expect(r.M11?.result?.targets?.length === 7, "M11: not every target was tried");
  expect(report.plan?.survivedCpuBurn, `plan check: ${show(report.plan)}`);
}

function checkSecondRun(report, firstRunId) {
  const m2 = report.driverErrors.M2;
  expect(m2?.status === 500 && m2.cfError === "1101", `run 2: M2's failure not recorded: ${show(m2)}`);
  expect(report.results.M2?.result?.stale === true && report.results.M2.result.run === firstRunId,
         `run 2: M2's run-1 record was not refused: ${show(report.results.M2)}`);
  for (const id of ["M1", "M4", "M5", "M12"]) {
    expect(finished(id, report.results[id]), `run 2: ${id} did not finish after M2 failed`);
  }
  expect(report.phase === "done", `run 2 ended in phase ${report.phase}`);
}

const mf = new Miniflare(convertV4MiniflareOptions({
  workers: [
    worker("caller.wrangler.jsonc", {
      name: "ta-capprobe",
      bindings: { RUN_KEY: KEY, CALLEE_URL: "https://callee.local" },
      outboundService: outbound,
    }),
    worker("callee.wrangler.jsonc", { name: "ta-capprobe-callee", bindings: { RUN_KEY: KEY } }),
  ],
}));

try {
  const request = (path, init) => mf.dispatchFetch(`http://capprobe.local${path}`, {
    ...init, headers: { ...(init.headers ?? {}), "x-probe-key": KEY },
  });
  expect((await mf.dispatchFetch("http://capprobe.local/health")).status === 403,
         "a request without the key was not refused");
  expect((await request("/results", { method: "GET" })).status === 400,
         "a request without a run id was not refused");

  const phases = [];
  const first = await drive({ request, runId: "local-1", params: PARAMS, timing: TIMING,
                              onPhase: (report) => phases.push(report.phase) });
  const raw = await (await request("/results", { method: "GET", headers: { "x-probe-run": "local-1" } })).text();
  const parsed = JSON.parse(raw);
  checkFirstRun(first, parsed);
  expect(phases.length >= 5 && phases.at(-1) === "done", `phases: ${show(phases)}`);
  console.log(summarize(first));

  // The second run: M2 answers like a Worker that threw (Cloudflare's error 1101).
  const failing = (path, init) => (path.startsWith("/run/M2?")
    ? Promise.resolve(new Response("error code: 1101", { status: 500 }))
    : request(path, init));
  const second = await drive({ request: failing, runId: "local-2", params: { ...PARAMS, holdMs: 0 },
                               timing: TIMING });
  checkSecondRun(second, "local-1");

  const worst = worstCaseMs({ holdMs: MAX_HOLD_MINUTES * 60_000 });
  expect(worst < STEP_TIMEOUT_MINUTES * 60_000,
         `the driver's worst case (${(worst / 60_000).toFixed(1)} min) does not fit the step timeout`);
  console.log(`driver worst case at a ${MAX_HOLD_MINUTES}-minute hold: ${(worst / 60_000).toFixed(1)} min`);

  const out = process.argv.indexOf("--out");
  if (out > 0) {
    writeFileSync(process.argv[out + 1], `${JSON.stringify({ first, second }, null, 2)}\n`);
  }
} finally {
  await mf.dispose();
}

if (problems.length) {
  console.error(`::error::cap probe harness check failed:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log(`cap probe harness check passed: ${CASES.length} cases, twice; /results is well-formed JSON`);
