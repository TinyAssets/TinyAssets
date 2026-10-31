/**
 * The thread host (`thread-host.ts`, tasks 5 S3) in the owner's object on faux models: one
 * pi Session per thread, each over its own store; a wake job per thread, driven by its own
 * Session's wake and kept in `agent_live`; Sessions opened on demand and closed idle, least
 * recently used first; an open past the bound refused at once, a pinned Session never
 * closed; a store reset mid-run recovered by the thread's own job; a destroyed store never
 * opened again. The wake and recovery cases are ported from agents' PiHarness tests
 * (agents `harness/pi/tests/harness.test.ts`, MIT, Copyright (c) 2025 Cloudflare, Inc.),
 * keyed per thread.
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

import { DESTROYED } from "../src/thread-store";
import { HOST, MAX_WAKE_BACKOFF_MS, OPEN_MAX, SESSIONS_FULL, wakeJob } from "../src/thread-host";
import { until } from "./gateway-support";
import { sqlIn, storeOf, storeSql, storeSubmission } from "./support";
import { type FauxOwnerDO, Reminder, says } from "./worker";

const BG = BACKGROUND_CONTEXT;

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

function viaTa(name: string, args: JsonObject): FauxContentBlock {
  return fauxToolCall("ta", { op: "call", name, arguments: args });
}

/** The model: `hold` holds (replay-unsafe), `look` reads (replay-safe), else says noted. */
function reply(messages: Message[]): AssistantMessage {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says(`tool said: ${last.content.map((part) =>
      (part.type === "text" ? part.text : "")).join("")}`);
  }
  const asked = JSON.stringify(last);
  return asked.includes("hold") ? calls(viaTa("hold", {}))
    : asked.includes("look") ? calls(fauxToolCall("read", { path: "a.md" }))
      : says("noted");
}

/** An owner of its own on the faux-model class, its model answering `reply`. */
function rig() {
  const name = `owner-${crypto.randomUUID()}`;
  const stub = () => env.AGENT.getByName(name);
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) =>
    R | Promise<R>) => runInDurableObject(stub(), async (instance: FauxOwnerDO, state) => {
      instance.script = Array.from({ length: 200 }, () => reply);
      return act(instance, state);
    });
  return {
    name, stub, in: inside,
    /** A run thread of the owner's, made by the agent. */
    thread: () => inside(async (instance) =>
      (await instance.agent.thread("main", null)).thread_id),
    /** The host's wake jobs: each thread's id and due time. */
    jobs: async () => (await sqlIn(stub(), "SELECT id, time FROM cf_agents_jobs WHERE " +
                                            "capability = ? ORDER BY id", HOST))
      .map((row) => ({ id: row["id"] as string, time: row["time"] as number })),
    live: async () => (await sqlIn(stub(), "SELECT thread_id, active, wake_at FROM " +
                                            "agent_live ORDER BY thread_id")),
  };
}

const gate = () => {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
};

describe("a wake job per thread", () => {
  it("keeps each thread's own job and its wake in agent_live while it works, and " +
     "completes it, leaving no row, once the thread is idle", async () => {
    const r = rig();
    const [a, b] = [await r.thread(), await r.thread()];
    const held = gate();
    const ops = await r.in(async (instance) => {
      instance.probes.holdGate = held.promise;
      const one = await instance.agent.host.submit(a, "hold one", { operationId: "op-a" });
      const two = await instance.agent.host.submit(b, "hold two", { operationId: "op-b" });
      await until(() => instance.probes.holds === 2, "both holds to start");
      return [one.operationId, two.operationId];
    });
    expect((await r.jobs()).map((job) => job.id).sort()).toEqual([wakeJob(a), wakeJob(b)].sort());
    expect(await r.live()).toEqual([a, b].sort().map((thread) =>
      ({ thread_id: thread, active: 1, wake_at: null })));
    held.release();
    for (const [thread, op] of [[a, ops[0]!], [b, ops[1]!]] as const) {
      await until(async () => (await storeSubmission(await storeOf(r.stub(), thread),
                                                     { request: op }))?.status === "done",
                  "each thread's work to settle");
    }
    await until(async () => (await r.live()).length === 0, "the live rows to go");
    await until(async () => (await r.jobs()).length === 0, "the wake jobs to complete");
  }, 30_000);

  it("wakes a sleeping task in a closed Session by its own job, at its deadline, and no " +
     "other thread's", async () => {
    const r = rig();
    const [sleeper, other] = [await r.thread(), await r.thread()];
    const due = Date.now() + 2_000;
    await r.in(async (instance) => {
      const pi = await instance.agent.host.pi(sleeper);
      const root = (await pi.conversation(1 as never, BG))!;
      await root.commit((tx) => tx.createTask(Reminder, { until: due },
                                              { ownership: { kind: "conversation" },
                                                background: true }), BG);
      pi.resume();
      await instance.agent.host.pi(other);
    });
    // Its job is set for the deadline, and its row says when; nothing else is due.
    await until(async () => (await r.jobs()).some((job) => job.id === wakeJob(sleeper)
                                                         && job.time === due),
                "the sleeper's job at its deadline");
    expect((await r.live()).find((row) => row["thread_id"] === sleeper))
      .toMatchObject({ active: 0, wake_at: due / 1000 });
    expect((await r.jobs()).map((job) => job.id)).toEqual([wakeJob(sleeper)]);
    // Closed, nothing of it is in memory: only its job can wake it.
    await r.in(async (instance) => {
      await instance.agent.host.close(sleeper);
      expect(instance.agent.host.openThreads()).not.toContain(sleeper);
    });
    const store = await storeOf(r.stub(), sleeper);
    const status = async () => (await storeSql(store, "SELECT status FROM pi_tasks WHERE " +
      "json_extract(record, '$.kind') = ?", Reminder.definition.name))[0]?.["status"];
    expect(await status()).not.toBe("terminal");
    await until(async () => Date.now() > due + 50, "the deadline");
    await runDurableObjectAlarm(r.stub());
    await until(async () => (await status()) === "terminal", "the reminder to run, woken");
    expect(await r.in((instance) => instance.agent.host.openThreads())).toContain(sleeper);
    await until(async () => (await r.jobs()).length === 0, "its job to complete");
  }, 30_000);

  it("recovers every thread's run after the owner dies mid-tool, each from its own job",
     async () => {
    const r = rig();
    const threads = [await r.thread(), await r.thread(), await r.thread()];
    await r.stub().fileWrite({ owner: r.name, caller: r.name, origin: null },
                             { path: "/workspace/a.md", expected_etag: null,
                               content: new TextEncoder().encode("alpha") });
    await r.in(async (instance, state) => {
      instance.probes.readGate = new Promise(() => undefined);
      for (const [n, thread] of threads.entries()) {
        await instance.agent.host.submit(thread, "look", { operationId: `op-${n}` });
      }
      await until(() => instance.probes.reads === 3, "the three reads to start");
      void state;
    });
    expect(await runInDurableObject(r.stub(), (_instance, state) => state.abort("crash"))
      .then(() => "alive", (error: Error) => error.message)).toBe("crash");
    // The alarm restarts the object (its model answering as before); each thread's job
    // reopens its own Session.
    await r.in(() => undefined);
    await runDurableObjectAlarm(r.stub());
    for (const [n, thread] of threads.entries()) {
      await until(async () => (await storeSubmission(await storeOf(r.stub(), thread),
                                                     { request: `op-${n}` }))?.status === "done",
                  "each thread's run to settle");
    }
    expect(await r.in((instance) => instance.probes.reads)).toBe(3);  // each read again
  }, 30_000);

  it("keeps a thread's job when its open keeps failing, backing off per thread, and opens " +
     "a destroyed store never again", async () => {
    const r = rig();
    const [gone, kept] = [await r.thread(), await r.thread()];
    await r.in(async (instance) => {
      await instance.harness.prompt("noted", { session: gone });
      await instance.harness.prompt("noted", { session: kept });
      await instance.agent.host.close(gone);
    });
    await env.PISTORE.getByName(await storeOf(r.stub(), gone)).destroy();
    expect(await r.in((instance) => instance.agent.host.pi(gone)
      .then(() => "opened", (error: Error) => error.message))).toContain(DESTROYED);
    // Its job, due now, fails through Lifecycle's retries, then backs off from the heartbeat.
    const delays: number[] = [];
    for (let n = 0; n < 3; n++) {
      const before = Date.now();
      await r.in(async (instance) => {
        await (instance.agent.host as unknown as { lifecycle: { jobs: {
          push(job: object): Promise<unknown> } } }).lifecycle.jobs.push({
          id: wakeJob(gone), fn: "wake", time: Date.now(), singleflight: true,
          recoveryLoop: true });
      });
      await runDurableObjectAlarm(r.stub());
      let job = (await r.jobs()).find((one) => one.id === wakeJob(gone));
      for (let i = 0; i < 200 && (job === undefined || job.time <= Date.now()); i++) {
        await new Promise((resolve) => setTimeout(resolve, 20));
        job = (await r.jobs()).find((one) => one.id === wakeJob(gone));
      }
      delays.push(job!.time - before);
    }
    expect(delays[0]).toBeGreaterThanOrEqual(1_000);
    expect(delays[1]).toBeGreaterThanOrEqual(2_000);
    expect(delays[2]).toBeGreaterThanOrEqual(4_000);
    expect(delays[2]).toBeLessThan(MAX_WAKE_BACKOFF_MS);
    // Another thread is untouched by it.
    expect(await r.in(async (instance) => (await instance.harness.prompt("again",
                                                                        { session: kept }))
      .status)).toBe("done");
  }, 60_000);
});

describe("the open Sessions", () => {
  it("refuse an open past the bound at once, never waiting, while every one works or is " +
     "pinned; an idle one is closed to make room, the least recently used first", async () => {
    const r = rig();
    const [a, b, c, d] = [await r.thread(), await r.thread(), await r.thread(),
                          await r.thread()];
    const held = gate();
    const out = await r.in(async (instance) => {
      instance.timing.openMax = 2;
      instance.probes.holdGate = held.promise;
      const host = instance.agent.host;
      await host.submit(a, "hold it", { operationId: "op-a" });
      await until(() => instance.probes.holds === 1, "the hold to start");
      await host.pin(b);
      const began = Date.now();
      const refused = await host.pi(c).then(() => "opened", (error: Error) => error.message);
      const took = Date.now() - began;
      const open = host.openThreads().sort();
      host.unpin(b);
      await host.pi(c);  // b is idle now: closed to make room
      const afterC = host.openThreads().sort();
      await new Promise((resolve) => setTimeout(resolve, 10));
      await host.pi(d);  // c is idle and the least recently used; a still works
      const afterD = host.openThreads().sort();
      held.release();
      return { refused, took, open, afterC, afterD };
    });
    expect(out.refused).toBe(SESSIONS_FULL);
    expect(out.took).toBeLessThan(1_000);
    expect(out.open).toEqual([a, b].sort());
    expect(out.afterC).toEqual([a, c].sort());
    expect(out.afterD).toEqual([a, d].sort());
    expect(OPEN_MAX).toBe(64);
  }, 30_000);

  it("close the least recently used idle one, never one a call is using", async () => {
    const r = rig();
    const [older, newer, used, next] = [await r.thread(), await r.thread(), await r.thread(),
                                        await r.thread()];
    const held = gate();
    const out = await r.in(async (instance) => {
      instance.timing.openMax = 3;
      const host = instance.agent.host;
      await host.pi(older);
      await new Promise((resolve) => setTimeout(resolve, 10));
      await host.pi(newer);
      await new Promise((resolve) => setTimeout(resolve, 10));
      // A call in flight holds its Session, idle as it is.
      const using = host.use(used, () => held.promise);
      await until(() => host.openThreads().includes(used), "the used Session to open");
      await host.pi(next);  // the least recently used idle one goes: `older`
      const afterNext = host.openThreads().sort();
      // Only the used Session left open, past a bound of one: refused, never closing it.
      await host.close(newer);
      await host.close(next);
      instance.timing.openMax = 1;
      const refused = await host.pi(older).then(() => "opened", (error: Error) =>
        error.message);
      held.release();
      await using;
      return { afterNext, refused, open: host.openThreads().sort() };
    });
    expect(out.afterNext).toEqual([newer, used, next].sort());
    expect(out.refused).toBe(SESSIONS_FULL);
    expect(out.open).toContain(used);
  }, 30_000);

  it("reopen a Session only once its last one has closed", async () => {
    const r = rig();
    const thread = await r.thread();
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.probes.holdLinger = 1_000;  // the tool takes a second to stop once aborted
      const host = instance.agent.host;
      await host.submit(thread, "hold it", { operationId: "op-c" });
      await until(() => instance.probes.holds === 1, "the hold to start");
      // Closing waits for the running tool's code to return.
      const closing = host.close(thread);
      let reopened = false;
      const reopening = host.pi(thread).then(() => {
        reopened = true;
      });
      await new Promise((resolve) => setTimeout(resolve, 300));
      const early = reopened;
      await closing;
      await reopening;
      return { early, reopened };
    });
    expect(out).toEqual({ early: false, reopened: true });
  }, 30_000);

  it("close idle past their time, and reopen with the conversation as it was", async () => {
    const r = rig();
    const [first, second] = [await r.thread(), await r.thread()];
    const out = await r.in(async (instance) => {
      instance.timing.idleCloseMs = 50;
      await instance.harness.prompt("noted once", { session: first });
      await new Promise((resolve) => setTimeout(resolve, 100));
      await instance.agent.host.pi(second);  // the host's next activity sweeps
      const closed = !instance.agent.host.openThreads().includes(first);
      const again = await instance.harness.prompt("noted twice", { session: first });
      return { closed, text: again.text,
               said: again.messages.flatMap((entry) => (entry.model ?? []) as Message[])
                 .map((message) => message.role).filter((role) => role !== "system") };
    });
    expect(out.closed).toBe(true);
    expect(out.text).toBe("noted");
    // Both inputs and both answers, from the store.
    expect(out.said).toEqual(["user", "assistant", "user", "assistant"]);
  }, 30_000);
});

describe("a store reset mid-run", () => {
  it("fails the Session, which the thread's own job reopens: committed work stands, and the " +
     "unsafe tool cut off comes back interrupted, never run twice", async () => {
    const r = rig();
    const thread = await r.thread();
    const held = gate();
    await r.in(async (instance) => {
      instance.probes.holdGate = held.promise;
      await instance.agent.host.submit(thread, "hold it", { operationId: "op-r" });
      await until(() => instance.probes.holds === 1, "the hold to start");
    });
    // A wait on the run, which the Session's failure must not end.
    const waited = r.in((instance) => instance.agent.host.settled(thread, "op-r"));
    // The store is reset as a deploy resets it: the Session's holder no longer stands.
    const store = await storeOf(r.stub(), thread);
    await runInDurableObject(env.PISTORE.getByName(store), (_instance, state) => {
      state.storage.kv.put("ta:holder", "0".repeat(32));
    });
    // The tool returns; its result's commit is refused, which fails the Session.
    held.release();
    await until(async () => (await storeSubmission(store, { request: "op-r" }))?.status
                            === "done", "the run to settle on the reopened Session");
    // The wait waited again on the reopened Session, and answers how it settled.
    expect(await waited).toMatchObject({ status: "done" });
    const out = await r.in(async (instance) => ({
      holds: instance.probes.holds,
      messages: (await instance.harness.messages({ session: thread }))
        .flatMap((entry) => (entry.model ?? []) as Message[]),
    }));
    expect(out.holds).toBe(1);
    const result = out.messages.find((message) => message.role === "toolResult");
    expect(result).toMatchObject({ isError: true });
    expect(JSON.stringify(result)).toMatch(/interrupt/i);
    expect(out.messages[0]).toMatchObject({ role: "user" });  // the input committed stands
    const notices = await r.in((instance) => instance.parts.notices.list().map((notice) =>
      notice.type));
    expect(notices).toContain("session_failed");
  }, 30_000);
});
