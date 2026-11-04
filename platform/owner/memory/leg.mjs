// The workerd memory leg (ADR-037): each of the owner's real paths that holds chunks, run at
// its worst, holds what it declares, whatever the size of its data, proven by differences.
//
// It bundles `probe.ts` (the platform's own owner object, files and bodies code) and runs it
// in standalone workerd with `--expose-gc`, against an in-memory chunk store in another
// isolate. Every chunk call is a checkpoint: the path waits on this driver while it collects
// the owner isolate (a swept full collection) and reads `usedSize + backingStorageSize`
// through the inspector, so a sample is the live set, byte arrays included (reviews H2,
// rpcretain_research.md §4: a read must be atomic with its collection); with several paths
// at once it waits until every one is parked or done. Each file's chunks are distinct, so a
// longer edit makes and puts every chunk after its change. Then:
// - the control holds: a path that keeps every chunk reads as growing, or the leg proves
//   nothing;
// - the baseline: the isolate at rest, one owner object built and served, is within
//   `OWNER_BASELINE_BYTES`;
// - O(1) in size: peak(L) - peak(S) is at most a quarter chunk, at sizes past the path's
//   steady state (an edit of 32 against 96 MiB; an append to 8 against 32 MiB; a body's
//   window from 2 against 8 MiB);
// - each path's cost: its peak is within the units it declares (`holds.ts`);
// - per unit: two edits at once hold at most one making step's units more than one;
// - its time: the worst edit's time a chunk, measured with no collections, keeps
//   `EDIT_BYTES_MAX` inside a request's 30 s of CPU (the time is wall time, at least the CPU
//   it spent), and a full queue turns over within `SLOT_WAIT_MS` at the measured step.
// Differences cancel the baseline and the probe's own code (review M4); the absolute figure
// is production's (`memoryUsageBytes`). Run: `npm run test:memory -w owner`. Exits non-zero
// on any failed check, printing every figure.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { egressLeg } from "./egress.mjs";

const OWNER = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(join(OWNER, "package.json"));
const MIB = 1024 * 1024;
const PORT = 8899;
const INSPECT = 9399;
const DRIVER = 8898;
const HTTP = `http://127.0.0.1:${PORT}`;
const CPU_MS = 30_000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const work = mkdtempSync(join(tmpdir(), "owner-memory-"));
const { build } = require("esbuild");
await build({ entryPoints: [join(OWNER, "memory", "probe.ts")], bundle: true, format: "esm",
              platform: "neutral", target: "es2022", outfile: join(work, "probe.js"),
              external: ["cloudflare:*", "node:*"], conditions: ["workerd", "worker", "browser"],
              mainFields: ["module", "main"], logLevel: "error" });
mkdirSync(join(work, "objects"));
writeFileSync(join(work, "dummy.js"),
              "export default { fetch() { return new Response(null, { status: 404 }); } };\n");
writeFileSync(join(work, "config.capnp"), `using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (
  v8Flags = ["--expose-gc"],
  services = [ (name = "main", worker = .owner), (name = "files", worker = .files),
               (name = "dummy", worker = .dummy),
               (name = "objects", disk = (path = "${join(work, "objects")}", writable = true)),
               (name = "driver", external = (address = "127.0.0.1:${DRIVER}", http = ())) ],
  sockets = [ (name = "http", address = "127.0.0.1:${PORT}", http = (), service = "main") ],
);
const owner :Workerd.Worker = (
  modules = [ (name = "probe.js", esModule = embed "probe.js") ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = ["nodejs_compat"],
  durableObjectNamespaces = [ (className = "LegOwner", uniqueKey = "owner-memory-leg",
                               enableSql = true) ],
  durableObjectStorage = (localDisk = "objects"),
  bindings = [ (name = "OWNER", durableObjectNamespace = "LegOwner"),
               (name = "FILES", service = (name = "files", entrypoint = "Store")),
               (name = "STORE", service = (name = "files", entrypoint = "Store")),
               (name = "DRIVER", service = "driver"),
               (name = "GATEWAY", service = "dummy"), (name = "HOOKS", service = "dummy"),
               (name = "CONTROL", kvNamespace = "dummy"),
               (name = "ROUTING_MAC_KEY", text = "bGVnLXJvdXRpbmcta2V5LWZvci10aGUtbWVtb3J5LWxlZw") ],
);
const files :Workerd.Worker = (
  modules = [ (name = "probe.js", esModule = embed "probe.js") ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = ["nodejs_compat"],
  bindings = [ (name = "DRIVER", service = "driver") ],
);
const dummy :Workerd.Worker = (
  modules = [ (name = "dummy.js", esModule = embed "dummy.js") ],
  compatibilityDate = "2026-10-23",
);
`);

const driver = createServer();
await new Promise((resolve) => driver.listen(DRIVER, "127.0.0.1", resolve));
const workerd = join(dirname(require.resolve("workerd/package.json")), "bin", "workerd");
const child = spawn(workerd, ["serve", join(work, "config.capnp"),
                              `--inspector-addr=127.0.0.1:${INSPECT}`],
                    { stdio: ["ignore", "inherit", "inherit"] });
const problems = [];
const rows = [];
try {
  let list = [];
  for (let n = 0; n < 100 && !list.some((target) => target.id === "main"); n++) {
    await sleep(100);
    list = await fetch(`http://127.0.0.1:${INSPECT}/json/list`).then((r) => r.json(), () => []);
  }
  const target = list.find((each) => each.id === "main");
  if (target === undefined) {
    throw new Error(`no owner isolate among the inspector's targets: ${JSON.stringify(list)}`);
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id !== undefined) {
      pending.get(message.id)?.(message.result ?? message.error);
      pending.delete(message.id);
    }
  };
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  const call = (method, params = {}) => new Promise((resolve) => {
    const n = ++id;
    pending.set(n, resolve);
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  await call("Runtime.enable");
  const get = async (path) => {
    const answer = await fetch(`${HTTP}${path}`);
    const body = await answer.text();
    if (!answer.ok) {
      throw new Error(`${path}: ${answer.status} ${body}`);
    }
    return body;
  };
  const live = async () => {
    await get("/gc");
    const usage = await call("Runtime.getHeapUsage");
    return usage.usedSize + (usage.backingStorageSize ?? 0);
  };

  // The checkpoints: each path parks on one; with every active path parked (or done), the
  // isolate is sampled, then they all go on.
  let sample = null;
  let active = 0;
  let parked = [];
  let sampling = Promise.resolve();
  const settle = () => {
    if (parked.length === 0 || parked.length < active) {
      return;
    }
    const go = parked;
    parked = [];
    sampling = sampling.then(async () => {
      if (sample !== null) {
        await sample();
      }
      for (const response of go) {
        response.end("go");
      }
    });
  };
  driver.on("request", (request, response) => {
    if (request.url === "/done") {
      active -= 1;
      response.end("done");
      settle();
    } else if (sample === null) {
      response.end("go");
    } else {
      parked.push(response);
      settle();
    }
  });

  const costs = JSON.parse(await get("/costs"));
  const UNIT = costs.unit;
  const QUARTER = costs.chunk / 4;
  /** The most a case holds over the isolate at rest after its setup, and its checkpoints. */
  const peak = async (path, mib, n = 1) => {
    await get(`/setup?path=${path}&mib=${mib}&n=${n}`);
    const floor = Math.min(await live(), await live());
    let most = 0;
    let points = 0;
    active = n;
    sample = async () => {
      points += 1;
      most = Math.max(most, await live() - floor);
    };
    try {
      await get(`/run?path=${path}&mib=${mib}`);
      await sampling;
    } finally {
      sample = null;
      active = 0;
    }
    // Every chunk seen: an edit reads each chunk to scan it and to hash it, and makes and puts
    // each (its content distinct, none already stored), so at least four calls a chunk.
    const chunks = Math.ceil(mib * MIB / (4 * MIB));
    const least = path === "edit" ? 4 * chunks : path === "hoard" ? chunks : 1;
    if (points < least) {
      throw new Error(`${path} ${mib} MiB x ${n}: ${points} checkpoints, fewer than ${least}`);
    }
    return most;
  };
  /** A case's wall time with no collections (every checkpoint answered at once). */
  const timed = async (path, mib) => {
    await get(`/setup?path=${path}&mib=${mib}&n=1`);
    const started = performance.now();
    await get(`/run?path=${path}&mib=${mib}&timed=1`);
    return performance.now() - started;
  };
  const check = (name, value, most, what) => {
    rows.push({ check: name, mib: +(value / MIB).toFixed(2), most_mib: +(most / MIB).toFixed(2),
                ok: value <= most });
    if (value > most) {
      problems.push(`${name}: ${value} bytes, past ${what} (${most})`);
    }
  };

  // The baseline: the isolate at rest with one owner object built, migrated and served.
  await get("/rest");
  const rest = Math.min(await live(), await live());
  check("baseline: the owner isolate at rest, one object served", rest,
        costs.OWNER_BASELINE_BYTES, "OWNER_BASELINE_BYTES");

  // The control: what it keeps must read as kept.
  const hoarded = await peak("hoard", 32) - await peak("hoard", 8);
  rows.push({ check: "control: peak(32 MiB) - peak(8 MiB), kept", mib: +(hoarded / MIB)
    .toFixed(2), least_mib: 18, ok: hoarded >= 18 * MIB });
  if (hoarded < 18 * MIB) {
    problems.push(`the control kept 24 MiB more and read ${hoarded} bytes more: the leg ` +
                  "cannot see what a path holds");
  }

  const edit = { 8: await peak("edit", 8), 32: await peak("edit", 32),
                 96: await peak("edit", 96) };
  check("edit: peak(96 MiB) - peak(32 MiB)", edit[96] - edit[32], QUARTER, "a quarter chunk");
  for (const mib of [8, 32, 96]) {
    check(`edit: peak(${mib} MiB)`, edit[mib], costs.EDIT_MAKE_UNITS * UNIT,
          "EDIT_MAKE_UNITS");
  }
  const twice = await peak("edit", 32, 2);
  check("edit: peak(2 at once) - peak(1), at 32 MiB", twice - edit[32],
        costs.EDIT_MAKE_UNITS * UNIT, "one making step's units");

  const append = { 8: await peak("append", 8), 32: await peak("append", 32) };
  check("append: peak(32 MiB) - peak(8 MiB)", append[32] - append[8], QUARTER,
        "a quarter chunk");
  check("append: peak(32 MiB)", append[32], costs.APPEND_UNITS * UNIT, "APPEND_UNITS");

  const window = { 2: await peak("window", 2), 8: await peak("window", 8) };
  check("window: peak(8 MiB body) - peak(2 MiB body)", window[8] - window[2], QUARTER,
        "a quarter chunk");
  check("window: peak(8 MiB body)", window[8], costs.WINDOW_UNITS * UNIT, "WINDOW_UNITS");

  check("fetch: peak(4 MiB body)", await peak("fetch", 4), costs.body_units[0] * UNIT,
        "bodyUnits(1)");
  check("fetch: peak(8 MiB body)", await peak("fetch", 8), costs.body_units[1] * UNIT,
        "bodyUnits(2)");

  // Time: the worst edit's, a chunk at a time, with no collections between.
  const chunks = 96 * MIB / costs.chunk;
  const msPerChunk = Math.min(await timed("edit", 96), await timed("edit", 96)) / chunks;
  const editChunks = costs.edit_bytes_max / costs.chunk;
  rows.push({ check: "edit: time a chunk (wall, no collections), at 96 MiB",
              ms: +msPerChunk.toFixed(2),
              edit_bytes_max_s: +(editChunks * msPerChunk / 1000).toFixed(1),
              most_s: CPU_MS / 1000, ok: editChunks * msPerChunk <= CPU_MS });
  if (editChunks * msPerChunk > CPU_MS) {
    problems.push(`EDIT_BYTES_MAX (${editChunks} chunks) at ${msPerChunk} ms a chunk passes ` +
                  `a request's ${CPU_MS} ms of CPU`);
  }
  // A full queue's turnover: every waiter ahead a making step, the pool's larger holds'
  // units turning over at the measured time a step (a chunk's three calls, at most).
  const steps = costs.OWNER_WAITERS * costs.EDIT_MAKE_UNITS /
    (costs.OWNER_POOL_UNITS - costs.OWNER_RESERVE_UNITS) + 1;
  rows.push({ check: "queue: a full queue's turnover at the measured step", ms:
              +(steps * msPerChunk).toFixed(1), most_ms: costs.wait_ms,
              ok: steps * msPerChunk <= costs.wait_ms });
  if (steps * msPerChunk > costs.wait_ms) {
    problems.push(`a full queue turns over in ${steps * msPerChunk} ms, past SLOT_WAIT_MS`);
  }
  console.log(JSON.stringify({ owner_rest_mib: +(rest / MIB).toFixed(2), checks: rows },
                             null, 1));
  ws.close();
} catch (error) {
  problems.push(String(error?.stack ?? error));
  console.log(JSON.stringify({ checks: rows }, null, 1));
} finally {
  child.kill("SIGKILL");
  driver.close();
  rmSync(work, { recursive: true, force: true });
}
// The egress cases (egress shape research §5, `egress.mjs`): the door and the gateway in
// isolates of their own, after the owner's paths.
const egress = await egressLeg();
console.log(JSON.stringify({ egress: egress.rows }, null, 1));
problems.push(...egress.problems);
if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
