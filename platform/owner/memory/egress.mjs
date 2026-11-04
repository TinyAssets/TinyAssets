// The memory leg's egress cases (egress shape research §5, "The memory leg"), run by `leg.mjs`
// after the owner's paths: the real egress door and the real gateway, each in its own
// isolate, a provider in a third (`egress-probe.ts`). The provider awaits the driver at each
// MiB of a body it reads and once the body has arrived; there the driver collects the door's
// and the gateway's isolates and reads `usedSize + backingStorageSize` while both wait. Then:
// - the control holds: the door that reads a body whole reads as growing;
// - the door at S against 4S, while the provider holds its answer: at most a quarter chunk;
// - the gateway at S against 4S, while the provider holds its answer: at most a quarter chunk;
// - the gateway while it uploads: at most 1.05·S + 0.25 MiB (the bytes, once);
// - N against 2N calls held at once: at most 0.25 MiB more in the gateway per call;
// - the pool: a call behind a whole-pool upload waits and then lands; one held past the
//   pool's wait is refused `busy`.
// Exports `egressLeg()`, which answers its rows and problems.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { randomBytes } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OWNER = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(join(OWNER, "package.json"));
const MIB = 1024 * 1024;
const CHUNK = 4 * MIB;
const DOORS = 8921;
const GATEWAY = 8922;
const UPSTREAM = 8923;
const DRIVER = 8924;
const INSPECT = 9421;
/** The pool's wait (`SLOT_WAIT_MS`), and a hold past it. */
const SLOT_WAIT_MS = 10_000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A model call's JSON of `bytes` bytes: one `—` (three bytes) in each 4 KiB, the worst shape. */
function body(model, bytes) {
  const head = JSON.stringify({ model, max_tokens: 50, stream: true,
                                messages: [{ role: "user", content: "" }] });
  const room = bytes - Buffer.byteLength(head);
  const piece = `${"a".repeat(4093)}—`;
  let text = piece.repeat(Math.floor(room / 4096));
  text += "a".repeat(room - Buffer.byteLength(text));
  return head.replace("\"content\":\"\"", `"content":"${text}"`);
}

export async function egressLeg() {
  const rows = [];
  const problems = [];
  const work = mkdtempSync(join(tmpdir(), "egress-memory-"));
  const { build } = require("esbuild");
  await build({ entryPoints: [join(OWNER, "memory", "egress-probe.ts")], bundle: true,
                format: "esm", platform: "neutral", target: "es2022",
                conditions: ["workerd", "worker", "browser"], mainFields: ["module", "main"],
                outfile: join(work, "probe.js"), external: ["cloudflare:*", "node:*"],
                logLevel: "error" });
  const keyring = JSON.stringify({ active: "k1", keys: { k1: randomBytes(32).toString("base64") } });
  const worker = (role, extra = "", flags = "\"nodejs_compat\"") => `(
  modules = [ (name = "probe.js", esModule = embed "probe.js") ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = [${flags}],
  bindings = [ (name = "PROBE_ROLE", text = "${role}"), (name = "DRIVER", service = "driver")${extra} ],
)`;
  writeFileSync(join(work, "config.capnp"), `using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (
  v8Flags = ["--expose-gc"],
  services = [ (name = "doors", worker = .doors), (name = "gateway", worker = .gateway),
               (name = "upstream", worker = .upstream),
               (name = "driver", external = (address = "127.0.0.1:${DRIVER}", http = ())) ],
  sockets = [ (name = "d", address = "127.0.0.1:${DOORS}", http = (), service = "doors"),
              (name = "g", address = "127.0.0.1:${GATEWAY}", http = (), service = "gateway"),
              (name = "u", address = "127.0.0.1:${UPSTREAM}", http = (), service = "upstream") ],
);
const doors :Workerd.Worker = ${worker("doors",
    `, (name = "EGRESS", service = (name = "gateway", entrypoint = "EgressGateway")), ` +
    `(name = "ROUTING_MAC_KEY", text = "${randomBytes(32).toString("base64url")}")`,
    "\"nodejs_compat\", \"disable_ctx_exports\", \"enable_request_signal\"")};
const gateway :Workerd.Worker = (
  modules = [ (name = "probe.js", esModule = embed "probe.js") ],
  compatibilityDate = "2026-10-23",
  compatibilityFlags = ["nodejs_compat", "nodejs_compat_do_not_populate_process_env",
                        "disable_ctx_exports"],
  bindings = [ (name = "PROBE_ROLE", text = "gateway"), (name = "DRIVER", service = "driver"),
               (name = "GATEWAY_KEYRING", text = "${keyring.replaceAll("\"", "\\\"")}") ],
  globalOutbound = "upstream",
);
const upstream :Workerd.Worker = ${worker("upstream")};
`);

  // The checkpoints: a sample is taken when as many calls wait at the sampled phase as the
  // case runs at once, all of them released together after it.
  let mode = null;
  let waiting = [];
  let seen = [];
  const driver = createServer((request, response) => {
    const url = new URL(request.url, "http://driver");
    const phase = url.searchParams.get("phase");
    const call = url.searchParams.get("call");
    seen.push(`${phase}:${call}`);
    if (mode === null || mode.phase !== phase) {
      if (mode?.hold !== undefined && phase === "upload" && call === mode.hold.call
          && !mode.hold.done) {
        mode.hold.done = true;
        setTimeout(() => response.end("go"), mode.hold.ms);
        return;
      }
      response.end("go");
      return;
    }
    waiting.push(response);
    if (waiting.length >= mode.n) {
      const released = waiting;
      waiting = [];
      mode.sample().then(() => released.forEach((each) => each.end("go")),
                         (error) => { problems.push(String(error)); released.forEach((each) =>
                           each.end("go")); });
    }
  });
  await new Promise((resolve) => driver.listen(DRIVER, "127.0.0.1", resolve));
  const workerd = join(dirname(require.resolve("workerd/package.json")), "bin", "workerd");
  const child = spawn(workerd, ["serve", join(work, "config.capnp"),
                                `--inspector-addr=127.0.0.1:${INSPECT}`],
                      { stdio: ["ignore", "inherit", "inherit"] });
  const sockets = [];
  try {
    let list = [];
    for (let n = 0; n < 100 && list.length < 3; n++) {
      await sleep(100);
      list = await fetch(`http://127.0.0.1:${INSPECT}/json/list`).then((r) => r.json(), () => []);
    }
    const inspect = async (name) => {
      const target = list.find((each) => each.id === name);
      if (target === undefined) {
        throw new Error(`no ${name} isolate among the inspector's targets`);
      }
      const ws = new WebSocket(target.webSocketDebuggerUrl);
      sockets.push(ws);
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
      const call = (method) => new Promise((resolve) => {
        const n = ++id;
        pending.set(n, resolve);
        ws.send(JSON.stringify({ id: n, method, params: {} }));
      });
      await call("Runtime.enable");
      return call;
    };
    const isolates = { doors: { call: await inspect("doors"), port: DOORS },
                       gateway: { call: await inspect("gateway"), port: GATEWAY } };
    const live = async (name) => {
      await fetch(`http://127.0.0.1:${isolates[name].port}/gc`).then((r) => r.text());
      const usage = await isolates[name].call("Runtime.getHeapUsage");
      return usage.usedSize + (usage.backingStorageSize ?? 0);
    };
    const { blob, model } = await (await fetch(`http://127.0.0.1:${GATEWAY}/seal`)).json();
    const { token, path } = await (await fetch(`http://127.0.0.1:${DOORS}/token`)).json();
    const send = (bytes, call, to = path) => fetch(`http://127.0.0.1:${DOORS}${to}`, {
      method: "POST", body: Buffer.from(body(model, bytes)),
      headers: { "content-type": "application/json", "x-ta-sandbox": token,
                 "x-ta-network-generation": "1", "x-ta-probe-blob": blob,
                 "x-probe-call": String(call), "x-api-key": "ta_ph_placeholder" } });
    /** The most each isolate holds over its floor at the phase's samples, `n` calls at once. */
    const peak = async (phase, bytes, n = 1, to = path) => {
      const floor = { doors: Math.min(await live("doors"), await live("doors")),
                      gateway: Math.min(await live("gateway"), await live("gateway")) };
      const most = { doors: 0, gateway: 0, samples: 0 };
      mode = { phase, n, sample: async () => {
        most.samples += 1;
        most.doors = Math.max(most.doors, await live("doors") - floor.doors);
        most.gateway = Math.max(most.gateway, await live("gateway") - floor.gateway);
      } };
      const answers = await Promise.all(Array.from({ length: n }, (_, call) =>
        send(bytes, call, to)));
      for (const answer of answers) {
        const text = await answer.text();
        if (!answer.ok) {
          throw new Error(`${phase} ${bytes} bytes × ${n}: ${answer.status} ${text}`);
        }
      }
      mode = null;
      if (most.samples === 0) {
        throw new Error(`${phase} ${bytes} bytes × ${n}: no checkpoint was sampled`);
      }
      return most;
    };
    const check = (name, value, most, what) => {
      rows.push({ check: name, mib: +(value / MIB).toFixed(2), most_mib: +(most / MIB).toFixed(2),
                  ok: value <= most });
      if (value > most) {
        problems.push(`${name}: ${value} bytes, past ${what} (${most})`);
      }
    };
    const S = 4 * MIB - 1024;
    const S4 = 16 * MIB - 1024;
    // The control: a door that reads the body whole must read as holding it.
    const hoarded = (await peak("held", S4, 1, "/hoard")).doors
      - (await peak("held", S, 1, "/hoard")).doors;
    rows.push({ check: "control: the door reading 16 against 4 MiB whole", mib: +(hoarded / MIB)
      .toFixed(2), least_mib: 9, ok: hoarded >= 9 * MIB });
    if (hoarded < 9 * MIB) {
      problems.push(`the control kept 12 MiB more and read ${hoarded} bytes more: the leg ` +
                    "cannot see what the door holds");
    }
    const small = await peak("held", S);
    const large = await peak("held", S4);
    check("doors, held: peak(16 MiB) - peak(4 MiB)", large.doors - small.doors, CHUNK / 4,
          "a quarter chunk");
    check("gateway, held: peak(16 MiB) - peak(4 MiB)", large.gateway - small.gateway, CHUNK / 4,
          "a quarter chunk");
    for (const bytes of [S, S4]) {
      const upload = await peak("upload", bytes);
      check(`gateway, uploading ${Math.round(bytes / MIB)} MiB`, upload.gateway,
            1.05 * bytes + MIB / 4, "1.05·S + 0.25 MiB");
    }
    const few = await peak("held", 256 * 1024, 4);
    const many = await peak("held", 256 * 1024, 8);
    check("gateway, held: (peak(8 calls) - peak(4 calls)) / 4", (many.gateway - few.gateway) / 4,
          MIB / 4, "0.25 MiB a call");
    rows.push({ check: "doors, held: (peak(8 calls) - peak(4 calls)) / 4, reported",
                mib: +((many.doors - few.doors) / 4 / MIB).toFixed(2) });
    // The pool: a whole-pool call held in its upload; a small call behind it waits, then lands
    // once the upload's last write is taken; held past the pool's wait, it is `busy`.
    for (const [ms, expected] of [[1_000, 200], [SLOT_WAIT_MS + 1_500, 503]]) {
      seen = [];
      mode = { phase: "none", n: 1, sample: async () => undefined,
               hold: { call: "0", ms, done: false } };
      const whole = send(16 * MIB - 1024, 0);
      while (!seen.includes("upload:0")) {
        await sleep(20);
      }
      const pool = await (await fetch(`http://127.0.0.1:${GATEWAY}/pool`)).json();
      const behind = await send(64 * 1024, 1);
      const status = behind.status;
      await behind.text();
      await (await whole).text();
      mode = null;
      const landedAfter = seen.indexOf("held:1") > seen.lastIndexOf("upload:0")
        || seen.indexOf("held:1") === -1;
      rows.push({ check: `pool: a call behind a whole-pool upload held ${ms} ms`, status,
                  expected, held_slots: pool.held, ok: status === expected && pool.held === 16
                    && landedAfter });
      if (status !== expected || pool.held !== 16 || !landedAfter) {
        problems.push(`pool: behind a whole-pool upload held ${ms} ms the call answered ` +
                      `${status}, not ${expected} (slots held ${pool.held}; order ${seen})`);
      }
    }
  } catch (error) {
    problems.push(String(error?.stack ?? error));
  } finally {
    sockets.forEach((ws) => ws.close());
    child.kill("SIGKILL");
    driver.close();
    rmSync(work, { recursive: true, force: true });
  }
  return { rows, problems };
}
