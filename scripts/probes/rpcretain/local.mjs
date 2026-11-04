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

import { collect, DO_COUNT, memorySelections, progress, queries, SECONDARY, table } from "./analytics.mjs";
import { HERE, SCRIPT, writeConfigs } from "./config.mjs";
import { classify, CONTROL_FAIL_BY, drive, FORMS, matrix, memoryMarks, readings, redact, REPEATS, SIZES,
         summarize } from "./drive.mjs";

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

/**
 * The reading key on fixed answers. A failure counts as memory only when its error is the
 * runtime's memory reset or the object's analytics charged it one; any other is inconclusive.
 */
function checkKey() {
  const MEMORY = "Durable Object's isolate exceeded its memory limit and was reset.";
  const TIMEOUT = "request failed: TimeoutError The operation was aborted due to timeout";
  const CPU = "Worker exceeded CPU time limit.";
  // failAt: { form: [from MiB, error] }
  const answers = (failAt) => matrix().map((step, i) => {
    const fail = failAt[step.form];
    const at = new Date(Date.UTC(2026, 10, 4, 12, i)).toISOString();
    return fail && step.mib >= fail[0]
      ? { ...step, at, wallMs: 5000, objectId: `obj-${i}`, ok: false, error: fail[1] }
      : { ...step, at, wallMs: 5000, objectId: `obj-${i}`, ok: true };
  });
  const good = readings(answers({ retain: [128, MEMORY], rpcArr: [512, MEMORY] }));
  expect(good.control.valid && good.control.firstMemory === 128, `valid control: ${show(good.control)}`);
  expect(/memory reset from 512/.test(good.forms.rpcArr.reading), `rpcArr: ${good.forms.rpcArr.reading}`);
  expect(/ok at every size to 1024/.test(good.forms.alloc.reading), `alloc: ${good.forms.alloc.reading}`);
  const late = readings(answers({ retain: [512, MEMORY] }));
  expect(!late.control.valid && /void/.test(late.forms.rpcStream.reading), `late control: ${show(late)}`);
  const never = readings(answers({}));
  expect(!never.control.valid && never.control.status === "NOT valid", `a control that never fails: ${show(never.control)}`);

  // The reviewer's case (rvprobe_validity.mjs): a timeout must not validate the control, and a
  // CPU-limit failure must not read as garbage counted against the limit.
  const review = readings(answers({ retain: [128, TIMEOUT], rpcArr: [1024, CPU] }));
  expect(!review.control.valid && review.control.status === "inconclusive",
         `a timed-out control must be inconclusive: ${show(review.control)}`);
  expect(!/memory reset/.test(review.forms.rpcArr.reading) && review.forms.rpcArr.firstMemory === null,
         `a CPU failure must not count as memory: ${review.forms.rpcArr.reading}`);
  // With a valid control, the timeout and the CPU failure each read inconclusive, not memory.
  const mixed = readings(answers({ retain: [128, MEMORY], rpcArr: [1024, CPU], rpcStream: [512, TIMEOUT] }));
  expect(mixed.control.valid, `mixed control: ${show(mixed.control)}`);
  expect(/^inconclusive: no memory reset/.test(mixed.forms.rpcArr.reading) && mixed.forms.rpcArr.firstMemory === null,
         `a CPU failure must read inconclusive: ${mixed.forms.rpcArr.reading}`);
  expect(/^inconclusive: no memory reset/.test(mixed.forms.rpcStream.reading),
         `a timeout must read inconclusive: ${mixed.forms.rpcStream.reading}`);
  expect(classify({ ok: false, error: "Worker exceeded resource limits" }) === "inconclusive",
         "the generic 1102 page must not count as memory on its own");

  // The object's own analytics name a reset the error text does not.
  const generic = answers({ retain: [128, "HTTP 503: Worker exceeded resource limits"] });
  const marks = memoryMarks({ readings: { "DO memory errors by object and minute": generic
    .filter((a) => !a.ok)
    .map((a) => ({ dimensions: { objectId: a.objectId, datetimeMinute: a.at.replace(".000Z", "Z") },
                   sum: { exceededMemoryErrors: 1 } })) } });
  expect(readings(generic).control.status === "inconclusive", "a generic reset without analytics is inconclusive");
  expect(readings(generic, marks).control.valid, `the analytics' memory errors must count: ${show(readings(generic, marks).control)}`);
  const elsewhere = memoryMarks({ readings: { "DO memory errors by object and minute":
    [{ dimensions: { objectId: "obj-other", datetimeMinute: generic[0].at }, sum: { exceededMemoryErrors: 3 } }] } });
  expect(!readings(generic, elsewhere).control.valid, "another object's memory error must not count");

  const summary = summarize({ startedAt: 0, planned: 1, phase: "done",
                              answers: [{ form: "retain", mib: 32, run: 1, ok: false, wallMs: 1, error: `bad ${TOKEN} | x` }] });
  expect(!redact(summary, [TOKEN]).includes(TOKEN) && summary.includes("bad local-token / x")
         && summary.includes("| inconclusive |"), "the summary must keep error text and the class in its cells");
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
  const names = Object.keys(queries(memory, "ns"));
  expect(Object.keys(queries(memory, undefined)).length === 3 && names.length === 7,
         "the DO readings must need the namespace id");
  expect(names[0] === DO_COUNT && names.filter((n) => n.startsWith(SECONDARY)).length === 3,
         `the DO readings come first and the Worker ones are secondary: ${show(names)}`);
  const account = "acct-123";
  const answers = [{ form: "retain", mib: 128, run: 1, objectId: "obj-a", ok: false, at: "2026-11-04T12:00:10.000Z", wallMs: 900 },
                   { form: "alloc", mib: 32, run: 1, objectId: "obj-b", ok: true, at: "2026-11-04T12:01:10.000Z", wallMs: 900 }];
  let doRows = [{ dimensions: { objectId: "obj-a" }, sum: { requests: 1 } }];
  const fetcher = async (_url, init) => {
    const { query, variables } = JSON.parse(init.body);
    if (query.includes("__schema")) {
      return Response.json({ data: { __schema: { types } } });
    }
    expect(variables.script === SCRIPT, `queried script ${variables.script}`);
    const rows = (list) => Response.json({ data: { viewer: { accounts: [{ rows: list }] } } });
    if (query.includes("objectId status")) {
      return Response.json({ errors: [{ message: `unknown field status on ${account}` }] });
    }
    if (query.includes("durableObjectsInvocationsAdaptiveGroups")) {
      return rows(doRows);
    }
    if (query.includes("exceededMemoryErrors")) {
      return rows([{ dimensions: { objectId: "obj-a", datetimeMinute: "2026-11-04T12:00:00Z" }, sum: { exceededMemoryErrors: 1 } }]);
    }
    if (query.includes("durableObjectsPeriodicGroups")) {
      return rows([{ dimensions: { objectId: "obj-a", datetimeMinute: "2026-11-04T12:00:00Z" }, max: { memoryUsageBytes: 136314880 } }]);
    }
    // Worker level: SELF's Store calls make it count far more than the calls.
    return rows([{ dimensions: { status: "success" }, sum: { requests: 300, errors: 0 }, quantiles: { memoryUsageBytesP50: 41943040 } }]);
  };
  const analytics = await collect({ token: "t", account, namespace: "ns-1", start: "a", end: "b", fetcher });
  const early = progress(analytics, answers);
  expect(early.counted === 1 && early.target === 2,
         `the wait must key on the DO invocations, not the Worker's 300: ${show(early)}`);
  doRows = [...doRows, { dimensions: { objectId: "obj-b" }, sum: { requests: 1 } }];
  const caught = progress(await collect({ token: "t", account, namespace: "ns-1", start: "a", end: "b", fetcher }), answers);
  expect(caught.counted === 2, `caught up: ${show(caught)}`);
  expect(/unknown field/.test(analytics.errors["DO invocations by object and status"] ?? "")
         && !analytics.errors["DO invocations by object and status"].includes(account), `errors: ${show(analytics.errors)}`);
  expect(readings(answers, memoryMarks(analytics)).classes[0] === "memory", "the DO memory error must class the call memory");
  const text = table(analytics, answers);
  expect(text.includes("130.0 MiB") && text.includes("retain 128 MiB run 1") && !text.includes("obj-a")
         && text.includes(`${SECONDARY}invocations by status`), `table: ${text.slice(0, 600)}`);
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
      expect(/^[0-9a-f]{64}$/.test(a.objectId ?? ""), `${a.form} ${a.mib}: object id ${a.objectId}`);
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
