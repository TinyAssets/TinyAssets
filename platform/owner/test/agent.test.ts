/**
 * pi-durable in the owner's object, on faux models (tasks 5, sub-slice A0): PiHarness on
 * the object's own Lifecycle, a turn over the owner's files, replay after an eviction, the
 * cold open, a package pi refuses, the write gate against a transaction of the queue
 * pi's commits use, and a model request's turn as pi's records give it.
 */
import { evictDurableObject, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import type { ConversationId, JsonObject } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import {
  AGENT_SETTINGS, AUTO, CONTEXT_RETENTION_MS, DOCUMENT_RETENTION_MS, writerOf,
} from "../src/agent";
import { INLINE_BYTES } from "../src/files";
import { OUTSIDE_GATE, WriteGate } from "../src/gate";
import { clock, WAKE_LEASE_S } from "../src/ledger";
import { NOTICE_DETAIL, Notices, NOTICES_KEPT } from "../src/notices";
import type { Binding } from "../src/owner-do";
import { DOT, ledgerOn, MCP, MUSE } from "./ledger-support";
import { drainDue, failure, storeOf, storeSql, tablesIn } from "./support";
import {
  type FauxOwnerDO, type MalformedOwnerDO, says, type Spawned, storeScript,
} from "./worker";

const BG = BACKGROUND_CONTEXT;
const text = (value: string): Uint8Array => new TextEncoder().encode(value);

/** An owner of its own, on the faux-model class of its object. */
function agentObject() {
  const name = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(name);
  return {
    name, stub,
    as: (): Binding => ({ owner: name, caller: name, origin: null }),
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
  };
}

/** A model answer that calls tools. */
function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

/** A model's call of a test tool, which it reaches through `ta`. */
function viaTa(name: string, args: JsonObject): FauxContentBlock {
  return fauxToolCall("ta", { op: "call", name, arguments: args });
}

/**
 * The tool results the model was shown, by tool name (a `ta` call's by the name it
 * called): their text, and whether in error.
 */
function results(messages: readonly Message[]): Record<string, { text: string; error: boolean }> {
  const out: Record<string, { text: string; error: boolean }> = {};
  const called = new Map<string, string>();
  for (const message of messages) {
    if (message.role === "assistant") {
      for (const part of message.content) {
        if (part.type === "toolCall" && part.name === "ta" && part.arguments["op"] === "call") {
          called.set(part.id, String(part.arguments["name"]));
        }
      }
    }
    if (message.role === "toolResult") {
      out[called.get(message.toolCallId) ?? message.toolName] = {
        text: message.content.map((part) => (part.type === "text" ? part.text : "")).join(""),
        error: message.isError,
      };
    }
  }
  return out;
}

/** An answer that repeats what the tools returned, so the test can read it back. */
const echo = (messages: Message[]): AssistantMessage => says(JSON.stringify(results(messages)));

async function until(check: () => Promise<boolean>, what: string): Promise<void> {
  for (let tries = 0; tries < 500; tries++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

describe("pi in the owner's object", () => {
  it("opens on a cold object's first call, with no start first, at pi's schema version 2",
     async () => {
    const r = agentObject();
    const first = await r.in(async (instance) => {
      instance.script = [says("hello")];
      return instance.harness.prompt("hi");
    });
    expect([first.status, first.text]).toEqual(["done", "hello"]);
    // pi's tables are the thread's store's alone, at pi's schema version: none in the owner's.
    const store = await storeOf(r.stub, first.session);
    expect((await storeSql(store, "SELECT version FROM pi_durable_schema"))[0]!["version"])
      .toBe(2);
    expect(await tablesIn(r.stub, "pi_")).toEqual([]);
    expect((await storeSql(store, "SELECT name FROM sqlite_master WHERE type = 'table' " +
                                  "AND name LIKE 'pi_%'")).length).toBeGreaterThan(5);
    // Its Session closed idle (an open one holds its store's session, which keeps both in
    // memory) and then evicted, the object is cold again: its first call opens pi and the
    // turn completes.
    await r.in((instance) => instance.agent.host.close(first.session));
    await evictDurableObject(r.stub);
    const again = await r.in(async (instance) => {
      instance.script = [says("hello"), says("again")];
      const { operationId } = await instance.harness.submit("once more");
      return instance.harness.wait(operationId);
    });
    expect([again.status, again.text]).toEqual(["done", "again"]);
  });
});

describe("a turn in the owner's object", () => {
  it("reads and writes the owner's files, as the agent in their history", async () => {
    const r = agentObject();
    await r.stub.fileWrite(r.as(), { path: "/workspace/notes.md", expected_etag: null,
                                     content: text("remember the milk") });
    const answer = await r.in(async (instance) => {
      instance.script = [
        calls(fauxToolCall("read", { path: "notes.md" }),
              fauxToolCall("write", { path: "out.md", content: "milk" })),
        echo,
      ];
      return instance.harness.prompt("summarize my notes");
    });
    expect(answer.status).toBe("done");
    const shown = JSON.parse(answer.text!) as ReturnType<typeof results>;
    expect(shown["read"]!.text).toContain("remember the milk");
    expect([shown["read"]!.error, shown["write"]!.error]).toEqual([false, false]);
    const read = await r.stub.fileRead(r.as(), { path: "/workspace/out.md" });
    expect(new TextDecoder().decode(read.inline)).toBe("milk");
    const writers = async (path: string) =>
      (await r.stub.fileHistory(r.as(), { path, limit: 10 })).changes.map((c) => c.writer);
    expect(await writers("/workspace/out.md")).toEqual(["agent"]);
    expect(await writers("/workspace/notes.md")).toEqual(["owner"]);
  });

  it("writes as the outside client a conversation is stamped for, and a subagent's " +
     "conversation keeps the stamp", async () => {
    const r = agentObject();
    // The client's first verified call records its grant: full, generation 1.
    await r.stub.outsideAgents({ ...r.as(), origin: DOT, source: MCP });
    const stamp = { client: DOT.client, generation: 1, authenticated_at: 1_700_000_000,
                    universe: null, agent: "main" };
    const out = await r.in(async (instance) => {
      const pi = await instance.harness.pi();
      const authority = instance.agent.authority;
      const stamped = (await instance.agent.thread("main", stamp)).thread_id;
      const spawned = (messages: Message[]) => says(results(messages)["spawn"]!.text);
      instance.script = [calls(fauxToolCall("write", { path: "theirs.md", content: "x" }),
                               viaTa("spawn", {})), spawned];
      const theirs = await instance.harness.prompt("write it", { session: stamped });
      // The same tool in the owner's own conversation: no stamp to keep.
      instance.script = [calls(viaTa("spawn", {})), spawned];
      const own = await instance.harness.prompt("spawn one");
      const theirPi = await instance.harness.pi({ session: stamped });
      return {
        theirs, own,
        root: await theirPi.snapshot(authority, 1 as ConversationId, BG),
        child: await theirPi.snapshot(authority, Number(theirs.text) as ConversationId, BG),
        ownChild: await pi.snapshot(authority, Number(own.text) as ConversationId, BG),
      };
    });
    expect([out.theirs.status, out.own.status]).toEqual(["done", "done"]);
    expect(Number(out.theirs.text)).toBeGreaterThan(1);
    // The thread is its Session's root, its stamp written in the root's creating commit.
    expect(out.root).toEqual({ by: "client", stamp });
    expect(out.child).toEqual({ by: "client", stamp });
    expect(out.ownChild).toEqual({ by: "owner" });
    const history = await r.stub.fileHistory(r.as(), { path: "/workspace/theirs.md", limit: 1 });
    expect(history.changes.map((c) => c.writer)).toEqual([`client:${stamp.client}`]);
  });

  it("runs a conversation's tools from its own working directory", async () => {
    const r = agentObject();
    const answer = await r.in(async (instance) => {
      const pi = await instance.harness.pi();
      const deep = await pi.createConversation({
        ownership: { kind: "ownerless" },
        agent: { model: { provider: AUTO.provider, modelId: AUTO.id }, cwd: "/workspace/deep" },
        // The owner's, as a thread of the owner's is made (`OwnerAgent.thread`).
        init: async (tx, id) => {
          Object.assign(await tx.doc(instance.agent.authority, id), { by: "owner" });
        },
      }, BG);
      instance.script = [calls(fauxToolCall("write", { path: "here.md", content: "x" })), echo];
      return instance.harness.prompt("write it", { session: String(deep.id) });
    });
    expect(answer.status).toBe("done");
    const read = await r.stub.fileRead(r.as(), { path: "/workspace/deep/here.md" });
    expect(new TextDecoder().decode(read.inline)).toBe("x");
  });

  it("brings an unsafe tool an eviction cut off back interrupted, never run twice, and " +
     "runs a cut-off read again", async () => {
    const r = agentObject();
    await r.stub.fileWrite(r.as(), { path: "/workspace/a.md", expected_etag: null,
                                     content: text("alpha") });
    const script = [calls(fauxToolCall("read", { path: "a.md" }), viaTa("hold", {})),
                    says("done")];
    const operation = await r.in(async (instance, state) => {
      storeScript(state, script);
      instance.probes.readGate = new Promise(() => undefined);
      instance.probes.holdGate = new Promise(() => undefined);
      return (await instance.harness.submit("look")).operationId;
    });
    await until(() => r.in((instance) => instance.probes.reads === 1
                                          && instance.probes.holds === 1), "both tools to start");
    // The object dies mid-tool, as an eviction or a reset leaves it: no tool returned.
    expect(await r.in((_instance, state) => state.abort("evicted mid-tool"))
      .then(() => "alive", (error: Error) => error.message)).toBe("evicted mid-tool");
    // The wake job is still due: its alarm starts the object again, and pi resumes its work.
    const fresh = env.AGENT.getByName(r.name);
    expect(await runDurableObjectAlarm(fresh)).toBe(true);
    const out = await runInDurableObject(fresh, async (instance: FauxOwnerDO) => ({
      settled: await instance.harness.wait(operation),
      reads: instance.probes.reads, holds: instance.probes.holds,
      shown: results((await instance.harness.messages())
        .flatMap((entry) => (entry.model ?? []) as Message[])),
    }));
    expect([out.settled.status, out.settled.text]).toEqual(["done", "done"]);
    // On the new instance: read, marked replay-safe, ran again; hold never did.
    expect([out.reads, out.holds]).toEqual([1, 0]);
    expect(out.shown["read"]).toEqual({ text: expect.stringContaining("alpha"), error: false });
    expect(out.shown["hold"]!.error).toBe(true);
    expect(out.shown["hold"]!.text).toMatch(/interrupt/i);
  });
});

/** Whether a request is a compaction's summarization, by the system message pi sends it with. */
function compacting(messages: readonly Message[]): boolean {
  return messages.some((message) => (message as { role: string }).role === "system"
    && JSON.stringify(message).includes("context summarization assistant"));
}

/** Every request's turn so far, as the agent read each when it was made. */
function turnsOf(instance: FauxOwnerDO): string[] {
  return [...instance.turns];
}

/** Wait for a submission pi admitted, by its id. */
async function settled(instance: FauxOwnerDO, id: number) {
  const pi = await instance.harness.pi();
  return (await pi.submission(id as never, BG))!.wait(BG);
}

/** Wait for background work, and the input it submitted to its conversation. */
async function backgroundDone(instance: FauxOwnerDO) {
  const pi = await instance.harness.pi();
  await until(async () => instance.probes.background !== null, "background work");
  const task = await pi.waitForTask(instance.probes.background as never, BG);
  const made = (task.state.outcome as { result: Spawned }).result;
  return { ...made, answer: await settled(instance, made.submission) };
}

/** The answers of a conversation tree's tests, by what each conversation was asked. */
function tree(messages: Message[]): AssistantMessage {
  // What was said, not the system prompt, which names the tools.
  const said = JSON.stringify(messages.filter((message) =>
    (message as { role: string }).role !== "system"));
  const asked = (text: string) => said.includes(text);
  return compacting(messages) ? says("## Goal\nsummary")
    : messages.at(-1)?.role === "toolResult" ? says("done")
      : asked("grandchild work") || asked("background work") ? says("answered")
        : asked("child hold") || asked("hold please") ? calls(viaTa("hold", {}))
          : asked("child work") ? calls(viaTa("spawn", { work: "grandchild work" }))
            : asked("spawn and hold")
              ? calls(viaTa("spawn", { work: "child work" }), viaTa("hold", {}))
              : asked("spawn one") ? calls(viaTa("spawn", { work: "child hold" }))
                : asked("start nested") ? calls(viaTa("background", { work: "background work",
                                                                     nested: true }))
                  : asked("start background")
                    ? calls(viaTa("background", { work: "background work" }))
                    : says("noted");
}

/** A held gate and its release, on an instance's probes. */
function gate(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** A user message of about 25,000 estimated tokens: past pi's 20,000 kept by a compaction. */
const LONG = "word ".repeat(20_000);
/** About 87,500: past the faux model's background compaction threshold of 78,848. */
const HUGE = "word ".repeat(70_000);

describe("a model request's turn", () => {
  it("is the input that started its run: a run's requests share it, and the next input's " +
     "run has its own", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done") : calls(viaTa("hold", {})));
      const first = await instance.harness.prompt("one");
      const second = await instance.harness.prompt("two");
      return { first, second, turns: turnsOf(instance) };
    });
    expect([out.first.status, out.second.status]).toEqual(["done", "done"]);
    expect(out.turns).toHaveLength(4);
    expect(out.turns.every((turn) => /^input:[0-9a-f-]{36}$/.test(turn))).toBe(true);
    expect(out.turns[1]).toBe(out.turns[0]);
    expect(out.turns[3]).toBe(out.turns[2]);
    expect(out.turns[2]).not.toBe(out.turns[0]);
  });

  it("keeps the run's turn when an input steers it", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done")
          : JSON.stringify(messages.at(-1)).includes("steer") ? says("steered")
            : calls(viaTa("hold", {})));
      const { operationId } = await instance.harness.submit("work");
      await until(async () => instance.probes.holds === 1, "the hold to start");
      // Joins the running work after the tool round.
      const steered = await instance.harness.submit("steer it", { whenBusy: "steer" });
      held.release();
      const answers = [await instance.harness.wait(operationId),
                       await instance.harness.wait(steered.operationId)];
      return { answers, turns: turnsOf(instance) };
    });
    expect(out.answers.map((answer) => answer.status)).toEqual(["done", "done"]);
    expect(out.turns.length).toBeGreaterThanOrEqual(2);
    expect(new Set(out.turns).size).toBe(1);
  });

  it("is the run's for a compaction the run starts, kept while it runs on past the run",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      let kept: unknown[] = [];
      instance.script = Array.from({ length: 12 }, () => (messages: Message[]) => {
        if (!compacting(messages)) {
          return says("noted");
        }
        kept = state.storage.sql.exec("SELECT turn, owners FROM model_turns").toArray();
        return says("## Goal\nsummary");
      });
      expect((await instance.harness.prompt("early")).status).toBe("done");
      // Past pi's background threshold (32,768 under the blocking one): the run's
      // generation starts a background compaction, which pi runs on past the run.
      const answer = await instance.harness.prompt(HUGE);
      await until(async () => (await instance.harness.messages())
        .some((entry) => entry.kind === "pi.compaction"), "the compaction's summary");
      await until(async () => state.storage.sql.exec("SELECT 1 FROM model_turns").toArray()
        .length === 0, "the turn to end with its compaction");
      return { answer, kept, turns: turnsOf(instance) };
    });
    expect(out.answer.status).toBe("done");
    // The early run's request, then the second run's, and its compaction's summarization
    // on the run's turn, under its caps: the compaction's task keeps the turn while it is
    // live, so it is there when the summarization calls, and goes with it.
    expect(out.turns).toHaveLength(3);
    expect(out.turns[1]).not.toBe(out.turns[0]);
    expect(out.turns[2]).toBe(out.turns[1]);
    expect(out.kept).toContainEqual({ turn: out.turns[1], owners: expect.any(Number) });
  });

  it("is a compaction's own when a door asks for one while a run is busy", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 12 }, () => (messages: Message[]) =>
        compacting(messages) ? says("## Goal\nkeep going")
          : messages.at(-1)?.role === "toolResult" ? says("done")
            : JSON.stringify(messages.at(-1)).includes("work") ? calls(viaTa("hold", {}))
              : says("noted"));
      expect((await instance.harness.prompt("early")).status).toBe("done");
      const held = gate();
      instance.probes.holdGate = held.promise;
      const { operationId } = await instance.harness.submit(`work ${LONG}`);
      await until(async () => instance.probes.holds === 1, "the hold to start");
      const pi = await instance.harness.pi();
      const root = await pi.root(BG);
      // Asked by the owner, not caused by the run: placed at the run's next boundary.
      const asked = root.compact(undefined, BG);
      held.release();
      const answer = await instance.harness.wait(operationId);
      const compaction = await asked;
      const compacted = await pi.waitForTask(compaction, BG);
      return { answer, compaction, compacted, turns: turnsOf(instance),
               thread: await instance.harness.thread() };
    });
    expect(out.answer.status).toBe("done");
    expect(out.compacted.state.outcome.status).toBe("completed");
    const [early, ...rest] = out.turns;
    const own = rest.filter((turn) => turn === `task:${out.thread}:${out.compaction}`);
    const run = rest.filter((turn) => turn !== `task:${out.thread}:${out.compaction}`);
    expect(own).toHaveLength(1);
    expect(run.length).toBeGreaterThanOrEqual(2);
    expect(new Set(run).size).toBe(1);
    expect(run[0]).not.toBe(early);
  });

  it("is the root run's for a subagent's and its subagent's calls", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      instance.script = Array.from({ length: 12 }, () => tree);
      const { operationId } = await instance.harness.submit("spawn and hold");
      // The subagent runs its work, and makes a subagent of its own, which runs too.
      await until(async () => instance.probes.submitted.length === 2, "both subagents' work");
      const below = [await settled(instance, instance.probes.submitted[0]!),
                     await settled(instance, instance.probes.submitted[1]!)];
      held.release();
      const answer = await instance.harness.wait(operationId);
      return { answer, below, turns: turnsOf(instance) };
    });
    expect([out.answer.status, ...out.below.map((answer) => answer.status)])
      .toEqual(["done", "done", "done"]);
    // The root's two requests, the subagent's two and the grandchild's one.
    expect(out.turns).toHaveLength(5);
    expect(new Set(out.turns).size).toBe(1);
  });

  it("is an input's own when it comes from outside any run, even in a subagent's " +
     "conversation", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 12 }, () => tree);
      const pi = await instance.harness.pi();
      instance.probes.holdGate = Promise.resolve();
      expect((await instance.harness.prompt("spawn one")).status).toBe("done");
      await settled(instance, instance.probes.submitted[0]!);
      const from = turnsOf(instance).length;
      const child = instance.probes.spawned as ConversationId;
      const asked = await (await pi.conversation(child, BG))!.submit(
        { type: "input", content: "background work" }, BG);
      const answer = await asked.wait(BG);
      return { answer, before: turnsOf(instance).slice(0, from),
               after: turnsOf(instance).slice(from) };
    });
    expect(out.answer.status).toBe("done");
    expect(new Set(out.before).size).toBe(1);
    expect(out.after).toHaveLength(1);
    expect(out.after[0]).not.toBe(out.before[0]);
  });

  it.each([
    ["background work", "start background"],
    ["background work's own child task", "start nested"],
  ])("keeps %s on the run that started it, after it ended, never a later run",
     async (_what, prompt) => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.backgroundGate = held.promise;
      instance.script = Array.from({ length: 12 }, () => tree);
      const started = await instance.harness.prompt(prompt);
      const spawning = turnsOf(instance);
      const later = await instance.harness.prompt("a later input");
      const laterTurns = turnsOf(instance).slice(spawning.length);
      // The background work submits its work only now, during no run.
      held.release();
      const work = await backgroundDone(instance);
      const workTurns = turnsOf(instance).slice(spawning.length + laterTurns.length);
      return { started, later, work, spawning, laterTurns, workTurns };
    });
    expect([out.started.status, out.later.status, out.work.answer.status])
      .toEqual(["done", "done", "done"]);
    expect(new Set(out.spawning).size).toBe(1);
    expect(out.laterTurns).toHaveLength(1);
    expect(out.laterTurns[0]).not.toBe(out.spawning[0]);
    expect(out.workTurns).toEqual([out.spawning[0]]);
  });

  it("is a subagent's own compaction while nothing runs", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 12 }, () => tree);
      instance.probes.holdGate = Promise.resolve();
      expect((await instance.harness.prompt("spawn one")).status).toBe("done");
      await settled(instance, instance.probes.submitted[0]!);
      const pi = await instance.harness.pi();
      const child = instance.probes.spawned as ConversationId;
      const conversation = (await pi.conversation(child, BG))!;
      await (await conversation.submit({ type: "input", content: LONG }, BG)).wait(BG);
      const compaction = await conversation.compact(undefined, BG);
      const done = await pi.waitForTask(compaction, BG);
      return { compaction, done, turns: turnsOf(instance),
               thread: await instance.harness.thread() };
    });
    expect(out.done.state.outcome.status).toBe("completed");
    expect(out.turns.at(-1)).toBe(`task:${out.thread}:${out.compaction}`);
  });

  it("is a compaction's own while the conversation is idle", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        compacting(messages) ? says("## Goal\nsummary") : says("noted"));
      await instance.harness.prompt("early");
      await instance.harness.prompt(LONG);
      const pi = await instance.harness.pi();
      const root = await pi.root(BG);
      const compaction = await root.compact(undefined, BG);
      const done = await pi.waitForTask(compaction, BG);
      return { compaction, done, turns: turnsOf(instance),
               thread: await instance.harness.thread() };
    });
    expect(out.done.state.outcome.status).toBe("completed");
    expect(out.turns.at(-1)).toBe(`task:${out.thread}:${out.compaction}`);
  });

  it("is never another thread's: two threads' compactions of one task id are two turns",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        compacting(messages) ? says("## Goal\nsummary") : says("noted"));
      const own = await instance.harness.thread();
      // Another conversation of the owner's: a settled run takes no second input.
      const other = (await instance.agent.thread("helper", null, { kind: "converse" }))
        .thread_id;
      const compactions = [];
      for (const session of [own, other]) {
        await instance.harness.prompt("early", { session });
        await instance.harness.prompt(LONG, { session });
        const pi = await instance.harness.pi({ session });
        const compaction = await (await pi.root(BG)).compact(undefined, BG);
        await pi.waitForTask(compaction, BG);
        compactions.push({ session, compaction, turn: turnsOf(instance).at(-1) });
      }
      return compactions;
    });
    // Task ids are per Session, so the two compactions share one; their turns do not.
    expect(out[0]!.compaction).toBe(out[1]!.compaction);
    expect(out.map((one) => one.turn)).toEqual(
      out.map((one) => `task:${one.session}:${one.compaction}`));
    expect(out[0]!.turn).not.toBe(out[1]!.turn);
  });
});

describe("a package pi refuses to install", () => {
  it("is skipped and kept as a notice, and the wakes and files lanes run on", async () => {
    const name = `owner-${crypto.randomUUID()}`;
    const stub = env.MALFORMED.getByName(name);
    const as = (): Binding => ({ owner: name, caller: name, origin: null });
    // Work for both lanes: an upload past its expiry, and a wake claim past its lease.
    await stub.fileUploadBegin(as(), { size: INLINE_BYTES + 1, chunks: ["e".repeat(64)] });
    const t = clock();
    await runInDurableObject(stub, (_instance: MalformedOwnerDO, state) => {
      state.storage.sql.exec("UPDATE file_intents SET expires_at = 1");
      const ledger = ledgerOn(state.storage);
      const agent = ledger.register(MUSE, MCP, { command_center_id: "alpha",
                                                 display_name: "Muse",
                                                 delivery_method: "mcp_event",
                                                 recipe_id: null }).agent_id;
      ledger.post(null, `dm:${agent}`, "c1", "hello", null, t - 10_000);
      const [wake] = ledger.dueOpen(t, 1);
      expect(ledger.claim(wake!.wake_id, t - WAKE_LEASE_S - 10).outcome).toBe("claimed");
    });
    // A new instance re-derives the lanes' jobs from the rows; its alarm opens pi.
    await evictDurableObject(stub);
    await drainDue(stub as never);
    const rows = await runInDurableObject(stub, async (instance: MalformedOwnerDO, state) => {
      const root = await (await instance.harness.pi()).root(BG);
      return {
        intents: state.storage.sql.exec("SELECT * FROM file_intents").toArray(),
        wakes: state.storage.sql.exec("SELECT state FROM ledger_wakes WHERE kind = 'wake'")
          .toArray(),
        tools: (await root.agent(BG)).callable.map((tool) => tool.name).sort(),
        offered: (await root.agent(BG)).tools.map((tool) => tool.name),
        gate: (await root.agent(BG)).extensions.filter((extension) =>
          extension.name === "ta.authority").map((extension) => extension.hooks?.length ?? 0),
      };
    });
    expect(rows.intents).toEqual([]);  // the files lane expired the upload
    expect(rows.wakes).toEqual([{ state: "pending" }]);  // the wakes lane recovered it
    // pi opened without the package, and with every other extension; the model is offered
    // the five tools alone.
    expect(rows.tools).toEqual(["background", "bash", "benign", "early", "edit", "files:usage",
                               "fork",
                               "hold", "nest", "pass", "read", "safe_hold", "sixth", "spawn",
                               "weather_lookup", "widen", "write"]);
    expect(rows.offered).toEqual(["read", "write", "edit", "bash", "ta"]);
    // A package named as the object's gate is refused, and the gate stays the object's.
    expect(rows.gate).toEqual([1]);
    const notices = (await stub.noticeList(as())).notices;
    expect(notices.map((notice) => [notice.type, notice.detail.split(":")[0]]))
      .toContainEqual(["extension_skipped", "pkg"]);
    expect(notices.filter((notice) => notice.type === "extension_skipped")
      .map((notice) => notice.detail).sort()).toEqual([
      expect.stringMatching(/^pkg:broken: Extension pkg:broken has two tools named twice/),
      "pkg:lonely: the tool lonely is not callable by tools, so nothing could call it",
      "pkg:op: a package tool may not take the name ta:catalog",
      "pkg:policy: a package tool may not take the name policy:show",
      "pkg:read: a package tool may not take the name read",
      "pkg:ta: a package tool may not take the name ta",
      "pkg:wrap: a package may not wrap bash",
      "ta.authority: the ta. names are the object's own"]);
    // pi opens again in every new instance (and after any pi failure): the package is
    // refused each time, and noticed once while that notice is kept.
    for (let open = 0; open < 2; open++) {
      expect(await runInDurableObject(env.MALFORMED.getByName(name),
                                      (_instance: MalformedOwnerDO, state) => state.abort("gone"))
        .then(() => "alive", (error: Error) => error.message)).toBe("gone");
      await runInDurableObject(env.MALFORMED.getByName(name),
                               async (instance: MalformedOwnerDO) => {
        await instance.harness.pi();
      });
    }
    const kept = (await env.MALFORMED.getByName(name).noticeList(as())).notices;
    expect(kept.filter((notice) => notice.type === "extension_skipped")).toHaveLength(8);
  });
});

describe("the write gate and the thread stores", () => {
  it("never holds a door's unit behind a thread's commit, which is its store's alone",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      const pi = await instance.harness.pi();
      const root = await pi.root(BG);
      const commit = root.submit({ type: "write", entry: { kind: "test.note", data: 1 } }, BG);
      // The door's unit runs at once: the commit is a store RPC, not a transaction here.
      const door = await instance.collectionWrite(r.as(), { collection: "board", row_id: "t",
                                                            body: "1", expected_etag: "",
                                                            private: false });
      await commit;
      return { door, thread: await instance.harness.thread() };
    });
    expect(out.door).toMatchObject({ row: { row_id: "t" } });
    expect(await tablesIn(r.stub, "pi_")).toEqual([]);
    const store = await storeOf(r.stub, out.thread);
    expect(await storeSql(store, "SELECT json_extract(record, '$.kind') AS kind " +
                                 "FROM pi_entries WHERE json_extract(record, '$.kind') = " +
                                 "'test.note'"))
      .toEqual([{ kind: "test.note" }]);
  });

  it("reports a pi commit that fails as a notice, and pi opens again on the next call",
     async () => {
    const r = agentObject();
    const thread = await r.in(async (instance) => {
      await instance.harness.pi();
      return instance.harness.thread();
    });
    const store = await storeOf(r.stub, thread);
    await storeSql(store, "CREATE TRIGGER test_pi_fails BEFORE UPDATE ON pi_durable_metadata " +
                          "BEGIN SELECT RAISE(ABORT, 'pi commit fails on purpose'); END");
    const failed = await r.in(async (instance) => {
      instance.script = [says("never")];
      return instance.harness.submit("hello")
        .then(() => "committed", (error: Error) => error.message);
    });
    await storeSql(store, "DROP TRIGGER test_pi_fails");
    expect(failed).toMatch(/pi commit fails on purpose/);
    await until(async () => (await r.stub.noticeList(r.as())).notices
      .some((notice) => notice.type === "session_failed"), "the session_failed notice");
    const next = await r.in(async (instance) => {
      instance.script = [says("back")];
      return instance.harness.prompt("again");
    });
    expect([next.status, next.text]).toEqual(["done", "back"]);
  });
});

describe("pi's settings and the owner's notices", () => {
  it("commits progress at most once a second, and starts on the gateway's auto", () => {
    expect(AGENT_SETTINGS).toEqual({
      progress: { partialIntervalMs: 1000, outputIntervalMs: 1000 },
      afterFailedRun: "withdraw",
      restartLimit: 5,
      contextRetentionMs: CONTEXT_RETENTION_MS,
      documentRetentionMs: DOCUMENT_RETENTION_MS,
    });
    // Many Sessions share an isolate: retention well under pi's ten-minute default (F11).
    expect([CONTEXT_RETENTION_MS, DOCUMENT_RETENTION_MS]).toEqual([60_000, 60_000]);
    expect(AUTO).toEqual({ provider: "gateway", id: "auto" });
    expect(writerOf(undefined)).toBe("agent");
    expect(writerOf({ by: "owner" })).toBe("agent");
    expect(writerOf({ by: "none" })).toBe("agent");
  });

  it("settles an input queued behind a failed run, rather than keep it waiting", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      // A run held in its tool, an input queued behind it, then the run's next answer
      // fails: no script holds one.
      let release!: () => void;
      instance.probes.holdGate = new Promise((resolve) => { release = resolve; });
      instance.script = [calls(viaTa("hold", {}))];
      const first = await instance.harness.submit("one");
      await until(async () => instance.probes.holds === 1, "the hold to start");
      const second = await instance.harness.submit("two");
      release();
      return [await instance.harness.wait(first.operationId),
              await instance.harness.wait(second.operationId)];
    });
    expect(out.map((result) => [result.status, result.reason])).toEqual([
      ["unanswered", "model_error"], ["unanswered", "run_failed"]]);
  });

  it("keeps only the newest notices, for the owner alone", async () => {
    const r = agentObject();
    await r.in(async (_instance, state) => {
      const gate = new WriteGate(state.storage);
      const notices = new Notices(gate.storage);
      for (let i = 0; i < NOTICES_KEPT + 5; i++) {
        await gate.write(() => notices.add("task_parked", `${i}: ${"x".repeat(2000)}`, i));
      }
      expect(() => notices.add("x", "y", 0)).toThrow(OUTSIDE_GATE);
    });
    const { notices } = await r.stub.noticeList(r.as());
    expect(notices).toHaveLength(NOTICES_KEPT);
    expect(notices[0]!.detail.startsWith(`${NOTICES_KEPT + 4}: `)).toBe(true);
    expect(notices.at(-1)!.detail.startsWith("5: ")).toBe(true);
    expect(notices[0]!.detail.length).toBe(NOTICE_DETAIL);
    const outside: Binding = { owner: r.name, caller: r.name, origin: MUSE, source: MCP };
    expect((await failure(() => r.stub.noticeList(outside))).message)
      .toBe("only the owner may read its notices");
  });

  it("keeps a standing condition once by its type and its detail as kept, cut", async () => {
    const r = agentObject();
    await r.in(async (_instance, state) => {
      const gate = new WriteGate(state.storage);
      const notices = new Notices(gate.storage);
      // Two packages refused: two notices. Each again: none. A detail past what a notice
      // keeps matches its kept, cut self.
      const long = `pkg:long: ${"y".repeat(NOTICE_DETAIL * 2)}`;
      for (const detail of ["pkg:a: refused", "pkg:b: refused", long, "pkg:a: refused",
                            "pkg:b: refused", long]) {
        await gate.write(() => notices.once("extension_skipped", detail, 1));
      }
    });
    const { notices } = await r.stub.noticeList(r.as());
    expect(notices.map((notice) => notice.detail.slice(0, 5)))
      .toEqual(["pkg:l", "pkg:b", "pkg:a"]);
  });
});
