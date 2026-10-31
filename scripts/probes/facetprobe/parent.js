/**
 * ta-facetprobe: the owner-like half of the live facet probe P13
 * (openspec/changes/clean-sheet-platform/facet-probe.md has the method, the questions, the
 * reading key and the results).
 *
 * `Parent` stands in for OwnerDO; `Child` (child.js) is the facet; `ThreadStore` stands in
 * for the per-thread store, a top-level object in the same script bound as `PISTORE`, as
 * the owner Worker binds it, so Q6 reads a facet against a separate top-level object in
 * the same run. A facet's class comes from one of six sources (Q2): `ctx.exports.Child`,
 * Worker Loader's `getDurableObjectClass`, the raw class, the `PISTORE` namespace, a
 * Durable Object binding to the unmigrated `Child` (`CHILD_CLASS`), or an unsafe
 * `durable_object_class` binding (`CHILD_DOCLASS`). Which copy has which is config.mjs's.
 *
 * Throwaway. `.github/workflows/facet-probe.yml` deploys it as several copies, drives it
 * through the `ta-facetprobe-door` Workflow and deletes every copy. No copy has a route or
 * a workers.dev URL. Nothing in the product imports it.
 */
import { DurableObject } from "cloudflare:workers";

import { Child } from "./child.js";
import CHILD_SOURCE from "./child.txt";
import { SHAPE } from "./shape.mjs";
import { errText, stats } from "./stats.mjs";

export { Child };

const LOADER_ID = "ta-facetprobe-child-v1";
export const SOURCES = Object.freeze(["exports", "loader", "raw", "pistore", "classbind", "unsafe"]);
/** A database-full or size-limit error, as SQLite or the runtime may word it. */
export const LIMIT = /SQLITE_FULL|database or disk is full|disk is full|storage limit|size limit|exceeded.{0,40}(storage|size)|too large|maximum database size/i;

const now = () => performance.now();

/** `n` sequential calls of `call`, each timed; every error is counted, the first kept. */
async function series(n, call) {
  const samples = [];
  let errors = 0;
  let firstError = null;
  for (let i = 0; i < n; i += 1) {
    const at = now();
    try {
      await call(i);
      samples.push(now() - at);
    } catch (error) {
      errors += 1;
      firstError ??= errText(error);
    }
  }
  return { ...stats(samples), errors, firstError };
}

function randomBytes(bytes) {
  const out = new Uint8Array(bytes);
  for (let at = 0; at < bytes; at += 65_536) {
    crypto.getRandomValues(out.subarray(at, Math.min(bytes, at + 65_536)));
  }
  return out;
}

export class ThreadStore extends DurableObject {
  #instance = crypto.randomUUID();

  ping() {
    return 1;
  }

  whoami() {
    return { kind: "ThreadStore", instance: this.#instance, id: String(this.ctx.id),
             size: this.ctx.storage.sql.databaseSize, envKeys: Object.keys(this.env).sort() };
  }
}

export class Parent extends DurableObject {
  #instance = crypto.randomUUID();
  #q4 = {};

  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS fill (i INTEGER PRIMARY KEY, b BLOB NOT NULL)");
  }

  #get(key) {
    const row = this.ctx.storage.sql.exec("SELECT v FROM kv WHERE k = ?", key).toArray()[0];
    return row ? JSON.parse(row.v) : null;
  }

  #set(key, value) {
    this.ctx.storage.sql.exec("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", key, JSON.stringify(value));
  }

  /** The facet class `source` names; throws when this copy cannot supply one. */
  #classFor(source) {
    switch (source) {
      case "exports":
        if (!this.ctx.exports) {
          throw new Error(`ctx.exports is ${typeof this.ctx.exports}`);
        }
        return this.ctx.exports.Child;
      case "loader":
        if (!this.env.LOADER) {
          throw new Error("no LOADER binding");
        }
        return this.env.LOADER.get(LOADER_ID, () => ({
          compatibilityDate: "2026-10-23", mainModule: "child.js", modules: { "child.js": CHILD_SOURCE },
        })).getDurableObjectClass("Child");
      case "raw":
        return Child;
      case "pistore":
        return this.env.PISTORE;
      case "classbind":
        if (!this.env.CHILD_CLASS) {
          throw new Error("no CHILD_CLASS binding");
        }
        return this.env.CHILD_CLASS;
      case "unsafe":
        if (!this.env.CHILD_DOCLASS) {
          throw new Error("no CHILD_DOCLASS binding");
        }
        return this.env.CHILD_DOCLASS;
      default:
        throw new Error(`unknown source ${source}`);
    }
  }

  /** The facet `name`, its class from `source`; `id` (optional) is the id it sees as its own. */
  facet(name, source, id) {
    return this.ctx.facets.get(name, () => ({ class: this.#classFor(source), ...(id ? { id } : {}) }));
  }

  whoami() {
    return { kind: "Parent", instance: this.#instance, id: String(this.ctx.id),
             size: this.ctx.storage.sql.databaseSize, envKeys: Object.keys(this.env).sort(),
             exports: typeof this.ctx.exports,
             exportsKeys: this.ctx.exports ? Object.keys(this.ctx.exports).sort() : null,
             facets: typeof this.ctx.facets };
  }

  ping() {
    return { instance: this.#instance };
  }

  /** Q2: each class source, tried in its own facet; a source either answers info() or throws. */
  async q2() {
    const out = { parent: this.whoami(), sources: {} };
    for (const source of SOURCES) {
      try {
        const facet = this.facet(`q2-${source}`, source);
        const info = await facet.info();
        const filled = await facet.fill(1, 1000);
        out.sources[source] = { ok: true, kind: info.kind, size: filled.size, envKeys: info.envKeys,
                                exports: info.exports };
      } catch (error) {
        out.sources[source] = { ok: false, error: errText(error) };
      }
    }
    return out;
  }

  /** Q3: what the facet sees in `env` and `ctx.exports`, and what it can reach by itself. */
  async q3({ source }) {
    const facet = this.facet(`q3-${source}`, source);
    const info = await facet.info();
    const reach = await facet.reach();
    return { source, parent: this.whoami(), facet: info, reach };
  }

  /** Q5, small: write, delete, get again; the stub from before the delete, called after. */
  async q5({ source }) {
    const name = `q5-${source}`;
    const facet = this.facet(name, source);
    await facet.fill(SHAPE.q5Rows, SHAPE.rowBytes);
    const before = await facet.info();
    const parentBefore = this.ctx.storage.sql.databaseSize;
    const at = now();
    this.ctx.facets.delete(name);
    const deleteMs = Math.round((now() - at) * 1000) / 1000;
    let oldStub;
    try {
      oldStub = { ok: true, value: await facet.info() };
    } catch (error) {
      oldStub = { ok: false, error: errText(error) };
    }
    const after = await this.facet(name, source).info();
    return { source, before, after, deleteMs, oldStub, parentBefore,
             parentAfter: this.ctx.storage.sql.databaseSize, freshInstance: after.instance !== before.instance };
  }

  /**
   * Q6: 1,000 parent -> facet pings on a facet with the parent's id and on one with its own
   * id, against 1,000 pings to a top-level ThreadStore in the same script (the store
   * probe's path). The ids go to analytics.mjs for the billing reading.
   */
  async q6({ source, n = SHAPE.q6N }) {
    const shared = this.facet("q6-shared-id", source);
    const own = source === "exports"
      ? this.facet("q6-own-id", source, this.ctx.exports.Parent.idFromName("q6-own-id-facet"))
      : null;
    const store = this.env.PISTORE.getByName(`q6-store-${source}`);
    const out = { source, n, parentId: String(this.ctx.id) };
    for (const [key, stub] of [["sharedId", shared], ["ownId", own], ["store", store]]) {
      if (!stub) {
        continue;
      }
      await series(SHAPE.q6Warm, () => stub.ping());
      out[key] = await series(n, () => stub.ping());
    }
    out.ids = { sharedId: (await shared.info()).id, ownId: own ? (await own.info()).id : null,
                store: (await store.whoami()).id };
    return out;
  }

  /**
   * Q4: starts `kase` in its own facet and returns at once; the outcome is kept in storage
   * when it settles, so a parent that survives can report it. The door pings meanwhile.
   */
  q4Start({ kase, source, ms, iters }) {
    const facet = this.facet(`q4-${kase}`, source);
    const startedAt = Date.now();
    const op = {
      yield: () => facet.burnYield(ms), solid: () => facet.burnSolid(iters), throw: () => facet.throwIt(),
      overcpu: () => facet.burnSolid(iters), oom: () => facet.oom(),
    }[kase];
    if (!op) {
      throw new Error(`unknown Q4 case ${kase}`);
    }
    this.#set(`q4-started:${kase}`, { instance: this.#instance, startedAt });
    const settle = (outcome) => this.#set(`q4:${kase}`, { ...outcome, ms: Date.now() - startedAt,
                                                           instance: this.#instance });
    op().then((value) => settle({ ok: true, value }), (error) => settle({ ok: false, error: errText(error) }));
    return { instance: this.#instance, startedAt };
  }

  /** Q4: the case's outcome if it settled, this instance, and whether the facet answers again. */
  async q4Outcome({ kase, source, probeFacet }) {
    const out = { instance: this.#instance, started: this.#get(`q4-started:${kase}`),
                  outcome: this.#get(`q4:${kase}`) };
    if (probeFacet) {
      try {
        out.facetAfter = { ok: true, value: await this.facet(`q4-${kase}`, source).info() };
      } catch (error) {
        out.facetAfter = { ok: false, error: errText(error) };
      }
    }
    return out;
  }

  #fillSelf(rows) {
    const sql = this.ctx.storage.sql;
    for (let i = 0; i < rows; i += 1) {
      sql.exec("INSERT INTO fill (b) VALUES (?)", randomBytes(SHAPE.rowBytes));
    }
  }

  /** Every database of a fill plan: the parent's and each facet's size, read now. */
  async #sizes(plan) {
    const out = { parent: this.ctx.storage.sql.databaseSize };
    for (const job of plan.jobs) {
      if (job.target !== "self") {
        try {
          out[job.target] = (await this.facet(job.target, plan.source).info()).size;
        } catch (error) {
          out[job.target] = errText(error);
        }
      }
    }
    return out;
  }

  /**
   * Q1: write each job's target (`self` or a facet name) up to its bytes, one after another,
   * in alarms. A limit error ends its job and the next runs; any other error aborts the plan.
   */
  async fillStart({ jobs, deadlineMs, source }) {
    const existing = this.#get("fill");
    if (existing) {
      return { already: true, status: existing.status };
    }
    const plan = {
      source, status: "running", at: 0, startedAt: Date.now(), deadline: Date.now() + deadlineMs,
      alarms: 0, resets: 0, inAlarm: false,
      jobs: jobs.map((job) => ({ target: job.target, bytes: job.bytes, rows: 0, size: null, status: "pending",
                                  points: [], startedAt: null, endedAt: null })),
    };
    this.#set("fill", plan);
    await this.ctx.storage.setAlarm(Date.now());
    return { started: true, instance: this.#instance };
  }

  async alarm() {
    const plan = this.#get("fill");
    if (!plan || plan.status !== "running") {
      return;
    }
    if (plan.inAlarm) {
      // The last alarm never finished: the object was reset under it.
      plan.resets += 1;
      if (plan.resets > 3) {
        plan.status = "aborted: the parent was reset more than 3 times";
      }
    }
    plan.alarms += 1;
    plan.inAlarm = true;
    this.#set("fill", plan);
    const windowEnd = Date.now() + SHAPE.alarmWindowMs;
    while (plan.status === "running" && Date.now() < windowEnd) {
      if (Date.now() > plan.deadline) {
        plan.status = "capped by time";
        break;
      }
      const job = plan.jobs[plan.at];
      if (!job) {
        plan.status = "done";
        break;
      }
      job.startedAt ??= Date.now();
      job.status = "writing";
      try {
        let size;
        if (job.target === "self") {
          this.#fillSelf(SHAPE.chunkRows);
          await this.ctx.storage.sync();
          size = this.ctx.storage.sql.databaseSize;
        } else {
          size = (await this.facet(job.target, plan.source).fill(SHAPE.chunkRows, SHAPE.rowBytes)).size;
        }
        job.rows += SHAPE.chunkRows;
        const crossed = Math.floor(size / SHAPE.pointBytes) > Math.floor((job.size ?? 0) / SHAPE.pointBytes);
        job.size = size;
        if (crossed) {
          job.points.push({ t: Date.now() - plan.startedAt, size, parent: this.ctx.storage.sql.databaseSize });
        }
        if (size >= job.bytes) {
          job.status = "reached";
          job.endedAt = Date.now();
          job.sizesAtEnd = await this.#sizes(plan);
          plan.at += 1;
        }
      } catch (error) {
        const text = errText(error);
        job.status = LIMIT.test(text) ? "limit" : "error";
        job.error = text;
        job.endedAt = Date.now();
        job.sizesAtEnd = await this.#sizes(plan);
        plan.at += 1;
        if (job.status === "error") {
          plan.status = "aborted: a non-limit error";
        }
      }
      this.#set("fill", plan);
    }
    plan.inAlarm = false;
    if (plan.status === "running" && !plan.jobs[plan.at]) {
      plan.status = "done";
    }
    plan.endedAt = plan.status === "running" ? null : Date.now();
    this.#set("fill", plan);
    if (plan.status === "running") {
      await this.ctx.storage.setAlarm(Date.now());
    }
  }

  /** Q1's progress, from storage only: reading it wakes no facet. */
  fillStatus() {
    return { plan: this.#get("fill"), parent: this.ctx.storage.sql.databaseSize, instance: this.#instance,
             id: String(this.ctx.id) };
  }

  /** Q1's end: every database's size, read from each facet. */
  async fillSizes() {
    const plan = this.#get("fill");
    return plan ? this.#sizes(plan) : null;
  }

  /** Q5, at size: delete a filled facet, then get it again. */
  async deleteFacet({ name, source }) {
    const facet = this.facet(name, source);
    const before = await facet.info();
    const parentBefore = this.ctx.storage.sql.databaseSize;
    const at = now();
    this.ctx.facets.delete(name);
    const deleteMs = Math.round((now() - at) * 1000) / 1000;
    const after = await this.facet(name, source).info();
    return { name, before, after, deleteMs, parentBefore, parentAfter: this.ctx.storage.sql.databaseSize,
             freshInstance: after.instance !== before.instance, deletedAt: Date.now() };
  }
}

export default {
  fetch() {
    return new Response("not found", { status: 404 });
  },
};
