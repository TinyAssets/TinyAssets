// The memory leg's egress cases (egress shape research §5, "The memory leg"), run by `leg.mjs`
// after the owner's paths: the real egress door and the real gateway, each in its own
// isolate, a provider in a third (`egress-probe.ts`). The provider awaits the driver at each
// MiB of a body it reads and once the body has arrived; there the driver collects the door's
// and the gateway's isolates and reads `usedSize + backingStorageSize` while both wait. Then:
// - the control holds: the door that reads a body whole reads as growing;
// - the door at S against 4S (3.75 against 15 MiB, the most a body with a non-ASCII
//   character may be), while the provider holds its answer: at most a quarter chunk;
// - the gateway at S against 4S, while the provider holds its answer: at most a quarter chunk;
// - the gateway while it uploads: at most 1.05·S + 0.25 MiB (the bytes, once);
// - N against 2N calls held at once: at most 0.25 MiB more in the gateway per call;
// - the parse's weight (review H1): for eleven body shapes (non-ASCII, `\u`-escaped and
//   ASCII text, and those whose trees cost most a token: empty objects and arrays, unique
//   keys, nesting two million deep), the gateway's live set right after the parse, the
//   stringify and the encode is at most the weight (`held.ts`) its slots were taken for; an under-count fails the leg;
// - the pool: a call behind a whole-pool upload waits and then lands; one held past the
//   pool's wait is refused `busy`.
// Exports `egressLeg()`, which answers its rows and problems.
import { createServer } from "node:http";
import { randomBytes } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { keyring as newKeyring } from "../../ci/fixtures.ts";
import { freePorts } from "./ports.mjs";
import { ownWorkerd } from "./workerd.mjs";

const OWNER = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(join(OWNER, "package.json"));
const MIB = 1024 * 1024;
const CHUNK = 4 * MIB;
/** The pool's wait (`SLOT_WAIT_MS`), and a hold past it. */
const SLOT_WAIT_MS = 10_000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The parse's body shapes (review H1 and its fit, `held.ts`): text with a non-ASCII character
 * and ASCII text, and the shapes whose trees cost most a token, each about `bytes` long.
 */
function shapes(bytes) {
  const list = (make) => {
    const parts = [];
    let size = 0;
    for (let i = 0; size < bytes - 64; i++) {
      const item = make(i);
      parts.push(item);
      size += item.length + 1;
    }
    return `{"model":"m","messages":[${parts.join(",")}]}`;
  };
  const depth = Math.floor((bytes - 40) / 2);
  return [
    ["mixed", JSON.stringify({ model: "m", messages: [{ role: "user",
      content: `${"a".repeat(4093)}\u2014`.repeat(Math.floor(bytes / 4100)) }] })],
    // The same text as Python's `json.dumps` writes it: every byte ASCII, each em dash a
    // `\u2014` escape the parse makes two-byte (review V-H1).
    ["escaped", JSON.stringify({ model: "m", messages: [{ role: "user",
      content: `${"a".repeat(4088)}\u2014`.repeat(Math.floor(bytes / 4100)) }] })
      .replaceAll("\u2014", "\\u2014")],
    ["ascii", JSON.stringify({ model: "m", messages: [{ role: "user",
      content: "a".repeat(bytes - 60) }] })],
    ["small messages", list(() => '{"role":"user","content":"a"}')],
    ["unique strings", list((i) => `"s${i}"`)],
    ["zeros", list(() => "0")],
    ["empty objects", list(() => "{}")],
    ["empty arrays", list(() => "[]")],
    ["unique-key objects", list((i) => `{"k${i}":0}`)],
    ["nested pairs", list(() => "[[]]")],
    ["deep", `{"model":"m","x":${"[".repeat(depth)}${"]".repeat(depth)}}`],
  ];
}

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
  const [DOORS, GATEWAY, UPSTREAM, DRIVER, INSPECT] = await freePorts(5);
  const rows = [];
  const problems = [];
  const work = mkdtempSync(join(tmpdir(), "egress-memory-"));
  const { build } = require("esbuild");
  await build({ entryPoints: [join(OWNER, "memory", "egress-probe.ts")], bundle: true,
                format: "esm", platform: "neutral", target: "es2022",
                conditions: ["workerd", "worker", "browser"], mainFields: ["module", "main"],
                outfile: join(work, "probe.js"), external: ["cloudflare:*", "node:*"],
                logLevel: "error" });
  const keyring = newKeyring();
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
    if (mode === null || !mode.phases.includes(phase)) {
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
      mode.sample(phase).then(() => released.forEach((each) => each.end("go")),
                         (error) => { problems.push(String(error)); released.forEach((each) =>
                           each.end("go")); });
    }
  });
  await new Promise((resolve) => driver.listen(DRIVER, "127.0.0.1", resolve));
  const workerd = join(dirname(require.resolve("workerd/package.json")), "bin", "workerd");
  // This leg's own workerd: its early exit fails the leg, and any end of the leg kills it.
  const own = ownWorkerd(workerd, ["serve", join(work, "config.capnp"),
                                   `--inspector-addr=127.0.0.1:${INSPECT}`],
                         { onEarlyExit: () => rmSync(work, { recursive: true, force: true }) });
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
    own.check();
    /** Every request to this leg's isolates: answered, then its workerd confirmed alive. */
    const ask = async (port, at, init) => {
      const answer = await fetch(`http://127.0.0.1:${port}${at}`, init);
      own.check();
      return answer;
    };
    const live = async (name) => {
      await ask(isolates[name].port, "/gc").then((r) => r.text());
      const usage = await isolates[name].call("Runtime.getHeapUsage");
      return usage.usedSize + (usage.backingStorageSize ?? 0);
    };
    const { blob, model } = await (await ask(GATEWAY, "/seal")).json();
    const { token, path } = await (await ask(DOORS, "/token")).json();
    const send = (bytes, call, to = path) => ask(DOORS, to, {
      method: "POST", body: Buffer.from(body(model, bytes)),
      headers: { "content-type": "application/json", "x-ta-sandbox": token,
                 "x-ta-network-generation": "1", "x-ta-probe-blob": blob,
                 "x-probe-call": String(call), "x-api-key": "ta_ph_placeholder" } });
    /** The most each isolate holds over its floor at the phase's samples, `n` calls at once. */
    const peak = async (phase, bytes, n = 1, to = path) => {
      const floor = { doors: Math.min(await live("doors"), await live("doors")),
                      gateway: Math.min(await live("gateway"), await live("gateway")) };
      const most = { doors: 0, gateway: 0, samples: 0 };
      mode = { phases: [phase], n, sample: async () => {
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
    // 15 MiB is the most a body with a non-ASCII character in it may be (`held.ts`).
    const S = Math.floor(15 * MIB / 4);
    const S4 = 4 * S;
    // The control: a door that reads the body whole must read as holding it.
    const hoarded = (await peak("held", S4, 1, "/hoard")).doors
      - (await peak("held", S, 1, "/hoard")).doors;
    rows.push({ check: "control: the door reading 15 against 3.75 MiB whole", mib: +(hoarded / MIB)
      .toFixed(2), least_mib: 9, ok: hoarded >= 9 * MIB });
    if (hoarded < 9 * MIB) {
      problems.push(`the control kept 11.25 MiB more and read ${hoarded} bytes more: the leg ` +
                    "cannot see what the door holds");
    }
    const small = await peak("held", S);
    const large = await peak("held", S4);
    check("doors, held: peak(15 MiB) - peak(3.75 MiB)", large.doors - small.doors, CHUNK / 4,
          "a quarter chunk");
    check("gateway, held: peak(15 MiB) - peak(3.75 MiB)", large.gateway - small.gateway, CHUNK / 4,
          "a quarter chunk");
    for (const bytes of [S, S4]) {
      const upload = await peak("upload", bytes);
      check(`gateway, uploading ${(bytes / MIB).toFixed(2)} MiB`, upload.gateway,
            1.05 * bytes + MIB / 4, "1.05·S + 0.25 MiB");
    }
    const few = await peak("held", 256 * 1024, 4);
    const many = await peak("held", 256 * 1024, 8);
    check("gateway, held: (peak(8 calls) - peak(4 calls)) / 4", (many.gateway - few.gateway) / 4,
          MIB / 4, "0.25 MiB a call");
    rows.push({ check: "doors, held: (peak(8 calls) - peak(4 calls)) / 4, reported",
                mib: +((many.doors - few.doors) / 4 / MIB).toFixed(2) });
    // The parse's weight (review H1): for each shape, the gateway's live set right after
    // its parse, its stringify and its encode is at most the weight it took slots for.
    for (const [name, text] of shapes(2 * MIB)) {
      const floor = Math.min(await live("gateway"), await live("gateway"));
      let most = 0;
      const phased = {};
      mode = { phases: ["parse", "stringify", "encode"], n: 1, sample: async (phase) => {
        const now = await live("gateway") - floor;
        phased[phase] = +(now / MIB).toFixed(2);
        most = Math.max(most, now);
      } };
      const answer = await ask(GATEWAY, "/parse", { method: "POST", body: Buffer.from(text) });
      const read = await answer.json();
      mode = null;
      const row = { check: `gateway, ${name}: held after its parse, its stringify, its encode`,
                    mib: +(most / MIB).toFixed(2), weight_mib: +(read.weight / MIB).toFixed(2),
                    times_body: +(most / read.bytes).toFixed(2), phases_mib: phased,
                    ok: most <= read.weight };
      rows.push(row);
      if (most > read.weight) {
        problems.push(`${row.check}: ${most} bytes, past its weight (${read.weight})`);
      }
    }
    // The pool: a whole-pool call held in its upload; a small call behind it waits, then lands
    // once the upload's last write is taken; held past the pool's wait, it is `busy`.
    for (const [ms, expected] of [[1_000, 200], [SLOT_WAIT_MS + 1_500, 503]]) {
      seen = [];
      mode = { phases: [], n: 1, sample: async () => undefined,
               hold: { call: "0", ms, done: false } };
      const whole = send(S4, 0);
      while (!seen.includes("upload:0")) {
        await sleep(20);
      }
      const pool = await (await ask(GATEWAY, "/pool")).json();
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
    own.stop();
    driver.close();
    rmSync(work, { recursive: true, force: true });
  }
  return { rows, problems };
}
