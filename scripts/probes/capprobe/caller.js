/**
 * ta-capprobe: the caller half of the live probe of Cloudflare's 32 service-binding calls
 * per request (openspec/changes/clean-sheet-platform/cap-probe.md has the method, the
 * cases and the results).
 *
 * Throwaway. `.github/workflows/cap-probe.yml` deploys it to workers.dev, drives it with
 * `drive.mjs` and deletes it. Nothing in the product imports it. Every route needs the
 * per-run `x-probe-key`.
 *
 *   POST /run/<case>     start or run one case (query: n, intervalMs, holdMs, ...)
 *   POST /record/<case>  store a Worker case's result when its own store call failed
 *   GET  /results        every case's stored result (query `only=M5,M7` for some)
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

function params(url) {
  const num = (name, fallback) => {
    const raw = url.searchParams.get(name);
    const value = raw === null ? fallback : Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      throw new Error(`bad ${name}: ${raw}`);
    }
    return value;
  };
  return {
    n: num("n", 40),
    intervalMs: num("intervalMs", 2000),
    holdMs: num("holdMs", 16 * 60 * 1000),
    beatMs: num("beatMs", 30 * 1000),
    messages: num("messages", 100),
    waitMs: num("waitMs", 60 * 1000),
    timeoutMs: num("timeoutMs", 8000),
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

/** One call of each kind the probe counts. */
function calls(env) {
  return {
    rpc: async (i) => {
      const got = await env.CALLEE.ping(i);
      if (got !== i) {
        throw new Error(`CALLEE.ping(${i}) returned ${got}`);
      }
    },
    fetch: async (i) => {
      const response = await env.CALLEE.fetch(`https://callee/ping?i=${i}`);
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
      const response = await fetch(`${env.CALLEE_URL}/ping?i=${i}`);
      const body = await response.text();
      if (!response.ok || body !== "pong") {
        throw new Error(`status ${response.status}: ${body.slice(0, 120)}`);
      }
    },
  };
}

/** M11: what a Worker `fetch` does with each private target. No body is kept. */
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
  return { targets: await addressChecks(p.timeoutMs) };
}

/** M9: one WebSocket over `CALLEE.fetch`, messages, then RPC calls, then messages again. */
async function webSocketCase(env, p) {
  const out = { open: null, echoesBefore: 0, rpcAfterOpen: null, echoesAfter: 0, wsError: null };
  const response = await env.CALLEE.fetch("https://callee/ws", { headers: { Upgrade: "websocket" } });
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
  const exchange = async (tag) => {
    let echoes = 0;
    for (let i = 0; i < p.messages; i += 1) {
      const message = `${tag}-${i}`;
      ws.send(message);
      if ((await next(p.timeoutMs)) === message) {
        echoes += 1;
      }
    }
    return echoes;
  };
  try {
    out.echoesBefore = await exchange("before");
    out.rpcAfterOpen = await burst("rpc", p.n, calls(env).rpc);
    out.echoesAfter = await exchange("after");
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
  /** M6a: whether the alarm is awaiting the loop right now. */
  #inAlarm = false;
  /** M12: the two invocations' rendezvous points. */
  #gates = new Map();

  /** Everything this case stored, by key. */
  async read() {
    const out = {};
    for (const [key, value] of await this.ctx.storage.list({ prefix: "r:" })) {
      out[key.slice(2)] = value;
    }
    return out;
  }

  async record(result) {
    await this.ctx.storage.put("r:result", result);
  }

  /** M4: the calls inside a Durable Object RPC method the Worker called. */
  async runRpc(p) {
    const result = { case: "M4", where: "Durable Object RPC method", status: "done", params: p,
                     startedAt: Date.now() };
    result.rpc = await burst("rpc", p.n, calls(this.env).rpc);
    result.finishedAt = Date.now();
    await this.record(result);
    return result;
  }

  /** M5, M7, M8, M9, M10: run the case from this object's alarm. */
  async arm(id, p) {
    await this.ctx.storage.put("pending", { id, p });
    await this.record({ case: id, status: "armed", params: p, armedAt: Date.now() });
    await this.ctx.storage.setAlarm(Date.now() + 100);
    return { case: id, status: "armed" };
  }

  /**
   * M6 and M6a: start a loop of `n` calls, one every `intervalMs`, and return at once.
   * M6 leaves it alone. M6a also arms an alarm that awaits it (PiHarness's shape: work
   * started in `submit()` continues inside an alarm's wait).
   */
  async startBackground(id, p) {
    if (this.#loop) {
      return { case: id, status: "already running" };
    }
    const result = {
      case: id, status: "running", params: p, startedAt: Date.now(),
      where: id === "M6" ? "background loop started in an RPC that returned; nothing awaits it"
                         : "background loop started in an RPC that returned; an alarm awaits it",
      ok: 0, errors: 0, firstError: null, firstErrorAt: null, calls: [],
    };
    await this.record(result);
    if (id === "M6a") {
      await this.ctx.storage.put("pending", { id, p });
      await this.ctx.storage.setAlarm(Date.now() + Math.floor(p.intervalMs / 2));
    }
    this.#loop = this.#background(result, p);
    return { case: id, status: "started" };
  }

  async #background(result, p) {
    try {
      const ping = calls(this.env).rpc;
      for (let i = 0; i < p.n; i += 1) {
        await sleep(p.intervalMs);
        const at = Date.now();
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
                            inAlarm: this.#inAlarm });
        await this.record(result);
      }
      result.status = "done";
    } catch (error) {
      result.status = "failed";
      result.error = errText(error);
    }
    result.finishedAt = Date.now();
    await this.record(result);
  }

  /**
   * M12: two invocations of this method interleave on one object, and each counts its
   * own calls. A makes 40% of `n`, then waits for B to make its 40%; then both make the
   * rest at once. Per-invocation counters let each reach 32; one shared counter stops
   * both near 16; calls charged to the newer invocation stop both in between.
   */
  async interleave(who, p) {
    const first = Math.floor(p.n * 0.4);
    const ping = calls(this.env).rpc;
    const result = { case: "M12", who, where: "two concurrent Durable Object RPCs on one object",
                     status: "done", params: p, startedAt: Date.now() };
    if (who === "B") {
      result.waitedForA = await this.#wait("A", p.waitMs);
    }
    result.phase1 = await burst("rpc", first, ping);
    this.#gate(who).resolve();
    if (who === "A") {
      result.waitedForB = await this.#wait("B", p.waitMs);
    }
    result.phase2 = await burst("rpc", p.n - first, ping);
    result.ok = result.phase1.ok + result.phase2.ok;
    result.finishedAt = Date.now();
    await this.ctx.storage.put(`r:${who}`, result);
    return result;
  }

  #gate(name) {
    if (!this.#gates.has(name)) {
      this.#gates.set(name, deferred());
    }
    return this.#gates.get(name);
  }

  async #wait(name, ms) {
    const at = Date.now();
    let timer;
    const met = await Promise.race([
      this.#gate(name).promise.then(() => true),
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
    const { id, p } = pending;
    if (id === "M6a") {
      await this.#drain(info);
      return;
    }
    if (id === "M10") {
      await this.#hold(p, info);
      return;
    }
    const prior = await this.ctx.storage.get("r:result");
    if (prior?.status === "done" || prior?.status === "failed") {
      return;
    }
    const c = calls(this.env);
    const result = { case: id, where: "Durable Object alarm", status: "done", params: p,
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
    await this.record(result);
  }

  /** M6a: the alarm awaits the loop an earlier RPC started. */
  async #drain(info) {
    const note = { startedAt: Date.now(), retryCount: info?.retryCount ?? 0,
                   loopPresent: this.#loop !== null };
    this.#inAlarm = true;
    try {
      if (this.#loop) {
        await this.#loop;
      }
    } finally {
      this.#inAlarm = false;
    }
    note.finishedAt = Date.now();
    await this.ctx.storage.put("r:alarm", note);
  }

  /** M10: the alarm awaits one callee call for `holdMs`, beating every `beatMs`. */
  async #hold(p, info) {
    const attempts = (await this.ctx.storage.get("r:attempts")) ?? [];
    const startedAt = Date.now();
    attempts.push({ startedAt, retryCount: info?.retryCount ?? 0 });
    await this.ctx.storage.put("r:attempts", attempts);
    const base = { case: "M10", where: "Durable Object alarm awaiting one call", params: p };
    if (attempts.length > 1) {
      // A later attempt means an earlier one never finished: the cut (or a crash) ended
      // it, and the runtime retried the alarm. Holding again would only repeat it.
      await this.record({ ...base, status: "done", outcome: "retried: an earlier attempt never finished",
                          attempts });
      return;
    }
    await this.record({ ...base, status: "holding", startedAt });
    const beat = setInterval(() => {
      this.ctx.storage.put("r:beat", { at: Date.now(), elapsedMs: Date.now() - startedAt });
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
    await this.record(result);
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
      await stub.record(await request.json());
      return json({ ok: true });
    }
    if (verb !== "run") {
      return json({ error: "not found" }, 404);
    }
    const p = params(url);
    if (WORKER_CASES.includes(id)) {
      const result = { case: id, where: "Worker fetch handler", status: "done", params: p,
                       startedAt: Date.now() };
      Object.assign(result, await workerCase(id, env, p));
      result.finishedAt = Date.now();
      // This store is itself a Durable Object call made after the burst; if the cap
      // refuses it, the driver stores the returned result with POST /record.
      try {
        await stub.record({ ...result, recorded: true });
        result.recorded = true;
      } catch (error) {
        result.recorded = errText(error);
      }
      return json(result);
    }
    if (id === "M4") {
      return json(await stub.runRpc(p));
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
