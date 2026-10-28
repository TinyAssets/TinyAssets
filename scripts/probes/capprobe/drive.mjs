#!/usr/bin/env node
/**
 * Drives ta-capprobe through every case of the 32-call cap probe and collects the results
 * (openspec/changes/clean-sheet-platform/cap-probe.md). `cap-probe.yml` runs it against
 * the deployed Worker; `local.mjs` runs the same `drive` against miniflare.
 *
 *   PROBE_KEY=... node drive.mjs --base https://ta-capprobe.<sub>.workers.dev \
 *       --run-id <id> --out results.json --summary summary.md \
 *       [--hold-minutes 16] [--burst 40] [--subrequests-limit 50] [--redact <text>]
 *
 * Order: the Worker cases and M4; M6 and M6a (background loops) and the alarm cases M5,
 * M7, M8, M9; M12's two invocations, B started after A's first phase; M10 (the 16-minute
 * alarm) last. No case is read while it should still be running: a read is an incoming
 * call to that object, and which invocation a call is charged to is what the probe
 * measures. A failing case is recorded and the run goes on; the report is written after
 * every phase, so a killed run still leaves what it had.
 */
import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const CASES = Object.freeze([
  "M1", "M2", "M3", "M4", "M5", "M6", "M6a", "M7", "M8", "M9", "M10", "M11", "M12",
]);
const WORKER_CASES = Object.freeze(["M1", "M2", "M3", "M11"]);
const INLINE = Object.freeze([...WORKER_CASES, "M4"]);
const ALARMS = Object.freeze(["M5", "M7", "M8", "M9"]);
const BACKGROUND = Object.freeze(["M6", "M6a"]);
/** M10's alarm may await at most this long, so the step's timeout bounds the run. */
export const MAX_HOLD_MINUTES = 20;
/**
 * N, the calls per burst. M12 needs at least 25 (its first phase is 40% of N, and A's
 * last phase adds 10), and the step timeout is sized at `MAX_BURST` (local.mjs checks).
 */
export const MIN_BURST = 25;
export const MAX_BURST = 120;
const MINUTE = 60_000;

export const LIVE_TIMING = Object.freeze({
  pollMs: 5000, requestMs: 45_000, healthMs: 120_000, planIters: 1e8,
  alarmsFirstReadMs: 30_000, alarmsGraceMs: 120_000,
  backgroundSlackMs: 30_000, backgroundGraceMs: 120_000,
  m12DelayMs: 10_000, m10FirstReadMs: 60_000, m10GraceMs: 180_000,
});
export const LIVE_PARAMS = Object.freeze({
  n: 40, intervalMs: 2000, holdMs: 16 * MINUTE, waitMs: 30_000, settleMs: 2000,
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The longest `drive` can take: every wait at its bound and every request at its timeout.
 * cap-probe.yml's step timeout is sized from this at `MAX_HOLD_MINUTES` (local.mjs checks).
 */
export function worstCaseMs(params = {}, timing = {}) {
  const p = { ...LIVE_PARAMS, ...params };
  const t = { ...LIVE_TIMING, ...timing };
  const r = t.requestMs;
  return (t.healthMs + r) + r // health, plan
    + INLINE.length * r + WORKER_CASES.length * r // inline cases and their fallback stores
    + (BACKGROUND.length + ALARMS.length) * r // starts and arms
    + t.m12DelayMs + r // M12
    + t.alarmsFirstReadMs + t.alarmsGraceMs + r // alarm reads
    + p.waitMs + p.n * p.intervalMs + t.backgroundSlackMs + t.backgroundGraceMs + r // background
    + r + p.holdMs + t.m10FirstReadMs + t.m10GraceMs + r // M10
    + r; // the final read
}

/** Whether a case's (fresh) records are final. */
export function finished(id, stored) {
  if (id === "M12") {
    return Boolean(stored?.A?.status && stored?.B?.status);
  }
  return ["done", "failed"].includes(stored?.result?.status);
}

/** A case's records with every one another run wrote replaced by a stale marker. */
export function fresh(stored, runId) {
  if (!stored || typeof stored !== "object" || "readError" in stored) {
    return { records: stored, stale: [] };
  }
  const records = {};
  const stale = [];
  for (const [key, value] of Object.entries(stored)) {
    if (value?.run === runId) {
      records[key] = value;
    } else {
      records[key] = { stale: true, run: value?.run ?? null };
      stale.push(key);
    }
  }
  return { records, stale };
}

function failure(message, detail) {
  const error = new Error(message);
  error.detail = detail;
  return error;
}

/**
 * Runs every case. `request(path, init)` answers like `fetch` against the Worker, with
 * the key already attached; the run id goes in `x-probe-run`. `timing` scales the waits.
 * `onPhase(report)` is called after each phase with the report so far.
 * `subrequestsLimit` is the `limits.subrequests` both Workers were deployed with, or
 * null for the plan's default; it is recorded, never sent.
 */
export async function drive({ request, runId, params = {}, timing = {}, log = () => {},
                              onPhase = () => {}, subrequestsLimit = null }) {
  if (!runId) {
    throw new Error("drive needs a runId");
  }
  const t = { ...LIVE_TIMING, ...timing };
  const p = { ...LIVE_PARAMS, ...params };
  const report = {
    runId, phase: "start", plan: null, params: p, timing: t, notes: [],
    subrequestsLimit: subrequestsLimit ?? "plan default",
    // What each case can tell apart at this limit and N, from the reading key.
    discrimination: subrequestsLimit ? discrimination(subrequestsLimit, p.n) : null,
    driverErrors: {}, checks: {}, results: {},
  };
  const query = (extra = {}) => new URLSearchParams(
    Object.entries({ ...p, ...extra }).map(([key, value]) => [key, String(value)]),
  ).toString();

  async function call(method, path, body) {
    const init = { method, headers: { "x-probe-run": runId },
                   signal: AbortSignal.timeout(t.requestMs) };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
      init.headers["content-type"] = "application/json";
    }
    let response;
    try {
      response = await request(path, init);
    } catch (error) {
      throw failure(`${method} ${path}: ${error.message}`, { method, path, error: String(error.message) });
    }
    const text = await response.text();
    const detail = { method, path, status: response.status, body: text.slice(0, 200),
                     cfError: /error code: (\d+)/.exec(text)?.[1] ?? null };
    if (!response.ok) {
      throw failure(`${method} ${path}: status ${response.status}`, detail);
    }
    try {
      return JSON.parse(text);
    } catch {
      throw failure(`${method} ${path}: status ${response.status}, not JSON`, detail);
    }
  }
  /** Runs `fn`; a failure is recorded under `label` and the run goes on. */
  async function attempt(label, fn) {
    try {
      return await fn();
    } catch (error) {
      report.driverErrors[label] = error.detail ?? { error: String(error.message) };
      log(`${label} failed: ${error.message}`);
      return undefined;
    }
  }
  async function collect(ids) {
    const got = await attempt("results", () => call("GET", `/results?only=${ids.join(",")}`));
    for (const id of got ? ids : []) {
      const { records, stale } = fresh(got.results[id], runId);
      report.results[id] = records;
      if (stale.length) {
        report.notes.push(`${id}: refused records from another run (${stale.join(", ")})`);
      }
    }
  }
  async function pollUntil(ids, graceMs) {
    const deadline = Date.now() + graceMs;
    await collect(ids);
    while (!ids.every((id) => finished(id, report.results[id])) && Date.now() < deadline) {
      await sleep(t.pollMs);
      await collect(ids);
    }
    const open = ids.filter((id) => !finished(id, report.results[id]));
    if (open.length) {
      report.notes.push(`not finished when read: ${open.join(", ")}`);
    }
  }
  function phase(name) {
    report.phase = name;
    log(name);
    onPhase(report);
  }

  const healthDeadline = Date.now() + t.healthMs;
  let healthy = false;
  while (!healthy) {
    try {
      await call("GET", "/health");
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
  await collect(CASES);
  phase(healthy ? "done" : "the probe never answered /health");
  return report;

  async function runCases() {
    const planResponse = await attempt("plan", () => request(`/plan?iters=${t.planIters}`, {
      method: "GET", headers: { "x-probe-run": runId }, signal: AbortSignal.timeout(t.requestMs),
    }));
    if (planResponse) {
      const text = await planResponse.text();
      report.plan = {
        status: planResponse.status, survivedCpuBurn: planResponse.ok,
        // Isolates tolerate an infrequent run over the CPU limit, so one burn that
        // survives says nothing (run 40701631460 survived on Free); a refusal does.
        reading: planResponse.ok
          ? "the CPU burn survived: inconclusive, since an isolate tolerates an infrequent overrun"
          : "the CPU burn was refused: the Free plan's 10 ms CPU limit is likely",
        body: text.slice(0, 200), cfError: /error code: (\d+)/.exec(text)?.[1] ?? null,
      };
    }
    phase("plan checked");

    for (const id of INLINE) {
      await attempt(id, async () => {
        const body = await call("POST", `/run/${id}?${query()}`);
        if (WORKER_CASES.includes(id) && body.recorded !== true) {
          await call("POST", `/record/${id}`, body);
          report.notes.push(`${id}: stored by the driver (${body.recorded})`);
        }
      });
    }
    await collect(INLINE);
    phase("inline cases");

    const backgroundStarted = Date.now();
    for (const id of [...BACKGROUND, ...ALARMS]) {
      await attempt(id, () => call("POST", `/run/${id}?${query()}`));
    }
    const alarmsArmed = Date.now();
    const a = attempt("M12 A", () => call("POST", `/run/M12?${query({ who: "A" })}`));
    await sleep(t.m12DelayMs);
    const b = attempt("M12 B", () => call("POST", `/run/M12?${query({ who: "B" })}`));
    await Promise.all([a, b]);
    await sleep(Math.max(0, alarmsArmed + t.alarmsFirstReadMs - Date.now()));
    await pollUntil([...ALARMS, "M12"], t.alarmsGraceMs);
    const { A, B } = report.results.M12 ?? {};
    report.checks.m12Order = m12Order(A, B);
    if (!report.checks.m12Order.ok) {
      report.notes.push("M12: B did not start after A's first phase, or a wait was not met; its reading is void");
    }
    phase("alarm cases and M12");

    await sleep(Math.max(0, backgroundStarted + p.n * p.intervalMs + t.backgroundSlackMs - Date.now()));
    await pollUntil(BACKGROUND, t.backgroundGraceMs);
    report.checks.observedCap = observedCap(report.results.M5?.result);
    if (subrequestsLimit && report.checks.observedCap.cap !== null
        && report.checks.observedCap.cap !== subrequestsLimit) {
      report.notes.push(`the cap M5 hit (${report.checks.observedCap.cap}) is not the configured `
                        + `limits.subrequests (${subrequestsLimit})`);
    }
    report.checks.m6aOrder = m6aOrder(report.results.M6a?.result);
    if (!report.checks.m6aOrder.ok) {
      report.notes.push("M6a: a loop call ran before the alarm was awaiting it; its reading is void");
    }
    phase("background cases");

    if (p.holdMs > 0) {
      const armed = await attempt("M10", () => call("POST", `/run/M10?${query()}`));
      phase(`M10 armed: holding ${p.holdMs} ms`);
      if (armed) {
        await sleep(p.holdMs + t.m10FirstReadMs);
        await pollUntil(["M10"], t.m10GraceMs);
      }
    } else {
      report.notes.push("M10 skipped (hold 0)");
    }
  }
}

/** M12 reads only if B started after A's first phase ended and every wait was met. */
export function m12Order(A, B) {
  if (!A?.phase1 || !B?.startedAt) {
    return { ok: false, why: "M12 has no fresh A and B records" };
  }
  const waits = { aWaitedForB: A.waitedForB?.met === true, aWaitedForBEnd: A.waitedForBEnd?.met === true,
                  bWaitedForA: B.waitedForA?.met === true };
  const bAfterA = B.startedAt > A.startedAt + A.phase1.totalMs;
  return { ok: bAfterA && Object.values(waits).every(Boolean), bAfterA, ...waits,
           aStartedAt: A.startedAt, aPhase1Ms: A.phase1.totalMs, bStartedAt: B.startedAt };
}

/** M6a reads only if the alarm was awaiting the loop before every one of its calls. */
export function m6aOrder(loop) {
  const calls = loop?.calls ?? [];
  const early = calls.filter((c) => !c.inAlarmBefore).map((c) => c.i);
  const waited = loop?.waitedForAlarm?.met === true;
  return { ok: calls.length > 0 && early.length === 0 && waited, calls: calls.length,
           beforeAlarm: early, waitedForAlarm: waited };
}

/** C as M5's alarm measured it: its RPC + fetch successes, if its fetch burst hit a cap. */
export function observedCap(m5) {
  if (!m5?.rpc || !m5?.fetch) {
    return { cap: null, why: "no fresh M5 result" };
  }
  return m5.fetch.errors > 0 || m5.rpc.errors > 0
    ? { cap: m5.rpc.ok + m5.fetch.ok, error: m5.fetch.firstError ?? m5.rpc.firstError }
    : { cap: null, why: `not reached by ${m5.rpc.n + m5.fetch.n} calls` };
}

/**
 * The reading key (cap-probe.md) as numbers: what each case shows under each rule, for a
 * per-invocation cap `C` and `N` calls per burst. A burst can show at most what it makes.
 */
export function rulePredictions(C, N) {
  const clamp = (x, max = N) => Math.max(0, Math.min(max, x));
  const h = Math.floor(N / 2);
  const f = Math.floor(N * 0.4);
  const s = N - f;
  const PRE = { worker: 16, arm: 8, start: 8, alarm: 4 };
  const capped = { capped: clamp(C), uncapped: N };
  return {
    M1: capped,
    M2: capped,
    M3: { oneCounter: [clamp(h, C), clamp(C - h, N - h)], twoCounters: [clamp(h, C), clamp(C, N - h)] },
    M4: { fresh: clamp(C), sharedStubFree: clamp(C - PRE.worker), sharedStubCounted: clamp(C - PRE.worker - 1),
          uncapped: N },
    M5: { fresh: clamp(C, 2 * N), continuesArm: clamp(C - PRE.arm, 2 * N), uncapped: 2 * N },
    M6: { startingRpcOrObject: clamp(C - PRE.start), fresh: clamp(C), uncapped: N },
    M6a: { neverReset: clamp(C - PRE.start - PRE.alarm), startingRpc: clamp(C - PRE.start),
           alarm: clamp(C - PRE.alarm), fresh: clamp(C), uncapped: N },
    M7: capped,
    M8: capped,
    M9: { socketOnce: clamp(C - 1, 2 * h), socketFree: clamp(C, 2 * h), messageRefreshes: 2 * clamp(C, h) },
    // [A + B phase 2, A phase 3]. Under "newest", phase 3 races B staying current, so
    // it is only ">=1" once B's counter is spent; otherwise A has room for all 10.
    M12: {
      perInvocation: [2 * clamp(C - f, s), clamp(C - Math.min(N, C), 10)],
      neverResetOrOldest: [clamp(C - 2 * f, 2 * s), clamp(C - 2 * f - 2 * s, 10)],
      newest: [clamp(C - f, 2 * s), C - f > 2 * s ? clamp(C - f - 2 * s, 10) : ">=1"],
      resetOnEvent: [clamp(C - f, 2 * s), clamp(C - f - 2 * s, 10)],
      uncapped: [2 * s, 10],
    },
  };
}

/** Per case: "full" if every rule predicts a different outcome, "none" if all agree. */
export function discrimination(C, N) {
  const out = {};
  for (const [id, rules] of Object.entries(rulePredictions(C, N))) {
    const outcomes = new Set(Object.values(rules).map((value) => JSON.stringify(value)));
    const count = Object.keys(rules).length;
    out[id] = outcomes.size === count ? "full" : outcomes.size === 1 ? "none" : "partial";
  }
  return out;
}

const minutes = (ms) => (typeof ms === "number" ? `${(ms / MINUTE).toFixed(1)} min` : "unknown");

/** M10's reading: the attempts' gap and the first attempt's last beat, together. */
export function m10Reading(stored) {
  const r = stored?.result;
  if (!r || r.stale) {
    return "no fresh result";
  }
  if (r.outcome === "finished") {
    return `finished after ${minutes(r.elapsedMs)}: no cut while the alarm awaited`;
  }
  if (r.outcome === "retried") {
    return r.gapMs >= 14 * MINUTE
      ? `cut: retried ${minutes(r.gapMs)} after the first start, whose last beat was at ${minutes(r.firstLastBeatMs)}`
      : `retried early (${minutes(r.gapMs)}; last beat ${minutes(r.firstLastBeatMs)}): a redelivery or crash, not the cut`;
  }
  if (r.outcome === "the call failed") {
    return `the awaited call failed after ${minutes(r.elapsedMs)}: ${r.error}`;
  }
  return `${r.status}, one attempt, last beat at ${minutes(stored.beat?.elapsedMs)}: a cut with no retry seen if the beats stopped near 15 min`;
}

const isBurst = (value) => value && typeof value === "object" && "kind" in value && "ok" in value;

/** A short table of the bursts, for the job summary. */
export function summarize(report) {
  const rows = [];
  for (const id of CASES) {
    const stored = report.results[id];
    const parts = id === "M12" ? { A: stored?.A, B: stored?.B } : { "": stored?.result };
    for (const [label, result] of Object.entries(parts)) {
      if (!result || result.stale) {
        rows.push(`| ${id} | ${label} | ${result?.stale ? "stale (refused)" : "(none)"} | | |`);
        continue;
      }
      let name = label;
      if (id === "M12") {
        const other = label === "A" ? stored.B : stored.A;
        const order = other?.startedAt ? (result.startedAt < other.startedAt ? "1st" : "2nd") : "?";
        const yes = (wait) => (wait?.met ? "yes" : "no");
        const waits = label === "A"
          ? `waited for B's phase 1: ${yes(result.waitedForB)}; for B's end: ${yes(result.waitedForBEnd)}`
          : `waited for A's phase 1: ${yes(result.waitedForA)}`;
        name = `${label} (arrived ${order}; ${waits})`;
      }
      const bursts = [];
      for (const holder of [result, id === "M6a" ? stored?.alarm : null]) {
        for (const [kind, value] of Object.entries(holder ?? {})) {
          if (isBurst(value)) {
            bursts.push([kind, value]);
          }
        }
      }
      if (Array.isArray(result.calls)) {
        bursts.push(["loop", { ok: result.ok, n: result.params?.n, firstError: result.firstError,
                               firstErrorAt: result.firstErrorAt }]);
      }
      if (id === "M10") {
        rows.push(`| M10 | | ${m10Reading(stored)} | ${result.status} | ${result.error ?? ""} |`);
        continue;
      }
      if (!bursts.length) {
        const target = (x) => `${x.url} ${x.error ? `error ${x.error}` : `${x.status}${x.cfError ? ` (${x.cfError})` : ""}`}`;
        const what = result.open ?? (result.targets
          ? result.targets.map(target).join("; ").replaceAll("|", "/") : "");
        rows.push(`| ${id} | ${name} | ${what} | ${result.status} | ${result.error ?? ""} |`);
      }
      for (const [kind, burst] of bursts) {
        const error = burst.firstError
          ? `${burst.firstError.replaceAll("|", "/")} (call ${burst.firstErrorAt})` : "";
        rows.push(`| ${id} | ${[name, kind].filter(Boolean).join(" ")} | ${burst.ok}/${burst.n} | ${result.status} | ${error} |`);
      }
    }
  }
  const errors = Object.entries(report.driverErrors).map(([label, d]) =>
    `- driver error, ${label}: ${d.status ?? ""} ${d.cfError ? `(error code ${d.cfError}) ` : ""}${(d.body ?? d.error ?? "").replaceAll("\n", " ")}`);
  const plan = report.plan ? `${report.plan.reading} (status ${report.plan.status})` : "not read";
  return [
    `Run \`${report.runId}\`, phase: ${report.phase}. Plan check: ${plan}.`,
    `Subrequest limit (limits.subrequests): ${report.subrequestsLimit}. N: ${report.params.n}. `
      + `Cap M5 hit: ${report.checks.observedCap?.cap ?? report.checks.observedCap?.why ?? "not read"}.`,
    ...(report.discrimination
      ? [`Discrimination at this limit and N: ${Object.entries(report.discrimination)
          .map(([id, level]) => `${id} ${level}`).join(", ")}.`]
      : []),
    ...["m12Order", "m6aOrder"].map((check) => `${check}: ${report.checks[check]
      ? (report.checks[check].ok ? "ok" : `FAILED (void) ${JSON.stringify(report.checks[check])}`)
      : "not reached"}.`), "",
    "| case | part | ok | status | first error |", "|---|---|---|---|---|", ...rows, "",
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
    throw new Error("usage: PROBE_KEY=... drive.mjs --base URL --run-id ID [--out F] [--summary F] [--hold-minutes M] [--burst N] [--subrequests-limit C]");
  }
  const holdMinutes = Number(args["hold-minutes"] ?? 16);
  if (!Number.isInteger(holdMinutes) || holdMinutes < 0 || holdMinutes > MAX_HOLD_MINUTES) {
    throw new Error(`--hold-minutes must be a whole number from 0 to ${MAX_HOLD_MINUTES}`);
  }
  const n = Number(args.burst ?? LIVE_PARAMS.n);
  if (!Number.isInteger(n) || n < MIN_BURST || n > MAX_BURST) {
    throw new Error(`--burst must be a whole number from ${MIN_BURST} to ${MAX_BURST}`);
  }
  // Empty means the plan's default; config.mjs has already refused anything out of range.
  const limitArg = args["subrequests-limit"] ?? "";
  const subrequestsLimit = limitArg === "" ? null : Number(limitArg);
  if (subrequestsLimit !== null && !Number.isInteger(subrequestsLimit)) {
    throw new Error("--subrequests-limit must be empty or a whole number");
  }
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
    request, runId: args["run-id"], params: { holdMs: holdMinutes * MINUTE, n }, onPhase: write,
    subrequestsLimit,
    log: (line) => console.log(`[${new Date().toISOString()}] ${redact(line, needles)}`),
  });
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
