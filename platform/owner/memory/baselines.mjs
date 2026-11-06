// The doors' and the files Worker's module-load floors in workerd (ADR-037): each bundle, as
// a deploy builds it, loaded with no bindings, one request served by a stub entry (not the
// Worker's own code), then a swept full collection, and its heap plus backing stores read
// through the inspector. A floor, not a baseline: no object is built and the Worker's own
// paths stay uncompiled (review M4). The owner's baseline is measured with an owner object
// built, migrated and served, by the memory leg (`memory/leg.mjs`), which holds it within
// `OWNER_BASELINE_BYTES`; the doors' and files' pools (PR 3b, PR 4) measure theirs the same
// way. Run by hand: `node memory/baselines.mjs`.
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { PLATFORM } from "../../ci/paths.ts";
import { bundle, workerConfig } from "../../ci/workers.ts";
import { freePorts } from "./ports.mjs";
import { ownWorkerd } from "./workerd.mjs";

const require = createRequire(join(PLATFORM, "owner", "package.json"));
const workerd = join(dirname(require.resolve("workerd/package.json")), "bin", "workerd");
const MIB = 1024 * 1024;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const figures = {};
for (const name of ["doors", "files"]) {
  const [port, inspector] = await freePorts(2);
  const out = mkdtempSync(join(tmpdir(), `baseline-${name}-`));
  try {
    const modules = bundle(join(PLATFORM, name), out).map(({ file }) => file);
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
  compatibilityDate = "${workerConfig(join(PLATFORM, name)).compatibility_date}",
  compatibilityFlags = ["nodejs_compat"],
);
`);
    const own = ownWorkerd(workerd, ["serve", join(out, "config.capnp"),
                                     `--inspector-addr=127.0.0.1:${inspector}`],
                           { onEarlyExit: () => rmSync(out, { recursive: true, force: true }) });
    try {
      let list = [];
      for (let n = 0; n < 100 && list.length === 0; n++) {
        await sleep(100);
        list = await fetch(`http://127.0.0.1:${inspector}/json/list`).then((r) => r.json(),
                                                                               () => []);
      }
      const ws = new WebSocket(list[0].webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      const usage = () => new Promise((resolve) => {
        ws.onmessage = (event) => resolve(JSON.parse(event.data).result);
        ws.send(JSON.stringify({ id: 1, method: "Runtime.getHeapUsage" }));
      });
      /** A request to this leg's own isolate: answered, then its workerd confirmed alive. */
      const ask = async () => {
        await fetch(`http://127.0.0.1:${port}/`).then((r) => r.text());
        own.check();
      };
      for (let n = 0; n < 3; n++) {
        await ask();
      }
      const at = await usage();
      figures[name] = +((at.usedSize + (at.backingStorageSize ?? 0)) / MIB).toFixed(2);
      ws.close();
    } finally {
      own.stop();
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}
console.log(JSON.stringify({ module_load_floor_mib: figures }));
