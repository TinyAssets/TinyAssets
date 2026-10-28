#!/usr/bin/env node
/**
 * Drives ta-capprobe through every case of the 32-call cap probe and collects the results
 * (openspec/changes/clean-sheet-platform/cap-probe.md). `cap-probe.yml` runs it against
 * the deployed Worker; `local.mjs` runs the same `drive` against miniflare.
 *
 *   PROBE_KEY=... node drive.mjs --base https://ta-capprobe.<sub>.workers.dev \
 *       --out results.json --summary summary.md [--hold-minutes 16] [--redact <text>]
 *
 * Order: the Worker cases and M4; then M6 and M6a (background loops) and the alarm cases
 * M5, M7, M8, M9; M12's two invocations at once; M10 (the 16-minute alarm) last. No case
 * is read while it should still be running: a read is an incoming call to that object,
 * and which invocation a call is charged to is part of what the probe measures.
 */
import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const CASES = Object.freeze([
  "M1", "M2", "M3", "M4", "M5", "M6", "M6a", "M7", "M8", "M9", "M10", "M11", "M12",
]);
const INLINE = Object.freeze(["M1", "M2", "M3", "M11", "M4"]);
const ALARMS = Object.freeze(["M5", "M7", "M8", "M9"]);
const BACKGROUND = Object.freeze(["M6", "M6a"]);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Whether a case's stored result is final. */
export function finished(id, stored) {
  if (id === "M12") {
    return Boolean(stored?.A && stored?.B);
  }
  return ["done", "failed"].includes(stored?.result?.status);
}

/**
 * Runs every case. `request(path, init)` answers like `fetch` against the Worker, with
 * the key already attached. `timing` scales the waits: the live run uses the defaults.
 */
export async function drive({ request, params = {}, timing = {}, log = () => {} }) {
  const t = {
    pollMs: 5000, healthMs: 120_000, alarmsFirstReadMs: 30_000, alarmsMs: 5 * 60_000,
    backgroundSlackMs: 30_000,
    m10FirstReadMs: 60_000, m10GraceMs: 5 * 60_000, planIters: 1e8, ...timing,
  };
  const p = { n: 40, intervalMs: 2000, holdMs: 16 * 60_000, ...params };
  const query = (extra = {}) => new URLSearchParams(
    Object.entries({ ...p, ...extra }).map(([key, value]) => [key, String(value)]),
  ).toString();
  const notes = [];

  async function call(method, path, body) {
    const init = { method };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
      init.headers = { "content-type": "application/json" };
    }
    const response = await request(path, init);
    const text = await response.text();
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`${method} ${path}: status ${response.status}, not JSON: ${text.slice(0, 200)}`);
    }
    if (!response.ok) {
      throw new Error(`${method} ${path}: status ${response.status}: ${text.slice(0, 300)}`);
    }
    return parsed;
  }
  const results = async (ids) => (await call("GET", `/results?only=${ids.join(",")}`)).results;
  async function pollUntil(ids, deadline) {
    let stored = await results(ids);
    while (!ids.every((id) => finished(id, stored[id])) && Date.now() < deadline) {
      await sleep(t.pollMs);
      stored = await results(ids);
    }
    const open = ids.filter((id) => !finished(id, stored[id]));
    if (open.length) {
      notes.push(`not finished by the deadline: ${open.join(", ")}`);
    }
    return stored;
  }

  const healthDeadline = Date.now() + t.healthMs;
  for (;;) {
    try {
      await call("GET", "/health");
      break;
    } catch (error) {
      if (Date.now() > healthDeadline) {
        throw new Error(`the probe never answered /health: ${error.message}`);
      }
      await sleep(t.pollMs);
    }
  }
  log("health ok");

  const planResponse = await request(`/plan?iters=${t.planIters}`, { method: "GET" });
  const planText = await planResponse.text();
  const plan = {
    status: planResponse.status,
    survivedCpuBurn: planResponse.ok,
    reading: planResponse.ok
      ? "the Worker ran well past 10 ms of CPU: not the Free plan's limit"
      : "the CPU burn was refused: the Free plan's 10 ms CPU limit is likely",
    body: planText.slice(0, 200),
  };
  log(`plan check: ${plan.reading} (status ${plan.status})`);

  for (const id of INLINE) {
    const body = await call("POST", `/run/${id}?${query()}`);
    if (id !== "M4" && body.recorded !== true) {
      await call("POST", `/record/${id}`, body);
      notes.push(`${id}: stored by the driver (${body.recorded})`);
    }
    log(`${id} done`);
  }

  const backgroundStarted = Date.now();
  for (const id of BACKGROUND) {
    await call("POST", `/run/${id}?${query()}`);
  }
  for (const id of ALARMS) {
    await call("POST", `/run/${id}?${query()}`);
  }
  const alarmsArmed = Date.now();
  log("background loops started and alarms armed");

  await Promise.all(["A", "B"].map((who) => call("POST", `/run/M12?${query({ who })}`)));
  log("M12 done");

  await sleep(Math.max(0, alarmsArmed + t.alarmsFirstReadMs - Date.now()));
  await pollUntil(ALARMS, Date.now() + t.alarmsMs);
  log("alarm cases done");

  const backgroundEnd = backgroundStarted + p.n * p.intervalMs + t.backgroundSlackMs;
  await sleep(Math.max(0, backgroundEnd - Date.now()));
  await pollUntil(BACKGROUND, Date.now() + t.alarmsMs);
  log("background cases done");

  if (p.holdMs > 0) {
    await call("POST", `/run/M10?${query()}`);
    log(`M10 armed: holding ${p.holdMs} ms`);
    await sleep(p.holdMs + t.m10FirstReadMs);
    await pollUntil(["M10"], Date.now() + t.m10GraceMs);
    log("M10 read");
  } else {
    notes.push("M10 skipped (hold 0)");
  }

  const final = await call("GET", "/results");
  return { plan, params: p, notes, results: final.results };
}

/** A short table of the bursts, for the job summary. */
export function summarize(report) {
  const rows = [];
  for (const [id, stored] of Object.entries(report.results)) {
    const parts = id === "M12" ? { A: stored?.A, B: stored?.B } : { "": stored?.result };
    for (const [label, result] of Object.entries(parts)) {
      if (!result) {
        rows.push(`| ${id} | ${label} | (none) | | |`);
        continue;
      }
      const bursts = Object.entries(result).filter(([, v]) => v && typeof v === "object" && "ok" in v && "n" in v);
      if (Array.isArray(result.calls)) {
        bursts.push(["background", { ok: result.ok, n: result.params?.n, firstError: result.firstError,
                                      firstErrorAt: result.firstErrorAt }]);
      }
      if (!bursts.length) {
        const what = result.outcome ?? result.open ?? (result.targets ? `${result.targets.length} targets` : "");
        rows.push(`| ${id} | ${label} | ${result.status} ${what} | | ${result.error ?? ""} |`);
      }
      for (const [kind, b] of bursts) {
        const error = b.firstError ? `${b.firstError.replaceAll("|", "/")} (call ${b.firstErrorAt})` : "";
        rows.push(`| ${id} | ${[label, kind].filter(Boolean).join(" ")} | ${b.ok}/${b.n} | ${result.status} | ${error} |`);
      }
    }
  }
  return [
    `Plan check: ${report.plan.reading} (status ${report.plan.status}).`, "",
    "| case | part | ok | status | first error |", "|---|---|---|---|---|", ...rows, "",
    ...report.notes.map((note) => `- ${note}`),
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

async function main(args) {
  const key = process.env.PROBE_KEY;
  if (!args.base || !key) {
    throw new Error("usage: PROBE_KEY=... drive.mjs --base URL [--out F] [--summary F] [--hold-minutes M]");
  }
  const base = args.base.replace(/\/$/, "");
  const holdMinutes = Number(args["hold-minutes"] ?? 16);
  const request = (path, init) => fetch(`${base}${path}`, {
    ...init, headers: { ...(init.headers ?? {}), "x-probe-key": key },
  });
  const report = await drive({
    request, params: { holdMs: holdMinutes * 60_000 },
    log: (line) => console.log(`[${new Date().toISOString()}] ${line}`),
  });
  const needles = [new URL(base).hostname, ...(args.redact ?? "").split(",")];
  const text = redact(JSON.stringify(report, null, 2), needles);
  if (args.out) {
    writeFileSync(args.out, `${text}\n`);
  }
  if (args.summary) {
    writeFileSync(args.summary, `${redact(summarize(report), needles)}\n`);
  }
  console.log(redact(summarize(report), needles));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = parseArgs(process.argv.slice(2));
  main(args).catch((error) => {
    console.error(`::error::${redact(String(error.message), [args.base, ...(args.redact ?? "").split(",")])}`);
    process.exit(1);
  });
}
