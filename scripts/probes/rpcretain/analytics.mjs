#!/usr/bin/env node
/**
 * Production's own figures for the RPC retention probe (rpcretain-probe.yml), from the GraphQL
 * Analytics API: the Worker's invocations by status (exceededMemory among them) and their V8
 * isolate memory quantiles, and the Durable Object namespace's periodic memory and memory
 * errors. Needs Account -> Account Analytics: Read; a refusal is reported, not fatal. Each
 * reading is its own query, so a field the API does not know fails only that reading. The
 * memory fields are found by introspection, with the documented names as the fallback.
 * The datasets lag, so it waits, then polls until the invocations cover every call.
 *
 *   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... [PROBE_NAMESPACE_ID=...] node analytics.mjs \
 *       --results results.json --summary summary.md [--wait-ms 180000] [--max-wait-ms 600000] [--redact a,b]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { redact, summarize } from "./drive.mjs";
import { SCRIPT } from "./config.mjs";

const ENDPOINT = "https://api.cloudflare.com/client/v4/graphql";
const SELECTIONS = { Quantiles: "quantiles", Max: "max", Sum: "sum", Avg: "avg", Min: "min" };
const DATASETS = Object.freeze({ workers: "WorkersInvocationsAdaptive", periodic: "DurableObjectsPeriodicGroups" });
/** The documented names (changelog 2026-06-30; DO metrics), if introspection finds none. */
export const FALLBACK = Object.freeze({
  workers: "quantiles { memoryUsageBytesP50 memoryUsageBytesP90 memoryUsageBytesP99 memoryUsageBytesP999 }",
  periodic: "max { memoryUsageBytes }",
});

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

/** Every reading's query; the DO ones only with the namespace id. */
export function queries(memory, namespace) {
  const workers = memory.workers || FALLBACK.workers;
  const periodic = memory.periodic || FALLBACK.periodic;
  const out = {
    "invocations by status": workersQuery("dimensions { status } sum { requests errors }"),
    "invocation memory by status": workersQuery(`dimensions { status } ${workers}`),
    "invocation memory by minute": workersQuery(`dimensions { datetimeMinute status } sum { requests } ${workers}`),
  };
  if (namespace) {
    out["DO memory errors"] = doQuery("durableObjectsPeriodicGroups", "sum { exceededMemoryErrors activeTime cpuTime }");
    out["DO periodic memory by minute"] = doQuery("durableObjectsPeriodicGroups", `dimensions { datetimeMinute } ${periodic}`);
    out["DO invocations by status"] = doQuery("durableObjectsInvocationsAdaptiveGroups", "dimensions { status } sum { requests }");
  }
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

/** The invocations the API has counted so far, to know when it has caught up. */
export function counted(analytics) {
  return (analytics.readings["invocations by status"] ?? []).reduce((n, row) => n + (row.sum?.requests ?? 0), 0);
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

/** One markdown table per reading. */
export function table(analytics) {
  const lines = ["", "#### Production analytics (GraphQL; Account Analytics: Read)", "",
                 `Window ${analytics.start} to ${analytics.end}. Memory is bytes, with MiB in brackets.`];
  if (!analytics.namespaceRead) {
    lines.push("", "- The Durable Object namespace id was not read, so the DO readings were not queried.");
  }
  if (analytics.introspected) {
    lines.push("", `- Memory fields found: workers \`${analytics.introspected.workers || "none"}\`; `
               + `DO periodic \`${analytics.introspected.periodic || "none"}\` (none means the documented names were tried).`);
  }
  for (const [name, rows] of Object.entries(analytics.readings)) {
    const flat = rows.map((row) => flatten(row));
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

async function main(args) {
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const account = process.env.CLOUDFLARE_ACCOUNT_ID;
  const namespace = process.env.PROBE_NAMESPACE_ID || undefined;
  if (!token || !account || !args.results) {
    throw new Error("usage: CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... analytics.mjs --results F [--summary F]");
  }
  const needles = (args.redact ?? "").split(",").filter(Boolean);
  const report = JSON.parse(readFileSync(args.results, "utf8"));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const minute = (ms) => new Date(Math.floor(ms / 60_000) * 60_000).toISOString();
  const start = minute((report.startedAt ?? Date.now() - 3_600_000) - 60_000);
  const deadline = Date.now() + Number(args["max-wait-ms"] ?? 600_000);
  await sleep(Number(args["wait-ms"] ?? 180_000));
  let analytics;
  for (;;) {
    analytics = await collect({ token, account, namespace, needles, start, end: minute(Date.now() + 60_000) });
    if (counted(analytics) >= report.answers.length || Date.now() + 60_000 > deadline) {
      break;
    }
    console.log(`analytics: ${counted(analytics)} of ${report.answers.length} invocations counted; waiting`);
    await sleep(60_000);
  }
  analytics.invocationsCounted = counted(analytics);
  report.analytics = analytics;
  const all = [account, namespace, ...needles];
  writeFileSync(args.results, `${redact(JSON.stringify(report, null, 2), all)}\n`);
  const text = table(analytics);
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
  main(args).catch((error) => {
    console.error(`::warning::analytics not read: ${error.message}`);
  });
}
