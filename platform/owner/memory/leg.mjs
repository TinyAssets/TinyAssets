// The workerd memory leg (ADR-037, memshape_research.md §5 PR 2): each chunk-wise path is
// O(1) in the size of its data, proven by differences, never by an absolute peak.
//
// It bundles `probe.ts` (the platform's own edit, window and slot code), runs it in
// standalone workerd with `--expose-gc` (one inspector client, every target), and for each
// case samples `usedSize + backingStorageSize` of the owner isolate right after an
// in-worker `gc()`, so a sample is the live set, byte arrays included (review H2). Then:
// - O(1) in size: peak(4S) - peak(S) is at most a quarter chunk (an edit of 8 against
//   32 MiB; a read of 1 against 8 MiB);
// - the unit's cost: peak(S) - idle is at most the slot's constructive cost
//   (`CHUNK_SLOT_BYTES`, 12 MiB);
// - per unit: N and 2N at once, the increment per unit at most that cost.
// Differences cancel the baseline and the probe's own code (review M4); the absolute figure
// is production's (`memoryUsageBytes`). It also prints each Worker's resting heap and
// backing stores and workerd's RSS, the figures `holds.ts` records until production's.
// Run: `npm run test:memory -w owner`. Exits non-zero on any failed check.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OWNER = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(join(OWNER, "package.json"));
const MIB = 1024 * 1024;
const CHUNK = 4 * MIB;
const SLOT = 3 * CHUNK;
const PORT = 8899;
const INSPECT = 9399;
const HTTP = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const work = mkdtempSync(join(tmpdir(), "owner-memory-"));
const { build } = require("esbuild");
await build({ entryPoints: [join(OWNER, "memory", "probe.ts")], bundle: true, format: "esm",
              platform: "neutral", target: "es2022", outfile: join(work, "probe.js"),
              external: ["cloudflare:*", "node:*"], logLevel: "error" });
writeFileSync(join(work, "config.capnp"), `using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (
  v8Flags = ["--expose-gc"],
  services = [ (name = "main", worker = .owner), (name = "files", worker = .files) ],
  sockets = [ (name = "http", address = "127.0.0.1:${PORT}", http = (), service = "main") ],
);
const owner :Workerd.Worker = (
  modules = [ (name = "probe.js", esModule = embed "probe.js") ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = ["nodejs_compat"],
  durableObjectNamespaces = [ (className = "Holder", uniqueKey = "owner-memory-leg") ],
  durableObjectStorage = (inMemory = void),
  bindings = [ (name = "HOLDER", durableObjectNamespace = "Holder"),
               (name = "STORE", service = (name = "files", entrypoint = "Store")) ],
);
const files :Workerd.Worker = (
  modules = [ (name = "probe.js", esModule = embed "probe.js") ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = ["nodejs_compat"],
  bindings = [ (name = "HOLDER", durableObjectNamespace = (className = "Holder",
                                                           serviceName = "main")),
               (name = "STORE", service = (name = "files", entrypoint = "Store")) ],
);
`);

const workerd = join(dirname(require.resolve("workerd/package.json")), "bin", "workerd");
const child = spawn(workerd, ["serve", join(work, "config.capnp"),
                              `--inspector-addr=127.0.0.1:${INSPECT}`],
                    { stdio: ["ignore", "inherit", "inherit"] });
const problems = [];
try {
  let list = [];
  for (let n = 0; n < 100 && list.length < 2; n++) {
    await sleep(100);
    list = await fetch(`http://127.0.0.1:${INSPECT}/json/list`).then((r) => r.json(), () => []);
  }
  const owner = list.find((target) => target.id === "main");
  if (owner === undefined) {
    throw new Error(`no owner isolate among the inspector's targets: ${JSON.stringify(list)}`);
  }
  const ws = new WebSocket(owner.webSocketDebuggerUrl);
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
  const live = async () => {
    await fetch(`${HTTP}/gc`).then((r) => r.text());
    const usage = await call("Runtime.getHeapUsage");
    return usage.usedSize + (usage.backingStorageSize ?? 0);
  };
  const rss = () => Number(/VmRSS:\s+(\d+)/.exec(readFileSync(`/proc/${child.pid}/status`,
                                                              "utf8"))[1]) * 1024;
  await live();
  const idle = await live();
  const restRss = rss();
  /** The most a case holds over the idle isolate, sampled live while it runs. */
  const peak = async (path, mib, n = 1) => {
    await fetch(`${HTTP}/drop`).then((r) => r.text());
    const floor = Math.min(await live(), await live());
    let most = 0;
    let running = true;
    const sampler = (async () => {
      while (running) {
        most = Math.max(most, await live() - floor);
        await sleep(1);
      }
    })();
    const answer = await fetch(`${HTTP}/run?path=${path}&mib=${mib}&n=${n}`);
    const body = await answer.text();
    running = false;
    await sampler;
    if (!answer.ok) {
      throw new Error(`${path} ${mib} MiB × ${n}: ${answer.status} ${body}`);
    }
    return most;
  };
  const rows = [];
  const check = (name, value, most, what) => {
    rows.push({ check: name, mib: +(value / MIB).toFixed(2), most_mib: +(most / MIB).toFixed(2),
                ok: value <= most });
    if (value > most) {
      problems.push(`${name}: ${value} bytes, past ${what} (${most})`);
    }
  };
  for (const [path, small, large] of [["alloc", 8, 32], ["store", 8, 32], ["streamstore", 8, 32], ["edit", 8, 32], ["read", 1, 8]]) {
    const at = await peak(path, small);
    const atLarge = await peak(path, large);
    check(`${path}: peak(${large} MiB) - peak(${small} MiB)`, atLarge - at, CHUNK / 4,
          "a quarter chunk");
    check(`${path}: peak(${small} MiB) - idle`, at, SLOT, "one slot's cost");
  }
  const one = await peak("edit", 8, 2);
  const two = await peak("edit", 8, 4);
  check("edit: (peak(4 objects) - peak(2 objects)) / 2", (two - one) / 2, SLOT,
        "one slot's cost");
  console.log(JSON.stringify({ owner_idle_mib: +(idle / MIB).toFixed(2),
                               workerd_rss_mib: +(restRss / MIB).toFixed(2), checks: rows },
                             null, 1));
  ws.close();
} catch (error) {
  problems.push(String(error?.stack ?? error));
} finally {
  child.kill("SIGKILL");
  rmSync(work, { recursive: true, force: true });
}
if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
