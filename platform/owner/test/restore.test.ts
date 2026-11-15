/**
 * Restore (`restore.ts`; `operator_research.md` §3.5): a production-shaped owner's generation
 * read back into the owner's own object once that object is lost, every table and every counter
 * as it stood at E and every chunk read back; each refusal with nothing written; a restore cut
 * anywhere, or two at once, ending the same, never a row twice; nothing put to R2 and no row in
 * an answer; the load order held to the schema's triggers.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { migrate } from "../../contract/src/migrate";
import { sha256Hex } from "../../contract/src/digest";
import { GET_READS } from "../../contract/src/files";
import { DELETE_BATCH, INLINE_BYTES, LOG_BYTES, PLACE_BYTES } from "../../contract/src/limits";
import type { RestoreAnswer, RestoreHold } from "../../contract/src/owner";
import { MANIFEST_UNRESTORABLE, RESTORABLE_LIFE_S } from "../src/backup";
import { exported, NOT_EXPORTED } from "../src/export";
import { CHUNK_ROW_BYTES, CHUNK_ROW_CHARGE } from "../src/manifest";
import { addNames, emptyFilter, FILTER_BYTES } from "../src/names-filter";
import type { OwnerDO } from "../src/owner-do";
import { loadOrder, RESTORING, SEEDED } from "../src/restore";
import { MIGRATIONS } from "../src/schema";

const LOAD = loadOrder();
import { erasedWhole } from "./erasure-support";
import {
  BURST, digest, type Generated, generate, objectsOf, type Owner, owner, PAGE, standing,
} from "./generation-support";
import { MUSE, registerOn } from "./ledger-support";
import {
  drainDue, failure, gateFor, laneAt, laneJobs, ownerObject, storesOn, TIERS,
} from "./support";

type Stub = DurableObjectStub<OwnerDO>;
const W = "/workspace";
/** A marker in every body the fixture writes: no answer and no R2 put may carry it. */
const PLAIN = "plaintext-marker";

const platform = (name: string): Binding => ({ owner: name, caller: PLATFORM_CALLER,
                                               origin: null });
const filled = (size: number, seed: number) =>
  Uint8Array.from({ length: size }, (_, i) => (i * 31 + seed) % 251);

/**
 * Every table the fence changes (`restore.ts` `fence`): the outside clients and their keys
 * revoked (with the policy receipts each revoke writes), the inbound bindings disabled, the
 * connections marked failed.
 */
const FENCED = ["outside_clients", "owner_keys", "policy_receipts", "inbound_bindings",
                "connections"];

/** The counters the triggers keep, which the export leaves out: each table whole. */
const COUNTERS = ["file_shares", "file_scope_contents", "file_scope_chunks", "collection_usage",
                  "proposal_shares"];
/** And the singletons' counters, by column (their allocators and floors move). */
const SINGLE_COUNTERS: Readonly<Record<string, readonly string[]>> = Object.freeze({
  file_space: ["entries", "history", "chunk_bytes", "inline_bytes", "manifest_bytes",
               "body_bytes", "garbage_bytes"],
  collection_space: ["charge", "rows_charge"],
  ledger_usage: ["agents", "enabled_agents", "messages", "unacked", "acked_bytes"],
  // Its receipts' and clients' counts move with the fence's revokes; these do not.
  policy_usage: ["pending_proposals", "applied_proposals", "pending_cards"],
});

/**
 * A production-shaped owner: its seed, a policy written in its session, inline and chunked
 * files with history and garbage, collection rows with a placed body and an erasure, an
 * outside agent with mail read and acked, notices, a model reservation settled and pruned (so
 * the spend outlives it), an API key, an inbound binding and a model connection.
 */
async function shaped(o: Owner): Promise<void> {
  const { generation, policy } = await o.stub.policyRead(o.bind());
  const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
  await o.stub.policyWrite(o.session(), { expected_generation: generation,
                                          policy: { ...rest, codemode: { on: false } } });
  const write = (path: string, content: Uint8Array | string, etag: string | null = null) =>
    o.stub.fileWrite(o.bind(), { path, expected_etag: etag, content });
  const etagOf = (made: unknown) => (made as { entry: { etag: string } }).entry.etag;
  const small = etagOf(await write(`${W}/small.txt`, `${PLAIN} small`));
  await write(`${W}/small.txt`, `${PLAIN} small, again`, small);
  await write(`${W}/big.bin`, filled(INLINE_BYTES + 5000, 2));
  const gone = etagOf(await write(`${W}/gone.bin`, filled(INLINE_BYTES + 7000, 3)));
  await o.stub.fileRemove(o.bind(), { path: `${W}/gone.bin`, expected_etag: gone,
                                      recursive: false });
  const row = (row_id: string, body: string) => o.stub.collectionWrite(o.bind(), {
    collection: "board", row_id, body, expected_etag: "", private: false });
  await row("t1", `${PLAIN} one`);
  await row("t2", `${PLAIN} ${"p".repeat(PLACE_BYTES + 100)}`);
  const doomed = await row("t3", `${PLAIN} three`) as { row: { etag: string } };
  await o.stub.collectionDelete(o.bind(), { collection: "board", row_id: "t3",
                                            expected_etag: doomed.row.etag });
  await runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
    const s = storesOn(state);
    await s.gate.write(() => {
      const agent = registerOn(s, MUSE, { command_center_id: "alpha", display_name: "Muse",
                                          delivery_method: "pull", recipe_id: null }).agent_id;
      let last = 0;
      for (let n = 0; n < 6; n++) {
        last = s.ledger.seqOf(s.ledger.post(null, `dm:${agent}`, `m${n}`, `${PLAIN} ${n}`,
                                            null).message_id)!;
      }
      s.ledger.read(MUSE, 3);
      s.ledger.ack(MUSE, last - 3);
    });
  });
  const sql = (text: string, ...args: SqlStorageValue[]) => sqlIn(o.stub, text, ...args);
  await sql("INSERT INTO owner_notices (at, type, detail) VALUES (1, 'x', ?)", `${PLAIN} n`);
  await sql("INSERT INTO model_reservations (id, run, connection_id, source, model, state, " +
            "max_output_tokens, est_tokens, est_cost, tokens, cost, policy_generation, day, " +
            "month, created_at, settled_at) VALUES ('r1', 'run', 'c', 'src', 'm', 'succeeded', " +
            "10, 10, 7, 9, 5, 1, '20261101', '202611', 1, 2), ('r2', 'run', 'c', 'src', 'm', " +
            "'succeeded', 10, 10, 7, 9, 3, 1, '20261101', '202611', 1, 2)");
  await sql("DELETE FROM model_reservations WHERE id = 'r1'");  // pruned: its spend outlives it
  await sql("INSERT INTO owner_keys (key_id, client, name, created_at) VALUES (?, ?, 'k', 1)",
            "0123456789abcdef", MUSE.client);
  await sql("INSERT INTO inbound_bindings (binding_id, kind, agent, enabled, revision, route, " +
            "created_at, changed_at) VALUES (?, 'capability', 'main', 1, 1, ?, 1, 1)",
            "b".repeat(32), "r".repeat(64));
  await sql("INSERT INTO connections (id, source, kind, display_name, provider, status, " +
            "version, created_at, revocable) VALUES (?, 'src', 'api_key', 'Mine', 'openai', " +
            "'active', 1, 1, 1)", "c".repeat(25));
}

/** Notices and files enough for a base read across several tables. */
async function shapedFiles(o: Owner): Promise<void> {
  for (let n = 0; n < 8; n++) {
    await o.stub.fileWrite(o.bind(), { path: `${W}/f${n}.txt`, expected_etag: null,
                                       content: `${PLAIN} ${n}` });
    await sqlIn(o.stub, "INSERT INTO owner_notices (at, type, detail) VALUES (?, 'x', ?)", n,
                `${PLAIN} notice ${n}`);
  }
}

/** The owner's rows as they stand, every table but `except`, keyed as the export keys them. */
const rowsOf = (stub: Stub, except: readonly string[] = []) =>
  runInDurableObject(stub, (_instance: OwnerDO, state) => {
    const rows = standing(state.storage.sql);
    for (const table of except) {
      delete rows[table];
    }
    return digest(rows);
  });

/** The counters the triggers keep, as they stand. */
const countersOf = (stub: Stub) => runInDurableObject(stub, (_instance: OwnerDO, state) => {
  const sql = state.storage.sql;
  return {
    ...Object.fromEntries(COUNTERS.map((table) => [table, sql.exec(
      `SELECT * FROM ${table} ORDER BY 1, 2`).toArray()])),
    ...Object.fromEntries(Object.entries(SINGLE_COUNTERS).map(([table, columns]) => [table,
      sql.exec(`SELECT ${columns.join(", ")} FROM ${table}`).one()])),
  };
});

/** The object lost: its database deleted and its instance reset, its R2 objects kept. */
async function lose(o: Owner): Promise<Stub> {
  await runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
    await state.storage.deleteAlarm();
    await state.storage.deleteAll();
  });
  await reset(o.stub);
  return env.OWNER.getByName(o.name);
}

/** The instance reset, as a crash resets it: its next call starts a fresh one. */
async function reset(stub: Stub): Promise<void> {
  try {
    await runInDurableObject(stub, (_instance: OwnerDO, state) => state.abort("reset"));
  } catch {
    // the reset's own error, as a crash's
  }
}

/** The restore stepped until it ends; each answer it gave. */
async function stepped(stub: Stub, name: string): Promise<RestoreAnswer[]> {
  const answers: RestoreAnswer[] = [];
  for (let n = 0; n < 50; n++) {
    const step = await stub.restoreStep(platform(name));
    answers.push(step);
    if (step.step === "done" || step.step === "failed") {
      return answers;
    }
  }
  throw new Error("the restore did not end");
}

/** A shaped owner, its generation, and its rows and counters at E (no write after it). */
async function source(): Promise<{ o: Owner; gen: Generated; ns: string; rows: string;
                                   counters: unknown; usage: unknown }> {
  const o = await owner();
  await shaped(o);
  const gen = await generate(o.stub);
  const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
  return { o, gen, ns: space!["ns"] as string, rows: await rowsOf(o.stub, FENCED),
           counters: await countersOf(o.stub), usage: await o.stub.storageUsage(o.bind()) };
}

describe("a restore", () => {
  it("reads a production-shaped owner back into its own object, every table and counter as " +
     "at E, every chunk read back, nothing put to R2 and no row in an answer", async () => {
    const { o, gen, ns, rows, counters, usage } = await source();
    const stub = await lose(o);
    expect(await sqlIn(stub, "SELECT state FROM owner_admission")).toEqual([{ state: "pending" }]);
    const files = gateFor(env.FILES_GATE, ns);
    const earlier = (await files.calls()).length;
    const begun = await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    expect(begun).toEqual({ step: "index", done: false });
    // A restore under way takes no admit, and no import, until it is done.
    expect((await failure(() => stub.admissionSet(platform(o.name), { admit: true }))).message)
      .toBe(RESTORING);
    expect((await failure(() => stub.importNotice(
      { owner: o.name, caller: PLATFORM_CALLER, origin: null, import: { bundle: "b".repeat(64) },
        tiers: TIERS } as Binding, { detail: "x" }))).message).toBe(RESTORING);
    // No lane runs while a restore is under way.
    expect(await laneJobs(stub)).toEqual({});
    const answers = await stepped(stub, o.name);
    const done = answers.at(-1)!;
    expect(done).toMatchObject({ step: "done", gen: gen.gen, state: "admitted", reasons: [],
                                 keys: ["0123456789abcdef"] });
    // Once done, the operator's admit is answered: the owner as the restore left it.
    expect(await stub.admissionSet(platform(o.name), { admit: true }))
      .toEqual({ state: "admitted", reasons: [] });
    // The generation it read is this object's newest: none restorable is told from here on.
    expect(await sqlIn(stub, "SELECT lost FROM owner_backup_state")).toEqual([{ lost: 0 }]);
    expect(await rowsOf(stub, FENCED)).toBe(rows);
    expect(await countersOf(stub)).toEqual(counters);
    expect(await stub.storageUsage(o.bind())).toEqual(usage);
    // Every chunk a content names was checked as stored, and nothing was put to R2 or deleted
    // from it but by the lane's sweep once done, which keeps the generation restored: the
    // restore's own sweep named its rows' chunks and listed the namespace, and found none
    // to delete.
    const crossed = (await files.calls()).slice(earlier);
    expect(crossed.filter((call) => !/^(?:get|check|chunkNames|listChunks)/.test(call.method)
      && !(call.method === "sweepManifests"
           && (call.args as [string, string[]])[1].includes(gen.gen)))).toEqual([]);
    const named = await sqlIn(stub, "SELECT DISTINCT sha256 FROM file_content_chunks");
    expect(named.length).toBeGreaterThan(1);
    expect((done as { chunks: number }).chunks).toBe(named.length);
    const checked = crossed.filter((call) => call.method === "checkChunks")
      .flatMap((call) => (call.args as [string, string, string[]])[2]);
    expect(new Set(checked)).toEqual(new Set(named.map((row) => row["sha256"])));
    expect(crossed.filter((call) => call.method === "getChunks")).toEqual([]);
    // No answer carries a row: the doors write them to the operator log.
    expect(JSON.stringify([begun, ...answers])).not.toContain(PLAIN);
  });

  it("fences what the backup never held: clients, keys, grants, inbound routes, connections; " +
     "and gives no allocator's value again", async () => {
    const { o, gen, ns } = await source();
    const [top] = await sqlIn(o.stub, "SELECT max(version) AS v FROM file_history");
    const [seq] = await sqlIn(o.stub, "SELECT max(seq) AS s FROM ledger_messages");
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    await stepped(stub, o.name);
    expect(await sqlIn(stub, "SELECT mode, revoked_at IS NOT NULL AS revoked FROM " +
                             "outside_clients")).toEqual([{ mode: "revoked", revoked: 1 }]);
    expect(await sqlIn(stub, "SELECT revoked_at IS NOT NULL AS revoked FROM owner_keys"))
      .toEqual([{ revoked: 1 }]);
    expect(await sqlIn(stub, "SELECT enabled, revision, route != ? AS fresh FROM " +
                             "inbound_bindings", "r".repeat(64)))
      .toEqual([{ enabled: 0, revision: 2, fresh: 1 }]);
    expect(await sqlIn(stub, "SELECT status FROM connections")).toEqual([{ status: "error" }]);
    const [fence] = await sqlIn(stub, "SELECT mcp_through FROM grant_fences");
    expect(fence!["mcp_through"]).toBeGreaterThan(0);
    // A sign-in is needed again: no session came back.
    expect(await sqlIn(stub, "SELECT count(*) AS n FROM owner_sessions")).toEqual([{ n: 0 }]);
    // A new file version and a new message go past every one the generation named.
    const [space] = await sqlIn(stub, "SELECT seq, feed_floor FROM file_space");
    expect(space!["seq"]).toBeGreaterThanOrEqual(top!["v"] as number);
    expect(space!["feed_floor"]).toBe(space!["seq"]);
    const [ledger] = await sqlIn(stub, "SELECT last_seq FROM ledger_usage");
    expect(ledger!["last_seq"]).toBeGreaterThanOrEqual(seq!["s"] as number);
    const written = await stub.fileWrite(o.bind(), { path: `${W}/after.txt`,
                                                     expected_etag: null, content: "after" }) as { entry: { version: number } };
    expect(written.entry.version).toBeGreaterThan(top!["v"] as number);
    // The restored generation is the object's newest, and an export reads it.
    expect((await stub.restoreSource(platform(o.name))).generations.map((each) => each.gen))
      .toEqual([gen.gen]);
  });

  it("reads a generation the owner wrote through, its log's rows given way to by the base's",
     async () => {
    const o = await owner();
    await shapedFiles(o);
    // A base of two bursts, and after every unit of the generation: a notice changed, one
    // gone, one new, and a file written, so the log holds rows of every kind before E.
    await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE " +
                        "i < ?) INSERT INTO owner_notices (at, type, detail) SELECT 0, 'x', " +
                        "printf('%.1000c', 'a') FROM n", BURST + 2 * PAGE);
    let after = 0;
    const gen = await generate(o.stub, { afterUnit: async ({ files, gate }) => {
      after++;
      await files.write(`${W}/during-${after}.txt`,
                        new TextEncoder().encode(`${PLAIN} ${after}`),
                        { expected: null, writer: "owner" });
      await gate.write(() => {
        gate.storage.sql.exec("UPDATE owner_notices SET detail = ? WHERE seq = 1", `${after}`);
        gate.storage.sql.exec("DELETE FROM owner_notices WHERE seq = ?", 10 + after);
        gate.storage.sql.exec("INSERT INTO owner_notices (at, type, detail) VALUES (?, 'y', " +
                              "'new')", after);
      });
    } });
    expect(gen.lines.filter((line) => line.t === "gone").length).toBeGreaterThan(0);
    const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: space!["ns"] as string, gen: gen.gen });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done" });
    expect(await rowsOf(stub)).toBe(gen.atE);
  });

  it("keeps the generation before a newest whose log is past what a restore stages, and " +
     "restores from it", async () => {
    const o = await owner();
    await shapedFiles(o);
    const first = await generate(o.stub);
    await o.stub.fileWrite(o.bind(), { path: `${W}/later.txt`, expected_etag: null,
                                       content: "later" });
    // The next generation's log as a busy pass's would be, recorded as its finish records it,
    // before the same pass prunes: the one before it is then kept, as the fallback.
    const second = await generate(o.stub, { afterUnit: async ({ gate }) => {
      await gate.write(() => gate.storage.sql.exec(
        "UPDATE owner_backups SET log_bytes = ? WHERE state = 'done' AND gen != ?",
        LOG_BYTES + 1, first.gen));
    } });
    await drainDue(o.stub);
    expect((await o.stub.restoreSource(platform(o.name))).generations
      .map((each) => [each.gen, each.restorable]))
      .toEqual([[first.gen, true], [second.gen, false]]);
    // The kept one restores the owner as it stood at its E.
    const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: space!["ns"] as string,
                                                gen: first.gen });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done", gen: first.gen });
    expect(await rowsOf(stub)).toBe(first.atE);
  });

  it("lets the fallback go a day before plain collection could reach a chunk it names, and " +
     "tells the owner none can be restored", async () => {
    const o = await owner();
    await shapedFiles(o);
    const first = await generate(o.stub);
    await o.stub.fileWrite(o.bind(), { path: `${W}/later.txt`, expected_etag: null,
                                       content: "later" });
    const second = await generate(o.stub, { afterUnit: async ({ gate }) => {
      await gate.write(() => gate.storage.sql.exec(
        "UPDATE owner_backups SET log_bytes = ? WHERE state = 'done' AND gen != ?",
        LOG_BYTES + 1, first.gen));
    } });
    await drainDue(o.stub);
    // Nothing changed since the newest began, so no later generation takes the fallback's
    // place; and a give-up's notice was given since (review F1), which must not silence this.
    await sqlIn(o.stub, "UPDATE owner_backups SET changed_at = NULL WHERE gen = ?", second.gen);
    await sqlIn(o.stub, "UPDATE owner_backup_state SET told = 1");
    const [kept] = await sqlIn(o.stub, "SELECT changed_at FROM owner_backups WHERE gen = ?",
                               first.gen);
    const ends = (kept!["changed_at"] as number) + RESTORABLE_LIFE_S;
    const restorable = async () => (await o.stub.restoreSource(platform(o.name))).generations
      .map((each) => [each.gen, each.restorable]);
    // The lane's next night finds no change: its only work left is the fallback's end.
    vi.useFakeTimers({ toFake: ["Date"], now: (await laneAt(o.stub, "manifest"))! * 1000 });
    await drainDue(o.stub);
    expect(await laneAt(o.stub, "manifest")).toBeCloseTo(ends, 0);
    vi.setSystemTime((ends - 60) * 1000);
    expect(await restorable()).toEqual([[first.gen, true], [second.gen, false]]);
    // At its end: offered no more and refused a pin, then pruned, and the owner told.
    vi.setSystemTime(ends * 1000);
    expect(await restorable()).toEqual([[first.gen, false], [second.gen, false]]);
    expect((await failure(() => o.stub.restorePin(platform(o.name), { gen: first.gen })))
      .code).toBe("refused");
    await drainDue(o.stub);
    expect(await restorable()).toEqual([[second.gen, false]]);
    expect(await sqlIn(o.stub, "SELECT type FROM owner_notices WHERE type = ?",
                       MANIFEST_UNRESTORABLE)).toEqual([{ type: MANIFEST_UNRESTORABLE }]);
  });

  it("tries a given-up generation again the next night, and lets a stale newest go once the " +
     "change it lacks is RESTORABLE_LIFE_S old", async () => {
    const o = await owner();
    await o.stub.fileWrite(o.bind(), { path: `${W}/gone.txt`, expected_etag: null,
                                       content: "to be deleted" });
    const newest = await generate(o.stub);
    // Three days on, the owner deletes a file, and the generation begun for it is given up
    // (its log overflowed, as in an R2 outage), with nothing written during it.
    const start = Date.now();
    vi.useFakeTimers({ toFake: ["Date"], now: start + 3 * 86_400_000 });
    await o.stub.fileRemove(o.bind(), { path: `${W}/gone.txt`, expected_etag: null,
                                        recursive: false });
    const lost = await generate(o.stub, { duringCall: async ({ gate, n }) => {
      if (n === 1) {
        await gate.quiet(() => gate.storage.sql.exec("UPDATE log_capture SET overflow = 1"));
      }
    } });
    expect(lost.notices).toEqual(["manifest_log_overflow"]);
    await drainDue(o.stub);
    const restorable = async () => (await o.stub.restoreSource(platform(o.name))).generations
      .map((each) => [each.gen, each.restorable]);
    // Tried again the next night after the one given up began, never at once.
    const today = Math.floor(Date.now() / 86_400_000);
    expect(await laneAt(o.stub, "manifest")).toBeGreaterThanOrEqual((today + 1) * 86_400);
    expect(await restorable()).toEqual([[newest.gen, true]]);
    // Every try since failed: once the change it lacks is RESTORABLE_LIFE_S old, the newest is
    // offered no more, and the lane deletes it with what it holds of the deleted file.
    const [row] = await sqlIn(o.stub, "SELECT changed_at FROM owner_backups WHERE gen = ?",
                              newest.gen);
    vi.setSystemTime(((row!["changed_at"] as number) + RESTORABLE_LIFE_S) * 1000);
    expect(await restorable()).toEqual([[newest.gen, false]]);
    expect((await failure(() => o.stub.restorePin(platform(o.name), { gen: newest.gen })))
      .code).toBe("refused");
    await drainDue(o.stub);
    expect(await sqlIn(o.stub, "SELECT gen FROM owner_backups WHERE gen = ?", newest.gen))
      .toEqual([]);
    const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
    expect(await objectsOf(space!["ns"] as string, newest.gen)).toEqual([]);
  });

  it("maps each generation of migration 12 as changed from its start, but the newest of an " +
     "owner with no change marked, none being written and no give-up told since", async () => {
    for (const told of [0, 1]) {
      const { stub } = await ownerObject();
      const marks = await runInDurableObject(stub, (_instance: OwnerDO, state) => {
        const sql = state.storage.sql;
        for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
            "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
            "AND name NOT GLOB 'cf_*'").toArray()) {
          sql.exec(`DROP TABLE "${name}"`);
        }
        migrate(state.storage, MIGRATIONS.slice(0, 11));
        for (const at of [100, 200]) {
          sql.exec("INSERT INTO owner_backups (gen, day, state, cursor, parts, part_list, " +
                     "bytes, s, schema, started_at, finished_at, seq) VALUES (?, '20261101', " +
                     "'done', 'ended', 0, '', 0, 1, 11, ?, ?, ?)", String(at).padStart(16, "0"),
                   at, at, at);
        }
        sql.exec("UPDATE owner_backup_state SET changed = 0, told = ?", told);
        migrate(state.storage, MIGRATIONS);
        return sql.exec("SELECT changed_at FROM owner_backups ORDER BY seq").toArray()
          .map((row) => row["changed_at"]);
      });
      // A give-up told since the newest finished was begun for a change the newest lacks.
      expect(marks).toEqual([100, told === 1 ? 200 : null]);
    }
  });

  it("keeps the owner's admission as it stood at E: a suspension holds", async () => {
    const o = await owner();
    await o.stub.admissionSet(platform(o.name), { reason: "abuse", suspended: true });
    const gen = await generate(o.stub);
    const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: space!["ns"] as string, gen: gen.gen });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({
      step: "done", state: "suspended", reasons: ["abuse"] });
  });
});

describe("a restore refuses, writing nothing", () => {
  /** Nothing of a restore in the object: no record, no namespace, no staged row. */
  const untouched = async (stub: Stub) => {
    expect(await sqlIn(stub, "SELECT (SELECT count(*) FROM owner_restore) AS r, (SELECT " +
                             "count(*) FROM restore_rows) AS s, (SELECT ns FROM file_space) " +
                             "AS ns")).toEqual([{ r: 0, s: 0, ns: null }]);
  };

  it("into an admitted object", async () => {
    const { o, gen, ns } = await source();
    const refused = await failure(() => o.stub.restoreBegin(platform(o.name),
                                                            { namespace: ns, gen: gen.gen }));
    expect([refused.code, refused.message]).toEqual(
      ["refused", "the owner is admitted: a restore takes only a pending owner"]);
    expect(await sqlIn(o.stub, "SELECT count(*) AS n FROM owner_restore")).toEqual([{ n: 0 }]);
  });

  it("into a pending object that holds anything", async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    await sqlIn(stub, "INSERT INTO owner_notices (at, type, detail) VALUES (1, 'x', 'y')");
    const refused = await failure(() => stub.restoreBegin(platform(o.name),
                                                          { namespace: ns, gen: gen.gen }));
    expect(refused.message).toBe("the owner's object is not empty (owner_notices): a restore " +
                                 "takes only an empty one");
    expect(await sqlIn(stub, "SELECT count(*) AS n FROM owner_restore")).toEqual([{ n: 0 }]);
  });

  it("another owner's generation, or one the namespace does not hold", async () => {
    const { gen, ns } = await source();
    const other = await owner();
    const stub = await lose(other);
    const refused = await failure(() => stub.restoreBegin(platform(other.name),
                                                          { namespace: ns, gen: gen.gen }));
    expect([refused.code, refused.message]).toEqual(["refused",
                                                     "the generation is another owner's"]);
    await untouched(stub);
    const missing = await failure(() => stub.restoreBegin(platform(other.name),
      { namespace: ns, gen: "0".repeat(16) }));
    expect(missing.message).toBe(`the namespace holds no finished generation ${"0".repeat(16)}`);
    await untouched(stub);
  });

  it("an erased owner: its tombstone beats its generation", async () => {
    const { o, gen, ns } = await source();
    await erasedWhole(o.stub, o.name, o.session());
    const refused = await failure(() => o.stub.restoreBegin(platform(o.name),
                                                            { namespace: ns, gen: gen.gen }));
    expect(refused.code).toBe("owner_erased");
    expect((await failure(() => o.stub.restoreStep(platform(o.name)))).code).toBe("owner_erased");
  });

  it("any caller but the platform's", async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    const refused = await failure(() => stub.restoreBegin(
      { owner: o.name, caller: o.name, origin: null }, { namespace: ns, gen: gen.gen }));
    expect(refused.code).toBe("refused");
    await untouched(stub);
  });
});

describe("a restore cut short", () => {
  it("goes on from its record after a crash at any read, never loading a row twice", async () => {
    const { o, gen, ns, rows, counters } = await source();
    let stub = await lose(o);
    const files = gateFor(env.FILES_GATE, ns);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // Each read of a part, then of a chunk, held and the object reset under it, as a crash.
    for (const method of ["getManifestPart", "checkChunks"]) {
      await files.arm(method, { hold: "before" });
      const cut = stub.restoreStep(platform(o.name)).catch(() => null);
      await files.held(method);
      await reset(stub);
      await files.release(method);
      await cut;
      stub = env.OWNER.getByName(o.name);
      // The fresh instance syncs its schedule as it starts: no lane runs on a half-restored
      // owner (its rows loaded, its chunks not yet read back), the garbage collector's none.
      expect(await laneJobs(stub)).toEqual({});
    }
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done" });
    expect(await rowsOf(stub, FENCED)).toBe(rows);
    expect(await countersOf(stub)).toEqual(counters);
  });

  it("ends the same when two run at once, and refuses a second begin once done", async () => {
    const { o, gen, ns, rows } = await source();
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // The same begin again answers as it stands.
    expect(await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen }))
      .toEqual({ step: "index", done: false });
    await Promise.all([stepped(stub, o.name), stepped(stub, o.name)]);
    expect(await rowsOf(stub, FENCED)).toBe(rows);
    expect((await failure(() => stub.restoreBegin(platform(o.name),
      { namespace: ns, gen: "1".repeat(16) }))).code).toBe("refused");
    expect(await stub.restoreStep(platform(o.name))).toMatchObject({ step: "done" });
  });

  it("fails at its chunks step when a chunk is not stored as its hash, named, never done " +
     "or admitted, and is wiped again by its words", async () => {
    const { o, gen, ns } = await source();
    const [space] = await sqlIn(o.stub, "SELECT data_key FROM file_space");
    const [chunk] = await sqlIn(o.stub, "SELECT sha256 FROM file_content_chunks LIMIT 1");
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // A chunk the generation names gone from the bucket: the bucket's answer, not a read lost.
    await env.FILES.deleteChunks(ns, space!["data_key"] as string, [chunk!["sha256"] as string]);
    expect((await stepped(stub, o.name)).at(-1)).toEqual({
      step: "failed", done: false,
      failed: "1 of the generation's chunks are not stored as their hash (missing)" });
    expect((await failure(() => stub.admissionSet(platform(o.name), { admit: true }))).code)
      .toBe("restoring");
    expect(await stub.restorePin(platform(o.name), { gen: gen.gen })).toMatchObject({
      step: "failed", confirm: `wipe-${o.name}-${gen.gen}` });
  });

  it("fails loudly, never admitting, when a row does not read back as the generation's",
     async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // Once loaded and checked, a row changed under the restore, as a trigger that rebuilt a
    // column wrongly would leave it, and the check asked again: it differs from the stream.
    const files = gateFor(env.FILES_GATE, ns);
    await files.arm("checkChunks", { hold: "before" });
    const held = stub.restoreStep(platform(o.name));
    await files.held("checkChunks");
    await sqlIn(stub, "UPDATE owner_notices SET detail = 'other' WHERE type = 'x'");
    await sqlIn(stub, "UPDATE owner_restore SET step = 'check', at = 0, after = ?",
                JSON.stringify({ p: 0, n: 0 }));
    await files.release("checkChunks");
    await held;
    const failed = (await stepped(stub, o.name)).at(-1)!;
    expect(failed).toMatchObject({ step: "failed" });
    // Nothing admits a failed restore: not the platform's admit, as a sign-in or the
    // operator's owner.admit sends it.
    const admit = await failure(() => stub.admissionSet(platform(o.name), { admit: true }));
    expect([admit.code, admit.message]).toEqual(["restoring", RESTORING]);
    expect(await sqlIn(stub, "SELECT state FROM owner_admission")).toEqual([{ state: "pending" }]);
  });
});

describe("the load table", () => {
  it("loads every exported table once, and only the tables a fresh object seeds hold a row",
     async () => {
    expect(LOAD.map((load) => load.table).sort()).toEqual(Object.keys(exported()).sort());
    expect(Object.hasOwn(NOT_EXPORTED, "restore_rows")).toBe(true);
    const o = await owner();
    const stub = await lose(o);
    const held = await runInDurableObject(stub, (_instance: OwnerDO, state) =>
      Object.keys(exported()).filter((table) => state.storage.sql.exec<{ n: number }>(
        `SELECT count(*) AS n FROM ${table}`).one().n > 0));
    expect(held.sort()).toEqual([...SEEDED].sort());
  });

  it("is held to the schema's triggers: a reference count loads before the rows that count " +
     "on it, a sum after the rows that add to it", async () => {
    const o = await owner();
    const triggers = await sqlIn(o.stub, "SELECT tbl_name, sql FROM sqlite_master WHERE " +
                                         "type = 'trigger'") as { tbl_name: string; sql: string }[];
    const tables = exported();
    const at = (table: string) => LOAD.findIndex((load) => load.table === table);
    const wrong: string[] = [];
    for (const { tbl_name: from, sql } of triggers) {
      const body = sql.slice(sql.indexOf(" BEGIN ") + 7);
      for (const [, table, set] of body.matchAll(/UPDATE (\w+) SET (.*?)(?: WHERE |;)/g)) {
        if (!Object.hasOwn(tables, table!) || !Object.hasOwn(tables, from) || table === from) {
          continue;
        }
        const load = LOAD[at(table!)]!;
        for (const [, column] of set!.matchAll(/(?:^|, )(\w+) = /g)) {
          if (!tables[table!]!.columns.includes(column!)) {
            continue;
          }
          const ref = (load.refs ?? []).includes(column!);
          if (ref !== at(table!) < at(from)) {
            wrong.push(`${table}.${column} (kept by ${from}'s triggers)`);
          }
        }
      }
      for (const [, table] of body.matchAll(/INSERT INTO (\w+) /g)) {
        if (Object.hasOwn(tables, table!) && Object.hasOwn(tables, from) && table !== from
            && at(table!) < at(from) && (LOAD[at(table!)]!.refs ?? []).length === 0) {
          wrong.push(`${table} (rows made by ${from}'s triggers)`);
        }
      }
    }
    expect(wrong).toEqual([]);
  });
});

afterEach(() => {
  vi.useRealTimers();
});

/** The pin asked until the owner's present state is written; its last answer. */
async function pinnedTo(stub: Stub, name: string, gen: string): Promise<RestoreHold> {
  for (let n = 0; n < 50; n++) {
    const hold = await stub.restorePin(platform(name), { gen });
    if (hold.written) {
      return hold;
    }
  }
  throw new Error("the pin's fresh generation was not written");
}

/** The object wiped, as the confirmed wipe leaves it: the call ends with its reset. */
async function wipedWith(stub: Stub, name: string, confirm: string): Promise<Stub> {
  await failure(() => stub.restoreWipe(platform(name), { confirm }));
  return env.OWNER.getByName(name);
}

/** The schema's tables, indexes and triggers, by name: what a fresh object's start makes. */
const schemaOf = (stub: Stub) => sqlIn(stub, "SELECT type, name FROM sqlite_master WHERE " +
                                             "substr(name, 1, 4) != '_cf_' ORDER BY 1, 2");

describe("a restore into a live owner", () => {
  it("pins the generation, holds the owner and writes its present state, wipes the object " +
     "to pending and empty with R2 untouched, and restores the pinned one equal at E",
     async () => {
    const { o, gen, ns, rows, counters } = await source();
    // Written after E: the fresh generation's finish would prune `gen` but for the pin.
    await o.stub.fileWrite(o.bind(), { path: `${W}/after.txt`, expected_etag: null,
                                       content: `${PLAIN} after` });
    expect((await o.stub.restoreSource(platform(o.name))).begins).toBe(false);
    const first = await o.stub.restorePin(platform(o.name), { gen: gen.gen });
    expect(first).toMatchObject({ step: "pinned", namespace: ns, gen: gen.gen });
    // From the pin, the owner is held: its doors and its admission.
    const door = await failure(() => o.stub.fileRead(o.bind(), { path: `${W}/after.txt` }));
    expect([door.code, door.message]).toEqual(["restoring", RESTORING]);
    expect((await failure(() => o.stub.admissionSet(platform(o.name), { reason: "abuse",
                                                                        suspended: true }))).code)
      .toBe("restoring");
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    expect(hold.fresh).not.toBe(gen.gen);
    expect(hold.confirm).toBe(`wipe-${o.name}-${hold.fresh}`);
    // Both kept: the pinned one past the fresh one's finish, which pruned every other.
    expect((await o.stub.restoreSource(platform(o.name))).generations.map((each) => each.gen))
      .toEqual([gen.gen, hold.fresh]);
    const wrong = await failure(() => o.stub.restoreWipe(platform(o.name),
                                                         { confirm: `wipe-${o.name}-${gen.gen}` }));
    expect(wrong.code).toBe("invalid");
    const files = gateFor(env.FILES_GATE, ns);
    const earlier = (await files.calls()).length;
    const stub = await wipedWith(o.stub, o.name, hold.confirm!);
    // Pending and empty, as a new object's start makes it, the pin's record held across.
    expect(await stub.restoreSource(platform(o.name))).toEqual({
      namespace: null, generations: [], state: "pending", begins: true,
      restore: { step: "wiped", gen: gen.gen, fresh: hold.fresh, since: expect.any(Number) } });
    const fresh = await owner();
    expect(await schemaOf(stub)).toEqual(await schemaOf(await lose(fresh)));
    expect((await failure(() => stub.admissionSet(platform(o.name), { admit: true }))).code)
      .toBe("restoring");
    // Asked again, the wipe answers as it stands; nothing crossed to R2 but reads.
    expect(await stub.restoreWipe(platform(o.name), { confirm: hold.confirm! }))
      .toMatchObject({ step: "wiped", confirm: null });
    expect((await files.calls()).slice(earlier).filter((call) => !call.method.startsWith("get")))
      .toEqual([]);
    // Only the generation it was pinned for, or the one its pin wrote.
    expect((await failure(() => stub.restoreBegin(platform(o.name),
      { namespace: ns, gen: "1".repeat(16) }))).code).toBe("refused");
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done", gen: gen.gen });
    expect(await rowsOf(stub, FENCED)).toBe(rows);
    expect(await countersOf(stub)).toEqual(counters);
    expect(await stub.admissionSet(platform(o.name), { admit: true }))
      .toEqual({ state: "admitted", reasons: [] });
  });

  it("undoes to the generation its pin wrote, and wipes a failed restore again", async () => {
    const { o, gen, ns } = await source();
    await o.stub.fileWrite(o.bind(), { path: `${W}/after.txt`, expected_etag: null,
                                       content: `${PLAIN} after` });
    const present = await rowsOf(o.stub, FENCED);
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    let stub = await wipedWith(o.stub, o.name, hold.confirm!);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // A failed check, as a rebuilt column gone wrong leaves it: the restore failed.
    await sqlIn(stub, "UPDATE owner_restore SET step = 'failed', failed = 'x'");
    const failed = await stub.restorePin(platform(o.name), { gen: hold.fresh! });
    expect(failed).toMatchObject({ step: "failed", written: true, confirm: hold.confirm });
    stub = await wipedWith(stub, o.name, failed.confirm!);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: hold.fresh! });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done", gen: hold.fresh });
    expect(await rowsOf(stub, FENCED)).toBe(present);
  });

  it("holds the owner through a pin cut short, refuses the wipe until its state is " +
     "written, and gives the pin up as it was", async () => {
    const { o, gen, ns } = await source();
    const files = gateFor(env.FILES_GATE, ns);
    await files.arm("putManifestRoot", { hold: "before" });
    const cut = o.stub.restorePin(platform(o.name), { gen: gen.gen }).catch(() => null);
    await files.held("putManifestRoot");
    const early = await failure(() => o.stub.restoreWipe(platform(o.name), { confirm: "x" }));
    expect(early.message).toBe("the owner's present state is not yet written: ask restore.pin " +
                               "again until it is");
    await reset(o.stub);
    await files.release("putManifestRoot");
    await cut;
    let stub = env.OWNER.getByName(o.name);
    // The fresh instance: still held, its doors and its admission refused.
    expect((await failure(() => stub.fileRead(o.bind(), { path: `${W}/small.txt` }))).code)
      .toBe("restoring");
    expect((await failure(() => stub.admissionSet(platform(o.name), { admit: true }))).code)
      .toBe("restoring");
    await pinnedTo(stub, o.name, gen.gen);
    expect((await failure(() => stub.restorePin(platform(o.name), { gen: "1".repeat(16) })))
      .code).toBe("refused");
    expect(await stub.restoreRelease(platform(o.name))).toEqual({ released: true });
    stub = env.OWNER.getByName(o.name);
    expect(new TextDecoder().decode((await stub.fileRead(o.bind(), { path: `${W}/small.txt` }))
      .inline!)).toContain(PLAIN);
    expect((await stub.restoreSource(platform(o.name))).restore).toBeNull();
    expect(Object.keys(await laneJobs(stub)).length).toBeGreaterThan(0);
  });

  it("answers when the hold began, the pin's time, through the wipe and the begin",
     async () => {
    const { o, gen, ns } = await source();
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    const since = (await o.stub.restoreSource(platform(o.name))).restore!.since;
    // An hour on, the hold still dates from the pin.
    vi.useFakeTimers({ toFake: ["Date"], now: since * 1000 + 3_600_000 });
    const stub = await wipedWith(o.stub, o.name, hold.confirm!);
    expect((await stub.restoreSource(platform(o.name))).restore!.since).toBe(since);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    expect((await stub.restoreSource(platform(o.name))).restore)
      .toMatchObject({ step: "index", since });
  });

  it("writes the pin's fresh generation as the newest though the clock reads before the " +
     "pinned one began, as a clock behind the last generation's reads it", async () => {
    const { o, gen } = await source();
    const [pinned] = await sqlIn(o.stub, "SELECT started_at FROM owner_backups WHERE gen = ?",
                                 gen.gen);
    // The object's clock a minute before the pinned generation's start, the event the race
    // turned on: its fresh generation then begins at an earlier reading than the pinned one.
    vi.useFakeTimers({ toFake: ["Date"], now: (pinned!["started_at"] as number) * 1000 - 60_000 });
    const hold = await o.stub.restorePin(platform(o.name), { gen: gen.gen });
    expect(hold).toMatchObject({ step: "pinned", written: true,
                                 confirm: `wipe-${o.name}-${hold.fresh}` });
    expect((await o.stub.restoreSource(platform(o.name))).generations.map((each) => each.gen))
      .toEqual([gen.gen, hold.fresh]);
  });

  it("takes a wipe that throws back whole: the owner still held, its tables and pin as they " +
     "were", async () => {
    const { o, gen } = await source();
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    // A table in the way of a migration, as a schema the export's lists missed would leave.
    await sqlIn(o.stub, "CREATE TABLE owner_restore_next (x)");
    const before = await schemaOf(o.stub);
    expect((await failure(() => o.stub.restoreWipe(platform(o.name),
                                                   { confirm: hold.confirm! }))).message)
      .toMatch(/owner_restore_next already exists/);
    expect(await schemaOf(o.stub)).toEqual(before);
    expect(await sqlIn(o.stub, "SELECT step, gen, fresh FROM owner_restore")).toEqual([
      { step: "pinned", gen: gen.gen, fresh: hold.fresh }]);
    expect((await failure(() => o.stub.fileRead(o.bind(), { path: `${W}/small.txt` }))).code)
      .toBe("restoring");
  });

  it("refuses a pin of a pending owner, of a generation it does not hold, and of an erased " +
     "owner, and a wipe with none pinned, writing nothing", async () => {
    const { o, gen } = await source();
    const missing = await failure(() => o.stub.restorePin(platform(o.name),
                                                          { gen: "0".repeat(16) }));
    expect(missing.message).toBe(`generation ${"0".repeat(16)} is not one of the owner's ` +
                                 "finished generations a restore can load: restore.source " +
                                 "names them");
    const none = await failure(() => o.stub.restoreWipe(platform(o.name), { confirm: "x" }));
    expect(none.message).toBe("no generation of this owner is pinned: restore.pin first");
    expect(await sqlIn(o.stub, "SELECT count(*) AS n FROM owner_restore")).toEqual([{ n: 0 }]);
    const other = await owner();
    const lost = await lose(other);
    expect((await failure(() => lost.restorePin(platform(other.name), { gen: gen.gen })))
      .message).toBe("the owner is pending: a pin holds a live owner, and a pending one is " +
                     "restored into with none");
    await erasedWhole(o.stub, o.name, o.session());
    expect((await failure(() => o.stub.restorePin(platform(o.name), { gen: gen.gen }))).code)
      .toBe("owner_erased");
  });

  it("keeps in R2 the generation its pin wrote while held, and sweeps it once the restore is " +
     "done, keeping the one restored", async () => {
    const { o, gen, ns } = await source();
    await o.stub.fileWrite(o.bind(), { path: `${W}/after.txt`, expected_etag: null,
                                       content: `${PLAIN} after` });
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    const stub = await wipedWith(o.stub, o.name, hold.confirm!);
    // Held: no lane runs, and only the record names the pin's generation, the undo.
    expect(await objectsOf(ns, hold.fresh!)).toContain("root");
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done", gen: gen.gen });
    await stub.admissionSet(platform(o.name), { admit: true });
    await drainDue(stub);
    expect(await objectsOf(ns, hold.fresh!)).toEqual([]);
    expect(await objectsOf(ns, gen.gen)).toContain("root");
    expect(await sqlIn(stub, "SELECT sweep FROM owner_backup_state")).toEqual([{ sweep: 0 }]);
  });

  it("refuses a rotation's rewrap while a restore holds the owner, through the wipe, so no kid " +
     "is retired under the roots it restores from; and answers once it is done", async () => {
    const { o, gen, ns } = await source();
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    const pinned = await failure(() => o.stub.dataKeyRewrap(platform(o.name)));
    expect([pinned.code, pinned.message]).toEqual(["restoring", RESTORING]);
    // Wiped, the object holds neither the namespace nor a row of either generation.
    const stub = await wipedWith(o.stub, o.name, hold.confirm!);
    expect((await failure(() => stub.dataKeyRewrap(platform(o.name)))).code).toBe("restoring");
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    expect((await failure(() => stub.dataKeyRewrap(platform(o.name)))).code).toBe("restoring");
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done", gen: gen.gen });
    expect(await stub.dataKeyRewrap(platform(o.name))).toEqual({ rewrapped: false, roots: 1 });
  });
});

/** What the bucket finds of each of `hashes` under `stub`'s namespace and key. */
async function bucketed(stub: Stub, hashes: readonly string[]): Promise<string[]> {
  const [space] = await sqlIn(stub, "SELECT ns, data_key FROM file_space");
  const found: string[] = [];
  for (let at = 0; at < hashes.length; at += GET_READS) {
    found.push(...await env.FILES.checkChunks(space!["ns"] as string,
                                              space!["data_key"] as string,
                                              hashes.slice(at, at + GET_READS)));
  }
  return found;
}

/** The filter of `stub`'s chunk rows' names, made afresh from its rows as `names` makes it. */
async function filterFrom(stub: Stub): Promise<Uint8Array> {
  const [space] = await sqlIn(stub, "SELECT ns, data_key FROM file_space");
  const hashes = (await sqlIn(stub, "SELECT sha256 FROM file_chunks ORDER BY sha256"))
    .map((row) => row["sha256"] as string);
  const filter = emptyFilter();
  for (let at = 0; at < hashes.length; at += DELETE_BATCH) {
    addNames(filter, await env.FILES.chunkNames(space!["ns"] as string,
                                                space!["data_key"] as string,
                                                hashes.slice(at, at + DELETE_BATCH)));
  }
  return filter;
}

/** The restore's filter as its row holds it. */
async function filterHeld(stub: Stub): Promise<Uint8Array> {
  const [row] = await sqlIn(stub, "SELECT bits FROM restore_filter");
  return new Uint8Array(row!["bits"] as ArrayBuffer);
}

/** `count` garbage chunk rows, no file's, put into `stub`'s object: rows with no bytes. */
const garbageRows = (stub: Stub, count: number) => sqlIn(stub,
  "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) INSERT INTO " +
    "file_chunks (sha256, size, state, stored, refs, holds, gc_after) SELECT " +
    "printf('%064x', i), 1, 'garbage', 1, 0, 0, 1e12 FROM n", count);

/**
 * The restore stepped, in the background, until the files Worker is asked `method`, held
 * there; the steps' end, once let go.
 */
async function heldAt(stub: Stub, name: string, files: ReturnType<typeof gateFor>,
                      method: string): Promise<{ cut: Promise<unknown> }> {
  await files.arm(method, { hold: "before" });
  const cut = stepped(stub, name).catch(() => null);
  await files.held(method);
  return { cut };
}

describe("a restore's sweep of the chunks no row names", () => {
  it("deletes a chunk put after E, and keeps every chunk a row names: a live file's, an " +
     "upload's held at E, and a deleted one's an open export reads, which revives", async () => {
    const o = await owner();
    const [a, g, u, b] = [11, 12, 13, 14].map((seed) => filled(INLINE_BYTES + 100 * seed, seed));
    const write = (path: string, content: Uint8Array) =>
      o.stub.fileWrite(o.bind(), { path, expected_etag: null, content });
    await write(`${W}/a.bin`, a!);
    const made = await write(`${W}/g.bin`, g!) as { entry: { etag: string } };
    await generate(o.stub);
    // An export opened on that generation, which names g's chunk; g then deleted and its
    // versions purged, so at E its chunk is a garbage row in its grace.
    await o.stub.ownerExport(o.session(), { cursor: null });
    await o.stub.fileRemove(o.bind(), { path: `${W}/g.bin`, expected_etag: made.entry.etag,
                                        recursive: false });
    await o.stub.fileHistoryPurge(o.bind(), { path: `${W}/g.bin` });
    // An upload held at E, its chunk not yet sent.
    const [ha, hg, hu, hb] = [a!, g!, u!, b!].map((bytes) => sha256Hex(bytes));
    const intent = await o.stub.fileUploadBegin(o.bind(), { size: u!.byteLength, chunks: [hu!] });
    expect(await sqlIn(o.stub, "SELECT sha256, state FROM file_chunks WHERE sha256 IN (?, ?) " +
                               "ORDER BY state", hg!, hu!))
      .toEqual([{ sha256: hg, state: "garbage" }, { sha256: hu, state: "uploading" }]);
    const gen = await generate(o.stub);
    // After E: the upload's chunk put, and a file written.
    await o.stub.fileUploadChunks(o.bind(), { intent_id: intent.intent_id,
                                              chunks: [{ sha256: hu!, bytes: u! }] });
    await write(`${W}/b.bin`, b!);
    const all = [ha!, hg!, hu!, hb!];
    expect(await bucketed(o.stub, all)).toEqual(["stored", "stored", "stored", "stored"]);
    const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
    const ns = space!["ns"] as string;
    const files = gateFor(env.FILES_GATE, ns);
    const hold = await pinnedTo(o.stub, o.name, gen.gen);
    const stub = await wipedWith(o.stub, o.name, hold.confirm!);
    const earlier = (await files.calls()).length;
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    expect((await stepped(stub, o.name)).at(-1)).toMatchObject({ step: "done", gen: gen.gen });
    expect(await bucketed(stub, all)).toEqual(["stored", "stored", "stored", "missing"]);
    // One delete, of the one chunk no row names, and the filter gone with the staged rows.
    expect((await files.calls()).slice(earlier).filter((call) => call.method === "deleteListed")
      .map((call) => (call.args as [string, string[]])[1].length)).toEqual([1]);
    expect(await sqlIn(stub, "SELECT count(*) AS n FROM restore_filter")).toEqual([{ n: 0 }]);
    // The deleted file revives by an upload that need send nothing: its chunk is stored.
    await stub.admissionSet(platform(o.name), { admit: true });
    expect((await stub.fileUploadBegin(o.bind(), { size: g!.byteLength, chunks: [hg!] }))
      .missing).toEqual([]);
  });

  it("fails at names, deleting nothing, when the generation holds no admission a restore " +
     "admits", async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    const files = gateFor(env.FILES_GATE, ns);
    const earlier = (await files.calls()).length;
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    const { cut } = await heldAt(stub, o.name, files, "checkChunks");
    await sqlIn(stub, "UPDATE owner_restore SET held = ?",
                JSON.stringify({ owner_admission: { state: "pending", reasons: "[]" } }));
    await files.release("checkChunks");
    await cut;
    expect(await stub.restoreStep(platform(o.name))).toEqual({
      step: "failed", done: false, failed: "the generation holds the owner's admission at E as " +
        "pending: a restore admits only an admitted or suspended one" });
    expect((await files.calls()).slice(earlier).filter((call) => !/^(?:get|check)/.test(
      call.method))).toEqual([]);
  });

  it("goes on from its cursor after a crash between pages of names, setting the bits a run " +
     "with none sets, and holds the filter in one row of its derived size", async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    const files = gateFor(env.FILES_GATE, ns);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // Rows past one names unit's 10,000, so names takes two pages.
    let { cut } = await heldAt(stub, o.name, files, "checkChunks");
    await garbageRows(stub, 10_500);
    // The first page's ten calls let through, and the second page's first held: a crash then.
    await files.arm("chunkNames", { hold: "before" }, 11);
    await files.release("checkChunks");
    for (let call = 0; call < 10; call++) {
      await files.held("chunkNames");
      await files.release("chunkNames");
    }
    await files.held("chunkNames");
    const [mid] = await sqlIn(stub, "SELECT step, after FROM owner_restore");
    const hashes = (await sqlIn(stub, "SELECT sha256 FROM file_chunks ORDER BY sha256 LIMIT 10000"))
      .map((row) => row["sha256"] as string);
    expect(mid).toEqual({ step: "names", after: hashes.at(-1) });
    await reset(stub);
    await files.release("chunkNames");
    await cut;
    const again = env.OWNER.getByName(o.name);
    ({ cut } = await heldAt(again, o.name, files, "listChunks"));
    expect(Buffer.from(await filterHeld(again)).equals(Buffer.from(await filterFrom(again))))
      .toBe(true);
    expect(await sqlIn(again, `SELECT length(bits) AS n, ${CHUNK_ROW_BYTES()} AS charge FROM ` +
                              "restore_filter")).toEqual([{ n: FILTER_BYTES(), charge:
                                                             CHUNK_ROW_CHARGE() }]);
    await files.release("listChunks");
    await cut;
    expect((await stepped(again, o.name)).at(-1)).toMatchObject({ step: "done" });
    const named = (await sqlIn(again, "SELECT DISTINCT sha256 FROM file_content_chunks"))
      .map((row) => row["sha256"] as string);
    expect(new Set(await bucketed(again, named))).toEqual(new Set(["stored"]));
  });

  it("ends the same after a crash between names and sweep and one mid-sweep, deleting each " +
     "unnamed chunk once, a page at a time", async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    // Unnamed chunks past one listed page, put straight into the bucket.
    const orphans = Array.from({ length: 1_200 }, (_, n) => sha256Hex(`orphan ${n}`));
    await Promise.all(orphans.map((name) => env.BLOBS.put(`o/${ns}/c/${name}`, "x")));
    const files = gateFor(env.FILES_GATE, ns);
    const earlier = (await files.calls()).length;
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // A crash with names done and the sweep not begun.
    let { cut } = await heldAt(stub, o.name, files, "listChunks");
    expect(await sqlIn(stub, "SELECT step, after FROM owner_restore"))
      .toEqual([{ step: "sweep", after: null }]);
    await reset(stub);
    await files.release("listChunks");
    await cut;
    // A crash mid-sweep: the second page's delete landed, its answer lost.
    let again = env.OWNER.getByName(o.name);
    await files.arm("deleteListed", { hold: "after" }, 2);
    cut = stepped(again, o.name).catch(() => null);
    await files.held("deleteListed");
    await files.release("deleteListed");
    await files.held("deleteListed");
    await files.lose("deleteListed");
    await cut;
    again = env.OWNER.getByName(o.name);
    expect((await stepped(again, o.name)).at(-1)).toMatchObject({ step: "done" });
    const crossed = (await files.calls()).slice(earlier);
    const deleted = crossed.filter((call) => call.method === "deleteListed")
      .flatMap((call) => (call.args as [string, string[]])[1]);
    expect([deleted.length, new Set(deleted).size]).toEqual([orphans.length, orphans.length]);
    expect(new Set(deleted)).toEqual(new Set(orphans));
    // Pages: the sweep listed from a name past the first page.
    expect(crossed.filter((call) => call.method === "listChunks")
      .some((call) => (call.args as [string, string | null])[1] !== null)).toBe(true);
    const named = (await sqlIn(again, "SELECT DISTINCT sha256 FROM file_content_chunks"))
      .map((row) => row["sha256"] as string);
    expect(new Set(await bucketed(again, named))).toEqual(new Set(["stored"]));
  });

  it("maps a record of migration 13 unchanged through migration 14", async () => {
    const { stub } = await ownerObject();
    const mapped = await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
          "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
          "AND name NOT GLOB 'cf_*'").toArray()) {
        sql.exec(`DROP TABLE "${name}"`);
      }
      migrate(state.storage, MIGRATIONS.slice(0, 13));
      sql.exec("INSERT INTO owner_restore (only, ns, gen, step, at, after, held, spans, fresh, " +
                 "since) VALUES (1, ?, ?, 'clear', 7, NULL, '{}', '[]', ?, 5)", "a".repeat(32),
               "b".repeat(16), "c".repeat(16));
      const before = sql.exec("SELECT * FROM owner_restore").toArray();
      migrate(state.storage, MIGRATIONS);
      return { before, after: sql.exec("SELECT * FROM owner_restore").toArray(),
               filters: sql.exec("SELECT count(*) AS n FROM restore_filter").one().n };
    });
    expect(mapped.after).toEqual(mapped.before);
    expect(mapped.filters).toBe(0);
  });
});
