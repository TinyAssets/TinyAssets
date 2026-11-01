/**
 * The conversation doors (tasks 5, A6; pi audit F10), in workerd on faux models: pi's own
 * `reset`, a manual `compact` within its per-thread cap, a fork at an entry as a new working
 * thread with the history up to it, a queued input withdrawn by `abortSubmission`, and the
 * task graph; each held to the caller's own threads and to its door class under a
 * suspension; and the per-agent model pin from an agent's `AGENTS.md` front matter.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, fauxToolCall, type Message,
} from "@earendil-works/pi-ai";
import {
  AgentDoc, type EntryRecord, ROOT_CONVERSATION_ID,
} from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { MODEL_TOOLS } from "../src/authority";
import type { Origin } from "../src/ledger";
import { agentFile, type Binding, NO_THREAD } from "../src/owner-do";
import { ALREADY_PLACED, COMPACTIONS_FULL } from "../src/thread-host";
import { THREADS_WORKING } from "../src/threads";
import { until } from "./gateway-support";
import { DOT, MCP, MUSE, signedIn } from "./ledger-support";
import { failure } from "./support";
import { type FauxOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const ROOT = ROOT_CONVERSATION_ID;

/** A user message's text: the envelope's content when the text is one. */
function textOf(message: Message): string {
  const text = typeof message.content === "string" ? message.content
    : message.content.map((part) => (part.type === "text" ? part.text : "")).join("");
  try {
    const envelope = JSON.parse(text) as { untrusted?: boolean; content?: string };
    return envelope.untrusted === true ? envelope.content ?? "" : text;
  } catch {
    return text;
  }
}

/** The model: `hold please` holds in `ta`'s `hold`; a summary request is answered; else echo. */
function reply(messages: Message[]): AssistantMessage {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says("held");
  }
  const asked = last === undefined ? "" : textOf(last);
  if (asked === "hold please") {
    return fauxAssistantMessage([fauxToolCall("ta", { op: "call", name: "hold", arguments: {} })],
                                { stopReason: "toolUse" });
  }
  return says(`echo: ${asked}`);
}

async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: Origin | null = null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const { session: cookie } = await stub.sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => ({ owner, caller: owner, origin: null, session: cookie });
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
    runInDurableObject(stub, act);
  await inside((instance) => {
    instance.script = Array.from({ length: 200 }, () => reply);
  });
  let sends = 0;
  const say = (message: string, origin: Origin | null = null, wait = 20_000) =>
    stub.agentSubmit(bind(origin), { message, client_send_id: `s-${++sends}`, wait_ms: wait });
  /** A thread's root's active transcript, as `kind: text` lines. */
  const lines = (thread: string) => inside(async (instance) =>
    (await instance.harness.messages({ session: thread })).flatMap((entry: EntryRecord) =>
      (entry.model ?? []).filter((message) => message.role === "user"
                                                || message.role === "assistant")
        .map((message) => `${message.role}: ${textOf(message)}`)));
  const platform: Binding = { owner, caller: "platform", origin: null };
  return { owner, stub, bind, session, in: inside, say, lines, platform };
}

type Rig = Awaited<ReturnType<typeof rig>>;

/** The entry ids of a thread's root's transcript, in order. */
function entryIds(r: Rig, thread: string): Promise<number[]> {
  return r.in(async (instance) =>
    (await instance.harness.messages({ session: thread })).map((entry) => Number(entry.id)));
}

describe("reset", () => {
  it("starts a new context, from a handoff when given; older entries stay stored", async () => {
    const r = await rig();
    const { thread_id: thread } = await r.say("one");
    await r.say("two");
    expect(await r.stub.agentReset(r.bind(), { thread_id: thread })).toEqual(
      { thread_id: thread, reset: true });
    expect(await r.lines(thread)).toEqual([]);
    await r.say("three");
    expect(await r.lines(thread)).toEqual(["user: three", "assistant: echo: three"]);
    await r.stub.agentReset(r.bind(), { thread_id: thread, handoff: "we were counting" });
    expect(await r.lines(thread)).toEqual(["user: we were counting"]);
    // Every entry is still in storage.
    expect(await r.in(async (instance) => (await (await instance.agent.pi(thread))
      .conversation(ROOT, BG))!.entries({}, 50, undefined, BG).then((page) => page.items.length)))
      .toBeGreaterThan(6);
  });
});

describe("compact", () => {
  it("is pi's manual compaction, at most THREAD_MANUAL_COMPACTIONS live a thread", async () => {
    const r = await rig();
    // Enough context past pi's kept recent tokens that a compaction has something to summarize.
    const { thread_id: thread } = await r.say("a".repeat(30_000));
    await r.say("b".repeat(30_000));
    await r.say("c".repeat(30_000));
    // The summary request waits on a gate, so the first compaction stays live.
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await r.in((instance) => {
      instance.script = Array.from({ length: 200 }, () =>
        ((messages: Message[]) => held.then(() => reply(messages))) as never);
    });
    const first = await r.stub.agentCompact(r.bind(), { thread_id: thread,
                                                        instructions: "keep the numbers" });
    expect(first.thread_id).toBe(thread);
    expect(first.task_id).toBeGreaterThan(0);
    const refused = await failure(() => r.stub.agentCompact(r.bind(), { thread_id: thread }));
    expect(refused.message).toBe(COMPACTIONS_FULL);
    release();
    await r.in(async (instance) => {
      await (await instance.agent.pi(thread)).waitForTask(first.task_id as never, BG);
    });
    await r.in((instance) => {
      instance.script = Array.from({ length: 200 }, () => reply);
    });
    // Once it ended, another may run.
    const second = await r.stub.agentCompact(r.bind(), { thread_id: thread });
    expect(second.task_id).toBeGreaterThan(first.task_id);
  });

  it("counts two at once against the one place, the one being admitted too", async () => {
    const r = await rig();
    const { thread_id: thread } = await r.say("a".repeat(30_000));
    await r.say("b".repeat(30_000));
    await r.say("c".repeat(30_000));
    await r.in((instance) => {
      instance.script = Array.from({ length: 200 }, () =>
        ((_messages: Message[]) => new Promise(() => undefined)) as never);
    });
    // Both asked in one turn of the instance: each reads the live list before either commits.
    const both = await r.in((instance) => Promise.allSettled([
      instance.agent.host.compact(thread, undefined),
      instance.agent.host.compact(thread, undefined)]));
    expect(both.filter((one) => one.status === "fulfilled").length).toBe(1);
    expect(both.flatMap((one) => (one.status === "rejected" ? [(one.reason as Error).message]
      : []))).toEqual([COMPACTIONS_FULL]);
  });
});

describe("fork at an entry", () => {
  it("makes a working thread with the history up to the entry, then its first input, the " +
     "model offered the five tools", async () => {
    const r = await rig();
    const { thread_id: source } = await r.say("one");
    await r.say("two");
    const lines = await r.lines(source);
    const at = await r.in(async (instance) => Number((await instance.harness.messages(
      { session: source })).find((entry) => entry.model?.some((message) =>
      message.role === "assistant" && textOf(message) === "echo: one"))!.id));
    const forked = await r.stub.agentFork(r.bind(), {
      thread_id: source, entry_id: at, message: "edited", client_send_id: "f-1",
      wait_ms: 20_000 });
    expect(forked.status).toBe("done");
    expect(forked.thread_id).not.toBe(source);
    expect(await r.lines(forked.thread_id)).toEqual([
      "user: one", "assistant: echo: one", "user: edited", "assistant: echo: edited"]);
    expect(await r.lines(source)).toEqual(lines);  // the source is untouched
    expect(await r.in((instance) => instance.offered.at(-1)!.slice().sort()))
      .toEqual([...MODEL_TOOLS].sort());
    const row = await r.in((_instance, state) => state.storage.sql.exec<{ kind: string;
      client: string | null }>("SELECT kind, client FROM agent_threads WHERE thread_id = ?",
                               forked.thread_id).one());
    expect(row).toEqual({ kind: "fork", client: null });
    // It goes on by its id.
    const more = await r.stub.agentSubmit(r.bind(), { message: "more", client_send_id: "f-2",
                                                      thread_id: forked.thread_id,
                                                      wait_ms: 20_000 });
    expect([more.thread_id, more.text]).toEqual([forked.thread_id, "echo: more"]);
  });

  it("takes the caller's own authority, a client's stamp for a client's fork", async () => {
    const r = await rig();
    const { thread_id: source } = await r.say("mine", MUSE);
    const ids = await entryIds(r, source);
    const forked = await r.stub.agentFork(r.bind(MUSE), {
      thread_id: source, entry_id: ids[0]!, message: "again", client_send_id: "f",
      wait_ms: 20_000 });
    const stamp = await r.in(async (instance) => {
      const row = instance.parts.threads.get(forked.thread_id)!;
      return [row.client, row.generation, row.kind,
              (await (await instance.agent.pi(forked.thread_id)).snapshot(
                instance.agent.authority, ROOT, BG))?.by];
    });
    expect(stamp).toEqual([MUSE.client, 1, "fork", "client"]);
    expect(forked.status).toBe("done");
  });

  it("counts against the working threads: refused past them, leaving nothing", async () => {
    const r = await rig();
    const { thread_id: source } = await r.say("one");
    const ids = await entryIds(r, source);
    const before = await r.in((_i, state) => state.storage.sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM agent_threads").one().n);
    await r.in((instance) => {
      instance.timing.workingMax = 0;
    });
    const refused = await failure(() => r.stub.agentFork(r.bind(), {
      thread_id: source, entry_id: ids[0]!, message: "edited", client_send_id: "f" }));
    expect(refused.message).toBe(THREADS_WORKING);
    await until(async () => (await r.in((_i, state) => state.storage.sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM agent_threads").one().n)) === before, "the fork dropped");
  });

  it("refuses an entry the thread's root does not show", async () => {
    const r = await rig();
    const { thread_id: source } = await r.say("one");
    const refused = await failure(() => r.stub.agentFork(r.bind(), {
      thread_id: source, entry_id: 999_999, message: "edited", client_send_id: "f" }));
    expect(refused.message).not.toBe("");
    expect((await failure(() => r.stub.agentFork(r.bind(), {
      thread_id: source, entry_id: 0, message: "edited", client_send_id: "f" }))).name)
      .toBe("Invalid");
  });
});

describe("withdraw", () => {
  it("withdraws a queued input, and refuses one already placed", async () => {
    const r = await rig();
    await r.say("one");
    await r.in((instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
    });
    const running = await r.say("hold please", null, 0);
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    const queued = await r.say("later", null, 0);
    expect(queued.status).toBe("running");
    expect(await r.stub.agentWithdraw(r.bind(), { operation_id: queued.operation_id }))
      .toEqual({ operation_id: queued.operation_id, withdrawn: true });
    expect((await r.stub.agentStatus(r.bind(), { operation_id: queued.operation_id })).status)
      .toBe("unanswered");
    const placed = await failure(() => r.stub.agentWithdraw(r.bind(),
      { operation_id: running.operation_id }));
    expect(placed.message).toBe(ALREADY_PLACED);
    // Withdrawing again withdraws nothing.
    expect((await r.stub.agentWithdraw(r.bind(), { operation_id: queued.operation_id }))
      .withdrawn).toBe(false);
  });
});

describe("the task graph", () => {
  it("reads the thread's live tasks", async () => {
    const r = await rig();
    const { thread_id: thread } = await r.say("one");
    expect((await r.stub.agentTaskGraph(r.bind(), { thread_id: thread })).graph)
      .toEqual({ tasks: {} });
    await r.in((instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
    });
    await r.say("hold please", null, 0);
    await until(() => r.in((instance) => instance.probes.holds === 1), "the hold to start");
    const { graph } = await r.stub.agentTaskGraph(r.bind(), { thread_id: thread });
    const kinds = new Set(Object.values(graph.tasks).map((task) => task.kind));
    expect(kinds).toEqual(new Set(["pi.generation", "pi.tool"]));
  });
});

describe("each door's authority and admission", () => {
  it("refuses another caller's thread alike, for every door", async () => {
    const r = await rig();
    const { thread_id: owners } = await r.say("one");
    const { thread_id: muses } = await r.say("mine", MUSE);
    await r.stub.outsideAgents(r.bind(DOT));
    const calls = (binding: Binding, thread: string) => [
      () => r.stub.agentReset(binding, { thread_id: thread }),
      () => r.stub.agentCompact(binding, { thread_id: thread }),
      () => r.stub.agentFork(binding, { thread_id: thread, entry_id: 1, message: "x",
                                        client_send_id: "x" }),
      () => r.stub.agentTaskGraph(binding, { thread_id: thread }),
    ];
    for (const [binding, thread] of [[r.bind(DOT), muses], [r.bind(DOT), owners],
                                     [r.bind(MUSE), owners], [r.bind(), muses]] as const) {
      for (const call of calls(binding, thread)) {
        expect((await failure(call)).message).toBe(NO_THREAD);
      }
    }
    const { operation_id } = await r.say("again", MUSE);
    expect((await failure(() => r.stub.agentWithdraw(r.bind(DOT), { operation_id }))).message)
      .toBe("no such operation");
  });

  it("holds each door to its class: billing refuses work and leaves reads and reductions, " +
     "abuse refuses every one", async () => {
    const r = await rig();
    const { thread_id: thread, operation_id } = await r.say("one");
    const ids = await entryIds(r, thread);
    const doors = {
      reset: () => r.stub.agentReset(r.bind(), { thread_id: thread }),
      compact: () => r.stub.agentCompact(r.bind(), { thread_id: thread }),
      fork: () => r.stub.agentFork(r.bind(), { thread_id: thread, entry_id: ids[0]!,
                                               message: "x", client_send_id: "x" }),
      withdraw: () => r.stub.agentWithdraw(r.bind(), { operation_id }),
      graph: () => r.stub.agentTaskGraph(r.bind(), { thread_id: thread }),
    };
    await r.stub.admissionSet(r.platform, { reason: "billing", suspended: true });
    const outcome = async () => Object.fromEntries(await Promise.all(Object.entries(doors)
      .map(async ([name, call]) => [name, await call().then(() => "ok",
        (error: Error) => error.message.split(":")[0])])));
    expect(await outcome()).toEqual({ reset: "owner_suspended", compact: "owner_suspended",
                                      fork: "owner_suspended", withdraw: "ok", graph: "ok" });
    await r.stub.admissionSet(r.platform, { reason: "abuse", suspended: true });
    expect(new Set(Object.values(await outcome()))).toEqual(new Set(["owner_suspended"]));
  });

  it("refuses a scoped client's work door where its scope does not hold", async () => {
    const r = await rig();
    const { thread_id: thread } = await r.say("mine", MUSE);
    const held = (await r.stub.grantList(r.bind())).clients.find((c) => c.client === MUSE.client)!;
    // A scope change drops the client's threads: the thread is gone for it.
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
                                         scopes: [{ universe: "alpha", agent: "main",
                                                    capability: "read_graph" }],
                                         name: "", expected_revision: held.revision });
    expect((await failure(() => r.stub.agentReset(r.bind(MUSE), { thread_id: thread })))
      .message).toBe(NO_THREAD);
  });
});

describe("the per-agent model pin", () => {
  it("sets pi.agent.model in the root's creating commit from AGENTS.md front matter", () => {
    expect(agentFile("p", "---\nmodel: pinned-1\n---\nBe brief.")).toEqual({
      instructions: "Be brief.", model: { provider: "gateway", modelId: "pinned-1" } });
    expect(agentFile("p", "Be brief.")).toEqual({ instructions: "Be brief." });
    expect(agentFile("p", "---\nmodel: x\n---\n")).toEqual({
      model: { provider: "gateway", modelId: "x" } });
    for (const bad of ["---\nmodel: a\n", "---\ntools: read\n---\n", "---\nmodel: a\nmodel: b\n---\n",
                       "---\nmodel: a b\n---\n"]) {
      expect(() => agentFile("p", bad)).toThrow(/front matter/);
    }
  });

  it("starts a pinned agent's thread on its model, and an unpinned one on auto", async () => {
    const r = await rig();
    await r.stub.fileWrite(r.session(), {
      path: "/workspace/agents/pinned/AGENTS.md", expected_etag: null,
      content: new TextEncoder().encode("---\nmodel: auto\n---\nBe brief.") });
    const pinned = await r.stub.agentSubmit(r.bind(), { message: "hi", client_send_id: "p",
                                                        agent_id: "pinned", wait_ms: 20_000 });
    const model = (thread: string) => r.in(async (instance) =>
      (await (await instance.agent.pi(thread)).snapshot(AgentDoc, ROOT, BG)));
    expect(await model(pinned.thread_id)).toMatchObject({
      model: { provider: "gateway", modelId: "auto" }, instructions: "Be brief." });
    expect(pinned.text).toBe("echo: hi");
    await r.stub.fileWrite(r.session(), {
      path: "/workspace/agents/elsewhere/AGENTS.md", expected_etag: null,
      content: new TextEncoder().encode("---\nmodel: another\n---\n") });
    const other = await r.stub.agentSubmit(r.bind(), { message: "hi", client_send_id: "q",
                                                       agent_id: "elsewhere", wait_ms: 20_000 });
    expect(await model(other.thread_id)).toMatchObject({
      model: { provider: "gateway", modelId: "another" } });
    expect(other.status).toBe("unanswered");  // a model the catalog lacks fails loudly
  });
});
