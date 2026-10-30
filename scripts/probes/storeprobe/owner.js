/**
 * ta-storeprobe: the owner-like half of the live store probe
 * (openspec/changes/clean-sheet-platform/store-probe.md has the method, the cases, the
 * reading key and the results).
 *
 * A minimal stand-in for design (a'): one script holds an owner-like Durable Object
 * (`Owner`) and the per-thread store (`ThreadStore`), and binds exactly one Durable Object
 * namespace, `PISTORE` -> `ThreadStore`, with no `script_name`, under the owner Worker's
 * runtime (`disable_ctx_exports`). Each `ThreadStore` serves pi's `Storage` methods over RPC
 * from pi-durable's own `SqliteStorage` (`openDurableObjectSqliteStorage`, prefix `pi_`);
 * the `Owner` opens real pi `Harness` Sessions over `RemoteStorage`, a thin RPC proxy, on
 * pi-ai's faux provider, so no model credentials exist anywhere.
 *
 * Throwaway. `.github/workflows/store-probe.yml` deploys it (and two copies with another
 * `limits.subrequests`, from `config.mjs`), drives it through `ta-storeprobe-door` with
 * `drive.mjs`, and deletes every copy. It has no route and no workers.dev URL. Nothing in
 * the product imports it.
 */
import { DurableObject, RpcTarget } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { createModels, fauxAssistantMessage, fauxProvider } from "@earendil-works/pi-ai";
import { createRegistry, Harness, StorageRequestError } from "@earendil-works/pi-durable";
import { openDurableObjectSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/cloudflare";

import { SHAPE } from "./shape.mjs";

const BG = BACKGROUND_CONTEXT;
const KIND_OWNER = "Owner";
const KIND_STORE = "ThreadStore";
const RUN_ID = /^[\w.-]{1,64}$/;
const MINUTE = 60_000;

/** pi's run settings, as the owner's agent sets them: partial commits every second. */
const SETTINGS = Object.freeze({
  progress: Object.freeze({ partialIntervalMs: 1000, outputIntervalMs: 1000 }),
  afterFailedRun: "withdraw",
  restartLimit: 5,
});
/** The one faux model every Session runs on. A huge window, so pi never compacts. */
const MODEL = Object.freeze({ provider: "probe", modelId: "faux" });


/** pi's `Storage` reads the proxy forwards; each takes its context last. */
const READS = Object.freeze([
  "conversation", "scanConversations", "findLatestHeadMarker", "scanEntries", "task",
  "scanTasks", "submission", "scanSubmissions", "submissionByRequest", "findDocument",
  "document", "scanDocuments",
]);
/** What a store dispatches: the reads, `commit`, and `entry`'s two overloads. */
const STORE_METHODS = new Set([...READS, "commit", "entry", "entryIn"]);

function errText(error) {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return text.slice(0, 500);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const now = () => performance.now();

/** Order statistics of `samples` (ms), and the smallest step the clock showed. */
export function stats(samples) {
  const n = samples.length;
  if (n === 0) {
    return { n: 0 };
  }
  const sorted = Float64Array.from(samples).sort();
  const at = (q) => sorted[Math.min(n - 1, Math.ceil(q * n) - 1)];
  const round = (x) => Math.round(x * 1000) / 1000;
  let step = Infinity;
  for (let i = 1; i < n; i += 1) {
    const d = sorted[i] - sorted[i - 1];
    if (d > 0 && d < step) {
      step = d;
    }
  }
  let sum = 0;
  for (const x of sorted) {
    sum += x;
  }
  return { n, min: round(sorted[0]), p50: round(at(0.5)), p95: round(at(0.95)), p99: round(at(0.99)),
           max: round(sorted[n - 1]), mean: round(sum / n),
           clockStep: Number.isFinite(step) ? round(step) : null };
}

/** `n` sequential calls; every call runs, so a cap shows as where errors start and stay. */
async function burst(kind, n, call) {
  const out = { kind, n, ok: 0, errors: 0, firstError: null, firstErrorAt: null, okAfterFirstError: 0,
                totalMs: 0 };
  const started = now();
  for (let i = 0; i < n; i += 1) {
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
  }
  out.totalMs = Math.round(now() - started);
  return out;
}

/** The colo this code runs in, from Cloudflare's trace page (a store-side reading). */
async function traceColo() {
  try {
    const response = await fetch("https://www.cloudflare.com/cdn-cgi/trace",
                                 { signal: AbortSignal.timeout(5000) });
    const text = await response.text();
    return { colo: /^colo=(.*)$/m.exec(text)?.[1] ?? null, status: response.status };
  } catch (error) {
    return { colo: null, error: errText(error) };
  }
}

/** Whatever the runtime reports about memory; Workers may report nothing real. */
function memoryReading() {
  try {
    const usage = globalThis.process?.memoryUsage?.();
    if (!usage) {
      return { available: false };
    }
    return { available: Object.values(usage).some((v) => v > 0), rss: usage.rss,
             heapTotal: usage.heapTotal, heapUsed: usage.heapUsed, external: usage.external };
  } catch (error) {
    return { available: false, error: errText(error) };
  }
}

/** Faux answer text of about `chars` characters, different per salt. */
function answerText(chars, salt) {
  const line = `Answer ${salt}: the store probe writes realistic transcript text here, line `;
  let out = "";
  for (let i = 0; out.length < chars; i += 1) {
    out += `${line}${i}.\n`;
  }
  return out.slice(0, chars);
}

/**
 * A faux `probe` provider whose every answer is `chars` characters, streamed at
 * `tokensPerSecond` (instant when absent), and the `Models` that serve it, for `answers`
 * answers. Each case shares one across its Sessions, so P4's memory per Session is pi's,
 * not a provider's.
 */
function fauxModels(chars, tokensPerSecond, answers) {
  const faux = fauxProvider({
    provider: MODEL.provider,
    models: [{ id: MODEL.modelId, contextWindow: 4_000_000, maxTokens: 1_000_000 }],
    ...(tokensPerSecond ? { tokensPerSecond } : {}),
  });
  let count = 0;
  const respond = () => fauxAssistantMessage(answerText(chars, (count += 1)));
  faux.setResponses(Array.from({ length: answers + 2 }, () => respond));
  const models = createModels();
  models.setProvider(faux.provider);
  return models;
}

/**
 * Counts and times every store call a group of Sessions makes, by method. Samples are
 * kept per method up to `cap`; the counts are exact.
 */
class Meter {
  total = 0;
  errors = 0;
  firstError = null;
  byMethod = {};
  #samples = {};
  #cap;
  #started = now();

  constructor(cap = 50_000) {
    this.#cap = cap;
  }

  async time(method, fn) {
    this.total += 1;
    this.byMethod[method] = (this.byMethod[method] ?? 0) + 1;
    const at = now();
    try {
      return await fn();
    } catch (error) {
      this.errors += 1;
      if (this.firstError === null) {
        this.firstError = { method, error: errText(error), atCall: this.total,
                            atMs: Math.round(at - this.#started) };
      }
      throw error;
    } finally {
      const list = (this.#samples[method] ??= []);
      if (list.length < this.#cap) {
        list.push(now() - at);
      }
    }
  }

  stats(method) {
    return stats(this.#samples[method] ?? []);
  }

  summary() {
    return { total: this.total, errors: this.errors, firstError: this.firstError, byMethod: this.byMethod,
             commit: this.stats("commit"), open: this.stats("open") };
  }
}

/**
 * pi's `Storage` over RPC to one `ThreadStore` (design (a'), `RemoteStorage`):
 * - `mintId` is local, seeded by `open` (commit advances the store's `next_id` from the
 *   ids in its writes);
 * - a request error comes back tagged and is thrown again as pi's `StorageRequestError`;
 * - an aborted context rejects before and after the call;
 * - nothing is retried: the probe wants every failure seen (the design retries reads once).
 * `link` is `{ open(), call(method, args), release() }`, over a stub or a session.
 */
class RemoteStorage {
  #link;
  #next;
  #meter;
  #closed = false;

  constructor(link, nextId, meter) {
    this.#link = link;
    this.#next = nextId;
    this.#meter = meter;
  }

  static async open(link, meter) {
    const opened = await meter.time("open", () => link.open());
    return { storage: new RemoteStorage(link, opened.nextId, meter), opened };
  }

  async _call(method, args, context) {
    if (this.#closed) {
      throw new Error("RemoteStorage is closed");
    }
    context?.abortSignal?.throwIfAborted();
    const result = await this.#meter.time(method, () => this.#link.call(method, args));
    context?.abortSignal?.throwIfAborted();
    if (result && typeof result === "object" && "requestError" in result) {
      throw new StorageRequestError(result.requestError);
    }
    return result?.ok;
  }

  async mintId() {
    if (this.#closed) {
      throw new Error("RemoteStorage is closed");
    }
    return this.#next++;
  }

  commit(writes, context) {
    return this._call("commit", [writes], context);
  }

  entry(a, b, c) {
    return c === undefined ? this._call("entry", [a], b) : this._call("entryIn", [a, b], c);
  }

  async close() {
    if (this.#closed) {
      return;
    }
    this.#closed = true;
    await this.#meter.time("release", () => this.#link.release());
  }
}
for (const method of READS) {
  RemoteStorage.prototype[method] = function read(...args) {
    const context = args.pop();
    return this._call(method, args, context);
  };
}

/** A link over per-call stub methods: each call is its own Durable Object request. */
function stubLink(store, holder) {
  return {
    open: () => store.open(holder),
    call: (method, args) => store.call(holder, method, args),
    release: () => store.release(holder),
  };
}

/** A link over one `RpcTarget` session from `openSession` (P7's other shape). */
function sessionLink(store, holder) {
  let session = null;
  return {
    open: async () => {
      const opened = await store.openSession(holder);
      session = opened.session;
      return opened;
    },
    call: (method, args) => session.call(method, args),
    release: async () => {
      try {
        await session.release();
      } finally {
        session[Symbol.dispose]?.();
        session = null;
      }
    },
  };
}

/** One open thread: its Session over its own store, and its root conversation. */
async function openThread(env, name, meter, models, shape = "stub") {
  const store = env.PISTORE.getByName(name);
  const holder = crypto.randomUUID();
  const link = shape === "session" ? sessionLink(store, holder) : stubLink(store, holder);
  const timings = {};
  const calls = {};
  const step = async (label, fn) => {
    const before = meter.total;
    const at = now();
    const value = await fn();
    timings[label] = Math.round((now() - at) * 1000) / 1000;
    calls[label] = meter.total - before;
    return value;
  };
  const { storage, opened } = await step("storeOpen", () => RemoteStorage.open(link, meter));
  const reports = [];
  const harness = await step("harnessOpen", () => Harness.open(storage, {
    models,
    registry: REGISTRY,
    settings: SETTINGS,
    onWake: () => {},
    onReport: (error, info) => {
      if (reports.length < 5) {
        reports.push(`${info?.type}: ${errText(error)}`);
      }
    },
  }, BG));
  const root = await step("root", () => harness.root(BG, { agent: { model: MODEL } }));
  harness.resume();
  return { name, store, harness, root, timings, calls, reports, opened };
}

/** Submit one input and wait for it to settle; the steps timed into `thread.timings`. */
async function turn(thread, meter, content, label = "") {
  const at = now();
  const before = meter.total;
  const submission = await thread.root.submit({ type: "input", content }, BG);
  const admitMs = now() - at;
  const settled = await submission.wait(BG);
  const round = (x) => Math.round(x * 1000) / 1000;
  if (label) {
    thread.timings[`${label}Admit`] = round(admitMs);
    thread.timings[`${label}Settle`] = round(now() - at - admitMs);
    thread.calls[label] = meter.total - before;
  }
  if (settled.status !== "done") {
    throw new Error(`turn settled ${settled.status}${settled.reason ? ` (${settled.reason})` : ""}`);
  }
  return settled;
}

async function closeThread(thread) {
  try {
    await thread.harness.close(BG);
  } catch {
    // A failed Session has closed already; its storage too.
  }
}

/** The JSON size of a thread's current model context, in characters. */
async function contextChars(thread) {
  const view = await thread.root.context(BG);
  return JSON.stringify(view.messages).length;
}

/** One registry for every Session in the isolate, as the owner keeps one. Nothing installed. */
const REGISTRY = createRegistry();

/**
 * The per-thread store (design (a'), `ThreadStore`): pi's `SqliteStorage` over its own
 * `ctx.storage` under prefix `pi_`, serving pi's `Storage` methods over RPC, behind a
 * holder fence. It reads no `env` binding.
 */
export class ThreadStore extends DurableObject {
  #storage = null;
  #opening = null;
  #instance = crypto.randomUUID();
  #startedAt = Date.now();
  #calls = 0;

  #holder() {
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS ta_holder (only INTEGER PRIMARY KEY, holder TEXT NOT NULL)");
    return this.ctx.storage.sql.exec("SELECT holder FROM ta_holder").toArray()[0]?.holder ?? null;
  }

  async #open() {
    if (this.#storage) {
      return this.#storage;
    }
    this.#opening ??= openDurableObjectSqliteStorage(this.ctx.storage, { prefix: "pi_" })
      .then((storage) => {
        this.#storage = storage;
        return storage;
      })
      .finally(() => {
        this.#opening = null;
      });
    return this.#opening;
  }

  #fence(holder) {
    const stored = this.#holder();
    if (stored === null) {
      throw new Error("this store was never opened");
    }
    if (stored !== holder) {
      throw new Error("refused: another holder opened this store");
    }
  }

  /** Takes the store for `holder`, migrates pi's schema on first use, and seeds the proxy's ids. */
  async open(holder) {
    this.#calls += 1;
    this.#holder();
    this.ctx.storage.sql.exec("INSERT OR REPLACE INTO ta_holder VALUES (1, ?)", holder);
    const storage = await this.#open();
    return { nextId: await storage.mintId(), instance: this.#instance, id: this.ctx.id.toString() };
  }

  /** `open`, plus an `RpcTarget` session whose calls ride this one request (P7). */
  async openSession(holder) {
    const opened = await this.open(holder);
    return { ...opened, session: new StoreSession(this, holder) };
  }

  /** One of pi's `Storage` methods; a request error comes back tagged. */
  async call(holder, method, args) {
    this.#calls += 1;
    this.#fence(holder);
    if (!STORE_METHODS.has(method)) {
      throw new Error(`no storage method ${method}`);
    }
    const storage = await this.#open();
    const target = method === "entryIn" ? "entry" : method;
    try {
      return { ok: await storage[target](...args, BG) };
    } catch (error) {
      if (error instanceof StorageRequestError) {
        return { requestError: error.message };
      }
      throw error;
    }
  }

  async release(holder) {
    this.#calls += 1;
    this.#fence(holder);
    const storage = this.#storage;
    this.#storage = null;
    await storage?.close(BG);
  }

  async ping(i) {
    this.#calls += 1;
    return i;
  }

  async whoami() {
    this.#calls += 1;
    return { kind: KIND_STORE, id: this.ctx.id.toString(), instance: this.#instance,
             startedAt: this.#startedAt, calls: this.#calls, exports: typeof this.ctx.exports,
             env: Object.keys(this.env ?? {}).sort() };
  }

  async size() {
    return this.ctx.storage.sql.databaseSize;
  }

  async colo() {
    return { ...(await traceColo()), id: this.ctx.id.toString() };
  }

  /** Resets this instance, so the next call starts a fresh one over the same database. */
  async reset() {
    this.ctx.abort("store probe reset");
  }
}

/** The session `openSession` hands back: calls on it are part of that one RPC session. */
class StoreSession extends RpcTarget {
  #store;
  #holder;

  constructor(store, holder) {
    super();
    this.#store = store;
    this.#holder = holder;
  }

  call(method, args) {
    return this.#store.call(this.#holder, method, args);
  }

  ping(i) {
    return this.#store.ping(i);
  }

  whoami() {
    return this.#store.whoami();
  }

  release() {
    return this.#store.release(this.#holder);
  }
}

function num(p, name, fallback, max) {
  const value = p?.[name] ?? fallback;
  if (!Number.isFinite(value) || value < 0 || value > max) {
    throw new Error(`bad ${name}: ${value}`);
  }
  return value;
}

/** The parameters every case reads, bounded; `drive.mjs` sets them, `local.mjs` shrinks them. */
function params(p) {
  if (!RUN_ID.test(p?.run ?? "")) {
    throw new Error("run is missing or malformed");
  }
  return {
    run: p.run, object: String(p.object ?? ""), doorColo: p.doorColo ?? null,
    shape: p.shape === "session" ? "session" : "stub",
    n: num(p, "n", SHAPE.p1aN, 20_000),
    threads: num(p, "threads", 1, 64),
    runMs: num(p, "runMs", 10 * MINUTE, 14 * MINUTE),
    heartbeatMs: num(p, "heartbeatMs", 30_000, 120_000),
    answerSeconds: num(p, "answerSeconds", 30, 120),
    tokensPerSecond: num(p, "tokensPerSecond", 20, 1000),
    windowMs: num(p, "windowMs", 20_000, 120_000),
    pings: num(p, "pings", 1000, 5000),
    streamSeconds: num(p, "streamSeconds", 20, 120),
    idleMs: num(p, "idleMs", 180_000, 10 * MINUTE),
    from: num(p, "from", 0, 1024),
    to: num(p, "to", 0, 1024),
    turns: num(p, "turns", 6, 40),
    answerBytes: num(p, "answerBytes", 50_000, 500_000),
    max: num(p, "max", 128, 1024),
  };
}

/** Whether a case's run should still be running: a read then would be an intrusion. */
const MEASURED = new Set(["p1b", "p1c", "p1run"]);

/**
 * The owner-like object. The door reaches it by name through its own `OWNER` binding (by
 * `script_name`), as the doors reach `OwnerDO`; this script binds no namespace of its own
 * class. Each case runs in its own object, named by the driver.
 */
export class Owner extends DurableObject {
  /** Work in flight that outlives the alarm that started it (P1b, P1 runs). */
  #work = null;
  #state = null;

  /** Every record carries the run that wrote it, and this object's id (for analytics). */
  async #put(key, value, p) {
    await this.ctx.storage.put(key, { ...value, run: p.run, ownerId: this.ctx.id.toString() });
  }

  /** Everything this object stored, by key. A read while a measured case runs is logged. */
  async read() {
    const pending = await this.ctx.storage.get("pending");
    if (pending && MEASURED.has(pending.id)) {
      const list = (await this.ctx.storage.get("intrusions")) ?? [];
      list.push(Date.now());
      await this.ctx.storage.put("intrusions", list);
    }
    const out = {};
    for (const [key, value] of await this.ctx.storage.list({ prefix: "r:" })) {
      out[key.slice(2)] = value;
    }
    const intrusions = await this.ctx.storage.get("intrusions");
    if (intrusions) {
      out.intrusions = intrusions;
    }
    return out;
  }

  /** Answers only as the owner class: P3 calls this through `PISTORE` and expects a refusal. */
  async ownerOnly() {
    return KIND_OWNER;
  }

  async whoami() {
    return { kind: KIND_OWNER, id: this.ctx.id.toString() };
  }

  /** P3: what `PISTORE` reaches, from inside the owner class. */
  async p3(raw) {
    const p = params(raw);
    const attempt = async (fn) => {
      try {
        return { ok: await fn() };
      } catch (error) {
        return { error: errText(error) };
      }
    };
    const ownerId = this.ctx.id.toString();
    const store = this.env.PISTORE.getByName(`p3-${p.run}`);
    const result = {
      case: "P3", status: "done", ownerId, ownerExports: typeof this.ctx.exports,
      ownerEnv: Object.keys(this.env ?? {}).sort(),
      store: await attempt(() => store.whoami()),
      ownerIdThroughPistore: await attempt(() => {
        const id = this.env.PISTORE.idFromString(ownerId);
        return this.env.PISTORE.get(id).whoami();
      }),
      ownerNameThroughPistore: await attempt(() => this.env.PISTORE.getByName(p.object).whoami()),
      ownerMethodThroughPistore: await attempt(() => store.ownerOnly()),
      ownerReadThroughPistore: await attempt(() => store.read()),
    };
    await this.#put("r:result", result, p);
    return result;
  }

  /** P1a: `n` store calls inside this one DO RPC invocation, over a stub or a session. */
  async p1Burst(raw) {
    const p = params(raw);
    const store = this.env.PISTORE.getByName(`p1a-${p.shape}`);
    const result = { case: "P1a", shape: p.shape, status: "done", startedAt: Date.now() };
    if (p.shape === "session") {
      const opened = await store.openSession(crypto.randomUUID());
      try {
        result.burst = await burst("session", p.n, (i) => opened.session.ping(i));
      } finally {
        opened.session[Symbol.dispose]?.();
      }
    } else {
      result.burst = await burst("stub", p.n, (i) => store.ping(i));
    }
    result.finishedAt = Date.now();
    // Storage is not a subrequest, so this record is stored whatever the burst hit.
    await this.#put("r:result", result, p);
    return result;
  }

  /**
   * P1's control: `n` sequential `fetch` calls to the trace page in this one DO RPC. A
   * fetch is a known subrequest, so on the low copy this stops at its limit only if that
   * limit applied. Bounded at 30 s, so a slow network cannot hold the door's request.
   */
  async p1Control(raw) {
    const p = params(raw);
    const result = { case: "P1 control", status: "done", startedAt: Date.now() };
    const deadline = Date.now() + 30_000;
    result.burst = await burst("fetch", p.n, async () => {
      if (Date.now() > deadline) {
        throw new Error("control stopped: past 30 s");
      }
      const response = await fetch("https://www.cloudflare.com/cdn-cgi/trace",
                                   { signal: AbortSignal.timeout(5000) });
      await response.arrayBuffer();
    });
    result.finishedAt = Date.now();
    await this.#put("r:result", result, p);
    return result;
  }

  /** P4's seeding: real pi turns into stores `p4-<from>`..`p4-<to - 1>`, then closed. */
  async p4Seed(raw) {
    const p = params(raw);
    const meter = new Meter();
    const out = [];
    const models = fauxModels(p.answerBytes, 0, (p.to - p.from) * p.turns);
    for (let i = p.from; i < p.to; i += 1) {
      const at = now();
      const thread = await openThread(this.env, `p4-${i}`, meter, models);
      try {
        for (let t = 0; t < p.turns; t += 1) {
          await turn(thread, meter, `Seed turn ${t} of thread ${i}. ${answerText(1500, `q${t}`)}`);
        }
        out.push({ i, contextChars: await contextChars(thread), bytes: await thread.store.size(),
                   ms: Math.round(now() - at) });
      } finally {
        await closeThread(thread);
      }
    }
    const result = { case: "P4 seed", status: "done", from: p.from, to: p.to, stores: out,
                     calls: meter.summary() };
    await this.#put(`r:seed-${p.from}`, result, p);
    return result;
  }

  /**
   * Arms a case that runs from this object's alarm. P1b's arming RPC spends `j` store
   * calls first, so the stopping point names the charging rule.
   */
  async arm(raw) {
    const p = params(raw);
    const id = String(raw.case);
    if (!["p1b", "p1c", "p1run", "p2", "p4", "p7"].includes(id)) {
      throw new Error(`no alarm case ${id}`);
    }
    const armPre = id === "p1b"
      ? await burst("stub", SHAPE.p1b.j, (i) => this.env.PISTORE.getByName("p1b").ping(i))
      : null;
    await this.ctx.storage.put("pending", { id, p });
    await this.#put("r:result", { case: id, status: "armed", armedAt: Date.now(), armPre }, p);
    await this.ctx.storage.setAlarm(Date.now() + 100);
    return { case: id, status: "armed" };
  }

  async alarm(info) {
    const pending = await this.ctx.storage.get("pending");
    if (!pending) {
      return;
    }
    const { id, p } = pending;
    if (pending.started) {
      if (this.#work === null || this.#state === null) {
        // The instance that started the case is gone: an eviction or a reset (P4: likely
        // memory). Record it and run nothing again.
        const prior = await this.ctx.storage.get("r:result");
        const progress = await this.ctx.storage.get("r:progress");
        await this.#put("r:result", { ...(prior ?? {}), case: id, status: "reset",
                                      resetSeenAt: Date.now(), retryCount: info?.retryCount ?? 0,
                                      progressAtReset: progress ?? null }, p);
        await this.ctx.storage.delete("pending");
        return;
      }
      await this.#heartbeat(id, p);
      return;
    }
    await this.ctx.storage.put("pending", { ...pending, started: true });
    const prior = await this.ctx.storage.get("r:result");
    const base = { case: id, armPre: prior?.armPre ?? null, startedAt: Date.now(),
                   retryCount: info?.retryCount ?? 0 };
    if (id === "p1b" || id === "p1run") {
      // Work in flight: started here, tracked across this alarm's return, and kept
      // company by a heartbeat alarm every `heartbeatMs` (PiHarness's wake shape).
      const started = id === "p1b" ? await this.#p1bStart(base, p) : await this.#p1runStart(base, p);
      this.#work = started.work.finally(() => {
        this.#work = Promise.resolve();
        this.#state.done = true;
      });
      await this.ctx.storage.setAlarm(Date.now() + (id === "p1b" ? p.windowMs : p.heartbeatMs));
      return;
    }
    this.#work = Promise.resolve();
    let result;
    try {
      if (id === "p1c") {
        result = { ...base, status: "done" };
        result.burst = await burst("stub", p.n, (i) => this.env.PISTORE.getByName("p1c").ping(i));
      } else if (id === "p2") {
        result = await this.#p2(base, p);
      } else if (id === "p4") {
        result = await this.#p4(base, p);
      } else {
        result = await this.#p7(base, p);
      }
    } catch (error) {
      result = { ...base, status: "failed", error: errText(error) };
    }
    result.finishedAt = Date.now();
    await this.#put("r:result", result, p);
    await this.ctx.storage.delete("pending");
  }

  async #heartbeat(id, p) {
    const state = this.#state;
    const beat = { at: Date.now() - state.startedAt };
    if (id === "p1b") {
      beat.loopCalls = state.loopCalls;
      beat.beatPre = await burst("stub", SHAPE.p1b.h, (i) => this.env.PISTORE.getByName("p1b").ping(i));
    } else {
      beat.total = state.meter.total;
      beat.errors = state.meter.errors;
    }
    state.beats.push(beat);
    await this.#put("r:beats", { list: state.beats }, p);
    const signal = state.onBeat;
    state.onBeat = null;
    signal?.();
    if (state.done) {
      await this.ctx.storage.delete("pending");
      return;
    }
    await this.ctx.storage.setAlarm(Date.now() + (id === "p1b" ? p.windowMs : p.heartbeatMs));
  }

  /** The next heartbeat, or `false` after `ms`. */
  #nextBeat(ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(false), ms);
      this.#state.onBeat = () => {
        clearTimeout(timer);
        resolve(true);
      };
    });
  }

  /**
   * P1b: the start alarm spends `k`, then a loop it does not await makes `perWindow` calls
   * per heartbeat window for `windows` windows (phase 1), waits for the next heartbeat and
   * bursts `burst` calls at once (phase 2). Each heartbeat alarm spends `h`.
   */
  async #p1bStart(base, p) {
    const s = SHAPE.p1b;
    const ping = (i) => this.env.PISTORE.getByName("p1b").ping(i);
    const result = { ...base, status: "running", alarmPre: await burst("stub", s.k, ping) };
    this.#state = { startedAt: Date.now(), beats: [], loopCalls: 0, onBeat: null, done: false };
    await this.#put("r:result", result, p);
    const intervalMs = p.windowMs / s.perWindow;
    const work = (async () => {
      const phase1 = { kind: "loop", n: s.perWindow * s.windows, ok: 0, errors: 0, firstError: null,
                       firstErrorAt: null, okAfterFirstError: 0 };
      for (let i = 0; i < phase1.n; i += 1) {
        await sleep(intervalMs);
        try {
          await ping(i);
          phase1.ok += 1;
          if (phase1.errors) {
            phase1.okAfterFirstError += 1;
          }
        } catch (error) {
          phase1.errors += 1;
          if (phase1.firstError === null) {
            phase1.firstError = errText(error);
            phase1.firstErrorAt = i;
          }
        }
        this.#state.loopCalls = i + 1;
      }
      result.phase1 = phase1;
      result.waitedForBeat = await this.#nextBeat(3 * p.windowMs);
      result.phase2 = await burst("stub", s.burst, ping);
      result.status = "done";
      result.finishedAt = Date.now();
      await this.#put("r:result", result, p);
    })().catch(async (error) => {
      result.status = "failed";
      result.error = errText(error);
      await this.#put("r:result", result, p);
    });
    return { work };
  }

  /**
   * P1 run: `threads` Sessions, each on a fresh store, each answering one faux input after
   * another (streamed at `tokensPerSecond` for about `answerSeconds`, partial commits every
   * second) until `runMs` has passed; then every conversation is aborted and closed. A
   * thread whose Session fails stops there: the failure is the reading.
   */
  async #p1runStart(base, p) {
    const meter = new Meter(20_000);
    const result = { ...base, status: "running", threads: p.threads, runMs: p.runMs };
    this.#state = { startedAt: Date.now(), beats: [], meter, onBeat: null, done: false };
    const chars = Math.round(p.answerSeconds * p.tokensPerSecond * 4);
    const threads = [];
    const opening = [];
    // Twice the answers the run should take, so a faster stream never runs the queue dry.
    const perThread = 2 * Math.ceil(p.runMs / (p.answerSeconds * 1000)) + 4;
    const models = fauxModels(chars, p.tokensPerSecond, p.threads * perThread);
    for (let t = 0; t < p.threads; t += 1) {
      try {
        const thread = await openThread(this.env, `p1run-${p.threads}-${t}`, meter, models);
        threads.push(thread);
        opening.push(thread.timings);
      } catch (error) {
        result.openError = errText(error);
        break;
      }
    }
    result.opened = threads.length;
    result.openMs = stats(opening.map((x) => x.storeOpen + x.harnessOpen + x.root));
    result.callsAtStart = meter.total;
    await this.#put("r:result", result, p);
    const deadline = this.#state.startedAt + p.runMs;
    const perThreadOut = threads.map(() => ({ answers: 0, unanswered: 0, error: null }));
    const loop = async (thread, out) => {
      while (Date.now() < deadline) {
        try {
          const submission = await thread.root.submit(
            { type: "input", content: `Turn ${out.answers}: go on.` }, BG);
          const settled = await submission.wait(BG);
          if (settled.status === "done") {
            out.answers += 1;
          } else {
            out.unanswered += 1;
          }
        } catch (error) {
          out.error = errText(error);
          return;
        }
      }
    };
    const work = (async () => {
      const stop = setTimeout(() => {
        for (const thread of threads) {
          thread.root.abort(BG).catch(() => {});
        }
      }, Math.max(0, deadline - Date.now()));
      await Promise.all(threads.map((thread, i) => loop(thread, perThreadOut[i])));
      clearTimeout(stop);
      const elapsedMs = Date.now() - this.#state.startedAt;
      const callsInRun = meter.total;
      await Promise.all(threads.map(closeThread));
      Object.assign(result, {
        status: "done", finishedAt: Date.now(), elapsedMs, callsInRun, calls: meter.summary(),
        perThread: perThreadOut, reports: threads.flatMap((thread) => thread.reports).slice(0, 5),
        beats: this.#state.beats,
      });
      await this.#put("r:result", result, p);
    })().catch(async (error) => {
      result.status = "failed";
      result.error = errText(error);
      await this.#put("r:result", result, p);
    });
    return { work };
  }

  /** P2: colos, ping latency over both shapes, new-thread first messages, commit latency. */
  async #p2(base, p) {
    const s = SHAPE.p2;
    const result = { ...base, status: "done", doorColo: p.doorColo, ownerColo: await traceColo() };
    const store = this.env.PISTORE.getByName("p2-ping");
    for (let i = 0; i < s.warm; i += 1) {
      await store.ping(i);
    }
    const timed = async (n, call) => {
      const samples = [];
      const started = now();
      for (let i = 0; i < n; i += 1) {
        const at = now();
        await call(i);
        samples.push(now() - at);
      }
      return { ...stats(samples), batchMeanMs: Math.round(((now() - started) / n) * 1000) / 1000 };
    };
    result.pingStub = await timed(p.pings, (i) => store.ping(i));
    const opened = await store.openSession(crypto.randomUUID());
    try {
      result.pingSession = await timed(p.pings, (i) => opened.session.ping(i));
    } finally {
      opened.session[Symbol.dispose]?.();
    }

    // New threads: store cold start and pi's migration (`storeOpen`), `Harness.open`,
    // `root`, then the first input's admission and settling, each with its store calls.
    const meter = new Meter();
    const fresh = [];
    const storeColos = [];
    const short = fauxModels(200, 0, s.threads + 2);
    for (let t = 0; t < s.threads; t += 1) {
      const thread = await openThread(this.env, `p2-new-${t}`, meter, short);
      await turn(thread, meter, "Hello.", "first");
      fresh.push({ timings: thread.timings, calls: thread.calls, instance: thread.opened.instance });
      storeColos.push((await thread.store.colo()).colo);
      await closeThread(thread);
    }
    result.newThread = fresh;
    result.storeColos = storeColos;
    // The same store again: warm (the live instance), then after `reset()` (a fresh
    // instance over the migrated database), with no first-use migration either time.
    const reopen = async (label) => {
      const thread = await openThread(this.env, "p2-new-0", meter, short);
      await turn(thread, meter, "Again.", "first");
      await closeThread(thread);
      return { label, timings: thread.timings, calls: thread.calls, instance: thread.opened.instance };
    };
    result.reopenWarm = await reopen("warm");
    try {
      await this.env.PISTORE.getByName("p2-new-0").reset();
    } catch {
      // `ctx.abort()` rejects the call that made it.
    }
    result.reopenAfterReset = await reopen("after reset");

    // Commit latency while `streams` threads stream at once (partial commits each second).
    const streamMeter = new Meter();
    const streams = [];
    const streaming = fauxModels(p.streamSeconds * 20 * 4, 20, s.streams);
    for (let t = 0; t < s.streams; t += 1) {
      streams.push(await openThread(this.env, `p2-stream-${t}`, streamMeter, streaming));
    }
    await Promise.all(streams.map((thread) => turn(thread, streamMeter, "Stream.")));
    await Promise.all(streams.map(closeThread));
    result.streaming = streamMeter.summary();
    result.memory = memoryReading();
    return result;
  }

  /**
   * P4: opens a Session on each seeded store `p4-0`.. in turn and keeps every one open,
   * each after one short turn, so pi holds its whole context in memory. At each
   * checkpoint it records how many are open; a reset shows as the alarm's retry.
   */
  async #p4(base, p) {
    const meter = new Meter();
    const checkpoints = [8, 32, 64, 128, 192, 256, 384, 512].filter((n) => n <= p.max);
    if (!checkpoints.includes(p.max)) {
      checkpoints.push(p.max);
    }
    const result = { ...base, status: "running", max: p.max, checkpoints: [] };
    await this.#put("r:result", result, p);
    const open = [];
    const cold = [];
    const started = now();
    const models = fauxModels(200, 0, p.max);
    try {
      for (let i = 0; i < p.max; i += 1) {
        const thread = await openThread(this.env, `p4-${i}`, meter, models);
        open.push(thread);
        await turn(thread, meter, "One more short answer, please.", "turn");
        cold.push(thread.timings);
        await this.#put("r:progress", { opened: open.length, at: Date.now() }, p);
        if (checkpoints.includes(open.length)) {
          result.checkpoints.push({
            n: open.length, elapsedMs: Math.round(now() - started),
            openMs: stats(cold.map((x) => x.storeOpen + x.harnessOpen + x.root)),
            turnMs: stats(cold.map((x) => x.turnAdmit + x.turnSettle)),
            contextChars: await contextChars(thread), memory: memoryReading(),
          });
          await this.#put("r:result", result, p);
        }
      }
      result.status = "done";
    } catch (error) {
      // A failure mid-run (the memory limit among them) keeps the checkpoints reached.
      result.status = "failed";
      result.error = errText(error);
      result.failedAt = open.length;
    } finally {
      result.calls = meter.summary();
      result.reports = open.flatMap((thread) => thread.reports).slice(0, 5);
      await Promise.all(open.map(closeThread));
    }
    return result;
  }

  /**
   * P7: the same `n` store calls as per-call stub requests and over one `RpcTarget`
   * session; then both stores are left idle for `idleMs` while the owner holds the
   * session, and each says whether its instance survived.
   */
  async #p7(base, p) {
    const result = { ...base, status: "done", n: SHAPE.p7N, idleMs: p.idleMs };
    const calls = this.env.PISTORE.getByName("p7-calls");
    const sessionStore = this.env.PISTORE.getByName("p7-session");
    const callsOpened = await calls.open(crypto.randomUUID());
    result.callsShape = await burst("stub", SHAPE.p7N, (i) => calls.ping(i));
    // The calls this object made on each store's stub; the session's own ride its one call.
    result.callsShape.stubCalls = SHAPE.p7N + 1;
    const opened = await sessionStore.openSession(crypto.randomUUID());
    const session = opened.session;
    result.sessionShape = await burst("session", SHAPE.p7N, (i) => session.ping(i));
    result.sessionShape.stubCalls = 1;
    result.sessionShape.sessionCalls = SHAPE.p7N;
    result.ids = { calls: callsOpened.id, session: opened.id };
    result.before = { calls: callsOpened.instance, session: opened.instance };
    result.idleFrom = Date.now();
    await sleep(p.idleMs);
    result.idleTo = Date.now();
    const attempt = async (fn) => {
      try {
        return { ok: await fn() };
      } catch (error) {
        return { error: errText(error) };
      }
    };
    result.after = {
      callsFresh: await attempt(() => calls.whoami()),
      sessionHeld: await attempt(() => session.whoami()),
      sessionFresh: await attempt(() => sessionStore.whoami()),
    };
    session[Symbol.dispose]?.();
    return result;
  }
}
