#!/usr/bin/env node
/**
 * The facet probe's billing-side readings (facet-probe.md, Q1, Q5 and Q6): what
 * Cloudflare's GraphQL Analytics API recorded over the run. Optional: it needs Account ->
 * Account Analytics: Read on the token, and a refusal is reported, not fatal.
 *
 * - Requests, active time, CPU and the exceeded-limit errors per object, kept to the run's
 *   own object ids: Q6's billing reading (are parent -> facet calls billed as requests, on
 *   the parent's id or the facet's own?) and Q4's cross-check.
 * - Every Durable Object storage dataset the schema lists (found by introspection, since
 *   their names and dimensions are not documented alike), queried with every dimension and
 *   metric it has, when it has an object or a namespace dimension; rows are kept to the
 *   run's own objects and namespaces. A dataset with neither is account-wide and is never
 *   queried: the repository and its job summaries are public. Q1 (what storage the API
 *   counts per object). Q5 is read from `databaseSize` only: the run's own facet delete and
 *   the cleanup's namespace deletes land in the same hourly or daily rows.
 *
 *   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... node analytics.mjs --results F \
 *       [--summary F] [--namespaces F] [--wait-ms 300000] [--redact <text>]
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { readings, redact, summarize } from "./drive.mjs";

const ENDPOINT = "https://api.cloudflare.com/client/v4/graphql";
export const PER_OBJECT = Object.freeze({
  requests: ["durableObjectsInvocationsAdaptiveGroups", "sum { requests }"],
  duration: ["durableObjectsPeriodicGroups", "sum { activeTime cpuTime }"],
  errors: ["durableObjectsPeriodicGroups", "sum { exceededCpuErrors exceededMemoryErrors }"],
});
/** Time filters, most precise first; a storage dataset takes the first it has. */
const TIME_FILTERS = ["datetimeMinute", "datetimeFiveMinutes", "datetimeHour", "datetime", "date"];

/** The run's objects, by what they are: Q6's parent, facets and store, and Q1's parents. */
export function objects(report) {
  const out = {};
  const r = report.results ?? {};
  for (const source of ["exports", "loader"]) {
    const q6 = r[`Q6 ${source}`];
    if (q6) {
      out[q6.parentId] = `Q6 ${source}: parent (1 driver call)`;
      if (q6.ids?.ownId) {
        out[q6.ids.ownId] = `Q6 ${source}: facet with its own id`;
      }
      if (q6.ids?.store && !(q6.ids.store in out)) {
        out[q6.ids.store] = `Q6 ${source}: top-level ThreadStore`;
      }
    }
  }
  for (const key of ["Q1 bc", "Q1 a"]) {
    if (r[key]?.id) {
      out[r[key].id] = `${key}: parent`;
    }
  }
  return out;
}

function perObjectQuery(dataset, fields) {
  return `query ($accountTag: string!, $start: Time!, $end: Time!) {
  viewer { accounts(filter: { accountTag: $accountTag }) {
    rows: ${dataset}(limit: 10000, filter: { datetimeMinute_geq: $start, datetimeMinute_leq: $end }) {
      dimensions { objectId }
      ${fields}
    }
  } }
}`;
}

const TYPE = `name kind ofType { name kind ofType { name kind ofType { name kind } } }`;
/** The named type under any list and non-null wrappers. */
const named = (type) => (type?.name ? type : type?.ofType ? named(type.ofType) : null);

/**
 * Every Durable Object storage dataset of the account type: its dimension and metric
 * fields and its time filter, by introspection. `gql(query, variables)` answers like the API.
 */
export async function storageDatasets(gql) {
  const typeOf = async (name, part = `fields { name type { ${TYPE} } }`) =>
    (await gql(`query ($name: String!) { __type(name: $name) { ${part} } }`, { name })).__type;
  const account = await typeOf("account", `fields { name type { ${TYPE} } args { name type { ${TYPE} } } }`);
  const out = [];
  for (const field of account?.fields ?? []) {
    if (!/^durableObjects/i.test(field.name) || !/storage/i.test(field.name)) {
      continue;
    }
    const groups = await typeOf(named(field.type).name);
    const scalarsOf = async (sub) => {
      const f = groups?.fields?.find((x) => x.name === sub);
      if (!f) {
        return [];
      }
      const t = await typeOf(named(f.type).name);
      return (t?.fields ?? []).filter((x) => ["SCALAR", "ENUM"].includes(named(x.type)?.kind)).map((x) => x.name);
    };
    const dimensions = await scalarsOf("dimensions");
    const metrics = {};
    for (const sub of ["max", "sum", "avg", "min"]) {
      const names = await scalarsOf(sub);
      if (names.length) {
        metrics[sub] = names;
      }
    }
    const filterArg = field.args?.find((arg) => arg.name === "filter");
    const filter = filterArg ? await typeOf(named(filterArg.type).name, "inputFields { name }") : null;
    const inputs = new Set((filter?.inputFields ?? []).map((x) => x.name));
    const time = TIME_FILTERS.find((t) => inputs.has(`${t}_geq`) && inputs.has(`${t}_leq`)) ?? null;
    out.push({ dataset: field.name, dimensions, metrics, time });
  }
  return out;
}

const dateOf = (iso) => iso.slice(0, 10);
const asTime = (time, iso) => (time === "date" ? dateOf(iso) : iso);

/**
 * One storage dataset over the window, kept to the run's own objects or namespaces. A
 * dataset with neither dimension is account-wide: it is not queried at all.
 */
export async function readStorage(gql, set, { account, start, end, ids, namespaces }) {
  const byObject = set.dimensions.includes("objectId");
  const byNamespace = set.dimensions.includes("namespaceId");
  if (!byObject && !byNamespace) {
    return { dataset: set.dataset, scope: "account-wide: not read (the job summary is public)" };
  }
  if (!set.time) {
    return { dataset: set.dataset, error: "no time filter the probe knows" };
  }
  const metricText = Object.entries(set.metrics).map(([sub, names]) => `${sub} { ${names.join(" ")} }`).join(" ");
  const kind = set.time === "date" ? "Date" : "Time";
  const query = `query ($accountTag: string!, $start: ${kind}!, $end: ${kind}!) {
  viewer { accounts(filter: { accountTag: $accountTag }) {
    rows: ${set.dataset}(limit: 10000, filter: { ${set.time}_geq: $start, ${set.time}_leq: $end }) {
      dimensions { ${set.dimensions.join(" ")} }
      ${metricText}
    }
  } }
}`;
  const data = await gql(query, { accountTag: account, start: asTime(set.time, start), end: asTime(set.time, end) });
  const rows = data?.viewer?.accounts?.[0]?.rows ?? [];
  const kept = rows.filter((row) => (byObject ? row.dimensions.objectId in ids : namespaces.has(row.dimensions.namespaceId)))
    .map((row) => ({ ...row, case: byObject ? ids[row.dimensions.objectId] : namespaces.get(row.dimensions.namespaceId) }));
  return { dataset: set.dataset, scope: byObject ? "per object" : "per namespace", rows: kept };
}

/** Runs every query for the window. Error text is redacted, since it lands in the job summary. */
export async function collect({ token, account, start, end, ids, namespaces = new Map(), fetcher = fetch, needles = [] }) {
  const clean = (text) => redact(String(text), [account, token, ...needles]).slice(0, 300);
  const gql = async (query, variables) => {
    const response = await fetcher(ENDPOINT, {
      method: "POST", signal: AbortSignal.timeout(60_000),
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
    const body = await response.json();
    if (!response.ok || body.errors?.length) {
      throw new Error(`HTTP ${response.status}: ${(body.errors ?? []).map((e) => e.message).join("; ")}`);
    }
    return body.data;
  };
  const byObject = Object.fromEntries(Object.keys(ids).map((id) => [id, { case: ids[id] }]));
  const errors = {};
  for (const [name, [dataset, fields]] of Object.entries(PER_OBJECT)) {
    try {
      const data = await gql(perObjectQuery(dataset, fields), { accountTag: account, start, end });
      for (const row of data?.viewer?.accounts?.[0]?.rows ?? []) {
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
  const storage = [];
  try {
    for (const set of await storageDatasets(gql)) {
      try {
        storage.push(await readStorage(gql, set, { account, start, end, ids, namespaces }));
      } catch (error) {
        storage.push({ dataset: set.dataset, error: clean(error.message) });
      }
    }
  } catch (error) {
    errors.storage = clean(error.message);
  }
  return { start, end, byObject, storage, errors };
}

/** Q6's billing reading: did the parent's facet calls show up as requests anywhere? */
export function billing(analytics, report) {
  const q6 = report.results?.["Q6 exports"];
  if (!q6) {
    return "not read (no Q6 on the control)";
  }
  const at = (id) => analytics.byObject[id]?.requests;
  const parent = at(q6.parentId);
  const own = at(q6.ids?.ownId);
  const store = at(q6.ids?.store);
  if (parent === undefined && own === undefined) {
    return "not read (no request rows for Q6's objects)";
  }
  const calls = q6.n + 20;
  const parts = [`parent ${parent ?? 0} requests for 1 driver call`, `own-id facet ${own ?? 0}`,
                 `ThreadStore ${store ?? 0} for its ${calls} calls`];
  const billed = (parent ?? 0) > calls / 2 || (own ?? 0) > calls / 2;
  return `${billed ? "facet calls are billed as requests" : "facet calls are not billed as requests"} (${parts.join("; ")})`;
}

/** The analytics section for the job summary. */
export function table(analytics, report) {
  const fields = ["requests", "activeTime", "cpuTime", "exceededCpuErrors", "exceededMemoryErrors"];
  const rows = Object.values(analytics.byObject).map((entry) =>
    `| ${entry.case} | ${fields.map((field) => entry[field] ?? "").join(" | ")} |`);
  const storage = analytics.storage.map((set) => `- **${set.dataset}** (${set.scope ?? "not read"}): `
    + `\`${JSON.stringify(set.error ?? set.rows ?? null).slice(0, 1500)}\``);
  return [
    "", "#### Analytics (live only; Account Analytics: Read)", "",
    `Window ${analytics.start} to ${analytics.end}. activeTime and cpuTime are as the API reports them (microseconds).`, "",
    `- **Q6 billing:** ${billing(analytics, report)}`, "",
    `| object | ${fields.join(" | ")} |`, `|---|${fields.map(() => "---").join("|")}|`, ...rows, "",
    "**Storage datasets (Q1; the run's own objects and namespaces only):**", ...(storage.length ? storage : ["- none listed"]), "",
    ...Object.entries(analytics.errors).map(([name, error]) => `- ${name} not read: ${error}`),
  ].join("\n");
}

async function main(args) {
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const account = process.env.CLOUDFLARE_ACCOUNT_ID;
  if (!token || !account || !args.results) {
    throw new Error("usage: CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... analytics.mjs --results F [--summary F]");
  }
  const needles = [token, account, ...(args.redact ?? "").split(",").filter(Boolean)];
  const report = JSON.parse(readFileSync(args.results, "utf8"));
  const namespaces = new Map();
  if (args.namespaces && existsSync(args.namespaces)) {
    for (const row of JSON.parse(readFileSync(args.namespaces, "utf8"))) {
      namespaces.set(row.id, `${row.script}/${row.class}`);
    }
  }
  // The datasets lag the events by a few minutes.
  await new Promise((resolve) => setTimeout(resolve, Number(args["wait-ms"] ?? 300_000)));
  const minute = (ms) => new Date(Math.floor(ms / 60_000) * 60_000).toISOString();
  const analytics = await collect({ token, account, ids: objects(report), namespaces, needles,
                                    start: minute(report.startedAt ?? Date.now() - 4 * 3_600_000),
                                    end: minute(Date.now()) });
  report.analytics = analytics;
  report.readings = readings(report);
  report.readings.Q6billing = billing(analytics, report);
  writeFileSync(args.results, `${redact(JSON.stringify(report, null, 2), needles)}\n`);
  const text = table(analytics, report);
  if (args.summary) {
    writeFileSync(args.summary, `${redact(`${summarize(report)}\n${text}`, needles)}\n`);
  }
  console.log(redact(text, needles));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = {};
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 2) {
    args[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  main(args).catch((error) => {
    console.error(`::warning::analytics not read: ${redact(error.message, [process.env.CLOUDFLARE_API_TOKEN, process.env.CLOUDFLARE_ACCOUNT_ID])}`);
  });
}
