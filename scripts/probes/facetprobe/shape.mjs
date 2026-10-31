/**
 * The fixed shape of each facet-probe case (facet-probe.md): the sizes and counts
 * parent.js runs and drive.mjs's reading key is written against. One module, so the two
 * cannot drift; config.mjs copies it beside parent.js for the bundle.
 */
export const GB = 1_000_000_000;

/** Every probe copy: its script name, and the binding the door reaches its root class by. */
export const COPIES = Object.freeze({
  main: Object.freeze({ script: "ta-facetprobe", binding: "PARENT_MAIN" }),
  exports: Object.freeze({ script: "ta-facetprobe-exports", binding: "PARENT_EXPORTS" }),
  classbind: Object.freeze({ script: "ta-facetprobe-classbind", binding: "PARENT_CLASSBIND" }),
  unsafe: Object.freeze({ script: "ta-facetprobe-unsafe", binding: "PARENT_UNSAFE" }),
  agents: Object.freeze({ script: "ta-facetprobe-agents", binding: "AGENTS_OURS" }),
  "agents-exports": Object.freeze({ script: "ta-facetprobe-agents-exports", binding: "AGENTS_EXPORTS" }),
});
export const DUMMY = "ta-facetprobe-dummy";
/** The door is a Worker and a Workflow of the same name. */
export const DOOR = "ta-facetprobe-door";
export const KV_TITLE = "ta-facetprobe-kv";

/**
 * A database-full or size-limit error, as SQLite or the runtime may word it. Only size
 * words: an error about request sizes or operation counts is not a storage limit.
 */
export const LIMIT = /SQLITE_FULL|database or disk is full|disk is full|storage limit|size limit|exceeded.{0,40}(size|database|bytes)|maximum database size/i;
/** The documented 10 GB may be 10^9 bytes or GiB: a limit reading accepts either. */
export const GIB_RATIO = 1.073741824;

export const SHAPE = Object.freeze({
  /** Q1: one row's blob, under the 2 MB row limit; fresh random bytes per row. */
  rowBytes: 1_900_000,
  /** Q1: rows per write call (one facet RPC, or one parent batch), then a sync. */
  chunkRows: 8,
  /** Q1: the documented per-object limit (1 GB = 10^9 bytes, as Cloudflare bills). */
  limitBytes: 10 * GB,
  /** Q1: the (b) and (c) fills, each well past half the limit, so two together pass it. */
  partBytes: 6 * GB,
  /** Q1 (a): one facet is written until a limit error or this cap, past 10 GB. */
  capBytes: 11 * GB,
  /** Q1: how long one alarm writes before it hands on to the next. */
  alarmWindowMs: 15_000,
  /** Q1: a progress point each time a database grows by this much. */
  pointBytes: GB / 2,
  /** Q5: rows the small delete case writes before it deletes. */
  q5Rows: 3,
  /** Q6: pings per series, after a warm-up. */
  q6N: 1000,
  q6Warm: 20,
  /** Q4: pings before a case and while it runs, and the burns' lengths. */
  q4BaselinePings: 30,
  q4YieldMs: 10_000,
  q4SolidMs: 5_000,
  q4OverCpuMs: 45_000,
  /** Q8: trials per variant, and the old rows each trial makes durable first. */
  q8N: 20,
  q8Rows: 20,
  q8RowBytes: 10_000,
  /** Q4: parent pings are spaced this far apart while a facet runs. */
  q4PingGapMs: 100,
});
