/**
 * Restore (d3 §G; `operator_research.md` §3, adopted whole; `docs/decisions/untangle-2026-11/
 * r5r.md`): the whole owner, from one finished generation (`backup.ts`, `export.ts`), into the
 * owner's own object, only while that object is `pending` and empty. The platform's alone: its
 * doors take the reserved caller (`PLATFORM_CALLER`), which only the operator channel binds
 * for it (`doors/src/operator.ts`, ops `restore.*`).
 *
 * **What it reads.** The generation's root, its base parts and its log segments, through the
 * files Worker's manifest reads (`GenerationSource`, `FILES`), the same reads an export makes.
 * The files Worker opens each under the owner's data key; nothing here holds a key or writes
 * to R2. A restore from the off-account copy (lane R's eighth PR) puts the copy's bytes back
 * at the same R2 keys first (the seal binds the key, `r4.md` item 6), so this reads them
 * unchanged: only where `{namespace, gen}` comes from differs.
 *
 * **The object takes the generation's namespace and wrapped key** (`file_space`), so every
 * chunk a content names is found where it is, under the same key: no chunk moves.
 *
 * **The steps**, each a unit or a bounded run of units, each resuming from the object's own
 * record (`owner_restore`), so a crash anywhere repeats nothing that committed and a second
 * run goes on or refuses, never loads a row twice:
 * 1. `restoreBegin {namespace, gen}`: refused unless the object is `pending` and empty
 *    (`emptiness`), and unless the root opens for the namespace and part 0's header names
 *    format `tinyassets-export` version 3, this object's owner, this generation and this
 *    object's schema. Then, in one unit, the namespace and key adopted and the record made.
 * 2. **index**: each base part's span of tables (the export's order), one unit a part.
 * 3. **log**: each log segment's lines staged in `restore_rows`, one unit a segment, the last
 *    line per key winning and a `gone` line's key held as gone: the stream read as the owner
 *    at E (`export.ts`, "Read last line per key wins"). The base is fuzzy, so a base row the
 *    log names never goes to its table; staging the log alone keeps the object's rows at one
 *    copy, the log's at most `LOG_BYTES` (a generation whose log passed it is refused: a
 *    recorded ceiling, `r5r.md`).
 * 4. **apply**: table by table in `loadOrder`'s order, a base part's rows of the table but those
 *    the log names, a unit a part, then the log's rows of it, a slice a unit. The schema's
 *    triggers rebuild every counter the export leaves out, and each exported column a
 *    trigger keeps as a reference count (`refs`) starts at 0 for them to rebuild. A column the
 *    export leaves out is left to its default, or, where the schema requires one (an inbound
 *    route, an egress token: credentials the backup never held), made afresh and held by no
 *    one.
 * 5. **check**: the same rows read back from their tables, column for column, and every
 *    table's count, equal to the generation's; a difference fails the restore, recorded, and
 *    the owner is never admitted. Then **chunks**: every chunk a content names read back
 *    through `FILES` with its hash verified. A read that fails is thrown, so the next step
 *    tries it again.
 * 6. **clear**: the staged rows deleted, a slice a unit.
 * 7. **done**, one unit: the fence (every outside client and API key revoked, every MCP grant
 *    fenced, every inbound binding disabled at a new revision, every connection marked for a
 *    reconnect: their secrets and routes were never in the backup), the allocators the export
 *    leaves out moved past everything the rows name (`ALLOCATORS`), the generation recorded as
 *    the object's newest finished one, and the owner's admission as it stood at E (`held`).
 *
 * While a restore is under way, no lane runs (`restoring`, the schedule's halt) and every door
 * but the platform's refuses: the owner is `pending`.
 *
 * **What the owner loses** (the kit's plan says so): its sessions, every agent's grant, its API
 * keys, its model connections' secrets, its inbound URLs, and anything written after E.
 */
import { HEX16, HEX32, randomHex } from "../../contract/src/ids";
import { LOG_BYTES } from "../../contract/src/limits";
import type { ChunkStore, ManifestRoot } from "../../contract/src/files";
import type { RestoreAnswer, RestoreBeginRequest, RestoreSource } from "../../contract/src/owner";
import { Refusal } from "../../contract/src/refusal";

import { clock } from "./clock";
import { exported } from "./export";
import type { OwnerStorage } from "./gate";
import type { Door, OwnerState } from "./owner-do";
import { MIGRATIONS } from "./schema";

/** Where a restore reads a generation: the files Worker's manifest reads (`FILES`). */
export type GenerationSource =
  Pick<ChunkStore, "getManifestRoot" | "getManifestPart" | "getManifestSegment">;

const FORMAT = "tinyassets-export";
const VERSION = 3;

/**
 * One exported table as a restore loads it: its name, the columns a trigger keeps as a
 * reference count (`refs`: 0 at insert, rebuilt by the rows that name it, then checked), and
 * the columns held back until the check passes (`held`).
 */
interface Load {
  readonly table: string;
  readonly refs?: readonly string[];
  readonly held?: readonly string[];
}

/**
 * Every exported table in its load order (`restore.test.ts` holds it to the schema's
 * triggers). A table whose reference counts another's triggers keep loads before that table,
 * so its rows are there to count on (contents before what names them, chunks before their
 * lists, messages and readers before recipients, agents before messages, clients before agents
 * and cards). A table whose other trigger-kept columns are sums over rows that may since have
 * been pruned (`model_spend` over reservations, a run's requests, tokens and cost over its
 * egress) loads after the rows that add to it, its value as the stream holds it: their
 * triggers find no row to add to, or a row its own load then overwrites. The owner's admission
 * keeps `pending` until the check passes (`held`). A function, as `exported` is, so no module
 * holds the table.
 */
export function loadOrder(): readonly Load[] {
  return Object.freeze([
    { table: "object_owner" },
    { table: "owner_admission", held: ["state", "reasons"] },
    { table: "owner_policy" },
    { table: "policy_proposals" },
    { table: "policy_receipts" },
    { table: "outside_clients", refs: ["agents", "held"] },
    { table: "agent_cards" },
    { table: "connections" },
    { table: "engine_signins" },
    { table: "owner_notices" },
    { table: "file_chunks", refs: ["refs"] },
    { table: "file_contents", refs: ["refs"] },
    { table: "file_content_chunks" },
    { table: "file_entries" },
    { table: "file_history" },
    { table: "file_temps" },
    { table: "starter_receipts" },
    { table: "starter_state" },
    { table: "collection_rows" },
    { table: "collection_history" },
    { table: "collection_erasures" },
    { table: "collection_seqs" },
    { table: "collection_clock" },
    { table: "outside_agents", refs: ["unacked_to_owner"] },
    { table: "native_agents", refs: ["unacked_to_owner"] },
    { table: "ledger_messages", refs: ["unacked"] },
    { table: "message_bodies" },
    { table: "ledger_readers", refs: ["unacked"] },
    { table: "ledger_recipients" },
    { table: "archive_segments" },
    { table: "archive_purges" },
    { table: "ledger_ids" },
    { table: "model_reservations" },
    { table: "egress_reservations" },
    { table: "model_spend" },
    { table: "runs" },
    { table: "run_inputs" },
    { table: "run_schedules" },
    { table: "conversations" },
    { table: "owner_keys" },
    { table: "inbound_bindings" },
  ].map((load) => Object.freeze(load)));
}

/**
 * The tables a fresh object's migrations seed with their one row: a restore's load updates
 * that row, and a restore takes an object whose other exported tables are empty.
 */
export const SEEDED: readonly string[] = Object.freeze(["owner_admission", "owner_policy",
                                                        "collection_clock"]);

/** A restore's record (`owner_restore`), as its row holds it. */
type Held = {
  readonly ns: string;
  readonly gen: string;
  readonly step: Step;
  readonly at: number;
  readonly after: string | null;
  readonly held: string | null;
  readonly spans: string | null;
  readonly failed: string | null;
};

type Step = "index" | "log" | "apply" | "check" | "chunks" | "clear" | "done" | "failed";

/** How long one `restoreStep` call takes new work: its answer is well inside the kit's wait. */
const RESTORE_PASS_MS = 30_000;
/** Logged rows one apply, check or clear unit takes (a base part's are one unit's). */
const SLICE_ROWS = 500;
/** Chunks one check unit reads back. */
const SLICE_CHUNKS = 64;

/** The restore's record, or null when none was begun. A read. */
function record(sql: Pick<SqlStorage, "exec">): Held | null {
  return sql.exec<Held>("SELECT ns, gen, step, at, after, held, spans, failed FROM owner_restore")
    .toArray()[0] ?? null;
}

/**
 * Whether a restore is under way: begun and not yet done. While it is, no lane runs (the
 * schedule's halt, `owner-do.ts`). A read.
 */
export function restoring(storage: OwnerStorage): boolean {
  const held = record(storage.sql);
  return held !== null && held.step !== "done";
}

/**
 * Why this object can't be restored into, or null: an object not `pending`, or one holding
 * anything but what its migrations seeded (`SEEDED`), a namespace, or a restore already. In a
 * unit.
 */
function emptiness(o: OwnerState): string | null {
  const { state } = o.admission();
  if (state !== "pending") {
    return `the owner is ${state}: a restore takes only a pending owner`;
  }
  if (record(o.storage.sql) !== null) {
    return "a restore of this owner was begun already";
  }
  const held = o.files.storedNamespace() !== null ? ["a namespace"]
    : Object.keys(exported()).filter((table) => o.storage.sql.exec<{ n: number }>(
      `SELECT count(*) AS n FROM ${table}`).one().n > (SEEDED.includes(table) ? 1 : 0));
  return held.length > 0
    ? `the owner's object is not empty (${held.join(", ")}): a restore takes only an empty one`
    : null;
}

/** A refusal of the restore, terminal: what is asked can't be done to this object. */
const refused = (why: string) => new Refusal("refused", why);

/** A line of a part or a segment, as JSON. */
type Line = { readonly t: string } & Readonly<Record<string, unknown>>;

function lines(bytes: Uint8Array): Line[] {
  return new TextDecoder().decode(bytes).trimEnd().split("\n")
    .map((line) => JSON.parse(line) as Line);
}

/** The generation's root, read through the source: refused when there is none. */
async function rootOf(source: GenerationSource, ns: string, gen: string): Promise<ManifestRoot> {
  const root = await source.getManifestRoot(ns, gen);
  if (root === null) {
    throw refused(`the namespace holds no finished generation ${gen}`);
  }
  return root;
}

/** The `index`th object of the generation (its parts, then its segments), read whole. */
function objectAt(source: GenerationSource, ns: string, root: ManifestRoot,
                  index: number): Promise<Uint8Array> {
  const part = root.parts[index];
  if (part !== undefined) {
    return source.getManifestPart(ns, root.key, root.gen, index, part.sha256);
  }
  const segment = root.segments[index - root.parts.length]!;
  return source.getManifestSegment(ns, root.key, root.gen, segment.a, segment.b,
                                   segment.sha256);
}

/** The key a staged row is held under: its key's values, as JSON (`replay`'s). */
const keyOf = (table: string, row: Readonly<Record<string, unknown>>): string =>
  JSON.stringify(exported()[table]!.key.map((column) => row[column]));

/** A value as the stream carries it, as SQLite takes it: bytes from their base64. */
function cell(value: unknown): SqlStorageValue {
  if (typeof value === "object" && value !== null && "$base64" in value) {
    return new Uint8Array(Buffer.from((value as { $base64: string }).$base64, "base64"))
      .buffer as ArrayBuffer;
  }
  return value as SqlStorageValue;
}

/** A column's value as the stream carries it (`export.ts`'s `value`). */
function streamed(value: unknown): unknown {
  return value instanceof ArrayBuffer ? { $base64: Buffer.from(value).toString("base64") } : value;
}

/**
 * The columns the export leaves out that the schema requires (`NOT NULL` and no default): each
 * made afresh as 32 random bytes in hex, held by no one (an inbound route, an egress token). A
 * read of the schema, so a column a later migration adds is covered by the same rule.
 */
function required(sql: Pick<SqlStorage, "exec">, table: string): string[] {
  const omitted = exported()[table]!.omitted ?? {};
  return sql.exec<{ name: string; notnull: number; dflt_value: SqlStorageValue }>(
    `PRAGMA table_info(${table})`).toArray()
    .filter((column) => Object.hasOwn(omitted, column.name) && column.notnull === 1
            && column.dflt_value === null)
    .map((column) => column.name);
}

/** A line's table and row (a `row` line's row, or null for a `gone` line), or null for none. */
function rowOf(line: Line): { table: string; k: string; row: Record<string, unknown> | null }
    | null {
  if (line.t === "header" || line.t === "chunk") {
    return null;  // part 0's header, held at begin; a chunk's bytes are in the bucket
  }
  const table = line["table"];
  if (typeof table !== "string" || !Object.hasOwn(exported(), table)
      || (line.t !== "row" && line.t !== "gone")) {
    throw refused(`the generation holds a line this schema has no table for (${line.t})`);
  }
  if (line.t === "gone") {
    return { table, k: JSON.stringify(line["key"]), row: null };
  }
  const row = line["row"] as Record<string, unknown>;
  return { table, k: keyOf(table, row), row };
}

/**
 * In a unit: a log segment's lines staged, the last line per key winning, a `gone` line's key
 * held as gone (a null row), so the base row it replaces is never loaded.
 */
function stageLog(sql: Pick<SqlStorage, "exec">, segment: readonly Line[]): void {
  for (const line of segment) {
    const held = rowOf(line);
    if (held !== null) {
      sql.exec("INSERT INTO restore_rows (tbl, k, row) VALUES (?, ?, ?) ON CONFLICT (tbl, k) " +
                 "DO UPDATE SET row = excluded.row", held.table, held.k,
               held.row === null ? null : JSON.stringify(held.row));
    }
  }
}

/**
 * A base part's span of tables, in the export's order: the index of the first and the last
 * table its rows are of, or null when it holds none (only chunks' lines).
 */
function spanOf(part: readonly Line[]): [number, number] | null {
  const names = Object.keys(exported());
  const at = part.map(rowOf).filter((held) => held !== null).map((held) => names.indexOf(held.table));
  return at.length === 0 ? null : [at[0]!, at.at(-1)!];
}

/** The base parts that hold rows of `table`, by index, from the record's spans. */
function partsOf(held: Held, table: string): number[] {
  const at = Object.keys(exported()).indexOf(table);
  return (JSON.parse(held.spans ?? "[]") as ([number, number] | null)[]).flatMap((span, part) =>
    span !== null && span[0] <= at && at <= span[1] ? [part] : []);
}

/** A row of `table` as the generation holds it at E: the base's, unless the log names its key. */
function baseRows(sql: Pick<SqlStorage, "exec">, table: string, part: readonly Line[]):
    Record<string, unknown>[] {
  return part.map(rowOf).filter((held) => held !== null && held.table === table
    && sql.exec("SELECT 1 FROM restore_rows WHERE tbl = ? AND k = ?", table, held.k).toArray()
      .length === 0).map((held) => held!.row!);
}

/** The log's rows of `table` after `after` (a key), at most `SLICE_ROWS`, and their keys. */
function loggedRows(sql: Pick<SqlStorage, "exec">, table: string, after: string):
    { k: string; row: Record<string, unknown> }[] {
  return sql.exec<{ k: string; row: string }>(
    "SELECT k, row FROM restore_rows WHERE tbl = ? AND k > ? AND row IS NOT NULL ORDER BY k " +
      "LIMIT ?", table, after, SLICE_ROWS).toArray()
    .map(({ k, row }) => ({ k, row: JSON.parse(row) as Record<string, unknown> }));
}

/**
 * In a unit: `rows` of `load` into its table, each reference count at 0 for the triggers to
 * rebuild, each column the schema requires and the export leaves out made afresh, each held
 * column at its seeded row's value, kept in the record until the check passes.
 */
function insert(sql: Pick<SqlStorage, "exec">, load: Load,
                rows: readonly Record<string, unknown>[]): void {
  const { columns, key } = exported()[load.table]!;
  const held = load.held ?? [];
  const refs = load.refs ?? [];
  const loaded = columns.filter((column) => !held.includes(column));
  const fresh = required(sql, load.table);
  const updated = loaded.filter((column) => !key.includes(column) && !refs.includes(column));
  // A seeded row, or one a trigger of a table loaded before made, takes the stream's values.
  const statement = `INSERT INTO ${load.table} (${[...loaded, ...fresh, ...held].join(", ")}) ` +
    `VALUES (${[...loaded, ...fresh].map(() => "?").concat(held.map((column) =>
      `(SELECT ${column} FROM ${load.table})`)).join(", ")}) ON CONFLICT DO ${
      updated.length === 0 ? "NOTHING"
        : `UPDATE SET ${updated.map((column) => `${column} = excluded.${column}`).join(", ")}`}`;
  for (const row of rows) {
    if (held.length > 0) {
      const kept = JSON.parse(record(sql)!.held ?? "{}") as Record<string, unknown>;
      kept[load.table] = Object.fromEntries(held.map((column) => [column, row[column]]));
      sql.exec("UPDATE owner_restore SET held = ?", JSON.stringify(kept));
    }
    sql.exec(statement, ...loaded.map((column) => refs.includes(column) ? 0 : cell(row[column])),
             ...fresh.map(() => randomHex(32)));
  }
}

/**
 * In a unit: whether each of `rows` of `load` reads back from its table as the generation
 * holds it, column for column but the held ones; the first that does not, by its key.
 */
function differs(sql: Pick<SqlStorage, "exec">, load: Load,
                 rows: readonly Record<string, unknown>[]): string | null {
  const { columns, key } = exported()[load.table]!;
  const compared = columns.filter((column) => !(load.held ?? []).includes(column));
  const as = (row: Record<string, unknown>) =>
    JSON.stringify(compared.map((column) => streamed(row[column])));
  for (const want of rows) {
    const got = sql.exec<Record<string, SqlStorageValue>>(
      `SELECT ${compared.join(", ")} FROM ${load.table} WHERE ${
        key.map((column) => `${column} IS ?`).join(" AND ")}`,
      ...key.map((column) => cell(want[column]))).toArray()[0];
    if (got === undefined || as(got) !== as(want)) {
      return `${load.table}'s row ${JSON.stringify(key.map((column) => want[column]))} is ` +
        "not the generation's";
    }
  }
  return null;
}

/**
 * Where an apply or a check is in its table: at a base part (`p`, its index among the parts
 * that hold the table's rows), or past the base at the log's rows after a key (`k`); and, for
 * a check, how many rows of the table it has held to the generation so far (`n`).
 */
type Within = { readonly p: number; readonly n: number }
  | { readonly k: string; readonly n: number };

const START = "{\"p\":0,\"n\":0}";

/**
 * In the last unit: the fence, each by its own module. Every outside client is revoked with its
 * API key, as the owner's own revoke does, and every MCP grant before now is fenced
 * (`Grants.revokeAll`, `Keys.revoked`); every inbound binding is disabled at a new revision, so
 * no route the directory still holds for it reaches it (`Inbound.disableAll`); every connection
 * is marked failed, for its owner to connect again (`Connections.secretsLost`). Runs live at E
 * have no lease here, so the runs lane settles them `unknown` once the owner is admitted
 * (`Runs.lost`). The doors delete the API keys from the directory (`restoreStep`'s answer).
 */
function fence(o: OwnerState, now: number): void {
  for (const client of o.grants.revokeAll(now)) {
    o.keys.revoked(client, now);
  }
  o.inbound.disableAll(now);
  o.connections.secretsLost();
}

/** The restore's answer for its record as it stands. In a unit. */
function answer(o: OwnerState, held: Held): RestoreAnswer {
  if (held.step === "failed") {
    return { step: "failed", done: false, failed: held.failed! };
  }
  if (held.step !== "done") {
    return { step: held.step, done: false };
  }
  const { state, reasons } = o.admission();
  const rows = Object.keys(exported()).reduce((sum, table) => sum + o.storage.sql.exec<{
    n: number }>(`SELECT count(*) AS n FROM ${table}`).one().n, 0);
  return { step: "done", done: true, gen: held.gen, rows, chunks: held.at, keys: o.keys.ids(),
           state, reasons };
}

/** The next step of a restore after `step`, in order. */
const NEXT: Readonly<Partial<Record<Step, Step>>> = Object.freeze({
  index: "log", log: "apply", apply: "check", check: "chunks", chunks: "clear", clear: "done" });

/**
 * One bounded piece of the restore's current step, from its record, the record moved on in
 * the same unit, and only while no other call moved it first (`still`), so two calls at once
 * never do one piece twice; whether the restore goes on.
 */
async function piece(o: OwnerState, door: Door<undefined>, source: GenerationSource):
    Promise<boolean> {
  const sql = o.storage.sql;
  const held = await o.door(() => {
    door.check();
    return record(sql);
  });
  if (held === null) {
    throw refused("no restore of this owner was begun");
  }
  /** In a unit: whether the record is still as `held` read it. */
  const still = () => {
    door.check();
    const now = record(sql);
    return now !== null && now.step === held.step && now.at === held.at
      && now.after === held.after;
  };
  /** In a unit: the record moved on from where `held` read it. */
  const move = (step: Step, at: number, after: string | null, failed: string | null = null) => {
    sql.exec("UPDATE owner_restore SET step = ?, at = ?, after = ?, failed = ?", step, at, after,
             failed);
  };
  switch (held.step) {
    case "index":
    case "log": {
      // Each base part's span of tables, then each log segment staged: one unit an object.
      const root = await rootOf(source, held.ns, held.gen);
      const index = held.step === "index";
      const count = index ? root.parts.length : root.segments.length;
      if (held.at >= count) {
        await o.door(() => {
          if (still()) {
            move(NEXT[held.step]!, 0, held.step === "log" ? START : null);
          }
        });
        return true;
      }
      const read = lines(await objectAt(source, held.ns, root,
                                        index ? held.at : root.parts.length + held.at));
      await o.door(() => {
        if (!still()) {
          return;
        }
        if (index) {
          const spans = JSON.parse(held.spans ?? "[]") as unknown[];
          sql.exec("UPDATE owner_restore SET spans = ?", JSON.stringify([...spans, spanOf(read)]));
        } else {
          stageLog(sql, read);
        }
        move(held.step, held.at + 1, null);
      });
      return true;
    }
    case "apply":
    case "check": {
      const load = loadOrder()[held.at];
      if (load === undefined) {
        await o.door(() => {
          if (still()) {
            move(NEXT[held.step]!, 0, held.step === "apply" ? START : null);
          }
        });
        return true;
      }
      const within = JSON.parse(held.after!) as Within;
      const parts = partsOf(held, load.table);
      // A base part's rows of the table, read before the unit that loads or checks them.
      const read = "p" in within && within.p < parts.length
        ? lines(await objectAt(source, held.ns, await rootOf(source, held.ns, held.gen),
                               parts[within.p]!)) : null;
      await o.door(() => {
        if (!still()) {
          return;
        }
        let rows: Record<string, unknown>[];
        let next: Within | null;
        if (read !== null) {
          rows = baseRows(sql, load.table, read);
          next = { p: (within as { p: number }).p + 1, n: within.n + rows.length };
        } else {
          const logged = loggedRows(sql, load.table, "k" in within ? within.k : "");
          rows = logged.map((each) => each.row);
          next = logged.length === SLICE_ROWS ? { k: logged.at(-1)!.k, n: within.n + rows.length }
            : null;
        }
        if (held.step === "apply") {
          insert(sql, load, rows);
          move("apply", next === null ? held.at + 1 : held.at, next === null ? START
            : JSON.stringify(next));
          return;
        }
        let difference = differs(sql, load, rows);
        const total = next?.n ?? within.n + rows.length;
        if (difference === null && next === null) {
          const stands = sql.exec<{ n: number }>(`SELECT count(*) AS n FROM ${load.table}`)
            .one().n;
          if (stands !== total) {
            difference = `${load.table} holds ${stands} rows where the generation has ${total}`;
          }
        }
        if (difference !== null) {
          console.error("a restore's check found a difference; the owner is not admitted");
          move("failed", held.at, held.after, difference);
          return;
        }
        move("check", next === null ? held.at + 1 : held.at, next === null ? START
          : JSON.stringify(next));
      });
      return true;
    }
    case "chunks": {
      const listed = await o.door(() => o.files.chunksPage(held.at, SLICE_CHUNKS));
      for (const chunk of listed) {
        // Read whole by the files Worker under the owner's key and checked against its hash;
        // one byte of it comes back. A chunk that fails is thrown: the next step reads again.
        await o.files.fetch([{ sha256: chunk.sha256, offset: 0, length: 1 }]);
      }
      await o.door(() => {
        if (still()) {
          move(listed.length < SLICE_CHUNKS ? "clear" : "chunks", held.at + listed.length, null);
        }
      });
      return true;
    }
    case "clear": {
      const root = await rootOf(source, held.ns, held.gen);
      await o.door(() => {
        if (still() && sql.exec("DELETE FROM restore_rows WHERE (tbl, k) IN (SELECT tbl, k " +
                                  "FROM restore_rows LIMIT ?)", SLICE_ROWS).rowsWritten
                                  < SLICE_ROWS) {
          finish(o, held, root);
        }
      });
      return true;
    }
    case "done":
    case "failed":
      return false;
  }
}

/**
 * In the last unit: the fence, the allocators past every value the rows name (each its
 * module's), the generation recorded as the object's newest, and the held columns as the
 * generation holds them: the owner's admission as it stood at E.
 */
function finish(o: OwnerState, held: Held, root: ManifestRoot): void {
  const sql = o.storage.sql;
  fence(o, clock());
  o.files.restored();
  o.collections.restored();
  o.ledger.restored();
  o.backups.adopt(root);
  const kept = JSON.parse(held.held ?? "{}") as Record<string, Record<string, SqlStorageValue>>;
  for (const load of loadOrder().filter((each) => each.held !== undefined)) {
    const columns = load.held!;
    sql.exec(`UPDATE ${load.table} SET ${columns.map((column) => `${column} = ?`).join(", ")}`,
             ...columns.map((column) => kept[load.table]?.[column] ?? null));
  }
  if (!["admitted", "suspended"].includes(o.admission().state)) {
    throw new Error("a checked restore holds the owner's admission at E, admitted or suspended");
  }
  sql.exec("UPDATE owner_restore SET step = 'done', after = NULL");
}

// -- THE DOORS: the `restore` rows' handlers (`contract/doors.json`) -------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands the
// object's state once the row's steps held: the bind (the platform's reserved caller), and the
// fields.

/** The `restore` doors' handlers, by door. */
export const restoreDoors = Object.freeze({
  /**
   * What a restore could read for this owner: its namespace and its finished generations, as
   * this object holds them, and its admission and any restore's step. A read.
   */
  async restoreSource(o: OwnerState, door: Door<undefined>): Promise<RestoreSource> {
    return o.door(() => {
      door.check();
      return { namespace: o.files.storedNamespace(),
               generations: o.backups.manifests().map(({ gen, finished_at }) =>
                 ({ gen, finished_at })),
               state: o.admission().state, restore: record(o.storage.sql)?.step ?? null };
    });
  },
  /**
   * A restore begun from generation `gen` in `namespace`: refused unless this object is pending
   * and empty, and unless the generation opens under the namespace and is this owner's, at this
   * object's schema. Asked again with the same generation, it answers as it stands.
   */
  async restoreBegin(o: OwnerState, door: Door<RestoreBeginRequest>): Promise<RestoreAnswer> {
    const asked = await o.door(() => {
      const { r } = door.check();
      const { namespace: ns, gen } = r;
      if (typeof ns !== "string" || !HEX32.test(ns) || typeof gen !== "string"
          || !HEX16.test(gen)) {
        throw new Refusal("invalid", "a restore names a namespace (32 hex) and a generation " +
                                     "(16 hex)");
      }
      const held = record(o.storage.sql);
      if (held !== null && held.ns === ns && held.gen === gen) {
        return { ns, gen, held };
      }
      const why = emptiness(o);
      if (why !== null) {
        throw refused(why);
      }
      return { ns, gen, held: null };
    });
    if (asked.held !== null) {
      return o.door(() => answer(o, asked.held!));
    }
    const { ns, gen } = asked;
    const source: GenerationSource = o.env.FILES;
    const root = await rootOf(source, ns, gen);
    const logged = root.segments.reduce((sum, segment) => sum + segment.size, 0);
    if (logged > LOG_BYTES) {
      throw refused(`the generation's log is ${logged} bytes, past the ${LOG_BYTES} a restore ` +
                    "stages: restore a quieter night's");
    }
    let header: Line | undefined;
    try {
      header = lines(await objectAt(source, ns, root, 0))[0];
    } catch {
      throw refused("the generation's first part does not open under the namespace's key");
    }
    if (header?.t !== "header" || header["format"] !== FORMAT || header["version"] !== VERSION
        || header["gen"] !== gen) {
      throw refused(`the generation is not a ${FORMAT} version ${VERSION} generation ${gen}`);
    }
    if (header["owner"] !== o.owner()) {
      throw refused("the generation is another owner's");
    }
    if (header["schema"] !== MIGRATIONS.length) {
      throw refused(`the generation is of schema ${String(header["schema"])}, this object's ` +
                    `is ${MIGRATIONS.length}: a restore loads only its own`);
    }
    return o.door(() => {
      door.check();
      const why = emptiness(o);
      if (why !== null) {
        throw refused(why);  // another call began one, or wrote, while the root was read
      }
      o.files.adoptSpace(ns, root.key);
      o.storage.sql.exec("INSERT INTO owner_restore (only, ns, gen, step, at) VALUES (1, ?, ?, " +
                           "'index', 0)", ns, gen);
      return answer(o, record(o.storage.sql)!);
    });
  },
  /**
   * The restore carried on, a piece at a time, for at most `RESTORE_PASS_MS`; how far it is.
   * Refused while none was begun; a failed one answers its failure.
   */
  async restoreStep(o: OwnerState, door: Door<undefined>): Promise<RestoreAnswer> {
    await o.door(() => door.check());
    const until = Date.now() + RESTORE_PASS_MS;
    const source: GenerationSource = o.env.FILES;
    while (await piece(o, door, source) && Date.now() < until) {
      // the next piece
    }
    await o.sync();
    return o.door(() => answer(o, record(o.storage.sql)!));
  },
});
