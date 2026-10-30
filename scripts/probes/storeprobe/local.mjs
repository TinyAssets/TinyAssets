#!/usr/bin/env node
/**
 * The store probe's harness check, in miniflare: config.mjs writes every config, wrangler
 * bundles each owner-like copy exactly as a deploy does (`deploy --dry-run`, which also
 * validates the same-script `PISTORE` binding under `disable_ctx_exports`), and miniflare
 * runs the bundles and the door. `drive` then runs every case with short waits, and this
 * checks that each case finished, that the readings are well formed, and that the reading
 * key separates its rules. A second run has one case refused: the driver must record it,
 * carry on, and refuse that case's first-run records as stale.
 *
 * It proves the harness, never a reading: workerd enforces no subrequest limit (so P1
 * reads "uncounted" here), evicts nothing (P7 is inconclusive here), has no 128 MB isolate
 * (P4 never breaks here), and its colo and latencies are this machine's. store-probe.yml
 * runs it before deploying. Needs `npm ci` in `platform/`.
 *
 *   node scripts/probes/storeprobe/local.mjs [--out results.json]
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { collect, objects, table } from "./analytics.mjs";
import { BUILD, COPIES, DOOR, LOW_LIMIT, MAIN_LIMIT, PLATFORM, readCommitted, writeConfigs } from "./config.mjs";
import { discrimination, drive, finished, idealBeats, MAX_P4, MAX_RUN_MINUTES, p1aPredictions,
         p1bPredictions, p4Cause, raisedReading, readings, summarize, worstCaseMs } from "./drive.mjs";
import { SHAPE } from "./shape.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const fromPlatform = createRequire(join(PLATFORM, "package.json"));
const load = async (name) => import(pathToFileURL(fromPlatform.resolve(name)).href);
const { unstable_getMiniflareWorkerOptions: workerOptions } = await load("wrangler");
const { Miniflare, convertV4MiniflareOptions } = await load("miniflare");
const WRANGLER = join(dirname(fromPlatform.resolve("wrangler/package.json")), "bin", "wrangler.js");

const KEY = "local-key";
const RAISED = 1_000_000;
/** store-probe.yml's "Run every case" timeout-minutes; the driver's worst case must fit. */
const STEP_TIMEOUT_MINUTES = 150;
const PARAMS = Object.freeze({
  runMs: 6000, heartbeatMs: 1000, answerSeconds: 2, tokensPerSecond: 50, windowMs: 1000,
  pings: 100, streamSeconds: 2, idleMs: 1000, p4Max: 8, p4Batch: 4, p4Turns: 2,
  p4AnswerBytes: 5000, threadCounts: [1, 8, 32],
});
const TIMING = Object.freeze({
  pollMs: 100, requestMs: 120_000, healthMs: 10_000, p2FirstReadMs: 200, p2GraceMs: 60_000,
  batchSlackMs: 500, batchGraceMs: 120_000, pingFallbackMs: 1, p4GraceMs: 60_000,
  p4PerOpenMs: 1000, runSlackMs: 1000, runGraceMs: 60_000,
});

const problems = [];
const expect = (ok, what) => {
  if (!ok) {
    problems.push(what);
  }
};
const show = (value) => JSON.stringify(value)?.slice(0, 400);

/** wrangler's own bundle of a config, as `deploy` would upload it. Throws if wrangler refuses. */
function bundle(config, outDir) {
  execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir", outDir, "--config", config],
               { cwd: PLATFORM, stdio: "pipe", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
  return outDir;
}

/** A miniflare worker from a config's runtime and bindings, running `script` from `root`. */
function worker(config, root, script, extra) {
  const { workerOptions: options } = workerOptions(config);
  const { compatibilityDate, compatibilityFlags, durableObjects, additionalUnboundDurableObjects } = options;
  return { compatibilityDate, compatibilityFlags, durableObjects, additionalUnboundDurableObjects,
           ...extra, modulesRoot: root,
           modules: [{ type: "ESModule", path: join(root, script), contents: readFileSync(join(root, script), "utf8") }] };
}

/** The trace page stand-in: every colo reads LCL here. */
function outbound(request) {
  if (new URL(request.url).pathname === "/cdn-cgi/trace") {
    return new Response("fl=local\ncolo=LCL\nloc=XX\n");
  }
  return new Response("local stand-in", { status: 502 });
}

/** The reading key on its own: each rule a different outcome where the cases need it. */
function checkKey() {
  const s = SHAPE.p1b;
  const ideal = idealBeats();
  const levels = discrimination(LOW_LIMIT, ideal);
  expect(Object.values(levels).every((level) => level === "full"), `discrimination at C ${LOW_LIMIT}: ${show(levels)}`);
  const key = p1bPredictions(LOW_LIMIT, ideal);
  expect(show(key) === show({ startEvent: [96, 0], neverReset: [84, 0], heartbeatResets: [180, 96],
                              uncounted: [180, 150] }), `P1b's key at C ${LOW_LIMIT}: ${show(key)}`);
  // A heartbeat that lands late stretches its window; the key follows the beats as they were.
  const late = p1bPredictions(LOW_LIMIT, [{ loopCalls: 100 }, { loopCalls: 145 }, { loopCalls: 180 }]);
  expect(late.heartbeatResets[0] === 96, `P1b with a late first beat: ${show(late)}`);
  expect(p1aPredictions().stub.counted === LOW_LIMIT && p1aPredictions().session.counted === LOW_LIMIT - 1,
         "P1a's key");
  expect(/applies/.test(raisedReading(SHAPE.p1cN, "counted")) && /did not apply/.test(raisedReading(MAIN_LIMIT, "counted")),
         "P1c's raised reading");
  expect(SHAPE.p1aN > LOW_LIMIT && SHAPE.p1cN > MAIN_LIMIT && SHAPE.p1cN < RAISED,
         "P1a and P1c must make more calls than the limit they test");
  expect(s.perWindow + s.h < LOW_LIMIT / 2, "P1b's windows must stay well under the low limit");
  for (const [raised, copies] of [[19_999, undefined], [10_000_001, undefined], [RAISED, ["low"]]]) {
    let refused = false;
    try {
      writeConfigs({ raised, outDir: join(tmpdir(), "storeprobe-refused"), copies });
    } catch {
      refused = true;
    }
    expect(refused, `config.mjs accepted raised ${raised}, copies ${copies}`);
  }
}

function checkFirstRun(report) {
  const g = report.readings;
  const r = report.results;
  expect(Object.keys(report.driverErrors).length === 0, `driver errors: ${show(report.driverErrors)}`);
  expect(report.phase === "done", `phase ${report.phase}`);
  for (const [key, stored] of Object.entries(r)) {
    if (key !== "P4 seed") {
      expect(finished(stored) && stored.result.status === "done", `${key} did not finish: ${show(stored?.result)}`);
      expect(stored?.result?.run === report.runId, `${key}: a record without this run's id`);
    }
    expect(!stored?.intrusions, `${key}: a read reached it while it ran: ${show(stored?.intrusions)}`);
  }
  expect(g.P3.onlyThreadStore === true, `P3: ${show(r.P3)}`);
  expect(g.P3.ctxExports?.owner === "undefined" && g.P3.ctxExports?.store === "undefined",
         `P3: ctx.exports under disable_ctx_exports: ${show(g.P3.ctxExports)}`);
  expect(show(r.P3?.result?.ownerEnv) === show(["PISTORE"]), `P3: the owner's env: ${show(r.P3?.result?.ownerEnv)}`);
  // workerd enforces no subrequest limit, so every call passes and the key reads "uncounted".
  for (const shape of ["stub", "session"]) {
    expect(g[`P1a ${shape}`]?.ok === SHAPE.p1aN
           && g[`P1a ${shape}`]?.rule === `void (uncounted, but the control did not stop at ${LOW_LIMIT})`,
           `P1a ${shape}: ${show(g[`P1a ${shape}`])}`);
  }
  expect(g["P1 control"]?.ok === SHAPE.p1aN && g["P1 control"].applied === false,
         `the control locally: ${show(g["P1 control"])}`);
  expect(g.P1b?.rule === `void (uncounted, but the control did not stop at ${LOW_LIMIT})`
         && g.P1b.beats.length >= SHAPE.p1b.windows,
         `P1b: ${show(g.P1b)}`);
  expect(r.P1b?.result?.armPre?.n === SHAPE.p1b.j && r.P1b?.result?.alarmPre?.n === SHAPE.p1b.k,
         `P1b's pre-spends: ${show(r.P1b?.result)}`);
  expect((r.P1b?.beats?.list ?? []).every((beat) => beat.beatPre?.n === SHAPE.p1b.h), "P1b: each beat spends h");
  expect(g["P1c main"]?.rule === "uncounted" && /nothing to compare/.test(g["P1c raised"]?.rule ?? ""),
         `P1c's readings: ${show([g["P1c main"]?.rule, g["P1c raised"]?.rule])}`);
  for (const copy of ["main", "raised"]) {
    expect(g[`P1c ${copy}`]?.ok === SHAPE.p1cN, `P1c ${copy}: ${show(g[`P1c ${copy}`])}`);
  }
  for (const threads of PARAMS.threadCounts) {
    const x = g[`P1 run ${threads}`];
    expect(x?.opened === threads && x.answers >= threads && x.threadErrors === 0 && x.callsInRun > 0
           && x.windows.length >= 2 && x.commit?.n > 0, `P1 run ${threads}: ${show(x)}`);
    expect(/no discrimination|not startEvent/.test(x?.rule ?? ""), `P1 run ${threads}'s reading: ${x?.rule}`);
  }
  const p2 = g.P2;
  expect(p2?.pingStub?.n === PARAMS.pings && p2.pingSession?.n === PARAMS.pings, `P2 pings: ${show(p2)}`);
  expect(r.P2?.result?.newThread?.length === SHAPE.p2.threads && p2.newThreadTotal !== null, `P2 new threads: ${show(p2?.newThread)}`);
  expect(p2?.resetGaveNewInstance === true, "P2: reset() did not give a fresh store instance");
  expect(p2?.commitStreaming?.n > 0, `P2: no commits while streaming: ${show(p2?.commitStreaming)}`);
  expect(p2?.colos.owner === "LCL" && show(p2?.colos.stores) === show(["LCL"]), `P2 colos: ${show(p2?.colos)}`);
  const p4 = g.P4;
  expect(p4?.status === "done" && p4.seeded === PARAMS.p4Max && p4.brokeAt === null
         && show(p4.checkpoints.map((point) => point.n)) === show([8]), `P4: ${show(p4)}`);
  expect(p4?.contextChars > PARAMS.p4Turns * PARAMS.p4AnswerBytes, `P4's seeded contexts: ${show(p4)}`);
  const p7 = g.P7;
  expect(p7?.stubShape?.ok === SHAPE.p7N && p7.sessionShape?.ok === SHAPE.p7N, `P7: ${show(p7)}`);
  expect(/inconclusive/.test(p7?.hibernation ?? ""), `P7 locally: ${p7?.hibernation}`);
  const summary = summarize(report);
  expect(summary.includes("| probe | case | reading | detail |") && !summary.includes("| not read |"),
         "the summary table is missing a reading");
}

/** analytics.mjs on a stand-in API: rows kept for the run's objects only, a refused query reported. */
async function checkAnalytics(report) {
  const ids = objects(report);
  const p7 = report.results.P7?.result?.ids;
  expect(Object.keys(ids).length >= 12 && ids[p7?.session] === "P7 store, held session",
         `analytics' objects: ${show(ids)}`);
  const id = p7?.calls;
  const fake = async (_url, init) => {
    const { query } = JSON.parse(init.body);
    if (query.includes("exceededMemoryErrors")) {
      return Response.json({ errors: [{ message: "unknown field for account acct-123" }] });
    }
    if (query.includes("exceededCpuErrors")) {
      return Response.json({ data: { viewer: { accounts: [{ rows: [
        { dimensions: { objectId: id }, sum: { exceededCpuErrors: 1 } }] }] } } });
    }
    const sum = query.includes("subrequests") ? { subrequests: 7 }
      : query.includes("requests") ? { requests: 3 } : { activeTime: 5, cpuTime: 2 };
    return Response.json({ data: { viewer: { accounts: [{ rows: [
      { dimensions: { objectId: id }, sum }, { dimensions: { objectId: "someone-else" }, sum }] }] } } });
  };
  const got = await collect({ token: "t", account: "acct-123", start: "s", end: "e", ids, fetcher: fake });
  expect(got.byObject[id]?.requests === 3 && got.byObject[id]?.subrequests === 7 && got.byObject[id]?.activeTime === 5
         && got.byObject[id]?.exceededCpuErrors === 1
         && /unknown field/.test(got.errors.memory ?? "") && !/acct-123/.test(got.errors.memory ?? "")
         && !("someone-else" in got.byObject), `analytics: ${show(got)}`);
  expect(table(got).includes("| object | requests |"), "analytics' table");
}

/** The door binds only the copies named, and every copy keeps the owner's runtime. */
function checkConfigs(dir) {
  const paths = writeConfigs({ raised: RAISED, outDir: join(dir, "two"), copies: ["main", "low"] });
  const door = JSON.parse(readFileSync(paths.door, "utf8"));
  expect(show(door.durable_objects.bindings.map((b) => b.name)) === show(["OWNER", "OWNER_LOW"]),
         `the door without the raised copy: ${show(door.durable_objects)}`);
  const owner = JSON.parse(readFileSync(join(PLATFORM, "owner", "wrangler.jsonc"), "utf8")
    .split("\n").filter((line) => !/^\s*\/\//.test(line)).join("\n"));
  const probe = readCommitted("owner");
  for (const field of ["compatibility_date", "compatibility_flags", "workers_dev", "limits"]) {
    expect(show(probe[field]) === show(owner[field]), `owner.wrangler.jsonc's ${field} differs from the owner Worker's`);
  }
  expect(show(probe.durable_objects.bindings) === show([{ name: "PISTORE", class_name: "ThreadStore" }]),
         "the probe binds exactly one namespace, PISTORE, with no script_name");
}

/** The readings on synthetic records: the control's gate, P1b's late heartbeat, P4's cause. */
function checkReadings() {
  const base = (results, extra = {}) => ({ results, limits: { main: MAIN_LIMIT, low: LOW_LIMIT, raised: RAISED },
                                           params: { threadCounts: [] }, deploy: {}, ...extra });
  const control = (ok) => ({ result: { burst: { ok, n: SHAPE.p1aN, errors: SHAPE.p1aN - ok } } });
  const p1b = (phase1, phase2, beats) => ({
    result: { phase1: { okAfterFirstError: 0, firstErrorAt: null, ...phase1 }, phase2: { ok: phase2 },
              waitedForBeat: true, finishedAt: 1 },
    beats: { list: beats.map((loopCalls) => ({ loopCalls })) } });
  const ontime = [45, 90, 135, 180, 180];
  const late = [100, 145, 180, 180];
  const rule = (results) => readings(base(results)).P1b?.rule;
  expect(rule({ "P1 control": control(LOW_LIMIT), P1b: p1b({ ok: 180 }, 96, ontime) }) === "heartbeatResets",
         "P1b: heartbeatResets on time");
  expect(rule({ "P1 control": control(LOW_LIMIT), P1b: p1b({ ok: 96, firstErrorAt: 96 }, 0, ontime) }) === "startEvent",
         "P1b: startEvent");
  // A late first heartbeat: phase 1 fails at 96, then recovers once the heartbeat lands.
  expect(rule({ "P1 control": control(LOW_LIMIT),
                P1b: p1b({ ok: 176, firstErrorAt: 96, okAfterFirstError: 80 }, 96, late) }) === "heartbeatResets",
         `P1b with a late heartbeat: ${rule({ "P1 control": control(LOW_LIMIT),
           P1b: p1b({ ok: 176, firstErrorAt: 96, okAfterFirstError: 80 }, 96, late) })}`);
  expect(/recovered/.test(rule({ "P1 control": control(LOW_LIMIT),
                                 P1b: p1b({ ok: 170, firstErrorAt: 93, okAfterFirstError: 77 }, 96, late) }) ?? ""),
         "P1b: a recovery is heartbeatResets' signature");
  expect(rule({ "P1 control": control(LOW_LIMIT), P1b: p1b({ ok: 180 }, 150, ontime) }) === "uncounted",
         "P1b: uncounted stands when the control stopped at the limit");
  expect(/^void \(uncounted/.test(rule({ "P1 control": control(SHAPE.p1aN), P1b: p1b({ ok: 180 }, 150, ontime) }) ?? ""),
         "P1b: uncounted is void when the control did not stop");
  const counted = readings(base({ "P1 control": control(LOW_LIMIT), P1b: p1b({ ok: 180 }, 150, ontime),
                                  "P1c main": { result: { burst: { ok: MAIN_LIMIT, n: SHAPE.p1cN }, finishedAt: 1 } } }));
  expect(/^void \(uncounted, but P1c main/.test(counted.P1b?.rule ?? ""), "P1b: uncounted is void when main counted");
  expect(p4Cause({ status: "failed", error: "Error: Durable Object exceeded its memory limit" }) === "memory"
         && p4Cause({ status: "failed", error: "Error: Too many subrequests" }) === "subrequests"
         && p4Cause({ status: "reset" }) === "reset, cause unproven"
         && p4Cause({ status: "reset" }, { exceededMemoryErrors: 2 }).startsWith("memory")
         && p4Cause({ status: "reset" }, { exceededCpuErrors: 1 }).startsWith("CPU"), "P4's cause");
  const p4 = readings(base({ P4: { result: { status: "done", max: 128, ownerId: "x",
                                             checkpoints: [{ n: 8, elapsedMs: 1000 }, { n: 128, elapsedMs: 11 * 60_000 }] } } }));
  expect(show(p4.P4.checkpoints.map((point) => point.void)) === show([false, true]), "P4: a checkpoint past 10 min is void");
}

function checkSecondRun(report, firstRunId) {
  const failed = report.driverErrors["P1a stub"];
  expect(failed?.status === 500 && failed.cfError === "1101", `run 2: P1a stub's failure not recorded: ${show(failed)}`);
  expect(report.results["P1a stub"]?.result?.stale === true && report.results["P1a stub"].result.run === firstRunId,
         `run 2: P1a stub's run-1 record was not refused: ${show(report.results["P1a stub"])}`);
  expect(report.phase === "done" && finished(report.results["P1 run 1"]), `run 2 ended in phase ${report.phase}`);
  expect(report.readings["P1c raised"]?.deploy === "refused", "run 2: a refused raised copy is not read as refused");
  expect(!("P1c raised" in report.results), "run 2: the refused raised copy was read anyway");
}

const dir = mkdtempSync(join(tmpdir(), "storeprobe-local-"));
checkKey();
checkReadings();
let mf;
try {
  const configs = writeConfigs({ raised: RAISED, outDir: join(dir, "config") });
  const workers = [];
  for (const copy of Object.keys(COPIES)) {
    const out = bundle(configs[copy], join(dir, `out-${copy}`));
    workers.push(worker(configs[copy], out, "owner.js", { name: COPIES[copy], outboundService: outbound }));
  }
  bundle(configs.door, join(dir, "out-door"));
  workers.unshift(worker(configs.door, HERE, "door.js", { name: DOOR, bindings: { RUN_KEY: KEY } }));
  mf = new Miniflare(convertV4MiniflareOptions({ workers }));

  const request = (path, init) => mf.dispatchFetch(`http://storeprobe.local${path}`, {
    ...init, headers: { ...(init.headers ?? {}), "x-probe-key": KEY },
  });
  expect((await mf.dispatchFetch("http://storeprobe.local/health")).status === 403,
         "a request without the key was not refused");
  expect((await request("/call/main/p3/read", { method: "POST", body: "{}" })).status === 400,
         "a request without a run id was not refused");
  expect((await request("/call/main/p3/ownerOnly", { method: "POST", body: "{}",
                                                      headers: { "x-probe-run": "x" } })).status === 404,
         "a method off the door's list was not refused");

  const phases = [];
  const started = Date.now();
  const first = await drive({ request, runId: "local-1", params: PARAMS, timing: TIMING, raised: RAISED,
                              deploy: { main: { status: "accepted (local dry-run)" } },
                              onPhase: (report) => phases.push(report.phase) });
  checkFirstRun(first);
  await checkAnalytics(first);
  checkConfigs(dir);
  expect(phases.length >= 7 && phases.at(-1) === "done", `phases: ${show(phases)}`);
  console.log(summarize(first));
  console.log(`first run: ${((Date.now() - started) / 1000).toFixed(0)} s`);

  // The second run: P1a stub answers like a Worker that threw (Cloudflare's error 1101),
  // and the raised copy reads as refused at deploy, so the driver never calls it.
  const failing = (path, init) => (path.startsWith("/call/low/p1a-stub/p1Burst")
    ? Promise.resolve(new Response("error code: 1101", { status: 500 }))
    : request(path, init));
  const second = await drive({ request: failing, runId: "local-2", timing: TIMING, raised: RAISED,
                               params: { ...PARAMS, threadCounts: [1], p4Max: 4 },
                               deploy: { main: { status: "accepted" }, raised: { status: "refused", error: "local" } } });
  second.readings = readings(second);
  checkSecondRun(second, "local-1");

  const worst = worstCaseMs({ runMs: MAX_RUN_MINUTES * 60_000, p4Max: MAX_P4 });
  expect(worst < STEP_TIMEOUT_MINUTES * 60_000,
         `the driver's worst case (${(worst / 60_000).toFixed(1)} min) does not fit the step timeout`);
  console.log(`driver worst case at a ${MAX_RUN_MINUTES}-minute run and ${MAX_P4} Sessions: `
              + `${(worst / 60_000).toFixed(1)} min`);
  const out = process.argv.indexOf("--out");
  if (out > 0) {
    writeFileSync(process.argv[out + 1], `${JSON.stringify({ first, second }, null, 2)}\n`);
  }
} finally {
  await mf?.dispose();
  rmSync(dir, { recursive: true, force: true });
  rmSync(BUILD, { recursive: true, force: true });
}

if (problems.length) {
  console.error(`::error::store probe harness check failed:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log("store probe harness check passed: every case, twice; the reading key separates its rules");
