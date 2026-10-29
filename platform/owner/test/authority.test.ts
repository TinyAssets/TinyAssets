/**
 * Outside-client authority over the agent, the ending of work and the work runner (tasks
 * 5, sub-slice A2), in workerd on faux models: the `ta.authority` gate on a scoped,
 * full and revoked client's tool calls, nested ones included; the stamp inherited by a
 * subagent and a fork; a revoke raced against a tool call through the held queue; a grant
 * change and a policy write ending live turns, views and waits; the `work` lane's runner;
 * status from pi's receipts; and an aborted call's reservation settled through `usage`.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import { type ConversationId, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { WAIT_ENDED } from "../src/agent";
import {
  AUTHORITY_CHANGED, AUTHORITY_RULE, capabilityOf, NO_SHELL, NOT_GRANTED, type Stamped,
  stamped,
} from "../src/authority";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import { type Binding, workRunner } from "../src/owner-do";
import { Policy } from "../src/policy";
import { Scheduled, type WorkTarget } from "../src/scheduled";
import { Threads } from "../src/threads";
import { gated, until } from "./gateway-support";
import { DOT, MCP, MUSE, signedIn } from "./ledger-support";
import { drainDue, failure } from "./support";
import { type FauxOwnerDO, type GatedOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const text = (value: string): Uint8Array => new TextEncoder().encode(value);
/** A blocked call's result as the model sees it, and its diagnostic as a program does. */
const blocked = (reason: string) => `<harness>\n[error] Tool call blocked: ${reason}\n</harness>`;
const blockedSaid = (reason: string) => `Tool call blocked: ${reason}`;

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

/**
 * The model's answers in every conversation, by what it was last asked; after a tool
 * round, the round's results, which the test reads back.
 */
function byAsk(messages: Message[]): AssistantMessage {
  if (messages.at(-1)?.role === "toolResult") {
    return says(JSON.stringify(results(messages)));
  }
  const last = [...messages].reverse().find((message) => message.role === "user");
  const asked = JSON.stringify(last ?? "");
  const answer = ASKS.find(([what]) => asked.includes(what));
  return answer === undefined ? says("noted") : calls(...answer[1]);
}

const EDIT = fauxToolCall("edit", { path: "notes.md",
                                    edits: [{ oldText: "milk", newText: "cream" }] });
/** What each ask calls, the first that matches. */
const ASKS: readonly [string, FauxContentBlock[]][] = [
  ["hold please", [fauxToolCall("hold", {})]],
  ["go scoped", [fauxToolCall("read", { path: "notes.md" }), EDIT,
                 fauxToolCall("write", { path: "out.md", content: "x" }),
                 fauxToolCall("bash", { command: "ls" }), fauxToolCall("pass", {})]],
  ["write it", [fauxToolCall("write", { path: "mine.md", content: "x" }), EDIT]],
  ["write again", [fauxToolCall("write", { path: "again.md", content: "x" })]],
  ["write raced", [fauxToolCall("write", { path: "raced.md", content: "x" })]],
  ["spawn hold", [fauxToolCall("spawn", { work: "hold please" })]],
  ["spawn one", [fauxToolCall("spawn", {})]],
  ["nest read", [fauxToolCall("nest", { name: "read", arguments: { path: "notes.md" } })]],
  ["nest write", [fauxToolCall("nest", { name: "write",
                                         arguments: { path: "nested.md", content: "x" } })]],
  ["start background", [fauxToolCall("background", { work: "background" })]],
];

/** A held gate and its release. */
function gate(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** Hold the queue every unit and pi commit waits on until `release` is called. */
function holdQueue(state: DurableObjectState): { release: () => void; done: Promise<void> } {
  const held = gate();
  const done = new DurableObjectSqliteDatabase(state.storage).transaction(async () => {
    await held.promise;
  });
  return { release: held.release, done };
}

/** An owner of its own on the faux-model class, signed in, and the binder's calls on it. */
async function agentRig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: Origin | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin, ...(origin === null ? {} : { source: MCP }), ...extra });
  const { session: cookie } = await stub.sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => bind(null, { session: cookie });
  return {
    owner, stub, bind, session, at,
    /** The client's first verified call records its grant: full, generation 1. */
    admit: async (origin: Origin) => {
      await stub.outsideAgents(bind(origin, { authenticated_at: at }));
    },
    /** The owner scopes the client to `capabilities` in universe `alpha`, as agent `main`. */
    scoped: async (origin: Origin, capabilities: string[]) => {
      const row = (await stub.grantList(bind(null))).clients
        .find((client) => client.client === origin.client);
      return (await stub.grantSet(session(), {
        client: origin.client, source: "mcp", mode: "scoped", name: "",
        scopes: capabilities.map((capability) => ({ universe: "alpha", agent: "main",
                                                    capability })),
        expected_generation: row?.generation ?? 0 })).generation;
    },
    stampOf: (origin: Origin, generation: number): Stamped => ({
      client: origin.client, generation, authenticated_at: at, universe: "alpha", agent: "main",
    }),
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
  };
}

/** A thread made for `stamp`, its pi session, and the model answering by `byAsk`. */
async function threadFor(instance: FauxOwnerDO, stamp: Stamped | null) {
  instance.script = Array.from({ length: 40 }, () => byAsk);
  const thread = await instance.agent.thread("main", stamp);
  return { thread, session: String(thread.conversation_id) };
}

/** A conversation's live tee session: its pi provider identity. */
async function teeSession(instance: FauxOwnerDO, id: number): Promise<string> {
  const pi = await instance.agent.harness.pi();
  return (await pi.snapshot(ProviderDoc, id as ConversationId, BG))!.sessionId;
}

const shown = (answer: { text?: string }) =>
  JSON.parse(answer.text!) as ReturnType<typeof results>;

describe("the authority gate's capability map", () => {
  it("names read_files for read, write_files for write and edit, none to grant for the " +
     "shell, and no capability for anything else, ta's until A3 fills its map", () => {
    const of = (name: string, args: unknown = {}) => capabilityOf({ name, arguments: args });
    expect([of("read"), of("write"), of("edit"), of("bash")])
      .toEqual(["read_files", "write_files", "write_files", null]);
    expect([of("hold"), of("toString"), of("__proto__"), of("ta", { op: "catalog" }),
            of("ta", { op: "call", name: "policy:show" }), of("ta", null)])
      .toEqual([undefined, undefined, undefined, undefined, undefined, undefined]);
  });

  it("takes a stamp only whole: a client, its generation, sign-in, universe and agent", () => {
    const good = { client: MUSE.client, generation: 2, authenticated_at: null,
                   universe: "alpha", agent: "main" };
    expect(stamped(good)).toEqual(good);
    expect(stamped(null)).toBeNull();
    for (const bad of [{ ...good, extra: 1 }, { ...good, generation: 0 },
                       { ...good, client: "x" }, { ...good, agent: "*" },
                       { ...good, universe: "" }, { ...good, authenticated_at: -1 }]) {
      expect(() => stamped(bad)).toThrow(/a stamp is a client/);
    }
  });
});

describe("a scoped client's conversation", () => {
  it("reads with read_files, and is refused a write without write_files, the shell and a " +
     "tool with no capability, each with a refusal receipt", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("remember the milk") });
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["read_files"]);
    const refusals = async () => (await r.stub.policyReceipts(r.bind(null))).receipts
      .filter((receipt) => receipt.rule === AUTHORITY_RULE);
    const answer = await r.in(async (instance) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, generation));
      return instance.agent.harness.prompt("go scoped", { session });
    });
    expect(answer.status).toBe("done");
    const tools = shown(answer);
    expect(tools["read"]).toEqual({ text: expect.stringContaining("milk"), error: false });
    expect(tools["write"]).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect(tools["edit"]).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect(tools["bash"]).toEqual({ text: blocked(NO_SHELL), error: true });
    expect(tools["pass"]).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect((await r.stub.fileStat(r.bind(null), { path: "/workspace/out.md" })).entry).toBeNull();
    expect((await refusals()).map((receipt) => [receipt.kind, receipt.decision,
                                               receipt.outcome]))
      .toEqual(Array.from({ length: 4 }, () => ["refusal", "deny", "refused"]));
  });

  it("writes and edits with write_files, as itself, only where its grant's universe and " +
     "agent are", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("remember the milk") });
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["write_files"]);
    const out = await r.in(async (instance) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, generation));
      const mine = await instance.agent.harness.prompt("write it", { session });
      // The same grant, for a thread in another universe, and for another agent: no
      // triple holds there.
      const write = async (stamp: Stamped, agent = "main") => {
        const thread = await instance.agent.thread(agent, stamp);
        return shown(await instance.agent.harness.prompt(
          "write again", { session: String(thread.conversation_id) }))["write"];
      };
      return { mine: shown(mine),
               elsewhere: await write({ ...r.stampOf(MUSE, generation), universe: "beta" }),
               helper: await write({ ...r.stampOf(MUSE, generation), agent: "helper" },
                                   "helper") };
    });
    expect([out.mine["write"]!.error, out.mine["edit"]!.error]).toEqual([false, false]);
    expect(out.elsewhere).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect(out.helper).toEqual({ text: blocked(NOT_GRANTED), error: true });
    const history = await r.stub.fileHistory(r.bind(null), { path: "/workspace/mine.md",
                                                             limit: 1 });
    expect(history.changes.map((change) => change.writer)).toEqual([`client:${MUSE.client}`]);
  });

  it("is refused a stamp that no longer stands, when the thread is made", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const tried = (agent: string, stamp: Stamped) => r.in(async (instance) =>
      (await instance.agent.thread(agent, stamp).then(() => "made", (e: Error) => e.message)));
    expect([await tried("main", r.stampOf(MUSE, 2)), await tried("main", r.stampOf(DOT, 1)),
            await tried("other", r.stampOf(MUSE, 1))])
      .toEqual([AUTHORITY_CHANGED, AUTHORITY_CHANGED, "a thread's stamp names its own agent"]);
    // Revoked, then granted again by the owner: a sign-in from before the revoke is fenced,
    // and one after it stands.
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                         scopes: [], expected_generation: 2, name: "" });
    const again = r.stampOf(MUSE, 3);
    expect([await tried("main", again),
            await tried("main", { ...again, authenticated_at: r.at + 100 })])
      .toEqual([AUTHORITY_CHANGED, "made"]);
  });
});

describe("a full client's conversation", () => {
  it("runs every tool, as the owner's would, until its grant moves", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("remember the milk") });
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const first = shown(await instance.agent.harness.prompt("go scoped", { session }));
      // A grant change made around the doors: only the gate's own check sees it.
      new Grants(state.storage, new Policy(state.storage))
        .revoke(MUSE.client, clock());
      const after = shown(await instance.agent.harness.prompt("write again", { session }));
      return { first, after };
    });
    expect([out.first["write"]!.error, out.first["edit"]!.error, out.first["pass"]!.error])
      .toEqual([false, false, false]);
    expect(out.first["bash"]!.text).toMatch(/no sandbox yet/);  // ran: the shell is not here
    expect(out.after["write"]).toEqual({ text: blocked(AUTHORITY_CHANGED), error: true });
  });

  it("gates a nested call with the same check: one the grant still allows runs, and one " +
     "made after it moved is blocked", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("nested milk") });
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const read = shown(await instance.agent.harness.prompt("nest read", { session }));
      const nest = gate();
      instance.probes.nestGate = nest.promise;
      const { operationId } = await instance.agent.harness.submit("nest write", { session });
      await until(() => instance.probes.gated.filter((call) => call.name === "nest").length === 2,
                  "the second nest to pass the gate");
      new Grants(state.storage, new Policy(state.storage))
        .revoke(MUSE.client, clock());
      nest.release();
      await instance.agent.harness.wait(operationId, { session });
      const messages = (await instance.agent.harness.messages({ session }))
        .flatMap((entry) => (entry.model ?? []) as Message[]);
      return { read, write: results(messages)["nest"]!, gated: instance.probes.gated };
    });
    expect(JSON.parse(out.read["nest"]!.text)).toMatchObject({ error: false });
    expect(JSON.stringify(JSON.parse(out.read["nest"]!.text).out)).toContain("nested milk");
    expect(JSON.parse(out.write.text))
      .toEqual({ error: true, said: blockedSaid(AUTHORITY_CHANGED), out: null });
    // The authority gate let the nested read through, as a nested call, and not the write.
    expect(out.gated).toEqual([{ name: "nest", nested: false }, { name: "read", nested: true },
                               { name: "nest", nested: false }]);
    expect((await r.stub.fileStat(r.bind(null), { path: "/workspace/nested.md" })).entry)
      .toBeNull();
  });
});

describe("a stamp", () => {
  it("is inherited by a subagent's conversation and a fork's, which the gate holds to it",
     async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const stamp = r.stampOf(MUSE, 1);
    const out = await r.in(async (instance) => {
      const { thread, session } = await threadFor(instance, stamp);
      const spawned = shown(await instance.agent.harness.prompt("spawn one", { session }));
      const child = Number(spawned["spawn"]!.text) as ConversationId;
      const pi = await instance.agent.harness.pi();
      const conversation = (await pi.conversation(thread.conversation_id as ConversationId, BG))!;
      const at = (await conversation.context(BG)).entries.at(-1)!.id;
      const fork = await conversation.fork(at, { ownership: { kind: "ownerless" } }, BG);
      const stamps = [await pi.snapshot(instance.agent.authority, child, BG),
                      await pi.snapshot(instance.agent.authority, fork.id, BG)];
      await instance.grantRevoke(r.bind(null), { client: MUSE.client });
      const write = async (id: number) => shown(await instance.agent.harness.prompt(
        "write again", { session: String(id) }))["write"];
      return { stamps, child: await write(child), fork: await write(fork.id),
               own: await write(1) };
    });
    expect(out.stamps).toEqual([{ stamp }, { stamp }]);
    expect(out.child).toEqual({ text: blocked(AUTHORITY_CHANGED), error: true });
    expect(out.fork).toEqual({ text: blocked(AUTHORITY_CHANGED), error: true });
    expect(out.own!.error).toBe(false);  // the owner's own conversation is not the client's
  });
});

describe("a grant change", () => {
  it("ends its client's running turn, live views and waits, and blocks its next tool call; " +
     "another client's turn and the owner's run on", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    await r.admit(DOT);
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      const muse = await threadFor(instance, r.stampOf(MUSE, 1));
      const idle = await threadFor(instance, r.stampOf(MUSE, 1));
      const parent = await threadFor(instance, r.stampOf(MUSE, 1));
      const dot = await threadFor(instance, r.stampOf(DOT, 1));
      const turns = {
        muse: await instance.agent.harness.submit("hold please", { session: muse.session }),
        // A subagent of the client's, holding in a conversation its parent's tool owns.
        parent: await instance.agent.harness.submit("spawn hold", { session: parent.session }),
        dot: await instance.agent.harness.submit("hold please", { session: dot.session }),
        own: await instance.agent.harness.submit("hold please"),
      };
      await until(() => instance.probes.holds === 4, "the four holds to start");
      const child = instance.probes.spawned!;
      const heard: Record<string, string[]> = { muse: [], idle: [], child: [], dot: [],
                                                own: [] };
      for (const [who, id] of [["muse", muse.thread.conversation_id],
                               ["idle", idle.thread.conversation_id], ["child", child],
                               ["dot", dot.thread.conversation_id], ["own", 1]] as const) {
        instance.parts.tee.subscribe(await teeSession(instance, id),
                                     (event) => heard[who]!.push(event.type));
      }
      const waits = {
        muse: instance.agent.wait(turns.muse.operationId, muse.session, MUSE.client)
          .then(() => "settled", (error: Error) => error.message),
        dot: instance.agent.wait(turns.dot.operationId, dot.session, DOT.client)
          .then(() => "settled", (error: Error) => error.message),
      };
      await instance.grantRevoke(r.bind(null), { client: MUSE.client });
      const pi = await instance.agent.harness.pi();
      const ended = [await instance.agent.harness.wait(turns.muse.operationId,
                                                       { session: muse.session }),
                     await instance.agent.harness.wait(turns.parent.operationId,
                                                       { session: parent.session }),
                     await (await pi.submission(instance.probes.submitted[0] as never, BG))!
                       .wait(BG)];
      const running = [await instance.agent.status(turns.dot.operationId, dot.session),
                       await instance.agent.status(turns.own.operationId, "1")];
      held.release();
      const done = [await instance.agent.harness.wait(turns.dot.operationId,
                                                      { session: dot.session }),
                    await instance.agent.harness.wait(turns.own.operationId)];
      const next = shown(await instance.agent.harness.prompt("write again",
                                                             { session: muse.session }));
      return { ended, running, done, next, heard, waits: { muse: await waits.muse,
                                                           dot: await waits.dot } };
    });
    expect(out.ended.map((answer) => [answer.status, answer.reason]))
      .toEqual([["unanswered", "aborted"], ["unanswered", "aborted"],
                ["unanswered", "aborted"]]);
    expect(out.running.map((status) => status.status)).toEqual(["running", "running"]);
    expect(out.done.map((answer) => answer.status)).toEqual(["done", "done"]);
    expect(out.waits).toEqual({ muse: WAIT_ENDED, dot: "settled" });
    expect(out.heard).toEqual({ muse: ["end"], idle: ["end"], child: ["end"], dot: [],
                                own: [] });
    expect(out.next["write"]).toEqual({ text: blocked(AUTHORITY_CHANGED), error: true });
    expect((await r.stub.fileStat(r.bind(null), { path: "/workspace/again.md" })).entry)
      .toBeNull();
  });

  it("set by the owner ends the client's running turn as a revoke does", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const { operationId } = await instance.agent.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      await instance.grantSet(r.session(), { client: MUSE.client, source: "mcp",
                                             mode: "scoped", name: "", expected_generation: 1,
                                             scopes: [{ universe: "alpha", agent: "main",
                                                        capability: "write_files" }] });
      return instance.agent.harness.wait(operationId, { session });
    });
    expect([out.status, out.reason]).toEqual(["unanswered", "aborted"]);
  });

  it("refuses, in the write's own unit, a write whose call passed the gate before the " +
     "revoke, the revoke's unit queued ahead of it behind a held queue", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const toolGate = gate();
      instance.probes.toolGate = toolGate.promise;
      const { operationId } = await instance.agent.harness.submit("write raced", { session });
      await until(() => instance.probes.gated.some((call) => call.name === "write"),
                  "the write to pass the authority gate");
      const queue = holdQueue(state);
      const revoke = instance.grantRevoke(r.bind(null), { client: MUSE.client });
      toolGate.release();  // the call goes on to its intent, queued behind the revoke's unit
      for (let i = 0; i < 50; i++) {
        await Promise.resolve();
      }
      queue.release();
      await queue.done;
      await revoke;
      return instance.agent.harness.wait(operationId, { session });
    });
    expect(out.status).toBe("unanswered");
    expect((await r.stub.fileStat(r.bind(null), { path: "/workspace/raced.md" })).entry)
      .toBeNull();
  });

  it("refuses a write in its own unit once the grant moved after the gate let the call by",
     async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const toolGate = gate();
      instance.probes.toolGate = toolGate.promise;
      const answer = instance.agent.harness.prompt("write raced", { session });
      await until(() => instance.probes.gated.some((call) => call.name === "write"),
                  "the write to pass the authority gate");
      // Around the doors, so no abort follows: only the write's own unit sees it.
      new Grants(state.storage, new Policy(state.storage))
        .revoke(MUSE.client, clock());
      toolGate.release();
      return shown(await answer);
    });
    expect(out["write"]!.error).toBe(true);
    expect(out["write"]!.text).toContain(AUTHORITY_CHANGED);
    expect((await r.stub.fileStat(r.bind(null), { path: "/workspace/raced.md" })).entry)
      .toBeNull();
  });
});

describe("a policy write", () => {
  it("ends every live turn and background work, closes every live view and ends every " +
     "wait", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.probes.backgroundGate = new Promise(() => undefined);
      const muse = await threadFor(instance, r.stampOf(MUSE, 1));
      // Background work its run started, in a thread of the owner's: it outlives the run.
      const started = await threadFor(instance, null);
      expect((await instance.agent.harness.prompt("start background",
                                                  { session: started.session })).status)
        .toBe("done");
      const own = await instance.agent.harness.submit("hold please");
      const theirs = await instance.agent.harness.submit("hold please",
                                                         { session: muse.session });
      await until(() => instance.probes.holds === 2 && instance.probes.background !== null,
                  "both holds and the background work to start");
      const heard: string[] = [];
      instance.parts.tee.subscribe(await teeSession(instance, 1), (event) =>
        heard.push(event.type));
      const waited = instance.agent.wait(own.operationId, "1", null)
        .then(() => "settled", (error: Error) => error.message);
      const { generation, policy } = await instance.policyRead(r.bind(null));
      await instance.policyWrite(r.session(), { expected_generation: generation, policy });
      const pi = await instance.agent.harness.pi();
      return {
        ended: [await instance.agent.harness.wait(own.operationId),
                await instance.agent.harness.wait(theirs.operationId, { session: muse.session })],
        background: (await pi.waitForTask(instance.probes.background as never, BG))
          .state.outcome,
        heard, waited: await waited,
      };
    });
    expect(out.ended.map((answer) => [answer.status, answer.reason]))
      .toEqual([["unanswered", "aborted"], ["unanswered", "aborted"]]);
    expect(out.background).toMatchObject({ status: "failed" });
    expect(out.heard).toEqual(["end"]);
    expect(out.waited).toBe(WAIT_ENDED);
  });
});

describe("an operation's status", () => {
  it("is pi's pending record while it is open, then how it settled, from pi's receipts",
     async () => {
    const r = await agentRig();
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      const { session } = await threadFor(instance, null);
      const first = await instance.agent.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      const second = await instance.agent.harness.submit("after", { session });
      const open = [await instance.agent.status(first.operationId, session),
                    await instance.agent.status(second.operationId, session),
                    await instance.agent.status("op-unknown", session)];
      held.release();
      await instance.agent.harness.wait(second.operationId, { session });
      return { open, settled: [await instance.agent.status(first.operationId, session),
                               await instance.agent.status(second.operationId, session)] };
    });
    expect(out.open.map((status) => [status.status, status.reason ?? null])).toEqual([
      ["running", null], ["queued", null], ["unanswered", "not_found"]]);
    expect(out.settled.map((status) => [status.status, status.text])).toEqual([
      ["done", expect.stringContaining("held")], ["done", "noted"]]);
  });
});

describe("the work lane's runner", () => {
  /** A claimed target for `work_id`, in `thread`, with its input. */
  const target = (workId: string, thread: string, stamp: WorkTarget["stamp"] = null):
      WorkTarget => ({ work_id: workId, claim_id: "claim_x", kind: "turn",
                       payload: { thread, input: "scheduled hello" }, stamp });

  it("hands held work its job: due work runs in the thread it named, as ta.work", async () => {
    const r = await agentRig();
    const { workId, session } = await r.in(async (instance, state) => {
      const { thread, session } = await threadFor(instance, null);
      const gateway = new WriteGate(state.storage);
      const threads = new Threads(gateway.storage);
      const work = new Scheduled(gateway.storage, new Grants(gateway.storage,
                                                             new Policy(gateway.storage)),
                                 threads);
      const { work_id: workId } = await gateway.write(() => work.add(
        "turn", clock() - 1, { thread: thread.thread_id, input: "scheduled hello" }, null,
        clock()));
      return { workId, session };
    });
    // Any changing door re-derives the lanes' jobs; the alarm runs the work lane.
    await r.stub.grantRevoke(r.bind(null), { client: DOT.client });
    await drainDue(r.stub as never);
    const out = await r.in(async (instance, state) => ({
      settled: await instance.agent.harness.wait(workId, { session }),
      status: await instance.agent.status(workId, session),
      row: state.storage.sql.exec("SELECT state, outcome FROM scheduled_work").toArray(),
      entries: (await instance.agent.harness.messages({ session }))
        .filter((entry) => entry.kind === "ta.work").map((entry) => entry.data),
    }));
    expect([out.settled.status, out.status.status]).toEqual(["done", "done"]);
    expect(out.row).toEqual([{ state: "handed", outcome: "{}" }]);
    expect(out.entries).toEqual([{ work_id: workId, client: null }]);
  });

  it("hands a work_id over once: a second hand-off is pi's duplicate, never run twice",
     async () => {
    const r = await agentRig();
    const out = await r.in(async (instance, state) => {
      const { thread, session } = await threadFor(instance, null);
      const runner = workRunner(new Threads(state.storage), instance.agent.harness);
      const marks: string[] = [];
      const mark = async () => {
        marks.push("mark");
      };
      const answers = [await runner.handOff(target("work_a", thread.thread_id), mark),
                       await runner.handOff(target("work_a", thread.thread_id), mark)];
      const settled = await instance.agent.harness.wait("work_a", { session });
      const users = (await instance.agent.harness.messages({ session }))
        .filter((entry) => entry.kind === "ta.work").length;
      return { answers, marks, settled, users, requests: instance.turns.length };
    });
    expect(out.answers).toEqual([{ state: "handed" }, { state: "handed", reason: "duplicate" }]);
    expect(out.marks).toEqual(["mark", "mark"]);
    expect([out.settled.status, out.users, out.requests]).toEqual(["done", 1, 1]);
  });

  it("queues a hand-off into a busy thread behind its run, as its own run", async () => {
    const r = await agentRig();
    const out = await r.in(async (instance, state) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      const { thread, session } = await threadFor(instance, null);
      const busy = await instance.agent.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      const runner = workRunner(new Threads(state.storage), instance.agent.harness);
      const answer = await runner.handOff(target("work_q", thread.thread_id), async () => {});
      const queued = await instance.agent.status("work_q", session);
      held.release();
      return { answer, queued, busy: await instance.agent.harness.wait(busy.operationId,
                                                                      { session }),
               work: await instance.agent.harness.wait("work_q", { session }) };
    });
    expect([out.answer, out.queued.status]).toEqual([{ state: "handed" }, "queued"]);
    // Its own run: the held run answered its tool round, then the work its own input.
    expect([out.busy.text, out.work.text]).toEqual([expect.stringContaining("held"), "noted"]);
  });

  it("stops before any effect when its claim lapsed, and fails work whose thread is gone " +
     "before its mark", async () => {
    const r = await agentRig();
    const out = await r.in(async (instance, state) => {
      const { thread, session } = await threadFor(instance, null);
      const runner = workRunner(new Threads(state.storage), instance.agent.harness);
      const lapsed = await failure(() => runner.handOff(target("work_b", thread.thread_id),
        async () => {
          throw new Error("the work's claim lapsed before its hand-off");
        }));
      let marked = false;
      const gone = await runner.handOff(target("work_c", `thread_${"0".repeat(32)}`), async () => {
        marked = true;
      });
      return { lapsed: lapsed.message, gone, marked,
               pending: await instance.agent.harness.pending({ session }),
               status: await instance.agent.status("work_b", session) };
    });
    expect(out.lapsed).toBe("the work's claim lapsed before its hand-off");
    expect(out.pending).toEqual([]);
    expect([out.status.status, out.status.reason]).toEqual(["unanswered", "not_found"]);
    expect([out.gone, out.marked]).toEqual([{ state: "failed", reason: "no_thread" }, false]);
  });
});

describe("an aborted turn's model call", () => {
  it("settles its reservation through the owner's usage when a revoke ends the turn " +
     "mid-stream", async () => {
    const r = await gated();
    await r.connect("sk-revoked");
    const at = signedIn();
    await r.stub.outsideAgents(r.bind({ origin: MUSE, source: MCP, authenticated_at: at }));
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
    const cancelled = async () => ((await (await fetch(`https://status.test/cancelled?id=${id}`))
      .json()) as { cancelled: boolean | null }).cancelled;
    const settled = await r.in(async (instance: GatedOwnerDO) => {
      const thread = await instance.agent.thread("main", {
        client: MUSE.client, generation: 1, authenticated_at: at, universe: null,
        agent: "main" });
      const session = String(thread.conversation_id);
      const { operationId } = await instance.agent.harness.submit(`cmd:slow-${id}`, { session });
      await until(async () => (await cancelled()) === false, "the upstream stream to open");
      await instance.grantRevoke(r.bind(), { client: MUSE.client });
      return instance.agent.harness.wait(operationId, { session });
    });
    expect([settled.status, settled.reason]).toEqual(["unanswered", "aborted"]);
    await until(async () => (await cancelled()) === true, "the upstream to be cancelled");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => receipt.state)).toEqual(["unknown"]);
  });
});
