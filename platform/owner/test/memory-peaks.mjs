// The workerd memory leg (memory_research.md §4, "the CI guard" 3): each path at its worst,
// run in the owner's own vitest runner isolate (its Worker and objects, the files Worker
// beside it), its heap sampled through the inspector after a full collection before each
// sample, so a sample is the live set. `test/memory/peaks.test.ts` marks each path's start
// and end with the grant it declares (`globalThis.__mark`, a binding this adds); a path whose
// live peak over its start passes its grant fails the leg, as does a path with no sample, a
// test that fails, or a mark that never came. Run: `npm run test:memory` in `owner/`.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OWNER = dirname(dirname(fileURLToPath(import.meta.url)));
const PORT = 9339;
const RUNNER = "core:user:vitest-plugin-runner-memory";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const vitest = join(dirname(createRequire(join(OWNER, "package.json"))
  .resolve("vitest/package.json")), "vitest.mjs");
const run = spawn(process.execPath, [vitest, "run", "--project", "memory",
                                     "--no-file-parallelism", `--inspect=127.0.0.1:${PORT}`],
                  { cwd: OWNER, stdio: ["ignore", "inherit", "inherit"],
                    env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
const exited = new Promise((resolve) => run.on("exit", (code) => resolve(code ?? 1)));

const samples = [];
const marks = [];
let attached = false;
let done = false;

async function attach(target) {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const call = (method, params = {}) => new Promise((resolve) => {
    const n = ++id;
    pending.set(n, resolve);
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id !== undefined) {
      pending.get(message.id)?.(message.result ?? message.error);
      pending.delete(message.id);
    } else if (message.method === "Runtime.bindingCalled") {
      marks.push({ t: performance.now(), at: samples.length, ...JSON.parse(message.params.payload) });
    }
  };
  ws.onclose = () => { done = true; };
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  await call("Runtime.enable");
  await call("Runtime.addBinding", { name: "__mark" });
  while (!done && ws.readyState === 1) {
    await call("HeapProfiler.collectGarbage");
    const usage = await call("Runtime.getHeapUsage");
    if (usage?.usedSize !== undefined) {
      samples.push({ t: performance.now(), used: usage.usedSize });
    }
    await sleep(1);
  }
}

const attaching = (async () => {
  while (!attached && !done) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const runner = list.find((target) => target.id === RUNNER);
      if (runner !== undefined) {
        attached = true;
        await attach(runner);
      }
    } catch {
      // not up yet
    }
    await sleep(20);
  }
})();

const code = await exited;
done = true;
await Promise.race([attaching, sleep(2000)]);

const MIB = 1024 * 1024;
const problems = [];
if (code !== 0) {
  problems.push(`the memory tests failed (exit ${code})`);
}
const rows = [];
for (const begin of marks.filter((mark) => mark.mark === "begin")) {
  const end = marks.find((mark) => mark.mark === "end" && mark.path === begin.path && mark.t > begin.t);
  if (end === undefined) {
    problems.push(`${begin.path}: no end mark`);
    continue;
  }
  const before = samples.filter((s) => s.t <= begin.t).at(-1);
  const during = samples.filter((s) => s.t > begin.t && s.t <= end.t);
  if (before === undefined || during.length === 0) {
    problems.push(`${begin.path}: no heap sample`);
    continue;
  }
  // The path's own: over the lower of the heap just before it and just after it (a test's
  // setup may still be held as it starts).
  const after = samples.find((s) => s.t > end.t);
  const floor = Math.min(before.used, after?.used ?? before.used);
  const peak = Math.max(...during.map((s) => s.used)) - floor;
  rows.push({ path: begin.path, grant_mib: +(begin.grant / MIB).toFixed(2),
              live_peak_mib: +(peak / MIB).toFixed(2), samples: during.length,
              rest_mib: +(floor / MIB).toFixed(1) });
  if (peak > begin.grant) {
    problems.push(`${begin.path}: a live peak of ${peak} bytes passes its grant of ${begin.grant}`);
  }
}
const rest = marks.find((mark) => mark.mark === "rest");
let baseline = null;
if (rest === undefined) {
  problems.push("no rest mark: the isolate's baseline was not measured");
} else {
  const near = samples.filter((s) => Math.abs(s.t - rest.t) < 300);
  baseline = near.length === 0 ? null : Math.max(...near.map((s) => s.used));
  if (baseline === null) {
    problems.push("no heap sample at rest");
  } else if (baseline > rest.baseline) {
    problems.push(`the isolate at rest holds ${baseline} bytes, past BASELINE_BYTES ` +
                  `${rest.baseline}`);
  }
}
if (rows.length === 0) {
  problems.push("no path was measured: the inspector never attached, or no mark came");
}
console.log(JSON.stringify({ baseline_mib: baseline === null ? null : +(baseline / MIB).toFixed(1),
                             paths: rows }, null, 1));
if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
