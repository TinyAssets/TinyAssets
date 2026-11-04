#!/usr/bin/env node
/**
 * Production's own figures for the RPC retention probe (rpcretain-probe.yml), from the GraphQL
 * Analytics API. Needs Account -> Account Analytics: Read; a refusal is reported, not fatal.
 *
 * The decisive figures are the Durable Object datasets, per object: the work and any reset
 * happen in the Prober object, and the fetch handler catches the object's error and answers
 * 200. So it reads, filtered to the Prober namespace, each object's invocations and their
 * status, its exceededMemoryErrors per minute, and its periodic memory. drive.mjs's key
 * counts a failure as memory when the object was charged a memory error in a minute the call
 * spanned. The Worker-level workersInvocationsAdaptive figures are secondary: they mix the
 * fetch handler with SELF's Store invocations.
 *
 * Each reading is its own query, so a field the API does not know fails only that reading.
 * The memory fields are found by introspection, with the documented names as the fallback.
 * The datasets lag, so it waits, then polls until the Durable Object invocations cover every
 * call's object.
 *
 *   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... [PROBE_NAMESPACE_ID=...] node analytics.mjs \
 *       --results results.json --summary summary.md [--wait-ms 180000] [--max-wait-ms 600000] [--redact a,b]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { memoryMarks, readings, redact, summarize } from "./drive.mjs";
import { SCRIPT } from "./config.mjs";

const ENDPOINT = "https://api.cloudflare.com/client/v4/graphql";
const SELECTIONS = { Quantiles: "quantiles", Max: "max", Sum: "sum", Avg: "avg", Min: "min" };
const DATASETS = Object.freeze({ workers: "WorkersInvocationsAdaptive", periodic: "DurableObjectsPeriodicGroups" });
/** The documented names (changelog 2026-06-30; DO metrics), if introspection finds none. */
export const FALLBACK = Object.freeze({
  workers: "quantiles { memoryUsageBytesP50 memoryUsageBytesP90 memoryUsageBytesP99 memoryUsageBytesP999 }",
  periodic: "max { memoryUsageBytes }",
});
/** The reading the wait keys on: one Prober invocation per call. */
export const DO_COUNT = "DO invocations by object";
/** Readings from the Worker-level dataset: context, not evidence. */
export const SECONDARY = "secondary (Worker level): ";

/** Memory fields per dataset, as `<selection> { <fields> }`, from an introspected type list. */
export function memorySelections(types) {
  const out = {};
  for (const [key, dataset] of Object.entries(DATASETS)) {
    const parts = [];
    for (const type of types ?? []) {
      const match = new RegExp(`${dataset}(${Object.keys(SELECTIONS).join("|")})$`).exec(type.name ?? "");
      const fields = (type.fields ?? []).map((f) => f.name).filter((name) => /memory/i.test(name));
      if (match && fields.length) {
        parts.push(`${SELECTIONS[match[1]]} { ${[...new Set(fields)].join(" ")} }`);
      }
    }
    out[key] = [...new Set(parts)].join(" ");
  }
  return out;
}

const workersQuery = (body) => `query ($accountTag: string!, $script: string!, $start: Time!, $end: Time!) {
  viewer { accounts(filter: { accountTag: $accountTag }) {
    rows: workersInvocationsAdaptive(limit: 10000, filter: { scriptName: $script, datetime_geq: $start, datetime_leq: $end }) {
      ${body}
    }
  } }
}`;

const doQuery = (dataset, body) => `query ($accountTag: string!, $namespace: string!, $start: Time!, $end: Time!) {
  viewer { accounts(filter: { accountTag: $accountTag }) {
    rows: ${dataset}(limit: 10000, filter: { namespaceId: $namespace, datetimeMinute_geq: $start, datetimeMinute_leq: $end }) {
      ${body}
    }
  } }
}`;

/** Every reading's query, the decisive Durable Object ones first (they need the namespace id). */
export function queries(memory, namespace) {
  const workers = memory.workers || FALLBACK.workers;
  const periodic = memory.periodic || FALLBACK.periodic;
  const out = {};
  if (namespace) {
    out[DO_COUNT] = doQuery("durableObjectsInvocationsAdaptiveGroups", "dimensions { objectId } sum { requests }");
    out["DO invocations by object and status"] =
      doQuery("durableObjectsInvocationsAdaptiveGroups", "dimensions { objectId status } sum { requests }");
    out["DO memory errors by object and minute"] =
      doQuery("durableObjectsPeriodicGroups", "dimensions { objectId datetimeMinute } sum { exceededMemoryErrors exceededCpuErrors activeTime cpuTime }");
    out["DO periodic memory by object and minute"] =
      doQuery("durableObjectsPeriodicGroups", `dimensions { objectId datetimeMinute } ${periodic}`);
  }
  out[`${SECONDARY}invocations by status`] = workersQuery("dimensions { status } sum { requests errors }");
  out[`${SECONDARY}invocation memory by status`] = workersQuery(`dimensions { status } ${workers}`);
  out[`${SECONDARY}invocation memory by minute`] =
    workersQuery(`dimensions { datetimeMinute status } sum { requests } ${workers}`);
  return out;
}

async function post(token, query, variables, fetcher) {
  const response = await fetcher(ENDPOINT, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ query, variables }),
    signal: AbortSignal.timeout(60_000),
  });
  const body = await response.json();
  if (!response.ok || body.errors?.length) {
    throw new Error(`HTTP ${response.status}: ${(body.errors ?? []).map((e) => e.message).join("; ")}`);
  }
  return body.data;
}

export async function collect({ token, account, namespace, start, end, fetcher = fetch, needles = [] }) {
  const clean = (text) => redact(String(text), [account, namespace, ...needles]).slice(0, 300);
  const out = { start, end, namespaceRead: Boolean(namespace), readings: {}, errors: {} };
  let memory = {};
  try {
    const data = await post(token, "{ __schema { types { name fields { name } } } }", {}, fetcher);
    memory = memorySelections(data.__schema?.types);
    out.introspected = memory;
  } catch (error) {
    out.errors.introspection = clean(error.message);
  }
  for (const [name, query] of Object.entries(queries(memory, namespace))) {
    try {
      const data = await post(token, query, { accountTag: account, script: SCRIPT, namespace, start, end }, fetcher);
      out.readings[name] = data?.viewer?.accounts?.[0]?.rows ?? [];
    } catch (error) {
      out.errors[name] = clean(error.message);
    }
  }
  return out;
}

/**
 * How far the Durable Object invocations have caught up: of the answers' objects, how many
 * the API has counted an invocation for (or, with no object ids, the summed requests against
 * the answers). The Worker-level count is not used: SELF's Store calls inflate it.
 */
export function progress(analytics, answers) {
  const rows = analytics.readings[DO_COUNT] ?? [];
  const ids = new Set(answers.map((a) => a.objectId).filter(Boolean));
  if (ids.size) {
    const seen = new Set(rows.filter((r) => (r.sum?.requests ?? 0) > 0).map((r) => r.dimensions?.objectId));
    return { counted: [...ids].filter((id) => seen.has(id)).length, target: ids.size };
  }
  return { counted: rows.reduce((n, r) => n + (r.sum?.requests ?? 0), 0), target: answers.length };
}

function flatten(row, prefix = "", out = {}) {
  for (const [key, value] of Object.entries(row ?? {})) {
    if (value && typeof value === "object") {
      flatten(value, `${prefix}${key}.`, out);
    } else {
      out[`${prefix}${key}`] = value;
    }
  }
  return out;
}

const show = (key, value) => (typeof value === "number" && /memory/i.test(key)
  ? `${value} (${(value / 1048576).toFixed(1)} MiB)` : String(value ?? ""));

/** One markdown table per reading; an object id is shown as the call it served. */
export function table(analytics, answers = []) {
  const calls = new Map(answers.filter((a) => a.objectId).map((a) => [a.objectId, `${a.form} ${a.mib} MiB run ${a.run}`]));
  const lines = ["", "#### Production analytics (GraphQL; Account Analytics: Read)", "",
                 `Window ${analytics.start} to ${analytics.end}. Memory is bytes, with MiB in brackets. `
                 + "The Durable Object readings are the evidence; the Worker-level ones are secondary context "
                 + "(they mix the fetch handler, which answers 200 for an object's reset, with SELF's Store calls)."];
  if (!analytics.namespaceRead) {
    lines.push("", "- The Durable Object namespace id was not read, so the decisive DO readings were not queried.");
  }
  if (analytics.progress) {
    lines.push("", `- Durable Object invocations counted: ${analytics.progress.counted} of ${analytics.progress.target}.`);
  }
  if (analytics.introspected) {
    lines.push("", `- Memory fields found: workers \`${analytics.introspected.workers || "none"}\`; `
               + `DO periodic \`${analytics.introspected.periodic || "none"}\` (none means the documented names were tried).`);
  }
  for (const [name, rows] of Object.entries(analytics.readings)) {
    const flat = rows.map((row) => {
      const out = flatten(row);
      if ("dimensions.objectId" in out) {
        out["dimensions.objectId"] = calls.get(out["dimensions.objectId"]) ?? "another object";
      }
      return out;
    });
    const keys = [...new Set(flat.flatMap((row) => Object.keys(row)))].sort();
    lines.push("", `##### ${name}`, "");
    if (!flat.length) {
      lines.push("No rows.");
      continue;
    }
    lines.push(`| ${keys.join(" | ")} |`, `|${keys.map(() => "---").join("|")}|`,
               ...flat.map((row) => `| ${keys.map((key) => show(key, row[key])).join(" | ")} |`));
  }
  lines.push("", ...Object.entries(analytics.errors).map(([name, error]) => `- ${name} not read: ${error}`));
  return lines.join("\n");
}

async function main(args, needles) {
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const account = process.env.CLOUDFLARE_ACCOUNT_ID;
  const namespace = process.env.PROBE_NAMESPACE_ID || undefined;
  if (!token || !account || !args.results) {
    throw new Error("usage: CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... analytics.mjs --results F [--summary F]");
  }
  const report = JSON.parse(readFileSync(args.results, "utf8"));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const minute = (ms) => new Date(Math.floor(ms / 60_000) * 60_000).toISOString();
  const start = minute((report.startedAt ?? Date.now() - 3_600_000) - 60_000);
  const deadline = Date.now() + Number(args["max-wait-ms"] ?? 600_000);
  await sleep(Number(args["wait-ms"] ?? 180_000));
  let analytics;
  for (;;) {
    analytics = await collect({ token, account, namespace, needles, start, end: minute(Date.now() + 60_000) });
    analytics.progress = progress(analytics, report.answers);
    // Without the namespace there is no DO count to wait on.
    if (!namespace || analytics.progress.counted >= analytics.progress.target || Date.now() + 60_000 > deadline) {
      break;
    }
    console.log(`analytics: ${analytics.progress.counted} of ${analytics.progress.target} Durable Object invocations counted; waiting`);
    await sleep(60_000);
  }
  report.analytics = analytics;
  report.readings = readings(report.answers, memoryMarks(analytics));
  const all = [account, namespace, ...needles];
  writeFileSync(args.results, `${redact(JSON.stringify(report, null, 2), all)}\n`);
  const text = table(analytics, report.answers);
  if (args.summary) {
    writeFileSync(args.summary, `${redact(`${summarize(report)}\n${text}`, all)}\n`);
  }
  console.log(redact(text, all));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = {};
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 2) {
    args[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  const needles = [process.env.CLOUDFLARE_ACCOUNT_ID, process.env.PROBE_NAMESPACE_ID,
                   ...(args.redact ?? "").split(",")].filter(Boolean);
  main(args, needles).catch((error) => {
    console.error(`::warning::analytics not read: ${redact(String(error?.message ?? error), needles)}`);
  });
}
