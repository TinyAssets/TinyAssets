/**
 * Scheduled launches as pi tasks, and the owner's admission (tasks 5, F1), in workerd on
 * faux models: a `ta.scheduled` task sleeping in its run thread's own Session, woken by
 * that thread's own job; its delivery's re-checks (the grant, the run policy, the
 * admission); its status and cancel from pi's records; `wake:list` from the owner's rows;
 * cron recurrence; the scheduled share of `runs_live`; admission at every door and lane;
 * and the erasure's tombstone.
 *
 * A days-long sleep is run on pi's own clock (`skewClock`, pi's `HarnessOptions.now`), moved
 * days ahead, and the thread's job made due at once: what the platform's alarm would do
 * when the days had passed.
 */
import { evictDurableObject, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import {
  type AssistantMessage, fauxAssistantMessage, fauxToolCall, type Message,
} from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";

import {
  OWNER_ERASED, OWNER_SUSPENDED, PLATFORM_CALLER, PLATFORM_ONLY, PLATFORM_RESERVED,
  tombstoned, writeTombstone,
} from "../src/admission";
import { AUTHORITY_CHANGED } from "../src/authority";
import {
  CRON_HORIZON_DAYS, CRON_MAX_CHARS, CRON_MIN_GAP_MIN, nextOccurrence, parseCron,
} from "../src/cron";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, Ledger, type Origin } from "../src/ledger";
import { type Binding, NO_OPERATION, type Operation, OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import { CANCELLED, RETRY_FIRST_MS, SCHEDULED } from "../src/scheduled";
import { THREAD_INPUTS } from "../src/schema";
import { wakeJob } from "../src/thread-host";
import { RUNS_LIVE, RUNS_SCHEDULED } from "../src/threads";
import { until } from "./gateway-support";
import { DOT, MCP, MUSE, rig as ledgerRig, signedIn } from "./ledger-support";
import { alarmOf, drainDue, failure, sqlIn, storeOf, storeSql } from "./support";
import { type FauxOwnerDO, says, skewClock, storeScript } from "./worker";

const DAY_S = 86_400;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A user message's text: an untrusted envelope's content when it is one. */
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

/** The model: `hold please` holds (until the test's gate opens); anything else is echoed. */
function reply(messages: Message[]): AssistantMessage {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says("held");
  }
  const asked = last === undefined ? "" : said(last);
  return asked === "hold please"
    ? fauxAssistantMessage([fauxToolCall("ta", { op: "call", name: "hold", arguments: {} })],
                           { stopReason: "toolUse" })
    : says(`echo: ${asked}`);
}

function gate(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** An owner of its own on the faux-model class, signed in. */
async function rig(name = `owner-${crypto.randomUUID()}`) {
  const owner = name;
  const stub = () => env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: Origin | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const platform: Binding = { owner, caller: PLATFORM_CALLER, origin: null };
  const { session: cookie } = await stub().sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => ({ owner, caller: owner, origin: null, session: cookie });
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) =>
    R | Promise<R>) => runInDurableObject(stub(), async (instance: FauxOwnerDO, state) => {
      if (instance.script.length === 0) {
        instance.script = Array.from({ length: 200 }, () => reply);
      }
      return act(instance, state);
    });
  await inside(() => undefined);
  const task = async (thread: string) => {
    const [row] = await storeSql(await storeOf(stub(), thread),
                                 "SELECT status, record FROM pi_tasks WHERE " +
                                   "json_extract(record, '$.kind') = ?", SCHEDULED);
    return row === undefined ? undefined
      : { status: row["status"] as string, record: JSON.parse(row["record"] as string) as {
          state: { status: string; until?: number; checkpoint?: { n: number; tries: number };
                   outcome?: { status: string; result?: unknown;
                               error?: { message: string } } } } };
  };
  return {
    owner, stub, bind, platform, session, at, in: inside, task,
    admit: async (origin: Origin) => {
      await stub().outsideAgents(bind(origin));
    },
    /** A thread's live row: whether it works, and when it next wakes. */
    live: async (thread: string) => (await sqlIn(stub(), "SELECT active, wake_at FROM " +
                                                   "agent_live WHERE thread_id = ?", thread))[0],
    /** Its wake job's time (ms), or undefined. */
    job: async (thread: string) => (await sqlIn(stub(), "SELECT time FROM cf_agents_jobs " +
                                                  "WHERE id = ?", wakeJob(thread)))[0]?.["time"],
    /** The submissions a schedule made in its thread, by the work's request ids. */
    delivered: async (op: Operation) => {
      const work = op.operation_id.split(".")[1]!;
      return (await storeSql(await storeOf(stub(), op.thread_id),
                             "SELECT json_extract(record, '$.requestId') AS request, status " +
                               "FROM pi_submissions ORDER BY id")).filter((row) =>
        String(row["request"]).startsWith(work));
    },
    /**
     * Time passes to `until` (ms, on pi's clock) and past it: pi's clock is moved there and
     * the thread's job made due now, as the platform's alarm would find it then.
     */
    pass: async (thread: string, untilMs: number) => {
      await inside((_instance, state) => skewClock(state, untilMs - Date.now() + 1_000));
      await sqlIn(stub(), "UPDATE cf_agents_jobs SET time = ? WHERE id = ?", Date.now(),
                  wakeJob(thread));
    },
    status: (op: Operation, origin: Origin | null = null) =>
      stub().agentStatus(bind(origin), { operation_id: op.operation_id }),
    settled: async (op: Operation, origin: Origin | null = null): Promise<Operation> => {
      for (let tries = 0; tries < 400; tries++) {
        const status = await stub().agentStatus(bind(origin), { operation_id: op.operation_id });
        if (status.status === "done" || status.status === "unanswered") {
          return status;
        }
        await sleep(25);
      }
      throw new Error("the operation did not settle");
    },
  };
}

type Rig = Awaited<ReturnType<typeof rig>>;

/** A launch an hour (or `ahead` s) from now, once its task sleeps. */
async function later(r: Rig, origin: Origin | null = null, ahead = 3600,
                     extra: { cron?: string; input?: string } = {}): Promise<Operation> {
  const op = await r.stub().agentRun(r.bind(origin), {
    agent: "main", input: extra.input ?? "wake up", at: clock() + ahead,
    ...(extra.cron === undefined ? {} : { cron: extra.cron }) });
  await until(async () => (await r.task(op.thread_id))?.status === "sleeping"
                && (await r.live(op.thread_id))?.["active"] === 0, "the launch to sleep");
  return op;
}

describe("a scheduled launch's sleep (F1)", () => {
  it("sleeps days holding no invocation, its wake in its live row, and through an eviction " +
     "and a reopen delivers once, as ta.work, then completes", async () => {
    const r = await rig();
    const due = clock() + 3 * DAY_S;
    const op = await r.stub().agentRun(r.bind(null), { agent: "main", input: "wake up",
                                                       at: due });
    expect(op).toMatchObject({ status: "scheduled", due });
    expect(op.operation_id).toMatch(/^thread_[0-9a-f]{32}\.work_[0-9a-f]{32}$/);
    await until(async () => (await r.live(op.thread_id))?.["wake_at"] === due, "its wake row");
    expect(await r.live(op.thread_id)).toEqual({ active: 0, wake_at: due });
    expect(await r.job(op.thread_id)).toBe(due * 1000);
    expect((await r.task(op.thread_id))!.record.state).toMatchObject({ status: "sleeping",
                                                                       until: due * 1000 });
    expect(await r.status(op)).toMatchObject({ status: "scheduled", due });
    expect(await r.delivered(op)).toEqual([]);
    // Its Session closed (an open one holds its store's session), the object is evicted.
    await r.in((instance) => instance.agent.host.close(op.thread_id));
    await evictDurableObject(r.stub());
    expect(await r.in((instance) => instance.agent.host.openThreads())).toEqual([]);
    // Three days on.
    await r.pass(op.thread_id, due * 1000);
    await drainDue(r.stub());
    expect(await r.settled(op)).toMatchObject({ status: "done", text: "echo: wake up" });
    expect(await r.delivered(op)).toEqual([{ request: op.operation_id.split(".")[1], status: "done" }]);
    const entries = (await r.in((instance) => instance.harness.messages({
      session: op.thread_id }))).filter((entry) => entry.kind === "ta.work");
    expect(entries.map((entry) => entry.data)).toEqual([{ work_id: op.operation_id.split(".")[1],
                                                          client: null }]);
    expect((await r.task(op.thread_id))!.record.state).toMatchObject({
      status: "terminal", outcome: { status: "completed", result: { deliveries: 1 } } });
    // Its run settles, and its job goes; a later alarm delivers nothing more.
    await until(async () => (await sqlIn(r.stub(), "SELECT settled_at FROM agent_threads WHERE " +
                                           "thread_id = ?", op.thread_id))[0]?.["settled_at"]
                  !== null, "the run to settle");
    await until(async () => (await r.job(op.thread_id)) === undefined, "its job to go");
    await runDurableObjectAlarm(r.stub());
    expect(await r.delivered(op)).toHaveLength(1);
    expect(await r.status(op)).toMatchObject({ status: "done", text: "echo: wake up" });
  }, 60_000);

  it("is cancelled during its sleep: its task ends aborted at once, its run settles " +
     "cancelled, and a second cancel cancels nothing", async () => {
    const r = await rig();
    const op = await later(r);
    expect(await r.stub().agentCancel(r.bind(null), { operation_id: op.operation_id }))
      .toEqual({ operation_id: op.operation_id, cancelled: true });
    await until(async () => (await r.task(op.thread_id))?.status === "terminal", "the abort");
    expect((await r.task(op.thread_id))!.record.state.outcome).toMatchObject({
      status: "aborted", reason: CANCELLED, result: { deliveries: 0 } });
    expect(await r.settled(op)).toMatchObject({ status: "unanswered", reason: CANCELLED });
    await until(async () => (await r.live(op.thread_id)) === undefined
                  && (await r.job(op.thread_id)) === undefined, "its live row and job to go");
    expect(await r.stub().agentCancel(r.bind(null), { operation_id: op.operation_id }))
      .toEqual({ operation_id: op.operation_id, cancelled: false });
    // Pass its time: nothing is delivered.
    await runDurableObjectAlarm(r.stub());
    expect(await r.delivered(op)).toEqual([]);
  }, 30_000);

  it("is wiped with its thread by its client's grant change, sleeper and all", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const op = await later(r, MUSE);
    const store = await storeOf(r.stub(), op.thread_id);
    await r.stub().grantRevoke(r.bind(null), { client: MUSE.client });
    await until(async () => (await sqlIn(r.stub(), "SELECT 1 FROM agent_threads WHERE " +
                                           "thread_id = ?", op.thread_id)).length === 0,
                "the drop");
    expect(await storeSql(store, "SELECT name FROM sqlite_master WHERE name GLOB 'pi_*'"))
      .toEqual([]);
    expect([await r.live(op.thread_id), await r.job(op.thread_id)])
      .toEqual([undefined, undefined]);
    expect(await r.in((instance) => instance.probes.wiped.has(store))).toBe(true);
    // The owner finds no operation of the client's.
    expect((await failure(() => r.status(op))).message).toBe(NO_OPERATION);
  }, 30_000);

  it("is left asleep by a policy write, which ends its thread's running work only; its " +
     "delivery then meets the policy as it stands, and is refused", async () => {
    const r = await rig();
    const op = await later(r);
    const held = gate();
    await r.in(async (instance) => {
      instance.probes.holdGate = held.promise;
      await instance.agent.host.submit(op.thread_id, "hold please", { operationId: "run_held" });
      await until(() => instance.probes.holds === 1, "the hold to start");
    });
    const { generation, policy } = await r.stub().policyRead(r.bind(null));
    // Room for one live run: the scheduled share of it is none.
    await r.stub().policyWrite(r.session(), { expected_generation: generation,
                                              policy: { ...policy, runs: { runs_live: 1 } } });
    const ended = await r.in((instance) => instance.agent.host.settled(op.thread_id,
                                                                       "run_held"));
    held.release();
    expect([ended.status, ended.reason]).toEqual(["unanswered", "aborted"]);
    expect((await r.task(op.thread_id))!.status).toBe("sleeping");
    // A new launch is refused at once: the sleeper is the one live run the policy allows.
    expect((await failure(() => r.stub().agentRun(r.bind(null), {
      agent: "main", input: "x", at: clock() + 60 }))).message).toBe(RUNS_LIVE);
    // Due: its delivery is refused, the task ends with the reason, and nothing is submitted.
    await r.pass(op.thread_id, (await r.task(op.thread_id))!.record.state.until!);
    await drainDue(r.stub());
    expect(await r.settled(op)).toMatchObject({ status: "unanswered", reason: "runs_scheduled" });
    expect((await r.task(op.thread_id))!.record.state.outcome).toMatchObject({
      status: "failed", error: { message: "runs_scheduled" } });
    expect(await r.delivered(op)).toEqual([]);
  }, 30_000);

  it("is refused at its delivery while the owner is suspended, ending terminal with that " +
     "reason, and the doors refuse meanwhile", async () => {
    const r = await rig();
    const op = await later(r);
    expect(await r.stub().admissionSet(r.platform, { reason: "billing", suspended: true }))
      .toEqual({ state: "suspended", reasons: ["billing"] });
    expect((await failure(() => r.status(op))).message).toMatch(new RegExp(`^${OWNER_SUSPENDED}`));
    expect((await r.task(op.thread_id))!.status).toBe("sleeping");
    await r.pass(op.thread_id, (await r.task(op.thread_id))!.record.state.until!);
    await drainDue(r.stub());
    await until(async () => (await r.task(op.thread_id))?.status === "terminal", "the refusal");
    expect((await r.task(op.thread_id))!.record.state.outcome).toMatchObject({
      status: "failed", error: { message: "owner_suspended" } });
    expect(await r.delivered(op)).toEqual([]);
    await r.stub().admissionSet(r.platform, { reason: "billing", suspended: false });
    expect(await r.settled(op)).toMatchObject({ status: "unanswered", reason: "owner_suspended" });
  }, 30_000);

  it("delivers once though the object dies inside its delivery, after the submit: pi's " +
     "requestId finds it, and it is not checked again", async () => {
    const r = await rig();
    const op = await later(r);
    await r.in((instance, state) => {
      // The model's answers for an instance after the crash, which no test sets in time.
      storeScript(state, [says("echo: wake up"), says("echo: wake up")]);
      const host = instance.agent.host;
      const submit = host.submit.bind(host);
      Object.assign(host, { submit: async (...args: Parameters<typeof submit>) => {
        const done = await submit(...args);
        if (args[2].entry?.kind === "ta.work") {
          state.abort("evicted inside the delivery");
        }
        return done;
      } });
    });
    await r.pass(op.thread_id, (await r.task(op.thread_id))!.record.state.until!);
    await runDurableObjectAlarm(r.stub()).catch(() => undefined);
    // A new instance answers; its alarm reopens the Session, and the task's phase runs again.
    for (let tries = 0; ; tries++) {
      if (await r.in(() => true).catch(() => false)) {
        break;
      }
      if (tries > 100) {
        throw new Error("the object did not come back");
      }
      await sleep(10);
    }
    await sqlIn(r.stub(), "UPDATE cf_agents_jobs SET time = ? WHERE id = ?", Date.now(),
                wakeJob(op.thread_id));
    await drainDue(r.stub());
    expect(await r.settled(op)).toMatchObject({ status: "done", text: "echo: wake up" });
    expect(await r.delivered(op)).toEqual([{ request: op.operation_id.split(".")[1], status: "done" }]);
    expect((await r.task(op.thread_id))!.record.state.outcome).toMatchObject({
      status: "completed", result: { deliveries: 1 } });
  }, 60_000);

  it("sleeps again, backing off, when its thread is full at its delivery, and later " +
     "delivers once", async () => {
    const r = await rig();
    const op = await later(r);
    const held = gate();
    await r.in(async (instance) => {
      instance.probes.holdGate = held.promise;
      await instance.agent.host.submit(op.thread_id, "hold please", { operationId: "run_0" });
      await until(() => instance.probes.holds === 1, "the hold to start");
      for (let n = 1; n < THREAD_INPUTS; n++) {
        await instance.agent.host.submit(op.thread_id, `more ${n}`, { operationId: `run_${n}` });
      }
      expect(instance.parts.turns.inputs(op.thread_id)).toBe(THREAD_INPUTS);
    });
    const first = (await r.task(op.thread_id))!.record.state.until!;
    await r.pass(op.thread_id, first);
    // The thread's job waits on its working Session; at the sleep's time the wait resumes it.
    await r.in((instance) => instance.agent.host.peek(op.thread_id)!.pi.resume());
    await until(async () => (await r.task(op.thread_id))?.record.state.checkpoint?.tries === 1,
                "the delivery to sleep again");
    const again = (await r.task(op.thread_id))!.record.state;
    expect(again).toMatchObject({ status: "sleeping", checkpoint: { n: 1, tries: 1 } });
    expect(again.until! - (first + 1_000)).toBeGreaterThanOrEqual(RETRY_FIRST_MS - 5_000);
    expect(await r.delivered(op)).toEqual([]);
    held.release();
    await until(() => r.in((instance) => instance.parts.turns.inputs(op.thread_id) === 0),
                "room to free");
    await r.pass(op.thread_id, again.until!);
    await drainDue(r.stub());
    expect(await r.settled(op)).toMatchObject({ status: "done", text: "echo: wake up" });
    expect(await r.delivered(op)).toHaveLength(1);
  }, 60_000);
});

describe("a scheduled launch's status, cancel and wake:list", () => {
  it("lists from the owner's rows, its run's Session never opened, nor its store claimed",
     async () => {
    const r = await rig();
    const op = await later(r);
    const out = await r.in(async (instance) => {
      await instance.agent.host.close(op.thread_id);
      const opens = instance.probes.storeCalls["open"] ?? 0;
      const rows = instance.parts.threads.scheduledOf(null, 10);
      const status = await instance.agent.scheduledStatus(
        instance.parts.threads.get(op.thread_id)!, op.operation_id.split(".")[1]!);
      return { rows, status, open: instance.agent.host.openThreads(),
               opened: (instance.probes.storeCalls["open"] ?? 0) - opens };
    });
    expect(out.rows).toEqual([expect.objectContaining({
      operation_id: op.operation_id, thread_id: op.thread_id, state: "sleeping",
      due: op.due, settled_at: null, status: null })]);
    expect(out.status).toMatchObject({ standing: { delivered: 0, due: op.due, ended: null },
                                       last: null });
    expect([out.open, out.opened]).toEqual([[], 0]);
    expect(await r.status(op)).toMatchObject({ status: "scheduled", due: op.due });
    expect(await r.in((instance) => instance.agent.host.openThreads())).toEqual([]);
  }, 30_000);

  it("answers a delivered launch's status from its delivery, then its receipt, and cancels " +
     "nothing of it; a refused one's with its reason", async () => {
    const r = await rig();
    const op = await later(r, null, 1);
    await r.pass(op.thread_id, (await r.task(op.thread_id))!.record.state.until!);
    await drainDue(r.stub());
    expect(await r.settled(op)).toMatchObject({ status: "done", text: "echo: wake up" });
    expect(await r.stub().agentCancel(r.bind(null), { operation_id: op.operation_id }))
      .toMatchObject({ cancelled: false });
    // Refused: the grant moved by a writer that kept the thread; its delivery refuses it.
    await r.admit(MUSE);
    const theirs = await later(r, MUSE);
    // Revoked by a writer that kept the thread (no door's drop).
    await r.in((_instance, state) => {
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
    });
    await r.pass(theirs.thread_id, (await r.task(theirs.thread_id))!.record.state.until!);
    await drainDue(r.stub());
    await until(async () => (await r.task(theirs.thread_id))?.status === "terminal", "refused");
    expect((await r.task(theirs.thread_id))!.record.state.outcome).toMatchObject({
      status: "failed", error: { message: AUTHORITY_CHANGED } });
    const status = await r.in(async (instance) => instance.agent.scheduledStatus(
      instance.parts.threads.get(theirs.thread_id)!, theirs.operation_id.split(".")[1]!));
    expect(status).toMatchObject({ standing: { delivered: 0, ended: { reason: AUTHORITY_CHANGED } } });
    expect(await r.in(async (instance) => instance.agent.cancelScheduled(
      instance.parts.threads.get(theirs.thread_id)!, theirs.operation_id.split(".")[1]!)))
      .toBe(false);
  }, 60_000);

  it("refuses a delivery whose stamp is not its thread's, and a launch made in a thread its " +
     "row does not name", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      const work = `work_${"9".repeat(32)}`;
      const thread = await instance.agent.thread("main", null, { kind: "run", work });
      // Under the client's stamp, in the owner's thread: its delivery refuses it.
      await instance.agent.schedule(thread, {
        work_id: work, thread: thread.thread_id, at: clock() + 3600, input: "x", cron: null,
        stamp: { client: MUSE.client, generation: 1, authenticated_at: r.at, universe: null,
                 agent: "main" } });
      const other = await instance.agent.thread("main", null, { kind: "run" });
      const unnamed = await instance.agent.schedule(other, {
        work_id: work, thread: other.thread_id, at: clock() + 3600, input: "x", cron: null,
        stamp: null }).then(() => "made", (error: Error) => error.message);
      return { thread: thread.thread_id, unnamed };
    });
    expect(out.unnamed).toBe("a scheduled launch is made in the run its row names");
    await until(async () => (await r.task(out.thread))?.status === "sleeping", "the sleep");
    await r.pass(out.thread, (await r.task(out.thread))!.record.state.until!);
    await drainDue(r.stub());
    await until(async () => (await r.task(out.thread))?.status === "terminal", "the refusal");
    expect((await r.task(out.thread))!.record.state.outcome).toMatchObject({
      status: "failed", error: { message: "thread_authority" } });
  }, 30_000);

  it("holds scheduled launches to their share of runs_live: past it a scheduled launch is " +
     "refused, while a launch now still runs", async () => {
    const r = await rig();
    const { generation, policy } = await r.stub().policyRead(r.bind(null));
    await r.stub().policyWrite(r.session(), { expected_generation: generation,
                                              policy: { ...policy, runs: { runs_live: 4 } } });
    await later(r);
    await later(r);
    expect((await failure(() => r.stub().agentRun(r.bind(null), {
      agent: "main", input: "x", at: clock() + 60 }))).message).toBe(RUNS_SCHEDULED);
    expect((await r.stub().agentRun(r.bind(null), { agent: "main", input: "now" })).status)
      .toBe("queued");
    // What the owner sees of them: its two scheduled launches, asleep.
    expect((await r.in((instance) => instance.parts.threads.scheduledOf(null, 10)))
      .map((row) => row.state)).toEqual(["sleeping", "sleeping"]);
  }, 30_000);
});

describe("cron recurrence", () => {
  /** A deterministic generator, so a failure names its seed. */
  function random(seed: number): () => number {
    let state = seed >>> 0;
    return () => {
      state = (state * 1664525 + 1013904223) >>> 0;
      return state / 2 ** 32;
    };
  }
  const pick = <T>(next: () => number, items: readonly T[]): T =>
    items[Math.floor(next() * items.length)]!;

  /** Whether `value` matches one field's text, by arithmetic, apart from `cron.ts`. */
  function fieldMatches(text: string, value: number, low: number, high: number): boolean {
    return text.split(",").some((item) => {
      const [base, step] = item.split("/") as [string, string | undefined];
      const by = step === undefined ? 1 : Number(step);
      const [from, to] = base === "*" ? [low, high]
        : base.includes("-") ? base.split("-").map(Number) as [number, number]
          : [Number(base), step === undefined ? Number(base) : high];
      return value >= from && value <= to && (value - from) % by === 0;
    });
  }

  /** Whether the minute starting at `t` (seconds) matches `expression`, in UTC. */
  function matches(expression: string, t: number): boolean {
    const [minute, hour, day, month, weekday] = expression.split(" ") as
      [string, string, string, string, string];
    const date = new Date(t * 1000);
    const dayOk = fieldMatches(day, date.getUTCDate(), 1, 31);
    const weekdayOk = fieldMatches(weekday, date.getUTCDay(), 0, 6);
    const days = day === "*" && weekday === "*" ? true : day === "*" ? weekdayOk
      : weekday === "*" ? dayOk : dayOk || weekdayOk;
    return t % 60 === 0 && fieldMatches(minute, date.getUTCMinutes(), 0, 59)
      && fieldMatches(hour, date.getUTCHours(), 0, 23)
      && fieldMatches(month, date.getUTCMonth() + 1, 1, 12) && days;
  }

  it("answers the first matching minute strictly after a time, for random expressions " +
     "(property tests against a minute-by-minute scan)", () => {
    const minutes = ["0", "*/15", "5,35", "0-30/10", "7", "*/20", "45"];
    const hours = ["*", "*/2", "9-17", "3", "1,13", "0-23/6", "22"];
    const days = ["*", "*", "1", "15", "1-7", "*/10", "28-31"];
    const months = ["*", "*", "1", "*/3", "6-8", "2"];
    const weekdays = ["*", "*", "1-5", "0", "6", "2,4"];
    let checked = 0;
    for (let seed = 1; seed <= 400; seed++) {
      const next = random(seed);
      const expression = [pick(next, minutes), pick(next, hours), pick(next, days),
                          pick(next, months), pick(next, weekdays)].join(" ");
      const cron = parseCron(expression);
      const after = 1_700_000_000 + Math.floor(next() * 3 * 365 * DAY_S) + Math.floor(next() * 60);
      const found = nextOccurrence(cron, after);
      expect(found, `${expression} after ${after}`).not.toBeNull();
      expect(found! > after && matches(expression, found!), `${expression} after ${after}`)
        .toBe(true);
      // Nothing matches between: scanned minute by minute while the gap is short.
      if (found! - after <= 3 * DAY_S) {
        for (let t = Math.floor(after / 60) * 60 + 60; t < found!; t += 60) {
          expect(matches(expression, t), `${expression}: ${t} before ${found}`).toBe(false);
        }
        checked++;
      }
      // From the occurrence itself, the next is a later one.
      expect(nextOccurrence(cron, found!)! > found!).toBe(true);
    }
    expect(checked).toBeGreaterThan(100);
  });

  it("matches a day by either day field when both are restricted, as classic cron", () => {
    // The 13th of a month, or a Friday: 2026-11-13 is a Friday; 2026-11-06 a Friday too.
    const cron = parseCron("0 9 13 * 5");
    const start = Date.UTC(2026, 10, 1) / 1000;
    expect(nextOccurrence(cron, start)).toBe(Date.UTC(2026, 10, 6, 9) / 1000);
    expect(nextOccurrence(cron, Date.UTC(2026, 10, 6, 9) / 1000))
      .toBe(Date.UTC(2026, 10, 13, 9) / 1000);
    // A leap day four years off is within the horizon.
    expect(nextOccurrence(parseCron("0 0 29 2 *"), Date.UTC(2025, 0, 1) / 1000))
      .toBe(Date.UTC(2028, 1, 29) / 1000);
    expect(CRON_HORIZON_DAYS).toBeGreaterThanOrEqual(8 * 365);
  });

  it.each([
    ["* * * * *", "minutes apart"], ["*/5 * * * *", "minutes apart"],
    ["0,10 * * * *", "minutes apart"], ["0 0-23 * * *", null],
    ["60 * * * *", "takes 0 to 59"], ["0 24 * * *", "takes 0 to 23"],
    ["0 0 0 * *", "takes 1 to 31"], ["0 0 * 13 *", "takes 1 to 12"],
    ["0 0 * * 7", "takes 0 to 6"], ["0 0 * * MON", "not a value"], ["0 0 * JAN *", "not a value"],
    ["0 0 L * *", "not a value"], ["0 0 ? * *", "not a value"], ["0 0 * * 1#2", "not a value"],
    ["0 0 30 2 *", "an occurrence within"], ["0 0 31 4 *", "an occurrence within"],
    ["0 0 * *", "exactly five fields"], ["0 0 * * * *", "exactly five fields"],
    [`0 ${Array.from({ length: 17 }, (_, n) => n).join(",")} * * *`, "at most 16 items"],
    [`0 0 * * *${" ".repeat(CRON_MAX_CHARS)}`, "characters"], ["", "characters"],
    ["0 5-1 * * *", "takes 0 to 23"], ["*/0 * * * *", "takes 0 to 59"],
  ])("bounds what it accepts: %s", (expression, refusal) => {
    if (refusal === null) {
      expect(parseCron(expression).hours).toHaveLength(24);
      return;
    }
    expect(() => parseCron(expression)).toThrow(refusal);
  });

  it("holds the bounds as named", () => {
    expect([CRON_MAX_CHARS, CRON_MIN_GAP_MIN]).toEqual([64, 15]);
    // The least gap counts across midnight: 23:50 then 00:00 is ten minutes.
    expect(() => parseCron("0,50 0,23 * * *")).toThrow("minutes apart");
    expect(parseCron("0 0,23 * * *").hours).toEqual([0, 23]);
  });

  it("delivers a recurring launch at each occurrence, three times, until its client's " +
     "grant changes, which ends it with its thread", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const op = await r.stub().agentRun(r.bind(MUSE), { agent: "main", input: "tick",
                                                       cron: "0 * * * *" });
    expect(op.status).toBe("scheduled");
    const work = op.operation_id.split(".")[1]!;
    expect(op.due! % 3600).toBe(0);
    for (let n = 1; n <= 3; n++) {
      await until(async () => (await r.task(op.thread_id))?.status === "sleeping"
                    && (await r.live(op.thread_id))?.["active"] === 0, `sleep ${n}`);
      const { until: due } = (await r.task(op.thread_id))!.record.state;
      expect(due! % 3_600_000).toBe(0);
      await r.pass(op.thread_id, due!);
      await drainDue(r.stub());
      await until(async () => (await r.delivered(op)).length === n
                    && (await r.delivered(op))[n - 1]!["status"] === "done", `delivery ${n}`);
    }
    expect((await r.delivered(op)).map((row) => row["request"]))
      .toEqual([work, `${work}.2`, `${work}.3`]);
    expect(await r.status(op, MUSE)).toMatchObject({ status: "done", text: "echo: tick",
                                                     due: expect.any(Number) });
    await until(async () => (await r.task(op.thread_id))?.status === "sleeping", "sleep 4");
    // The grant change ends it: its thread dropped, its store wiped, no fourth delivery.
    const store = await storeOf(r.stub(), op.thread_id);
    await r.stub().grantRevoke(r.bind(null), { client: MUSE.client });
    await until(async () => (await sqlIn(r.stub(), "SELECT 1 FROM agent_threads WHERE " +
                                           "thread_id = ?", op.thread_id)).length === 0,
                "the drop");
    expect(await storeSql(store, "SELECT name FROM sqlite_master WHERE name GLOB 'pi_*'"))
      .toEqual([]);
    expect(await r.job(op.thread_id)).toBeUndefined();
  }, 90_000);

  it("refuses a launch whose cron it does not accept, before any thread", async () => {
    const r = await rig();
    expect((await failure(() => r.stub().agentRun(r.bind(null), {
      agent: "main", input: "x", cron: "* * * * *" }))).name).toBe("Invalid");
    expect(await sqlIn(r.stub(), "SELECT 1 FROM agent_threads")).toEqual([]);
  }, 30_000);
});

describe("the owner's admission", () => {
  const doors = () => Object.getOwnPropertyNames(OwnerDO.prototype)
    .filter((door) => door !== "constructor" && door !== "alarm" && door !== "admissionSet");
  const call = (r: Rig, door: string, binding: Binding) =>
    (r.stub() as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>)[door]!(
      binding, {});

  it("refuses every door while suspended, for either reason, until each is lifted", async () => {
    const r = await rig();
    expect(doors().length).toBeGreaterThan(60);
    await r.stub().admissionSet(r.platform, { reason: "billing", suspended: true });
    await r.stub().admissionSet(r.platform, { reason: "abuse", suspended: true });
    const answers = new Map<string, string>();
    for (const door of doors()) {
      answers.set(door, (await failure(() => call(r, door, r.session()))).message);
    }
    expect([...answers].filter(([, answer]) =>
      answer !== `${OWNER_SUSPENDED} (abuse, billing)`)).toEqual([]);
    expect(await r.stub().admissionSet(r.platform, { reason: "billing", suspended: false }))
      .toEqual({ state: "suspended", reasons: ["abuse"] });
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
      .toBe(`${OWNER_SUSPENDED} (abuse)`);
    expect(await r.stub().admissionSet(r.platform, { reason: "abuse", suspended: false }))
      .toEqual({ state: "admitted", reasons: [] });
    expect((await r.stub().policyRead(r.bind(null))).generation).toBeGreaterThan(0);
  }, 60_000);

  it("is set only by the platform's reserved caller, which reaches no other door, and no " +
     "owner is named so", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const set = { reason: "billing", suspended: true };
    expect((await failure(() => r.stub().admissionSet(r.bind(null), set))).message)
      .toBe(PLATFORM_ONLY);
    expect((await failure(() => r.stub().admissionSet(r.session(), set))).message)
      .toBe(PLATFORM_ONLY);
    expect((await failure(() => r.stub().admissionSet(
      { ...r.platform, origin: MUSE, source: MCP }, set))).message).toBe(PLATFORM_ONLY);
    expect((await failure(() => r.stub().admissionSet({ ...r.platform, session: "x" }, set)))
      .message).toBe(PLATFORM_ONLY);
    for (const door of ["policyRead", "collectionPull", "agentStatus", "fileUsage"]) {
      expect((await failure(() => call(r, door, r.platform))).message).toBe(PLATFORM_RESERVED);
    }
    for (const bad of [{ reason: "fraud", suspended: true }, { reason: "billing" },
                       { reason: "billing", suspended: "yes" }, {}]) {
      expect((await failure(() => r.stub().admissionSet(r.platform, bad as never))).name)
        .toBe("Invalid");
    }
    const named = env.AGENT.getByName(PLATFORM_CALLER);
    expect((await failure(() => named.policyRead(
      { owner: PLATFORM_CALLER, caller: PLATFORM_CALLER, origin: null }))).message)
      .toBe(PLATFORM_RESERVED);
    // Still admitted: nothing above set it.
    expect((await r.stub().policyRead(r.bind(null))).generation).toBeGreaterThan(0);
  }, 30_000);

  it("holds the wakes lane's claims while the owner is not admitted", async () => {
    const r = await ledgerRig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    const due = clock() + 3600;
    const out = await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage));
      const wake = (admitted: boolean) => {
        const ledger = new Ledger(state.storage, grants, undefined, () => admitted);
        return { next: ledger.nextDue(clock(), admitted),
                 claim: ledger.claim(ledger.dueOpen(due, 1)[0]!.wake_id, due).outcome };
      };
      return [wake(false), wake(true)];
    });
    expect(out[0]).toEqual({ next: null, claim: "held" });
    expect(out[1]).toMatchObject({ claim: "claimed" });
  }, 30_000);
});

describe("the erasure's tombstone", () => {
  /** The owner's tables, the runtime's (`_cf_`) aside. */
  const tables = (r: Rig) => sqlIn(r.stub(), "SELECT name FROM sqlite_master WHERE " +
                                     "type = 'table' AND name NOT LIKE '\\_cf\\_%' ESCAPE " +
                                     "'\\' ORDER BY name");

  it("leaves exactly one erased admission row, in the wipe's own turn; a stale getByName is " +
     "then refused, migrating nothing and starting no Lifecycle, across evictions", async () => {
    const r = await rig();
    await r.stub().agentSubmit(r.bind(null), { message: "hello", client_send_id: "a" });
    await later(r);
    await r.in((instance) => instance.erasure.erase());
    expect(await tables(r)).toEqual([{ name: "owner_admission" }]);
    expect(await sqlIn(r.stub(), "SELECT only, state, reasons FROM owner_admission"))
      .toEqual([{ only: 1, state: "erased", reasons: "[]" }]);
    for (let round = 0; round < 2; round++) {
      await evictDurableObject(r.stub());
      for (const call of [() => r.stub().policyRead(r.bind(null)),
                          () => r.stub().sessionOpen(r.bind(null, { authenticated_at: r.at })),
                          () => r.stub().admissionSet(r.platform, { reason: "billing",
                                                                    suspended: false }),
                          () => r.stub().agentRun(r.bind(null), { agent: "main", input: "x" })]) {
        expect((await failure(call)).message).toBe(OWNER_ERASED);
      }
      expect(await tables(r)).toEqual([{ name: "owner_admission" }]);
      expect(await sqlIn(r.stub(), "SELECT name FROM sqlite_master WHERE name GLOB " +
                                     "'cf_agents_*'")).toEqual([]);
      expect(await alarmOf(r.stub())).toBeNull();
      expect(await runDurableObjectAlarm(r.stub())).toBe(false);
    }
  }, 60_000);

  it("is written in the same turn as the wipe, only into a wiped database, and once", async () => {
    const { stub } = await rig();
    const out = await runInDurableObject(stub(), async (_instance: OwnerDO, state) => {
      // Not wiped (its erasure's mark-table is there): nothing is written.
      writeTombstone(state.storage.sql);
      const before = tombstoned(state.storage.sql);
      const gate = new WriteGate(state.storage);
      const wiping = gate.wipe(writeTombstone);
      const sameTurn = tombstoned(state.storage.sql);
      await wiping;
      writeTombstone(state.storage.sql);  // again: one row still
      return { before, sameTurn, rows: state.storage.sql.exec(
        "SELECT state FROM owner_admission").toArray() };
    });
    expect(out).toEqual({ before: false, sameTurn: true, rows: [{ state: "erased" }] });
  }, 30_000);

  it("leaves another owner admitted, made fresh as ever", async () => {
    const r = await rig();
    await r.in((instance) => instance.erasure.erase());
    const other = await rig();
    expect((await other.stub().policyRead(other.bind(null))).generation).toBeGreaterThan(0);
    expect(await sqlIn(other.stub(), "SELECT state FROM owner_admission"))
      .toEqual([{ state: "admitted" }]);
  }, 30_000);
});

describe("ta's agent:run with cron, and DOT's launch", () => {
  it("makes a recurring launch through ta's path, stamped as the calling client", async () => {
    const r = await rig();
    await r.admit(DOT);
    const op = await r.in(async (instance) => instance.parts.platform.run(
      { by: "client", stamp: { client: DOT.client, generation: 1, authenticated_at: r.at,
                               universe: null, agent: "main" } },
      "main", "tick", null, "30 9 * * 1-5")) as Operation;
    expect(op).toMatchObject({ status: "scheduled" });
    expect(new Date(op.due! * 1000).getUTCMinutes()).toBe(30);
    const rows = await r.in((instance) => instance.parts.threads.scheduledOf(DOT.client, 10));
    expect(rows.map((row) => row.operation_id)).toEqual([op.operation_id]);
  }, 30_000);
});
