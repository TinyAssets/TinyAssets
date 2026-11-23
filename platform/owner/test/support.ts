import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { vi } from "vitest";

import { HELD_WAIT_MS, OPEN, OPEN_RECORD, openRecord, TIERS } from "../../ci/fixtures";
import { sqlIn } from "../../ci/workerd";
import { bundledSources } from "../../sources/src/catalog";
import { type Collections, LIMITS } from "../src/collections";
import type { Files } from "../src/files";
import { type Write, WriteGate } from "../src/gate";
import {
  CHUNK_LIST_ROW_BYTES, CHUNK_ROW_BYTES, CONTENT_BYTES, CONTENT_RECORD_BYTES, ENTRY_BYTES,
  HISTORY_BYTES, SCOPE_REF_BYTES, SHARE_BYTES,
} from "../src/manifest";
import { type OwnerDO, ownerStores } from "../src/owner-do";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { contentIdOf } from "../../contract/src/content";
import { sha256Hex } from "../../contract/src/digest";
import { INLINE_BYTES, PLACE_BYTES } from "../../contract/src/limits";
import { AGENT_FILES, agentPath } from "../../contract/src/agent-definition";
import { carried, NEWER } from "../src/starter-package";
import { LANE_PASS_S, SCHEDULE } from "../src/schedule";
import { clock } from "../src/clock";
import type { ChunkStore } from "../../contract/src/files";
import type { FileSettings } from "../../contract/src/policy-document";
import type { GateControl } from "../../ci/gate";
import type { Arm, Crossed, Held } from "../../ci/gate-worker";

export const text = (value: string): Uint8Array => new TextEncoder().encode(value);

/**
 * `OPEN`'s text with the vendor sign-in of each source `on` names switched on and every other
 * off (every one on when it names none), as a binding hands the record (`Binding.control`).
 */
export function signinRecord(on?: readonly string[]): string {
  return JSON.stringify({ ...OPEN, signins: Object.fromEntries(bundledSources().ids().map(
    (id) => [id, on === undefined || on.includes(id)])) });
}

/** Every served sign-in switched on: the record a test's binding hands unless it names one. */
export const EVERY_SIGNIN_RECORD = signinRecord();

export { OPEN_RECORD, openRecord, TIERS };

/** A lane pass's end as the schedule hands it (`Lane.run`'s `until`): `LANE_PASS_S` on. */
export function passEnd(): number {
  return clock() + LANE_PASS_S;
}

/**
 * The object's clock held at `seconds`, by a fake `Date`, which the object reads as the test
 * does; the test makes the clock real again after it (`vi.useRealTimers`).
 */
export function clockAt(seconds: number): void {
  const now = Math.round(seconds * 1000);
  if (vi.isFakeTimers()) {
    vi.setSystemTime(now);
  } else {
    vi.useFakeTimers({ toFake: ["Date"], now });
  }
}

/**
 * `act` run with the object's clock held at `seconds` (`clockAt`), the clock then as it was:
 * real again, or the fake `Date`'s time before.
 */
export async function atClock<T>(seconds: number, act: () => Promise<T>): Promise<T> {
  const was = vi.isFakeTimers() ? Date.now() : null;
  clockAt(seconds);
  try {
    return await act();
  } finally {
    if (was === null) {
      vi.useRealTimers();
    } else {
      vi.setSystemTime(was);
    }
  }
}

/**
 * The founder's tiers (`TIERS`) with storage figures a test sets, free's and paid's: their
 * other numbers as the control record holds them.
 */
export function tiersAt(free: number, paid: number): typeof TIERS {
  return Object.freeze({ free: { ...TIERS.free, storage_bytes: free },
                         paid: { ...TIERS.paid, storage_bytes: paid } });
}

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
 * The files Worker as the `FILES` binding reaches it, with `overrides` in place of some of
 * its methods: a store that fails or records one call and passes the rest through.
 */
export function through(overrides: Partial<ChunkStore>): ChunkStore {
  const files = env.FILES;
  return {
    mintKey: (ns) => files.mintKey(ns),
    putChunks: (ns, key, chunks) => files.putChunks(ns, key, chunks),
    getChunks: (ns, key, reads) => files.getChunks(ns, key, reads),
    checkChunks: (ns, key, sha256s) => files.checkChunks(ns, key, sha256s),
    deleteChunks: (ns, key, sha256s) => files.deleteChunks(ns, key, sha256s),
    rewrapKey: (ns, key) => files.rewrapKey(ns, key),
    rewrapManifestRoot: (ns, day) => files.rewrapManifestRoot(ns, day),
    markNamespace: (ns) => files.markNamespace(ns),
    deleteNamespace: (ns) => files.deleteNamespace(ns),
    holdNamespace: (ns, held) => files.holdNamespace(ns, held),
    rootNamespace: (ns) => files.rootNamespace(ns),
    putManifestPart: (ns, key, day, part, bytes) =>
      files.putManifestPart(ns, key, day, part, bytes),
    putManifestSegment: (ns, key, gen, a, b, bytes) =>
      files.putManifestSegment(ns, key, gen, a, b, bytes),
    putManifestRoot: (ns, key, gen, parts, segments, span) =>
      files.putManifestRoot(ns, key, gen, parts, segments, span),
    getManifestRoot: (ns, gen) => files.getManifestRoot(ns, gen),
    getManifestPart: (ns, key, gen, part, sha256) =>
      files.getManifestPart(ns, key, gen, part, sha256),
    getManifestSegment: (ns, key, gen, a, b, sha256) =>
      files.getManifestSegment(ns, key, gen, a, b, sha256),
    deleteSegments: (ns, gen, segments) => files.deleteSegments(ns, gen, segments),
    sweepManifests: (ns, keep, cursor) => files.sweepManifests(ns, keep, cursor),
    chunkNames: (ns, key, sha256s) => files.chunkNames(ns, key, sha256s),
    listChunks: (ns, after) => files.listChunks(ns, after),
    deleteListed: (ns, names) => files.deleteListed(ns, names),
    ...overrides,
  };
}

/**
 * The object's stores on `state`'s own storage, wired as the object wires them
 * (`ownerStores`), over a write gate of the test's own: it shares the object's queue, as
 * every adapter over one storage does (`gate.ts`), so each store writes only in its units.
 * Their outside calls go to the files Worker, or to `files` (`through`). A file change tells
 * the owner's other modules, as the object's own does.
 */
export function storesOn(state: Pick<DurableObjectState, "id" | "storage">,
                         files: ChunkStore = env.FILES) {
  const gate = new WriteGate(state.storage);
  return { gate, ...ownerStores(gate, { ...env, FILES: files }, state.id) };
}
export type TestStores = ReturnType<typeof storesOn>;

/**
 * The owner's files settings, as the owner sets them in its session (`policyWrite`): `files`,
 * and the rest at their defaults. The object, and every store of it (`storesOn`), reads them.
 */
export async function setFiles(r: { readonly stub: Stub; session(origin: null): Binding },
                               files: Partial<FileSettings>): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.session(null));
  await r.stub.policyWrite(r.session(null), { expected_generation: generation,
                                              policy: { ...policy, files } as never });
}

/**
 * An index this full, stood in for: `who`'s index charge (the owner's whole, `file_space`,
 * with null; else its `file_shares` row, which must exist) raised so `room` bytes are left
 * under `bound`. What it gives back takes the stand-in away, so the counters equal a recount
 * again.
 */
export async function crowd(stub: DurableObjectStub<OwnerDO>, bound: number, room: number,
                            who: string | null = null): Promise<() => Promise<void>> {
  const [table, where, args] = who === null ? ["file_space", "", []]
    : ["file_shares", " WHERE who = ?", [who]];
  const [row] = await sqlIn(stub, `SELECT manifest_bytes AS m FROM ${table}${where}`, ...args);
  const delta = bound - room - (row!["m"] as number);
  const move = (by: number) => sqlIn(
    stub, `UPDATE ${table} SET manifest_bytes = manifest_bytes + ?${where}`, by, ...args);
  await move(delta);
  return async () => {
    await move(-delta);
  };
}

/**
 * The owner's files on `stub`'s own storage (`storesOn`), over the real files Worker or the
 * store given; their own units go through a gate on that storage, and `act` runs each of
 * their synchronous changes in a unit of it (`unit`), as a door runs one. Their settings are
 * the owner's own (`setFiles`).
 */
export function inFiles<R>(stub: DurableObjectStub<OwnerDO>,
                           act: (files: Files, unit: Write) => R | Promise<R>,
                           store: ChunkStore = env.FILES): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) => {
    const { gate, files } = storesOn(state, store);
    return act(files, gate.write);
  });
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

/** What one arming does: hold a call before or after it, change its bytes, or refuse it. */
type Act = Omit<Arm, "method" | "name" | "times">;

/**
 * One name's calls at a gate (`ci/gate.ts`): an owner's at the GATEWAY gate, its namespace's
 * at the FILES gate (each call is named by its first argument). It arms the gate for that
 * name alone, waits for and lets go its held calls, and keeps every call that crossed
 * (`calls`, oldest first), so no other owner's lane meets a test's arming.
 */
export function gateFor(gate: GateControl, name: string) {
  const crossed: Crossed[] = [];
  const mine = async (method?: string): Promise<Held[]> => (await gate.held())
    .filter((call) => call.name === name && (method === undefined || call.method === method));
  return {
    arm: (method: string, act: Act, times = 1) =>
      gate.arm({ ...act, method, name, times } as Arm),
    /** The held calls of `method` (any when absent), once one is held; loud when none is. */
    held: async (method?: string): Promise<Held[]> => {
      for (let waited = 0; waited < HELD_WAIT_MS; waited += 5) {
        const held = await mine(method);
        if (held.length > 0) {
          return held;
        }
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      throw new Error(`no ${method ?? "call"} of ${name} was held at the gate`);
    },
    /** Every held call of `method` (any when absent) let go. */
    release: async (method?: string): Promise<void> => {
      for (const call of await mine(method)) {
        await gate.release(call.id);
      }
    },
    /** Every held call of `method` let go with its answer lost: each throws to its caller. */
    lose: async (method: string): Promise<void> => {
      for (const call of await mine(method)) {
        await gate.lose(call.id);
      }
    },
    calls: async (): Promise<Crossed[]> => {
      crossed.push(...await gate.seen(name));
      return crossed;
    },
  };
}

/** The namespace the owner's object keeps its files under (`file_space`), the FILES gate's name. */
export async function spaceOf(stub: DurableObjectStub<OwnerDO>): Promise<string> {
  const [space] = await sqlIn(stub, "SELECT ns FROM file_space");
  if (space === undefined) {
    throw new Error("the owner's object has no files namespace yet");
  }
  return space["ns"] as string;
}

/** The tables of `stub`'s own database whose names begin `prefix`. */
export async function tablesIn(stub: DurableObjectStub<OwnerDO>, prefix: string):
    Promise<string[]> {
  return (await sqlIn(stub, "SELECT name FROM sqlite_master WHERE type = 'table' " +
                              "AND substr(name, 1, length(?)) = ? ORDER BY name", prefix, prefix))
    .map((row) => row["name"] as string);
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
  "UNION SELECT writer FROM file_history) UNION " +
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
  "ON s.writer = h.writer), " +
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

/**
 * `name`'s object, admitted by the platform's door, as the doors' sign-in callback admits a
 * new owner at sign-up (`admission.ts`): a fresh object is `pending` and refuses
 * every door until then. Its seed has not landed yet (`seeded`).
 */
export async function admitted(name: string): Promise<Stub> {
  const stub = env.OWNER.getByName(name);
  await stub.admissionSet({ owner: name, caller: PLATFORM_CALLER, origin: null },
                          { admit: true });
  return stub;
}

/**
 * `name`'s object once its seed of the starter package this code carries has landed: a door
 * hands it the tier's numbers, and its lanes run until its starter says the carried version
 * is reconciled (the `starterReceipts` door), so no test races the seed's alarm.
 */
export async function seeded(stub: Stub, name: string): Promise<Stub> {
  const binding: Binding = { owner: name, caller: name, origin: null, control: OPEN_RECORD };
  for (let tries = 0; tries < 200; tries++) {
    const { version, carried } = await stub.starterReceipts(binding);
    if (version === carried) {
      return stub;
    }
    await drainDue(stub);
  }
  throw new Error(`${name}'s seed did not land`);
}

/**
 * What every owner's seed writes, read from the starter package this code carries
 * (`carried()`), never a literal: its files (path and bytes, inline or placed in R2 past
 * `INLINE_BYTES`, each in one chunk), the directories they make below the workspace, their
 * content ids and chunks, and their totals. A test that counts files, bytes, rows, listing
 * pages or changes adds this to its own; one that means "only what I wrote" filters to its
 * own paths, contents or chunks (`mine`).
 */
export function starterSeed() {
  const files = carried().files.map((each) => {
    const bytes = text(each.text);
    return { path: each.path, bytes: bytes.byteLength, content: contentIdOf(bytes),
             chunk: bytes.byteLength > INLINE_BYTES ? sha256Hex(bytes) : null };
  });
  const dirs = [...new Set(files.flatMap((each) => {
    const parts = each.path.split("/").slice(1, -1);
    return parts.slice(1).map((_, n) => `/${parts.slice(0, n + 2).join("/")}`);
  }))].sort();
  const placed = files.filter((each) => each.chunk !== null);
  const sum = (some: typeof files) => some.reduce((total, each) => total + each.bytes, 0);
  const paths = new Set([...files.map((each) => each.path), ...dirs]);
  const seeded = new Set([...paths, ...files.map((each) => each.content),
                          ...placed.map((each) => each.chunk!)]);
  return {
    files, dirs, paths,
    entries: paths.size,
    bytes: sum(files),
    inlineBytes: sum(files.filter((each) => each.chunk === null)),
    placedBytes: sum(placed),
    placed: placed.length,
    contents: new Set(files.map((each) => each.content)),
    chunks: new Set(placed.map((each) => each.chunk!)),
    /** The owner's agents it defines (an `agent.json` each), each a ledger identity. */
    agents: files.map((each) => each.path.split("/")[3] ?? "")
      .filter((agent, n) => agentPath(agent, AGENT_FILES.agent) === files[n]!.path),
    /**
     * Of `items`, those not the seed's: what the test itself wrote. Each is named (by
     * `nameOf`, itself by default) by its path, a newer version's path beside its file, its
     * content id or its chunk's hash.
     */
    mine: <T>(items: Iterable<T>, nameOf: (item: T) => string = String): T[] =>
      [...items].filter((item) => {
        const named = nameOf(item);
        return !seeded.has(named.endsWith(NEWER) ? named.slice(0, -NEWER.length) : named);
      }),
  };
}

/** `name`'s object, admitted, its seed landed: the rigs' owner (`admitted`, `seeded`). */
export async function admittedOwner(name: string): Promise<Stub> {
  return seeded(await admitted(name), name);
}

/** A fresh owner's admitted object, its seed landed, and the binder's bindings on it. */
export async function ownerObject(): Promise<Owner> {
  const owner = await unseededObject();
  await seeded(owner.stub, owner.name);
  return owner;
}

/**
 * As `ownerObject`, before any door of its own has bound it, so before its seed: for the
 * tests of what a first call binds.
 */
export async function unseededObject(): Promise<Owner> {
  const name = `owner-${crypto.randomUUID()}`;
  return {
    name,
    stub: await admitted(name),
    as: (caller, owner = name) => ({ owner, caller, origin: null, control: OPEN_RECORD }),
  };
}

/** An error as a call fails with it: a refusal's own fields (its `code`) cross RPC with it. */
export type Failed = Error & { readonly code?: string };

/**
 * The code a refusal whose message is `message` carries: the one its message names, as its
 * prefix (`file_quota: ...`), else its family's. A test's expectation, never the code's: it
 * holds the message's prefix and the code together.
 */
export function namedCode(message: string, family: "refused" | "invalid" = "refused"): string {
  return /^([a-z][a-z_]{0,63}):/.exec(message)?.[1] ?? family;
}

/**
 * The error an RPC call fails with. Awaited inside a try, because handing the call's
 * promise to `expect(...).rejects` leaves its rejection reported as unhandled.
 */
export async function failure(call: () => Promise<unknown>): Promise<Failed> {
  try {
    await call();
  } catch (error) {
    return error as Failed;
  }
  throw new Error("expected the call to fail");
}

/** `act` on `stub`'s own stores (`storesOn`), in one unit of their gate. */
export function inStores<R>(stub: Stub, act: (stores: TestStores) => R): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) => {
    const stores = storesOn(state);
    return stores.gate.write(() => act(stores));
  });
}

/**
 * The primitive on `stub`'s own storage, as the object builds it (`storesOn`), `act` run in
 * one unit of the test's gate, as a door runs the collections.
 */
export function inObject<R>(stub: Stub,
                            act: (store: Collections, state: DurableObjectState) => R): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) => {
    const { gate, collections } = storesOn(state);
    return gate.write(() => act(collections, state));
  });
}

/**
 * `inObject` for a test that fills a bound (`fillRows`, `fillHistory`), and then the object's
 * storage let go: a filled object is hundreds of MB on disk, and a test file's objects are
 * kept until the file ends, so its fills would add up.
 */
export async function inFilled<R>(stub: Stub,
                                  act: (store: Collections, state: DurableObjectState) => R):
    Promise<R> {
  try {
    return await inObject(stub, act);
  } finally {
    await runInDurableObject(stub, (_instance: OwnerDO, state) => state.storage.deleteAll());
  }
}

/**
 * What fills a bound in the fewest writes, and so the fewest rows for a recount to read: an
 * inline body as long as one stays (`PLACE_BYTES`), under a collection, a key and a writer at
 * the validators' widest (256 and 512 four-byte code points), each counted in the row and its
 * index entries.
 */
const FILLING = Object.freeze({
  body: "f".repeat(PLACE_BYTES),
  collection: "\u{1D11E}".repeat(255) + "f",
  writer: "\u{1D11E}".repeat(512),
  key: (n: number | string) => "\u{1D11E}".repeat(250) + String(n).padStart(6, "0"),
});

/**
 * The owner's rows filled with rows of the owner's own files scope: `FILLING`'s, then empty
 * ones under short names, until a write is refused past the rows' charge
 * (`LIMITS.maxRowsCharge`), so less room is left than any one more row takes. Their history
 * stays short of its own charge (`fillHistory` passes it). Every filling row is outside the
 * collections a test names.
 */
export function fillRows(store: Collections, owner: string): void {
  const passes = [
    (n: number) => [FILLING.writer, FILLING.collection, FILLING.key(n), FILLING.body] as const,
    (n: number) => [owner, "fill", `e${n}`, ""] as const,
  ];
  for (const pass of passes) {
    for (let n = 0; ; n++) {
      if (n > LIMITS.maxRowsCharge / PLACE_BYTES) {
        throw new Error("the rows were filled past their charge and nothing was refused");
      }
      const [writer, collection, key, body] = pass(n);
      try {
        store.write(owner, writer, collection, key, body, "", false);
      } catch (error) {
        if ((error as Error).message !== "the owner's collections are full") {
          throw error;
        }
        break;
      }
    }
  }
}

/**
 * The history filled with entries of one `FILLING` row written again and again, until
 * `pruned` holds: past the history's charge (`LIMITS.maxHistoryCharge`), its oldest entries
 * are let go.
 */
export function fillHistory(store: Collections, owner: string, pruned: () => boolean): void {
  const key = FILLING.key("h");
  let etag = store.get(FILLING.writer, FILLING.collection, key, false).row?.etag ?? "";
  for (let n = 0; !pruned(); n++) {
    if (n > LIMITS.maxHistoryCharge / PLACE_BYTES) {
      throw new Error("the history was filled past its charge and nothing was let go");
    }
    etag = store.write(owner, FILLING.writer, FILLING.collection, key, FILLING.body, etag,
                       false).etag;
  }
}

export type RowTuple = [string, string, number, number, string, number, string];

/** The stored rows as `(collection, row_id, revision, seq, writer, private, body)`. */
export function rows(stub: Stub): Promise<RowTuple[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Record<string, string | number>>(
      "SELECT collection, row_id, revision, seq, writer, space != '' AS private, body " +
        "FROM collection_rows ORDER BY seq",
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

/**
 * The stored history as `(collection, key, revision, op, body_json, writer)`: the writes'
 * entries and the deletions', a deletion's body empty.
 */
export function history(stub: Stub): Promise<HistoryTuple[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Record<string, string | number>>(
      "SELECT collection, key, revision, op, body_json, writer FROM collection_history " +
        "UNION ALL SELECT collection, key, revision, op, '', writer FROM collection_erasures " +
        "ORDER BY collection, key, revision",
    ).toArray().map((r) => [r["collection"], r["key"], r["revision"], r["op"], r["body_json"],
                            r["writer"]] as HistoryTuple));
}
