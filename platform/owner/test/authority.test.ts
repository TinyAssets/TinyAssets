/**
 * Outside-client authority over the agent, the ending of work and the work runner (tasks
 * 5, sub-slice A2), in workerd on faux models: the `ta.authority` gate on a scoped,
 * full and revoked client's tool calls, nested ones included; the stamp inherited by a
 * subagent and a fork; a revoke raced against a tool call through the held queue; a grant
 * change and a policy write ending live turns, views and waits; the `work` lane's runner;
 * status from pi's receipts; and an aborted call's reservation settled through `usage`.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import { type ConversationId, type JsonObject, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { GATE, keepsGate, NO_THREAD, WAIT_ENDED } from "../src/agent";
import {
  Authority, AUTHORITY_CHANGED, AUTHORITY_RULE, capabilityOf, type Need, NO_AUTHORITY,
  NO_CONVERSATION, NO_SHELL, NOT_GRANTED, OWNER_ONLY, type Stamped, stamped,
  TA_CAPABILITIES,
} from "../src/authority";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import { originContext } from "../src/origin";
import type { Binding } from "../src/owner-do";
import { Policy, policyDigest } from "../src/policy";
import { DESTROYED } from "../src/thread-store";
import { type Thread, THREAD_LIMITS, type ThreadKind, Threads } from "../src/threads";
import { asked, gated, provider, until } from "./gateway-support";
import {
  DOT, ledgerOn, MCP, MUSE, registeredElsewhere, registeredOn, signedIn,
} from "./ledger-support";
import { drainDue, failure, storeSubmission } from "./support";
import {
  type FauxOwnerDO, type GatedOwnerDO, says, storeScript, wipedSubmission,
} from "./worker";

const BG = BACKGROUND_CONTEXT;
const text = (value: string): Uint8Array => new TextEncoder().encode(value);
/** A blocked call's result as the model sees it, and its diagnostic as a program does. */
const blocked = (reason: string) => `<harness>\n[error] Tool call blocked: ${reason}\n</harness>`;
const blockedSaid = (reason: string) => `Tool call blocked: ${reason}`;

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
  ["hold please", [viaTa("hold", {})]],
  ["go scoped", [fauxToolCall("read", { path: "notes.md" }), EDIT,
                 fauxToolCall("write", { path: "out.md", content: "x" }),
                 fauxToolCall("bash", { command: "ls" }), viaTa("pass", {})]],
  ["write it", [fauxToolCall("write", { path: "mine.md", content: "x" }), EDIT]],
  ["write again", [fauxToolCall("write", { path: "again.md", content: "x" })]],
  ["write raced", [fauxToolCall("write", { path: "raced.md", content: "x" })]],
  ["spawn hold", [viaTa("spawn", { work: "hold please" })]],
  ["spawn one", [viaTa("spawn", {})]],
  ["nest read", [viaTa("nest", { name: "read", arguments: { path: "notes.md" } })]],
  ["nest write", [viaTa("nest", { name: "write",
                                         arguments: { path: "nested.md", content: "x" } })]],
  ["start background", [viaTa("background", { work: "background" })]],
  ["read it", [fauxToolCall("read", { path: "notes.md" })]],
  ["shell it", [fauxToolCall("bash", { command: "ls" })]],
];

/** A held gate and its release. */
function gate(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
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
    /**
     * The client's first verified call records its grant: full, generation 1; and its agent
     * registered in universe `alpha`, where its stamps act.
     */
    admit: async (origin: Origin) => {
      await stub.outsideAgents(bind(origin, { authenticated_at: at }));
      await runInDurableObject(stub, (_instance, state) => ledgerOn(state.storage).register(
        origin, MCP, { command_center_id: "alpha", display_name: "Muse",
                       delivery_method: "pull", recipe_id: null }));
    },
    /** The owner scopes the client to `capabilities` in universe `alpha`, as agent `main`. */
    scoped: async (origin: Origin, capabilities: string[]) => {
      const row = (await stub.grantList(bind(null))).clients
        .find((client) => client.client === origin.client);
      return (await stub.grantSet(session(), {
        client: origin.client, source: "mcp", mode: "scoped", name: "",
        scopes: capabilities.map((capability) => ({ universe: "alpha", agent: "main",
                                                    capability })),
        expected_revision: row?.revision ?? 0 })).generation;
    },
    stampOf: (origin: Origin, generation: number): Stamped => ({
      client: origin.client, generation, authenticated_at: at, universe: "alpha", agent: "main",
    }),
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
  };
}

/**
 * A thread made for `stamp`, its pi session, and the model answering by `byAsk`: a run, or
 * the caller's conversation, for input after input (a settled run takes no more).
 */
async function threadFor(instance: FauxOwnerDO, stamp: Stamped | null,
                         kind: "run" | "converse" = "run") {
  instance.script = Array.from({ length: 40 }, () => byAsk);
  const thread = await instance.agent.thread("main", stamp, { kind });
  return { thread, session: thread.thread_id };
}

/** A conversation's live tee session: its pi provider identity, in its thread's Session. */
async function teeSession(instance: FauxOwnerDO, id: number, session?: string):
    Promise<string> {
  const pi = await instance.harness.pi(session === undefined ? {} : { session });
  return (await pi.snapshot(ProviderDoc, id as ConversationId, BG))!.sessionId;
}

/** The owner's own thread's row. */
async function ownRow(instance: FauxOwnerDO) {
  return instance.parts.threads.get(await instance.harness.thread())!;
}

const shown = (answer: { text?: string }) =>
  JSON.parse(answer.text!) as ReturnType<typeof results>;

describe("the authority gate's capability map", () => {
  it("names read_files for read, write_files for write, both for edit, none to grant for " +
     "the shell, ta's by its own map, and no capability for anything else", () => {
    const of = (name: string, args: unknown = {}) => capabilityOf({ name, arguments: args });
    expect([of("read"), of("write"), of("edit"), of("bash")])
      .toEqual([["read_files"], ["write_files"], ["read_files", "write_files"], null]);
    expect([of("hold"), of("toString"), of("__proto__"), of("ta", { op: "call", name: "hold" }),
            of("ta", { op: "call", name: "toString" }), of("ta", null)])
      .toEqual([undefined, undefined, undefined, undefined, undefined, undefined]);
    expect([of("ta", { op: "catalog" }), of("ta", { op: "call", name: "policy:show" })])
      .toEqual([[], OWNER_ONLY]);
  });

  it("holds each ta capability to what its door holds: the owner's policy the owner's, a " +
     "proposal to propose_policy, file usage to read_files, code mode to run_code (each " +
     "tool its script calls held to its own need), the rest to a standing grant",
     () => {
    expect(TA_CAPABILITIES).toEqual({
      "ta:catalog": [], "policy:show": OWNER_ONLY, "policy:propose": ["propose_policy"],
      "wake:list": [], "files:usage": ["read_files"], "model": [], "agent:run": ["run_graph"],
      "agent:delegate": ["run_graph"], "codemode": ["run_code"],
    });
  });

  it("reads ta's map by the op or the capability called, the ta: names the ops' alone",
     () => {
    // A map as a test fills it: the catalog's need, and a capability's by the name it calls.
    const filled: Record<string, Need> = { "ta:catalog": ["read_files"],
                                           "files:usage": ["read_files", "write_files"] };
    const of = (args: unknown) => capabilityOf({ name: "ta", arguments: args }, filled);
    expect([of({ op: "catalog" }), of({ op: "call", name: "files:usage" }),
            of({ op: "call", name: "ta:catalog" }), of({ op: "call", name: "toString" }),
            of({ op: "catalog", name: "files:usage" }), of({ op: "other" })])
      .toEqual([["read_files"], ["read_files", "write_files"], undefined, undefined,
                ["read_files"], undefined]);
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
      return instance.harness.prompt("go scoped", { session });
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
    // Each receipt is of the client's call: its digest names the client, the tool, the reason.
    const requests = [["write", NOT_GRANTED], ["edit", NOT_GRANTED], ["bash", NO_SHELL],
                      ["ta", NOT_GRANTED]].map(([tool, reason]) => policyDigest({
      action: "tool_call", request: { client: MUSE.client, tool, reason } }));
    expect((await refusals()).map((receipt) => receipt.request_digest).sort())
      .toEqual(requests.sort());
  });

  it("writes with write_files, as itself, only where its grant's universe and agent are, " +
     "and edits only with read_files too", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("remember the milk") });
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["write_files"]);
    const out = await r.in(async (instance, state) => {
      registeredElsewhere(state.storage, MUSE, "beta");
      const { session } = await threadFor(instance, r.stampOf(MUSE, generation));
      const mine = await instance.harness.prompt("write it", { session });
      // The same grant, for a thread in another universe, and for another agent: no
      // triple holds there.
      const write = async (stamp: Stamped, agent = "main") => {
        const thread = await instance.agent.thread(agent, stamp);
        return shown(await instance.harness.prompt(
          "write again", { session: thread.thread_id }))["write"];
      };
      return { mine: shown(mine),
               elsewhere: await write({ ...r.stampOf(MUSE, generation), universe: "beta" }),
               helper: await write({ ...r.stampOf(MUSE, generation), agent: "helper" },
                                   "helper") };
    });
    expect(out.mine["write"]!.error).toBe(false);
    expect(out.mine["edit"]).toEqual({ text: blocked(NOT_GRANTED), error: true });
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
                                         scopes: [], expected_revision: 2, name: "" });
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
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1), "converse");
      const first = shown(await instance.harness.prompt("go scoped", { session }));
      // A grant change made around the doors: only the gate's own check sees it.
      new Grants(state.storage, new Policy(state.storage))
        .revoke(MUSE.client, clock());
      const after = shown(await instance.harness.prompt("write again", { session }));
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
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1), "converse");
      const read = shown(await instance.harness.prompt("nest read", { session }));
      const nest = gate();
      instance.probes.nestGate = nest.promise;
      const { operationId } = await instance.harness.submit("nest write", { session });
      await until(() => instance.probes.gated.filter((call) => call.name === "nest").length === 2,
                  "the second nest to pass the gate");
      new Grants(state.storage, new Policy(state.storage))
        .revoke(MUSE.client, clock());
      nest.release();
      await instance.harness.wait(operationId, { session });
      const messages = (await instance.harness.messages({ session }))
        .flatMap((entry) => (entry.model ?? []) as Message[]);
      return { read, write: results(messages)["nest"]!, gated: instance.probes.gated };
    });
    expect(JSON.parse(out.read["nest"]!.text)).toMatchObject({ error: false });
    expect(JSON.stringify(JSON.parse(out.read["nest"]!.text).out)).toContain("nested milk");
    expect(JSON.parse(out.write.text))
      .toEqual({ error: true, said: blockedSaid(AUTHORITY_CHANGED), out: null });
    // The authority gate let the nested read through, as a nested call, and not the write.
    expect(out.gated).toEqual([{ name: "ta", nested: false }, { name: "nest", nested: true },
                               { name: "read", nested: true }, { name: "ta", nested: false },
                               { name: "nest", nested: true }]);
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
    const out = await r.in(async (instance, state) => {
      const { thread, session } = await threadFor(instance, stamp);
      const spawned = shown(await instance.harness.prompt("spawn one", { session }));
      const child = Number(spawned["spawn"]!.text) as ConversationId;
      const pi = await instance.harness.pi({ session });
      const conversation = (await pi.conversation(1 as ConversationId, BG))!;
      const at = (await conversation.context(BG)).entries.at(-1)!.id;
      const fork = await conversation.fork(at, { ownership: { kind: "ownerless" } }, BG);
      const stamps = [await pi.snapshot(instance.agent.authority, child, BG),
                      await pi.snapshot(instance.agent.authority, fork.id, BG)];
      // The revoke's row alone, as one whose end never ran leaves it (a door's revoke also
      // drops the thread): the Session's conversations are held to the stamp that no
      // longer stands.
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
      const write = async (id: number, at?: string) => shown(await instance.harness.prompt(
        "write again", { session: String(id), ...(at === undefined ? {} : { thread: at }) }))[
        "write"];
      return { stamps, child: await write(child, thread.thread_id),
               fork: await write(fork.id, thread.thread_id), own: await write(1) };
    });
    expect(out.stamps).toEqual([{ by: "client", stamp }, { by: "client", stamp }]);
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
        muse: await instance.harness.submit("hold please", { session: muse.session }),
        // A subagent of the client's, holding in a conversation its parent's tool owns.
        parent: await instance.harness.submit("spawn hold", { session: parent.session }),
        dot: await instance.harness.submit("hold please", { session: dot.session }),
        own: await instance.harness.submit("hold please"),
      };
      await until(() => instance.probes.holds === 4, "the four holds to start");
      const child = instance.probes.spawned!;
      const heard: Record<string, string[]> = { muse: [], idle: [], child: [], dot: [],
                                                own: [] };
      for (const [who, id, session] of [["muse", 1, muse.session], ["idle", 1, idle.session],
                                        ["child", child, parent.session],
                                        ["dot", 1, dot.session],
                                        ["own", 1, undefined]] as const) {
        instance.parts.tee.subscribe(await teeSession(instance, id, session),
                                     (event) => heard[who]!.push(event.type));
      }
      const waits = {
        muse: instance.agent.wait(muse.thread, turns.muse.operationId, MUSE.client)
          .then(() => "settled", (error: Error) => error.message),
        dot: instance.agent.wait(dot.thread, turns.dot.operationId, DOT.client)
          .then(() => "settled", (error: Error) => error.message),
      };
      await instance.grantRevoke(r.bind(null), { client: MUSE.client });
      // The door returned once the work it ended had settled, within its wait: the client's
      // threads are dropped and their Sessions closed, then their stores wiped, so what each
      // store held as it was wiped says how it ended.
      const atOnce = wipedSubmission(instance.probes, muse.thread.store,
                                     { request: turns.muse.operationId })?.status;
      const ended = [wipedSubmission(instance.probes, muse.thread.store,
                                     { request: turns.muse.operationId }),
                     wipedSubmission(instance.probes, parent.thread.store,
                                     { request: turns.parent.operationId }),
                     wipedSubmission(instance.probes, parent.thread.store,
                                     { id: instance.probes.submitted[0]! })];
      const closed = instance.agent.host.openThreads();
      const running = [await instance.agent.status(dot.thread, turns.dot.operationId),
                       await instance.agent.status(await ownRow(instance), turns.own.operationId)];
      held.release();
      const done = [await instance.harness.wait(turns.dot.operationId,
                                                      { session: dot.session }),
                    await instance.harness.wait(turns.own.operationId)];
      // Nothing reaches the dropped thread again: no row names its store.
      const next = await instance.harness.prompt("write again", { session: muse.session })
        .then(() => "ran", (error: Error) => error.message);
      return { atOnce, ended, running, done, next, heard, closed,
               dropped: [muse, idle, parent].map((one) => one.thread.thread_id),
               waits: { muse: await waits.muse, dot: await waits.dot } };
    });
    expect(out.atOnce).toBe("unanswered");
    expect(out.ended.map((answer) => [answer?.status, answer?.reason]))
      .toEqual([["unanswered", "aborted"], ["unanswered", "aborted"],
                ["unanswered", "aborted"]]);
    expect(out.closed.filter((thread) => out.dropped.includes(thread))).toEqual([]);
    expect(out.running.map((status) => status.status)).toEqual(["running", "running"]);
    expect(out.done.map((answer) => answer.status)).toEqual(["done", "done"]);
    expect(out.waits).toEqual({ muse: WAIT_ENDED, dot: "settled" });
    expect(out.heard).toEqual({ muse: ["end"], idle: ["end"], child: ["end"], dot: [],
                                own: [] });
    expect(out.next).toBe(NO_THREAD);
    expect((await r.stub.fileStat(r.bind(null), { path: "/workspace/again.md" })).entry)
      .toBeNull();
  });

  it("set by the owner ends the client's running turn as a revoke does", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      const { thread, session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const { operationId } = await instance.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      await instance.grantSet(r.session(), { client: MUSE.client, source: "mcp",
                                             mode: "scoped", name: "", expected_revision: 1,
                                             scopes: [{ universe: "alpha", agent: "main",
                                                        capability: "write_files" }] });
      return wipedSubmission(instance.probes, thread.store, { request: operationId })!;
    });
    expect([out.status, out.reason]).toEqual(["unanswered", "aborted"]);
  });

  it("returns from its door once the work it ended settled, a tool slow to stop included",
     async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.probes.holdLinger = 200;
      const { thread, session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const { operationId } = await instance.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      await instance.grantRevoke(r.bind(null), { client: MUSE.client });
      return wipedSubmission(instance.probes, thread.store, { request: operationId })!.status;
    });
    expect(out).toBe("unanswered");
  });

  it("refuses, in the write's own unit, a write whose call passed the gate before the " +
     "revoke, the revoke's unit committed ahead of it", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      const { thread, session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const toolGate = gate();
      instance.probes.toolGate = toolGate.promise;
      const { operationId } = await instance.harness.submit("write raced", { session });
      await until(() => instance.probes.gated.some((call) => call.name === "write"),
                  "the write to pass the authority gate");
      // The revoke's unit runs at once, synchronously; its end of the work follows.
      const revoke = instance.grantRevoke(r.bind(null), { client: MUSE.client });
      toolGate.release();  // the call goes on to its intent, after the revoke's unit
      await revoke;
      return wipedSubmission(instance.probes, thread.store, { request: operationId })!;
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
      const answer = instance.harness.prompt("write raced", { session });
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
      expect((await instance.harness.prompt("start background",
                                                  { session: started.session })).status)
        .toBe("done");
      const own = await instance.harness.submit("hold please");
      const theirs = await instance.harness.submit("hold please",
                                                         { session: muse.session });
      await until(() => instance.probes.holds === 2 && instance.probes.background !== null,
                  "both holds and the background work to start");
      const heard: string[] = [];
      instance.parts.tee.subscribe(await teeSession(instance, 1), (event) =>
        heard.push(event.type));
      const waited = instance.agent.wait(await ownRow(instance), own.operationId, null)
        .then(() => "settled", (error: Error) => error.message);
      const { generation, policy } = await instance.policyRead(r.bind(null));
      await instance.policyWrite(r.session(), { expected_generation: generation, policy });
      const pi = await instance.harness.pi({ session: started.session });
      return {
        ended: [await instance.harness.wait(own.operationId),
                await instance.harness.wait(theirs.operationId, { session: muse.session })],
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

  it("ends a thread its live row says is working, its Session closed: opened past the " +
     "bound, aborted before it resumes, so no model request starts, then closed", async () => {
    const r = await agentRig();
    const op = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      await threadFor(instance, null);
      const { operationId } = await instance.harness.submit("hold please");
      await until(() => instance.probes.holds === 1, "the hold to start");
      return operationId;
    });
    // The object dies mid-run: its row still says the thread is working.
    await runInDurableObject(r.stub, (_i, state) => state.abort("crash")).catch(() => undefined);
    const again = <R>(act: (instance: FauxOwnerDO) => Promise<R>) =>
      runInDurableObject(env.AGENT.getByName(r.owner), act);
    const out = await again(async (instance) => {
      const requests: number[] = [];
      instance.script = Array.from({ length: 4 }, () => (messages: Message[]) => {
        requests.push(messages.length);
        return says("resumed");
      });
      instance.timing.openMax = 0;  // every other open is refused: an ending's is not
      const thread = await ownRow(instance);
      const before = { open: instance.agent.host.openThreads(),
                       active: instance.parts.threads.active() };
      const { generation, policy } = await instance.policyRead(r.bind(null));
      await instance.policyWrite(r.session(), { expected_generation: generation, policy });
      return { before, thread: thread.thread_id, requests,
               after: instance.agent.host.openThreads() };
    });
    expect(out.before).toEqual({ open: [], active: [out.thread] });
    expect(out.requests).toEqual([]);
    expect(out.after).toEqual([]);
    const settled = await again(async (instance) => {
      instance.timing.openMax = 4;
      return instance.agent.status(await ownRow(instance), op);
    });
    expect([settled.status, settled.reason]).toEqual(["unanswered", "aborted"]);
  });

  it("ends working threads in batches of the bound, never more opened for it at once",
     async () => {
    // Review round 2 (L2): every stale live row after a mass eviction opened at once.
    const r = await agentRig();
    const ops = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      const made = [];
      for (let n = 0; n < 3; n++) {
        const { session } = await threadFor(instance, null);
        made.push([session, (await instance.harness.submit("hold please", { session }))
          .operationId] as const);
      }
      await until(() => instance.probes.holds === 3, "the holds to start");
      return made;
    });
    await runInDurableObject(r.stub, (_i, state) => state.abort("crash")).catch(() => undefined);
    const out = await runInDurableObject(env.AGENT.getByName(r.owner),
                                         async (instance: FauxOwnerDO) => {
      instance.script = Array.from({ length: 8 }, () => says("resumed"));
      instance.timing.openMax = 2;
      const host = instance.agent.host;
      const end = host.end.bind(host);
      let ending = 0;
      let most = 0;
      host.end = async (thread, act) => {
        most = Math.max(most, ++ending);
        try {
          return await end(thread, act);
        } finally {
          ending--;
        }
      };
      const { generation, policy } = await instance.policyRead(r.bind(null));
      await instance.policyWrite(r.session(), { expected_generation: generation, policy });
      instance.timing.openMax = 64;
      const settled = [];
      for (const [session, operationId] of ops) {
        settled.push((await instance.harness.wait(operationId, { session })).reason);
      }
      return { most, settled };
    });
    expect(out.most).toBe(2);
    expect(out.settled).toEqual(["aborted", "aborted", "aborted"]);
  });

  it("ends a thread whose open Session is working though its live row is gone", async () => {
    const r = await agentRig();
    const out = await r.in(async (instance, state) => {
      instance.probes.holdGate = new Promise(() => undefined);
      await threadFor(instance, null);
      const { operationId } = await instance.harness.submit("hold please");
      await until(() => instance.probes.holds === 1, "the hold to start");
      state.storage.sql.exec("DELETE FROM agent_live");
      const { generation, policy } = await instance.policyRead(r.bind(null));
      await instance.policyWrite(r.session(), { expected_generation: generation, policy });
      return instance.harness.wait(operationId);
    });
    expect([out.status, out.reason]).toEqual(["unanswered", "aborted"]);
  });

  it("rejects, loudly, when a working thread's work cannot be ended; the write stands",
     async () => {
    const r = await agentRig();
    const thread = await r.in(async (instance) => {
      const { thread: row } = await threadFor(instance, null);
      expect((await instance.harness.prompt("hi")).status).toBe("done");
      await instance.agent.host.close(row.thread_id);
      return row;
    });
    // Its store is gone, and its row says it is working: it cannot be opened to end it.
    await env.PISTORE.getByName(thread.store).destroy();
    await r.in((_instance, state) => {
      state.storage.sql.exec("INSERT INTO agent_live (thread_id, active, wake_at) " +
                             "VALUES (?, 1, NULL)", thread.thread_id);
    });
    const { generation, policy } = await r.stub.policyRead(r.bind(null));
    const refused = await failure(() => r.stub.policyWrite(r.session(), {
      expected_generation: generation, policy }));
    expect(refused.message).toContain(DESTROYED);
    expect((await r.stub.policyRead(r.bind(null))).generation).toBe(generation + 1);
  });
});

describe("an operation's status", () => {
  it("is pi's pending record while it is open, then how it settled, from pi's receipts",
     async () => {
    const r = await agentRig();
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      const { thread, session } = await threadFor(instance, null);
      const first = await instance.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      const second = await instance.harness.submit("after", { session });
      const began = Date.now();
      const open = [await instance.agent.status(thread, first.operationId),
                    await instance.agent.status(thread, second.operationId)];
      // An open operation's status is its submission record, read at once: no wait on it.
      const took = Date.now() - began;
      open.push(await instance.agent.status(thread, "op-unknown"));
      held.release();
      await instance.harness.wait(second.operationId, { session });
      return { open, took, settled: [await instance.agent.status(thread, first.operationId),
                                     await instance.agent.status(thread, second.operationId)] };
    });
    expect(out.took).toBeLessThan(1_000);
    expect(out.open.map((status) => [status.status, status.reason ?? null])).toEqual([
      ["running", null], ["queued", null], ["unanswered", "not_found"]]);
    expect(out.settled.map((status) => [status.status, status.text])).toEqual([
      ["done", expect.stringContaining("held")], ["done", "noted"]]);
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
      const session = thread.thread_id;
      const { operationId } = await instance.harness.submit(`cmd:slow-${id}`, { session });
      await until(async () => (await cancelled()) === false, "the upstream stream to open");
      await instance.grantRevoke(r.bind(), { client: MUSE.client });
      return wipedSubmission(instance.probes, thread.store, { request: operationId })!;
    });
    expect([settled.status, settled.reason]).toEqual(["unanswered", "aborted"]);
    await until(async () => (await cancelled()) === true, "the upstream to be cancelled");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => receipt.state)).toEqual(["unknown"]);
  });
});

describe("the authority's enforcement (review M1)", () => {
  it("holds a conversation to its authority in its environment, whatever extensions it " +
     "selects", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("the secret milk") });
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["write_files"]);  // no read_files
    const out = await r.in(async (instance) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, generation), "converse");
      const pi = await instance.harness.pi({ session });
      const conversation = (await pi.conversation(1 as ConversationId, BG))!;
      await conversation.configure({ extensions: { remove: [{ name: "ta.authority" } as never] } },
                                   BG);
      return {
        read: shown(await instance.harness.prompt("read it", { session }))["read"]!,
        shell: shown(await instance.harness.prompt("shell it", { session }))["bash"]!,
      };
    });
    // The gate was selected away; the environment refused the read and the shell itself.
    expect(out.read.error).toBe(true);
    expect(out.read.text).toContain(NOT_GRANTED);
    expect(out.read.text).not.toContain("secret");
    expect(out.shell.error).toBe(true);
    expect(out.shell.text).toContain(NO_SHELL);
  });

  it("holds a replay of a replay-safe read, which runs no beforeTool, to the authority as " +
     "it stands", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("the secret milk") });
    await r.admit(MUSE);
    const { operationId, session } = await r.in(async (instance, state) => {
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      storeScript(state, [calls(fauxToolCall("read", { path: "notes.md" })), says("done")]);
      instance.script = [];
      instance.probes.readGate = new Promise(() => undefined);
      return { operationId: (await instance.harness.submit("read it", { session }))
        .operationId, session };
    });
    await until(() => r.in((instance) => instance.probes.reads === 1), "the read to start");
    expect(await r.in((_instance, state) => state.abort("evicted mid-read"))
      .then(() => "alive", (error: Error) => error.message)).toBe("evicted mid-read");
    // Revoked while the object is down, around the doors: no abort reaches the read.
    const fresh = env.AGENT.getByName(r.owner);
    await runInDurableObject(fresh, (_instance: FauxOwnerDO, state) => {
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
    });
    expect(await runDurableObjectAlarm(fresh)).toBe(true);
    const read = await runInDurableObject(fresh, async (instance: FauxOwnerDO) => {
      await instance.harness.wait(operationId, { session });
      return { reads: instance.probes.reads,
               shown: results((await instance.harness.messages({ session }))
                 .flatMap((entry) => (entry.model ?? []) as Message[]))["read"]! };
    });
    expect(read.reads).toBe(1);  // the replay ran the read again, past no gate
    expect(read.shown.error).toBe(true);
    expect(read.shown.text).toContain(AUTHORITY_CHANGED);
    expect(read.shown.text).not.toContain("secret");
  });

  it("makes the owner's authority explicit: a conversation made as no thread does nothing",
     async () => {
    const r = await agentRig();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 40 }, () => byAsk);
      const pi = await instance.harness.pi();
      const made = await pi.createConversation({ ownership: { kind: "ownerless" },
        agent: { model: { provider: "gateway", modelId: "auto" } } }, BG);
      const state = await pi.snapshot(instance.agent.authority, made.id, BG);
      return { state, read: shown(await instance.harness.prompt(
        "read it", { session: String(made.id) }))["read"] };
    });
    expect(out.state).toEqual({ by: "none" });
    expect(out.read).toEqual({ text: blocked(NO_AUTHORITY), error: true });
  });

  it("keeps a task's conversation under its owner conversation's authority, a fork's " +
     "differing copy refused", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      const theirs = await threadFor(instance, r.stampOf(MUSE, 1));
      // A conversation of the owner's authority in the client's thread's own Session (no
      // door makes one: every conversation of a thread's Session is the thread's or below
      // it), with an entry to fork at.
      const pi = await instance.harness.pi({ session: theirs.session });
      const owners = await pi.createConversation({
        ownership: { kind: "ownerless" },
        agent: { model: { provider: "gateway", modelId: "auto" } },
        init: async (tx, id) => {
          Object.assign(await tx.doc(instance.agent.authority, id), { by: "owner" });
        },
      }, BG);
      expect((await instance.harness.prompt("noted", { session: String(owners.id),
                                                      thread: theirs.session })).status)
        .toBe("done");
      const at = (await owners.context(BG)).entries.at(-1)!.id;
      // A client's tool forks the owner's conversation, as a conversation its call owns.
      instance.script = [calls(viaTa("fork", { from: owners.id, at })),
                         (messages: Message[]) => says(JSON.stringify(results(messages)))];
      return shown(await instance.harness.prompt("fork it", { session: theirs.session }));
    });
    expect(out["fork"]!.error).toBe(true);
    expect(out["fork"]!.text).toContain("a fork's authority disagrees with its owner");
  });
});

describe("an operation's status (review M3)", () => {
  it("fails loudly for a thread the object never made, never answering running", async () => {
    const r = await agentRig();
    const out = await r.in(async (instance) => instance.agent.status(
      { thread_id: `thread_${"9".repeat(32)}` } as Thread, "op-x")
      .then((status) => status.status, (error: Error) => error.message));
    expect(out).toBe(NO_THREAD);
  });
});

describe("a revoked client's model calls (review M2)", () => {
  /** The gated owner, a model source connected, and a full client admitted. */
  async function spending() {
    const r = await gated();
    await r.connect(`sk-${crypto.randomUUID().slice(0, 8)}`);
    const at = signedIn();
    await r.stub.outsideAgents(r.bind({ origin: MUSE, source: MCP, authenticated_at: at }));
    const stamp: Stamped = { client: MUSE.client, generation: 1, authenticated_at: at,
                             universe: null, agent: "main" };
    const fetches = (instance: GatedOwnerDO) =>
      instance.calls.filter((call) => call.method === "fetch").length;
    return { r, stamp, fetches };
  }

  it("are refused while the conversation's selection drops the gate, its grant standing; " +
     "the owner's are not", async () => {
    const { r, stamp, fetches } = await spending();
    const out = await r.in(async (instance) => {
      const thread = await instance.agent.thread("main", stamp);
      const drop = async (session?: string) => (await (await instance.harness.pi(
        session === undefined ? {} : { session })).conversation(1 as ConversationId, BG))!
        .configure({ extensions: { remove: [{ name: GATE } as never] } }, BG);
      await drop(thread.thread_id);
      await drop();
      const before = fetches(instance);
      const client = await instance.harness.prompt(
        "cmd:echo", { session: thread.thread_id });
      const between = fetches(instance);
      const owner = await instance.harness.prompt("cmd:echo");
      return { client: client.status, owner: owner.status,
               sent: [between - before, fetches(instance) - between] };
    });
    expect(out).toEqual({ client: "unanswered", owner: "done", sent: [0, 1] });
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.rule]))
      .toContainEqual(["refused", AUTHORITY_RULE]);
  });

  it("read the gate as kept only by a selection that keeps it", () => {
    expect([keepsGate(undefined), keepsGate([GATE, "pkg"]), keepsGate(["pkg"]),
            keepsGate({ remove: [GATE] }), keepsGate({ add: ["pkg"] }),
            keepsGate({ remove: ["pkg"] })])
      .toEqual([true, true, false, false, true, true]);
  });

  it("are refused when the authority cannot be read", async () => {
    const { r, stamp, fetches } = await spending();
    const out = await r.in(async (instance) => {
      const thread = await instance.agent.thread("main", stamp);
      instance.agent.modelAuthority = async () => {
        throw new Error("the authority could not be read");
      };
      const before = fetches(instance);
      const answer = await instance.harness.prompt(
        "cmd:echo", { session: thread.thread_id });
      return { status: answer.status, sent: fetches(instance) - before };
    });
    expect(out).toEqual({ status: "unanswered", sent: 0 });
  });

  it("are not sent when the revoke commits between the reservation and the dispatch",
     async () => {
    const { r, stamp, fetches } = await spending();
    const out = await r.in(async (instance, state) => {
      const thread = await instance.agent.thread("main", stamp);
      const agent = instance.agent;
      const real = agent.modelAuthority.bind(agent);
      let checks = 0;
      // The revoke commits with the reservation, after its check: no abort reaches the turn.
      agent.modelAuthority = async (pi, session) => {
        const rule = await real(pi, session);
        return () => {
          const said = rule();
          if (++checks === 1) {
            new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
          }
          return said;
        };
      };
      const before = fetches(instance);
      const answer = await instance.harness.prompt("cmd:echo",
                                                { session: thread.thread_id });
      return { answer, checks, sent: fetches(instance) - before };
    });
    expect([out.answer.status, out.checks, out.sent]).toEqual(["unanswered", 2, 0]);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.rule]))
      .toEqual([["not_sent", AUTHORITY_RULE]]);
  });

  it("are refused when the revoke's abort never ran (an eviction between its commit and " +
     "its abort), and a call naming no conversation is refused too", async () => {
    const { r, stamp, fetches } = await spending();
    const out = await r.in(async (instance, state) => {
      const thread = await instance.agent.thread("main", stamp);
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
      const before = fetches(instance);
      const answer = await instance.harness.prompt(
        "cmd:echo", { session: thread.thread_id });
      // A call of the owner's own thread's Session, and one of no Session at all.
      const bound = await provider(instance);
      const stray = (sessionId: string | undefined, through = bound) => through.streamSimple(
        instance.parts.provider.getModels()[0]!, asked("cmd:echo"),
        { ...(sessionId === undefined ? {} : { sessionId }),
          telemetry: originContext("input:stray") }).result();
      const strays = [(await stray(crypto.randomUUID())).errorMessage,
                      (await stray(undefined)).errorMessage,
                      (await stray(crypto.randomUUID(), instance.parts.provider)).errorMessage];
      return { answer, strays, sent: fetches(instance) - before };
    });
    expect([out.answer.status, out.sent, out.strays])
      .toEqual(["unanswered", 0, [NO_CONVERSATION, NO_CONVERSATION, NO_CONVERSATION]]);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.rule]))
      .toContainEqual(["refused", AUTHORITY_RULE]);
  });
});

describe("the agent's threads (review L3, L4, L5, L8)", () => {
  it("are bounded per client, across clients and for the owner, hold the client's grant " +
     "row, and go with its grant's change", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    await r.admit(DOT);
    const made = await r.in(async (instance, state) => {
      const threads = new Threads(state.storage, { ...THREAD_LIMITS, perClient: 3, clients: 5,
                                                   owner: 2 }, undefined, undefined,
                                  registeredOn(state.storage));
      // The owner's bound is its conversations', one an agent: its runs have their own.
      const tried = async (stamp: Stamped | null, times: number, kind: ThreadKind = "run") => {
        const out: string[] = [];
        for (let i = 0; i < times; i++) {
          const agent = kind === "converse" ? `agent${i}` : "main";
          out.push(await new WriteGate(state.storage).write(() =>
            threads.record(agent, stamp === null ? null : { ...stamp, agent }, clock(), kind)
              .thread_id.slice(0, 7))
            .catch((error: Error) => error.message));
        }
        return out;
      };
      // A client's bounds are its conversations', as the owner's are; runs have their own.
      return { muse: await tried(r.stampOf(MUSE, 1), 4, "converse"),
               dot: await tried(r.stampOf(DOT, 1), 3, "converse"),
               owner: await tried(null, 3, "converse") };
    });
    expect(made).toEqual({
      muse: ["thread_", "thread_", "thread_", "this client's threads are at their limit"],
      dot: ["thread_", "thread_", "the outside clients' threads are at their limit"],
      owner: ["thread_", "thread_", "the owner's threads are at their limit"],
    });
    const held = async () => (await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === MUSE.client)!.held;
    expect(await held()).toBe(3);
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect(await held()).toBe(0);
    // Its threads gone with its grant, the registration they named goes at the owner's
    // delete, and the owner may forget the revoked client.
    const [registration] = await r.in((_instance, state) => state.storage.sql.exec<
      { agent_id: string }>("SELECT agent_id FROM outside_agents WHERE outside_client = ?",
                            MUSE.client).toArray());
    await r.stub.deleteAgent(r.session(), { agent_id: registration!.agent_id });
    expect(await r.in((_instance, state) => state.storage.sql.exec(
      "SELECT 1 FROM outside_agents WHERE outside_client = ?", MUSE.client).toArray()))
      .toEqual([]);
    expect((await r.stub.grantForgetRevoked(r.session(), { clients: [MUSE.client] })).forgotten)
      .toEqual([MUSE.client]);
  });

  it("are refused before any thread or store is made, when the stamp no longer stands or " +
     "the bound is met", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      await instance.harness.pi();
      const sql = state.storage.sql;
      const conversations = () => sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM agent_threads").one().n + instance.agent.host.openThreads()
        .length;
      // Its conversations, one an agent, to its bound (its runs have their own).
      const converse = (i: number, generation: number) => instance.agent.thread(
        `agent-${i}`, { ...r.stampOf(MUSE, generation), agent: `agent-${i}` },
        { kind: "converse" });
      for (let i = 0; i < 64; i++) {
        await converse(i, 1);
      }
      const before = conversations();
      const refused = [
        await converse(64, 1).then(() => "made", (error: Error) => error.message),
        await converse(65, 2).then(() => "made", (error: Error) => error.message),
      ];
      return { refused, made: conversations() - before };
    });
    expect(out).toEqual({ refused: ["this client's threads are at their limit", AUTHORITY_CHANGED],
                          made: 0 });
  });

  it("each name a store of their own, by schema, and a client recorded after its threads " +
     "counts them", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const made = await instance.agent.thread("main", r.stampOf(MUSE, 1));
      const sql = state.storage.sql;
      const insert = (store: string | null) => failure(async () => sql.exec(
        "INSERT INTO agent_threads (thread_id, store, state, agent_id, created_at, kind, " +
        "last_active) VALUES (?, ?, 'ready', 'main', 1, 'run', 1)", `thread_${"1".repeat(32)}`,
        store));
      const none = await insert(null);
      const odd = await insert("g".repeat(32));
      const taken = await insert(made.store);
      // A thread's row kept for a client whose grant row is written after it (the schema
      // orders nothing between them) is counted when the grant row is.
      sql.exec("INSERT INTO agent_threads (thread_id, store, state, agent_id, client, " +
        "generation, created_at, kind, last_active) VALUES (?, ?, 'ready', 'main', ?, 1, 1, " +
        "'run', 1)", `thread_${"2".repeat(32)}`, "2".repeat(32), DOT.client);
      // A store never changes, and a state moves only to dropping.
      const moved = await failure(async () => sql.exec(
        "UPDATE agent_threads SET store = ? WHERE thread_id = ?", "3".repeat(32),
        made.thread_id));
      const back = await failure(async () => {
        sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ?",
                 made.thread_id);
        sql.exec("UPDATE agent_threads SET state = 'ready' WHERE thread_id = ?",
                 made.thread_id);
      });
      return { none: none.message, odd: odd.message, taken: taken.message,
               moved: moved.message, back: back.message,
               state: sql.exec<{ state: string }>("SELECT state FROM agent_threads WHERE " +
                 "thread_id = ?", made.thread_id).one().state };
    });
    expect(out.none).toMatch(/NOT NULL constraint failed/);
    expect(out.odd).toMatch(/CHECK constraint failed/);
    expect(out.taken).toMatch(/UNIQUE constraint failed/);
    expect(out.moved).toMatch(/never changes the stamp/);
    expect(out.back).toMatch(/never changes the stamp/);
    expect(out.state).toBe("dropping");
    await r.admit(DOT);
    expect((await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === DOT.client)!.held).toBe(1);
  });

  it("re-check, in the recording unit, that the stamp still stands", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      await instance.harness.pi();
      const held = gate();
      instance.probes.writeGate = held.promise;
      const writes = instance.probes.writes;
      const making = instance.agent.thread("main", r.stampOf(MUSE, 1))
        .then(() => "made", (error: Error) => error.message);
      await until(() => instance.probes.writes > writes, "the recording unit to be asked for");
      // The stamp stood when the thread was asked for; it is revoked before its unit.
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
      instance.probes.writeGate = null;
      held.release();
      return { made: await making,
               rows: state.storage.sql.exec("SELECT * FROM agent_threads WHERE " +
                                            "client IS NOT NULL").toArray().length };
    });
    expect(out).toEqual({ made: AUTHORITY_CHANGED, rows: 0 });
  });

  it("never change the stamp they were made under", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    await r.in((instance) => instance.agent.thread("main", r.stampOf(MUSE, 1)));
    await expect(r.in((_instance, state) => state.storage.sql.exec(
      "UPDATE agent_threads SET generation = 9"))).rejects.toThrow(/never changes the stamp/);
    // Nor is one recorded under a stamp for another agent.
    await expect(r.in((_instance, state) => new Threads(state.storage).record(
      "main", { ...r.stampOf(MUSE, 1), agent: "helper" }, 1)))
      .rejects.toThrow(/names its own agent/);
  });

});

describe("the authority's checks", () => {
  it("fail loudly, never as a refusal, when the grants cannot be read (review L8)", async () => {
    const authority = new Authority({
      grants: { standing: () => {
        throw new Error("the grants could not be read");
      } },
      receipt: async () => undefined,
    });
    const client = { by: "client" as const, stamp: { client: MUSE.client, generation: 1,
                                                     authenticated_at: null, universe: null,
                                                     agent: "main" } };
    expect(() => authority.refusal(client, ["read_files"])).toThrow(/could not be read/);
    await expect(authority.decide(client, { name: "read", arguments: {} }))
      .rejects.toThrow(/could not be read/);
    expect(authority.refusal({ by: "owner" }, null)).toBeUndefined();
    expect(authority.refusal({ by: "none" }, [])).toBe(NO_AUTHORITY);
    expect(authority.modelRule({ by: "none" })).toBe(AUTHORITY_RULE);
  });

  it("run every tool only under a grant that says full: any other mode is held to its scopes",
     () => {
    const under = (mode: string) => new Authority({
      grants: { standing: () => ({ mode, holds: () => false }) },
      receipt: async () => undefined,
    });
    const client = { by: "client" as const, stamp: { client: MUSE.client, generation: 1,
                                                     authenticated_at: null, universe: "alpha",
                                                     agent: "main" } };
    expect(["full", "scoped", "revoked", "unknown"].map((mode) =>
      under(mode).refusal(client, ["read_files"]) ?? "runs"))
      .toEqual(["runs", NOT_GRANTED, NOT_GRANTED, NOT_GRANTED]);
  });
});
