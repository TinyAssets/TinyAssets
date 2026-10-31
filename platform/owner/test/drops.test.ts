/**
 * Drops wipe, run retention and the owner's erasure (tasks 5, S4-S5), in workerd on faux
 * models: every path that forgets a thread wipes its store first (the `dropping` mark, the
 * Session ended and closed, the store destroyed, then the row forgotten), a drop cut short
 * finished by the `threads` lane, a live thread never marked; a run's store wiped
 * `keep_runs` after it settled, its status answered from its receipt, receipts let go at
 * `keep_run_receipts` and bounded; and erasure's store-first order, resumed after a crash.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import type { JsonObject } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { NO_THREAD } from "../src/agent";
import { ERASING } from "../src/erasure";
import { WriteGate } from "../src/gate";
import { clock, type Origin } from "../src/ledger";
import { type Binding, NO_OPERATION, type Operation, OwnerDO } from "../src/owner-do";
import {
  DEFAULT_POLICY_JSON, RUN_DEFAULTS, RUN_SETTINGS, runRetention, validate,
} from "../src/policy";
import { holderId } from "../src/remote-storage";
import { namedByOpenWork } from "../src/scheduled";
import { HOST, wakeJob } from "../src/thread-host";
import { DESTROYED } from "../src/thread-store";
import {
  DROP_RETRY_S, RECEIPT_TEXT_MAX, retainThreads, RUN_RETENTION, type Thread, THREAD_LIMITS,
  Threads, utf8Prefix,
} from "../src/threads";
import { until } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { failure, sqlIn, storeSql } from "./support";
import { type FauxOwnerDO, Reminder, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** Retention at a second: a conversation idle, and a run settled, a second ago are taken. */
const QUICK = Object.freeze({ idleS: 1, perPass: 32 });
const QUICK_RUNS = Object.freeze({ keepRunsS: 1, keepReceiptsS: 90 * 86_400 });

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

function viaTa(name: string, args: JsonObject): FauxContentBlock {
  return fauxToolCall("ta", { op: "call", name, arguments: args });
}

/** A user message's text: the envelope's content when the text is one. */
function said(message: Message): string {
  const text = typeof message.content === "string" ? message.content
    : message.content.map((part) => (part.type === "text" ? part.text : "")).join("");
  try {
    const envelope = JSON.parse(text) as { untrusted?: boolean; content?: string };
    return envelope.untrusted === true ? envelope.content ?? "" : text;
  } catch {
    return text;
  }
}

/** The model: `hold please` holds, `spawn hold` holds in background work, else an echo. */
function reply(messages: Message[]): AssistantMessage {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says("tools answered");
  }
  const asked = last === undefined ? "" : said(last);
  if (asked === "hold please") {
    return calls(viaTa("hold", {}));
  }
  if (asked === "spawn hold") {
    return calls(viaTa("background", { work: "hold please" }));
  }
  return says(`echo: ${asked}`);
}

function gate(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** An owner of its own on the faux-model class, signed in, its model answering `reply`. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  // A fresh stub each time: one a crash broke is never reused.
  const fresh = () => env.AGENT.getByName(owner);
  const stub = fresh();
  const at = signedIn();
  const bind = (origin: Origin | null): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }) });
  const { session: cookie } = await stub.sessionOpen({ ...bind(null), authenticated_at: at });
  const session = (): Binding => ({ owner, caller: owner, origin: null, session: cookie });
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
    runInDurableObject(fresh(), async (instance: FauxOwnerDO, state) => {
      instance.script = Array.from({ length: 200 }, () => reply);
      return act(instance, state);
    });
  await inside(() => undefined);  // the model's script, before any door asks it
  return {
    owner, bind, session, at, in: inside,
    get stub() {
      return fresh();
    },
    admit: async (origin: Origin) => {
      await stub.outsideAgents(bind(origin));
    },
    /** Every thread row, any state. */
    threads: () => inside((_instance, state) => state.storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads ORDER BY created_at").toArray()),
    settled: async (binding: Binding, operation: string): Promise<Operation> => {
      for (let tries = 0; tries < 400; tries++) {
        const status = await stub.agentStatus(binding, { operation_id: operation });
        if (status.status === "done" || status.status === "unanswered") {
          return status;
        }
        await sleep(25);
      }
      throw new Error("the operation did not settle");
    },
    /** One `threads` lane pass at quick retention, through the object's own drop path. */
    retain: (perPass = 32) => inside(async (instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, THREAD_LIMITS, { ...QUICK, perPass },
                                  namedByOpenWork, () => QUICK_RUNS);
      return retainThreads(threads, clock, gated.write, (thread) => instance.agent.drop(thread));
    }),
  };
}

type Rig = Awaited<ReturnType<typeof rig>>;

/** How a store stands: its pi tables, and whether a fresh holder could open it. */
async function storeState(store: string): Promise<{ tables: number; opens: string }> {
  const tables = (await storeSql(store, "SELECT name FROM sqlite_master WHERE name LIKE " +
                                        "'pi\\_%' ESCAPE '\\'")).length;
  const opens = await env.PISTORE.getByName(store).open(holderId())
    .then(() => "opened", (error: Error) => (error.message.includes(DESTROYED) ? DESTROYED
      : error.message));
  return { tables, opens };
}

/** A store's pi tables, read without opening it (an open would fence its Session). */
async function piTables(store: string): Promise<number> {
  return (await storeSql(store, "SELECT name FROM sqlite_master WHERE name LIKE 'pi_%'"))
    .length;
}

/** Reset the object as a crash would, and wait until a new instance answers. */
async function crash(r: { readonly stub: DurableObjectStub }): Promise<void> {
  await runInDurableObject(r.stub, (_instance, state) => state.abort("crash"))
    .catch(() => undefined);
  for (let tries = 0; tries < 100; tries++) {
    const answered = await runInDurableObject(r.stub, () => true).catch(() => false);
    if (answered) {
      return;
    }
    await sleep(10);
  }
  throw new Error("the object did not come back");
}

/** The rows keyed to a thread in the owner's database, and its wake job. */
async function keyed(r: Rig, thread: string): Promise<Record<string, number>> {
  const count = async (statement: string, ...params: string[]) =>
    (await sqlIn(r.stub, statement, ...params)).length;
  return {
    row: await count("SELECT 1 FROM agent_threads WHERE thread_id = ?", thread),
    live: await count("SELECT 1 FROM agent_live WHERE thread_id = ?", thread),
    usage: await count("SELECT 1 FROM agent_usage WHERE thread_id = ?", thread),
    job: await count("SELECT 1 FROM cf_agents_jobs WHERE id = ? AND capability = ?",
                     wakeJob(thread), HOST),
  };
}

const GONE = Object.freeze({ row: 0, live: 0, usage: 0, job: 0 });

describe("a drop (S4)", () => {
  it("wipes the thread's store before its row is forgotten: the store is empty and " +
     "refuses any open, and every row keyed to the thread goes with it", async () => {
    const r = await rig();
    const sent = await r.stub.agentSubmit(r.bind(null), { message: "hello",
                                                         client_send_id: "a" });
    expect(sent.status).toBe("done");
    const [thread] = await r.threads();
    const size = await env.PISTORE.getByName(thread!.store).size();
    await r.in((instance) => instance.agent.host.close(thread!.thread_id));
    expect((await keyed(r, thread!.thread_id)).usage).toBe(1);
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    // As its store was wiped, its Session was closed and its row still named it.
    const wiped = await r.in((instance) => instance.probes.wiped.get(thread!.store));
    expect(wiped?.open).not.toContain(thread!.thread_id);
    expect(await storeState(thread!.store)).toEqual({ tables: 0, opens: DESTROYED });
    expect(await env.PISTORE.getByName(thread!.store).size()).toBeLessThan(size);
    expect(await keyed(r, thread!.thread_id)).toEqual(GONE);
    expect(await r.in((instance) => instance.agent.host.openThreads())).toEqual([]);
  }, 30_000);

  it("never marks a live thread: one working, and one whose task sleeps", async () => {
    const r = await rig();
    const held = gate();
    await r.in((instance) => { instance.probes.holdGate = held.promise; });
    const working = await r.stub.agentSubmit(r.bind(null), { message: "hold please",
                                                            client_send_id: "w", wait_ms: 0 });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    const sleeper = await r.in(async (instance) => {
      const made = await instance.agent.thread("helper", null, { kind: "converse" });
      const pi = await instance.agent.host.pi(made.thread_id);
      const root = (await pi.conversation(1 as never, BG))!;
      await root.commit((tx) => tx.createTask(Reminder, { until: Date.now() + 3_600_000 },
                                              { ownership: { kind: "conversation" },
                                                background: true }), BG);
      pi.resume();
      return made.thread_id;
    });
    await until(async () => (await sqlIn(r.stub, "SELECT 1 FROM agent_live WHERE " +
                                                 "thread_id = ?", sleeper)).length === 1,
                "the sleeper's live row");
    await r.in((instance) => instance.agent.host.close(sleeper));
    await sleep(1100);
    expect(await r.retain()).toBe(0);
    const rows = await r.threads();
    expect(rows.map((row) => row.state)).toEqual(["ready", "ready"]);
    for (const row of rows) {
      expect(await piTables(row.store)).toBeGreaterThan(0);
    }
    expect(await r.in((instance) => instance.probes.wiped.size)).toBe(0);
    held.release();
    await r.settled(r.bind(null), working.operation_id);
  }, 30_000);

  it("answers a dropped thread's operations as no such operation, while it drops and after",
     async () => {
    const r = await rig();
    const sent = await r.stub.agentSubmit(r.bind(null), { message: "hello",
                                                         client_send_id: "a" });
    const held = gate();
    await r.in((instance) => { instance.probes.destroyGate = held.promise; });
    await sleep(1100);
    const pass = r.retain();
    await until(async () => (await r.threads())[0]?.state === "dropping", "the mark");
    const asked = () => failure(() => r.stub.agentStatus(r.bind(null), {
      operation_id: sent.operation_id }));
    expect((await asked()).message).toBe(NO_OPERATION);
    expect((await failure(() => r.stub.agentCancel(r.bind(null), {
      operation_id: sent.operation_id }))).message).toBe(NO_OPERATION);
    // Never opened again while it drops: the store is about to be wiped.
    expect(await r.in((instance) => instance.agent.host.pi(sent.thread_id)
      .then(() => "opened", (error: Error) => error.message))).toBe(NO_THREAD);
    held.release();
    expect(await pass).toBe(1);
    expect((await asked()).message).toBe(NO_OPERATION);
    expect(await r.threads()).toEqual([]);
  }, 30_000);

  it("is finished by the lane after a crash between any two of its steps", async () => {
    const r = await rig();
    // Three of the owner's conversations, one an agent, each with its store made.
    const made = await r.in(async (instance) => {
      const ids: string[] = [];
      for (const agent of ["agent-a", "agent-b", "agent-c"]) {
        const thread = await instance.agent.thread(agent, null, { kind: "converse" });
        await instance.agent.host.pi(thread.thread_id);
        await instance.agent.host.close(thread.thread_id);
        ids.push(thread.thread_id);
      }
      return ids;
    });
    const [marked, unwiped, wipedOnly] = made as [string, string, string];
    const stores = new Map((await r.threads()).map((row) => [row.thread_id, row.store]));
    // Crash after the mark: the row dropping, its Session still open.
    await r.in(async (instance, state) => {
      await instance.agent.host.pi(marked);
      state.storage.sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ?",
                             marked);
    });
    // Crash after the store was wiped, before the row was forgotten.
    await r.in((_instance, state) => state.storage.sql.exec(
      "UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ?", wipedOnly));
    await env.PISTORE.getByName(stores.get(wipedOnly)!).destroy();
    await crash(r);
    // A crash at the destroy itself: the lane tries it again later, by its row.
    await r.in((instance) => { instance.probes.destroyFails = 1; });
    await r.in((_instance, state) => state.storage.sql.exec(
      "UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ?", unwiped));
    const due = await r.in((instance) => instance.parts.threads.nextDue(clock()));
    expect(due).toBeLessThanOrEqual(clock() + 1);
    await r.retain();
    const left = await r.threads();
    expect(left.map((row) => [row.thread_id, row.state])).toEqual([[left[0]!.thread_id,
                                                                    "dropping"]]);
    expect(left[0]!.drop_retry).toBeGreaterThan(clock() + DROP_RETRY_S - 5);
    expect(await r.in((instance) => instance.parts.threads.nextDue(clock())))
      .toBeCloseTo(left[0]!.drop_retry!, 0);
    expect(await r.in((instance) => instance.parts.threads.dropping(clock()))).toEqual([]);
    await r.in((_instance, state) => state.storage.sql.exec(
      "UPDATE agent_threads SET drop_retry = 1"));
    await r.retain();
    expect(await r.threads()).toEqual([]);
    for (const thread of made) {
      expect(await storeState(stores.get(thread)!)).toEqual({ tables: 0, opens: DESTROYED });
      expect(await keyed(r, thread)).toEqual(GONE);
    }
  }, 30_000);

  it("on a revoke while a tool runs: the work ended, then the Session closed, then the " +
     "store wiped", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const held = gate();
    await r.in((instance) => { instance.probes.holdGate = held.promise; });
    const run = await r.stub.agentRun(r.bind(MUSE), { agent: "main", input: "hold please" });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    const [thread] = await r.threads();
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    const wiped = await r.in((instance) => instance.probes.wiped.get(thread!.store));
    // When the store was wiped, the run had already ended, and its Session closed.
    expect(wiped!.submissions.map((record) => [record.requestId, record.status,
                                               (record as { reason?: string }).reason]))
      .toEqual([[run.operation_id.split(".")[1], "unanswered", "aborted"]]);
    expect(wiped!.open).not.toContain(thread!.thread_id);
    expect(await storeState(thread!.store)).toEqual({ tables: 0, opens: DESTROYED });
    expect(await keyed(r, thread!.thread_id)).toEqual(GONE);
    held.release();
  }, 30_000);

  it("on a grant change, wipes a client's sleeping task with its store, its wake job too",
     async () => {
    const r = await rig();
    await r.admit(MUSE);
    const stamp = { client: MUSE.client, generation: 1, authenticated_at: r.at,
                    universe: null, agent: "main" };
    const thread = await r.in(async (instance) => {
      const made = await instance.agent.thread("main", stamp);
      const pi = await instance.agent.host.pi(made.thread_id);
      const root = (await pi.conversation(1 as never, BG))!;
      await root.commit((tx) => tx.createTask(Reminder, { until: Date.now() + 86_400_000 },
                                              { ownership: { kind: "conversation" },
                                                background: true }), BG);
      pi.resume();
      return made;
    });
    await until(async () => (await keyed(r, thread.thread_id)).job === 1, "the sleeper's job");
    await r.in((instance) => instance.agent.host.close(thread.thread_id));
    expect(await keyed(r, thread.thread_id)).toEqual({ row: 1, live: 1, usage: 1, job: 1 });
    expect((await storeSql(thread.store, "SELECT 1 FROM pi_tasks WHERE status != 'terminal'"))
      .length).toBe(1);
    const grant = (await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         name: "renamed", expected_generation: grant.generation,
                                         scopes: [] });
    expect(await storeState(thread.store)).toEqual({ tables: 0, opens: DESTROYED });
    expect(await keyed(r, thread.thread_id)).toEqual(GONE);
    // A run's receipt stays: how it stood, nothing pi answered.
    expect(await sqlIn(r.stub, "SELECT status, reason FROM run_receipts WHERE " +
                               "thread_id = ?", thread.thread_id))
      .toEqual([{ status: "unanswered", reason: "not_found" }]);
  }, 30_000);
});

describe("a run's retention (S5)", () => {
  it("wipes a settled run's store at keep_runs and answers its status from its receipt",
     async () => {
    const r = await rig();
    await r.admit(MUSE);
    const long = "é".repeat(3_000);  // an answer past the receipt's 4 KiB
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: long });
    const done = await r.settled(r.bind(null), run.operation_id);
    expect(done).toMatchObject({ status: "done", text: `echo: ${long}` });
    const [thread] = await r.threads();
    // A status read that opens its Session again is its activity too (pi reports the open's
    // reconcile as work), so a run read since is kept from then.
    await r.in((instance) => instance.agent.host.close(thread!.thread_id));
    await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id });
    expect((await r.threads())[0]!.last_active).toBeGreaterThanOrEqual(thread!.last_active);
    // At policy's own keep_runs, the lane is due a week after it settled.
    const read = (await r.threads())[0]!;
    expect(await r.in((instance) => instance.parts.threads.nextDue(clock())))
      .toBeCloseTo(read.last_active + RUN_RETENTION.keepRunsS, 0);
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await storeState(thread!.store)).toEqual({ tables: 0, opens: DESTROYED });
    expect(await r.threads()).toEqual([]);
    const answered = await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id });
    expect(answered).toEqual({ operation_id: run.operation_id, thread_id: thread!.thread_id,
                               status: "done",
                               text: utf8Prefix(`echo: ${long}`, RECEIPT_TEXT_MAX) });
    expect(new TextEncoder().encode(answered.text!).length).toBeLessThanOrEqual(4096);
    expect(answered.text!.length).toBeGreaterThan(2000);
    // Another request id in the run's thread is one pi never saw; another caller's is none.
    expect(await r.stub.agentStatus(r.bind(null), {
      operation_id: `${thread!.thread_id}.run_${"0".repeat(32)}` }))
      .toMatchObject({ status: "unanswered", reason: "not_found" });
    expect((await failure(() => r.stub.agentStatus(r.bind(MUSE), {
      operation_id: run.operation_id }))).message).toBe(NO_OPERATION);
    expect(await r.stub.agentCancel(r.bind(null), { operation_id: run.operation_id }))
      .toEqual({ operation_id: run.operation_id, cancelled: false });
  }, 30_000);

  it("keeps a run whose background work is live, and wipes it once that settled",
     async () => {
    const r = await rig();
    const held = gate();
    await r.in((instance) => { instance.probes.holdGate = held.promise; });
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "spawn hold" });
    expect(await r.settled(r.bind(null), run.operation_id)).toMatchObject({ status: "done" });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the background hold");
    await sleep(1100);
    expect(await r.retain()).toBe(0);
    const [kept] = await r.threads();
    expect(kept!.state).toBe("ready");
    expect(await piTables(kept!.store)).toBeGreaterThan(0);
    held.release();
    await until(async () => (await sqlIn(r.stub, "SELECT 1 FROM agent_live")).length === 0,
                "the background work to settle");
    await r.in((instance) => instance.agent.host.close(kept!.thread_id));
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await storeState(kept!.store)).toEqual({ tables: 0, opens: DESTROYED });
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toMatchObject({ status: "done" });
  }, 30_000);

  it("refuses none of 1,000 launches in a day, past the owner's conversation bound; runs " +
     "and receipts each refuse loudly at their own", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage);
      // The owner at its conversations' bound.
      await gated.write(() => {
        for (let i = 0; i < THREAD_LIMITS.owner; i++) {
          state.storage.sql.exec(
            "INSERT INTO agent_threads (thread_id, store, state, agent_id, created_at, kind, " +
              "last_active) VALUES (?, ?, 'ready', ?, 1, 'converse', 1)",
            `thread_${i.toString(16).padStart(32, "0")}`, `b${i.toString(16).padStart(31, "0")}`,
            `agent-${i}`);
        }
      });
      const refused = (act: () => unknown) => {
        try {
          act();
          return "made";
        } catch (error) {
          return (error as Error).message;
        }
      };
      const conversation = await gated.write(() => refused(() =>
        threads.record("another", null, clock(), "converse")));
      let launched = 0;
      for (let i = 0; i < 1_000; i++) {
        await gated.write(() => threads.record("main", null, clock(), "run"));
        launched++;
      }
      const runs = new Threads(gated.storage, { ...THREAD_LIMITS, runs: 1_000 });
      const atRuns = await gated.write(() => refused(() => runs.record("main", null, clock())));
      const receipts = new Threads(gated.storage, { ...THREAD_LIMITS, receipts: 1_001 });
      await gated.write(() => state.storage.sql.exec(
        "INSERT INTO run_receipts (thread_id, client, request, status, reason, text, " +
          "settled_at) VALUES (?, NULL, NULL, 'unanswered', 'not_found', '', 1)",
        `thread_${"f".repeat(32)}`));
      const atReceipts = await gated.write(() => refused(() =>
        receipts.record("main", null, clock())));
      return { conversation, launched, atRuns, atReceipts };
    });
    expect(out).toEqual({ conversation: "the owner's threads are at their limit",
                          launched: 1_000, atRuns: "the agent's runs are at their limit",
                          atReceipts: "the runs' receipts are at their limit" });
    // At 1,000 a day, the policy's highest keep_runs and its default receipts fit the bounds.
    expect(1_000 * RUN_SETTINGS.keep_runs[1]).toBeLessThan(THREAD_LIMITS.runs);
    expect(1_000 * RUN_DEFAULTS.keep_run_receipts).toBeLessThanOrEqual(THREAD_LIMITS.receipts);
  }, 60_000);

  it("lets receipts go at keep_run_receipts, keeps the rest, and refuses a receipt past " +
     "its bound", async () => {
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      const gated = new WriteGate(state.storage);
      const now = clock();
      await gated.write(() => {
        for (const [n, at] of [[1, now - 91 * 86_400], [2, now - 89 * 86_400]] as const) {
          state.storage.sql.exec(
            "INSERT INTO run_receipts (thread_id, client, request, status, reason, text, " +
              "settled_at) VALUES (?, NULL, ?, 'done', NULL, 'ok', ?)",
            `thread_${String(n).repeat(32)}`, `run_${String(n).repeat(32)}`, at);
        }
      });
      const due = instance.parts.threads.nextDue(now);
      const threads = new Threads(gated.storage, THREAD_LIMITS, QUICK, namedByOpenWork,
                                  () => RUN_RETENTION);
      await retainThreads(threads, clock, gated.write, (thread) => instance.agent.drop(thread));
      const left = state.storage.sql.exec<{ thread_id: string }>(
        "SELECT thread_id FROM run_receipts").toArray().map((row) => row.thread_id);
      // A receipt past the bound is a bug: the run's room was kept when it was made.
      const bounded = new Threads(gated.storage, { ...THREAD_LIMITS, receipts: 1 });
      const run = await gated.write(() => threads.record("main", null, now));
      await gated.write(() => threads.retain(run.thread_id, run.last_active, now));
      const past = await gated.write(() => bounded.keepReceipt(threads.row(run.thread_id)!, {
        request: null, status: "unanswered", reason: "not_found", text: "" }))
        .then(() => "kept", (error: Error) => error.message);
      return { due, left, past };
    });
    expect(out.due).toBeLessThanOrEqual(clock() + 1);
    expect(out.left).toEqual([`thread_${"2".repeat(32)}`]);
    expect(out.past).toBe("the runs' receipts are past their bound");
  }, 30_000);

  it("takes keep_runs and keep_run_receipts from owner policy, within their bounds", () => {
    const base = JSON.parse(DEFAULT_POLICY_JSON) as Record<string, unknown>;
    expect(runRetention(validate(base))).toEqual(RUN_RETENTION);
    expect(runRetention(validate({ ...base, runs: { keep_runs: 30, keep_run_receipts: 365 } })))
      .toEqual({ keepRunsS: 30 * 86_400, keepReceiptsS: 365 * 86_400 });
    const refused = (runs: unknown) => {
      try {
        validate({ ...base, runs });
        return "valid";
      } catch (error) {
        return (error as Error).message;
      }
    };
    expect([refused({ keep_runs: 31 }), refused({ keep_runs: 0 }),
            refused({ keep_run_receipts: 366 }), refused({ keep_runs: 1.5 }),
            refused({ other: 1 }), refused([])])
      .toEqual(Array(6).fill("invalid runs policy"));
    expect(refused({ keep_runs: 10, keep_run_receipts: 9 }))
      .toBe("runs policy: keep_run_receipts is at least keep_runs");
    expect(refused({ keep_runs: 10, keep_run_receipts: 10 })).toBe("valid");
  });
});

describe("the owner's erasure (S4-S5)", () => {
  /** An owner with a conversation, a run never opened, and a collection row. */
  async function populated() {
    const r = await rig();
    await r.stub.agentSubmit(r.bind(null), { message: "hello", client_send_id: "a" });
    await r.in((instance) => instance.agent.thread("main", null));
    await r.stub.collectionWrite(r.bind(null), { collection: "board", row_id: "t", body: "1",
                                                 expected_etag: "", private: false });
    const stores = (await r.threads()).map((row) => row.store);
    expect(stores).toHaveLength(2);
    return { r, stores };
  }

  /** The owner's own tables: none of the runtime's (`_cf_`). */
  const tables = (r: Rig) => r.in((_instance, state) => state.storage.sql.exec<
    { name: string }>("SELECT name FROM sqlite_master WHERE type = 'table'").toArray()
    .map((row) => row.name).filter((name) => !name.startsWith("_cf")));

  it("destroys every store a row names before the owner's deleteAll, a thread still " +
     "being made among them, while every door, lane and new thread refuses", async () => {
    const { r, stores } = await populated();
    const held = gate();
    const out = await r.in(async (instance, state) => {
      instance.probes.destroyGate = held.promise;
      // A thread being made: its row recorded, its first open in flight.
      const making = await instance.agent.thread("helper", null);
      const opening = instance.agent.host.pi(making.thread_id).then(() => "opened",
        (error: Error) => error.message);
      const erasing = instance.erasure.erase();
      await until(() => state.storage.sql.exec("SELECT 1 FROM owner_erasure").toArray()
        .length === 1, "the mark");
      const recorded = await instance.agent.thread("main", null)
        .then(() => "made", (error: Error) => error.message);
      // An old receipt makes the threads lane due: it does not run while the owner erases.
      state.storage.sql.exec(
        "INSERT INTO run_receipts (thread_id, client, request, status, reason, text, " +
          "settled_at) VALUES (?, NULL, NULL, 'unanswered', 'not_found', '', 1)",
        `thread_${"f".repeat(32)}`);
      return { making, opening, erasing, recorded };
    });
    expect(out.recorded).toBe(ERASING);
    // Doors refuse, the lanes run nothing, and the owner's rows are all still there.
    for (const call of [
      () => r.stub.agentSubmit(r.bind(null), { message: "again", client_send_id: "b" }),
      () => r.stub.collectionGet(r.bind(null), { collection: "board", row_id: "t" }),
      () => r.stub.policyRead(r.bind(null)),
      () => r.stub.fileList(r.bind(null), { dir: "/workspace", limit: 10 }),
      () => r.stub.grantList(r.bind(null)),
    ]) {
      expect((await failure(call)).message).toBe(ERASING);
    }
    // Every door of the object's, before anything it was asked is read.
    const doors = Object.getOwnPropertyNames(OwnerDO.prototype)
      .filter((door) => door !== "constructor" && door !== "alarm");
    expect(doors.length).toBeGreaterThan(60);
    const answers = new Map<string, string>();
    for (const door of doors) {
      const call = (r.stub as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>)[
        door]!;
      answers.set(door, (await failure(() => call(r.session(), {}))).message);
    }
    expect([...answers].filter(([, answer]) => answer !== ERASING)).toEqual([]);
    await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-schedule/threads'");
    expect(await runDurableObjectAlarm(r.stub)).toBe(true);
    expect(await sqlIn(r.stub, "SELECT 1 FROM run_receipts")).toHaveLength(1);
    expect(await tables(r)).toContain("collection_rows");
    held.release();
    await r.in(async (instance) => {
      await instance.erasure.erase();
      expect(instance.erasure.erased).toBe(true);
    });
    for (const store of [...stores, out.making.store]) {
      expect(await storeState(store)).toEqual({ tables: 0, opens: DESTROYED });
    }
    expect(await tables(r)).toEqual([]);
    expect((await failure(() => r.stub.collectionGet(r.bind(null), {
      collection: "board", row_id: "t" }))).message).toBe(ERASING);
  }, 30_000);

  it("resumes after a crash at each step, by its own job", async () => {
    // Crash after the mark (the first destroy fails), then mid-way (a destroy in flight
    // as the object is evicted), then after every store is destroyed but before the wipe.
    for (const step of ["mark", "destroying", "destroyed"] as const) {
      const { r, stores } = await populated();
      const held = gate();
      await r.in(async (instance) => {
        if (step === "mark") {
          instance.probes.destroyFails = 2;
          await instance.erasure.erase().catch(() => undefined);
        } else {
          instance.probes.destroyGate = held.promise;
          void instance.erasure.erase().catch(() => undefined);
        }
      });
      await until(async () => (await sqlIn(r.stub, "SELECT 1 FROM owner_erasure")).length === 1,
                  "the mark");
      if (step === "destroyed") {
        for (const store of stores) {
          await env.PISTORE.getByName(store).destroy();
        }
        await sqlIn(r.stub, "DELETE FROM agent_threads");
      }
      expect((await failure(() => r.stub.policyRead(r.bind(null)))).message).toBe(ERASING);
      await crash(r);
      // The new instance refuses too, and its alarm resumes the erasure.
      expect((await failure(() => r.stub.policyRead(r.bind(null)))).message).toBe(ERASING);
      expect(await sqlIn(r.stub, "SELECT state FROM agent_threads WHERE state != 'dropping'"))
        .toEqual([]);
      await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-erasure/erase'");
      await runDurableObjectAlarm(r.stub);
      await until(async () => (await tables(r)).length === 0, `the wipe after ${step}`);
      for (const store of stores) {
        expect(await storeState(store)).toEqual({ tables: 0, opens: DESTROYED });
      }
      held.release();
    }
  }, 60_000);
});
