#!/usr/bin/env node
/**
 * Drives the store probe through every case and collects the results
 * (openspec/changes/clean-sheet-platform/store-probe.md). `store-probe.yml` runs it
 * against the deployed door; `local.mjs` runs the same `drive` against miniflare.
 *
 *   PROBE_KEY=... node drive.mjs --base https://ta-storeprobe-door.<sub>.workers.dev \
 *       --run-id <id> --out results.json --summary summary.md [--run-minutes 10] \
 *       [--p4-max 128] [--raised 1000000] [--deploy deploy.json] [--redact <text>]
 *
 * Order: P3, the low copy's control and P1a (inline); P2 alone (latency, before anything else loads the
 * isolate); P7, P1b and P1c at once (separate objects); P4 (seeding, then the host,
 * alone, since it fills an isolate); the P1 runs at 1, 8 and 32 threads, one after
 * another. A measured case (P1b, P1c, P1 runs) is never read while it should still be
 * running: a read is an incoming event to that object, and which event a call is charged
 * to is what P1 measures. The object logs any read that arrives while it runs, and the
 * key voids that reading. A failing case is recorded and the run goes on; the report is
 * written after every phase, so a killed run still leaves what it had.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { LOW_LIMIT, MAIN_LIMIT, MAX_RAISED, MIN_RAISED } from "./config.mjs";
import { SHAPE } from "./shape.mjs";

const MINUTE = 60_000;
/** P1's run length bounds: an alarm is cut at 15 minutes (cap-probe M10). */
export const MIN_RUN_MINUTES = 1;
export const MAX_RUN_MINUTES = 14;
/** P4's bounds on open Sessions. */
export const MIN_P4 = 8;
export const MAX_P4 = 256;
export const THREAD_COUNTS = Object.freeze([1, 8, 32]);

export const LIVE_TIMING = Object.freeze({
  pollMs: 5000, requestMs: 45_000, healthMs: 120_000,
  p2FirstReadMs: 30_000, p2GraceMs: 240_000,
  batchSlackMs: 30_000, batchGraceMs: 180_000, pingFallbackMs: 5,
  p4GraceMs: 600_000, p4PerOpenMs: 2000,
  runSlackMs: 60_000, runGraceMs: 240_000,
});
export const LIVE_PARAMS = Object.freeze({
  runMs: 10 * MINUTE, heartbeatMs: 30_000, answerSeconds: 30, tokensPerSecond: 20,
  windowMs: 20_000, pings: 1000, streamSeconds: 20, idleMs: 180_000,
  p4Max: 128, p4Batch: 8, p4Turns: 6, p4AnswerBytes: 50_000,
  threadCounts: THREAD_COUNTS,
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** The cases, by key: the owner copy and object each runs in. */
export function cases(p = LIVE_PARAMS) {
  return {
    P3: ["main", "p3"],
    "P1 control": ["low", "p1-control"],
    "P1a stub": ["low", "p1a-stub"],
    "P1a session": ["low", "p1a-session"],
    P2: ["main", "p2"],
    P7: ["main", "p7"],
    P1b: ["low", "p1b"],
    "P1c main": ["main", "p1c"],
    "P1c raised": ["raised", "p1c"],
    "P4 seed": ["main", "p4-seed"],
    P4: ["main", "p4-host"],
    ...Object.fromEntries(p.threadCounts.map((t) => [`P1 run ${t}`, ["main", `p1run-${t}`]])),
  };
}

/**
 * The longest `drive` can take: every wait at its bound and every request at its timeout.
 * store-probe.yml's step timeout is sized from this at the input maxima (local.mjs checks).
 */
export function worstCaseMs(params = {}, timing = {}) {
  const p = { ...LIVE_PARAMS, ...params };
  const t = { ...LIVE_TIMING, ...timing };
  const r = t.requestMs;
  const batchWait = Math.max(p.idleMs, SHAPE.p1cN * 10 * 1.5, (SHAPE.p1b.windows + 2) * p.windowMs);
  return t.healthMs + r + 4 * r // health, P3, the control, P1a
    + r + t.p2FirstReadMs + t.p2GraceMs + r // P2
    + 4 * r + batchWait + t.batchSlackMs + t.batchGraceMs + 4 * r // P7, P1b, P1c
    + Math.ceil(p.p4Max / p.p4Batch) * r + r + t.p4GraceMs + p.p4Max * t.p4PerOpenMs + r // P4
    + p.threadCounts.length * (r + p.runMs + p.answerSeconds * 1000 + t.runSlackMs + t.runGraceMs + r)
    + Object.keys(cases(p)).length * r; // the final read
}

/** A case's records with every one another run wrote replaced by a stale marker. */
export function fresh(stored, runId) {
  if (!stored || typeof stored !== "object" || "readError" in stored) {
    return { records: stored, stale: [] };
  }
  const records = {};
  const stale = [];
  for (const [key, value] of Object.entries(stored)) {
    if (key === "intrusions" || value?.run === runId) {
      records[key] = value;
    } else {
      records[key] = { stale: true, run: value?.run ?? null };
      stale.push(key);
    }
  }
  return { records, stale };
}

/** Whether a case's (fresh) records are final. */
export function finished(stored) {
  return ["done", "failed", "reset"].includes(stored?.result?.status);
}

function failure(message, detail) {
  const error = new Error(message);
  error.detail = detail;
  return error;
}

/**
 * Runs every case. `request(path, init)` answers like `fetch` against the door, with the
 * key already attached; the run id goes in `x-probe-run`. `deploy` is what the workflow
 * saw when it deployed each copy (P3's deploy reading, and whether the raised copy
 * exists). `onPhase(report)` is called after each phase with the report so far.
 */
export async function drive({ request, runId, params = {}, timing = {}, log = () => {},
                              onPhase = () => {}, deploy = {}, raised = 1_000_000 }) {
  if (!runId) {
    throw new Error("drive needs a runId");
  }
  const t = { ...LIVE_TIMING, ...timing };
  const p = { ...LIVE_PARAMS, ...params };
  const all = cases(p);
  const report = {
    runId, phase: "start", startedAt: Date.now(), params: p, timing: t, deploy,
    limits: { main: MAIN_LIMIT, low: LOW_LIMIT, raised },
    notes: [], driverErrors: {}, results: {},
  };
  const raisedDeployed = deploy.raised?.status !== "refused";

  async function call(key, method, body = {}) {
    const [copy, object] = all[key];
    const init = { method: "POST", headers: { "x-probe-run": runId, "content-type": "application/json" },
                   body: JSON.stringify(body), signal: AbortSignal.timeout(t.requestMs) };
    let response;
    try {
      response = await request(`/call/${copy}/${object}/${method}`, init);
    } catch (error) {
      throw failure(`${key} ${method}: ${error.message}`, { key, method, error: String(error.message) });
    }
    const text = await response.text();
    const detail = { key, method, status: response.status, body: text.slice(0, 300),
                     cfError: /error code: (\d+)/.exec(text)?.[1] ?? null };
    if (!response.ok) {
      throw failure(`${key} ${method}: status ${response.status}`, detail);
    }
    try {
      return JSON.parse(text);
    } catch {
      throw failure(`${key} ${method}: status ${response.status}, not JSON`, detail);
    }
  }
  async function attempt(label, fn) {
    try {
      return await fn();
    } catch (error) {
      report.driverErrors[label] = error.detail ?? { error: String(error.message) };
      log(`${label} failed: ${error.message}`);
      return undefined;
    }
  }
  async function collect(keys) {
    for (const key of keys) {
      const got = await attempt(`read ${key}`, () => call(key, "read"));
      if (!got) {
        continue;
      }
      const { records, stale } = fresh(got.result, runId);
      report.results[key] = records;
      if (stale.length) {
        report.notes.push(`${key}: refused records from another run (${stale.join(", ")})`);
      }
    }
  }
  async function pollUntil(keys, graceMs) {
    const deadline = Date.now() + graceMs;
    await collect(keys);
    while (!keys.every((key) => finished(report.results[key])) && Date.now() < deadline) {
      await sleep(t.pollMs);
      await collect(keys.filter((key) => !finished(report.results[key])));
    }
    const open = keys.filter((key) => !finished(report.results[key]));
    if (open.length) {
      report.notes.push(`not finished when read: ${open.join(", ")}`);
    }
  }
  function phase(name) {
    report.phase = name;
    log(name);
    onPhase(report);
  }
  const arm = (key, body) => attempt(key, () => call(key, "arm", body));

  const healthDeadline = Date.now() + t.healthMs;
  let healthy = false;
  while (!healthy) {
    try {
      const response = await request("/health", { method: "GET", signal: AbortSignal.timeout(t.requestMs) });
      if (!response.ok) {
        throw failure(`health: status ${response.status}`, { status: response.status });
      }
      healthy = true;
    } catch (error) {
      if (Date.now() > healthDeadline) {
        report.driverErrors.health = error.detail ?? { error: String(error.message) };
        break;
      }
      await sleep(t.pollMs);
    }
  }
  if (healthy) {
    await runCases();
  }
  await collect(Object.keys(all).filter((key) => key !== "P1c raised" || raisedDeployed));
  report.readings = readings(report);
  phase(healthy ? "done" : "the door never answered /health");
  return report;

  async function runCases() {
    await attempt("P3", () => call("P3", "p3"));
    await attempt("P1 control", () => call("P1 control", "p1Control", { n: SHAPE.p1aN }));
    for (const shape of ["stub", "session"]) {
      await attempt(`P1a ${shape}`, () => call(`P1a ${shape}`, "p1Burst", { shape, n: SHAPE.p1aN }));
    }
    await collect(["P3", "P1 control", "P1a stub", "P1a session"]);
    phase("P3 and P1a");

    const p2 = { pings: p.pings, streamSeconds: p.streamSeconds };
    if (await arm("P2", { case: "p2", ...p2 })) {
      await sleep(t.p2FirstReadMs);
      await pollUntil(["P2"], t.p2GraceMs);
    }
    phase("P2");

    // P1c's alarm makes 10,100 calls in a row: wait for them at P2's measured pace
    // (fallback 5 ms a call) before the first read, which would otherwise intrude.
    const pingMs = report.results.P2?.result?.pingStub?.batchMeanMs ?? t.pingFallbackMs;
    const batch = ["P7", "P1b", "P1c main", ...(raisedDeployed ? ["P1c raised"] : [])];
    const armedAt = Date.now();
    await arm("P7", { case: "p7", idleMs: p.idleMs });
    await arm("P1b", { case: "p1b", windowMs: p.windowMs });
    for (const key of batch.filter((k) => k.startsWith("P1c"))) {
      await arm(key, { case: "p1c", n: SHAPE.p1cN });
    }
    const batchWait = Math.max(p.idleMs, SHAPE.p1cN * Math.min(10, Math.max(pingMs, 0.5)) * 1.5,
                               (SHAPE.p1b.windows + 2) * p.windowMs);
    await sleep(Math.max(0, armedAt + batchWait + t.batchSlackMs - Date.now()));
    await pollUntil(batch, t.batchGraceMs);
    phase("P7, P1b and P1c");

    const seed = { turns: p.p4Turns, answerBytes: p.p4AnswerBytes };
    let seeded = 0;
    for (let from = 0; from < p.p4Max; from += p.p4Batch) {
      const to = Math.min(p.p4Max, from + p.p4Batch);
      const got = await attempt(`P4 seed ${from}`, () => call("P4 seed", "p4Seed", { ...seed, from, to }));
      if (!got) {
        break;
      }
      seeded = to;
    }
    report.p4Seeded = seeded;
    if (seeded > 0 && await arm("P4", { case: "p4", max: seeded })) {
      await sleep(t.pollMs);
      await pollUntil(["P4"], t.p4GraceMs + seeded * t.p4PerOpenMs);
    }
    await collect(["P4 seed"]);
    phase("P4");

    for (const threads of p.threadCounts) {
      const key = `P1 run ${threads}`;
      const body = { case: "p1run", threads, runMs: p.runMs, heartbeatMs: p.heartbeatMs,
                     answerSeconds: p.answerSeconds, tokensPerSecond: p.tokensPerSecond };
      const armedRun = Date.now();
      if (await arm(key, body)) {
        await sleep(Math.max(0, armedRun + p.runMs + p.answerSeconds * 1000 + t.runSlackMs - Date.now()));
        await pollUntil([key], t.runGraceMs);
      }
      phase(key);
    }
  }
}

// ---------------------------------------------------------------------------------
// The reading key (store-probe.md), as code.

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

/** P1a: what each shape's burst of `n` shows at the low copy's limit `C`, per rule. */
export function p1aPredictions(C = LOW_LIMIT, n = SHAPE.p1aN) {
  return {
    stub: { counted: clamp(C, 0, n), uncounted: n },
    // The session's open is one stub call; the question is whether the calls riding it count.
    session: { counted: clamp(C - 1, 0, n), uncounted: n },
  };
}

/**
 * P1b: [phase 1 ok, phase 2 ok] under each rule, from the beats as they happened
 * (`beats[].loopCalls`: how many loop calls had run when that heartbeat spent its `h`).
 * - startEvent: work in flight stays charged to the alarm that started it;
 * - neverReset: one counter for the object, never reset;
 * - heartbeatResets: each heartbeat alarm takes the count over (newest-or-reset, M12);
 * - uncounted: store calls are not subrequests.
 */
export function p1bPredictions(C, beats, s = SHAPE.p1b) {
  const L1 = s.perWindow * s.windows;
  const before = (x) => beats.filter((beat) => beat.loopCalls < x).length;
  // startEvent
  const start1 = clamp(C - s.k, 0, L1);
  const start2 = start1 < L1 ? 0 : clamp(C - s.k - L1, 0, s.burst);
  // neverReset
  let never1 = 0;
  for (let x = 1; x <= L1; x += 1) {
    if (s.j + s.k + x + s.h * before(x) <= C) {
      never1 = x;
    } else {
      break;
    }
  }
  const never2 = never1 < L1 ? 0 : clamp(C - s.j - s.k - L1 - s.h * beats.length, 0, s.burst);
  // heartbeatResets: the newest event's own spend plus the loop's calls since it began.
  let reset1 = 0;
  for (let x = 1; x <= L1; x += 1) {
    const last = beats.filter((beat) => beat.loopCalls < x).at(-1);
    const spent = last ? s.h + (x - last.loopCalls) : s.k + x;
    if (spent <= C) {
      reset1 = x;
    } else {
      break;
    }
  }
  const reset2 = clamp(C - s.h, 0, s.burst);
  return { startEvent: [start1, start2], neverReset: [never1, never2],
           heartbeatResets: [reset1, reset2], uncounted: [L1, s.burst] };
}

/** P1c on main: the alarm's ok calls under each rule, at the owner's limit. */
export function p1cPredictions(limit = MAIN_LIMIT, n = SHAPE.p1cN) {
  return { counted: clamp(limit, 0, n), uncounted: n };
}

/**
 * P1c on the raised copy, read against main's: past the default only means the raised
 * limit applies when main stopped at its 10,000.
 */
export function raisedReading(raisedOk, mainRule, n = SHAPE.p1cN) {
  if (raisedOk === n) {
    return mainRule === "counted"
      ? "the raised limit applies: past 10,000 in one alarm, where main stopped at 10,000"
      : "completed, but main did not stop at 10,000 either: nothing to compare";
  }
  return raisedOk === MAIN_LIMIT ? "stopped at 10,000: the raised limit did not apply"
    : `stopped at ${raisedOk}: matches no rule`;
}

/** Per P1b and P1a case: "full" if every rule predicts a different outcome. */
export function discrimination(C = LOW_LIMIT, beats = idealBeats()) {
  const grade = (rules) => {
    const outcomes = new Set(Object.values(rules).map((value) => JSON.stringify(value)));
    const count = Object.keys(rules).length;
    return outcomes.size === count ? "full" : outcomes.size === 1 ? "none" : "partial";
  };
  const p1a = p1aPredictions(C);
  return { "P1a stub": grade(p1a.stub), "P1a session": grade(p1a.session), P1b: grade(p1bPredictions(C, beats)) };
}

/** P1b's beats when every heartbeat lands on time: after each window of loop calls. */
export function idealBeats(s = SHAPE.p1b) {
  return Array.from({ length: s.windows }, (_, i) => ({ loopCalls: (i + 1) * s.perWindow }));
}

function ruleOf(predictions, observed) {
  const hit = Object.entries(predictions).filter(([, value]) => JSON.stringify(value) === JSON.stringify(observed));
  return hit.length === 1 ? hit[0][0] : hit.length > 1 ? `ambiguous (${hit.map(([rule]) => rule).join(", ")})` : null;
}

/**
 * Why P4 stopped. A failure is classified by its error; a reset (the alarm's retry) is
 * memory or CPU only when analytics counted that error on the P4 owner, else unproven.
 */
export function p4Cause(p4, analytics) {
  if (p4?.status === "failed") {
    const text = String(p4.error ?? "");
    return /memory/i.test(text) ? "memory" : /subrequest/i.test(text) ? "subrequests"
      : /cpu|time limit/i.test(text) ? "CPU" : "other";
  }
  if (p4?.status === "reset") {
    return analytics?.exceededMemoryErrors > 0 ? "memory (analytics: exceededMemoryErrors)"
      : analytics?.exceededCpuErrors > 0 ? "CPU (analytics: exceededCpuErrors)" : "reset, cause unproven";
  }
  return null;
}

/** Intrusions (reads that reached the object while it ran) before `until` (ms epoch). */
function intruded(stored, until) {
  return (stored?.intrusions ?? []).filter((at) => until === undefined || at <= until).length;
}

/** The P1 run's calls per heartbeat window, from its beats (the start alarm's window first). */
export function windows(result) {
  const totals = [0, ...(result?.beats ?? []).map((beat) => beat.total)];
  if (typeof result?.callsInRun === "number") {
    totals.push(result.callsInRun);
  }
  const out = [];
  for (let i = 1; i < totals.length; i += 1) {
    out.push(Math.max(0, totals[i] - totals[i - 1]));
  }
  return out;
}

const SUBREQUESTS = /subrequest/i;

/** Every probe's reading, from the report's records. */
export function readings(report) {
  const r = report.results;
  const out = {};
  const deploy = report.deploy ?? {};

  // P3
  const p3 = r.P3?.result;
  const kinds = p3 ? [p3.store, p3.ownerIdThroughPistore, p3.ownerNameThroughPistore,
                      p3.ownerMethodThroughPistore, p3.ownerReadThroughPistore] : [];
  const reachedOwner = kinds.some((x) => x?.ok === "Owner" || x?.ok?.kind === "Owner");
  out.P3 = {
    deploy: deploy.main?.status ?? "not recorded",
    deployError: deploy.main?.error ?? null,
    onlyThreadStore: p3 ? (!reachedOwner && p3.store?.ok?.kind === "ThreadStore"
      && p3.ownerNameThroughPistore?.ok?.kind === "ThreadStore"
      && p3.ownerNameThroughPistore.ok.id !== p3.ownerId
      && (Boolean(p3.ownerIdThroughPistore?.error) || p3.ownerIdThroughPistore?.ok?.kind === "ThreadStore")
      && Boolean(p3.ownerMethodThroughPistore?.error) && Boolean(p3.ownerReadThroughPistore?.error)
      // disable_ctx_exports honoured in both classes, and the store sees only PISTORE.
      && p3.ownerExports === "undefined" && p3.store.ok.exports === "undefined"
      && JSON.stringify(p3.store.ok.env) === JSON.stringify(["PISTORE"])) : null,
    ctxExports: p3 ? { owner: p3.ownerExports, store: p3.store?.ok?.exports ?? null } : null,
  };

  // P1a
  const p1a = p1aPredictions(report.limits.low);
  for (const shape of ["stub", "session"]) {
    const b = r[`P1a ${shape}`]?.result?.burst;
    out[`P1a ${shape}`] = b ? { ok: b.ok, n: b.n, firstError: b.firstError,
                                rule: ruleOf(p1a[shape], b.ok) ?? "matches no rule" } : null;
  }

  // P1b
  const p1b = r.P1b?.result;
  if (p1b?.phase1 && p1b?.phase2) {
    const beats = r.P1b?.beats?.list ?? [];
    const observed = [p1b.phase1.firstErrorAt ?? p1b.phase1.ok, p1b.phase2.ok];
    const predictions = p1bPredictions(report.limits.low, beats);
    const voided = intruded(r.P1b, p1b.finishedAt);
    // Only a count that a heartbeat resets can fail and then succeed again in phase 1.
    const recovered = p1b.phase1.okAfterFirstError > 0;
    out.P1b = { observed, predictions, beats: beats.map((beat) => beat.loopCalls),
                okAfterFirstError: p1b.phase1.okAfterFirstError,
                rule: voided ? `void (${voided} read(s) while it ran)`
                  : !p1b.waitedForBeat ? "void (no heartbeat before phase 2)"
                    : ruleOf(predictions, observed)
                      ?? (recovered ? "heartbeatResets (phase 1 recovered after a heartbeat)" : "matches no rule"),
                firstError: p1b.phase1.firstError ?? p1b.phase2.firstError };
  } else {
    out.P1b = p1b ? { status: p1b.status, error: p1b.error ?? null } : null;
  }

  // P1c
  for (const copy of ["main", "raised"]) {
    const key = `P1c ${copy}`;
    const result = r[key]?.result;
    const limit = report.limits[copy];
    if (copy === "raised" && deploy.raised?.status === "refused") {
      out[key] = { deploy: "refused", error: deploy.raised.error ?? null };
      continue;
    }
    const b = result?.burst;
    if (!b) {
      out[key] = result ? { status: result.status, error: result.error ?? null } : null;
      continue;
    }
    let rule;
    if (intruded(r[key], result.finishedAt)) {
      rule = "void (a read while it ran)";
    } else if (copy === "main") {
      rule = ruleOf(p1cPredictions(limit), b.ok) ?? "matches no rule";
    } else {
      rule = raisedReading(b.ok, out["P1c main"]?.rule);
    }
    out[key] = { deploy: deploy[copy]?.status ?? "not recorded", limit, ok: b.ok, n: b.n,
                 firstError: b.firstError, rule };
  }

  // The control: fetches are known subrequests, so they stop at the low limit only if it
  // applied. Without that, or when main counted store calls, a low-copy "uncounted" is void.
  const control = r["P1 control"]?.result?.burst;
  out["P1 control"] = control ? { ok: control.ok, n: control.n, firstError: control.firstError,
                                  applied: control.ok === report.limits.low && control.errors > 0 } : null;
  const doubt = !out["P1 control"]?.applied ? `the control did not stop at ${report.limits.low}`
    : out["P1c main"]?.rule === "counted" ? "P1c main counted store calls" : null;
  for (const key of ["P1a stub", "P1a session", "P1b"]) {
    if (doubt && out[key]?.rule === "uncounted") {
      out[key].rule = `void (uncounted, but ${doubt})`;
    }
  }

  // P1 runs
  for (const threads of report.params.threadCounts) {
    const key = `P1 run ${threads}`;
    const result = r[key]?.result;
    if (!result || result.status === "armed" || result.status === "running") {
      out[key] = result ? { status: result.status } : null;
      continue;
    }
    const perWindow = windows(result);
    const first = result.calls?.firstError ?? null;
    const C = report.limits.main;
    const seconds = (result.elapsedMs ?? 0) / 1000;
    let rule;
    if (intruded(r[key], result.startedAt + result.runMs)) {
      rule = "void (a read while it ran)";
    } else if (first && SUBREQUESTS.test(first.error)) {
      rule = Math.abs(first.atCall - (C + 1)) <= 2 * threads
        ? "startEvent or neverReset (P1b separates them): the run was cut at the limit across heartbeats"
        : `a subrequest error at call ${first.atCall}, not at the limit: read the windows`;
    } else if (first) {
      rule = `another error: ${first.error}`;
    } else if ((result.callsInRun ?? 0) > C) {
      rule = "not startEvent: more calls than the limit, no error (heartbeats reset, or uncounted)";
    } else {
      rule = "no discrimination: the run stayed under the limit";
    }
    out[key] = {
      status: result.status, threads, opened: result.opened, callsInRun: result.callsInRun,
      maxWindow: perWindow.length ? Math.max(...perWindow) : null, windows: perWindow,
      perThreadSecond: seconds && result.opened ? Math.round((result.callsInRun / result.opened / seconds) * 100) / 100 : null,
      answers: (result.perThread ?? []).reduce((sum, x) => sum + x.answers, 0),
      threadErrors: (result.perThread ?? []).filter((x) => x.error).length,
      commit: result.calls?.commit ?? null, openMs: result.openMs ?? null, firstError: first, rule,
    };
  }

  // P2
  const p2 = r.P2?.result;
  if (p2?.pingStub) {
    const median = (list, pick) => {
      const values = list.map(pick).filter((x) => typeof x === "number").sort((a, b) => a - b);
      return values.length ? values[Math.floor((values.length - 1) / 2)] : null;
    };
    const steps = ["storeOpen", "harnessOpen", "root", "firstAdmit", "firstSettle"];
    const fresh = p2.newThread ?? [];
    out.P2 = {
      colos: { door: p2.doorColo, owner: p2.ownerColo?.colo ?? p2.ownerColo?.error ?? null,
               stores: [...new Set(p2.storeColos ?? [])] },
      pingStub: p2.pingStub, pingSession: p2.pingSession,
      commitStreaming: p2.streaming?.commit ?? null,
      newThread: Object.fromEntries(steps.map((step) => [step, median(fresh, (x) => x.timings[step])])),
      newThreadCalls: Object.fromEntries(Object.keys(fresh[0]?.calls ?? {})
        .map((step) => [step, median(fresh, (x) => x.calls[step])])),
      newThreadTotal: median(fresh, (x) => steps.reduce((sum, step) => sum + (x.timings[step] ?? 0), 0)),
      reopenWarm: p2.reopenWarm?.timings ?? null, reopenAfterReset: p2.reopenAfterReset?.timings ?? null,
      resetGaveNewInstance: p2.reopenAfterReset ? p2.reopenAfterReset.instance !== p2.reopenWarm?.instance : null,
      memory: p2.memory ?? null,
    };
  } else {
    out.P2 = p2 ? { status: p2.status, error: p2.error ?? null } : null;
  }

  // P4
  const p4 = r.P4?.result;
  const seeds = Object.entries(r["P4 seed"] ?? {}).filter(([key]) => key.startsWith("seed-"))
    .flatMap(([, value]) => value.stores ?? []);
  if (p4) {
    out.P4 = {
      status: p4.status, max: p4.max, seeded: report.p4Seeded ?? null,
      // pi drops an idle context after contextRetentionMs (10 min by default), so a
      // checkpoint reached later may hold less than it claims.
      checkpoints: (p4.checkpoints ?? []).map((point) => ({ ...point, void: point.elapsedMs > 10 * MINUTE })),
      brokeAt: p4.status === "reset" ? (p4.progressAtReset?.opened ?? "unknown")
        : p4.status === "failed" ? (p4.failedAt ?? r.P4?.progress?.opened ?? "unknown") : null,
      cause: p4Cause(p4, report.analytics?.byObject?.[p4.ownerId]),
      error: p4.error ?? null,
      storeBytes: seeds.length ? Math.round(seeds.reduce((sum, x) => sum + x.bytes, 0) / seeds.length) : null,
      contextChars: seeds.length ? Math.round(seeds.reduce((sum, x) => sum + x.contextChars, 0) / seeds.length) : null,
    };
  } else {
    out.P4 = null;
  }

  // P7
  const p7 = r.P7?.result;
  if (p7?.after) {
    const heldSame = p7.after.sessionHeld?.ok?.instance === p7.before.session;
    const unheldChanged = p7.after.callsFresh?.ok && p7.after.callsFresh.ok.instance !== p7.before.calls;
    let hibernation;
    if (!unheldChanged) {
      hibernation = "inconclusive: the unheld store kept its instance through the idle window";
    } else if (heldSame) {
      hibernation = "holding the session kept its store in memory while the unheld one was evicted";
    } else {
      hibernation = "holding the session did not keep its store in memory";
    }
    out.P7 = { stubShape: p7.callsShape, sessionShape: p7.sessionShape, idleMs: p7.idleMs, ids: p7.ids,
               heldSame, unheldChanged: Boolean(unheldChanged), hibernation, after: p7.after,
               analytics: report.analytics ?? null };
  } else {
    out.P7 = p7 ? { status: p7.status, error: p7.error ?? null } : null;
  }
  return out;
}

// ---------------------------------------------------------------------------------
// The summary: one fixed table.

const cell = (value) => String(value ?? "").replaceAll("|", "/").replaceAll("\n", " ");
const fmt = (s) => (s && s.n ? `p50 ${s.p50}, p95 ${s.p95}, p99 ${s.p99}, max ${s.max} ms (n ${s.n}${s.batchMeanMs !== undefined ? `, batch mean ${s.batchMeanMs}` : ""})` : "none");

/** The job summary's table: probe, case, reading, detail; and which readings are live only. */
export function summarize(report) {
  const g = report.readings ?? readings(report);
  const rows = [];
  const row = (probe, name, reading, detail = "") => rows.push(`| ${probe} | ${cell(name)} | ${cell(reading)} | ${cell(detail)} |`);
  row("P3", "deploy (wrangler + production)", g.P3.deploy, g.P3.deployError ?? "");
  row("P3", "PISTORE reaches only ThreadStore", g.P3.onlyThreadStore ?? "not read",
      report.results.P3?.result ? JSON.stringify({
        ownerId: report.results.P3.result.ownerIdThroughPistore,
        ownerMethod: report.results.P3.result.ownerMethodThroughPistore,
      }).slice(0, 300) : "");
  row("P3", "ctx.exports (owner, store)", g.P3.ctxExports ? `${g.P3.ctxExports.owner}, ${g.P3.ctxExports.store}` : "not read");
  const control = g["P1 control"];
  row("P1a", `control: ${SHAPE.p1aN} fetches in one DO RPC at C ${report.limits.low}`,
      control ? `${control.ok}/${control.n}: ${control.applied ? "the low limit applies" : "the low limit did not show"}` : "not read",
      control?.firstError ?? "");
  for (const shape of ["stub", "session"]) {
    const x = g[`P1a ${shape}`];
    row("P1a", `${shape}: ${SHAPE.p1aN} calls in one DO RPC at C ${report.limits.low}`,
        x ? `${x.ok}/${x.n}: ${x.rule}` : "not read", x?.firstError ?? "");
  }
  const b = g.P1b;
  row("P1b", `heartbeats at C ${report.limits.low}: [phase 1, phase 2]`,
      b?.observed ? `[${b.observed.join(", ")}]: ${b.rule}` : (b ? `${b.status}: ${b.error}` : "not read"),
      b?.predictions ? `key ${JSON.stringify(b.predictions)}; beats at ${JSON.stringify(b.beats)}; ${b.firstError ?? ""}` : "");
  for (const copy of ["main", "raised"]) {
    const x = g[`P1c ${copy}`];
    const what = `${copy}: ${SHAPE.p1cN} calls in one alarm at C ${report.limits[copy]}`;
    if (x?.deploy === "refused") {
      row("P1c", what, "the raised limit was refused at deploy", x.error ?? "");
    } else {
      row("P1c", what, x?.n ? `${x.ok}/${x.n}: ${x.rule}` : (x ? `${x.status}: ${x.error}` : "not read"),
          x?.firstError ?? (copy === "raised" ? `deploy ${x?.deploy ?? "not recorded"}` : ""));
    }
  }
  for (const threads of report.params.threadCounts) {
    const x = g[`P1 run ${threads}`];
    if (!x?.callsInRun && x?.callsInRun !== 0) {
      row("P1", `${threads} thread(s), ${report.params.runMs / MINUTE} min`, x ? x.status : "not read");
      continue;
    }
    row("P1", `${threads} thread(s), ${report.params.runMs / MINUTE} min`,
        `${x.callsInRun} store calls, max ${x.maxWindow} per heartbeat window, ${x.perThreadSecond}/thread/s: ${x.rule}`,
        `answers ${x.answers}, thread errors ${x.threadErrors}; commit ${fmt(x.commit)}${x.firstError ? `; first error at call ${x.firstError.atCall}: ${x.firstError.error}` : ""}`);
  }
  const p2 = g.P2;
  if (p2?.pingStub) {
    row("P2", "colo: door / owner / stores", `${p2.colos.door} / ${p2.colos.owner} / ${p2.colos.stores.join(", ")}`);
    row("P2", "ping, per-call stub", fmt(p2.pingStub));
    row("P2", "ping, over one session", fmt(p2.pingSession));
    row("P2", "commit while 4 threads stream", fmt(p2.commitStreaming));
    row("P2", "new thread (median ms)", `total ${p2.newThreadTotal}`, `${JSON.stringify(p2.newThread)}; store calls ${JSON.stringify(p2.newThreadCalls)}`);
    row("P2", "reopen: warm / after reset", `${JSON.stringify(p2.reopenWarm)} / ${JSON.stringify(p2.reopenAfterReset)}`,
        `reset gave a new instance: ${p2.resetGaveNewInstance}`);
  } else {
    row("P2", "all", p2 ? `${p2.status}: ${p2.error}` : "not read");
  }
  const p4 = g.P4;
  if (p4) {
    row("P4", "seeded stores", `${p4.seeded ?? "?"}: mean ${p4.storeBytes} bytes, ${p4.contextChars} context chars`);
    for (const point of p4.checkpoints) {
      row("P4", `${point.n} Sessions open`,
          point.void ? `void: reached ${point.elapsedMs} ms in, past contextRetentionMs` : `held, ${point.elapsedMs} ms in`,
          `cold open ${fmt(point.openMs)}; first turn ${fmt(point.turnMs)}; memory ${JSON.stringify(point.memory)}`);
    }
    row("P4", "break", p4.brokeAt !== null ? `${p4.status} with ${p4.brokeAt} Sessions open: ${p4.cause}`
      : `${p4.status}, no reset up to ${p4.max}`, p4.error ?? "");
  } else {
    row("P4", "all", "not read");
  }
  const p7 = g.P7;
  if (p7?.stubShape) {
    row("P7", `${SHAPE.p7N} calls per shape`, `stub: ${p7.stubShape.stubCalls} stub calls; session: ${p7.sessionShape.stubCalls} stub call + ${p7.sessionShape.sessionCalls} session calls`,
        p7.analytics ? JSON.stringify(p7.analytics).slice(0, 400) : "analytics not read");
    row("P7", `hold a session idle ${p7.idleMs / 1000} s`, p7.hibernation, JSON.stringify(p7.after).slice(0, 300));
  } else {
    row("P7", "all", p7 ? `${p7.status}: ${p7.error}` : "not read");
  }
  const errors = Object.entries(report.driverErrors).map(([label, d]) =>
    `- driver error, ${label}: ${d.status ?? ""} ${d.cfError ? `(error code ${d.cfError}) ` : ""}${cell(d.body ?? d.error ?? "")}`);
  return [
    `Run \`${report.runId}\`, phase: ${report.phase}. Limits: main ${report.limits.main}, low ${report.limits.low}, raised ${report.limits.raised}.`,
    "Live only: every P1 and P7 reading, P2's colos and latencies, P3's production deploy and namespace checks, P4's checkpoints and break. Locally workerd enforces no subrequest limit, evicts nothing and has no 128 MB isolate.",
    "",
    "| probe | case | reading | detail |", "|---|---|---|---|", ...rows, "",
    ...errors, ...report.notes.map((note) => `- ${note}`),
  ].join("\n");
}

/** Replaces each `needles` text (an account's workers.dev subdomain) before printing. */
export function redact(text, needles) {
  return needles.filter(Boolean).reduce((out, needle) => out.split(needle).join("<redacted>"), text);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    args[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  return args;
}

async function main(args, needles) {
  const key = process.env.PROBE_KEY;
  if (!args.base || !args["run-id"] || !key) {
    throw new Error("usage: PROBE_KEY=... drive.mjs --base URL --run-id ID [--out F] [--summary F] [--run-minutes M] [--p4-max N] [--raised N] [--deploy F]");
  }
  const runMinutes = Number(args["run-minutes"] ?? 10);
  if (!Number.isInteger(runMinutes) || runMinutes < MIN_RUN_MINUTES || runMinutes > MAX_RUN_MINUTES) {
    throw new Error(`--run-minutes must be a whole number from ${MIN_RUN_MINUTES} to ${MAX_RUN_MINUTES}`);
  }
  const p4Max = Number(args["p4-max"] ?? LIVE_PARAMS.p4Max);
  if (!Number.isInteger(p4Max) || p4Max < MIN_P4 || p4Max > MAX_P4) {
    throw new Error(`--p4-max must be a whole number from ${MIN_P4} to ${MAX_P4}`);
  }
  const raised = Number(args.raised ?? 1_000_000);
  if (!Number.isInteger(raised) || raised < MIN_RAISED || raised > MAX_RAISED) {
    throw new Error(`--raised must be a whole number from ${MIN_RAISED} to ${MAX_RAISED}`);
  }
  const deploy = args.deploy ? JSON.parse(readFileSync(args.deploy, "utf8")) : {};
  const base = args.base.replace(/\/$/, "");
  const request = (path, init) => fetch(`${base}${path}`, {
    ...init, headers: { ...(init.headers ?? {}), "x-probe-key": key },
  });
  const write = (report) => {
    if (args.out) {
      writeFileSync(args.out, `${redact(JSON.stringify(report, null, 2), needles)}\n`);
    }
    if (args.summary) {
      writeFileSync(args.summary, `${redact(summarize(report), needles)}\n`);
    }
  };
  const report = await drive({
    request, runId: args["run-id"], params: { runMs: runMinutes * MINUTE, p4Max }, deploy, raised,
    onPhase: (partial) => {
      partial.readings = readings(partial);
      write(partial);
    },
    log: (line) => console.log(`[${new Date().toISOString()}] ${redact(line, needles)}`),
  });
  write(report);
  console.log(redact(summarize(report), needles));
  // The whole report in the step log too: the job summary is not readable through the API.
  console.log("::group::results JSON");
  console.log(redact(JSON.stringify(report, null, 2), needles));
  console.log("::endgroup::");
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = parseArgs(process.argv.slice(2));
  const needles = [args.base, ...(args.redact ?? "").split(",")];
  main(args, needles).catch((error) => {
    console.error(`::error::${redact(String(error.message), needles)}`);
    process.exit(1);
  });
}
