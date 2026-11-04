#!/usr/bin/env node
/**
 * Drives the RPC retention probe's matrix (rpcretain-probe.yml): every form at every size,
 * each run twice, 30 s apart. It rewrites the results JSON and the summary after every call,
 * so a step killed partway still leaves what it had. Error text is redacted of each
 * `--redact` needle (comma-separated) and the token, since it lands in the job summary.
 *
 *   PROBE_TOKEN=... node drive.mjs --base https://... --out results.json --summary summary.md \
 *       [--redact a,b] [--gap-ms 30000] [--sizes 32,128,...] [--timeout-ms 180000]
 */
import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const FORMS = Object.freeze(["alloc", "rpcArr", "rpcStream", "retain"]);
export const SIZES = Object.freeze([32, 128, 256, 512, 1024]);
export const REPEATS = 2;
export const GAP_MS = 30_000;
export const TIMEOUT_MS = 180_000;
/** The size at which the control must have failed, for the probe to be able to see a hold. */
export const CONTROL_FAIL_BY = 256;

export function redact(text, needles) {
  return needles.filter(Boolean).reduce((out, needle) => out.split(needle).join("<redacted>"), String(text));
}

/** The matrix in run order: form by form, size by size, each cell's runs back to back. */
export function matrix(sizes = SIZES) {
  const out = [];
  for (const form of FORMS) {
    for (const mib of sizes) {
      for (let run = 1; run <= REPEATS; run++) {
        out.push({ form, mib, run });
      }
    }
  }
  return out;
}

/** One call: the worker's answer, or what came back instead (a reset, a timeout, a page). */
export async function call({ base, token, form, mib, fetcher = fetch, timeoutMs = TIMEOUT_MS }) {
  const t0 = Date.now();
  const url = `${base}/burst?form=${encodeURIComponent(form)}&mib=${mib}`;
  try {
    const response = await fetcher(url, { headers: { "x-probe-token": token },
                                          signal: AbortSignal.timeout(timeoutMs) });
    const text = await response.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
    if (body && typeof body.ok === "boolean") {
      return { http: response.status, ok: body.ok, held: body.held, ms: body.ms,
               error: body.error, wallMs: Date.now() - t0 };
    }
    const flat = text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 240);
    return { http: response.status, ok: false, error: `HTTP ${response.status}: ${flat}`, wallMs: Date.now() - t0 };
  } catch (error) {
    return { http: null, ok: false, error: `request failed: ${error?.name ?? ""} ${error?.message ?? error}`.trim(),
             wallMs: Date.now() - t0 };
  }
}

/** The control's reading and each form's, from the answers alone. */
export function readings(answers) {
  const cell = (form, mib) => answers.filter((a) => a.form === form && a.mib === mib);
  const allOk = (rows) => rows.length > 0 && rows.every((a) => a.ok);
  const allFail = (rows) => rows.length > 0 && rows.every((a) => !a.ok);
  const sizes = [...new Set(answers.map((a) => a.mib))].sort((a, b) => a - b);
  const firstFail = (form) => sizes.find((mib) => cell(form, mib).some((a) => !a.ok)) ?? null;
  const control = {
    smallestOk: allOk(cell("retain", Math.min(...sizes))),
    failedBy: sizes.filter((mib) => mib <= CONTROL_FAIL_BY).some((mib) => allFail(cell("retain", mib))),
    firstFail: firstFail("retain"),
  };
  control.valid = control.smallestOk && control.failedBy;
  const forms = {};
  for (const form of FORMS.filter((f) => f !== "retain")) {
    const fail = firstFail(form);
    forms[form] = { firstFail: fail, ran: cell(form, sizes.at(-1)).length > 0 };
    if (!control.valid) {
      forms[form].reading = "void: the control did not prove the probe can see a hold";
    } else if (fail === null) {
      forms[form].reading = `ok at every size to ${sizes.at(-1)} MiB: uncollected garbage is not condemned`;
    } else {
      forms[form].reading = `fails from ${fail} MiB: what it leaves behind counts against the limit`;
    }
  }
  return { control, forms };
}

export function summarize(report) {
  const cellText = (value) => String(value ?? "").replace(/\|/g, "/").replace(/\n/g, " ");
  const rows = report.answers.map((a) =>
    `| ${a.form} | ${a.mib} | ${a.run} | ${a.http ?? ""} | ${a.ok} | ${a.held ?? ""} | ${a.ms ?? ""} | ${a.wallMs} | ${cellText(a.error)} |`);
  const g = readings(report.answers);
  const c = g.control;
  return [
    "#### Answers", "",
    `Started ${new Date(report.startedAt).toISOString()}; ${report.answers.length} of ${report.planned} calls; phase ${report.phase}.`, "",
    "| form | MiB | run | HTTP | ok | held | ms (in object) | wall ms | error |",
    "|---|---|---|---|---|---|---|---|---|", ...rows, "",
    "#### Readings (from the answers)", "",
    `- **control (retain):** ${c.valid ? "valid" : "NOT valid"}; smallest size ok: ${c.smallestOk}; `
      + `failed every run at some size up to ${CONTROL_FAIL_BY} MiB: ${c.failedBy}; first failure at ${c.firstFail ?? "none"} MiB`
      + (c.valid ? "" : ". The probe proves nothing about the other forms."),
    ...Object.entries(g.forms).map(([form, r]) => `- **${form}:** ${r.reading}`),
    ...(report.driverError ? [`- **driver error:** ${cellText(report.driverError)}`] : []),
  ].join("\n");
}

export async function drive({ base, token, sizes = SIZES, gapMs = GAP_MS, timeoutMs = TIMEOUT_MS,
                              fetcher = fetch, onCall = () => {}, sleep }) {
  const wait = sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const plan = matrix(sizes);
  const report = { startedAt: Date.now(), planned: plan.length, sizes, gapMs, answers: [], phase: "running" };
  for (const [i, step] of plan.entries()) {
    if (i > 0) {
      await wait(gapMs);
    }
    const at = new Date().toISOString();
    report.answers.push({ ...step, at, ...(await call({ base, token, ...step, fetcher, timeoutMs })) });
    onCall(report);
  }
  report.phase = "done";
  report.endedAt = Date.now();
  onCall(report);
  return report;
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    args[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  return args;
}

async function main(args) {
  const token = process.env.PROBE_TOKEN;
  if (!token || !args.base || !args.out) {
    throw new Error("usage: PROBE_TOKEN=... drive.mjs --base URL --out F [--summary F]");
  }
  const needles = [token, ...(args.redact ?? "").split(",")].filter(Boolean);
  const write = (report) => {
    writeFileSync(args.out, `${redact(JSON.stringify(report, null, 2), needles)}\n`);
    if (args.summary) {
      writeFileSync(args.summary, `${redact(summarize(report), needles)}\n`);
    }
  };
  const sizes = args.sizes ? args.sizes.split(",").map(Number) : SIZES;
  let last = null;
  try {
    last = await drive({ base: args.base.replace(/\/$/, ""), token, sizes,
                         gapMs: Number(args["gap-ms"] ?? GAP_MS), timeoutMs: Number(args["timeout-ms"] ?? TIMEOUT_MS),
                         onCall: (report) => {
                           last = report;
                           const a = report.answers.at(-1);
                           if (a && report.phase === "running") {
                             console.log(redact(`${a.form} ${a.mib} MiB run ${a.run}: ok=${a.ok} http=${a.http} `
                                                + `${a.error ? `error=${a.error}` : `ms=${a.ms}`}`, needles));
                           }
                           write(report);
                         } });
  } catch (error) {
    if (last) {
      last.driverError = String(error?.message ?? error);
      write(last);
    }
    throw error;
  }
  console.log(redact(summarize(last), needles));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main(parseArgs(process.argv.slice(2))).catch((error) => {
    console.error(`::error::${redact(error.message, [process.env.PROBE_TOKEN])}`);
    process.exit(1);
  });
}
