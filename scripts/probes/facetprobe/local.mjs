#!/usr/bin/env node
/**
 * The facet probe's harness check, in miniflare: config.mjs writes every config, wrangler
 * bundles each exactly as a deploy does (`deploy --dry-run`), and miniflare runs the
 * bundles. `drive` then runs every question with small sizes and short waits through the
 * door's local-only fetch, which starts the same `Drive` Workflow the live run starts
 * through the API, and this checks that each question finished and reads as workerd
 * says it should. The live transport (`workflowCall`) and the reading key are checked on
 * stand-ins.
 *
 * It proves the harness, never a reading: workerd has no 10 GB limit (Q1 here writes
 * megabytes and reads "separate" against a 100 MB stand-in limit), no CPU or memory limit
 * (Q4's over-CPU case completes, and the out-of-memory case is not run), no colo, no
 * billing or analytics (Q6's billing), and it is not production (every deploy reading in
 * Q2). facet-probe.yml runs it before deploying. Needs `npm ci` in `platform/`.
 *
 *   node scripts/probes/facetprobe/local.mjs [--out results.json]
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

import { billing, collect, objects, table } from "./analytics.mjs";
import { BUILD, copyConfigs, PLATFORM, readCommitted, writeConfigs } from "./config.mjs";
import { drive, q1Reading, q7Gate, readings, summarize, workflowCall } from "./drive.mjs";
import { COPIES, DOOR, DUMMY } from "./shape.mjs";

const fromPlatform = createRequire(join(PLATFORM, "package.json"));
const load = async (name) => import(pathToFileURL(fromPlatform.resolve(name)).href);
const { unstable_getMiniflareWorkerOptions: workerOptions } = await load("wrangler");
const { Miniflare, convertV4MiniflareOptions } = await load("miniflare");
const WRANGLER = join(dirname(fromPlatform.resolve("wrangler/package.json")), "bin", "wrangler.js");

const KEY = "local-key";
const MB = 1_000_000;
const PARAMS = Object.freeze({
  fillMinutes: 2, partBytes: 40 * MB, capBytes: 60 * MB, limitBytes: 100 * MB, q6N: 100,
  q4: { yieldMs: 1500, solidMs: 500, overCpuMs: 1500, cases: ["yield", "solid", "throw", "overcpu"] },
});
const TIMING = Object.freeze({ instancePollMs: 50, callTimeoutMs: 120_000, fillPollMs: 1000, fillGraceMs: 30_000 });
/** Copies miniflare runs. The unsafe binding is production's to judge: workerd has no such type. */
const RUN = ["main", "exports", "classbind", "agents", "agents-exports"];

const problems = [];
const expect = (ok, what) => {
  if (!ok) {
    problems.push(what);
  }
};
const show = (value) => JSON.stringify(value)?.slice(0, 500);

/** wrangler's own bundle of a config, as `deploy` would upload it. Throws if wrangler refuses. */
function bundle(config, outDir) {
  execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir", outDir, "--config", config],
               { cwd: PLATFORM, stdio: "pipe", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
  return outDir;
}

/** A miniflare worker from a config's runtime and bindings, running its bundle. */
function worker(config, root, script, extra = {}) {
  const { workerOptions: options } = workerOptions(config);
  const keep = ["name", "compatibilityDate", "compatibilityFlags", "durableObjects", "additionalUnboundDurableObjects",
                "serviceBindings", "kvNamespaces", "workerLoaders", "workflows", "bindings"];
  const picked = Object.fromEntries(keep.filter((key) => options[key] !== undefined).map((key) => [key, options[key]]));
  // The entry first, then the bundle's other modules (child.txt, as wrangler emits it).
  const others = readdirSync(root).filter((file) => file !== script && /\.(js|txt)$/.test(file));
  const modules = [script, ...others].map((file) => ({
    type: file.endsWith(".txt") ? "Text" : "ESModule", path: join(root, file),
    contents: readFileSync(join(root, file), "utf8") }));
  return { ...picked, name: JSON.parse(readFileSync(config, "utf8")).name, ...extra, bindings: { ...(picked.bindings ?? {}), ...(extra.bindings ?? {}) }, modulesRoot: root,
           modules };
}

/** The trace page stand-in: every colo reads LCL here. */
function outbound(request) {
  if (new URL(request.url).pathname === "/cdn-cgi/trace") {
    return new Response("fl=local\ncolo=LCL\nloc=XX\n");
  }
  return new Response("local stand-in", { status: 502 });
}

/** Every config: the owner's runtime on main, no route and no workers.dev URL anywhere. */
function checkConfigs(paths) {
  const owner = JSON.parse(readFileSync(join(PLATFORM, "owner", "wrangler.jsonc"), "utf8")
    .split("\n").filter((line) => !/^\s*\/\//.test(line)).join("\n"));
  const main = readCommitted("parent");
  for (const field of ["compatibility_date", "compatibility_flags", "workers_dev", "preview_urls", "limits"]) {
    expect(show(main[field]) === show(owner[field]), `parent.wrangler.jsonc's ${field} differs from the owner Worker's`);
  }
  expect(show(main.durable_objects.bindings) === show(owner.durable_objects.bindings.map((b) => ({ ...b }))),
         "the parent binds PISTORE -> ThreadStore exactly as the owner does");
  for (const [name, path] of Object.entries(paths)) {
    const config = JSON.parse(readFileSync(path, "utf8"));
    expect(config.workers_dev === false && config.preview_urls === false && !config.routes && !config.route,
           `${name}: a route or a workers.dev URL`);
    expect(/^ta-facetprobe/.test(config.name), `${name}: named ${config.name}`);
  }
  const copies = copyConfigs();
  expect(!copies.exports.compatibility_flags.includes("disable_ctx_exports")
         && copies.main.compatibility_flags.includes("disable_ctx_exports"), "only the control drops disable_ctx_exports");
  expect(!JSON.stringify(copies.main).includes("CHILD_"), "main binds no facet class");
  const door = JSON.parse(readFileSync(writeConfigs({ outDir: join(tmpdir(), "facetprobe-two"),
                                                       copies: ["main", "exports"] }).door, "utf8"));
  expect(show(door.durable_objects.bindings.map((b) => b.name)) === show(["PARENT_MAIN", "PARENT_EXPORTS"]),
         `the door binds only the copies that deployed: ${show(door.durable_objects)}`);
  let refused = false;
  try {
    writeConfigs({ outDir: join(tmpdir(), "facetprobe-bad"), kv: "not-an-id" });
  } catch {
    refused = true;
  }
  expect(refused, "config.mjs accepted a malformed KV id");
}

/** The live transport on a stand-in API: create, poll, unwrap; an errored instance and a 403 throw. */
async function checkTransport() {
  const seen = [];
  const stand = (script) => async (url, init) => {
    seen.push(`${init?.method ?? "GET"} ${url.replace(/.*\/accounts\/[^/]+/, "")}`);
    return Response.json(script.shift());
  };
  const timing = { instancePollMs: 1, callTimeoutMs: 5000 };
  const ok = workflowCall({ token: "t", account: "acct", runId: "r", timing, fetcher: stand([
    { success: true, result: { id: "i1" } }, { success: true, result: { status: "running" } },
    { success: true, result: { status: "complete", output: { result: { hello: 1 } } } }]) });
  expect(show(await ok({ copy: "main", object: "o", method: "whoami" })) === show({ hello: 1 }), "transport: result");
  expect(seen[0] === `POST /workflows/${DOOR}/instances` && seen[1] === `GET /workflows/${DOOR}/instances/i1`,
         `transport: paths ${show(seen)}`);
  const fails = async (script, pattern, what) => {
    const call = workflowCall({ token: "t", account: "acct", runId: "r", timing, fetcher: stand(script) });
    try {
      await call({ copy: "main", object: "o", method: "whoami" });
      expect(false, `transport: ${what} did not throw`);
    } catch (error) {
      expect(pattern.test(error.message), `transport: ${what}: ${error.message}`);
    }
  };
  await fails([{ success: true, result: { id: "i" } }, { success: true, result: { status: "complete", output: { error: "boom" } } }],
              /^boom$/, "a call's error");
  await fails([{ success: true, result: { id: "i" } }, { success: true, result: { status: "errored", error: { message: "x" } } }],
              /instance errored/, "an errored instance");
  await fails([{ success: false, errors: [{ message: "Authentication error" }] }], /Authentication error/, "a refused token");
}

/** The reading key on synthetic plans: separate, shared, void, and the Q7 gate. */
function checkKey() {
  const GB = 1_000_000_000;
  const plan = (jobs, status = "done") => ({ plan: { status, jobs: jobs.map(([target, size, st, error]) =>
    ({ target, size, status: st, error, rows: 0 })) } });
  const separate = q1Reading(plan([["self", 6 * GB, "reached"], ["q1b", 6 * GB, "reached"], ["q1c", 6 * GB, "reached"]]),
                             plan([["q1a", 10 * GB, "limit", "SQLITE_FULL"]]));
  expect(separate.verdict === "separate" && /stopped at 10.00 GB/.test(separate.facetLimit), `separate: ${show(separate)}`);
  const shared = q1Reading(plan([["self", 6 * GB, "reached"], ["q1b", 4 * GB, "limit", "database or disk is full"],
                                 ["q1c", 0, "limit", "database or disk is full"]]), null);
  expect(shared.verdict === "shared", `shared: ${show(shared)}`);
  const aborted = q1Reading(plan([["self", 2 * GB, "error", "Error: network lost"]], "aborted: a non-limit error"), null);
  expect(aborted.verdict === "void", `void: ${show(aborted)}`);
  expect(!q7Gate({ Q1: shared, Q2: { anyWorks: true } }).run && q7Gate({ Q1: separate, Q2: { anyWorks: true } }).run
         && !q7Gate({ Q1: separate, Q2: { anyWorks: false } }).run, "the Q7 gate");
}

function checkRun(report) {
  const g = report.readings;
  const r = report.results;
  expect(Object.keys(report.driverErrors).length === 0, `driver errors: ${show(report.driverErrors)}`);
  expect(report.phase === "done", `phase ${report.phase}`);
  const main = g.Q2.copies.main.sources;
  expect(/ctx\.exports is undefined/.test(main?.exports ?? "") && main?.loader === "works"
         && /Incorrect type/.test(main?.raw ?? "") && /Incorrect type/.test(main?.pistore ?? ""),
         `Q2 main: ${show(main)}`);
  expect(g.Q2.copies.exports.sources?.exports === "works" && g.Q2.copies.exports.sources?.loader === "works",
         `Q2 exports: ${show(g.Q2.copies.exports)}`);
  expect(g.Q2.copies.main.ctxExports === "undefined" && g.Q2.copies.exports.ctxExports === "object", "Q2: ctx.exports");
  const q3 = g["Q3 exports"];
  expect(show(q3?.facetEnv) === show(["KV", "LOADER", "PISTORE", "SVC"]) && q3.reach.SVC === "reached"
         && q3.reach.fetch === "reached" && q3.reach.PISTORE === "reached", `Q3 exports: ${show(q3)}`);
  expect(show(g["Q3 loader"]?.facetEnv) === show([]), `Q3 loader: ${show(g["Q3 loader"])}`);
  for (const source of ["exports", "loader"]) {
    expect(g[`Q5 ${source}`]?.wiped === true && g[`Q5 ${source}`].fresh === true, `Q5 ${source}: ${show(g[`Q5 ${source}`])}`);
    const q6 = g[`Q6 ${source}`];
    expect(q6?.sharedId?.n === PARAMS.q6N && q6.store?.n === PARAMS.q6N && q6.sharedId.errors === 0,
           `Q6 ${source}: ${show(q6)}`);
  }
  expect(g["Q6 exports"]?.ownId?.n === PARAMS.q6N && g["Q6 exports"].ids.ownId !== g["Q6 exports"].ids.sharedId,
         `Q6's own-id facet: ${show(g["Q6 exports"])}`);
  for (const kase of PARAMS.q4.cases) {
    const x = g[`Q4 ${kase}`];
    expect(x && x.pingErrors === 0 && x.parentReset === false && x.pings > 0, `Q4 ${kase}: ${show(x)}`);
    expect(kase === "throw" ? /thrown by the facet/.test(x?.outcome ?? "") : x?.outcome === "returned",
           `Q4 ${kase}'s outcome: ${x?.outcome}`);
  }
  expect(report.q4Rate > 0, "Q4: no burn rate from the yield case");
  expect(g.Q1?.verdict === "separate" && /passed/.test(g.Q1.facetLimit ?? "") && g.Q1.rowsWritten > 0,
         `Q1: ${show(g.Q1)}`);
  expect(r["Q1 bc"]?.plan?.jobs?.every((job) => job.status === "reached" && job.points.length >= 0), "Q1: a job did not reach its size");
  expect(g["Q5 at size"]?.wiped === true && g["Q5 at size"].before > PARAMS.capBytes, `Q5 at size: ${show(g["Q5 at size"])}`);
  expect(/ctx\.exports|not supported/.test(g["Q7 agents"]?.steps?.plain ?? ""), `Q7 ours: ${show(g["Q7 agents"])}`);
  // workerd's own answer for the PiHarness child is recorded, not asserted: it is a reading.
  const q7 = g["Q7 agents-exports"];
  expect(q7?.steps?.plain === "ok" && (q7.steps.spawn === "ok" ? /facetprobe answer/.test(q7.firstText ?? "") : Boolean(q7.steps.spawn)),
         `Q7 exports: ${show(q7)}`);
  const summary = summarize(report);
  expect(summary.includes("| question | case | reading | detail |") && !summary.includes("| not read |"),
         "the summary table is missing a reading");
}

/**
 * analytics.mjs on a stand-in API: per-object rows kept for the run's ids, storage datasets
 * found by introspection, account-wide rows reduced to their change, a refusal reported.
 */
async function checkAnalytics(report) {
  const ids = objects(report);
  const q6 = report.results["Q6 exports"];
  expect(ids[q6.parentId] && ids[q6.ids.ownId] && Object.keys(ids).length >= 5, `analytics' objects: ${show(ids)}`);
  const scalar = (name) => ({ name, type: { name: "Float", kind: "SCALAR" } });
  const object = (name) => ({ name: null, kind: "LIST", ofType: { name: null, kind: "NON_NULL", ofType: { name, kind: "OBJECT" } } });
  const types = {
    account: { fields: [
      { name: "durableObjectsStorageGroups", type: object("Acct"), args: [{ name: "filter", type: { name: "AcctF", kind: "INPUT_OBJECT" } }] },
      { name: "durableObjectsSqlStorageGroups", type: object("Obj"), args: [{ name: "filter", type: { name: "ObjF", kind: "INPUT_OBJECT" } }] },
      { name: "durableObjectsInvocationsAdaptiveGroups", type: object("Inv"), args: [] }] },
    Acct: { fields: [{ name: "dimensions", type: { name: "AcctD", kind: "OBJECT" } }, { name: "max", type: { name: "AcctM", kind: "OBJECT" } }] },
    AcctD: { fields: [{ name: "datetimeHour", type: { name: "Time", kind: "SCALAR" } }] },
    AcctM: { fields: [scalar("storedBytes")] },
    AcctF: { inputFields: [{ name: "datetimeHour_geq" }, { name: "datetimeHour_leq" }] },
    Obj: { fields: [{ name: "dimensions", type: { name: "ObjD", kind: "OBJECT" } }, { name: "max", type: { name: "AcctM", kind: "OBJECT" } }] },
    ObjD: { fields: [{ name: "date", type: { name: "Date", kind: "SCALAR" } }, { name: "objectId", type: { name: "String", kind: "SCALAR" } }] },
    ObjF: { inputFields: [{ name: "date_geq" }, { name: "date_leq" }] },
  };
  const fake = async (_url, init) => {
    const { query, variables } = JSON.parse(init.body);
    if (query.includes("__type")) {
      return Response.json({ data: { __type: types[variables.name] ?? null } });
    }
    if (query.includes("exceededCpuErrors")) {
      return Response.json({ errors: [{ message: "not authorized for account acct-123" }] });
    }
    const rows = query.includes("durableObjectsInvocationsAdaptiveGroups")
      ? [{ dimensions: { objectId: q6.ids.ownId }, sum: { requests: 1021 } }, { dimensions: { objectId: q6.parentId }, sum: { requests: 1 } },
         { dimensions: { objectId: "someone-else" }, sum: { requests: 9 } }]
      : query.includes("durableObjectsSqlStorageGroups")
        ? [{ dimensions: { date: "d", objectId: q6.parentId }, max: { storedBytes: 5 } }, { dimensions: { date: "d", objectId: "x" }, max: { storedBytes: 7 } }]
        : query.includes("durableObjectsStorageGroups")
          ? [{ dimensions: { datetimeHour: "2" }, max: { storedBytes: 900 } }, { dimensions: { datetimeHour: "1" }, max: { storedBytes: 100 } }]
          : [];
    return Response.json({ data: { viewer: { accounts: [{ rows }] } } });
  };
  const got = await collect({ token: "t", account: "acct-123", start: "2026-10-31T00:00:00Z", end: "2026-10-31T01:00:00Z",
                              ids, fetcher: fake });
  const sql = got.storage.find((set) => set.dataset === "durableObjectsSqlStorageGroups");
  const acct = got.storage.find((set) => set.dataset === "durableObjectsStorageGroups");
  expect(got.byObject[q6.ids.ownId]?.requests === 1021 && !("someone-else" in got.byObject)
         && /not authorized/.test(got.errors.errors ?? "") && !/acct-123/.test(got.errors.errors ?? ""),
         `analytics per object: ${show(got)}`);
  expect(sql?.rows?.length === 1 && sql.rows[0].case && acct?.change?.["max.storedBytes"]?.firstToLast === 800
         && !JSON.stringify(acct).includes("900"), `analytics storage: ${show(got.storage)}`);
  expect(/are billed/.test(billing(got, report)), `Q6 billing: ${billing(got, report)}`);
  expect(table(got, report).includes("| object | requests |"), "analytics' table");
}

const dir = mkdtempSync(join(tmpdir(), "facetprobe-local-"));
checkKey();
await checkTransport();
let mf;
try {
  const paths = writeConfigs({ outDir: join(dir, "config"), kv: "0123456789abcdef0123456789abcdef", copies: RUN });
  checkConfigs(paths);
  for (const name of Object.keys(paths)) {
    bundle(paths[name], join(dir, `out-${name}`));
  }
  const workers = [
    worker(paths.door, join(dir, "out-door"), "door.js", { bindings: { LOCAL_KEY: KEY } }),
    worker(paths.dummy, join(dir, "out-dummy"), "dummy.js"),
    ...RUN.map((copy) => worker(paths[copy], join(dir, `out-${copy}`), copy.startsWith("agents") ? "agents.js" : "parent.js",
                                { outboundService: outbound })),
  ];
  mf = new Miniflare(convertV4MiniflareOptions({ workers }));
  const door = (body, key = KEY) => mf.dispatchFetch("http://door.local/", {
    method: "POST", headers: { "x-probe-key": key }, body: JSON.stringify(body) });
  expect((await door({}, "wrong")).status === 403, "the door's fetch answered without the key");
  const refused = await (await door({ copy: "main", object: "x", method: "alarm" })).json();
  expect(/^refused/.test(refused.error ?? ""), `a method off the door's list: ${show(refused)}`);
  const call = async (request) => {
    const body = await (await door(request)).json();
    if (body.error) {
      throw new Error(body.error);
    }
    return body.result;
  };
  const deploy = Object.fromEntries(Object.keys(COPIES).map((copy) => [copy, { status: RUN.includes(copy) ? "accepted (local dry-run)" : "not run locally" }]));
  const started = Date.now();
  const phases = [];
  const report = await drive({ call, runId: "local-1", params: PARAMS, timing: TIMING, deploy, kv: "local stand-in",
                               onPhase: (x) => phases.push(x.phase) });
  report.readings = readings(report);
  checkRun(report);
  await checkAnalytics(report);
  expect(phases.includes("Q1") && phases.at(-1) === "done", `phases: ${show(phases)}`);
  console.log(summarize(report));
  console.log(`local run: ${((Date.now() - started) / 1000).toFixed(0)} s; dummy ${DUMMY}`);
  const out = process.argv.indexOf("--out");
  if (out > 0) {
    writeFileSync(process.argv[out + 1], `${JSON.stringify(report, null, 2)}\n`);
  }
} finally {
  await mf?.dispose();
  rmSync(dir, { recursive: true, force: true });
  rmSync(BUILD, { recursive: true, force: true });
}

if (problems.length) {
  console.error(`::error::facet probe harness check failed:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log("facet probe harness check passed: every question, through the Workflow; the reading key separates its rules");
