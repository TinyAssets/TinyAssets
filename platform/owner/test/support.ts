import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { Collections, LIMITS, type Limits } from "../src/collections";
import { type ChunkStore, type FileLimits, Files } from "../src/files";
import { type OwnerStorage, type Write, WriteGate } from "../src/gate";
import {
  CHUNK_LIST_ROW_BYTES, CHUNK_ROW_BYTES, CONTENT_BYTES, CONTENT_RECORD_BYTES, ENTRY_BYTES,
  HISTORY_BYTES, SCOPE_REF_BYTES, SHARE_BYTES, TEMP_BYTES,
} from "../src/manifest";
import type { Binding, OwnerDO } from "../src/owner-do";
import { FILE_DEFAULTS } from "../src/policy";
import { SCHEDULE } from "../src/schedule";

export const text = (value: string): Uint8Array => new TextEncoder().encode(value);

/**
 * `act` in `stub`'s object, with a write gate of its own over the object's storage: it
 * shares the object's queue, as every adapter over one storage does (`gate.ts`).
 */
export function inGate<R>(stub: DurableObjectStub<OwnerDO>,
                          act: (instance: OwnerDO, state: DurableObjectState,
                                gate: WriteGate) => R | Promise<R>): Promise<R> {
  return runInDurableObject(stub, (instance: OwnerDO, state) =>
    act(instance, state, new WriteGate(state.storage)));
}

/** The fault a `faulty` gate's unit fails with. */
export const STORAGE_FAULT = "the unit's storage failed";

/**
 * `write`, except that each unit `fails` names (counted from 1, over this gate's units)
 * rejects unrun, as a unit whose storage failed does: what awaits the unit must fail with
 * it, never carry on as though it was written.
 */
export function faulty(write: Write, fails: (n: number) => boolean): Write {
  let n = 0;
  return <T,>(unit: () => T): Promise<T> =>
    fails(++n) ? Promise.reject(new Error(STORAGE_FAULT)) : write(unit);
}

/**
 * Files over `storage`, writing their own units through `write`. The object builds them
 * over its gate's view; a test that calls their synchronous changes directly passes the
 * storage itself.
 */
export function filesOn(storage: OwnerStorage, write: Write, store: ChunkStore = env.FILES,
                        limits: Partial<FileLimits> = {}): Files {
  return new Files(storage, store, () => ({ ...FILE_DEFAULTS, ...limits }), write);
}

/**
 * The owner's files on `stub`'s own storage, with some settings changed, over the real
 * files Worker or the store given; their own units go through a gate on that storage.
 */
export function inFiles<R>(stub: DurableObjectStub<OwnerDO>, limits: Partial<FileLimits>,
                           act: (files: Files, state: DurableObjectState) => R | Promise<R>,
                           store: ChunkStore = env.FILES): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    act(filesOn(state.storage, new WriteGate(state.storage).write, store, limits), state));
}

type Row = Record<string, SqlStorageValue>;

/** A schedule lane's Lifecycle job as Lifecycle stores it: due time in ms, and payload. */
export interface LaneJob {
  readonly time: number;
  readonly payload: unknown;
}

/** Every Lifecycle job of the object's schedule, by lane (ids are `owner-schedule/<lane>`). */
export async function laneJobs(stub: DurableObjectStub<OwnerDO>):
    Promise<Record<string, LaneJob>> {
  const rows = await sqlIn(stub, "SELECT id, time, payload FROM cf_agents_jobs WHERE " +
                                   "capability = ? ORDER BY id", SCHEDULE).catch(() => []);
  return Object.fromEntries(rows.map((row) => [(row["id"] as string).slice(SCHEDULE.length + 1), {
    time: row["time"] as number,
    payload: row["payload"] === null ? null : JSON.parse(row["payload"] as string),
  }]));
}

/** One lane's job due time in seconds, or null when it has none. */
export async function laneAt(stub: DurableObjectStub<OwnerDO>, lane: string):
    Promise<number | null> {
  const job = (await laneJobs(stub))[lane];
  return job === undefined ? null : job.time / 1000;
}

/**
 * Let the object's due Lifecycle jobs run. workerd fires the alarm Lifecycle armed for
 * them on its own, so this runs it now if it is still pending, then waits until no job of
 * any capability is due.
 */
export async function drainDue(stub: DurableObjectStub<OwnerDO>): Promise<void> {
  await runDurableObjectAlarm(stub);
  for (let tries = 0; tries < 200; tries++) {
    const due = await sqlIn(stub, "SELECT id FROM cf_agents_jobs WHERE time <= ?", Date.now());
    if (due.length === 0) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("the due jobs did not run");
}

/** The object's physical alarm in seconds, as Lifecycle armed it, or null. */
export function alarmOf(stub: DurableObjectStub<OwnerDO>): Promise<number | null> {
  return runInDurableObject(stub, async (_instance: OwnerDO, state) => {
    const at = await state.storage.getAlarm();
    return at === null ? null : at / 1000;
  });
}

/** Rows a query returns in `stub`'s own storage. */
export function sqlIn(stub: DurableObjectStub<OwnerDO>, statement: string,
                      ...params: SqlStorageValue[]): Promise<Row[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Row>(statement, ...params).toArray());
}

/**
 * What the manifest names but could lose: a content an entry or a kept history row
 * names that has no row, and a chunk of such content that is missing, garbage or being
 * deleted. Always empty.
 */
export async function lostContent(stub: DurableObjectStub<OwnerDO>): Promise<Row[]> {
  return sqlIn(stub,
    "WITH named AS (SELECT content_id FROM file_entries WHERE content_id IS NOT NULL " +
      "UNION SELECT content_id FROM file_history WHERE content_id IS NOT NULL) " +
      "SELECT n.content_id, 'no content row' AS lost FROM named n " +
      "WHERE n.content_id NOT IN (SELECT content_id FROM file_contents) " +
      "UNION ALL SELECT x.content_id, coalesce(k.state, 'no chunk row') FROM named n " +
      "JOIN file_content_chunks x ON x.content_id = n.content_id " +
      "LEFT JOIN file_chunks k ON k.sha256 = x.sha256 " +
      "WHERE k.state IS NULL OR k.state NOT IN ('live')");
}

/**
 * Each writer's scopes, as the schema counts them: an outside client's own and all
 * clients', anyone else's the owner's.
 */
const SCOPED = "scoped (writer, scope) AS (SELECT writer, writer FROM writers " +
  "WHERE writer GLOB 'client:*' UNION ALL SELECT writer, '*' FROM writers " +
  "WHERE writer GLOB 'client:*' UNION ALL SELECT writer, 'owner' FROM writers " +
  "WHERE writer NOT GLOB 'client:*')";

/**
 * The file manifest's counters, recounted from its rows alone: the owner-wide ones, each
 * scope's references to contents and chunks, and each scope's share, by the charge of
 * `manifest.ts` but not by its triggers.
 */
const RECOUNT =
  // SQLite here takes few terms in one compound SELECT, so the unions are nested.
  "WITH RECURSIVE writers (writer) AS (SELECT writer FROM (SELECT writer FROM file_entries " +
  "UNION SELECT writer FROM file_history UNION SELECT writer FROM file_temps) UNION " +
  "SELECT writer FROM (SELECT writer FROM file_intents UNION SELECT holder FROM file_chunks)), " +
  `${SCOPED}, ` +
  "rows_c (writer, content_id) AS (SELECT writer, content_id FROM file_entries " +
  "WHERE content_id IS NOT NULL UNION ALL SELECT writer, content_id FROM file_history " +
  "WHERE content_id IS NOT NULL), " +
  "refs_c (scope, content_id, refs) AS (SELECT s.scope, r.content_id, COUNT(*) FROM rows_c r " +
  "JOIN scoped s ON s.writer = r.writer GROUP BY s.scope, r.content_id), " +
  "cut (intent_id, writer, chunks, i) AS (SELECT intent_id, writer, chunks, 0 FROM " +
  "file_intents WHERE length(chunks) > 0 UNION ALL SELECT intent_id, writer, chunks, i + 1 " +
  "FROM cut WHERE (i + 1) * 64 < length(chunks)), " +
  "held (writer, sha256) AS (SELECT DISTINCT writer, intent_id || ':' || " +
  "substr(chunks, i * 64 + 1, 64) FROM cut), " +
  "refs_k (scope, sha256, refs) AS (SELECT scope, sha256, SUM(n) FROM (" +
  "SELECT c.scope, x.sha256, COUNT(*) AS n FROM refs_c c JOIN file_content_chunks x " +
  "ON x.content_id = c.content_id GROUP BY c.scope, x.sha256 UNION ALL " +
  "SELECT s.scope, substr(h.sha256, 34), COUNT(*) FROM held h JOIN scoped s " +
  "ON s.writer = h.writer GROUP BY s.scope, substr(h.sha256, 34)) GROUP BY scope, sha256), " +
  "rows_own (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes) AS (" +
  `SELECT s.scope, 1, 0, 0, 0, ${ENTRY_BYTES("e")} FROM file_entries e JOIN scoped s ` +
  "ON s.writer = e.writer UNION ALL " +
  `SELECT s.scope, 0, 1, 0, 0, ${HISTORY_BYTES("h")} FROM file_history h JOIN scoped s ` +
  "ON s.writer = h.writer UNION ALL " +
  `SELECT s.scope, 0, 0, 0, 0, ${TEMP_BYTES("t")} FROM file_temps t JOIN scoped s ` +
  "ON s.writer = t.writer), " +
  "rows_held (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes) AS (" +
  "SELECT r.scope, 0, 0, 0, coalesce(length(c.inline), 0), " +
  `${SCOPE_REF_BYTES("r")} + ${CONTENT_RECORD_BYTES("c")} + ${CHUNK_LIST_ROW_BYTES()} * ` +
  "(SELECT COUNT(*) FROM file_content_chunks x WHERE x.content_id = c.content_id) " +
  "FROM refs_c r JOIN file_contents c ON c.content_id = r.content_id UNION ALL " +
  `SELECT r.scope, 0, 0, k.size, 0, ${SCOPE_REF_BYTES("r")} + ${CHUNK_ROW_BYTES()} ` +
  "FROM refs_k r JOIN file_chunks k ON k.sha256 = r.sha256 UNION ALL " +
  `SELECT s.scope, 0, 0, 0, 0, ${CHUNK_ROW_BYTES()} FROM file_chunks k JOIN scoped s ` +
  "ON s.writer = k.holder WHERE k.state IN ('garbage', 'deleting')), " +
  "charged AS (SELECT * FROM rows_own UNION ALL SELECT * FROM rows_held), " +
  "scopes (who) AS (SELECT '*' UNION SELECT 'owner' UNION SELECT who FROM charged), " +
  "shares AS (SELECT w.who, coalesce(SUM(c.entries), 0) AS entries, " +
  "coalesce(SUM(c.history), 0) AS history, coalesce(SUM(c.chunk_bytes), 0) AS chunk_bytes, " +
  "coalesce(SUM(c.inline_bytes), 0) AS inline_bytes, coalesce(SUM(c.manifest_bytes), 0) + " +
  `${SHARE_BYTES} * (CASE WHEN w.who = '*' THEN (SELECT COUNT(*) FROM scopes ` +
  "WHERE who GLOB 'client:*') + 1 ELSE 1 END) AS manifest_bytes FROM scopes w " +
  "LEFT JOIN charged c ON c.who = w.who GROUP BY w.who) ";

/**
 * Every counter and reference count the file manifest keeps, and the same numbers
 * counted from the rows: the two must always be equal. Content anything names is never
 * lost (`lostContent`), checked here too, so every counter check also checks that.
 */
export async function fileCounters(stub: DurableObjectStub<OwnerDO>): Promise<[Row, Row]> {
  const lost = await lostContent(stub);
  if (lost.length > 0) {
    throw new Error(`content the manifest names is lost: ${JSON.stringify(lost)}`);
  }
  const [kept] = await sqlIn(stub,
    "SELECT entries, history, chunk_bytes, inline_bytes, manifest_bytes, intents, " +
      "intent_chunks, (SELECT json_group_object(who, json_array(entries, history, " +
      "chunk_bytes, inline_bytes, manifest_bytes)) FROM (SELECT * FROM file_shares " +
      "ORDER BY who)) AS shares, " +
      "(SELECT json_group_object(scope || '/' || content_id, refs) FROM (SELECT * FROM " +
      "file_scope_contents ORDER BY scope, content_id)) AS scope_contents, " +
      "(SELECT json_group_object(scope || '/' || sha256, refs) FROM (SELECT * FROM " +
      "file_scope_chunks ORDER BY scope, sha256)) AS scope_chunks, " +
      "(SELECT json_group_object(content_id, refs) FROM (SELECT * FROM file_contents " +
      "ORDER BY content_id)) AS contents, " +
      "(SELECT json_group_object(sha256, refs || '/' || holds) FROM (SELECT * FROM file_chunks " +
      "ORDER BY sha256)) AS chunks FROM file_space");
  const [counted] = await sqlIn(stub,
    `${RECOUNT}SELECT (SELECT COUNT(*) FROM file_entries) AS entries, ` +
      "(SELECT COUNT(*) FROM file_history) AS history, " +
      "(SELECT COALESCE(SUM(size), 0) FROM file_chunks WHERE state IN ('uploading', 'live')) " +
      "AS chunk_bytes, " +
      "(SELECT COALESCE(SUM(length(inline)), 0) FROM file_contents) AS inline_bytes, " +
      `(SELECT coalesce(SUM(${ENTRY_BYTES("e")}), 0) FROM file_entries e) + ` +
      `(SELECT coalesce(SUM(${HISTORY_BYTES("h")}), 0) FROM file_history h) + ` +
      `(SELECT coalesce(SUM(${CONTENT_BYTES("c")}), 0) FROM file_contents c) + ` +
      `(SELECT COUNT(*) FROM file_chunks) * ${CHUNK_ROW_BYTES()} + ` +
      `(SELECT coalesce(SUM(${TEMP_BYTES("t")}), 0) FROM file_temps t) + ` +
      `(SELECT coalesce(SUM(${SCOPE_REF_BYTES("r")}), 0) FROM refs_c r) + ` +
      `(SELECT coalesce(SUM(${SCOPE_REF_BYTES("r")}), 0) FROM refs_k r) + ` +
      `(SELECT COUNT(*) FROM shares) * ${SHARE_BYTES} AS manifest_bytes, ` +
      "(SELECT COUNT(*) FROM file_intents) AS intents, " +
      "(SELECT COALESCE(SUM(length(chunks) / 64), 0) FROM file_intents) AS intent_chunks, " +
      "(SELECT json_group_object(who, json_array(entries, history, chunk_bytes, " +
      "inline_bytes, manifest_bytes)) FROM (SELECT * FROM shares ORDER BY who)) AS shares, " +
      "(SELECT json_group_object(scope || '/' || content_id, refs) FROM (SELECT * FROM " +
      "refs_c ORDER BY scope, content_id)) AS scope_contents, " +
      "(SELECT json_group_object(scope || '/' || sha256, refs) FROM (SELECT * FROM " +
      "refs_k ORDER BY scope, sha256)) AS scope_chunks, " +
      "(SELECT json_group_object(content_id, n) FROM (SELECT c.content_id, " +
      "(SELECT COUNT(*) FROM file_history h WHERE h.content_id = c.content_id) + " +
      "(SELECT COUNT(*) FROM file_entries e WHERE e.content_id = c.content_id) AS n " +
      "FROM file_contents c ORDER BY c.content_id)) AS contents, " +
      "(SELECT json_group_object(sha256, r || '/' || h) FROM (SELECT k.sha256, " +
      "(SELECT COUNT(*) FROM file_content_chunks x WHERE x.sha256 = k.sha256) AS r, " +
      "(SELECT COUNT(*) FROM file_intents i WHERE instr(i.chunks, k.sha256) > 0) AS h " +
      "FROM file_chunks k ORDER BY k.sha256)) AS chunks");
  return [kept!, counted!];
}

export type Stub = DurableObjectStub<OwnerDO>;

/** An owner of its own and its object, reached as a binder reaches it: by name. */
export interface Owner {
  /** The owner, unique to the test, so no test sees another's rows. */
  readonly name: string;
  readonly stub: Stub;
  /** The binder's binding: `caller` on `owner`'s object (this owner by default). */
  as(caller: string, owner?: string): Binding;
}

export function ownerObject(): Owner {
  const name = `owner-${crypto.randomUUID()}`;
  return {
    name,
    stub: env.OWNER.getByName(name),
    as: (caller, owner = name) => ({ owner, caller, origin: null }),
  };
}

/**
 * The error an RPC call fails with. Awaited inside a try, because handing the call's
 * promise to `expect(...).rejects` leaves its rejection reported as unhandled.
 */
export async function failure(call: () => Promise<unknown>): Promise<Error> {
  try {
    await call();
  } catch (error) {
    return error as Error;
  }
  throw new Error("expected the call to fail");
}

/** The primitive on `stub`'s own storage, with some bounds lowered for the test. */
export function inObject<R>(stub: Stub, limits: Partial<Limits>,
                            act: (store: Collections, state: DurableObjectState) => R): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    act(new Collections(state.storage, { ...LIMITS, ...limits }), state));
}

export type RowTuple = [string, string, number, number, string, number, string];

/** The stored rows as `(collection, row_id, revision, seq, writer, private, body)`. */
export function rows(stub: Stub): Promise<RowTuple[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Record<string, string | number>>(
      "SELECT collection, row_id, revision, seq, writer, private, body FROM collection_rows " +
        "ORDER BY seq",
    ).toArray().map((r) => [r["collection"], r["row_id"], r["revision"], r["seq"], r["writer"],
                            r["private"], r["body"]] as RowTuple));
}

export function etags(stub: Stub): Promise<Record<string, string>> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    Object.fromEntries(state.storage.sql.exec<{ row_id: string; etag: string }>(
      "SELECT row_id, etag FROM collection_rows",
    ).toArray().map((r) => [r.row_id, r.etag])));
}

export type HistoryTuple = [string, string, number, string, string, string];

/** The stored history as `(collection, key, revision, op, body_json, writer)`. */
export function history(stub: Stub): Promise<HistoryTuple[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Record<string, string | number>>(
      "SELECT collection, key, revision, op, body_json, writer FROM collection_history " +
        "ORDER BY collection, key, revision",
    ).toArray().map((r) => [r["collection"], r["key"], r["revision"], r["op"], r["body_json"],
                            r["writer"]] as HistoryTuple));
}
