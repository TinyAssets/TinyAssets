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
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import { type ConversationId, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { GATE, keepsGate, STATUS_WAIT_MS, WAIT_ENDED } from "../src/agent";
import {
  Authority, AUTHORITY_CHANGED, AUTHORITY_RULE, capabilityOf, type Need, NO_AUTHORITY,
  NO_CONVERSATION, NO_SHELL, NOT_GRANTED, type Stamped, stamped, TA_CAPABILITIES,
} from "../src/authority";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import { originContext } from "../src/origin";
import { type Binding, workRunner } from "../src/owner-do";
import { Policy, policyDigest } from "../src/policy";
import { Scheduled, type WorkTarget } from "../src/scheduled";
import { Threads } from "../src/threads";
import { asked, gated, until } from "./gateway-support";
import { DOT, MCP, MUSE, signedIn } from "./ledger-support";
import { drainDue, failure } from "./support";
import { type FauxOwnerDO, type GatedOwnerDO, says, storeScript } from "./worker";

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
  ["read it", [fauxToolCall("read", { path: "notes.md" })]],
  ["shell it", [fauxToolCall("bash", { command: "ls" })]],
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
  it("names read_files for read, write_files for write, both for edit, none to grant for " +
     "the shell, and no capability for anything else, ta's until A3 fills its map", () => {
    const of = (name: string, args: unknown = {}) => capabilityOf({ name, arguments: args });
    expect([of("read"), of("write"), of("edit"), of("bash")])
      .toEqual([["read_files"], ["write_files"], ["read_files", "write_files"], null]);
    expect([of("hold"), of("toString"), of("__proto__"), of("ta", { op: "catalog" }),
            of("ta", { op: "call", name: "policy:show" }), of("ta", null)])
      .toEqual([undefined, undefined, undefined, undefined, undefined, undefined]);
  });

  it("reads ta's map by the op or the capability called, the ta: names the ops' alone",
     () => {
    expect(TA_CAPABILITIES).toEqual({});
    // A map as A3 fills it: the catalog's need, and a capability's by the name it calls.
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
    // Each receipt is of the client's call: its digest names the client, the tool, the reason.
    const requests = [["write", NOT_GRANTED], ["edit", NOT_GRANTED], ["bash", NO_SHELL],
                      ["pass", NOT_GRANTED]].map(([tool, reason]) => policyDigest({
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
      // The door returned once the work it ended had settled, within its wait.
      const atOnce = (await instance.agent.status(turns.muse.operationId, muse.session)).status;
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
      return { atOnce, ended, running, done, next, heard, waits: { muse: await waits.muse,
                                                           dot: await waits.dot } };
    });
    expect(out.atOnce).toBe("unanswered");
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

  it("returns from its door once the work it ended settled, a tool slow to stop included",
     async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.probes.holdLinger = 200;
      const { session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const { operationId } = await instance.agent.harness.submit("hold please", { session });
      await until(() => instance.probes.holds === 1, "the hold to start");
      await instance.grantRevoke(r.bind(null), { client: MUSE.client });
      return (await instance.agent.status(operationId, session)).status;
    });
    expect(out).toBe("unanswered");
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
      const began = Date.now();
      const open = [await instance.agent.status(first.operationId, session),
                    await instance.agent.status(second.operationId, session)];
      // An open operation's status is its pending record, read at once: no wait on it.
      const took = Date.now() - began;
      open.push(await instance.agent.status("op-unknown", session));
      held.release();
      await instance.agent.harness.wait(second.operationId, { session });
      return { open, took, settled: [await instance.agent.status(first.operationId, session),
                                     await instance.agent.status(second.operationId, session)] };
    });
    expect(out.took).toBeLessThan(STATUS_WAIT_MS);
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
      const runner = workRunner(new Threads(state.storage), instance.agent.harness,
                                instance.parts.authority);
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
      const runner = workRunner(new Threads(state.storage), instance.agent.harness,
                                instance.parts.authority);
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
      const runner = workRunner(new Threads(state.storage), instance.agent.harness,
                                instance.parts.authority);
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

describe("the authority's enforcement (review M1)", () => {
  it("holds a conversation to its authority in its environment, whatever extensions it " +
     "selects", async () => {
    const r = await agentRig();
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/notes.md", expected_etag: null,
                                           content: text("the secret milk") });
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["write_files"]);  // no read_files
    const out = await r.in(async (instance) => {
      const { thread, session } = await threadFor(instance, r.stampOf(MUSE, generation));
      const pi = await instance.agent.harness.pi();
      const conversation = (await pi.conversation(thread.conversation_id as ConversationId,
                                                  BG))!;
      await conversation.configure({ extensions: { remove: [{ name: "ta.authority" } as never] } },
                                   BG);
      return {
        read: shown(await instance.agent.harness.prompt("read it", { session }))["read"]!,
        shell: shown(await instance.agent.harness.prompt("shell it", { session }))["bash"]!,
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
      return { operationId: (await instance.agent.harness.submit("read it", { session }))
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
      await instance.agent.harness.wait(operationId, { session });
      return { reads: instance.probes.reads,
               shown: results((await instance.agent.harness.messages({ session }))
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
      const pi = await instance.agent.harness.pi();
      const made = await pi.createConversation({ ownership: { kind: "ownerless" },
        agent: { model: { provider: "gateway", modelId: "auto" } } }, BG);
      const state = await pi.snapshot(instance.agent.authority, made.id, BG);
      return { state, read: shown(await instance.agent.harness.prompt(
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
      const own = await threadFor(instance, null);
      expect((await instance.agent.harness.prompt("noted", { session: own.session })).status)
        .toBe("done");
      const pi = await instance.agent.harness.pi();
      const ownConversation = (await pi.conversation(own.thread.conversation_id as never, BG))!;
      const at = (await ownConversation.context(BG)).entries.at(-1)!.id;
      const theirs = await threadFor(instance, r.stampOf(MUSE, 1));
      // A client's tool forks the owner's thread, as a conversation its call owns.
      instance.script = [calls(fauxToolCall("fork", { from: own.thread.conversation_id, at })),
                         (messages: Message[]) => says(JSON.stringify(results(messages)))];
      return shown(await instance.agent.harness.prompt("fork it", { session: theirs.session }));
    });
    expect(out["fork"]!.error).toBe(true);
    expect(out["fork"]!.text).toContain("a fork's authority disagrees with its owner");
  });
});

describe("an operation's status (review M3)", () => {
  it("fails loudly for a session pi never made, never answering running", async () => {
    const r = await agentRig();
    const out = await r.in(async (instance) => instance.agent.status("op-x", "987654")
      .then((status) => status.status, (error: Error) => error.message));
    expect(out).toMatch(/Unknown pi session 987654/);
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

  it.each([["run to its end", true], ["only started", false]])(
    "are refused in their reservation when a revoke %s between a hand-off's mark and its " +
    "submit misses the work in its abort", async (_how, awaited) => {
    const { r, stamp, fetches } = await spending();
    const out = await r.in(async (instance, state) => {
      const thread = await instance.agent.thread("main", stamp);
      const runner = workRunner(new Threads(state.storage), instance.agent.harness,
                                instance.parts.authority);
      const before = fetches(instance);
      let revoke: Promise<unknown> = Promise.resolve();
      const answer = await runner.handOff({
        work_id: "work_r", claim_id: "claim_x", kind: "turn",
        payload: { thread: thread.thread_id, input: "cmd:echo" },
        stamp: { client: MUSE.client, generation: 1, authenticated_at: stamp.authenticated_at },
      }, async () => {
        revoke = instance.grantRevoke(r.bind(), { client: MUSE.client });
        if (awaited) {
          await revoke;
        }
      });
      await revoke;
      const settled = await instance.agent.harness.wait(
        "work_r", { session: String(thread.conversation_id) });
      return { answer, settled, sent: fetches(instance) - before };
    });
    expect(out.answer).toEqual({ state: "handed" });
    expect([out.settled.status, out.sent]).toEqual(["unanswered", 0]);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    const states = receipts.map((receipt) => [receipt.state, receipt.rule]);
    if (awaited) {
      expect(states).toEqual([["refused", AUTHORITY_RULE]]);
    } else {  // refused, or aborted before any reserve: never a call that spent
      expect(states.filter(([state, rule]) => state !== "refused" || rule !== AUTHORITY_RULE))
        .toEqual([]);
    }
  });

  it("are refused while the conversation's selection drops the gate, its grant standing; " +
     "the owner's are not", async () => {
    const { r, stamp, fetches } = await spending();
    const out = await r.in(async (instance) => {
      const thread = await instance.agent.thread("main", stamp);
      const pi = await instance.agent.harness.pi();
      const drop = async (id: number) => (await pi.conversation(id as ConversationId, BG))!
        .configure({ extensions: { remove: [{ name: GATE } as never] } }, BG);
      await drop(thread.conversation_id);
      await drop(1);
      const before = fetches(instance);
      const client = await instance.agent.harness.prompt(
        "cmd:echo", { session: String(thread.conversation_id) });
      const between = fetches(instance);
      const owner = await instance.agent.harness.prompt("cmd:echo");
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
      const answer = await instance.agent.harness.prompt(
        "cmd:echo", { session: String(thread.conversation_id) });
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
      agent.modelAuthority = async (session) => {
        const rule = await real(session);
        return () => {
          const said = rule();
          if (++checks === 1) {
            new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
          }
          return said;
        };
      };
      const before = fetches(instance);
      const answer = await agent.harness.prompt("cmd:echo",
                                                { session: String(thread.conversation_id) });
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
      const answer = await instance.agent.harness.prompt(
        "cmd:echo", { session: String(thread.conversation_id) });
      const stray = (sessionId: string | undefined) => instance.parts.provider.streamSimple(
        instance.parts.provider.getModels()[0]!, asked("cmd:echo"),
        { ...(sessionId === undefined ? {} : { sessionId }),
          telemetry: originContext("input:stray") }).result();
      const strays = [(await stray(crypto.randomUUID())).errorMessage,
                      (await stray(undefined)).errorMessage];
      return { answer, strays, sent: fetches(instance) - before };
    });
    expect([out.answer.status, out.sent, out.strays])
      .toEqual(["unanswered", 0, [NO_CONVERSATION, NO_CONVERSATION]]);
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
      const threads = new Threads(state.storage, { perClient: 3, clients: 5, owner: 2 });
      const tried = async (stamp: Stamped | null, times: number) => {
        const out: string[] = [];
        for (let i = 0; i < times; i++) {
          const pi = await instance.agent.harness.pi();
          const conversation = await pi.createConversation(
            { ownership: { kind: "ownerless" } }, BG);
          out.push(await new WriteGate(state.storage).write(() =>
            threads.record(conversation.id, "main", stamp, clock()).thread_id.slice(0, 7))
            .catch((error: Error) => error.message));
        }
        return out;
      };
      return { muse: await tried(r.stampOf(MUSE, 1), 4), dot: await tried(r.stampOf(DOT, 1), 3),
               owner: await tried(null, 3) };
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
    // Its threads gone with its grant, the owner may forget the revoked client.
    expect((await r.stub.grantForgetRevoked(r.session(), { clients: [MUSE.client] })).forgotten)
      .toEqual([MUSE.client]);
  });

  it("are refused before any conversation is made, when the stamp no longer stands or the " +
     "bound is met", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      await instance.agent.harness.pi();
      const sql = state.storage.sql;
      const table = sql.exec<{ name: string }>("SELECT name FROM sqlite_master WHERE " +
        "type = 'table' AND name LIKE '%conversations'").one().name;
      const conversations = () => sql.exec<{ n: number }>(
        `SELECT COUNT(*) AS n FROM "${table}"`).one().n;
      for (let i = 0; i < 64; i++) {
        await instance.agent.thread("main", r.stampOf(MUSE, 1));
      }
      const before = conversations();
      const refused = [
        await instance.agent.thread("main", r.stampOf(MUSE, 1))
          .then(() => "made", (error: Error) => error.message),
        await instance.agent.thread("main", r.stampOf(MUSE, 2))
          .then(() => "made", (error: Error) => error.message),
      ];
      return { refused, made: conversations() - before };
    });
    expect(out).toEqual({ refused: ["this client's threads are at their limit", AUTHORITY_CHANGED],
                          made: 0 });
  });

  it("are never the root, by schema, and a client recorded after its threads counts them",
     async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      await instance.agent.thread("main", r.stampOf(MUSE, 1));
      const root = await failure(async () =>
        new Threads(state.storage).record(1, "main", null, 1));
      const sql = state.storage.sql;
      const raw = await failure(async () => sql.exec(
        "INSERT INTO agent_threads (thread_id, conversation_id, agent_id, created_at) " +
        "VALUES (?, 1, 'main', 1)", `thread_${"1".repeat(32)}`));
      // A thread's row kept for a client whose grant row is written after it (the schema
      // orders nothing between them) is counted when the grant row is.
      sql.exec("INSERT INTO agent_threads (thread_id, conversation_id, agent_id, client, " +
        "generation, created_at) VALUES (?, 999, 'main', ?, 1, 1)", `thread_${"2".repeat(32)}`,
        DOT.client);
      return { root: root.message, raw: raw.message };
    });
    expect(out.root).toMatch(/never the root/);
    expect(out.raw).toMatch(/CHECK constraint failed/);
    await r.admit(DOT);
    expect((await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === DOT.client)!.held).toBe(1);
  });

  it("re-check, in the recording unit, that the stamp still stands", async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      await instance.agent.harness.pi();
      const queue = holdQueue(state);
      const making = instance.agent.thread("main", r.stampOf(MUSE, 1))
        .then(() => "made", (error: Error) => error.message);
      for (let i = 0; i < 20; i++) {
        await Promise.resolve();
      }
      // The stamp stood when the thread was asked for; it is revoked before its unit.
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
      queue.release();
      await queue.done;
      return { made: await making,
               rows: state.storage.sql.exec("SELECT * FROM agent_threads").toArray().length };
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
      2, "main", { ...r.stampOf(MUSE, 1), agent: "helper" }, 1)))
      .rejects.toThrow(/names its own agent/);
  });

  it("hand stamped work over only at the work's own generation, its entry naming the client",
     async () => {
    const r = await agentRig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const { thread, session } = await threadFor(instance, r.stampOf(MUSE, 1));
      const runner = workRunner(new Threads(state.storage), instance.agent.harness,
                                instance.parts.authority);
      let marked = 0;
      const mark = async () => {
        marked++;
      };
      const handOff = (workId: string, generation: number) => runner.handOff({
        work_id: workId, claim_id: "claim_x", kind: "turn",
        payload: { thread: thread.thread_id, input: "noted" },
        stamp: { client: MUSE.client, generation, authenticated_at: r.at } }, mark);
      const other = await handOff("work_g", 2);
      const own = await handOff("work_h", 1);
      await instance.agent.harness.wait("work_h", { session });
      const entries = (await instance.agent.harness.messages({ session }))
        .filter((entry) => entry.kind === "ta.work").map((entry) => entry.data);
      // A grant moved by a writer that kept the thread: its own generation no longer stands.
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
      const lapsed = await handOff("work_i", 1);
      return { other, own, lapsed, marked, entries };
    });
    expect(out.other).toEqual({ state: "failed", reason: "thread_authority" });
    expect(out.lapsed).toEqual({ state: "failed", reason: "thread_authority" });
    expect([out.own, out.marked]).toEqual([{ state: "handed" }, 1]);
    expect(out.entries).toEqual([{ work_id: "work_h", client: MUSE.client }]);
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
