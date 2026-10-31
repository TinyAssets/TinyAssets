/**
 * The facet class of the facet probe (facet-probe.md): one module, used two ways. parent.js
 * imports it as the static `Child` class (reached as `ctx.exports.Child`, a class binding,
 * or the raw class), and config.mjs copies it to child.txt, which parent.js imports as text
 * and hands to Worker Loader, so the loader's facet runs this same code. It imports only
 * `cloudflare:workers` for that reason. Throwaway: nothing in the product imports it.
 */
import { DurableObject } from "cloudflare:workers";

/** Iterations between yields in burnYield: tens of milliseconds of CPU. */
const CHUNK = 20_000_000;

function errText(error) {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return text.slice(0, 500);
}

/** `bytes` of fresh random data (getRandomValues fills at most 65,536 bytes a call). */
function randomBytes(bytes) {
  const out = new Uint8Array(bytes);
  for (let at = 0; at < bytes; at += 65_536) {
    crypto.getRandomValues(out.subarray(at, Math.min(bytes, at + 65_536)));
  }
  return out;
}

/** One outcome of a reach attempt: the value, or the error's text. */
async function attempt(fn) {
  try {
    return { ok: true, value: await fn() };
  } catch (error) {
    return { ok: false, error: errText(error) };
  }
}

export class Child extends DurableObject {
  #instance = crypto.randomUUID();

  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS fill (i INTEGER PRIMARY KEY, b BLOB NOT NULL)");
  }

  ping() {
    return 1;
  }

  info() {
    const sql = this.ctx.storage.sql;
    const exportsKeys = this.ctx.exports ? Object.keys(this.ctx.exports).sort() : null;
    return {
      kind: "Child", instance: this.#instance, id: String(this.ctx.id), idName: this.ctx.id?.name ?? null,
      size: sql.databaseSize, rows: sql.exec("SELECT count(*) AS n FROM fill").one().n,
      envKeys: Object.keys(this.env ?? {}).sort(), exports: typeof this.ctx.exports, exportsKeys,
      facets: typeof this.ctx.facets,
    };
  }

  /** `rows` rows of `rowBytes` random bytes, synced; then the database's size. */
  async fill(rows, rowBytes) {
    const sql = this.ctx.storage.sql;
    for (let i = 0; i < rows; i += 1) {
      sql.exec("INSERT INTO fill (b) VALUES (?)", randomBytes(rowBytes));
    }
    await this.ctx.storage.sync();
    return { size: sql.databaseSize };
  }

  /** What this facet can reach on its own: the network, each binding, ctx.exports. */
  async reach() {
    const env = this.env ?? {};
    const out = {};
    out.fetch = await attempt(async () => {
      const response = await fetch("https://www.cloudflare.com/cdn-cgi/trace", { signal: AbortSignal.timeout(5000) });
      const text = await response.text();
      return { status: response.status, colo: /^colo=(.*)$/m.exec(text)?.[1] ?? null };
    });
    if (env.SVC) {
      out.SVC = await attempt(async () => (await env.SVC.fetch("https://dummy/whoami")).text());
    }
    if (env.KV) {
      out.KV = await attempt(async () => ({ get: await env.KV.get("facetprobe-absent") }));
    }
    if (env.PISTORE) {
      out.PISTORE = await attempt(() => env.PISTORE.getByName("reach-from-facet").whoami());
    }
    if (this.ctx.exports?.Parent) {
      out.exportsParent = await attempt(() => this.ctx.exports.Parent.getByName("reach-from-facet").whoami());
    }
    if (this.ctx.exports?.ThreadStore) {
      out.exportsThreadStore = await attempt(() => this.ctx.exports.ThreadStore.getByName("reach-from-facet").whoami());
    }
    out.facetsInFacet = await attempt(async () => {
      const nested = this.ctx.facets.get("nested", () => ({ class: this.ctx.exports.Child }));
      return (await nested.info()).kind;
    });
    return out;
  }

  /** CPU for about `ms`, yielding to the event loop between chunks (the clock moves only then). */
  async burnYield(ms) {
    const start = Date.now();
    let iters = 0;
    let x = 1;
    while (Date.now() - start < ms) {
      for (let i = 0; i < CHUNK; i += 1) {
        x = (Math.imul(x, 1103515245) + 12345) | 0;
      }
      iters += CHUNK;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    return { ms: Date.now() - start, iters, x };
  }

  /** `iters` iterations with no yield at all: a true busy loop. */
  burnSolid(iters) {
    let x = 1;
    for (let i = 0; i < iters; i += 1) {
      x = (Math.imul(x, 1103515245) + 12345) | 0;
    }
    return { iters, x };
  }

  throwIt() {
    throw new Error("facetprobe: thrown by the facet on purpose");
  }

  /** Allocates and touches 16 MB arrays until the isolate is out of memory. */
  oom() {
    const keep = [];
    for (;;) {
      keep.push(new Uint8Array(16 * 1024 * 1024).fill(keep.length % 251));
    }
  }
}

export default {
  fetch() {
    return new Response("not found", { status: 404 });
  },
};
