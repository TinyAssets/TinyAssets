// The doors' and the files Worker's module-load floors in workerd (ADR-037): each bundle, as
// a deploy builds it, loaded with no bindings, one request served by a stub entry (not the
// Worker's own code), then a swept full collection, and its heap plus backing stores read
// through the inspector. A floor, not a baseline: no object is built and the Worker's own
// paths stay uncompiled (review M4). The owner's baseline is measured with an owner object
// built, migrated and served, by the memory leg (`memory/leg.mjs`), which holds it within
// `OWNER_BASELINE_BYTES`; the doors' and files' pools (PR 3b, PR 4) measure theirs the same
// way. Run by hand: `node memory/baselines.mjs`.
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PLATFORM = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const require = createRequire(join(PLATFORM, "owner", "package.json"));
const wrangler = join(dirname(require.resolve("wrangler/package.json")), "bin", "wrangler.js");
const workerd = join(dirname(require.resolve("workerd/package.json")), "bin", "workerd");
const MIB = 1024 * 1024;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const figures = {};
for (const [name, port] of [["doors", 8912], ["files", 8913]]) {
  const out = mkdtempSync(join(tmpdir(), `baseline-${name}-`));
  try {
    execFileSync(process.execPath, [wrangler, "deploy", "--dry-run", "--outdir", out, "--config",
                                    join(PLATFORM, name, "wrangler.jsonc")],
                 { cwd: join(PLATFORM, name), stdio: "pipe",
                   env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    const modules = readdirSync(out).filter((file) => /\.(js|mjs|wasm)$/.test(file));
    writeFileSync(join(out, "rest.js"), `import * as bundle from "./index.js";
void bundle;
export default {
  async fetch() {
    gc();
    await scheduler.wait(5);
    gc();
    return new Response("rest");
  },
};
`);
    const embed = ["rest.js", ...modules].map((file) => file.endsWith(".wasm")
      ? `(name = "${file}", wasm = embed "${file}")`
      : `(name = "${file}", esModule = embed "${file}")`).join(", ");
    writeFileSync(join(out, "config.capnp"), `using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (
  v8Flags = ["--expose-gc"],
  services = [ (name = "main", worker = .w) ],
  sockets = [ (name = "http", address = "127.0.0.1:${port}", http = (), service = "main") ],
);
const w :Workerd.Worker = (
  modules = [ ${embed} ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = ["nodejs_compat"],
);
`);
    const child = spawn(workerd, ["serve", join(out, "config.capnp"),
                                  `--inspector-addr=127.0.0.1:${port + 100}`],
                        { stdio: ["ignore", "ignore", "inherit"] });
    try {
      let list = [];
      for (let n = 0; n < 100 && list.length === 0; n++) {
        await sleep(100);
        list = await fetch(`http://127.0.0.1:${port + 100}/json/list`).then((r) => r.json(),
                                                                               () => []);
      }
      const ws = new WebSocket(list[0].webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      const usage = () => new Promise((resolve) => {
        ws.onmessage = (event) => resolve(JSON.parse(event.data).result);
        ws.send(JSON.stringify({ id: 1, method: "Runtime.getHeapUsage" }));
      });
      for (let n = 0; n < 3; n++) {
        await fetch(`http://127.0.0.1:${port}/`).then((r) => r.text());
      }
      const at = await usage();
      figures[name] = +((at.usedSize + (at.backingStorageSize ?? 0)) / MIB).toFixed(2);
      ws.close();
    } finally {
      child.kill("SIGKILL");
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}
console.log(JSON.stringify({ module_load_floor_mib: figures }));
