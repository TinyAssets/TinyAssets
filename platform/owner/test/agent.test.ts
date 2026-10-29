/**
 * pi-durable in the owner's object, on faux models (tasks 5, sub-slice A0): PiHarness on
 * the object's own Lifecycle, a turn over the owner's files, replay after an eviction, the
 * cold open, a package pi refuses, the write gate against a transaction of the queue
 * pi's commits use, and a model request's turn as pi's records give it.
 */
import { evictDurableObject, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import { type ConversationId, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { AGENT_SETTINGS, AUTO, NO_RUN, writerOf } from "../src/agent";
import { INLINE_BYTES } from "../src/files";
import { OUTSIDE_GATE, WriteGate } from "../src/gate";
import { clock, WAKE_LEASE_S } from "../src/ledger";
import { NOTICE_DETAIL, Notices, NOTICES_KEPT } from "../src/notices";
import type { Binding } from "../src/owner-do";
import { ledgerOn, MCP, MUSE } from "./ledger-support";
import { drainDue, failure } from "./support";
import { type FauxOwnerDO, type MalformedOwnerDO, says, storeScript } from "./worker";

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

/** The tool results the model was shown, by tool name: their text, and whether in error. */
function results(messages: readonly Message[]): Record<string, { text: string; error: boolean }> {
  const out: Record<string, { text: string; error: boolean }> = {};
  for (const message of messages) {
    if (message.role === "toolResult") {
      out[message.toolName] = {
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
      return instance.agent.harness.prompt("hi");
    });
    expect([first.status, first.text]).toEqual(["done", "hello"]);
    const version = await r.in((_instance, state) => state.storage.sql.exec<{ version: number }>(
      "SELECT version FROM pi_durable_schema").one().version);
    expect(version).toBe(2);
    // Evicted, the object is cold again: its first call opens pi and the turn completes.
    await evictDurableObject(r.stub);
    const again = await r.in(async (instance) => {
      instance.script = [says("hello"), says("again")];
      const { operationId } = await instance.agent.harness.submit("once more");
      return instance.agent.harness.wait(operationId);
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
      return instance.agent.harness.prompt("summarize my notes");
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
    const stamp = { client: "c".repeat(64), generation: 3, authenticated_at: 1_700_000_000 };
    const out = await r.in(async (instance) => {
      const pi = await instance.agent.harness.pi();
      const authority = instance.agent.authority;
      const stamped = await pi.createConversation({
        ownership: { kind: "ownerless" },
        agent: { model: { provider: AUTO.provider, modelId: AUTO.id } },
        init: async (tx, id) => {
          (await tx.doc(authority, id)).stamp = stamp;
        },
      }, BG);
      const spawned = (messages: Message[]) => says(results(messages)["spawn"]!.text);
      instance.script = [calls(fauxToolCall("write", { path: "theirs.md", content: "x" }),
                               fauxToolCall("spawn", {})), spawned];
      const theirs = await instance.agent.harness.prompt("write it",
                                                         { session: String(stamped.id) });
      // The same tool in the owner's own conversation: no stamp to keep.
      instance.script = [calls(fauxToolCall("spawn", {})), spawned];
      const own = await instance.agent.harness.prompt("spawn one");
      return {
        theirs, own,
        child: await pi.snapshot(authority, Number(theirs.text) as ConversationId, BG),
        ownChild: await pi.snapshot(authority, Number(own.text) as ConversationId, BG),
      };
    });
    expect([out.theirs.status, out.own.status]).toEqual(["done", "done"]);
    expect(Number(out.theirs.text)).toBeGreaterThan(1);
    expect(out.child).toEqual({ stamp });
    expect(out.ownChild?.stamp ?? null).toBeNull();
    const history = await r.stub.fileHistory(r.as(), { path: "/workspace/theirs.md", limit: 1 });
    expect(history.changes.map((c) => c.writer)).toEqual([`client:${stamp.client}`]);
  });

  it("runs a conversation's tools from its own working directory", async () => {
    const r = agentObject();
    const answer = await r.in(async (instance) => {
      const pi = await instance.agent.harness.pi();
      const deep = await pi.createConversation({
        ownership: { kind: "ownerless" },
        agent: { model: { provider: AUTO.provider, modelId: AUTO.id }, cwd: "/workspace/deep" },
      }, BG);
      instance.script = [calls(fauxToolCall("write", { path: "here.md", content: "x" })), echo];
      return instance.agent.harness.prompt("write it", { session: String(deep.id) });
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
    const script = [calls(fauxToolCall("read", { path: "a.md" }), fauxToolCall("hold", {})),
                    says("done")];
    const operation = await r.in(async (instance, state) => {
      storeScript(state, script);
      instance.probes.readGate = new Promise(() => undefined);
      instance.probes.holdGate = new Promise(() => undefined);
      return (await instance.agent.harness.submit("look")).operationId;
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
      settled: await instance.agent.harness.wait(operation),
      reads: instance.probes.reads, holds: instance.probes.holds,
      shown: results((await instance.agent.harness.messages())
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

/** Every request's turn, once the agent has read each. */
async function turnsOf(instance: FauxOwnerDO): Promise<string[]> {
  await until(async () => !instance.turns.includes("pending"), "every request's turn");
  return [...instance.turns];
}

/** A user message of about 25,000 estimated tokens: past pi's 20,000 kept by a compaction. */
const LONG = "word ".repeat(20_000);

describe("a model request's turn", () => {
  it("is one run of the root conversation: every request of a run shares it, and the " +
     "next run has its own", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done") : calls(fauxToolCall("hold", {})));
      const first = await instance.agent.harness.prompt("one");
      const second = await instance.agent.harness.prompt("two");
      const root = await (await instance.agent.harness.pi()).root(BG);
      return { first, second, root: root.id, turns: await turnsOf(instance) };
    });
    expect([out.first.status, out.second.status]).toEqual(["done", "done"]);
    expect(out.turns).toHaveLength(4);
    expect(out.turns.every((turn) => turn.startsWith(`${out.root}/run:`))).toBe(true);
    expect(out.turns[1]).toBe(out.turns[0]);
    expect(out.turns[3]).toBe(out.turns[2]);
    expect(out.turns[2]).not.toBe(out.turns[0]);
  });

  it("goes on through a compaction inside the run, whose summary is a user message",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 12 }, () => (messages: Message[]) =>
        compacting(messages) ? says("## Goal\nkeep going")
          : messages.at(-1)?.role === "toolResult" ? says("done")
            : JSON.stringify(messages.at(-1)).includes("work") ? calls(fauxToolCall("hold", {}))
              : says("noted"));
      expect((await instance.agent.harness.prompt("early")).status).toBe("done");
      let release!: () => void;
      instance.probes.holdGate = new Promise<void>((resolve) => { release = resolve; });
      const { operationId } = await instance.agent.harness.submit(`work ${LONG}`);
      await until(async () => instance.probes.holds === 1, "the hold to start");
      const pi = await instance.agent.harness.pi();
      const root = await pi.root(BG);
      const during = await instance.agent.turnOf(
        (await pi.snapshot(ProviderDoc, root.id, BG))!.sessionId);
      // Asked while the run is busy, it is placed at the run's next boundary.
      const asked = root.compact(undefined, BG);
      release();
      const settled = await instance.agent.harness.wait(operationId);
      const compacted = await pi.waitForTask(await asked, BG);
      const summaries = (await instance.agent.harness.messages())
        .filter((entry) => entry.kind === "pi.compaction").length;
      return { settled, compacted, summaries, during, turns: await turnsOf(instance) };
    });
    expect(out.settled.status).toBe("done");
    expect(out.compacted.state.outcome.status).toBe("completed");
    expect(out.summaries).toBe(1);
    // The early run's one request, then the held run's: its tool call, the compaction's
    // summarization, and the answer after the summary.
    const [early, ...held] = out.turns;
    expect(held.length).toBeGreaterThanOrEqual(3);
    expect(held.every((turn) => turn === out.during)).toBe(true);
    expect(early).not.toBe(out.during);
  });

  it("is the parent's for a subagent's conversation, through pi's owner edge", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      let release!: () => void;
      instance.probes.holdGate = new Promise<void>((resolve) => { release = resolve; });
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done")
          : calls(fauxToolCall("spawn", {}), fauxToolCall("hold", {})));
      const { operationId } = await instance.agent.harness.submit("spawn and hold");
      await until(async () => instance.probes.holds === 1 && instance.probes.spawned !== null,
                  "the subagent's conversation and the hold");
      const pi = await instance.agent.harness.pi();
      const root = await pi.root(BG);
      const session = async (id: ConversationId) => (await pi.snapshot(ProviderDoc, id, BG))!
        .sessionId;
      const child = instance.probes.spawned as ConversationId;
      const indexed = await pi.snapshot(instance.agent.sessions, await session(child), BG);
      const turns = [await instance.agent.turnOf(await session(root.id)),
                     await instance.agent.turnOf(await session(child))];
      release();
      await instance.agent.harness.wait(operationId);
      return { root: root.id, child, indexed, turns };
    });
    expect(out.indexed).toEqual({ conversation: out.child, root: out.root });
    expect(out.turns[1]).toBe(out.turns[0]);
  });

  it("is refused, no_run, with no run to be part of: idle, an unknown session or none",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = [says("hello")];
      await instance.agent.harness.prompt("hi");
      const pi = await instance.agent.harness.pi();
      const root = await pi.root(BG);
      const session = (await pi.snapshot(ProviderDoc, root.id, BG))!.sessionId;
      const refused = (ask: Promise<string>) => ask.then(() => "ran", (error: Error) =>
        error.message);
      return [await refused(instance.agent.turnOf(session)),
              await refused(instance.agent.turnOf("01900000-0000-7000-8000-000000000000")),
              await refused(instance.agent.turnOf(undefined))];
    });
    expect(out).toEqual([NO_RUN, NO_RUN, NO_RUN]);
  });

  it("is a compaction's own while the conversation is idle", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 8 }, () => (messages: Message[]) =>
        compacting(messages) ? says("## Goal\nsummary") : says("noted"));
      await instance.agent.harness.prompt("early");
      await instance.agent.harness.prompt(LONG);
      const pi = await instance.agent.harness.pi();
      const root = await pi.root(BG);
      const compaction = await root.compact(undefined, BG);
      const settled = await pi.waitForTask(compaction, BG);
      return { root: root.id, compaction, settled, turns: await turnsOf(instance) };
    });
    expect(out.settled.state.outcome.status).toBe("completed");
    expect(out.turns.at(-1)).toBe(`${out.root}/compaction:${out.compaction}`);
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
      const root = await (await instance.agent.harness.pi()).root(BG);
      return {
        intents: state.storage.sql.exec("SELECT * FROM file_intents").toArray(),
        wakes: state.storage.sql.exec("SELECT state FROM ledger_wakes WHERE kind = 'wake'")
          .toArray(),
        tools: (await root.agent(BG)).tools.map((tool) => tool.name).sort(),
      };
    });
    expect(rows.intents).toEqual([]);  // the files lane expired the upload
    expect(rows.wakes).toEqual([{ state: "pending" }]);  // the wakes lane recovered it
    // pi opened without the package, and with every other extension.
    expect(rows.tools).toEqual(["bash", "edit", "hold", "read", "spawn", "write"]);
    const notices = (await stub.noticeList(as())).notices;
    expect(notices.map((notice) => [notice.type, notice.detail.split(":")[0]]))
      .toContainEqual(["extension_skipped", "pkg"]);
    expect(notices.find((notice) => notice.type === "extension_skipped")!.detail)
      .toMatch(/^pkg:broken: Extension pkg:broken has two tools named twice/);
    // pi opens again in every new instance (and after any pi failure): the package is
    // refused each time, and noticed once while that notice is kept.
    for (let open = 0; open < 2; open++) {
      expect(await runInDurableObject(env.MALFORMED.getByName(name),
                                      (_instance: MalformedOwnerDO, state) => state.abort("gone"))
        .then(() => "alive", (error: Error) => error.message)).toBe("gone");
      await runInDurableObject(env.MALFORMED.getByName(name),
                               async (instance: MalformedOwnerDO) => {
        await instance.agent.harness.pi();
      });
    }
    const kept = (await env.MALFORMED.getByName(name).noticeList(as())).notices;
    expect(kept.filter((notice) => notice.type === "extension_skipped")).toHaveLength(1);
  });
});

describe("the write gate and pi's transactions", () => {
  it("is the queue pi's commits hold: a door begun during one waits behind it", async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      const pi = await instance.agent.harness.pi();
      const root = await pi.root(BG);
      // Any adapter over the object's storage reaches the one queue chord keeps for it.
      const queue = (new DurableObjectSqliteDatabase(state.storage) as unknown as
        { queue: { pending: number } }).queue;
      const entries = () => state.storage.sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM pi_entries").one().n;
      const before = entries();
      const commit = root.submit({ type: "write", entry: { kind: "test.note", data: 1 } }, BG);
      for (let i = 0; i < 1000 && queue.pending === 0; i++) {
        await Promise.resolve();
      }
      const held = queue.pending;
      // The door's unit joins the same queue at once, behind pi's commit.
      const door = instance.collectionWrite(r.as(), { collection: "board", row_id: "t",
                                                      body: "1", expected_etag: "",
                                                      private: false });
      const queued = queue.pending;
      const seen = await new WriteGate(state.storage).write(() => entries());
      await Promise.all([commit, door]);
      return { held, queued, seen, before };
    });
    expect([out.held, out.queued]).toEqual([1, 2]);
    expect(out.seen).toBe(out.before + 1);  // a unit queued then ran after pi's commit
  });

  it("keeps a door's write made inside a transaction of that queue, which rolls back a " +
     "direct write with it", async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      const sql = state.storage.sql;
      sql.exec("CREATE TABLE test_probe (n INTEGER)");
      let door: Promise<unknown> = Promise.resolve(null);
      // A commit as pi's adapter makes one: rows through the transaction's handle, awaits
      // between statements, and work running in the object meanwhile, then a failure that
      // rolls the transaction back.
      const failed = await new DurableObjectSqliteDatabase(state.storage)
        .transaction(async (tx) => {
          await tx.run("INSERT INTO test_probe VALUES (1)");
          sql.exec("INSERT INTO test_probe VALUES (2)");  // a direct write, past the gate
          door = instance.collectionWrite(r.as(), { collection: "board", row_id: "t",
                                                    body: "kept", expected_etag: "",
                                                    private: false });
          await tx.run("INSERT INTO test_probe VALUES (3)");
          throw new Error("the commit fails");
        }).then(() => null, (error: Error) => error.message);
      return { failed, door: await door,
               probe: sql.exec("SELECT n FROM test_probe").toArray(),
               board: sql.exec("SELECT row_id, body FROM collection_rows").toArray() };
    });
    expect(out.failed).toBe("the commit fails");
    expect(out.probe).toEqual([]);  // the commit's rows and the direct write: rolled back
    expect(out.door).toMatchObject({ row: { row_id: "t" } });
    expect(out.board).toEqual([{ row_id: "t", body: "kept" }]);
  });

  it("reports a pi commit that fails as a notice, and pi opens again on the next call",
     async () => {
    const r = agentObject();
    const failed = await r.in(async (instance, state) => {
      const sql = state.storage.sql;
      await instance.agent.harness.pi();
      sql.exec("CREATE TRIGGER test_pi_fails BEFORE UPDATE ON pi_durable_metadata " +
               "BEGIN SELECT RAISE(ABORT, 'pi commit fails on purpose'); END");
      instance.script = [says("never")];
      const outcome = await instance.agent.harness.submit("hello")
        .then(() => "committed", (error: Error) => error.message);
      sql.exec("DROP TRIGGER test_pi_fails");
      return outcome;
    });
    expect(failed).toMatch(/pi commit fails on purpose/);
    await until(async () => (await r.stub.noticeList(r.as())).notices
      .some((notice) => notice.type === "session_failed"), "the session_failed notice");
    const next = await r.in(async (instance) => {
      instance.script = [says("back")];
      return instance.agent.harness.prompt("again");
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
    });
    expect(AUTO).toEqual({ provider: "gateway", id: "auto" });
    expect(writerOf(undefined)).toBe("agent");
    expect(writerOf({ stamp: null })).toBe("agent");
  });

  it("settles an input queued behind a failed run, rather than keep it waiting", async () => {
    const r = agentObject();
    const out = await r.in(async (instance) => {
      // A run held in its tool, an input queued behind it, then the run's next answer
      // fails: no script holds one.
      let release!: () => void;
      instance.probes.holdGate = new Promise((resolve) => { release = resolve; });
      instance.script = [calls(fauxToolCall("hold", {}))];
      const first = await instance.agent.harness.submit("one");
      await until(async () => instance.probes.holds === 1, "the hold to start");
      const second = await instance.agent.harness.submit("two");
      release();
      return [await instance.agent.harness.wait(first.operationId),
              await instance.agent.harness.wait(second.operationId)];
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
