#!/usr/bin/env node
/**
 * The store probe's billing-side readings (store-probe.md, P7 and the P1 cross-check): what
 * Cloudflare's GraphQL Analytics API recorded for each probe object over the run. Optional:
 * it needs Account -> Account Analytics: Read on the token, and a refusal is reported, not
 * fatal. Each dataset is its own query, so a field the API does not know fails only its
 * own reading. Rows are filtered here to the run's own object ids. It then reads every
 * probe again with the analytics in hand (P4's reset is memory only when the API counted a
 * memory error on its owner) and rewrites the summary.
 *
 *   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... node analytics.mjs \
 *       --results results.json --summary summary.md [--wait-ms 180000] [--redact <text>]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { readings, redact, summarize } from "./drive.mjs";

const ENDPOINT = "https://api.cloudflare.com/client/v4/graphql";
/** One query per dataset and field set; a refusal of one leaves the others. */
export const QUERIES = Object.freeze({
  requests: ["durableObjectsInvocationsAdaptiveGroups", "sum { requests }"],
  duration: ["durableObjectsPeriodicGroups", "sum { activeTime cpuTime }"],
  subrequests: ["durableObjectsPeriodicGroups", "sum { subrequests }"],
  memory: ["durableObjectsPeriodicGroups", "sum { exceededMemoryErrors }"],
  cpu: ["durableObjectsPeriodicGroups", "sum { exceededCpuErrors }"],
});

/** The run's objects, by case: every owner record's `ownerId`, and P7's two stores. */
export function objects(report) {
  const out = {};
  for (const [key, stored] of Object.entries(report.results ?? {})) {
    const id = Object.values(stored ?? {}).find((value) => value?.ownerId)?.ownerId;
    if (id) {
      out[id] = `${key} (owner)`;
    }
  }
  const ids = report.results?.P7?.result?.ids;
  if (ids) {
    out[ids.calls] = "P7 store, per-call stub";
    out[ids.session] = "P7 store, held session";
  }
  return out;
}

function query(dataset, fields) {
  return `query ($accountTag: string!, $start: Time!, $end: Time!) {
  viewer { accounts(filter: { accountTag: $accountTag }) {
    rows: ${dataset}(limit: 10000, filter: { datetimeMinute_geq: $start, datetimeMinute_leq: $end }) {
      dimensions { objectId }
      ${fields}
    }
  } }
}`;
}

/**
 * Runs every query for the window and keeps the rows of `ids`. Error text is redacted of
 * the account id and each of `needles`, since it lands in the job summary.
 */
export async function collect({ token, account, start, end, ids, fetcher = fetch, needles = [] }) {
  const clean = (text) => redact(String(text), [account, ...needles]).slice(0, 300);
  const byObject = Object.fromEntries(Object.keys(ids).map((id) => [id, { case: ids[id] }]));
  const errors = {};
  for (const [name, [dataset, fields]] of Object.entries(QUERIES)) {
    try {
      const response = await fetcher(ENDPOINT, {
        method: "POST",
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: JSON.stringify({ query: query(dataset, fields),
                               variables: { accountTag: account, start, end } }),
        signal: AbortSignal.timeout(60_000),
      });
      const body = await response.json();
      if (!response.ok || body.errors?.length) {
        errors[name] = clean(`HTTP ${response.status}: ${(body.errors ?? []).map((e) => e.message).join("; ")}`);
        continue;
      }
      for (const row of body.data?.viewer?.accounts?.[0]?.rows ?? []) {
        const entry = byObject[row.dimensions?.objectId];
        if (entry) {
          for (const [field, value] of Object.entries(row.sum ?? {})) {
            entry[field] = (entry[field] ?? 0) + value;
          }
        }
      }
    } catch (error) {
      errors[name] = clean(error.message);
    }
  }
  return { start, end, byObject, errors };
}

/** The analytics table for the job summary. */
export function table(analytics) {
  const fields = ["requests", "activeTime", "cpuTime", "subrequests", "exceededMemoryErrors", "exceededCpuErrors"];
  const rows = Object.values(analytics.byObject).map((entry) =>
    `| ${entry.case} | ${fields.map((field) => entry[field] ?? "").join(" | ")} |`);
  return [
    "", "#### Analytics (live only; Account Analytics: Read)", "",
    `Window ${analytics.start} to ${analytics.end}. activeTime and cpuTime are as the API reports them (microseconds).`, "",
    `| object | ${fields.join(" | ")} |`, `|---|${fields.map(() => "---").join("|")}|`, ...rows, "",
    ...Object.entries(analytics.errors).map(([name, error]) => `- ${name} not read: ${error}`),
  ].join("\n");
}

async function main(args) {
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const account = process.env.CLOUDFLARE_ACCOUNT_ID;
  if (!token || !account || !args.results) {
    throw new Error("usage: CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... analytics.mjs --results F [--summary F]");
  }
  const needles = (args.redact ?? "").split(",").filter(Boolean);
  const report = JSON.parse(readFileSync(args.results, "utf8"));
  // The datasets lag the events by a few minutes.
  await new Promise((resolve) => setTimeout(resolve, Number(args["wait-ms"] ?? 180_000)));
  const minute = (ms) => new Date(Math.floor(ms / 60_000) * 60_000).toISOString();
  const analytics = await collect({ token, account, ids: objects(report), needles,
                                    start: minute(report.startedAt ?? Date.now() - 3 * 3_600_000),
                                    end: minute(Date.now()) });
  report.analytics = analytics;
  report.readings = readings(report);
  writeFileSync(args.results, `${redact(JSON.stringify(report, null, 2), [account, ...needles])}\n`);
  const text = table(analytics);
  if (args.summary) {
    writeFileSync(args.summary, `${redact(`${summarize(report)}\n${text}`, [account, ...needles])}\n`);
  }
  console.log(text);
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
