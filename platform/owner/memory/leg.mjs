// The workerd memory leg (ADR-037): each of the owner's real paths that holds chunks, run at
// its worst, holds what it declares, whatever the size of its data, proven by differences.
//
// It bundles `probe.ts` (the platform's own owner object, files and bodies code) and runs it
// in standalone workerd with `--expose-gc`, against an in-memory chunk store in another
// isolate. Checkpoints: every chunk call (at the store, and in the owner isolate before it
// and after a chunk arrives) and the end of every hold, before its release (review M-a).
// At each the path waits on this driver while it collects the owner isolate (a swept full
// collection) and reads `usedSize + backingStorageSize` through the inspector, so a sample
// is the live set, byte arrays included (rpcretain_research.md §4: a read must be atomic with
// its collection); with several paths at once it waits until every one is parked or done.
// The owner isolate's checkpoints say the units its pool holds. Each file's chunks are
// distinct, so a longer edit makes and puts every chunk after its change. Then:
// - the control holds: a path that keeps every chunk reads as growing, or the leg proves
//   nothing;
// - the baseline: the isolate at rest, one owner object built and served, is within
//   `OWNER_BASELINE_BYTES`;
// - each step on its own: at every checkpoint inside a hold, what is live is within the
//   units the pool holds then, beside a quarter chunk of the steps' own objects;
// - O(1) in size: peak(L) - peak(S) is at most a quarter chunk, at sizes past the path's
//   steady state (an edit of 96 against 192 MiB; an append to 8 against 32 MiB; a body's
//   window from 2 against 8 MiB), each the least of two runs;
// - each path's cost: its worst run's peak is within the units it declares (`holds.ts`);
// - the archive (STORE item 2): what a move's records hold at `MOVE_RECORDS` (built by the
//   move's own code: no real mail reaches it) within `moveTableUnits` and 256 bytes a record;
//   the merge of the most records real mail reaches (round 5's), its records within the same
//   at its passes' ends; a tail seal of small messages, a move of
//   two large bodies (4 against 8 MiB each, O(1) in size) and a purge's rewrite of them,
//   each run by the object's own `archive` lane, every pass within its table's and its own
//   units;
// - a generation's burst (slice P): at production sizes, of the smallest lines, run by the
//   real lane, its pages and parts at every part's put within the units it holds
//   (`BURST_UNITS`), an owner's write during a part's put shipping a full segment inside them;
// - a log segment shipped outside a burst: a full segment of wide lines, its build and its
//   put within the units it holds (`SHIP_UNITS`);
// - per unit: two edits at once hold at most one making step's units more than one;
// - its time: the worst edit's time a chunk, measured with no collections, keeps
//   `EDIT_BYTES_MAX` inside a request's 30 s of CPU (the time is wall time, at least the CPU
//   it spent), and each class's full queue turns over within `SLOT_WAIT_MS`.
// Differences cancel the baseline and the probe's own code (review M4); the absolute figure
// is production's (`memoryUsageBytes`). Run: `npm run test:memory -w owner`. Exits non-zero
// on any failed check, printing every figure.
import { createServer } from "node:http";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { egressLeg } from "./egress.mjs";
import { freePorts } from "./ports.mjs";
import { ownWorkerd } from "./workerd.mjs";

const OWNER = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(join(OWNER, "package.json"));
const MIB = 1024 * 1024;
const [PORT, INSPECT, DRIVER] = await freePorts(3);
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
                               enableSql = true),
                              (className = "LegArchive", uniqueKey = "owner-memory-archive",
                               enableSql = true),
                              (className = "LegSegments", uniqueKey = "owner-memory-segments",
                               enableSql = true) ],
  durableObjectStorage = (localDisk = "objects"),
  bindings = [ (name = "OWNER", durableObjectNamespace = "LegOwner"),
               (name = "ARCHIVE", durableObjectNamespace = "LegArchive"),
               (name = "SEGMENTS", durableObjectNamespace = "LegSegments"),
               (name = "FILES", service = (name = "files", entrypoint = "Store")),
               (name = "STORE", service = (name = "files", entrypoint = "Store")),
               (name = "DRIVER", service = "driver"),
               (name = "GATEWAY", service = "dummy"), (name = "CODEMODE_VM", service = "dummy"),
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
// This leg's own workerd: its early exit fails the leg, and any end of the leg kills it.
const own = ownWorkerd(workerd, ["serve", join(work, "config.capnp"),
                                 `--inspector-addr=127.0.0.1:${INSPECT}`],
                       { onEarlyExit: () => rmSync(work, { recursive: true, force: true }) });
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
  own.check();
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
    own.check();
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
    // The units the pool holds, as the last path to park said them: nothing has run since.
    const held = go.at(-1).held;
    const at = go.map((each) => each.at).join("+");
    sampling = sampling.then(async () => {
      if (sample !== null) {
        await sample(held, at);
      }
      for (const { response } of go) {
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
      const query = new URL(request.url, "http://driver").searchParams;
      const held = query.get("held");
      parked.push({ response, held: held === null ? null : Number(held), at: query.get("at") });
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
    // The most a sample in the owner isolate read past the units its pool held then: each
    // step's live set against its own units, the reading steps' among them (review M-a).
    let over = -Infinity;
    active = n;
    let where = "";
    let overAt = "";
    let answer = null;
    // The most live at a hold's end (after its chunks are let go): its records' own.
    let ends = 0;
    // The most units the pool held at a sample.
    let heldMost = 0;
    // The most live while a log segment's ship hold (`SHIP_UNITS`) is what the pool holds.
    let shipMost = 0;
    sample = async (held, at) => {
      points += 1;
      heldMost = Math.max(heldMost, held ?? 0);
      const now = await live() - floor;
      if (held === costs.SHIP_UNITS) {
        shipMost = Math.max(shipMost, now);
      }
      if (now > most) {
        where = `${at} (${held} units)`;
      }
      most = Math.max(most, now);
      if (at?.startsWith("end-of") || at === "move-records") {
        ends = Math.max(ends, now);
      }
      // Inside a hold (outside one, what is live is a call's own arguments, which no unit
      // counts yet: PR 3b).
      if (held !== null && held > 0) {
        // Beside its chunks, a step's own objects (its plan, its hashes, its request): at
        // most the leg's quarter chunk.
        if (now - held * UNIT - QUARTER > over) {
          overAt = `${at} (${held} units, ${(now / MIB).toFixed(2)} MiB)`;
        }
        over = Math.max(over, now - held * UNIT - QUARTER);
      }
    };
    try {
      answer = JSON.parse(await get(`/run?path=${path}&mib=${mib}`));
      await sampling;
    } finally {
      sample = null;
      active = 0;
    }
    // Every chunk seen: an edit reads each chunk to scan it and to hash it, and makes and puts
    // each (its content distinct, none already stored), so at least four calls a chunk.
    const chunks = Math.ceil(mib * MIB / (4 * MIB));
    const least = path === "edit" ? 4 * chunks : path === "hoard" ? chunks
      : path === "bodies" || path === "rewrite" ? 2 * chunks : 1;
    if (points < least) {
      throw new Error(`${path} ${mib} MiB x ${n}: ${points} checkpoints, fewer than ${least}`);
    }
    if (path !== "hoard") {
      check(`${path} ${mib} MiB x ${n}: live past the units held, at every hold's checkpoint`,
            over, 0, "the units its pool held");
      rows.push({ run: `${path} ${mib} MiB x ${n}`, answer, ends_mib: +(ends / MIB).toFixed(2),
                  peak_mib: +(most / MIB).toFixed(2),
                  at: where, closest_to_its_units: overAt });
    }
    peak.ends = ends;
    peak.held = heldMost;
    peak.ship = shipMost;
    peak.answer = answer;
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

  // Each size run twice. Now and then a dead chunk reads live one collection longer (a
  // stale reference the collector cannot yet rule out, never more than one chunk), the more
  // often the more steps a run takes: each path's cost is held to its worst run, and its
  // size-independence read from its least, at sizes where that chunk is seen in every run
  // (96 against 192 MiB), so it is not taken for growth. A path that grows grows in every run.
  const twice = async (path, mib) => {
    const [a, b] = [await peak(path, mib), await peak(path, mib)];
    return { most: Math.max(a, b), least: Math.min(a, b) };
  };
  const edit = { 8: await twice("edit", 8), 32: await twice("edit", 32),
                 96: await twice("edit", 96), 192: await twice("edit", 192) };
  check("edit: peak(192 MiB) - peak(96 MiB), least of two runs",
        edit[192].least - edit[96].least, QUARTER, "a quarter chunk");
  for (const mib of [8, 32, 96, 192]) {
    check(`edit: peak(${mib} MiB), worst of two runs`, edit[mib].most,
          costs.EDIT_MAKE_UNITS * UNIT, "EDIT_MAKE_UNITS");
  }
  const both = await peak("edit", 32, 2);
  check("edit: peak(2 at once) - peak(1), at 32 MiB", both - edit[32].least,
        costs.EDIT_MAKE_UNITS * UNIT, "one making step's units");

  const append = { 8: await twice("append", 8), 32: await twice("append", 32) };
  check("append: peak(32 MiB) - peak(8 MiB), least of two runs",
        append[32].least - append[8].least, QUARTER, "a quarter chunk");
  check("append: peak(32 MiB), worst of two runs", Math.max(append[8].most, append[32].most),
        costs.APPEND_UNITS * UNIT, "APPEND_UNITS");

  const window = { 2: await twice("window", 2), 8: await twice("window", 8) };
  check("window: peak(8 MiB body) - peak(2 MiB body), least of two runs",
        window[8].least - window[2].least, QUARTER, "a quarter chunk");
  check("window: peak(8 MiB body), worst of two runs", window[8].most,
        costs.WINDOW_UNITS * UNIT, "WINDOW_UNITS");

  // The cutover's check (`importCheck`): every chunk read back through the store as a verified
  // range, so the owner isolate holds none of a chunk's bytes, whatever the file's size.
  const checked = { 8: await twice("check", 8), 32: await twice("check", 32) };
  check("check: peak(32 MiB) - peak(8 MiB), least of two runs",
        checked[32].least - checked[8].least, QUARTER, "a quarter chunk");
  check("check: peak(32 MiB), worst of two runs", checked[32].most, QUARTER,
        "a quarter chunk: no chunk's bytes in hand");
  if (peak.answer?.[0] !== 8) {
    problems.push(`check: proved ${JSON.stringify(peak.answer)} chunks of 8`);
  }

  check("fetch: peak(4 MiB body)", await peak("fetch", 4), costs.body_units[0] * UNIT,
        "bodyUnits(1)");
  check("fetch: peak(8 MiB body)", await peak("fetch", 8), costs.body_units[1] * UNIT,
        "bodyUnits(2)");

  // A generation's burst at production sizes, of the smallest lines (the most lines its bytes
  // allow), run by the real lane: at every part's put, its pages and parts within the units
  // it holds (above, at every checkpoint), and its peak within BURST_UNITS (review rvbackup 1).
  check("a generation's burst: peak, the smallest lines", await peak("burst", 0),
        costs.BURST_UNITS * UNIT, "BURST_UNITS");
  // Its log's ships after it (each under `SHIP_UNITS` alone): compared with a ship alone below.
  const burstShips = peak.ship;
  check("a generation's burst: its puts inside its units of the pool",
        costs.BURST_UNITS - peak.held, 0, "the units held at its puts");
  if (peak.answer[0] < 3 * MIB) {
    throw new Error(`the burst case's widest segment was ${peak.answer[0]} bytes, not a full one`);
  }
  // A full log segment shipped outside a burst (its build, then its put), within SHIP_UNITS.
  check("a log segment shipped outside a burst: peak, a full segment of wide lines",
        await peak("ship", 0), costs.SHIP_UNITS * UNIT, "SHIP_UNITS");
  check("a log segment shipped outside a burst: inside its units of the pool",
        costs.SHIP_UNITS - peak.held, 0, "the units held at its checkpoints");
  if (peak.answer[0] < 3 * MIB) {
    throw new Error(`the ship case's widest segment was ${peak.answer[0]} bytes, not a full one`);
  }
  // A burst's log ships with nothing of the burst live, and each segment's bytes let go before
  // the next is built (rvburst X2): its ships read about what a ship alone does.
  check("a generation's burst: its log's ships after it, about a ship alone",
        burstShips - peak.ship, QUARTER, "a quarter chunk past the ship alone");

  // The archive: each pass of its move and rewrite within the units it holds (above, at
  // every checkpoint), each run's peak within its table's and its pass's units, and a move
  // of large bodies O(1) in their size.
  const pass = costs.MOVE_PASS_UNITS * UNIT;
  // A move's records at their most, as its own code builds them: within its units, and
  // within MOVE_RECORD_BYTES a record (beside a quarter chunk of the probe's own).
  const records = await peak("records", 0);
  check("archive move: its records at MOVE_RECORDS, within moveTableUnits", records,
        costs.move_most_units * UNIT, "moveTableUnits(MOVE_RECORDS)");
  check("archive move: its records at MOVE_RECORDS, a record's bytes", records,
        costs.move_records * costs.MOVE_RECORD_BYTES + QUARTER,
        "MOVE_RECORDS x MOVE_RECORD_BYTES + a quarter chunk");
  rows.push({ check: "archive move: bytes a record at MOVE_RECORDS",
              bytes: +(records / costs.move_records).toFixed(1),
              most: costs.MOVE_RECORD_BYTES, ok: records / costs.move_records
                <= costs.MOVE_RECORD_BYTES + QUARTER / costs.move_records });
  // The merge of the most records real mail reaches: each pass within its units (above),
  // its records within MOVE_RECORD_BYTES a record at its passes' ends.
  const merged = await peak("merge", 0);
  const mergedRecords = peak.answer[0];
  check("archive merge: peak, the most records real mail reaches", merged,
        (costs.move_most_units + costs.MOVE_PASS_UNITS) * UNIT,
        "moveTableUnits(MOVE_RECORDS) + MOVE_PASS_UNITS");
  check(`archive merge: live at its passes' ends, ${mergedRecords} records archived`, peak.ends,
        mergedRecords * costs.MOVE_RECORD_BYTES + QUARTER,
        "its records x MOVE_RECORD_BYTES + a quarter chunk");
  check("archive move: peak, a tail seal of small messages", await peak("move", 1),
        costs.move_seal_units * UNIT + pass,
        "moveTableUnits(archiveRecords, the most a seal takes) + MOVE_PASS_UNITS");
  const bodies = { 4: await twice("bodies", 4), 8: await twice("bodies", 8) };
  check("archive move: peak(two 8 MiB bodies) - peak(two 4 MiB), least of two runs",
        bodies[8].least - bodies[4].least, QUARTER, "a quarter chunk");
  check("archive move: peak(two 8 MiB bodies), worst of two runs", bodies[8].most,
        costs.move_body_units * UNIT + pass, "moveTableUnits(2) + MOVE_PASS_UNITS");
  check("archive rewrite: peak, a purge of one of two 8 MiB bodies",
        await peak("rewrite", 8), costs.move_body_units * UNIT + pass,
        "moveTableUnits(2) + MOVE_PASS_UNITS");

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
  // The one-unit holds' queue apart: the reserve serving every waiter, a window at a time.
  const msPerWindow = Math.min(await timed("window", 8), await timed("window", 8));
  const smallTurn = (costs.OWNER_SMALL_WAITERS / costs.OWNER_RESERVE_UNITS + 1) * msPerWindow;
  rows.push({ check: "queue: a full one-unit queue's turnover, a window at a time",
              ms: +smallTurn.toFixed(1), window_ms: +msPerWindow.toFixed(2),
              most_ms: costs.wait_ms, ok: smallTurn <= costs.wait_ms });
  if (smallTurn > costs.wait_ms) {
    problems.push(`a full one-unit queue turns over in ${smallTurn} ms, past SLOT_WAIT_MS`);
  }
  console.log(JSON.stringify({ owner_rest_mib: +(rest / MIB).toFixed(2), checks: rows },
                             null, 1));
  ws.close();
} catch (error) {
  problems.push(String(error?.stack ?? error));
  console.log(JSON.stringify({ checks: rows }, null, 1));
} finally {
  own.stop();
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
