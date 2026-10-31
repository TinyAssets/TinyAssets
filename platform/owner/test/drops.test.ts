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

import { NO_THREAD, settledIn, WAIT_ENDED } from "../src/agent";
import type { Stamped } from "../src/authority";
import { ERASING } from "../src/erasure";
import { WriteGate } from "../src/gate";
import { Notices } from "../src/notices";
import { clock, type Origin } from "../src/ledger";
import {
  type Binding, NO_OPERATION, type Operation, OwnerDO, workRunner,
} from "../src/owner-do";
import {
  DEFAULT_POLICY_JSON, RUN_DEFAULTS, RUN_SETTINGS, runPolicy, validate,
} from "../src/policy";
import { holderId } from "../src/remote-storage";
import { namedByOpenWork } from "../src/scheduled";
import { HOST, PARKED, RUN_SETTLED, wakeJob } from "../src/thread-host";
import { DESTROYED } from "../src/thread-store";
import {
  DROP_NOTICE_AFTER, DROP_RETRY_S, DROP_STUCK, foldedUsage, RECEIPT_TEXT_MAX, retainThreads,
  RUNS_LIVE, RUNS_LIVE_CLIENTS, RUNS_LIVE_SHARE, RUN_POLICY, type RunPolicy, type Thread,
  THREAD_LIMITS, THREAD_RETENTION, Threads,
  USAGE_OTHER, USAGE_SPACE_MAX, USAGE_TOTAL_KEYS, type UsageTotals, utf8Prefix,
} from "../src/threads";
import { until } from "./gateway-support";
import { DOT, MCP, MUSE, signedIn } from "./ledger-support";
import { failure, sqlIn, storeSql } from "./support";
import { type FauxOwnerDO, Reminder, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** Retention at a second: a conversation idle, and a run settled, a second ago are taken. */
const QUICK = Object.freeze({ idleS: 1, perPass: 32 });
const QUICK_RUNS: RunPolicy = Object.freeze({ ...RUN_POLICY, keepRunsS: 1 });
/** An empty store's `databaseSize` in workerd: what a bare `deleteAll` leaves. */
const EMPTY_STORE = 4096;

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
    retain: (perPass = 32, runs: RunPolicy = QUICK_RUNS) => inside(async (instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, THREAD_LIMITS, { ...QUICK, perPass },
                                  namedByOpenWork, () => runs);
      return retainThreads(threads, clock, gated.write, (thread) => instance.agent.drop(thread),
                           noticed(state));
    }),
  };
}

type Rig = Awaited<ReturnType<typeof rig>>;

/** The owner notices a lane pass keeps, in its own unit (the test's own gate's). */
function noticed(state: DurableObjectState) {
  return (type: string, detail: string) => new Notices(state.storage).add(type, detail,
                                                                          clock());
}

/**
 * How a store stands: its pi tables, whether it is as empty as a store never made, and
 * whether a fresh holder could open it (the instance that destroyed it refuses).
 */
async function storeState(store: string):
    Promise<{ tables: number; empty: boolean; opens: string }> {
  const tables = (await storeSql(store, "SELECT name FROM sqlite_master WHERE name LIKE " +
                                        "'pi\\_%' ESCAPE '\\'")).length;
  const empty = await env.PISTORE.getByName(store).size() === EMPTY_STORE;
  const opens = await env.PISTORE.getByName(store).open(holderId())
    .then(() => "opened", (error: Error) => (error.message.includes(DESTROYED) ? DESTROYED
      : error.message));
  return { tables, empty, opens };
}

const WIPED = Object.freeze({ tables: 0, empty: true, opens: DESTROYED });

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
    expect(await storeState(thread!.store)).toEqual(WIPED);
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
      expect(await storeState(stores.get(thread)!)).toEqual(WIPED);
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
    expect(await storeState(thread!.store)).toEqual(WIPED);
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
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         name: "", expected_generation: grant.generation,
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "read_files" }] });
    expect(await storeState(thread.store)).toEqual(WIPED);
    expect(await keyed(r, thread.thread_id)).toEqual(GONE);
    // Nothing made under the grant is seen under the next: no receipt is kept.
    expect(await sqlIn(r.stub, "SELECT status, reason FROM run_receipts WHERE " +
                               "thread_id = ?", thread.thread_id)).toEqual([]);
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
    // It settled once, its receipt kept in the same unit, by when it settled.
    expect(thread!.settled_at).not.toBeNull();
    expect(await sqlIn(r.stub, "SELECT settled_at FROM run_receipts WHERE thread_id = ?",
                       thread!.thread_id)).toEqual([{ settled_at: thread!.settled_at }]);
    // Its status is its receipt's, its Session never opened for it: reads move nothing.
    await r.in((instance) => instance.agent.host.close(thread!.thread_id));
    const opens = () => r.in((instance) => instance.probes.storeCalls["open"] ?? 0);
    const before = await opens();
    for (let i = 0; i < 3; i++) {
      // Its whole answer, read from its store while it is there: no Session, no claim.
      expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
        .toEqual({ operation_id: run.operation_id, thread_id: thread!.thread_id,
                   status: "done", text: `echo: ${long}` });
    }
    expect(await opens()).toBe(before);
    expect(await r.in((instance) => instance.agent.host.openThreads())).toEqual([]);
    expect((await r.threads())[0]).toEqual(thread);
    // At policy's own keep_runs, the lane is due a week after it settled.
    expect(await r.in((instance) => instance.parts.threads.nextDue(clock())))
      .toBeCloseTo(thread!.settled_at! + RUN_POLICY.keepRunsS, 0);
    await sleep(1100);
    // A conversation's idle age is not a run's keep_runs: at a week, it is kept.
    expect(await r.retain(32, RUN_POLICY)).toBe(0);
    expect(await r.retain()).toBe(1);
    expect(await storeState(thread!.store)).toEqual(WIPED);
    expect(await r.threads()).toEqual([]);
    const answered = await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id });
    // Once its store is wiped, its receipt's first 4 KiB, marked as cut.
    expect(answered).toEqual({ operation_id: run.operation_id, thread_id: thread!.thread_id,
                               status: "done", truncated: true,
                               text: utf8Prefix(`echo: ${long}`, RECEIPT_TEXT_MAX) });
    expect(new TextEncoder().encode(answered.text!).length).toBeLessThanOrEqual(
      RECEIPT_TEXT_MAX);
    expect(answered.text!.length).toBeGreaterThan(RECEIPT_TEXT_MAX / 2 - 10);
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
    // Its input answered, its background work running: it has not settled.
    expect(kept!.settled_at).toBeNull();
    expect(await sqlIn(r.stub, "SELECT 1 FROM run_receipts")).toEqual([]);
    expect(await piTables(kept!.store)).toBeGreaterThan(0);
    await sleep(1100);
    held.release();
    await until(async () => (await sqlIn(r.stub, "SELECT 1 FROM agent_live")).length === 0,
                "the background work to settle");
    await r.in((instance) => instance.agent.host.close(kept!.thread_id));
    // keep_runs runs from when it settled, just now, not from its last mark.
    expect(await r.retain()).toBe(0);
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await storeState(kept!.store)).toEqual(WIPED);
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toMatchObject({ status: "done" });
  }, 30_000);

  it("refuses none of 1,000 launches in a day, past the owner's conversation bound; runs " +
     "refuse loudly at their own", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage);
      // The owner one conversation short of its conversations' bound.
      await gated.write(() => {
        for (let i = 0; i < THREAD_LIMITS.owner - 1; i++) {
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
      let launched = 0;
      for (let i = 0; i < 1_000; i++) {
        await gated.write(() => threads.record("main", null, clock(), "run"));
        launched++;
      }
      // The runs count in none of the owner's conversations: its last one is still made.
      const conversation = [
        await gated.write(() => refused(() => threads.record("another", null, clock(),
                                                             "converse"))),
        await gated.write(() => refused(() => threads.record("one-more", null, clock(),
                                                             "converse")))];
      const runs = new Threads(gated.storage, { ...THREAD_LIMITS, runs: 1_000 });
      const atRuns = await gated.write(() => refused(() => runs.record("main", null, clock())));
      return { conversation, launched, atRuns };
    });
    expect(out).toEqual({ conversation: ["made", "the owner's threads are at their limit"],
                          launched: 1_000, atRuns: "the agent's runs are at their limit" });
    // At 1,000 a day, the policy's default keep_runs fits the runs kept, and its live runs.
    expect(1_000 * RUN_DEFAULTS.keep_runs).toBeLessThan(THREAD_LIMITS.runs);
    expect(1_000).toBeLessThan(RUN_DEFAULTS.runs_live);
  }, 60_000);

  it("lets receipts go at keep_run_receipts, and keeps the rest", async () => {
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
                                  () => RUN_POLICY);
      await retainThreads(threads, clock, gated.write, (thread) => instance.agent.drop(thread),
                          noticed(state));
      const left = state.storage.sql.exec<{ thread_id: string }>(
        "SELECT thread_id FROM run_receipts").toArray().map((row) => row.thread_id);
      return { due, left };
    });
    expect(out.due).toBeLessThanOrEqual(clock() + 1);
    expect(out.left).toEqual([`thread_${"2".repeat(32)}`]);
  }, 30_000);

  it("takes a run never handed an input keep_runs after it was made, answered by its work",
     async () => {
    const r = await rig();
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "later",
                                                      at: clock() + 86_400 });
    await r.stub.agentCancel(r.bind(null), { operation_id: run.operation_id });
    const [thread] = await r.threads();
    expect(thread!.settled_at).toBeNull();
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await r.threads()).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT request, status, reason FROM run_receipts"))
      .toEqual([{ request: null, status: "unanswered", reason: "not_found" }]);
    // pi never saw it, so its work's own row says how it stands.
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toMatchObject({ status: "unanswered", reason: "cancelled" });
  }, 30_000);

  it("bounds every thread's kept usage together, past each one's own bound", async () => {
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      const made = await instance.agent.thread("main", null);
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage);
      const space = () => state.storage.sql.exec<{ bytes: number }>(
        "SELECT bytes FROM agent_usage_space").one().bytes;
      state.storage.sql.exec("UPDATE agent_usage_space SET bytes = ?", USAGE_SPACE_MAX - 10);
      const keep = (usage: string) => gated.write(() => threads.keepUsage(made.thread_id, usage))
        .then(() => "kept", (error: Error) => error.message);
      const kept = [await keep("x".repeat(10)), await keep("x".repeat(11)),
                    await keep("x".repeat(4))];
      const full = space();
      await gated.write(() => state.storage.sql.exec("DELETE FROM agent_usage"));
      return { kept, full, after: space() };
    });
    expect(out).toEqual({ kept: ["kept", "the threads' kept usage is past its bound", "kept"],
                          full: USAGE_SPACE_MAX - 6, after: USAGE_SPACE_MAX - 10 });
  }, 30_000);

  it("takes keep_runs and keep_run_receipts from owner policy, within their bounds", () => {
    const base = JSON.parse(DEFAULT_POLICY_JSON) as Record<string, unknown>;
    expect(runPolicy(validate(base))).toEqual(RUN_POLICY);
    expect(runPolicy(validate({ ...base, runs: { keep_runs: 30, keep_run_receipts: 365,
                                                  client_share: 10, owner_reserve: 50, runs_live: 10_000 } })))
      .toEqual({ keepRunsS: 30 * 86_400, keepReceiptsS: 365 * 86_400, clientShare: 10,
                 ownerReserve: 50, runsLive: 10_000 });
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
            refused({ other: 1 }), refused([]), refused({ client_share: 101 }),
            refused({ owner_reserve: -1 })])
      .toEqual(Array(8).fill("invalid runs policy"));
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
      expect(await storeState(store)).toEqual(WIPED);
    }
    expect(await tables(r)).toEqual([]);
    expect((await failure(() => r.stub.collectionGet(r.bind(null), {
      collection: "board", row_id: "t" }))).message).toBe(ERASING);
  }, 30_000);

  it("resumes after a crash at each step, at the object's next start", async () => {
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
      // The new instance's start resumes it from the step it recorded, and refuses meanwhile.
      expect((await failure(() => r.stub.policyRead(r.bind(null)))).message).toBe(ERASING);
      await until(async () => (await tables(r)).length === 0, `the wipe after ${step}`);
      for (const store of stores) {
        expect(await storeState(store)).toEqual(WIPED);
      }
      held.release();
    }
  }, 60_000);
});

describe("S4-S5 review round 1", () => {
  const stampOf = (r: Rig, origin: Origin, generation = 1, agent = "main") =>
    ({ client: origin.client, generation, authenticated_at: r.at, universe: null, agent });

  it("resumes an erasure's wipe at the object's next start: after a crash past the alarm's " +
     "disabling, and after a wipe that failed", async () => {
    for (const how of ["hangs", "fails"] as const) {
      const r = await rig();
      await r.stub.collectionWrite(r.bind(null), { collection: "board", row_id: "t", body: "1",
                                                   expected_etag: "", private: false });
      await r.in(async (instance) => instance.agent.thread("main", null));
      const out = await r.in(async (instance, state) => {
        Object.defineProperty(state.storage, "deleteAll", {
          configurable: true,
          value: how === "fails" ? () => Promise.reject(new Error("the wipe failed (the test's)"))
            : () => new Promise(() => undefined),
        });
        const erasing = instance.erasure.erase().then(() => "wiped",
                                                     (error: Error) => error.message);
        const answer = how === "fails" ? await erasing : "pending";
        await until(async () => state.storage.sql.exec<{ step: number }>(
          "SELECT step FROM owner_erasure").toArray()[0]?.step === 2
          && (await state.storage.getAlarm()) === null, "the alarm's end");
        return answer;
      });
      expect(out).toBe(how === "fails" ? "the wipe failed (the test's)" : "pending");
      // Every store destroyed and recorded so; the owner's data still there, no alarm left.
      expect(await sqlIn(r.stub, "SELECT step FROM owner_erasure")).toEqual([{ step: 2 }]);
      expect(await sqlIn(r.stub, "SELECT 1 FROM agent_threads")).toEqual([]);
      expect(await sqlIn(r.stub, "SELECT 1 FROM collection_rows")).toHaveLength(1);
      await crash(r);
      await until(async () => (await r.in((_instance, state) => state.storage.sql.exec<
        { name: string }>("SELECT name FROM sqlite_master WHERE type = 'table'").toArray()
        .filter((row) => !row.name.startsWith("_cf")).length)) === 0, `the wipe that ${how}`);
      expect((await failure(() => r.stub.policyRead(r.bind(null)))).message).toBe(ERASING);
    }
  }, 60_000);

  it("goes on past a store that refuses its destroy, and fails the step only once every " +
     "other was tried", async () => {
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      for (const agent of ["agent-a", "agent-b", "agent-c"]) {
        await instance.agent.thread(agent, null);
      }
      instance.probes.destroyFails = 1;
      const answer = await instance.erasure.erase().then(() => "wiped",
                                                         (error: Error) => error.message);
      return { answer, left: state.storage.sql.exec("SELECT 1 FROM agent_threads").toArray()
        .length, step: state.storage.sql.exec<{ step: number }>(
          "SELECT step FROM owner_erasure").one().step };
    });
    expect(out).toEqual({ answer: "some of the owner's thread stores could not be destroyed yet",
                          left: 1, step: 1 });
    await r.in((instance) => instance.erasure.erase());
    expect(await r.in((instance) => instance.erasure.erased)).toBe(true);
  }, 30_000);

  it("ends every pending wait as the owner's erasure begins", async () => {
    const r = await rig();
    const held = gate();
    await r.in((instance) => { instance.probes.holdGate = held.promise; });
    const waiting = failure(() => r.stub.agentSubmit(r.bind(null), {
      message: "hold please", client_send_id: "w", wait_ms: 60_000 }));
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    await r.in((instance) => instance.erasure.erase());
    expect((await waiting).message).toBe(WAIT_ENDED);
    held.release();
  }, 30_000);

  it("gives each client a share of the runs' bound, the owner a reserve, and every run room " +
     "past the conversations' bounds", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, { ...THREAD_LIMITS, perClient: 1, runs: 8 },
                                  THREAD_RETENTION, undefined,
                                  () => ({ ...RUN_POLICY, clientShare: 50, ownerReserve: 50 }));
      const record = (stamp: Stamped | null, kind: "run" | "converse" = "run",
                      agent = "main") => gated.write(() => threads.record(
        agent, stamp, clock(), kind).kind).catch((error: Error) => error.message);
      const muse = [];
      for (let i = 0; i < 5; i++) {
        muse.push(await record(stampOf(r, MUSE)));
      }
      const conversation = await record(stampOf(r, MUSE), "converse");
      const dot = await record(stampOf(r, DOT));
      const owner = [];
      for (let i = 0; i < 5; i++) {
        owner.push(await record(null));
      }
      return { muse, conversation, dot, owner };
    });
    expect(out).toEqual({
      muse: ["run", "run", "run", "run", "this client's runs are at its share"],
      conversation: "converse",
      dot: "the outside clients' runs are at their share",
      owner: ["run", "run", "run", "run", "the agent's runs are at their limit"],
    });
  }, 30_000);

  it("keeps receipts as a history: past a scope's share its own oldest goes, and nothing " +
     "is refused", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, { ...THREAD_LIMITS, receipts: 4 },
                                  THREAD_RETENTION, undefined,
                                  () => ({ ...RUN_POLICY, clientShare: 50, ownerReserve: 25 }));
      const names = new Map<string, string>();
      const kept = () => state.storage.sql.exec<{ thread_id: string }>(
        "SELECT thread_id FROM run_receipts ORDER BY settled_at").toArray()
        .map((row) => names.get(row.thread_id));
      const seen: (string | undefined)[][] = [];
      let at = 1;
      const settled = async (name: string, stamp: Stamped | null) => {
        const made = await gated.write(() => threads.record("main", stamp, clock()));
        names.set(made.thread_id, name);
        const when = at++;
        await gated.write(() => threads.settle(made.thread_id, {
          request: "run_x", status: "done", reason: null, text: name }, when));
      };
      for (const [name, stamp] of [["r1", stampOf(r, MUSE)], ["r2", stampOf(r, MUSE)],
                                   ["r3", stampOf(r, MUSE)], ["d1", stampOf(r, DOT)],
                                   ["d2", stampOf(r, DOT)], ["o1", null], ["o2", null],
                                   ["r4", stampOf(r, MUSE)]] as const) {
        await settled(name, stamp);
        seen.push(kept());
      }
      return seen;
    });
    expect(out).toEqual([["r1"], ["r1", "r2"], ["r2", "r3"], ["r2", "r3", "d1"],
                         ["r3", "d1", "d2"], ["r3", "d1", "d2", "o1"],
                         ["r3", "d1", "d2", "o2"], ["d1", "d2", "o2", "r4"]]);
  }, 30_000);

  it("keeps a run that settled after the pass read it, by when it settled", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage);
      const run = await gated.write(() => threads.record("main", null, clock() - 10 * 86_400));
      const seen = threads.retainedAt(run);
      await gated.write(() => threads.settle(run.thread_id, {
        request: "run_x", status: "done", reason: null, text: "ok" }, clock()));
      return gated.write(() => threads.retain(run.thread_id, seen, clock()));
    });
    expect(out).toBe("touched");
  }, 30_000);

  it("lets a client's receipts go with its grant's change, so a grant made again sees none",
     async () => {
    const r = await rig();
    await r.admit(MUSE);
    const run = await r.stub.agentRun(r.bind(MUSE), { agent: "main", input: "hello" });
    expect(await r.settled(r.bind(MUSE), run.operation_id)).toMatchObject({ status: "done" });
    expect(await sqlIn(r.stub, "SELECT 1 FROM run_receipts WHERE client = ?", MUSE.client))
      .toHaveLength(1);
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect(await sqlIn(r.stub, "SELECT 1 FROM run_receipts WHERE client = ?", MUSE.client))
      .toEqual([]);
    const generation = (await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === MUSE.client)!.generation;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         scopes: [], name: "", expected_generation: generation });
    // A sign-in past the revoke and its clock margin.
    const fresh = { ...r.bind(MUSE), authenticated_at: signedIn() + 10 };
    expect((await failure(() => r.stub.agentStatus(fresh, {
      operation_id: run.operation_id }))).message).toBe(NO_OPERATION);
  }, 30_000);

  it("refuses none of a day's 1,000 launches with its receipts at their bound, as " +
     "keep_run_receipts at 365 days leaves them", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, THREAD_LIMITS, THREAD_RETENTION, undefined,
                                  () => ({ ...RUN_POLICY, keepReceiptsS: 365 * 86_400 }));
      await gated.write(() => {
        for (let i = 0; i < THREAD_LIMITS.receipts; i++) {
          state.storage.sql.exec(
            "INSERT INTO run_receipts (thread_id, client, request, status, reason, text, " +
              "settled_at) VALUES (?, NULL, NULL, 'unanswered', 'not_found', '', ?)",
            `thread_${i.toString(16).padStart(32, "0")}`, i);
        }
      });
      let launched = 0;
      for (let i = 0; i < 1_000; i++) {
        const made = await gated.write(() => threads.record("main", null, clock()));
        await gated.write(() => threads.settle(made.thread_id, {
          request: "run_x", status: "done", reason: null, text: "ok" }, clock()));
        launched++;
      }
      return { launched, receipts: state.storage.sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM run_receipts").one().n };
    });
    expect(out).toEqual({ launched: 1_000, receipts: THREAD_LIMITS.receipts });
  }, 120_000);

  it("finishes a run's drop after a crash at its destroy, its receipt answering throughout",
     async () => {
    const r = await rig();
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "hello" });
    const done = await r.settled(r.bind(null), run.operation_id);
    const [thread] = await r.threads();
    const receipt = await sqlIn(r.stub, "SELECT * FROM run_receipts");
    expect(receipt).toHaveLength(1);
    await r.in((instance) => instance.agent.host.close(thread!.thread_id));
    // A short answer is its receipt's whole: its status reads no store.
    const reads = () => r.in((instance) => instance.probes.storeCalls["answer"] ?? 0);
    const before = await reads();
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toEqual(done);
    expect(await reads()).toBe(before);
    await r.in((instance) => { instance.probes.destroyFails = 1; });
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect((await r.threads()).map((row) => row.state)).toEqual(["dropping"]);
    // Partway through its drop, it answers as it did, from its receipt.
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toEqual(done);
    await sqlIn(r.stub, "UPDATE agent_threads SET drop_retry = 1");
    await r.retain();
    expect(await r.threads()).toEqual([]);
    expect(await storeState(thread!.store)).toEqual(WIPED);
    expect(await sqlIn(r.stub, "SELECT * FROM run_receipts")).toEqual(receipt);
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toEqual(done);
  }, 30_000);

  it("rejects the grant change's door when a thread's work cannot be ended; the change " +
     "stands and the lane finishes the drop", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      const made = await instance.agent.thread("main", stampOf(r, MUSE));
      await instance.agent.host.pi(made.thread_id);
      const host = instance.agent.host;
      Object.defineProperty(host, "drop", { configurable: true,
        value: () => Promise.reject(new Error("its work could not be ended (the test's)")) });
      const answer = await instance.grantRevoke(r.bind(null), { client: MUSE.client })
        .then(() => "returned", (error: Error) => error.message);
      delete (host as unknown as Record<string, unknown>)["drop"];
      return answer;
    });
    expect(out).toBe("its work could not be ended (the test's)");
    expect((await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === MUSE.client)!.mode).toBe("revoked");
    await r.retain();
    expect(await r.threads()).toEqual([]);
  }, 30_000);

  it("cuts off a Session that will not close, wipes its store, and tells the owner of a " +
     "drop that keeps failing", async () => {
    const r = await rig();
    await r.admit(MUSE);
    await r.in((instance) => { instance.probes.holdGate = new Promise(() => undefined); });
    await r.stub.agentRun(r.bind(MUSE), { agent: "main", input: "hold please" });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    const [thread] = await r.threads();
    // Its Session never closes: the drop waits for it no longer than its bound.
    await r.in((instance) => {
      Object.defineProperty(instance.agent.host, "drop", { configurable: true,
                                                         value: () => new Promise(() => {}) });
    });
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    await until(async () => (await keyed(r, thread!.thread_id)).row === 0, "the drop", 20_000);
    expect(await storeState(thread!.store)).toEqual(WIPED);
    await r.in((instance) => {
      delete (instance.agent.host as unknown as Record<string, unknown>)["drop"];
    });
    // A drop whose store keeps refusing: retried by its row, the owner told once.
    const other = await r.in(async (instance, state) => {
      const made = await instance.agent.thread("helper", null, { kind: "converse" });
      state.storage.sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ?",
                             made.thread_id);
      instance.probes.destroyFails = DROP_NOTICE_AFTER + 1;
      return made.thread_id;
    });
    const stuck = async () => (await sqlIn(r.stub, "SELECT 1 FROM owner_notices WHERE " +
                                                   "type = ?", DROP_STUCK)).length;
    for (let i = 0; i < DROP_NOTICE_AFTER + 1; i++) {
      expect(await stuck()).toBe(i < DROP_NOTICE_AFTER ? 0 : 1);
      await sqlIn(r.stub, "UPDATE agent_threads SET drop_retry = 1");
      await r.retain();
    }
    expect(await stuck()).toBe(1);
    expect(await sqlIn(r.stub, "SELECT drop_failures FROM agent_threads WHERE thread_id = ?",
                       other)).toEqual([{ drop_failures: DROP_NOTICE_AFTER + 1 }]);
    await sqlIn(r.stub, "UPDATE agent_threads SET drop_retry = 1");
    await r.retain();
    expect(await r.threads()).toEqual([]);
  }, 60_000);

  it("keeps a revoked client's dropping threads on its bound and grant row until they are " +
     "forgotten, so a grant made again at once makes none past it", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const held = gate();
    await r.in(async (instance) => {
      for (let i = 0; i < THREAD_LIMITS.perClient; i++) {
        await instance.parts.write(() => instance.parts.threads.record(
          `agent-${i}`, stampOf(r, MUSE, 1, `agent-${i}`), clock(), "converse"));
      }
      instance.probes.destroyGate = held.promise;
    });
    const grant = async () => (await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === MUSE.client)!;
    expect((await grant()).held).toBe(THREAD_LIMITS.perClient);
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect((await grant()).held).toBe(THREAD_LIMITS.perClient);
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         scopes: [], name: "",
                                         expected_generation: (await grant()).generation });
    const generation = (await grant()).generation;
    const room = () => r.in((instance) => {
      try {
        instance.parts.threads.room(stampOf(r, MUSE, generation, "fresh"), "converse");
        return "room";
      } catch (error) {
        return (error as Error).message;
      }
    });
    expect(await room()).toBe("this client's threads are at their limit");
    held.release();
    await until(async () => (await r.threads()).length === 0, "the drops to finish", 20_000);
    expect(await room()).toBe("room");
    expect((await grant()).held).toBe(0);
  }, 60_000);

  it("adds a dropped thread's usage into the owner's total, which the usage view reads",
     async () => {
    const r = await rig();
    await r.stub.agentSubmit(r.bind(null), { message: "hello", client_send_id: "u" });
    const [thread] = await r.threads();
    await r.in((instance) => instance.agent.host.close(thread!.thread_id));
    const kept = JSON.parse((await sqlIn(r.stub, "SELECT usage FROM agent_usage"))[0]![
      "usage"] as string) as UsageTotals;
    const before = await r.stub.usageMetrics(r.session());
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await sqlIn(r.stub, "SELECT 1 FROM agent_usage")).toEqual([]);
    expect(JSON.parse((await sqlIn(r.stub, "SELECT usage FROM agent_usage_total"))[0]![
      "usage"] as string)).toEqual(foldedUsage({ models: {}, tools: {} }, kept));
    const after = await r.stub.usageMetrics(r.session());
    expect([after.models, after.tools]).toEqual([before.models, before.tools]);
  }, 30_000);

  it("folds the usage total's keys past its bound into one, its sums exact", () => {
    const usage = (n: number, base: number) => ({ models: Object.fromEntries(
      Array.from({ length: n }, (_, i) => [`p/m${base + i}`, { input: 1, cost: { total: 2 } }])),
                                                tools: {} });
    const total = foldedUsage(foldedUsage({ models: {}, tools: {} }, usage(40, 0)),
                              usage(20, 30));
    expect(Object.keys(total.models)).toHaveLength(USAGE_TOTAL_KEYS + 1);
    expect(total.models[USAGE_OTHER]).toEqual({ input: 2, cost: { total: 4 } });
    const sum = Object.values(total.models).reduce<number>((n, counts) =>
      n + (counts as { input: number }).input, 0);
    expect(sum).toBe(60);
    expect(total.models["p/m30"]).toEqual({ input: 2, cost: { total: 4 } });
  });
});

describe("S4-S5 review round 2", () => {
  it("keeps a run whose input is answered while a task of it sleeps: not settled, not wiped",
     async () => {
    const r = await rig();
    const thread = await r.in(async (instance) => {
      const made = await instance.agent.thread("main", null);
      const pi = await instance.agent.host.pi(made.thread_id);
      const root = (await pi.conversation(1 as never, BG))!;
      await root.commit((tx) => tx.createTask(Reminder, { until: Date.now() + 3_600_000 },
                                              { ownership: { kind: "conversation" },
                                                background: true }), BG);
      pi.resume();
      await instance.agent.submit(made, "hello", { operationId: `run_${"1".repeat(32)}` });
      expect((await instance.agent.host.settled(made.thread_id, `run_${"1".repeat(32)}`))
        .status).toBe("done");
      return made;
    });
    await until(async () => (await sqlIn(r.stub, "SELECT wake_at FROM agent_live WHERE " +
                                                 "thread_id = ?", thread.thread_id))[0]?.[
      "wake_at"] !== undefined && (await sqlIn(r.stub, "SELECT active FROM agent_live"))[0]?.[
      "active"] === 0, "the sleeper's wake");
    await r.in((instance) => instance.agent.host.close(thread.thread_id));
    await sleep(1100);
    expect(await r.retain()).toBe(0);
    const [row] = await r.threads();
    expect([row!.state, row!.settled_at]).toEqual(["ready", null]);
    expect(await sqlIn(r.stub, "SELECT 1 FROM run_receipts")).toEqual([]);
    expect(await piTables(thread.store)).toBeGreaterThan(0);
  }, 30_000);

  it("settles a run whose Session reports idle with its input still placed only once the " +
     "input settles: a parked run", async () => {
    const r = await rig();
    const name = r.owner;
    const reply = (messages: Message[]) => {
      const last = messages.at(-1);
      if (last?.role === "toolResult") {
        return says("tool done");
      }
      return JSON.stringify(last).includes("look")
        ? fauxAssistantMessage([fauxToolCall("read", { path: "a.md" })],
                               { stopReason: "toolUse" })
        : says("noted");
    };
    const inside = <R>(act: (i: FauxOwnerDO) => R | Promise<R>) =>
      runInDurableObject(r.stub, async (i: FauxOwnerDO) => {
        i.script = Array.from({ length: 200 }, () => reply);
        i.probes.readGate = new Promise(() => undefined);  // the read never ends
        return act(i);
      });
    await r.stub.fileWrite({ owner: name, caller: name, origin: null },
                           { path: "/workspace/a.md", expected_etag: null,
                             content: new TextEncoder().encode("alpha") });
    const request = `run_${"2".repeat(32)}`;
    const thread = await inside(async (i) => {
      const made = await i.agent.thread("main", null);
      await i.agent.submit(made, "look", { operationId: request });
      await until(() => i.probes.reads === 1, "the read to start");
      return made;
    });
    const parked = async () => (await inside((i) => i.parts.notices.list()))
      .some((notice) => notice.type === PARKED);
    for (let n = 0; n < 8 && !await parked(); n++) {
      await crash(r);
      await inside(() => undefined);
      for (let k = 0, got = 0; k < 20 && got === 0; k++) {
        await sleep(400);
        await runDurableObjectAlarm(r.stub);
        await sleep(200);
        got = await inside((i) => i.probes.reads);
      }
    }
    await until(async () => (await sqlIn(r.stub, "SELECT 1 FROM run_receipts")).length === 1,
                "the parked run to settle", 20_000);
    const stored = await storeSql(thread.store, "SELECT json_extract(record, '$.reason') AS " +
                                                "reason FROM pi_submissions WHERE " +
                                                "json_extract(record, '$.requestId') = ?",
                                  request);
    // Its receipt is how its input settled, never a guess from an idle Session.
    expect(await sqlIn(r.stub, "SELECT request, status, reason FROM run_receipts"))
      .toEqual([{ request, status: "unanswered", reason: stored[0]!["reason"] }]);
  }, 120_000);

  it("rewrites a stale \"running\" row from the Session when its job finds nothing to do, " +
     "and settles the run", async () => {
    const r = await rig();
    const held = gate();
    await r.in((instance) => { instance.probes.holdGate = held.promise; });
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "hold please" });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    await r.in((instance) => { instance.probes.scanGate = new Promise(() => undefined); });
    held.release();
    const [thread] = await r.threads();
    const store = thread!.store;
    await until(async () => (await storeSql(store, "SELECT status FROM pi_submissions"))[0]?.[
      "status"] === "done", "the run's input to be answered");
    // Its settling's read is held, so its row still says it runs, and its job, which found
    // nothing to do but could not keep that, hands its step back to try again.
    expect(await sqlIn(r.stub, "SELECT active FROM agent_live")).toEqual([{ active: 1 }]);
    await until(async () => (await sqlIn(r.stub, "SELECT running FROM cf_agents_jobs WHERE " +
                                                 "capability = ?", HOST))[0]?.["running"] === 0,
                "the job to hand the step back");
    expect(await sqlIn(r.stub, "SELECT active FROM agent_live")).toEqual([{ active: 1 }]);
    // With the store answering again, the job's next step rewrites the row from the
    // Session, which settles the run: the Session's own report is long past.
    await r.in((instance) => { instance.probes.scanGate = null; });
    await until(async () => (await sqlIn(r.stub, "SELECT 1 FROM agent_live")).length === 0
                            && (await r.threads())[0]?.settled_at !== null, "the row's rewrite");
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toMatchObject({ status: "done", text: "tools answered" });
  }, 30_000);

  it("never settles a run reopened while its background work runs again", async () => {
    const r = await rig();
    await r.in((instance) => { instance.probes.backgroundGate = new Promise(() => undefined); });
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "spawn hold" });
    expect(await r.settled(r.bind(null), run.operation_id)).toMatchObject({ status: "done" });
    await until(() => r.in((instance) => instance.probes.backgroundRuns === 1),
                "the background work to wait");
    await crash(r);
    // Reopened by its job, its background work waits again: its input is answered, but the
    // run is live, so it has not settled.
    await r.in((instance) => { instance.probes.backgroundGate = new Promise(() => undefined); });
    await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE capability = ?", HOST);
    await runDurableObjectAlarm(r.stub);
    await until(() => r.in((instance) => instance.probes.backgroundRuns === 1),
                "the background work to wait again");
    await sleep(300);
    const [row] = await r.threads();
    expect(row!.settled_at).toBeNull();
    expect(await sqlIn(r.stub, "SELECT active FROM agent_live")).toEqual([{ active: 1 }]);
    expect(await sqlIn(r.stub, "SELECT 1 FROM run_receipts")).toEqual([]);
  }, 30_000);

  it("reads a run as settled only from its input: one placed, or none, is not", async () => {
    const storage = (input: unknown) => ({
      scanSubmissions: async () => ({ items: input === undefined ? [] : [input] }),
      entry: async () => undefined,
    }) as unknown as Parameters<typeof settledIn>[0];
    for (const status of ["queued", "placed"]) {
      expect(await settledIn(storage({ type: "input", status, requestId: "run_x" })))
        .toBeNull();
    }
    expect(await settledIn(storage(undefined))).toBeNull();
    expect(await settledIn(storage({ type: "input", status: "unanswered", reason: "aborted",
                                     requestId: "run_x" })))
      .toEqual({ request: "run_x", status: "unanswered", reason: "aborted", text: "" });
  });

  it("keeps a receipt naming the input of a run whose settling was never seen", async () => {
    const r = await rig();
    const held = gate();
    await r.in((instance) => { instance.probes.holdGate = held.promise; });
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "hold please" });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    // No job and no live row are left to see it settle: a crash with both lost.
    await r.in((_instance, state) => {
      state.storage.sql.exec("DELETE FROM cf_agents_jobs WHERE capability = ?", HOST);
      state.storage.sql.exec("DELETE FROM agent_live");
    });
    await crash(r);
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await sqlIn(r.stub, "SELECT request, status, reason FROM run_receipts"))
      .toEqual([{ request: run.operation_id.split(".")[1], status: "unanswered",
                  reason: "dropped" }]);
    expect(await r.stub.agentStatus(r.bind(null), { operation_id: run.operation_id }))
      .toMatchObject({ status: "unanswered", reason: "dropped" });
    held.release();
  }, 30_000);

  it("keeps a cut-off Session's usage in the owner's total", async () => {
    const r = await rig();
    await r.admit(MUSE);
    await r.in((instance) => { instance.probes.holdGate = new Promise(() => undefined); });
    await r.stub.agentRun(r.bind(MUSE), { agent: "main", input: "hold please" });
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    const [thread] = await r.threads();
    await r.in((instance) => {
      const host = instance.agent.host;
      Object.defineProperty(host, "drop", { configurable: true,
        value: (id: string, act: (opened: unknown) => Promise<void>) => {
          void act(host.peek(id)).catch(() => undefined);
          return new Promise(() => {});  // its Session never closes
        } });
    });
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    await until(async () => (await keyed(r, thread!.thread_id)).row === 0, "the drop", 20_000);
    const total = JSON.parse((await sqlIn(r.stub, "SELECT usage FROM agent_usage_total"))[0]![
      "usage"] as string) as UsageTotals;
    expect(Object.keys(total.models).length).toBeGreaterThan(0);
    await r.in((instance) => {
      delete (instance.agent.host as unknown as Record<string, unknown>)["drop"];
    });
  }, 30_000);

  it("resumes a failed erasure from a refused door, at most once a retry interval",
     async () => {
    const r = await rig();
    await r.in(async (instance) => {
      await instance.agent.thread("main", null);
      instance.probes.destroyFails = 1;
      await instance.erasure.erase().catch(() => undefined);
    });
    expect((await failure(() => r.stub.policyRead(r.bind(null)))).message).toBe(ERASING);
    await sleep(200);
    expect(await r.in((instance) => instance.erasure.erased)).toBe(false);  // within its wait
    await sleep(500);
    expect((await failure(() => r.stub.policyRead(r.bind(null)))).message).toBe(ERASING);
    await until(() => r.in((instance) => instance.erasure.erased), "the door's resume");
  }, 30_000);
});

describe("live and settled runs", () => {
  const stampOf = (r: Rig, origin: Origin, agent = "main") =>
    ({ client: origin.client, generation: 1, authenticated_at: r.at, universe: null, agent });

  it("refuses a new input to a settled run at admission, so it holds no turn", async () => {
    const r = await rig();
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "hello" });
    expect(await r.settled(r.bind(null), run.operation_id)).toMatchObject({ status: "done" });
    await until(async () => (await r.threads())[0]?.settled_at !== null, "the run to settle");
    const [thread] = await r.threads();
    const out = await r.in(async (instance) => {
      const again = await instance.agent.submit(thread!, "again", {
        operationId: `run_${"3".repeat(32)}` }).then(() => "accepted",
                                                     (error: Error) => error.message);
      // Its own input sent again is pi's duplicate: accepted, nothing new.
      const resend = await instance.agent.submit(thread!, "hello", {
        operationId: run.operation_id.split(".")[1]! }).then((done) => done.accepted,
                                                             (error: Error) => error.message);
      return { again, resend };
    });
    expect(out).toEqual({ again: RUN_SETTLED, resend: false });
    // Scheduled work naming it ends at once, never retried, and nothing reaches its store.
    const work = await r.in((instance, state) => workRunner(
      new Threads(state.storage), instance.agent, instance.parts.authority).handOff({
      work_id: "work_late", claim_id: "claim_x", kind: "turn",
      payload: { thread: thread!.thread_id, input: "later" }, stamp: null,
    }, async () => {}));
    expect(work).toEqual({ state: "failed", reason: "run_settled" });
    // A late commit of its Session lets an owner it held go, and keeps none anew.
    const owners = await r.in(async (instance, state) => {
      state.storage.sql.exec("INSERT INTO model_turn_owners (thread, owner, turn) " +
                               "VALUES (?, 's:1', 'turn_old')", thread!.thread_id);
      await instance.parts.turns.owned(thread!.thread_id, [
        { owner: "s:1", turn: null }, { owner: "s:2", turn: "turn_new" }]);
      return state.storage.sql.exec("SELECT owner FROM model_turn_owners WHERE thread = ? " +
                                      "UNION ALL SELECT turn FROM model_turns WHERE thread = ?",
                                    thread!.thread_id, thread!.thread_id).toArray();
    });
    expect(owners).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT 1 FROM model_turn_owners WHERE thread = ?",
                       thread!.thread_id)).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT 1 FROM model_turns WHERE thread = ?",
                       thread!.thread_id)).toEqual([]);
  }, 30_000);

  it("adds a run's usage into the owner's total as it settles, once, the usage view the " +
     "same before and after", async () => {
    const r = await rig();
    const run = await r.stub.agentRun(r.bind(null), { agent: "main", input: "hello" });
    expect(await r.settled(r.bind(null), run.operation_id)).toMatchObject({ status: "done" });
    await until(async () => (await r.threads())[0]?.settled_at !== null, "the run to settle");
    const [thread] = await r.threads();
    const view = async () => {
      const { models, tools } = await r.stub.usageMetrics(r.session());
      return { models, tools };
    };
    const settled = await view();
    expect(Object.keys(settled.models).length).toBeGreaterThan(0);
    const total = JSON.parse((await sqlIn(r.stub, "SELECT usage FROM agent_usage_total"))[0]![
      "usage"] as string) as UsageTotals;
    expect([total.models, total.tools]).toEqual([settled.models, settled.tools]);
    // Its Session closing keeps no row of its own, and its drop adds nothing more.
    await r.in((instance) => instance.agent.host.close(thread!.thread_id));
    expect(await sqlIn(r.stub, "SELECT 1 FROM agent_usage")).toEqual([]);
    expect(await view()).toEqual(settled);
    await sleep(1100);
    expect(await r.retain()).toBe(1);
    expect(await view()).toEqual(settled);
  }, 30_000);

  it("bounds the live runs, a sleeping one among them, with each client's share and the " +
     "owner's reserve", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, THREAD_LIMITS, THREAD_RETENTION, undefined,
                                  () => ({ ...RUN_POLICY, runsLive: 4, clientShare: 50,
                                           ownerReserve: 50 }));
      const record = (stamp: Stamped | null) => gated.write(() => threads.record(
        "main", stamp, clock()).thread_id).catch((error: Error) => error.message);
      const muse = [await record(stampOf(r, MUSE)), await record(stampOf(r, MUSE)),
                    await record(stampOf(r, MUSE))];
      const dot = await record(stampOf(r, DOT));
      const owner = [await record(null), await record(null), await record(null)];
      // One of the client's settles: it is live no longer, and the client may launch again.
      await gated.write(() => threads.settle(muse[0]!, { request: "run_x", status: "done",
                                                        reason: null, text: "ok" }, clock()));
      const again = await record(stampOf(r, MUSE));
      const made = (one: string) => (one.startsWith("thread_") ? "thread_" : one);
      return { muse: muse.map(made), dot, owner: owner.map(made), again: made(again) };
    });
    expect(out).toEqual({
      muse: ["thread_", "thread_", RUNS_LIVE_SHARE], dot: RUNS_LIVE_CLIENTS,
      owner: ["thread_", "thread_", RUNS_LIVE], again: "thread_" });
    // A run whose task sleeps is live: it holds the one place a bound of one leaves.
    const sleeping = await r.in(async (instance, state) => {
      const made = await instance.agent.thread("helper", null);
      const pi = await instance.agent.host.pi(made.thread_id);
      const root = (await pi.conversation(1 as never, BG))!;
      await root.commit((tx) => tx.createTask(Reminder, { until: Date.now() + 3_600_000 },
                                              { ownership: { kind: "conversation" },
                                                background: true }), BG);
      pi.resume();
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage, THREAD_LIMITS, THREAD_RETENTION, undefined,
                                  () => ({ ...RUN_POLICY, runsLive: 1 }));
      return gated.write(() => threads.record("main", null, clock()).kind)
        .catch((error: Error) => error.message);
    });
    expect(sleeping).toBe(RUNS_LIVE);
  }, 30_000);

  it("refuses none of a week of 1,000 launches a day, each settled, at the policy's " +
     "defaults", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      const gated = new WriteGate(state.storage);
      const threads = new Threads(gated.storage);
      let launched = 0;
      for (let i = 0; i < 7 * 1_000; i++) {
        const made = await gated.write(() => threads.record("main", null, clock()));
        await gated.write(() => threads.settle(made.thread_id, {
          request: "run_x", status: "done", reason: null, text: "ok" }, clock()));
        launched++;
      }
      return launched;
    });
    expect(out).toBe(7_000);
    expect(7 * 1_000).toBeLessThan(THREAD_LIMITS.runs);
  }, 300_000);
});
