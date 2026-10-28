import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { Collections, LIMITS, type Limits } from "../src/collections";
import { type ChunkStore, type FileLimits, Files } from "../src/files";
import type { Binding, OwnerDO } from "../src/owner-do";
import { FILE_DEFAULTS } from "../src/policy";
import { SCHEDULE } from "../src/schedule";

export const text = (value: string): Uint8Array => new TextEncoder().encode(value);

/**
 * The owner's files on `stub`'s own storage, with some settings changed, over the real
 * files Worker or the store given.
 */
export function inFiles<R>(stub: DurableObjectStub<OwnerDO>, limits: Partial<FileLimits>,
                           act: (files: Files, state: DurableObjectState) => R | Promise<R>,
                           store: ChunkStore = env.FILES): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    act(new Files(state.storage, store, () => ({ ...FILE_DEFAULTS, ...limits })), state));
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
    "SELECT entries, history, chunk_bytes, inline_bytes, intents, intent_chunks, " +
      "(SELECT json_group_object(content_id, refs) FROM (SELECT * FROM file_contents " +
      "ORDER BY content_id)) AS contents, " +
      "(SELECT json_group_object(sha256, refs || '/' || holds) FROM (SELECT * FROM file_chunks " +
      "ORDER BY sha256)) AS chunks FROM file_space");
  const [counted] = await sqlIn(stub,
    "SELECT (SELECT COUNT(*) FROM file_entries) AS entries, " +
      "(SELECT COUNT(*) FROM file_history) AS history, " +
      "(SELECT COALESCE(SUM(size), 0) FROM file_chunks WHERE state IN ('uploading', 'live')) " +
      "AS chunk_bytes, " +
      "(SELECT COALESCE(SUM(length(inline)), 0) FROM file_contents) AS inline_bytes, " +
      "(SELECT COUNT(*) FROM file_intents) AS intents, " +
      "(SELECT COALESCE(SUM(length(chunks) / 64), 0) FROM file_intents) AS intent_chunks, " +
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
