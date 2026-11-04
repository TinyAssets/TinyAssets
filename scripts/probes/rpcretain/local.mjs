#!/usr/bin/env node
/**
 * The RPC retention probe's harness check, in miniflare (workerd): config.mjs writes both
 * configs, wrangler bundles each as a deploy would (`deploy --dry-run`), and miniflare runs the
 * probe Worker from the full config, SELF binding included. drive.mjs then runs every form at
 * small sizes, the gate and the input checks are tried, and the reading key and analytics.mjs
 * are checked on fixed inputs.
 *
 * It proves the harness, never a reading: workerd has no 128 MB isolate limit, so `retain`
 * never fails here and the control reads "NOT valid". rpcretain-probe.yml runs it before
 * deploying. Needs `npm ci` in `platform/`.
 *
 *   node scripts/probes/rpcretain/local.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

import { collect, counted, memorySelections, queries, table } from "./analytics.mjs";
import { HERE, SCRIPT, writeConfigs } from "./config.mjs";
import { CONTROL_FAIL_BY, drive, FORMS, matrix, readings, redact, REPEATS, SIZES, summarize } from "./drive.mjs";

const PLATFORM = join(HERE, "..", "..", "..", "platform");
const fromPlatform = createRequire(join(PLATFORM, "package.json"));
const load = async (name) => import(pathToFileURL(fromPlatform.resolve(name)).href);
const { unstable_getMiniflareWorkerOptions: workerOptions } = await load("wrangler");
const { Miniflare, convertV4MiniflareOptions } = await load("miniflare");
const WRANGLER = join(dirname(fromPlatform.resolve("wrangler/package.json")), "bin", "wrangler.js");

const TOKEN = "local-token";
const LOCAL_SIZES = [8, 16];
const problems = [];
const expect = (ok, what) => {
  if (!ok) {
    problems.push(what);
  }
};
const show = (value) => JSON.stringify(value)?.slice(0, 400);

function checkMatrix() {
  const plan = matrix();
  expect(show(SIZES) === show([32, 128, 256, 512, 1024]), `sizes ${show(SIZES)}`);
  expect(plan.length === FORMS.length * SIZES.length * REPEATS && REPEATS === 2, `matrix of ${plan.length}`);
  expect(SIZES.includes(CONTROL_FAIL_BY), "the control's failure size is not in the matrix");
}

/** The reading key on fixed answers: valid control, a failing form, an invalid control. */
function checkKey() {
  const answers = (failAt) => matrix().map((step) => ({ ...step, ok: !(failAt[step.form] && step.mib >= failAt[step.form]) }));
  const good = readings(answers({ retain: 128, rpcArr: 512 }));
  expect(good.control.valid && good.control.firstFail === 128, `valid control: ${show(good.control)}`);
  expect(/fails from 512/.test(good.forms.rpcArr.reading), `rpcArr: ${good.forms.rpcArr.reading}`);
  expect(/ok at every size to 1024/.test(good.forms.alloc.reading), `alloc: ${good.forms.alloc.reading}`);
  const late = readings(answers({ retain: 512 }));
  expect(!late.control.valid && /void/.test(late.forms.rpcStream.reading), `late control: ${show(late)}`);
  const never = readings(answers({}));
  expect(!never.control.valid, `a control that never fails: ${show(never.control)}`);
  const summary = summarize({ startedAt: 0, planned: 1, phase: "done",
                              answers: [{ form: "retain", mib: 32, run: 1, ok: false, wallMs: 1, error: `bad ${TOKEN} | x` }] });
  expect(!redact(summary, [TOKEN]).includes(TOKEN) && summary.includes("bad local-token / x"),
         "the summary must keep error text in its cell and redact the token");
}

async function checkAnalytics() {
  const types = [
    { name: "AccountWorkersInvocationsAdaptiveQuantiles", fields: [{ name: "cpuTimeP50" }, { name: "memoryUsageBytesP50" }] },
    { name: "AccountDurableObjectsPeriodicGroupsMax", fields: [{ name: "memoryUsageBytes" }] },
    { name: "AccountDurableObjectsPeriodicGroupsSum", fields: [{ name: "activeTime" }] },
  ];
  const memory = memorySelections(types);
  expect(memory.workers === "quantiles { memoryUsageBytesP50 }" && memory.periodic === "max { memoryUsageBytes }",
         `introspection: ${show(memory)}`);
  expect(Object.keys(queries(memory, undefined)).length === 3 && Object.keys(queries(memory, "ns")).length === 6,
         "the DO readings must need the namespace id");
  const account = "acct-123";
  const fetcher = async (_url, init) => {
    const { query, variables } = JSON.parse(init.body);
    if (query.includes("__schema")) {
      return Response.json({ data: { __schema: { types } } });
    }
    expect(variables.script === SCRIPT, `queried script ${variables.script}`);
    if (query.includes("durableObjectsInvocationsAdaptiveGroups")) {
      return Response.json({ errors: [{ message: `unknown field status on ${account}` }] });
    }
    return Response.json({ data: { viewer: { accounts: [{ rows: [
      { dimensions: { status: "success" }, sum: { requests: 30, errors: 0 }, quantiles: { memoryUsageBytesP50: 41943040 } },
      { dimensions: { status: "exceededMemory" }, sum: { requests: 10, errors: 10 } },
    ] }] } } });
  };
  const analytics = await collect({ token: "t", account, namespace: "ns-1", start: "a", end: "b", fetcher });
  expect(counted(analytics) === 40, `counted ${counted(analytics)}`);
  expect(/unknown field/.test(analytics.errors["DO invocations by status"] ?? "")
         && !analytics.errors["DO invocations by status"].includes(account), `errors: ${show(analytics.errors)}`);
  const text = table(analytics);
  expect(text.includes("40.0 MiB") && text.includes("exceededMemory"), `table: ${text.slice(0, 400)}`);
}

async function checkLive(paths) {
  const { workerOptions: options, main } = workerOptions(paths.probe);
  const { compatibilityDate, compatibilityFlags, durableObjects, serviceBindings } = options;
  expect(show(serviceBindings?.SELF) === show({ name: SCRIPT, entrypoint: "Store" }), `SELF: ${show(serviceBindings)}`);
  const mf = new Miniflare(convertV4MiniflareOptions({ workers: [{
    name: SCRIPT, compatibilityDate, compatibilityFlags, durableObjects, serviceBindings,
    modules: true, scriptPath: main, bindings: { TOKEN },
  }] }));
  try {
    const fetcher = (url, init) => mf.dispatchFetch(url, init);
    const base = "http://probe.local";
    const status = async (path, token) =>
      (await mf.dispatchFetch(`${base}${path}`, token === undefined ? {} : { headers: { "x-probe-token": token } })).status;
    expect(await status("/burst?form=alloc&mib=8") === 403, "no token must be refused");
    expect(await status("/burst?form=alloc&mib=8", "wrong") === 403, "a wrong token must be refused");
    expect(await status("/burst?form=alloc&mib=8&token=local-token") === 403, "a token in the query must be refused");
    expect(await status("/burst?form=nope&mib=8", TOKEN) === 400, "an unknown form must be refused");
    expect(await status("/burst?form=alloc&mib=6", TOKEN) === 400, "a size not a multiple of 4 must be refused");
    expect(await status("/burst?form=alloc&mib=4096", TOKEN) === 400, "a size over the cap must be refused");
    const report = await drive({ base, token: TOKEN, sizes: LOCAL_SIZES, gapMs: 0, fetcher });
    expect(report.answers.length === FORMS.length * LOCAL_SIZES.length * REPEATS, `${report.answers.length} answers`);
    for (const a of report.answers) {
      expect(a.ok === true && a.http === 200, `${a.form} ${a.mib} run ${a.run}: ${show(a)}`);
      expect(a.held === (a.form === "retain" ? a.mib / 4 : 0), `${a.form} ${a.mib}: held ${a.held}`);
    }
    // No 128 MB isolate here, so the control cannot fail and every reading is void.
    expect(/NOT valid/.test(summarize(report)), "locally the control must read NOT valid");
    console.log(summarize(report));
  } finally {
    await mf.dispose();
  }
}

const dir = mkdtempSync(join(tmpdir(), "rpcretain-config-"));
try {
  checkMatrix();
  checkKey();
  await checkAnalytics();
  const paths = writeConfigs(dir);
  for (const [name, config] of Object.entries(paths)) {
    execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir", join(dir, `${name}-out`), "--config", config],
                 { cwd: PLATFORM, stdio: "pipe", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
  }
  await checkLive(paths);
} catch (error) {
  problems.push(`harness threw: ${error?.stack ?? error}`);
} finally {
  rmSync(dir, { recursive: true, force: true });
}
if (problems.length) {
  console.error(`::error::rpcretain local check failed:\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log("rpcretain local check: ok");
