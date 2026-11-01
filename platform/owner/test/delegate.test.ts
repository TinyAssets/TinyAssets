/**
 * Subagents (tasks 5, A5 with F5; pi-durable's example 22): `ta`'s `agent:delegate` makes a
 * conversation its call owns, in the thread's own Session, configured as the named agent,
 * holding the caller's authority and the five model tools; its calls are the caller's turn,
 * under its caps; a rerun after an eviction finds the child and its input again; aborting
 * the caller aborts it, and the caller is idle only once it is; a thread holds a bounded
 * number of working conversations; a linked admission whose turn has no row is refused
 * before pi admits it; and a revoke, a suspension and a policy write end the child.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import {
  type ConversationId, type ConversationRecord, type JsonObject, ROOT_CONVERSATION_ID,
  type SubmissionRecord,
} from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { PLATFORM_CALLER } from "../src/admission";
import { ledgerOn } from "./ledger-support";
import { NOT_GRANTED, type Stamped } from "../src/authority";
import { THREAD_CONVERSATIONS_FULL } from "../src/delegate";
import { type Origin } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import {
  THREAD_CHILD_INPUTS, THREAD_CONVERSATIONS, THREAD_INPUTS, WORKING_THREADS,
} from "../src/schema";
import {
  HOST_TIMING, OPEN_MAX, THREAD_CHILD_INPUTS_FULL, THREAD_INPUTS_FULL, TURNS_UNKEPT,
} from "../src/thread-host";
import {
  THREADS_WORKING, THREADS_WORKING_CLIENTS, THREADS_WORKING_SHARE,
} from "../src/threads";
import { capTurnOwners, failure } from "./support";
import { gated } from "./gateway-support";
import { DOT, MCP, MUSE, signedIn } from "./ledger-support";
import { sqlIn } from "./support";
import { type FauxOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
/** The schema's compaction bound, which a capped trigger keeps. */
const THREAD_COMPACTIONS_ALL = THREAD_CONVERSATIONS * 2;
const ROOT = ROOT_CONVERSATION_ID;
const text = (value: string): Uint8Array => new TextEncoder().encode(value);

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

function viaTa(name: string, args: JsonObject): FauxContentBlock {
  return fauxToolCall("ta", { op: "call", name, arguments: args });
}

/** Every tool result the model was shown, in order: the `ta` call's name, text and error. */
function resultsOf(messages: readonly Message[]):
    { name: string; text: string; error: boolean }[] {
  const called = new Map<string, string>();
  const out: { name: string; text: string; error: boolean }[] = [];
  for (const message of messages) {
    if (message.role === "assistant") {
      for (const part of message.content) {
        if (part.type === "toolCall") {
          called.set(part.id, part.name === "ta" ? String(part.arguments["name"]) : part.name);
        }
      }
    }
    if (message.role === "toolResult") {
      out.push({ name: called.get(message.toolCallId) ?? message.toolName,
                 text: message.content.map((part) => (part.type === "text" ? part.text : ""))
                   .join(""),
                 error: message.isError });
    }
  }
  return out;
}

/** The last user message's text. */
function lastAsked(messages: readonly Message[]): string {
  const last = [...messages].reverse().find((message) => message.role === "user");
  const content = last?.content;
  return typeof content === "string" ? content
    : (content ?? []).map((part) => (part.type === "text" ? part.text : "")).join("");
}

/** A section the request's transcript holds, its system messages replayed. */
function sectionOf(messages: readonly Message[], key: string): string | undefined {
  let shown: string | undefined;
  for (const message of messages as unknown as { role: string;
                                                  sections?: Record<string, string | null> }[]) {
    if (message.role === "system" && message.sections !== undefined && key in message.sections) {
      shown = message.sections[key] ?? undefined;
    }
  }
  return shown;
}

const HOLD = viaTa("hold", {});
/** What each ask calls, the first that matches; a delegation's own task among them. */
const ASKS: readonly [string, (messages: Message[]) => AssistantMessage][] = [
  ["delegate thrice", () => calls(...Array.from({ length: THREAD_CONVERSATIONS }, () =>
    viaTa("agent:delegate", { task: "hold please" })))],
  ["delegate twice", () => calls(...Array.from({ length: THREAD_CONVERSATIONS - 1 }, () =>
    viaTa("agent:delegate", { task: "hold please" })))],
  ["delegate to helper", () => calls(viaTa("agent:delegate", { agent: "helper",
                                                               task: "who are you" }))],
  ["delegate hold", () => calls(viaTa("agent:delegate", { task: "hold please" }))],
  ["delegate it", () => calls(viaTa("agent:delegate", { task: "child work" }))],
  ["delegate background", () => calls(viaTa("agent:delegate", { task: "start background" }))],
  ["start background", () => calls(viaTa("background", { work: "late work" }))],
  ["delegate a sleeper", () => calls(viaTa("agent:delegate", { task: "background and hold" }))],
  ["background and hold", () => calls(viaTa("background", { work: "late work" }), HOLD)],
  ["hold please", () => calls(HOLD)],
  ["propose it", () => calls(viaTa("policy:propose", { change: { note: "x" } }))],
  ["hold safely", () => calls(viaTa("safe_hold", {}))],
  ["delegate too much", () => calls(viaTa("agent:delegate", { task: "\u{1F600}".repeat(32_001) }))],
  ["who are you", (messages) => says(`I am: ${sectionOf(messages, "instructions") ?? "none"}`)],
];

/** The model: a tool round answered with what the tools said, else by its ask. */
function byAsk(messages: Message[]): AssistantMessage {
  if (messages.at(-1)?.role === "toolResult") {
    return says(JSON.stringify(resultsOf(messages)));
  }
  const asked = lastAsked(messages);
  const answer = ASKS.find(([what]) => asked.includes(what));
  return answer === undefined ? says(`answered: ${asked}`) : answer[1](messages);
}

/** The delegations' results a run's answer reports. */
function delegated(answer: { text?: string }): { text: string; error: boolean }[] {
  return (JSON.parse(answer.text!) as { name: string; text: string; error: boolean }[])
    .filter((result) => result.name === "agent:delegate")
    .map(({ text, error }) => ({ text, error }));
}

function gate(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

async function until(check: () => Promise<boolean> | boolean, what: string): Promise<void> {
  for (let tries = 0; tries < 500; tries++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** An owner of its own on the faux-model class, signed in, its model answering `byAsk`. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = () => env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: Origin | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin, ...(origin === null ? {} : { source: MCP }), ...extra });
  const { session: cookie } = await stub().sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => bind(null, { session: cookie });
  const inside = <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
    runInDurableObject(stub(), act);
  await inside((instance) => {
    instance.script = Array.from({ length: 200 }, () => byAsk);
  });
  return {
    owner, stub, bind, session, in: inside,
    platform: { owner, caller: PLATFORM_CALLER, origin: null } as Binding,
    write: (path: string, content: string) => stub().fileWrite(bind(null), {
      path, expected_etag: null, content: text(content) }),
    admit: async (origin: Origin) => {
      await stub().outsideAgents(bind(origin, { authenticated_at: at }));
      await runInDurableObject(stub(), (_instance, state) => ledgerOn(state.storage).register(
        origin, MCP, { command_center_id: "alpha", display_name: "Muse",
                       delivery_method: "pull", recipe_id: null }));
    },
    /** The owner scopes the client to `capabilities` in universe `alpha` as agent `main`. */
    scoped: async (origin: Origin, capabilities: string[]) => {
      const row = (await stub().grantList(bind(null))).clients
        .find((client) => client.client === origin.client);
      return (await stub().grantSet(session(), {
        client: origin.client, source: "mcp", mode: "scoped", name: "",
        scopes: capabilities.map((capability) => ({ universe: "alpha", agent: "main",
                                                    capability })),
        expected_revision: row?.revision ?? 0 })).generation;
    },
    stampOf: (origin: Origin, generation: number): Stamped => ({
      client: origin.client, generation, authenticated_at: at, universe: "alpha", agent: "main",
    }),
  };
}

/** A thread's conversations, as its store holds them. */
async function conversationsOf(instance: FauxOwnerDO, thread: string):
    Promise<readonly ConversationRecord[]> {
  return instance.agent.host.use(thread, async ({ storage }) =>
    (await storage.scanConversations({}, 20, undefined, BG)).items);
}

/** A conversation's submissions, as its thread's store holds them. */
async function submissionsOf(instance: FauxOwnerDO, thread: string, conversation: number):
    Promise<readonly SubmissionRecord[]> {
  return instance.agent.host.use(thread, async ({ storage }) =>
    (await storage.scanSubmissions({ conversationId: conversation as ConversationId }, 20,
                                   undefined, BG)).items);
}

describe("a delegation to a subagent", () => {
  it("makes a conversation its call owns, configured as the agent named, holding the " +
     "caller's authority and the five tools, its calls the caller's turn", async () => {
    const r = await rig();
    await r.write("/workspace/agents/helper/AGENTS.md", "You are the helper.");
    const out = await r.in(async (instance) => {
      const answer = await instance.harness.prompt("delegate to helper");
      const thread = await instance.harness.thread();
      const pi = await instance.harness.pi();
      const conversations = await conversationsOf(instance, thread);
      const child = conversations.find((record) => record.id !== ROOT)!;
      return {
        answer, conversations, child,
        authority: [await pi.snapshot(instance.agent.authority, ROOT, BG),
                    await pi.snapshot(instance.agent.authority, child.id, BG)],
        submissions: await submissionsOf(instance, thread, child.id),
        turns: [...instance.turns], offered: [...instance.offered],
      };
    });
    expect(out.answer.status).toBe("done");
    const [result] = delegated(out.answer);
    expect(result!.error).toBe(false);
    expect(JSON.parse(result!.text)).toEqual({
      conversation_id: Number(out.child.id), status: "done",
      text: "I am: <instructions>\nYou are the helper.\n</instructions>" });
    // Owned by the ta call's own task, in the root, its one input that call's.
    expect(out.conversations).toHaveLength(2);
    expect(out.child.owner).toEqual({ conversationId: ROOT, taskId: expect.any(Number) });
    const task = (out.child.owner as { taskId: number }).taskId;
    expect(out.submissions.map((record) => [record.requestId, record.status]))
      .toEqual([[`subagent:${task}`, "done"]]);
    expect(out.authority[1]).toEqual(out.authority[0]);
    expect(out.authority[0]).toEqual({ by: "owner" });
    // The child's request was the caller's turn, between the caller's two, and every
    // request offered the five.
    expect(out.turns).toHaveLength(3);
    expect(new Set(out.turns).size).toBe(1);
    expect(out.turns[0]).toMatch(/^input:/);
    expect(out.offered.every((tools) => tools.join() === "read,write,edit,bash,ta")).toBe(true);
  });

  it("is found again by a rerun after an eviction, its child and input reused, the child's " +
     "unsafe tool interrupted, never run twice", async () => {
    const r = await rig();
    const before = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      const { operationId } = await instance.harness.submit("delegate hold");
      await until(() => instance.probes.holds === 1, "the child's hold");
      const thread = await instance.harness.thread();
      const child = (await conversationsOf(instance, thread)).find((record) =>
        record.id !== ROOT)!;
      return { operationId, thread, child: Number(child.id) };
    });
    expect(await r.in((_instance, state) => state.abort("evicted mid-delegation"))
      .then(() => "alive", (error: Error) => error.message)).toBe("evicted mid-delegation");
    // The fresh instance answers by its asks too; its alarm resumes the thread's work.
    await r.in((instance) => {
      instance.script = Array.from({ length: 200 }, () => byAsk);
    });
    expect(await runDurableObjectAlarm(r.stub())).toBe(true);
    const out = await r.in(async (instance) => ({
      settled: await instance.harness.wait(before.operationId),
      conversations: (await conversationsOf(instance, before.thread)).map((record) =>
        Number(record.id)),
      submissions: await submissionsOf(instance, before.thread, before.child),
      holds: instance.probes.holds,
    }));
    expect(out.settled.status).toBe("done");
    const [result] = delegated(out.settled);
    const answer = JSON.parse(result!.text) as { conversation_id: number; status: string;
                                                 text: string };
    expect([answer.conversation_id, answer.status]).toEqual([before.child, "done"]);
    // The child's hold came back interrupted; the child answered with that.
    expect(answer.text).toMatch(/interrupt/i);
    expect(out.conversations).toEqual([Number(ROOT), before.child]);
    expect(out.submissions.map((record) => record.status)).toEqual(["done"]);
    expect(out.holds).toBe(0);
  }, 30_000);

  it("is aborted with its call, and its caller is idle only once it is", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      instance.probes.holdLinger = 300;
      const { operationId } = await instance.harness.submit("delegate hold");
      await until(() => instance.probes.holds === 1, "the child's hold");
      const thread = await instance.harness.thread();
      const pi = await instance.harness.pi();
      const child = (await conversationsOf(instance, thread)).find((record) =>
        record.id !== ROOT)!;
      const started = Date.now();
      const aborting = instance.harness.abort();
      await new Promise((resolve) => setTimeout(resolve, 100));
      const busy = pi.wake().active;
      await aborting;
      // The moment the caller's abort resolved, idle: its child had ended already.
      const atIdle = (await submissionsOf(instance, thread, child.id)).map((record) =>
        [record.status, (record as { reason?: string }).reason]);
      return { busy, took: Date.now() - started, atIdle,
               settled: await instance.harness.wait(operationId) };
    });
    // The child's hold lingered past the abort; the caller's abort waited for it.
    expect(out.busy).toBe(true);
    expect(out.took).toBeGreaterThanOrEqual(250);
    expect(out.atIdle).toEqual([["unanswered", "aborted"]]);
    expect(out.settled).toMatchObject({ status: "unanswered", reason: "aborted" });
  });

  it("holds a thread to its working conversations, refused past them, a finished one " +
     "freeing its place", async () => {
    const r = await rig();
    const held = gate();
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = held.promise;
      const { operationId } = await instance.harness.submit("delegate thrice");
      // Two subagents work beside the root; the third delegation is refused at once.
      await until(() => instance.probes.holds === THREAD_CONVERSATIONS - 1, "two holds");
      held.release();
      const thrice = await instance.harness.wait(operationId);
      const again = await instance.harness.prompt("delegate it");
      return { thrice, again, holds: instance.probes.holds,
               conversations: await conversationsOf(instance, await instance.harness.thread()) };
    });
    const results = delegated(out.thrice);
    expect(results.filter((result) => result.error).map((result) => result.text))
      .toEqual([expect.stringContaining(THREAD_CONVERSATIONS_FULL)]);
    expect(results.filter((result) => !result.error)).toHaveLength(THREAD_CONVERSATIONS - 1);
    expect(out.holds).toBe(THREAD_CONVERSATIONS - 1);
    // Both finished, a delegation is taken again.
    expect(delegated(out.again).at(-1)).toEqual({ text: expect.stringContaining("answered: " +
                                                                         "child work"),
                                                 error: false });
    expect(out.conversations).toHaveLength(THREAD_CONVERSATIONS + 1);
  });

  it("counts a child whose own work outlives its aborted call", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.probes.backgroundGate = new Promise(() => undefined);
      instance.probes.holdGate = new Promise(() => undefined);
      await instance.harness.submit("delegate a sleeper");
      await until(() => instance.probes.holds === 1 && instance.probes.background !== null,
                  "the child's background work and hold");
      // The call is aborted: the child's run with it, its background work not.
      await instance.harness.abort();
      instance.probes.holdGate = null;
      const pi = await instance.harness.pi();
      const background = await pi.getTask(instance.probes.background as never, BG);
      const thrice = await instance.harness.prompt("delegate thrice");
      return { background: background?.state.status, thrice };
    });
    expect(out.background).not.toBe("terminal");
    // The root and that child leave room for one more working conversation.
    const results = delegated(out.thrice).slice(-THREAD_CONVERSATIONS);
    expect(results.filter((result) => result.error).map((result) => result.text))
      .toEqual(Array.from({ length: THREAD_CONVERSATIONS - 1 }, () =>
        expect.stringContaining(THREAD_CONVERSATIONS_FULL)));
  });

  it("ends what its child left running once the child answered", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.probes.backgroundGate = new Promise(() => undefined);
      const answer = await instance.harness.prompt("delegate background");
      const pi = await instance.harness.pi();
      const task = await pi.getTask(instance.probes.background as never, BG);
      return { answer, state: task?.state.status, live: pi.wake().active };
    });
    expect(delegated(out.answer).map((result) => JSON.parse(result.text).status))
      .toEqual(["done"]);
    // The child's background work was ended with it: nothing of it holds the thread's cap.
    expect(out.state).toBe("terminal");
    expect(out.live).toBe(false);
  });

  it("refuses a linked admission whose turn has no row at admission, before pi admits it",
     async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const thread = await instance.harness.thread();
      const writes = instance.probes.writes;
      const held = gate();
      instance.probes.writeGate = held.promise;
      const { operationId } = await instance.harness.submit("delegate it");
      // The delegation's admission waits in its unit; meanwhile its turn ends, every owner
      // of it gone, as a turn whose input settled leaves it.
      await until(() => instance.probes.writes > writes, "the admission's unit");
      await sqlIn(r.stub(), "DELETE FROM model_turn_owners WHERE thread = ?", thread);
      instance.probes.writeGate = null;
      held.release();
      const settled = await instance.harness.wait(operationId);
      const child = (await conversationsOf(instance, thread)).find((record) =>
        record.id !== ROOT)!;
      return { settled, child: await submissionsOf(instance, thread, child.id) };
    });
    expect(delegated(out.settled)).toEqual([
      { text: expect.stringContaining("turn_ended"), error: true }]);
    // The child's input never reached pi: no submission of it at all.
    expect(out.child).toEqual([]);
  });

  it("counts its child's input among the thread's subagent inputs, refused past them " +
     "before its child is made", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const thread = await instance.harness.thread();
      await instance.harness.pi();  // open, so its reconcile has run
      // The thread's subagents hold all their inputs (an eviction's leftovers, say).
      await instance.parts.turns.owned(thread, Array.from({ length: THREAD_CHILD_INPUTS },
        (_, n) => ({ owner: `c:${900_000 + n}`, turn: `input:held-${n}` })));
      const answer = await instance.harness.prompt("delegate it");
      return { answer, notices: instance.parts.notices.list().map((notice) => notice.type),
               conversations: (await conversationsOf(instance, thread)).length };
    });
    expect(delegated(out.answer)).toEqual([
      { text: expect.stringContaining(THREAD_CHILD_INPUTS_FULL), error: true }]);
    // Refused before its child was made: nothing of it is left in the thread.
    expect(out.conversations).toBe(1);
    // Refused by the host, never by the schema's bound, which would fail the Session.
    expect(out.notices).not.toContain("session_failed");
  });

  it("leaves a thread's eight inputs to its doors while two subagents work", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const held = gate();
      instance.probes.holdGate = held.promise;
      const { operationId } = await instance.harness.submit("delegate twice");
      await until(() => instance.probes.holds === THREAD_CONVERSATIONS - 1, "two holds");
      // The root's input and seven more fill the doors' bound beside the subagents' inputs.
      const steers = [];
      for (let n = 1; n < THREAD_INPUTS; n++) {
        steers.push((await instance.harness.submit(`steer ${n}`, { whenBusy: n % 2 === 0
          ? "steer" : "followUp" })).accepted);
      }
      const past = await failure(() => instance.harness.submit("one too many"));
      const children = instance.parts.turns.children(await instance.harness.thread());
      held.release();
      await instance.harness.wait(operationId);
      return { steers, past: past.message, children };
    });
    expect(out.steers).toEqual(Array.from({ length: THREAD_INPUTS - 1 }, () => true));
    expect(out.past).toBe(THREAD_INPUTS_FULL);
    expect(out.children).toBe(THREAD_CONVERSATIONS - 1);
  });

  it("takes a task of at most 32,000 code points, an astral one counted once", async () => {
    const r = await rig();
    const answer = await r.in((instance) => instance.harness.prompt("delegate too much"));
    expect(delegated(answer)).toEqual([
      { text: expect.stringContaining("at most 32000 characters"), error: true }]);
  });

  it("leaves the doors' inputs their bound while a delegation's admission is in flight",
     async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      await instance.harness.thread();
      const writes = instance.probes.writes;
      const held = gate();
      instance.probes.writeGate = held.promise;
      const { operationId } = await instance.harness.submit("delegate it");
      await until(() => instance.probes.writes > writes, "the admission's unit");
      const steers = [];
      for (let n = 1; n < THREAD_INPUTS; n++) {
        steers.push((await instance.harness.submit(`follow up ${n}`)).accepted);
      }
      instance.probes.writeGate = null;
      held.release();
      await instance.harness.wait(operationId);
      return steers;
    });
    expect(out).toEqual(Array.from({ length: THREAD_INPUTS - 1 }, () => true));
  });

  it("does not check a resent subagent input again: it is pi's duplicate", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      await instance.harness.prompt("delegate it");
      const thread = await instance.harness.thread();
      const child = (await conversationsOf(instance, thread)).find((record) =>
        record.id !== ROOT)!;
      const [sent] = await submissionsOf(instance, thread, child.id);
      // The owner suspended and the turn gone: a fresh input would be refused both ways.
      await sqlIn(r.stub(), "UPDATE owner_admission SET state = 'suspended', " +
                            "reasons = '[\"billing\"]'");
      let asked = 0;
      const resent = await instance.agent.host.linked(thread, {
        conversation: child.id, requestId: sent!.requestId! }, async () => {
        asked++;
        throw new Error("the turn is gone");
      }, async () => "resent");
      return { resent, asked };
    });
    expect(out).toEqual({ resent: "resent", asked: 0 });
  });

  it("is refused to a scoped client without run_graph, and past its scope to another " +
     "agent, inheriting its stamp within it", async () => {
    const r = await rig();
    await r.write("/workspace/agents/helper/AGENTS.md", "You are the helper.");
    await r.admit(MUSE);
    const reads = await r.scoped(MUSE, ["read_files"]);
    await r.admit(DOT);
    const runs = await r.scoped(DOT, ["read_files", "run_graph"]);
    const out = await r.in(async (instance) => {
      const at = async (stamp: Stamped, ask: string) => {
        const thread = await instance.agent.thread("main", stamp, { kind: "run" });
        const answer = await instance.harness.prompt(ask, { session: thread.thread_id });
        return { answer, thread: thread.thread_id };
      };
      const muse = await at(r.stampOf(MUSE, reads), "delegate it");
      const helper = await at(r.stampOf(DOT, runs), "delegate to helper");
      const own = await at(r.stampOf(DOT, runs), "delegate it");
      const pi = await instance.harness.pi({ session: own.thread });
      const child = (await conversationsOf(instance, own.thread)).find((record) =>
        record.id !== ROOT)!;
      return { muse: muse.answer, helper: helper.answer, own: own.answer,
               stamps: [await pi.snapshot(instance.agent.authority, ROOT, BG),
                        await pi.snapshot(instance.agent.authority, child.id, BG)],
               helperConversations: (await conversationsOf(instance, helper.thread)).length };
    });
    expect(delegated(out.muse)).toEqual([{ text: expect.stringContaining(NOT_GRANTED),
                                           error: true }]);
    expect(delegated(out.helper)).toEqual([{ text: expect.stringContaining(NOT_GRANTED),
                                             error: true }]);
    expect(out.helperConversations).toBe(1);
    expect(delegated(out.own).map((result) => JSON.parse(result.text).status)).toEqual(["done"]);
    expect(out.stamps[1]).toEqual(out.stamps[0]);
    expect(out.stamps[0]).toEqual({ by: "client", stamp: r.stampOf(DOT, runs) });
  });

  it("ends with its thread's work on a revoke, a suspension and a policy write", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      // A client's subagent holding, then its grant revoked: the thread is dropped.
      const theirs = await instance.agent.thread("main", r.stampOf(MUSE, 1), { kind: "run" });
      await instance.harness.submit("delegate hold", { session: theirs.thread_id });
      await until(() => instance.probes.holds === 1, "the client's child holding");
      await instance.grantRevoke(r.bind(null), { client: MUSE.client });
      const wiped = instance.probes.wiped.get(theirs.store)?.submissions
        .filter((record) => record.requestId?.startsWith("subagent:"))
        .map((record) => [record.status, (record as { reason?: string }).reason]);
      // The owner's subagent holding, then the owner suspended: its work is ended.
      const { operationId } = await instance.harness.submit("delegate hold");
      await until(() => instance.probes.holds === 2, "the owner's child holding");
      await instance.admissionSet(r.platform, { reason: "billing", suspended: true });
      const suspended = await instance.harness.wait(operationId);
      const thread = await instance.harness.thread();
      const child = (await conversationsOf(instance, thread)).find((record) =>
        record.id !== ROOT)!;
      const ended = (await submissionsOf(instance, thread, child.id)).map((record) =>
        [record.status, (record as { reason?: string }).reason]);
      await instance.admissionSet(r.platform, { reason: "billing", suspended: false });
      // And a policy write ends a working one too.
      const later = await instance.harness.submit("delegate hold");
      await until(() => instance.probes.holds === 3, "the next child holding");
      const { generation, policy } = await instance.policyRead(r.bind(null));
      await instance.policyWrite(r.session(), { expected_generation: generation, policy });
      return { wiped, theirs: instance.parts.threads.row(theirs.thread_id), suspended, ended,
               written: await instance.harness.wait(later.operationId) };
    });
    expect(out.wiped).toEqual([["unanswered", "aborted"]]);
    expect(out.theirs).toBeNull();
    expect(out.suspended).toMatchObject({ status: "unanswered" });
    expect(out.ended).toEqual([["unanswered", "aborted"]]);
    expect(out.written).toMatchObject({ status: "unanswered", reason: "aborted" });
  }, 30_000);
});

describe("the owner's working threads", () => {
  it("refuse a client past its share while the owner still works", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const out = await r.in(async (instance) => {
      instance.timing.workingMax = 4;  // a client's share a quarter: one thread
      instance.probes.holdGate = new Promise(() => undefined);
      const stamp = { client: MUSE.client, generation: 1, authenticated_at: null,
                      universe: null, agent: "main" };
      const first = await instance.agent.thread("main", stamp, { kind: "run" });
      const second = await instance.agent.thread("main", stamp, { kind: "run" });
      await instance.harness.submit("hold please", { session: first.thread_id });
      await until(() => instance.probes.holds === 1, "the client's first hold");
      const refused = await failure(() => instance.harness.submit("hold please",
                                                                  { session: second.thread_id }));
      // Its working thread takes another input; the owner's threads still work.
      const again = await instance.harness.submit("more", { session: first.thread_id });
      const own = await instance.harness.submit("hold please");
      return { refused: refused.message, again: again.accepted, own: own.accepted };
    });
    expect(out).toEqual({ refused: THREADS_WORKING_SHARE, again: true, own: true });
  });

  it("keep a place for a compaction in an open Session when admissions are at their limit",
     async () => {
    // Production's reserve: all but the open Sessions' places are the admissions'.
    expect(HOST_TIMING.workingMax).toBe(WORKING_THREADS - OPEN_MAX);
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      // The trigger's bound three, one Session's reserve: admissions take two places.
      capTurnOwners(state.storage.sql, THREAD_INPUTS, THREAD_COMPACTIONS_ALL, 3);
      instance.timing.openMax = 1;
      instance.timing.workingMax = 3 - instance.timing.openMax;
      const threads: string[] = [];
      for (let n = 0; n < 4; n++) {
        threads.push((await instance.agent.thread("main", null, { kind: "run" })).thread_id);
      }
      const [one, two, three, four] = threads as [string, string, string, string];
      await instance.parts.turns.owned(one, [{ owner: "s:1", turn: "input:one" }]);
      await instance.parts.turns.owned(two, [{ owner: "s:1", turn: "input:two" }]);
      const refused = await failure(() => instance.harness.submit("hi", { session: three }));
      // An open Session with no owner starts a compaction: its owner lands in the reserve.
      await instance.harness.pi({ session: four });
      await instance.parts.turns.owned(four, [{ owner: "t:5", turn: `task:${four}:5` }]);
      return { refused: refused.message,
               notices: instance.parts.notices.list().map((notice) => notice.type),
               owners: state.storage.sql.exec<{ n: number }>(
                 "SELECT COUNT(DISTINCT thread) AS n FROM model_turn_owners").one().n };
    });
    expect(out.refused).toBe(THREADS_WORKING);
    expect(out.owners).toBe(3);
    expect(out.notices).not.toContain(TURNS_UNKEPT);
  });

  it("keep the owner's reserve from the clients together", async () => {
    const r = await rig();
    const origins = ["1", "2", "3", "4"].map((n) => ({ client: n.repeat(64),
                                                       family: n.repeat(64) }));
    for (const origin of origins) {
      await r.admit(origin);
    }
    const out = await r.in(async (instance) => {
      instance.timing.workingMax = 4;  // a client's share one, the clients' together three
      const threads: string[] = [];
      for (const origin of origins) {
        threads.push((await instance.agent.thread("main", {
          client: origin.client, generation: 1, authenticated_at: null, universe: null,
          agent: "main" }, { kind: "run" })).thread_id);
      }
      for (const thread of threads.slice(0, 3)) {
        await instance.parts.turns.owned(thread, [{ owner: "s:1", turn: `input:${thread}` }]);
      }
      const fourth = await failure(() => instance.harness.submit("hi",
                                                                 { session: threads[3]! }));
      const own = await instance.harness.submit("hi");
      return { fourth: fourth.message, own: own.accepted };
    });
    expect(out).toEqual({ fourth: THREADS_WORKING_CLIENTS, own: true });
  });

  it("count the admissions in flight, so two at once take one place once", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.timing.workingMax = 1;
      const one = (await instance.agent.thread("main", null, { kind: "run" })).thread_id;
      const two = (await instance.agent.thread("main", null, { kind: "run" })).thread_id;
      // The first admission waits between its check and its commit, its thread not yet
      // holding a turn owner.
      const turns = instance.parts.turns as { working: typeof instance.parts.turns.working };
      const original = turns.working;
      const reached = gate();
      const paused = gate();
      turns.working = async (...args) => {
        const answer = await original(...args);
        turns.working = original;
        reached.release();
        await paused.promise;
        return answer;
      };
      const first = instance.harness.submit("hi", { session: one });
      await reached.promise;
      const second = await failure(() => instance.harness.submit("hi", { session: two }));
      paused.release();
      return [(await first).accepted, second.message];
    });
    expect(out).toEqual([true, THREADS_WORKING]);
  });

  it("free a working thread's place once its work settles", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.timing.workingMax = 2;
      const held = gate();
      instance.probes.holdGate = held.promise;
      const threads: string[] = [];
      for (let n = 0; n < 3; n++) {
        threads.push((await instance.agent.thread("main", null, { kind: "run" })).thread_id);
      }
      const [one, two, three] = threads as [string, string, string];
      const first = await instance.harness.submit("hold please", { session: one });
      await instance.harness.submit("hold please", { session: two });
      await until(() => instance.probes.holds === 2, "two holds");
      const refused = await failure(() => instance.harness.submit("hi", { session: three }));
      instance.probes.holdGate = null;
      held.release();
      await instance.harness.wait(first.operationId, { session: one });
      await until(() => instance.parts.threads.settledRun(one), "the first run settled");
      const admitted = await instance.harness.submit("hi", { session: three });
      return { refused: refused.message, admitted: admitted.accepted };
    });
    expect(out).toEqual({ refused: THREADS_WORKING, admitted: true });
  });
});

describe("ta's rerun after a restart", () => {
  it("refuses an effect a restart cut off, never acting twice, where a delegation is found " +
     "again", async () => {
    const r = await rig();
    const before = await r.in(async (instance) => {
      // The proposal's unit waits at the gate when the object dies.
      await instance.harness.thread();
      const writes = instance.probes.writes;
      instance.probes.writeGate = new Promise(() => undefined);
      const { operationId } = await instance.harness.submit("propose it");
      await until(() => instance.probes.writes > writes, "the proposal's unit");
      return operationId;
    });
    expect(await r.in((_instance, state) => state.abort("evicted mid-proposal"))
      .then(() => "alive", (error: Error) => error.message)).toBe("evicted mid-proposal");
    await r.in((instance) => {
      instance.script = Array.from({ length: 200 }, () => byAsk);
    });
    expect(await runDurableObjectAlarm(r.stub())).toBe(true);
    const out = await r.in(async (instance) => instance.harness.wait(before));
    expect(out.status).toBe("done");
    const [result] = (JSON.parse(out.text!) as { name: string; text: string; error: boolean }[])
      .filter((shown) => shown.name === "policy:propose");
    // pi's own answer for a call cut off: its code and its words.
    expect(result).toMatchObject({ error: true, text: expect.stringContaining(
      "[error] Tool ta was interrupted and may have partially run") });
    const codes = await r.in(async (instance) => (await instance.harness.messages())
      .flatMap((entry) => ((entry.data as { diagnostics?: { code?: string }[] } | undefined)
        ?.diagnostics ?? []).map((diagnostic) => diagnostic.code)));
    expect(codes).toContain("interrupted");
    expect(await sqlIn(r.stub(), "SELECT COUNT(*) AS n FROM policy_proposals"))
      .toEqual([{ n: 0 }]);
  }, 30_000);
});

describe("a replay-safe tool ta reached, rerun after a restart", () => {
  it("passes the early gate again: refused while the owner is not admitted", async () => {
    const r = await rig();
    const before = await r.in(async (instance) => {
      instance.probes.holdGate = new Promise(() => undefined);
      const { operationId } = await instance.harness.submit("hold safely");
      await until(() => instance.probes.safeHolds === 1, "the safe hold");
      return operationId;
    });
    expect(await r.in((_instance, state) => state.abort("evicted mid-hold"))
      .then(() => "alive", (error: Error) => error.message)).toBe("evicted mid-hold");
    // Suspended while it was down: its live row says working, so its job opens it now.
    await sqlIn(r.stub(), "UPDATE owner_admission SET state = 'suspended', " +
                          "reasons = '[\"billing\"]'");
    await r.in((instance) => {
      instance.script = Array.from({ length: 200 }, () => byAsk);
    });
    expect(await runDurableObjectAlarm(r.stub())).toBe(true);
    const out = await r.in(async (instance) => {
      const settled = await instance.harness.wait(before);
      const shown = resultsOf((await instance.harness.messages())
        .flatMap((entry) => (entry.model ?? []) as Message[]));
      return { settled, shown, safeHolds: instance.probes.safeHolds };
    });
    // pi's recovery reran it with no hook; its own gate refused it before it ran.
    expect(out.safeHolds).toBe(0);
    expect(out.shown.find((result) => result.name === "safe_hold"))
      .toMatchObject({ error: true, text: expect.stringContaining("owner_suspended") });
    expect(out.settled.status).toBe("done");  // the faux model is held to no admission
  }, 30_000);
});

describe("a delegation's calls through the gateway", () => {
  it("are reserved on the caller's turn, under its caps", async () => {
    const r = await gated();
    await r.connect("sk-delegate");
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                            policy: { ...policy, models: { turn_requests: 1 } } });
    await r.in(async (instance) => {
      await instance.harness.prompt("cmd:delegate-echo");
    });
    const rows = await r.sql("SELECT turn, state, rule FROM model_reservations ORDER BY seq");
    // The caller's call, then the subagent's, refused by the caller's turn's cap.
    expect(rows.slice(0, 2).map((row) => [row["state"], row["rule"]]))
      .toEqual([["succeeded", null], ["refused", "turn_requests"]]);
    expect(new Set(rows.map((row) => row["turn"])).size).toBe(1);
  }, 30_000);

  it("are the caller's turn when its caps hold them", async () => {
    const r = await gated();
    await r.connect("sk-delegate-free");
    const answer = await r.in((instance) => instance.harness.prompt("cmd:delegate-echo"));
    expect(answer.status).toBe("done");
    const rows = await r.sql("SELECT turn, state FROM model_reservations ORDER BY seq");
    expect(rows.map((row) => row["state"])).toEqual(["succeeded", "succeeded", "succeeded"]);
    expect(new Set(rows.map((row) => row["turn"])).size).toBe(1);
  }, 30_000);
});
