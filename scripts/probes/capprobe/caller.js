/**
 * ta-capprobe: the caller half of the live probe of Cloudflare's 32 service-binding calls
 * per request (openspec/changes/clean-sheet-platform/cap-probe.md has the method, the
 * cases, the reading key and the results).
 *
 * Throwaway. `.github/workflows/cap-probe.yml` deploys it to workers.dev, drives it with
 * `drive.mjs` and deletes it. Nothing in the product imports it. Every route needs the
 * per-run `x-probe-key`; every route but /health needs `x-probe-run`, the run id that
 * stamps each stored record so the driver can refuse a record from another run.
 *
 *   POST /run/<case>     start or run one case (query: n, intervalMs, holdMs, ...)
 *   POST /record/<case>  store a Worker case's result when its own store call failed
 *   GET  /results        every case's stored records (query `only=M5,M7` for some)
 *   GET  /plan           burn CPU well past the Free plan's 10 ms (the plan check)
 *   GET  /health
 *
 * Each case runs in its own `Probe` object (named by the case), so one case's calls,
 * alarms and background work never share an object with another's.
 */
import { DurableObject } from "cloudflare:workers";

const CASES = Object.freeze([
  "M1", "M2", "M3", "M4", "M5", "M6", "M6a", "M7", "M8", "M9", "M10", "M11", "M12",
]);
/** The cases that run in the Worker's own fetch handler. */
const WORKER_CASES = Object.freeze(["M1", "M2", "M3", "M11"]);
/**
 * Calls spent before the measured burst, so each charging rule predicts a different
 * stopping point: by the Worker before it calls M4's object, by M5's `arm()` RPC, by the
 * RPC that starts M6's and M6a's loops, and by M6a's alarm before it awaits the loop.
 */
const PRE = Object.freeze({ worker: 16, arm: 8, start: 8, alarm: 4 });
/** M12: calls A makes after B's invocation has ended. */
const M12_AFTER = 10;
/** M11: loopback, private and metadata addresses, as literals and as DNS names. */
const PRIVATE_TARGETS = Object.freeze([
  "http://127.0.0.1/",
  "http://10.0.0.1/",
  "http://169.254.169.254/latest/meta-data/",
  "http://localhost/",
  "http://127.0.0.1.nip.io/",
  "http://10.0.0.1.nip.io/",
  "http://169.254.169.254.nip.io/latest/meta-data/",
]);
const RUN_ID = /^[\w.-]{1,64}$/;

function params(url, run) {
  const num = (name, fallback) => {
    const raw = url.searchParams.get(name);
    const value = raw === null ? fallback : Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      throw new Error(`bad ${name}: ${raw}`);
    }
    return value;
  };
  return {
    run,
    n: num("n", 40),
    intervalMs: num("intervalMs", 2000),
    holdMs: num("holdMs", 16 * 60 * 1000),
    beatMs: num("beatMs", 30 * 1000),
    messages: num("messages", 100),
    waitMs: num("waitMs", 30 * 1000),
    settleMs: num("settleMs", 2000),
    timeoutMs: num("timeoutMs", 8000),
    fetchTimeoutMs: num("fetchTimeoutMs", 5000),
  };
}

function errText(error) {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return text.slice(0, 500);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function json(body, status = 200) {
  return Response.json(body, { status });
}

/** `n` sequential calls; every call runs, so a cap shows as where errors start and stay. */
async function burst(kind, n, call) {
  const out = {
    kind, n, ok: 0, errors: 0, firstError: null, firstErrorAt: null, okAfterFirstError: 0,
    ms: [], totalMs: 0,
  };
  const started = Date.now();
  for (let i = 0; i < n; i += 1) {
    const at = Date.now();
    try {
      await call(i);
      out.ok += 1;
      if (out.errors) {
        out.okAfterFirstError += 1;
      }
    } catch (error) {
      out.errors += 1;
      if (out.firstError === null) {
        out.firstError = errText(error);
        out.firstErrorAt = i;
      }
    }
    out.ms.push(Date.now() - at);
  }
  out.totalMs = Date.now() - started;
  return out;
}

/** One call of each kind the probe counts. The callee's fetch wants the run key. */
function calls(env) {
  const keyed = { headers: { "x-probe-key": env.RUN_KEY } };
  return {
    rpc: async (i) => {
      const got = await env.CALLEE.ping(i);
      if (got !== i) {
        throw new Error(`CALLEE.ping(${i}) returned ${got}`);
      }
    },
    fetch: async (i) => {
      const response = await env.CALLEE.fetch(`https://callee/ping?i=${i}`, keyed);
      const body = await response.text();
      if (!response.ok || body !== "pong") {
        throw new Error(`CALLEE.fetch status ${response.status}`);
      }
    },
    peer: async (i) => {
      const got = await env.PEER.getByName(`peer-${i}`).ping(i);
      if (got !== i) {
        throw new Error(`Peer.ping(${i}) returned ${got}`);
      }
    },
    public: async (i) => {
      if (!env.CALLEE_URL) {
        throw new Error("CALLEE_URL is not set");
      }
      const response = await fetch(`${env.CALLEE_URL}/ping?i=${i}`, keyed);
      const body = await response.text();
      if (!response.ok || body !== "pong") {
        throw new Error(`status ${response.status}: ${body.slice(0, 120)}`);
      }
    },
  };
}

/**
 * M11: what a Worker `fetch` does with each private target. No body is kept. Each fetch
 * gets `fetchTimeoutMs` (5 s), so all 7 fit inside the driver's 45 s request timeout.
 */
async function addressChecks(timeoutMs) {
  const out = [];
  for (const url of PRIVATE_TARGETS) {
    const at = Date.now();
    try {
      const response = await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(timeoutMs) });
      const body = await response.text();
      out.push({
        url, status: response.status, bytes: body.length,
        server: response.headers.get("server"),
        cfError: /error code: (\d+)/.exec(body)?.[1] ?? null,
        ms: Date.now() - at,
      });
    } catch (error) {
      out.push({ url, error: errText(error), ms: Date.now() - at });
    }
  }
  return out;
}

async function workerCase(id, env, p) {
  const c = calls(env);
  if (id === "M1") {
    return { rpc: await burst("rpc", p.n, c.rpc) };
  }
  if (id === "M2") {
    return { fetch: await burst("fetch", p.n, c.fetch) };
  }
  if (id === "M3") {
    const half = Math.floor(p.n / 2);
    return { rpc: await burst("rpc", half, c.rpc), fetch: await burst("fetch", p.n - half, c.fetch) };
  }
  return { targets: await addressChecks(p.fetchTimeoutMs) };
}

/**
 * M9: one WebSocket over `CALLEE.fetch`, then `messages` echoes, half of `n` RPC calls,
 * one echo, the other half, and `messages` echoes again. The lone echo between the two
 * bursts tells "the socket counts once" from "a socket message refreshes the budget".
 */
async function webSocketCase(env, p) {
  const out = { open: null, echoesBefore: 0, rpc1: null, bridgeEcho: false, rpc2: null,
                echoesAfter: 0, wsError: null };
  const response = await env.CALLEE.fetch("https://callee/ws", {
    headers: { Upgrade: "websocket", "x-probe-key": env.RUN_KEY },
  });
  const ws = response.webSocket;
  if (!ws) {
    out.open = `no WebSocket: status ${response.status}`;
    return out;
  }
  ws.accept();
  out.open = "ok";
  const inbox = [];
  const waiters = [];
  ws.addEventListener("message", (event) => {
    const waiter = waiters.shift();
    if (waiter) {
      waiter(String(event.data));
    } else {
      inbox.push(String(event.data));
    }
  });
  const next = (timeoutMs) => {
    if (inbox.length) {
      return Promise.resolve(inbox.shift());
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("no echo within the timeout")), timeoutMs);
      waiters.push((message) => {
        clearTimeout(timer);
        resolve(message);
      });
    });
  };
  const exchange = async (tag, count) => {
    let echoes = 0;
    for (let i = 0; i < count; i += 1) {
      const message = `${tag}-${i}`;
      ws.send(message);
      if ((await next(p.timeoutMs)) === message) {
        echoes += 1;
      }
    }
    return echoes;
  };
  const ping = calls(env).rpc;
  const half = Math.floor(p.n / 2);
  try {
    out.echoesBefore = await exchange("before", p.messages);
    out.rpc1 = await burst("rpc", half, ping);
    out.bridgeEcho = (await exchange("bridge", 1)) === 1;
    out.rpc2 = await burst("rpc", half, ping);
    out.echoesAfter = await exchange("after", p.messages);
  } catch (error) {
    out.wsError = errText(error);
  }
  ws.close(1000, "done");
  return out;
}

/** M12's rendezvous: one promise per name, resolved once. */
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

export class Probe extends DurableObject {
  /** M6 and M6a: the background loop, which outlives the RPC that started it. */
  #loop = null;
  #running = false;
  /** M6a: whether the alarm is awaiting the loop right now, and its ready signal. */
  #inAlarm = false;
  #alarmReady = null;
  /** M12: the two invocations' rendezvous points, by run and name. */
  #gates = new Map();

  /** Every record stored here carries the run that wrote it. */
  async #put(key, value, p) {
    await this.ctx.storage.put(key, { ...value, run: p.run });
  }

  /** Everything this case stored, by key. */
  async read() {
    const out = {};
    for (const [key, value] of await this.ctx.storage.list({ prefix: "r:" })) {
      out[key.slice(2)] = value;
    }
    return out;
  }

  async record(result, p) {
    await this.#put("r:result", result, p);
  }

  /** M4: the calls inside a Durable Object RPC method, after the Worker spent `workerPre`. */
  async runRpc(p, workerPre) {
    const result = { case: "M4", where: "Durable Object RPC method", status: "done", params: p,
                     startedAt: Date.now(), workerPre };
    result.rpc = await burst("rpc", p.n, calls(this.env).rpc);
    result.finishedAt = Date.now();
    await this.record(result, p);
    return result;
  }

  /** M5, M7, M8, M9, M10: run the case from this object's alarm. M5's RPC spends first. */
  async arm(id, p) {
    const armPre = id === "M5" ? await burst("rpc", PRE.arm, calls(this.env).rpc) : null;
    await this.ctx.storage.put("pending", { id, p, armPre });
    await this.record({ case: id, status: "armed", params: p, armedAt: Date.now(), armPre }, p);
    await this.ctx.storage.setAlarm(Date.now() + 100);
    return { case: id, status: "armed" };
  }

  /**
   * M6 and M6a: spend `PRE.start` calls, start a loop of `n` calls (one every
   * `intervalMs`), and return. M6 leaves the loop alone. M6a also arms an alarm that
   * spends `PRE.alarm` calls and then awaits the loop (PiHarness's shape: work started in
   * `submit()` continues inside an alarm's wait).
   */
  async startBackground(id, p) {
    if (this.#running) {
      return { case: id, status: "already running" };
    }
    this.#running = true;
    const result = {
      case: id, status: "running", params: p, startedAt: Date.now(),
      where: id === "M6" ? "background loop started in an RPC that returned; nothing awaits it"
                         : "background loop started in an RPC that returned; an alarm awaits it",
      ok: 0, errors: 0, firstError: null, firstErrorAt: null, calls: [],
    };
    result.startPre = await burst("rpc", PRE.start, calls(this.env).rpc);
    await this.record(result, p);
    // M6a's loop waits (at most `waitMs`) for the alarm to have spent its calls and be
    // awaiting, so every loop call runs inside the alarm's wait (the driver's m6aOrder).
    this.#alarmReady = id === "M6a" ? deferred() : null;
    if (id === "M6a") {
      await this.ctx.storage.put("pending", { id, p });
      await this.ctx.storage.setAlarm(Date.now() + Math.min(100, Math.floor(p.intervalMs / 4)));
    }
    this.#loop = this.#background(result, p);
    return { case: id, status: "started" };
  }

  async #background(result, p) {
    try {
      const ping = calls(this.env).rpc;
      if (this.#alarmReady) {
        result.waitedForAlarm = await this.#wait(this.#alarmReady, p.waitMs);
      }
      for (let i = 0; i < p.n; i += 1) {
        await sleep(p.intervalMs);
        const at = Date.now();
        const inAlarmBefore = this.#inAlarm;
        let ok = true;
        try {
          await ping(i);
          result.ok += 1;
        } catch (error) {
          ok = false;
          result.errors += 1;
          if (result.firstError === null) {
            result.firstError = errText(error);
            result.firstErrorAt = i;
          }
        }
        result.calls.push({ i, atMs: at - result.startedAt, ms: Date.now() - at, ok,
                            inAlarmBefore, inAlarmAfter: this.#inAlarm });
        await this.record(result, p);
      }
      result.status = "done";
    } catch (error) {
      result.status = "failed";
      result.error = errText(error);
    } finally {
      this.#running = false;
    }
    result.finishedAt = Date.now();
    await this.record(result, p);
  }

  /**
   * M12: two invocations of this method on one object, B started by the driver only after
   * A's first phase has ended. A makes 40% of `n`; B arrives and makes 40% of `n`; then
   * both make the rest at once; then, once B's invocation has ended, A makes `M12_AFTER`
   * more. The reading key in cap-probe.md names the rule each outcome means.
   */
  async interleave(who, p) {
    const first = Math.floor(p.n * 0.4);
    const ping = calls(this.env).rpc;
    const gate = (name) => this.#gate(`${p.run}:${name}`);
    const result = { case: "M12", who, where: "two Durable Object RPCs on one object",
                     status: "done", params: p, startedAt: Date.now() };
    if (who === "B") {
      result.waitedForA = await this.#wait(gate("A1"), p.waitMs);
    }
    result.phase1 = await burst("rpc", first, ping);
    result.phase1EndedAt = Date.now();
    gate(`${who}1`).resolve();
    if (who === "A") {
      result.waitedForB = await this.#wait(gate("B1"), p.waitMs);
    }
    result.phase2 = await burst("rpc", p.n - first, ping);
    if (who === "B") {
      gate("B2").resolve();
    } else {
      result.waitedForBEnd = await this.#wait(gate("B2"), p.waitMs);
      await sleep(p.settleMs);
      result.phase3 = await burst("rpc", M12_AFTER, ping);
    }
    result.ok = result.phase1.ok + result.phase2.ok + (result.phase3?.ok ?? 0);
    result.finishedAt = Date.now();
    await this.#put(`r:${who}`, result, p);
    return result;
  }

  #gate(name) {
    if (!this.#gates.has(name)) {
      this.#gates.set(name, deferred());
    }
    return this.#gates.get(name);
  }

  async #wait(gate, ms) {
    const at = Date.now();
    let timer;
    const met = await Promise.race([
      gate.promise.then(() => true),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(false), ms);
      }),
    ]);
    clearTimeout(timer);
    return { met, ms: Date.now() - at };
  }

  async alarm(info) {
    const pending = await this.ctx.storage.get("pending");
    if (!pending) {
      return;
    }
    const { id, p, armPre } = pending;
    if (id === "M6a") {
      await this.#drain(p, info);
      return;
    }
    if (id === "M10") {
      await this.#hold(p, info);
      return;
    }
    const prior = await this.ctx.storage.get("r:result");
    if (prior?.run === p.run && (prior.status === "done" || prior.status === "failed")) {
      return; // alarms are at-least-once: a redelivery runs nothing twice
    }
    const c = calls(this.env);
    const result = { case: id, where: "Durable Object alarm", status: "done", params: p, armPre,
                     startedAt: Date.now(), retryCount: info?.retryCount ?? 0 };
    try {
      if (id === "M5") {
        result.rpc = await burst("rpc", p.n, c.rpc);
        result.fetch = await burst("fetch", p.n, c.fetch);
      } else if (id === "M7") {
        result.peer = await burst("peer", p.n, c.peer);
      } else if (id === "M8") {
        result.public = await burst("public", p.n, c.public);
      } else if (id === "M9") {
        Object.assign(result, await webSocketCase(this.env, p));
      } else {
        throw new Error(`no alarm case ${id}`);
      }
    } catch (error) {
      result.status = "failed";
      result.error = errText(error);
    }
    result.finishedAt = Date.now();
    await this.record(result, p);
  }

  /** M6a: the alarm spends `PRE.alarm` calls, then awaits the loop an earlier RPC started. */
  async #drain(p, info) {
    const note = { startedAt: Date.now(), retryCount: info?.retryCount ?? 0,
                   loopPresent: this.#loop !== null };
    this.#inAlarm = true;
    try {
      note.alarmPre = await burst("rpc", PRE.alarm, calls(this.env).rpc);
      this.#alarmReady?.resolve();
      if (this.#loop) {
        await this.#loop;
      }
    } finally {
      this.#inAlarm = false;
    }
    note.finishedAt = Date.now();
    await this.#put("r:alarm", note, p);
  }

  /**
   * M10: the alarm awaits one callee call for `holdMs`, beating every `beatMs`. Alarms are
   * at-least-once, so a later attempt records the gap since the first start and the first
   * attempt's last beat, and holds nothing again: the reading key decides cut or redelivery.
   */
  async #hold(p, info) {
    const stored = await this.ctx.storage.get("r:attempts");
    const list = stored?.run === p.run ? stored.list : [];
    const startedAt = Date.now();
    list.push({ startedAt, retryCount: info?.retryCount ?? 0 });
    await this.#put("r:attempts", { list }, p);
    const base = { case: "M10", where: "Durable Object alarm awaiting one call", params: p };
    if (list.length > 1) {
      const prior = await this.ctx.storage.get("r:result");
      if (prior?.run === p.run && prior.outcome === "finished") {
        return; // a redelivery after the hold finished
      }
      const beat = await this.ctx.storage.get("r:beat");
      await this.record({
        ...base, status: "done", outcome: "retried", attempts: list,
        gapMs: startedAt - list[0].startedAt,
        firstLastBeatMs: beat?.run === p.run ? beat.elapsedMs : null,
      }, p);
      return;
    }
    await this.record({ ...base, status: "holding", startedAt }, p);
    const beat = setInterval(() => {
      this.#put("r:beat", { at: Date.now(), elapsedMs: Date.now() - startedAt }, p);
    }, p.beatMs);
    let result;
    try {
      const held = await this.env.CALLEE.hold(p.holdMs);
      result = { ...base, status: "done", outcome: "finished", heldMs: held };
    } catch (error) {
      result = { ...base, status: "done", outcome: "the call failed", error: errText(error) };
    } finally {
      clearInterval(beat);
    }
    result.startedAt = startedAt;
    result.elapsedMs = Date.now() - startedAt;
    await this.record(result, p);
  }
}

/** The plan check: the Free plan's 10 ms CPU limit ends this; Paid's 30 s does not. */
function burn(iters) {
  let x = 0;
  for (let i = 0; i < iters; i += 1) {
    x = (x * 31 + i) | 0;
  }
  return { iters, x };
}

export default {
  async fetch(request, env) {
    if (!env.RUN_KEY) {
      return json({ error: "RUN_KEY is not set" }, 500);
    }
    if (request.headers.get("x-probe-key") !== env.RUN_KEY) {
      return json({ error: "forbidden" }, 403);
    }
    const url = new URL(request.url);
    const [, verb, id] = url.pathname.split("/");
    if (verb === "health") {
      return json({ ok: true, cases: CASES });
    }
    const run = request.headers.get("x-probe-run") ?? "";
    if (!RUN_ID.test(run)) {
      return json({ error: "x-probe-run is missing or malformed" }, 400);
    }
    if (verb === "plan") {
      return json(burn(Number(url.searchParams.get("iters") ?? 1e8)));
    }
    if (verb === "results") {
      const only = url.searchParams.get("only");
      const ids = only ? only.split(",").filter((c) => CASES.includes(c)) : CASES;
      const results = {};
      for (const c of ids) {
        try {
          results[c] = await env.PROBE.getByName(c).read();
        } catch (error) {
          results[c] = { readError: errText(error) };
        }
      }
      return json({ readAt: Date.now(), results });
    }
    if (!CASES.includes(id) || request.method !== "POST") {
      return json({ error: "not found" }, 404);
    }
    const stub = env.PROBE.getByName(id);
    if (verb === "record") {
      await stub.record(await request.json(), { run });
      return json({ ok: true });
    }
    if (verb !== "run") {
      return json({ error: "not found" }, 404);
    }
    const p = params(url, run);
    if (WORKER_CASES.includes(id)) {
      const result = { case: id, where: "Worker fetch handler", status: "done", params: p,
                       startedAt: Date.now() };
      Object.assign(result, await workerCase(id, env, p));
      result.finishedAt = Date.now();
      // This store is itself a Durable Object call made after the burst; if the cap
      // refuses it, the driver stores the returned result with POST /record.
      try {
        await stub.record({ ...result, recorded: true }, p);
        result.recorded = true;
      } catch (error) {
        result.recorded = errText(error);
      }
      return json(result);
    }
    if (id === "M4") {
      const workerPre = await burst("rpc", PRE.worker, calls(env).rpc);
      return json(await stub.runRpc(p, workerPre));
    }
    if (id === "M6" || id === "M6a") {
      return json(await stub.startBackground(id, p));
    }
    if (id === "M12") {
      return json(await stub.interleave(url.searchParams.get("who") === "B" ? "B" : "A", p));
    }
    return json(await stub.arm(id, p));
  },
};
