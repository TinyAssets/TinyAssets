/**
 * ta-latencyprobe: the owner-shaped half of the latency probe
 * (openspec/changes/clean-sheet-platform/latency-probe.md).
 *
 * Throwaway. `.github/workflows/latency-probe.yml` deploys it and deletes it. Its runtime is
 * the owner Worker's (`platform/owner/wrangler.jsonc`): the same date and flags,
 * `disable_ctx_exports` among them, no workers.dev URL. Two SQLite classes:
 *   - `Owner`, the stand-in for `OwnerDO`, where every case runs and is timed;
 *   - `Shard`, option A's second named object, bound by the one same-script binding `SHARD`
 *     (no `script_name`), as store probe P3 found production takes it.
 * Option B's R2 calls go the owner's real path, through the `FILES` service binding to
 * `ta-latencyprobe-files`, which holds the throwaway bucket; `BUCKET`, the same bucket bound
 * here directly, is the lower bound without that hop.
 *
 * Workers clocks advance only across I/O, so each sample is the I/O it times, in whole ms.
 */
import { DurableObject } from "cloudflare:workers";

import { traceColo } from "./trace.js";

export const SIZES = Object.freeze({ "1k": 1024, "64k": 64 * 1024, "1m": 1024 * 1024 });
const PARALLEL = 6;

/** Bytes of `size`, filled so no two runs' objects are the same content. */
function payload(size, seed) {
  const bytes = new Uint8Array(size);
  crypto.getRandomValues(bytes.subarray(0, Math.min(size, 65536)));
  for (let at = 65536; at < size; at += 65536) {
    bytes.copyWithin(at, 0, Math.min(65536, size - at));
  }
  new DataView(bytes.buffer).setUint32(0, seed);
  return bytes;
}

/** `op(i)` timed `n` times one after another, or `PARALLEL` at a time, each call on its own. */
async function timed(n, parallel, op) {
  const samples = [];
  const errors = [];
  const one = async (i) => {
    const start = Date.now();
    try {
      await op(i);
      samples.push(Date.now() - start);
    } catch (error) {
      errors.push(String(error).slice(0, 200));
    }
  };
  const started = Date.now();
  if (parallel) {
    for (let i = 0; i < n; i += PARALLEL) {
      await Promise.all(Array.from({ length: Math.min(PARALLEL, n - i) }, (_, k) => one(i + k)));
    }
  } else {
    for (let i = 0; i < n; i++) {
      await one(i);
    }
  }
  return { samples, errors: errors.slice(0, 5), failed: errors.length, wallMs: Date.now() - started };
}

/** The R2 paths a case may take: through the files Worker (the owner's real path), or direct. */
function store(env, path) {
  if (path === "direct") {
    return {
      put: async (key, bytes, create) => {
        const done = await env.BUCKET.put(key, bytes, create
          ? { onlyIf: new Headers({ "if-none-match": "*" }) } : undefined);
        return done !== null;
      },
      get: async (key) => {
        const object = await env.BUCKET.get(key);
        if (object === null) {
          throw new Error(`no object ${key}`);
        }
        return (await object.arrayBuffer()).byteLength;
      },
      list: async (prefix, limit) => (await env.BUCKET.list({ prefix, limit })).objects.length,
    };
  }
  return {
    put: (key, bytes, create) => env.FILES.put(key, bytes, create),
    get: (key) => env.FILES.get(key),
    list: (prefix, limit) => env.FILES.list(prefix, limit),
  };
}

export class Owner extends DurableObject {
  /** Where this object, its files Worker and `count` fresh shards run. */
  async where({ run, count = 4 }) {
    const shards = [];
    for (let i = 0; i < count; i++) {
      shards.push(await this.env.SHARD.getByName(`${run}-where-${i}`).where());
    }
    return { owner: await traceColo(), files: this.env.FILES ? await this.env.FILES.where()
      : null, shards, id: this.ctx.id.toString() };
  }

  /**
   * Option A. `cold`: the first call to each of `n` fresh shards (made by that call), a small
   * SQLite write and read. `rw`: the same call on one warm shard. `ping`: a call doing nothing,
   * the RPC alone.
   */
  async shard({ run, kind, n = 300, parallel = false }) {
    const warm = this.env.SHARD.getByName(`${run}-warm`);
    if (kind !== "cold") {
      await warm.rw(-1);
    }
    const op = kind === "cold"
      ? (i) => this.env.SHARD.getByName(`${run}-cold-${parallel ? "p" : "s"}-${i}`).rw(i)
      : kind === "rw" ? (i) => warm.rw(i) : () => warm.ping();
    return timed(n, parallel, op);
  }

  /**
   * Option B. `put`: create-only puts (`If-None-Match: *`) of fresh keys; `exists`: the same
   * put on keys already there (the precondition fails, nothing is written); `get_cold`: the
   * first read of each of `n` keys a put case wrote; `get_warm`: one key read again and again;
   * `list`: one list of `limit` keys under a prefix of at least that many.
   */
  async r2({ run, op, size = "1k", n = 300, parallel = false, path = "files", limit = 1000 }) {
    const bytes = SIZES[size];
    if (bytes === undefined) {
      throw new Error(`no size ${size}`);
    }
    const s = store(this.env, path);
    const prefix = `${run}/${path}/${size}/${parallel ? "p" : "s"}/`;
    if (op === "put") {
      const body = payload(bytes, 1);
      return timed(n, parallel, async (i) => {
        new DataView(body.buffer).setUint32(4, i);
        if (!await s.put(`${prefix}${i}`, body, true)) {
          throw new Error("a create-only put of a fresh key was refused");
        }
      });
    }
    if (op === "exists") {
      const body = payload(bytes, 2);
      return timed(n, parallel, async (i) => {
        if (await s.put(`${prefix}${i % 300}`, body, true)) {
          throw new Error("a create-only put over an existing key wrote");
        }
      });
    }
    if (op === "get_cold") {
      return timed(n, parallel, async (i) => {
        if (await s.get(`${prefix}${i}`) !== bytes) {
          throw new Error("short read");
        }
      });
    }
    if (op === "get_warm") {
      await s.get(`${prefix}0`);
      return timed(n, parallel, async () => {
        if (await s.get(`${prefix}0`) !== bytes) {
          throw new Error("short read");
        }
      });
    }
    if (op === "list") {
      return timed(n, parallel, async () => {
        // the put case of the same plan wrote `n` keys under this prefix
        if (await s.list(prefix, limit) < Math.min(limit, n)) {
          throw new Error("a list came back short");
        }
      });
    }
    throw new Error(`no op ${op}`);
  }

  /** Every object under the run's prefix deleted, through the direct binding; what is left. */
  async cleanup({ run }) {
    let deleted = 0;
    for (let round = 0; round < 1000; round++) {
      const listed = await this.env.BUCKET.list({ prefix: `${run}/`, limit: 1000 });
      if (listed.objects.length === 0) {
        break;
      }
      await this.env.BUCKET.delete(listed.objects.map((object) => object.key));
      deleted += listed.objects.length;
    }
    const left = (await this.env.BUCKET.list({ limit: 10 })).objects.length;
    return { deleted, left };
  }
}

export class Shard extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS t (i INTEGER PRIMARY KEY, at INTEGER, " +
                         "v TEXT)");
  }

  /** A small write and a read in this object's SQLite: one row, then a count. */
  rw(i) {
    this.ctx.storage.sql.exec("INSERT OR REPLACE INTO t VALUES (?, ?, ?)", i, Date.now(),
                              "x".repeat(100));
    return this.ctx.storage.sql.exec("SELECT count(*) AS n FROM t").one().n;
  }

  ping() {
    return 1;
  }

  async where() {
    return { ...(await traceColo()), id: this.ctx.id.toString() };
  }
}

export default {
  fetch() {
    return new Response("not found", { status: 404 });
  },
};
