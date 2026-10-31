#!/usr/bin/env node
/**
 * Drives the facet probe through every question and collects the results
 * (openspec/changes/clean-sheet-platform/facet-probe.md). `facet-probe.yml` runs it against
 * the deployed copies through the `ta-facetprobe-door` Workflow, one instance per call,
 * started and read through the Cloudflare API; `local.mjs` runs the same `drive` against
 * miniflare through the door's local-only fetch.
 *
 *   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... node drive.mjs --run-id <id> \
 *       --out results.json --summary summary.md --deploy deploy.json [--fill-minutes 100] \
 *       [--redact <text>]
 *
 * Order: Q2 on every parent copy; Q3, Q5 (small) and Q6 on the control and on main; Q8 on
 * main (deleteAll, a write and a crash in one turn, 20 trials per variant); Q4
 * alone on the control, before anything fills, since a broken facet may reset the isolate;
 * Q1's two fills at once, in two parents, polled from storage only; Q5 at size, on Q1's
 * big facet; Q7 if Q1 and Q2 pass. A failing call is recorded and the run goes on; the
 * report is written after every phase, so a killed run still leaves what it had.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { DOOR, GB, GIB_RATIO, SHAPE } from "./shape.mjs";
import { errText } from "./stats.mjs";

const MINUTE = 60_000;
export const MIN_FILL_MINUTES = 10;
export const MAX_FILL_MINUTES = 150;
const PARENTS = Object.freeze(["main", "exports", "classbind", "unsafe"]);
export const Q8_VARIANTS = Object.freeze(["same-turn-noabort", "same-turn", "awaited"]);

export const LIVE_TIMING = Object.freeze({
  instancePollMs: 1500, callTimeoutMs: 17 * MINUTE, fillPollMs: 30_000, fillGraceMs: 5 * MINUTE,
});
export const LIVE_PARAMS = Object.freeze({
  fillMinutes: 150, partBytes: SHAPE.partBytes, capBytes: SHAPE.capBytes, limitBytes: SHAPE.limitBytes, q6N: SHAPE.q6N,
  q8N: SHAPE.q8N,
  q4: Object.freeze({ yieldMs: SHAPE.q4YieldMs, solidMs: SHAPE.q4SolidMs, overCpuMs: SHAPE.q4OverCpuMs,
                      cases: Object.freeze(["yield", "solid", "throw", "overcpu", "oom"]) }),
});
/** If the yield case gives no rate: iterations per CPU millisecond, a conservative guess. */
const FALLBACK_RATE = 300_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Replaces every needle (the account id, the token, ...) in `text`. */
export function redact(text, needles) {
  let out = String(text);
  for (const needle of needles) {
    if (needle && needle.length >= 4) {
      out = out.split(needle).join("<redacted>");
    }
  }
  return out;
}

/**
 * The live transport: one Workflow instance per call, through the Cloudflare API. Returns
 * `call({ copy, object, method, params })`, which resolves to the call's result or throws.
 */
export function workflowCall({ token, account, runId, fetcher = fetch, timing = LIVE_TIMING }) {
  const base = `https://api.cloudflare.com/client/v4/accounts/${account}/workflows/${DOOR}/instances`;
  const api = async (url, init = {}) => {
    const response = await fetcher(url, {
      ...init, signal: AbortSignal.timeout(60_000),
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.success === false) {
      const messages = (body.errors ?? []).map((e) => e.message).join("; ");
      throw new Error(`Workflows API HTTP ${response.status}: ${messages || "no detail"}`);
    }
    return body.result;
  };
  return async ({ copy, object, method, params }) => {
    const created = await api(base, { method: "POST",
                                      body: JSON.stringify({ params: { run: runId, copy, object, method, params } }) });
    const id = created?.id;
    if (!id) {
      throw new Error("the Workflows API returned no instance id");
    }
    const until = Date.now() + timing.callTimeoutMs;
    let wait = 300;
    while (Date.now() < until) {
      await sleep(wait);
      wait = Math.min(timing.instancePollMs, wait * 2);
      let got;
      try {
        got = await api(`${base}/${id}`);
      } catch (error) {
        // A read that fails is retried until the call's own timeout.
        continue;
      }
      if (got?.status === "complete") {
        if (!("output" in got)) {
          throw new Error(`instance complete, but the API gave no output (fields: ${Object.keys(got).join(", ")})`);
        }
        let output = got.output;
        if (typeof output === "string") {
          output = JSON.parse(output);
        }
        if (output?.error) {
          throw new Error(output.error);
        }
        return output?.result;
      }
      if (["errored", "terminated"].includes(got?.status)) {
        throw new Error(`instance ${got.status}: ${JSON.stringify(got.error ?? null).slice(0, 300)}`);
      }
    }
    throw new Error(`no answer within ${timing.callTimeoutMs / MINUTE} minutes`);
  };
}

const accepted = (deploy, copy) => /^accepted/.test(deploy?.[copy]?.status ?? "");

/**
 * Runs every question. `call` is a transport (workflowCall, or local.mjs's); `deploy` is
 * what the workflow saw when it deployed each copy (Q2's deploy readings, and which copies
 * exist). `onPhase(report)` is called after each phase with the report so far.
 */
export async function drive({ call, runId, params = {}, timing = {}, deploy = {}, kv = null, onPhase = () => {} }) {
  const p = { ...LIVE_PARAMS, ...params, q4: { ...LIVE_PARAMS.q4, ...(params.q4 ?? {}) } };
  const t = { ...LIVE_TIMING, ...timing };
  const report = { runId, startedAt: Date.now(), phase: "start", params: p, deploy, kv, results: {}, driverErrors: {} };
  const record = async (key, request) => {
    try {
      report.results[key] = await call(request);
    } catch (error) {
      report.driverErrors[key] = errText(error);
    }
    return report.results[key];
  };
  const phase = (name) => {
    report.phase = name;
    onPhase(report);
  };
  const has = (copy) => accepted(deploy, copy);

  for (const copy of PARENTS.filter(has)) {
    await record(`Q2 ${copy}`, { copy, object: "q2", method: "q2" });
  }
  phase("Q2");

  const sources = [["exports", "exports"], ["main", "loader"]].filter(([copy]) => has(copy));
  for (const [copy, source] of sources) {
    await record(`Q3 ${source}`, { copy, object: "q3", method: "q3", params: { source } });
    await record(`Q5 ${source}`, { copy, object: "q5", method: "q5", params: { source } });
    await record(`Q6 ${source}`, { copy, object: "q6", method: "q6", params: { source, n: p.q6N } });
  }
  phase("Q3 Q5 Q6");

  if (has("main")) {
    for (const variant of Q8_VARIANTS) {
      await record(`Q8 ${variant}`, { copy: "main", object: "q8", method: "q8", params: { variant, n: p.q8N } });
    }
    phase("Q8");
  }

  if (has("exports")) {
    let rate = null;
    for (const kase of p.q4.cases) {
      const iters = Math.round((rate ?? FALLBACK_RATE) * (kase === "overcpu" ? p.q4.overCpuMs : p.q4.solidMs));
      const boundMs = { yield: p.q4.yieldMs + 30_000, solid: p.q4.solidMs * 4 + 30_000, throw: 15_000,
                        overcpu: p.q4.overCpuMs * 2 + 30_000, oom: 60_000 }[kase];
      const got = await record(`Q4 ${kase}`, { copy: "exports", object: "q4", method: "q4",
                                               params: { kase, source: "exports", ms: p.q4.yieldMs, iters, boundMs } });
      const value = got?.after?.outcome?.value;
      if (kase === "yield" && value?.iters && value?.ms) {
        rate = value.iters / value.ms;
        report.q4Rate = rate;
      }
    }
    phase("Q4");

    const deadlineMs = p.fillMinutes * MINUTE;
    const plans = {
      "Q1 bc": [{ target: "self", bytes: p.partBytes }, { target: "q1b", bytes: p.partBytes },
                { target: "q1c", bytes: p.partBytes }],
      "Q1 a": [{ target: "q1a", bytes: p.capBytes }],
    };
    const objectOf = { "Q1 bc": "q1-bc", "Q1 a": "q1-a" };
    for (const [key, jobs] of Object.entries(plans)) {
      await record(`${key} start`, { copy: "exports", object: objectOf[key], method: "fillStart",
                                     params: { jobs, deadlineMs, source: "exports" } });
    }
    const until = Date.now() + deadlineMs + t.fillGraceMs;
    const running = () => Object.keys(plans).some((key) => {
      const status = report.results[key]?.plan?.status;
      return report.results[`${key} start`] && (!status || status === "running");
    });
    do {
      await sleep(t.fillPollMs);
      for (const key of Object.keys(plans)) {
        if (report.results[`${key} start`]) {
          await record(key, { copy: "exports", object: objectOf[key], method: "fillStatus" });
        }
      }
      onPhase(report);
    } while (running() && Date.now() < until);
    for (const key of Object.keys(plans)) {
      await record(`${key} sizes`, { copy: "exports", object: objectOf[key], method: "fillSizes" });
    }
    phase("Q1");

    await record("Q5 at size", { copy: "exports", object: "q1-a", method: "deleteFacet",
                                 params: { name: "q1a", source: "exports" } });
    phase("Q5 at size");
  }

  report.readings = readings(report);
  const gate = q7Gate(report.readings);
  if (gate.run) {
    for (const copy of ["agents", "agents-exports"].filter(has)) {
      await record(`Q7 ${copy}`, { copy, object: "q7", method: "q7" });
    }
  } else {
    report.q7Skipped = gate.why;
  }
  report.phase = "done";
  report.readings = readings(report);
  onPhase(report);
  return report;
}

/** Q7 runs only if Q1 shows separate databases and Q2 shows a facet working somewhere. */
export function q7Gate(g) {
  if (g.Q1?.verdict !== "separate") {
    return { run: false, why: `Q1 did not show a facet outside its parent's budget (${g.Q1?.verdict ?? "not read"})` };
  }
  if (!g.Q2?.anyWorks) {
    return { run: false, why: "Q2 found no facet class source that works" };
  }
  return { run: true };
}

const gb = (bytes) => (typeof bytes === "number" ? `${(bytes / GB).toFixed(2)} GB` : String(bytes));

/**
 * Q1's readings from the two fill plans, in two parts.
 * - Parent vs facet (`verdict`, Q7's gate): "separate" only when the parent and `q1b`
 *   both reached their sizes, together at least 1.1 times the larger possible limit;
 *   "shared" when `q1b` hit a limit with the two together within 0.9-1.1 of either
 *   possible limit (a failed plan write, `persistError`, is reported beside it, never read alone).
 * - Facet vs facet (`facetVerdict`): read only after "separate": "separate" when `q1c`
 *   reached its size too, "pooled" when `q1c` hit a limit with `q1b` and `q1c` together
 *   within 0.9-1.1 of a limit.
 * A plan still running at the end is "stalled", a non-limit error "void", anything else
 * (a time cap among them) "unproven".
 */
export function q1Reading(bc, a, limitBytes = SHAPE.limitBytes) {
  const limits = [limitBytes, limitBytes * GIB_RATIO];
  const inBand = (bytes) => limits.some((limit) => bytes >= 0.9 * limit && bytes <= 1.1 * limit);
  const plan = bc?.plan;
  const jobs = Object.fromEntries((plan?.jobs ?? []).map((job) => [job.target, job]));
  const big = a?.plan?.jobs?.[0];
  const self = jobs.self;
  const b = jobs.q1b;
  const c = jobs.q1c;
  const size = (job) => job?.size ?? 0;
  const status = plan?.status ?? "not read";
  const persist = plan?.persistError ?? null;
  const out = { bc: status, a: a?.plan?.status ?? "not read", persistError: persist,
                limitsRead: limits.map(gb) };
  out.parts = { self: self?.size ?? null, q1b: b?.size ?? null, q1c: c?.size ?? null, q1a: big?.size ?? null };
  out.statuses = { self: self?.status ?? null, q1b: b?.status ?? null, q1c: c?.status ?? null, q1a: big?.status ?? null };
  out.errors = Object.fromEntries([self, b, c, big].filter((job) => job?.error).map((job) => [job.target, job.error]));
  const pair = size(self) + size(b);
  if (self?.status === "reached" && b?.status === "reached" && pair >= 1.1 * Math.max(...limits)) {
    out.verdict = "separate";
    out.detail = `the parent reached ${gb(self.size)} and its facet q1b ${gb(b.size)}, ${gb(pair)} together, with no limit error: a facet's database does not count against its parent's`;
  } else if (b?.status === "limit" && inBand(pair)) {
    out.verdict = "shared";
    out.detail = `q1b hit a limit at ${gb(b.size)} with the parent at ${gb(self?.size)} (${gb(pair)} together)`
      + `${persist ? `, and the parent's own write then failed: ${persist.error}` : ""}: one budget`;
  } else if (/^aborted/.test(status)) {
    out.verdict = "void";
    out.detail = `a non-limit error stopped the fill: ${Object.values(out.errors)[0] ?? status}`;
  } else if (status === "running") {
    out.verdict = "stalled";
    out.detail = `the plan was still running at the end (parent ${gb(self?.size)}, q1b ${gb(b?.size)}${persist ? `; a plan write failed: ${persist.error}` : ""})`;
  } else {
    out.verdict = "unproven";
    out.detail = `${status}: parent ${gb(self?.size)} (${self?.status ?? "-"}), q1b ${gb(b?.size)} (${b?.status ?? "-"})`;
  }
  if (out.verdict !== "separate") {
    out.facetVerdict = "not read (needs the parent vs facet reading first)";
  } else if (c?.status === "reached") {
    out.facetVerdict = "separate";
    out.facetDetail = `q1c reached ${gb(c.size)} beside q1b's ${gb(b.size)}: facets do not share one pool`;
  } else if (c?.status === "limit" && inBand(size(b) + size(c))) {
    out.facetVerdict = "pooled";
    out.facetDetail = `q1c hit a limit at ${gb(c.size)} with q1b at ${gb(b.size)} (${gb(size(b) + size(c))} together): the facets share one pool`;
  } else {
    out.facetVerdict = "unproven";
    out.facetDetail = `q1c ${gb(c?.size)} (${c?.status ?? "-"}${c?.error ? `: ${c.error}` : ""})`;
  }
  if (big) {
    out.facetLimit = big.status === "limit"
      ? `a lone facet stopped at ${gb(big.size)}${inBand(size(big)) ? ", at a 10 GB limit of its own" : ", not near 10 GB or 10 GiB"}: ${big.error}`
      : big.status === "reached" ? `a lone facet passed ${gb(big.size)} with no limit error`
        : `a lone facet reached ${gb(big.size)} (${big.status}${big.error ? `: ${big.error}` : ""})`;
  }
  return out;
}

/**
 * Q8's reading from its three variants. The order control (`same-turn-noabort`) must read
 * wiped and written in every trial, and the crash-model control (`awaited`) wiped and not
 * written in every trial; only then is a same-turn "wiped and not written" F1's crash
 * window. A control that itself loses the row is a different finding.
 */
export function q8Reading(control, sameTurn, awaited) {
  const n = sameTurn?.n ?? 0;
  const c = control?.counts;
  const s = sameTurn?.counts;
  const a = awaited?.counts;
  const notWiped = s ? s.notWipedWritten + s.notWipedNotWritten : 0;
  if (!c || !a) {
    return { verdict: "void", detail: "a control did not run" };
  }
  if (c.wipedNotWritten > 0) {
    return { verdict: "deleteAll erases a same-turn write",
             detail: `with no crash, ${c.wipedNotWritten} of ${control.n} same-turn rows were lost: the un-awaited deleteAll lands after the write` };
  }
  if (c.wipedWritten !== control.n || c.notKilled !== control.n) {
    return { verdict: "void", detail: `the order control read ${JSON.stringify(c)}, not ${control.n} of ${control.n} wiped and written` };
  }
  if (a.wipedNotWritten !== awaited.n || awaited.freshInstances !== awaited.n) {
    return { verdict: "void", detail: `the crash-model control read ${JSON.stringify(a)}: ctx.abort() did not act as a crash before commit` };
  }
  if (s.wipedNotWritten > 0) {
    return { verdict: "crash window",
             detail: `${s.wipedNotWritten} of ${n} same-turn trials were wiped with the row lost: the delete commits apart from the same-turn write` };
  }
  if (notWiped === n) {
    return { verdict: "no window at this crash point",
             detail: `every trial kept the old rows and lost the row: consistent with one atomic commit at this one crash point; 20 trials sample one moment, so this does not prove there is no window` };
  }
  return { verdict: "unproven", detail: `same-turn read ${JSON.stringify(s)}` };
}

/** Each question's reading from the results. */
export function readings(report) {
  const r = report.results ?? {};
  const g = {};
  g.Q2 = { anyWorks: false, copies: {} };
  for (const copy of PARENTS) {
    const deployed = report.deploy?.[copy];
    const got = r[`Q2 ${copy}`];
    g.Q2.copies[copy] = {
      deploy: deployed?.status ?? "not tried", deployError: deployed?.error ?? null,
      sources: got ? Object.fromEntries(Object.entries(got.sources).map(([source, x]) =>
        [source, x.ok ? "works" : x.error])) : null,
      ctxExports: got?.parent?.exports ?? null,
      error: report.driverErrors?.[`Q2 ${copy}`] ?? null,
    };
    if (got && Object.values(got.sources).some((x) => x.ok)) {
      g.Q2.anyWorks = true;
    }
  }
  const ours = ["main", "classbind", "unsafe"].flatMap((copy) =>
    Object.entries(g.Q2.copies[copy].sources ?? {}).filter(([, v]) => v === "works").map(([s]) => `${copy}:${s}`));
  g.Q2.underDisableCtxExports = ours;
  for (const source of ["exports", "loader"]) {
    const q3 = r[`Q3 ${source}`];
    if (q3) {
      g[`Q3 ${source}`] = {
        facetEnv: q3.facet.envKeys, parentEnv: q3.parent.envKeys, facetExports: q3.facet.exportsKeys,
        reach: Object.fromEntries(Object.entries(q3.reach).map(([k, v]) => [k, v.ok ? "reached" : v.error])),
      };
    }
    const q5 = r[`Q5 ${source}`];
    if (q5) {
      g[`Q5 ${source}`] = { wiped: q5.after.rows === 0 && q5.after.size < q5.before.size, before: q5.before.size,
                            after: q5.after.size, rowsAfter: q5.after.rows, fresh: q5.freshInstance,
                            oldStub: q5.oldStub.ok ? "still answers" : q5.oldStub.error, deleteMs: q5.deleteMs,
                            parentBefore: q5.parentBefore, parentAfter: q5.parentAfter };
    }
    const q6 = r[`Q6 ${source}`];
    if (q6) {
      // The clock moves in whole milliseconds: a 0 percentile reads "<1", and meanMs (the series' total over n) resolves it.
      const ms = (v) => (v === 0 ? "<1" : v);
      const pick = (s) => (s ? { p50: ms(s.p50), p95: ms(s.p95), p99: ms(s.p99), meanMs: s.meanMs, n: s.n, errors: s.errors } : null);
      g[`Q6 ${source}`] = { sharedId: pick(q6.sharedId), ownId: pick(q6.ownId), store: pick(q6.store), ids: q6.ids,
                            parentId: q6.parentId };
    }
  }
  for (const kase of LIVE_PARAMS.q4.cases) {
    const q4 = r[`Q4 ${kase}`];
    if (q4) {
      const startedOn = q4.started?.instance;
      g[`Q4 ${kase}`] = {
        baselineP50: q4.baseline.p50, duringP50: q4.during.p50, duringP99: q4.during.p99, duringMax: q4.during.max,
        pings: q4.during.n, pingErrors: q4.during.errors, firstPingError: q4.during.firstError,
        parentReset: q4.after?.instance ? q4.after.instance !== startedOn : "unknown",
        parentInstances: q4.instances.length,
        outcome: q4.after?.outcome ? (q4.after.outcome.ok ? "returned" : q4.after.outcome.error) : "never settled (lost with the parent's memory, or still running)",
        facetAfter: q4.after?.facetAfter ? (q4.after.facetAfter.ok ? "answers" : q4.after.facetAfter.error) : null,
      };
      // A burn blocks the parent when one ping waits for half the burn; otherwise, a second.
      const burnMs = { solid: report.params?.q4?.solidMs, overcpu: report.params?.q4?.overCpuMs }[kase];
      g[`Q4 ${kase}`].blocked = burnMs ? q4.during.max >= burnMs / 2
        : q4.during.max > 1000 + 20 * (q4.baseline.p50 ?? 0);
    }
  }
  if (r["Q1 bc"] || r["Q1 a"]) {
    g.Q1 = q1Reading(r["Q1 bc"], r["Q1 a"], report.params?.limitBytes ?? SHAPE.limitBytes);
    g.Q1.sizesAtEnd = { bc: r["Q1 bc sizes"] ?? null, a: r["Q1 a sizes"] ?? null };
    const rows = [r["Q1 bc"], r["Q1 a"]].flatMap((x) => x?.plan?.jobs ?? []).reduce((n, job) => n + job.rows, 0);
    g.Q1.rowsWritten = rows;
    g.Q1.bytesWritten = rows * SHAPE.rowBytes;
  }
  const big = r["Q5 at size"];
  if (big) {
    g["Q5 at size"] = { wiped: big.after.rows === 0 && big.after.size < big.before.size,
                        before: big.before.size, after: big.after.size, rowsAfter: big.after.rows,
                        deleteMs: big.deleteMs, fresh: big.freshInstance, parentBefore: big.parentBefore,
                        parentAfter: big.parentAfter };
  }
  for (const copy of ["agents", "agents-exports"]) {
    const q7 = r[`Q7 ${copy}`];
    if (q7) {
      g[`Q7 ${copy}`] = { exports: q7.exports,
                          steps: Object.fromEntries(["plain", "spawn", "first", "second", "list", "delete", "afterDelete"]
                            .filter((k) => q7[k]).map((k) => [k, q7[k].ok ? "ok" : q7[k].error])),
                          firstText: q7.first?.value?.text ?? null, childEnv: q7.first?.value?.envKeys ?? null,
                          afterDeleteMessages: q7.afterDelete?.value?.messages ?? null };
    }
  }
  for (const variant of Q8_VARIANTS) {
    const q8 = r[`Q8 ${variant}`];
    if (q8) {
      g[`Q8 ${variant}`] = { counts: q8.counts, n: q8.n, freshInstances: q8.freshInstances, armErrors: q8.armErrors,
                             sizes: q8.sizes };
    }
  }
  if (r["Q8 same-turn"]) {
    g.Q8 = q8Reading(r["Q8 same-turn-noabort"], r["Q8 same-turn"], r["Q8 awaited"]);
  }
  if (report.q7Skipped) {
    g.Q7 = { skipped: report.q7Skipped };
  }
  return g;
}

const cell = (value) => String(typeof value === "string" ? value : JSON.stringify(value))
  .replace(/\|/g, "/").replace(/\n/g, " ").slice(0, 600);

/** The job summary: one table row per reading, then the driver's errors. */
export function summarize(report) {
  const g = report.readings ?? readings(report);
  const rows = [];
  const row = (question, kase, reading, detail = "") => rows.push(`| ${question} | ${kase} | ${cell(reading)} | ${cell(detail)} |`);
  if (g.Q1) {
    row("Q1", "parent vs facet", g.Q1.verdict, g.Q1.detail);
    row("Q1", "facet vs facet", g.Q1.facetVerdict, g.Q1.facetDetail ?? "");
    row("Q1", "a lone facet", g.Q1.facetLimit ?? "not read", g.Q1.parts);
    row("Q1", "written", `${g.Q1.rowsWritten} rows, ${gb(g.Q1.bytesWritten)}`,
        { bc: g.Q1.bc, a: g.Q1.a, statuses: g.Q1.statuses, errors: g.Q1.errors, persistError: g.Q1.persistError });
  } else {
    row("Q1", "parent vs facet", "not read");
  }
  for (const [copy, x] of Object.entries(g.Q2.copies)) {
    row("Q2", copy, x.deploy, x.deployError ?? x.error ?? { ctxExports: x.ctxExports, sources: x.sources });
  }
  row("Q2", "under disable_ctx_exports", g.Q2.underDisableCtxExports.join(", ") || "none works");
  for (const source of ["exports", "loader"]) {
    if (g[`Q3 ${source}`]) {
      row("Q3", `${source} facet`, { facetEnv: g[`Q3 ${source}`].facetEnv }, g[`Q3 ${source}`]);
    }
  }
  for (const kase of LIVE_PARAMS.q4.cases) {
    const x = g[`Q4 ${kase}`];
    if (x) {
      row("Q4", kase, x.blocked ? "parent blocked" : "parent answered",
          { baselineP50: x.baselineP50, duringP50: x.duringP50, duringMax: x.duringMax, pingErrors: x.pingErrors,
            parentReset: x.parentReset, outcome: x.outcome, facetAfter: x.facetAfter });
    }
  }
  for (const key of ["Q5 exports", "Q5 loader", "Q5 at size"]) {
    if (g[key]) {
      row("Q5", key.slice(3), g[key].wiped ? "wiped" : "not wiped", g[key]);
    }
  }
  for (const source of ["exports", "loader"]) {
    const x = g[`Q6 ${source}`];
    if (x) {
      row("Q6", `${source}: facet, parent's id`, x.sharedId);
      if (x.ownId) {
        row("Q6", `${source}: facet, own id`, x.ownId);
      }
      row("Q6", `${source}: top-level ThreadStore`, x.store);
    }
  }
  if (g.Q8) {
    row("Q8", "deleteAll's crash window", g.Q8.verdict, g.Q8.detail);
  }
  for (const variant of Q8_VARIANTS) {
    const x = g[`Q8 ${variant}`];
    if (x) {
      const c = x.counts;
      row("Q8", `deleteAll, ${variant}${variant.endsWith("noabort") ? "" : ", then abort"}`,
          `${c.wipedWritten} wiped and written, ${c.wipedNotWritten} wiped and not written, `
          + `${c.notWipedWritten + c.notWipedNotWritten} not wiped (of ${x.n})`,
          { counts: c, killedAndReset: x.freshInstances, armErrors: x.armErrors, sizes: x.sizes });
    }
  }
  for (const copy of ["agents", "agents-exports"]) {
    if (g[`Q7 ${copy}`]) {
      const steps = g[`Q7 ${copy}`].steps;
      row("Q7", copy, { plainChild: steps.plain, piChild: steps.first ?? steps.spawn }, g[`Q7 ${copy}`]);
    }
  }
  if (g.Q7?.skipped) {
    row("Q7", "PiHarness in a facet", "skipped", g.Q7.skipped);
  }
  const errors = Object.entries(report.driverErrors ?? {}).map(([key, error]) => `- ${key}: ${cell(error)}`);
  return [
    `#### Facet probe readings (run ${report.runId}, phase ${report.phase})`, "",
    "| question | case | reading | detail |", "|---|---|---|---|", ...rows, "",
    ...(report.kv ? [`- **KV:** ${cell(report.kv)}`] : []),
    ...(errors.length ? ["**Calls that failed:**", ...errors] : ["No call failed."]),
  ].join("\n");
}

async function main(args) {
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const account = process.env.CLOUDFLARE_ACCOUNT_ID;
  if (!token || !account || !args["run-id"] || !args.out) {
    throw new Error("usage: CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... drive.mjs --run-id ID --out F [--summary F]");
  }
  const needles = [token, account, ...(args.redact ?? "").split(",").filter(Boolean)];
  const fillMinutes = Number(args["fill-minutes"] ?? LIVE_PARAMS.fillMinutes);
  if (!Number.isInteger(fillMinutes) || fillMinutes < MIN_FILL_MINUTES || fillMinutes > MAX_FILL_MINUTES) {
    throw new Error(`--fill-minutes must be a whole number from ${MIN_FILL_MINUTES} to ${MAX_FILL_MINUTES}`);
  }
  const deploy = args.deploy ? JSON.parse(readFileSync(args.deploy, "utf8")) : {};
  const kv = args.kv ? readFileSync(args.kv, "utf8").trim() : null;
  const write = (report) => {
    report.readings = readings(report);
    writeFileSync(args.out, `${redact(JSON.stringify(report, null, 2), needles)}\n`);
    if (args.summary) {
      writeFileSync(args.summary, `${redact(summarize(report), needles)}\n`);
    }
    console.log(`phase ${report.phase}: ${Object.keys(report.results).length} results, `
                + `${Object.keys(report.driverErrors).length} failed calls`);
  };
  const call = workflowCall({ token, account, runId: args["run-id"] });
  const report = await drive({ call, runId: args["run-id"], params: { fillMinutes }, deploy, kv, onPhase: write });
  write(report);
  console.log(redact(summarize(report), needles));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = {};
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 2) {
    args[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  main(args).catch((error) => {
    console.error(`::error::${redact(error.message, [process.env.CLOUDFLARE_API_TOKEN, process.env.CLOUDFLARE_ACCOUNT_ID])}`);
    process.exit(1);
  });
}
