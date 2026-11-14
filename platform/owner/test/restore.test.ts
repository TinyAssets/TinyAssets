/**
 * Restore (`restore.ts`; `operator_research.md` §3.5): a production-shaped owner's generation
 * read back into the owner's own object once that object is lost, every table and every counter
 * as it stood at E and every chunk read back; each refusal with nothing written; a restore cut
 * anywhere, or two at once, ending the same, never a row twice; nothing put to R2 and no row in
 * an answer; the load order held to the schema's triggers.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { INLINE_BYTES, LOG_BYTES, PLACE_BYTES } from "../../contract/src/limits";
import type { RestoreAnswer, RestoreHold } from "../../contract/src/owner";
import { exported, NOT_EXPORTED } from "../src/export";
import type { OwnerDO } from "../src/owner-do";
import { loadOrder, RESTORING, SEEDED } from "../src/restore";

const LOAD = loadOrder();
import { erasedWhole } from "./erasure-support";
import {
  BURST, digest, type Generated, generate, type Owner, owner, PAGE, standing,
} from "./generation-support";
import { MUSE, registerOn } from "./ledger-support";
import { drainDue, failure, gateFor, laneJobs, sqlIn, storesOn, TIERS } from "./support";

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
    expect(await rowsOf(stub, FENCED)).toBe(rows);
    expect(await countersOf(stub)).toEqual(counters);
    expect(await stub.storageUsage(o.bind())).toEqual(usage);
    // Every chunk a content names was read back, and nothing was put to R2 or deleted from it.
    const crossed = (await files.calls()).slice(earlier);
    expect(crossed.filter((call) => !call.method.startsWith("get"))).toEqual([]);
    const named = await sqlIn(stub, "SELECT DISTINCT sha256 FROM file_content_chunks");
    expect(named.length).toBeGreaterThan(1);
    expect((done as { chunks: number }).chunks).toBe(named.length);
    expect(crossed.filter((call) => call.method === "getChunks").length)
      .toBeGreaterThanOrEqual(named.length);
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
    for (const method of ["getManifestPart", "getChunks"]) {
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

  it("fails loudly, never admitting, when a row does not read back as the generation's",
     async () => {
    const { o, gen, ns } = await source();
    const stub = await lose(o);
    await stub.restoreBegin(platform(o.name), { namespace: ns, gen: gen.gen });
    // Once loaded and checked, a row changed under the restore, as a trigger that rebuilt a
    // column wrongly would leave it, and the check asked again: it differs from the stream.
    const files = gateFor(env.FILES_GATE, ns);
    await files.arm("getChunks", { hold: "before" });
    const held = stub.restoreStep(platform(o.name));
    await files.held("getChunks");
    await sqlIn(stub, "UPDATE owner_notices SET detail = 'other' WHERE type = 'x'");
    await sqlIn(stub, "UPDATE owner_restore SET step = 'check', at = 0, after = ?",
                JSON.stringify({ p: 0, n: 0 }));
    await files.release("getChunks");
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
      restore: { step: "wiped", gen: gen.gen, fresh: hold.fresh } });
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
});
