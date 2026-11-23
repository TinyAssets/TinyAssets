// The egress memory leg (egress shape research §5, "The memory leg"):
// the real doors and the real gateway, each bundled as a deploy bundles it (`ci/workers.ts`
// `bundle`) and run in its own isolate of this leg's own workerd, with `--expose-gc`, beside
// the leg's own Worker (`leg-workers.ts`): the doors' owner object and control record, and the
// gateway's provider, which awaits this driver at each MiB of a body it reads and once the
// body has arrived. Nothing of the leg runs inside the doors or the gateway: each isolate is
// collected (a swept full collection) and read (`usedSize + backingStorageSize`) through its
// inspector, while it waits. Its own workerd, not miniflare: the gateway's outbound is wired
// straight to the provider, where miniflare's outbound path holds a second copy of an upload.
// Then:
// - the control holds: 11.25 MiB put in each isolate through its inspector reads as at least
//   9 MiB more, or the leg cannot see what the isolate holds;
// - the door at S against 4S (3.75 against 15 MiB, the most a body with a non-ASCII
//   character may be), while the provider holds its answer: at most a quarter chunk;
// - the gateway at S against 4S, while the provider holds its answer: at most a quarter chunk;
// - the gateway while it uploads: at most 1.05·S + 0.25 MiB (the bytes, once);
// - N against 2N calls held at once: at most 0.25 MiB more in the gateway per call;
// - the parse's weight: for eleven body shapes (non-ASCII, `\u`-escaped and
//   ASCII text, and those whose trees cost most a token: empty objects and arrays, unique
//   keys, nesting a million deep), the gateway's live set paused right after the metered
//   path's parse, its stringify and its encode (`forward.ts` `serialized` and `prepared`,
//   each pause found by its statement's text in the bundle), over a 16 KiB call's there, is
//   at most the weight (`held.ts`) its slots were taken for over that call's, each read from
//   the paused call; an under-count fails the leg;
// - the pool: a call behind a whole-pool upload waits and then lands; one held past the
//   pool's wait is refused `busy`.
// Run: `npm run test:memory -w gateway`. Exits non-zero on any failed check, printing every
// figure.
import { randomUUID } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer, request } from "node:http";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";

import { installed } from "../../ci/deps.ts";
import { keyring as newKeyring } from "../../ci/fixtures.ts";
import { capnpModules, freePorts, ownWorkerd, WORKERD } from "../../ci/own-workerd.mjs";
import { bundle, workerConfig } from "../../ci/workers.ts";
import { CHUNK_BYTES, SLOT_WAIT_MS } from "../../contract/src/limits.ts";

installed();
const GATEWAY = dirname(dirname(fileURLToPath(import.meta.url)));
const DOORS = join(dirname(GATEWAY), "doors");
const require = createRequire(join(GATEWAY, "package.json"));
const MIB = 1024 * 1024;
const CHUNK = CHUNK_BYTES;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/**
 * The statements of the metered path (`forward.ts`) the parse cases pause at, in its order,
 * each by its function: the one right after a step, so the step's input and what it made are
 * both held. A name may carry the number the bundler adds to tell names apart (`text3`).
 */
const PAUSES = [["parse", "serialized", /(?<![.\w])read\d*\.text = null;/g],
                ["stringify", "serialized", /(?<![.\w])return \{ text\b/g],
                ["encode", "prepared", /(?<![.\w])return \{ upload: uploaded\d*\(/g]];
/** The weighed body's holder in `prepared`, whose weight the parse cases are held to. */
const READ = /(?<![.\w])const (read\d*) = await weighedBody\d*\(/g;

/**
 * The parse's body shapes (the weight's fit, `held.ts`), each a model call for `model`
 * about `bytes` long: text with a non-ASCII character and ASCII text, and the shapes whose
 * trees cost most a token.
 */
function shapes(model, bytes) {
  const list = (make) => {
    const parts = [];
    let size = 0;
    for (let i = 0; size < bytes - 64; i++) {
      const item = make(i);
      parts.push(item);
      size += item.length + 1;
    }
    return `{"model":"${model}","messages":[${parts.join(",")}]}`;
  };
  const depth = Math.floor((bytes - 40 - model.length) / 2);
  return [
    ["mixed", JSON.stringify({ model, messages: [{ role: "user",
      content: `${"a".repeat(4093)}—`.repeat(Math.floor(bytes / 4100)) }] })],
    // The same text as Python's `json.dumps` writes it: every byte ASCII, each em dash a
    // `—` escape the parse makes two-byte.
    ["escaped", JSON.stringify({ model, messages: [{ role: "user",
      content: `${"a".repeat(4088)}—`.repeat(Math.floor(bytes / 4100)) }] })
      .replaceAll("—", "\\u2014")],
    ["ascii", JSON.stringify({ model, messages: [{ role: "user",
      content: "a".repeat(bytes - 60 - model.length) }] })],
    ["small messages", list(() => '{"role":"user","content":"a"}')],
    ["unique strings", list((i) => `"s${i}"`)],
    ["zeros", list(() => "0")],
    ["empty objects", list(() => "{}")],
    ["empty arrays", list(() => "[]")],
    ["unique-key objects", list((i) => `{"k${i}":0}`)],
    ["nested pairs", list(() => "[[]]")],
    ["deep", `{"model":"${model}","x":${"[".repeat(depth)}${"]".repeat(depth)}}`],
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

/**
 * Where in the gateway bundle's `text` each statement `PAUSES` names is (its line and column),
 * and the name `prepared` holds the weighed body by (`READ`). Each is in its function exactly
 * once, after the statements before it there, or the leg fails, so a change to the metered
 * path breaks the leg rather than move a sample silently.
 */
function pausesIn(text) {
  const bodies = new Map();
  const bodyOf = (name) => {
    if (!bodies.has(name)) {
      const found = [...text.matchAll(new RegExp(`^async function ${name}\\d*\\(`, "gm"))];
      if (found.length !== 1) {
        throw new Error(`the gateway bundle holds ${found.length} \`async function ${name}(\``);
      }
      const start = found[0].index;
      const end = text.slice(start + 1).search(/^(?:async )?function /m) + start + 1;
      bodies.set(name, { start, body: text.slice(start, end), after: 0 });
    }
    return bodies.get(name);
  };
  const once = (name, statement) => {
    const at = bodyOf(name);
    const hits = [...at.body.matchAll(statement)];
    if (hits.length !== 1 || hits[0].index < at.after) {
      throw new Error(`the gateway bundle's \`${name}\` holds ${statement} ${hits.length} ` +
                      "times, or before the statements before it");
    }
    at.after = hits[0].index;
    return { hit: hits[0], before: text.slice(0, at.start + hits[0].index) };
  };
  const read = once("prepared", READ).hit[1];
  return { read, pauses: PAUSES.map(([phase, name, statement]) => {
    const { before } = once(name, statement);
    return { phase, lineNumber: before.split("\n").length - 1,
             columnNumber: before.length - before.lastIndexOf("\n") - 1 };
  }) };
}

/**
 * A request to the leg's workerd at `port`, as `fetch` makes one, but with its own `host`:
 * the doors route by origin, and Node's `fetch` sends the address it dials as the host.
 */
function requested(port, at, { method = "GET", headers = {}, body: sent } = {}) {
  return new Promise((resolve, reject) => {
    const outgoing = request({ host: "127.0.0.1", port, path: at, method,
                               headers: sent === undefined ? headers
                                 : { ...headers, "content-length": String(sent.byteLength) } },
                             (incoming) => resolve(new Response(Readable.toWeb(incoming), {
                               status: incoming.statusCode,
                               headers: Object.entries(incoming.headers)
                                 .flatMap(([name, value]) => [value].flat()
                                   .map((each) => [name, each])) })));
    outgoing.on("error", reject);
    outgoing.end(sent);
  });
}

const [DOORS_PORT, LEG, DRIVER, INSPECT] = await freePorts(4);
const rows = [];
const problems = [];
const work = mkdtempSync(join(tmpdir(), "egress-memory-"));
const { build } = require("esbuild");
await build({ entryPoints: [join(GATEWAY, "memory", "leg-workers.ts")], bundle: true,
              format: "esm", platform: "neutral", target: "es2022",
              conditions: ["workerd", "worker", "browser"], mainFields: ["module", "main"],
              outfile: join(work, "leg", "leg.js"), external: ["cloudflare:*", "node:*"],
              logLevel: "error" });
const built = {};
for (const [name, dir] of [["doors", DOORS], ["gateway", GATEWAY]]) {
  mkdirSync(join(work, name));
  built[name] = await bundle(dir, join(work, name));
}
const doors = workerConfig(DOORS);
const gateway = workerConfig(GATEWAY);
const flags = (config) => (config.compatibility_flags ?? []).map((flag) => `"${flag}"`)
  .join(", ");
const routing = JSON.stringify(newKeyring());
writeFileSync(join(work, "config.capnp"), `using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (
  v8Flags = ["--expose-gc"],
  services = [ (name = "doors", worker = .doors), (name = "gateway", worker = .gateway),
               (name = "leg", worker = .leg),
               (name = "driver", external = (address = "127.0.0.1:${DRIVER}", http = ())) ],
  sockets = [ (name = "d", address = "127.0.0.1:${DOORS_PORT}", service = "doors",
               http = (forwardedProtoHeader = "x-forwarded-proto")),
              (name = "l", address = "127.0.0.1:${LEG}", http = (), service = "leg") ],
);
const doors :Workerd.Worker = (
  modules = [ ${capnpModules("doors", built.doors)} ],
  compatibilityDate = "${doors.compatibility_date}",
  compatibilityFlags = [${flags(doors)}],
  bindings = [
    (name = "ROUTING_KEYRING", text = ${routing}),
    (name = "CONTROL", kvNamespace = (name = "leg", entrypoint = "Control")),
    (name = "OWNER", durableObjectNamespace = (className = "LegOwner", serviceName = "leg")),
    (name = "EGRESS", service = (name = "gateway", entrypoint = "EgressGateway")),
    (name = "${doors.version_metadata.binding}", json = ${JSON.stringify(JSON.stringify(
      { id: randomUUID(), tag: "", timestamp: "0" }))}) ],
);
const gateway :Workerd.Worker = (
  modules = [ ${capnpModules("gateway", built.gateway)} ],
  compatibilityDate = "${gateway.compatibility_date}",
  compatibilityFlags = [${flags(gateway)}],
  bindings = [ (name = "GATEWAY_KEYRING", text = "${newKeyring().replaceAll("\"", "\\\"")}") ],
  globalOutbound = (name = "leg", entrypoint = "Provider"),
);
const leg :Workerd.Worker = (
  modules = [ (name = "leg.js", esModule = embed "leg/leg.js") ],
  compatibilityDate = "${gateway.compatibility_date}",
  compatibilityFlags = ["nodejs_compat"],
  durableObjectNamespaces = [ (className = "LegOwner", uniqueKey = "egress-memory-leg") ],
  durableObjectStorage = (inMemory = void),
  bindings = [
    (name = "DRIVER", service = "driver"),
    (name = "CONNECT", service = (name = "gateway", entrypoint = "ConnectGateway")),
    (name = "OWNER", durableObjectNamespace = "LegOwner"),
    (name = "ROUTING_KEYRING", text = ${routing}) ],
);
`);

// The checkpoints: a sample is taken when as many calls wait at the sampled phase as the
// case runs at once, all of them released together after it.
let mode = null;
let waiting = [];
let seen = [];
const driver = createServer((incoming, answer) => {
  const url = new URL(incoming.url, "http://driver");
  const phase = url.searchParams.get("phase");
  const call = url.searchParams.get("call");
  seen.push(`${phase}:${call}`);
  if (mode === null || !mode.phases.includes(phase)) {
    if (mode?.hold !== undefined && phase === "upload" && call === mode.hold.call
        && !mode.hold.done) {
      mode.hold.done = true;
      setTimeout(() => answer.end("go"), mode.hold.ms);
      return;
    }
    answer.end("go");
    return;
  }
  waiting.push(answer);
  if (waiting.length >= mode.n) {
    const released = waiting;
    waiting = [];
    mode.sample(phase).then(() => released.forEach((each) => each.end("go")),
                            (error) => { problems.push(String(error)); released.forEach((each) =>
                              each.end("go")); });
  }
});
await new Promise((resolve) => driver.listen(DRIVER, "127.0.0.1", resolve));
// This leg's own workerd: its early exit fails the leg, and any end of the leg kills it.
const own = ownWorkerd(WORKERD, ["serve", join(work, "config.capnp"),
                                 `--inspector-addr=127.0.0.1:${INSPECT}`],
                       { onEarlyExit: () => rmSync(work, { recursive: true, force: true }) });
const sockets = [];
try {
  let list = [];
  for (let n = 0; n < 100 && list.length < 3; n++) {
    await sleep(100);
    list = await fetch(`http://127.0.0.1:${INSPECT}/json/list`).then((r) => r.json(), () => []);
  }
  /**
   * An isolate's inspector: its calls, the events it has sent, the next event of a kind it
   * will send (`next`, asked before what sends it), and its context's id.
   */
  const inspect = async (name) => {
    const target = list.find((each) => each.id === name);
    if (target === undefined) {
      throw new Error(`no ${name} isolate among the inspector's targets`);
    }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    sockets.push(ws);
    let id = 0;
    const pending = new Map();
    const events = [];
    const awaited = new Set();
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id === undefined) {
        events.push(message);
        awaited.forEach((each) => each(message));
      } else {
        pending.get(message.id)?.(message);
        pending.delete(message.id);
      }
    };
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const n = ++id;
      pending.set(n, (message) => message.error === undefined ? resolve(message.result)
        : reject(new Error(`${name} ${method}: ${message.error.message}`)));
      ws.send(JSON.stringify({ id: n, method, params }));
    });
    const next = (method) => new Promise((resolve) => {
      const each = (message) => {
        if (message.method === method) {
          awaited.delete(each);
          resolve(message.params);
        }
      };
      awaited.add(each);
    });
    const created = next("Runtime.executionContextCreated");
    await call("Runtime.enable");
    return { call, events, next, context: (await created).context.id };
  };
  const isolates = { doors: await inspect("doors"), gateway: await inspect("gateway") };
  own.check();
  /** Every request to this leg's workerd: answered, then its workerd confirmed alive. */
  const ask = async (port, at, init) => {
    const answer = await requested(port, at, init);
    own.check();
    return answer;
  };
  /**
   * An expression run in the isolate's context, paused or not (never in a paused frame: an
   * evaluation there keeps the frame's values past it), its value by value.
   */
  const run = async (name, expression) => {
    const { call, context } = isolates[name];
    const { result, exceptionDetails } = await call("Runtime.evaluate", {
      expression, contextId: context, returnByValue: true });
    if (exceptionDetails !== undefined) {
      throw new Error(`${name}: ${expression}: ${exceptionDetails.exception?.description}`);
    }
    return result.value;
  };
  /**
   * The isolate's live set: a full collection, a moment for the freed backing stores to be
   * swept, then another, and its heap and backing stores read.
   */
  const live = async (name) => {
    await run(name, "gc()");
    await sleep(5);
    await run(name, "gc()");
    const usage = await isolates[name].call("Runtime.getHeapUsage");
    return usage.usedSize + (usage.backingStorageSize ?? 0);
  };
  const { model } = await (await ask(LEG, "/seal")).json();
  const { host, path, token } = await (await ask(LEG, "/token")).json();
  const send = (text, call) => ask(DOORS_PORT, path, {
    method: "POST", body: Buffer.from(text),
    headers: { host, "x-forwarded-proto": "https", "content-type": "application/json",
               "x-ta-sandbox": token, "x-ta-network-generation": "1",
               "x-probe-call": String(call), "x-api-key": "ta_ph_placeholder" } });
  /** The most each isolate holds over its floor at the phase's samples, `n` calls at once. */
  const peak = async (phase, bytes, n = 1) => {
    const floor = { doors: Math.min(await live("doors"), await live("doors")),
                    gateway: Math.min(await live("gateway"), await live("gateway")) };
    const most = { doors: 0, gateway: 0, samples: 0 };
    mode = { phases: [phase], n, sample: async () => {
      most.samples += 1;
      most.doors = Math.max(most.doors, await live("doors") - floor.doors);
      most.gateway = Math.max(most.gateway, await live("gateway") - floor.gateway);
    } };
    const answers = await Promise.all(Array.from({ length: n }, (_, call) =>
      send(body(model, bytes), call)));
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
  // The control: what an isolate is made to hold must read as held, or the leg proves nothing.
  for (const name of ["doors", "gateway"]) {
    const before = Math.min(await live(name), await live(name));
    await run(name, `globalThis.legControl = new Uint8Array(${S4 - S}).fill(1), 0`);
    const held = await live(name) - before;
    await run(name, "delete globalThis.legControl");
    rows.push({ check: `control: the ${name} isolate made to hold 11.25 MiB`,
                mib: +(held / MIB).toFixed(2), least_mib: 9, ok: held >= 9 * MIB });
    if (held < 9 * MIB) {
      problems.push(`the ${name} control held 11.25 MiB and read ${held} bytes more: the leg ` +
                    "cannot see what the isolate holds");
    }
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
  // The parse's weight: for each shape, the gateway's live set paused right after
  // its parse, its stringify and its encode is at most the weight it took slots for.
  const gw = isolates.gateway;
  await gw.call("Debugger.enable");
  const script = gw.events.find((each) => each.method === "Debugger.scriptParsed"
    && each.params.url.endsWith(built.gateway[0].file));
  if (script === undefined) {
    throw new Error(`the gateway's inspector names no script ${built.gateway[0].file}`);
  }
  const { scriptSource } = await gw.call("Debugger.getScriptSource",
                                         { scriptId: script.params.scriptId });
  const { read, pauses } = pausesIn(scriptSource);
  for (const pause of pauses) {
    await gw.call("Debugger.setBreakpoint", { location: {
      scriptId: script.params.scriptId, lineNumber: pause.lineNumber,
      columnNumber: pause.columnNumber } });
  }
  /** A local's own properties, by name, in a scope or an object the paused isolate names. */
  const properties = async (objectId) => new Map(
    (await gw.call("Runtime.getProperties", { objectId, ownProperties: true })).result
      .map((each) => [each.name, each.value]));
  /**
   * `text` sent as a call, `sample` run at each of its pauses with the paused frames; its
   * answer. A call answered before its last pause was refused on its way, and fails the leg.
   */
  const through = async (name, text, sample) => {
    let paused = gw.next("Debugger.paused");
    const answered = send(text, name);
    const early = answered.then((answer) => {
      throw new Error(`gateway, ${name}: answered ${answer.status} before its pauses`);
    });
    for (const { phase, lineNumber } of pauses) {
      // Where it paused, by the frame's own line: `hitBreakpoints` can name another of them.
      const { callFrames } = await Promise.race([paused, early]);
      const at = callFrames[0].location.lineNumber;
      if (at !== lineNumber) {
        throw new Error(`gateway, ${name}: paused at line ${at}, not its ${phase}'s (` +
                        `${lineNumber})`);
      }
      await sample(phase, callFrames);
      paused = gw.next("Debugger.paused");
      await gw.call("Debugger.resume");
    }
    return answered;
  };
  // A first call through every pause, unsampled: the debugger's own records of the script,
  // made at its first pause, are then in every floor.
  await (await through("warm", body(model, 64 * 1024), async () => undefined)).text();
  /** A call's live set over its floor at each pause, and what its slots were taken for. */
  const sampled = async (name, text) => {
    const floor = Math.min(await live("gateway"), await live("gateway"));
    const at = {};
    let weight = null;
    let bytes = null;
    const answer = await through(name, text, async (phase, callFrames) => {
      at[phase] = await live("gateway") - floor;
      if (phase === "encode") {
        // The weight the call's slots were taken for, from `prepared`'s own holder, read
        // after the last sample.
        const local = callFrames[0].scopeChain.find((each) => each.type === "local");
        const holder = (await properties(local.object.objectId)).get(read);
        const fields = await properties(holder.objectId);
        weight = fields.get("weight").value;
        bytes = fields.get("bytes").value;
      }
    });
    await answer.text();
    return { at, weight, bytes, status: answer.status };
  };
  // Each shape against a 16 KiB call at the same pause, held to the difference of their
  // weights, as the leg's other rows are differences: what any call in flight holds whatever
  // its body (measured: 0.24 to 0.36 MiB at these pauses, beside 0.06 MiB of body) is the
  // isolate's in-flight set, not the body's, and the weight prices the body (`held.ts`).
  const base = await sampled("base", body(model, 16 * 1024));
  for (const [name, text] of shapes(model, 2 * MIB)) {
    const call = await sampled(name, text);
    const over = Object.entries(call.at).map(([phase, now]) => [phase, now - base.at[phase]]);
    const most = Math.max(...over.map(([, each]) => each));
    const weight = call.weight - base.weight;
    const status = call.status;
    const row = { check: `gateway, ${name}: held after its parse, its stringify, its encode`,
                  mib: +(most / MIB).toFixed(2), weight_mib: +(weight / MIB).toFixed(2),
                  times_body: +(most / (call.bytes - base.bytes)).toFixed(2),
                  phases_mib: Object.fromEntries(over.map(([phase, each]) =>
                    [phase, +(each / MIB).toFixed(2)])), status,
                  ok: most <= weight && status === 200 };
    rows.push(row);
    if (!row.ok) {
      problems.push(`${row.check}: ${most} bytes against its weight (${weight}), answered ` +
                    `${status}`);
    }
  }
  await gw.call("Debugger.disable");
  // The pool: a whole-pool call held in its upload; a small call behind it waits, then lands
  // once the upload's last write is taken; held past the pool's wait, it is `busy`.
  for (const [ms, expected] of [[1_000, 200], [SLOT_WAIT_MS + 1_500, 503]]) {
    seen = [];
    mode = { phases: [], n: 1, sample: async () => undefined,
             hold: { call: "0", ms, done: false } };
    const whole = send(body(model, S4), 0);
    while (!seen.includes("upload:0")) {
      await sleep(20);
    }
    const behind = await send(body(model, 64 * 1024), 1);
    const status = behind.status;
    await behind.text();
    await (await whole).text();
    mode = null;
    const landedAfter = seen.indexOf("held:1") > seen.lastIndexOf("upload:0")
      || seen.indexOf("held:1") === -1;
    rows.push({ check: `pool: a call behind a whole-pool upload held ${ms} ms`, status,
                expected, ok: status === expected && landedAfter });
    if (status !== expected || !landedAfter) {
      problems.push(`pool: behind a whole-pool upload held ${ms} ms the call answered ` +
                    `${status}, not ${expected} (order ${seen})`);
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
console.log(JSON.stringify({ egress: rows }, null, 1));
if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
