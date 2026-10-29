/**
 * `ta` (tasks 5, sub-slice A3), in workerd: the model offered exactly read, write, edit,
 * bash and `ta` on the root, a thread, a fork and a subagent's conversation; the catalog
 * ranked, paged and with schemas, filtered to what the conversation's authority may call;
 * a platform read and a platform effect; refusals of an unknown name, a file tool, a
 * reserved name and a request with a field it does not take; a nested call through
 * `ta.authority` with `call.parent`; the re-check inside `execute` against a rewrite and a
 * revoke; `policy:propose` for the owner and for outside clients; and a nested model call
 * reserved and settled on its run's turn, through the gateway Worker.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import type { ConversationId, JsonObject } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import {
  AUTHORITY_CHANGED, AUTHORITY_RULE, NOT_GRANTED, NOT_OWNER, type Stamped,
} from "../src/authority";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import { Policy, policyDigest } from "../src/policy";
import { Scheduled } from "../src/scheduled";
import {
  INVALID_REQUEST, kindOf, MODEL_TOOLS, READ_WRITES, RESERVED_NAME, UNKNOWN_CAPABILITY,
} from "../src/ta";
import { Threads } from "../src/threads";
import { gated, until } from "./gateway-support";
import { DOT, MCP, MUSE, signedIn } from "./ledger-support";
import { failure } from "./support";
import { type FauxOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
/** A third outside client. */
const CLAW: Origin = Object.freeze({ client: "e".repeat(64), family: "f".repeat(64) });
const FIVE = ["read", "write", "edit", "bash", "ta"];
/** A call refused by a gate, as the model sees it. */
const blocked = (reason: string) => `<harness>\n[error] Tool call blocked: ${reason}\n</harness>`;
/** A call whose tool threw, as the model sees it. */
const threw = (message: string) => `<harness>\n[error] ${message}\n</harness>`;

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

function textOf(message: Message): string {
  const content = message.content;
  return typeof content === "string" ? content
    : content.map((part) => (part.type === "text" ? part.text : "")).join("");
}

/**
 * The model of these tests: `TA <json>` calls `ta` with those arguments, `CALL <name>`
 * calls that tool directly; after a tool round it answers with the last result's text and
 * error, and anything else (a nested model call's question among them) it notes.
 */
function step(messages: Message[]): AssistantMessage {
  const last = messages.at(-1);
  if (last?.role === "toolResult") {
    return says(JSON.stringify({ text: textOf(last), error: last.isError }));
  }
  const user = [...messages].reverse().find((message) => message.role === "user");
  const asked = user === undefined ? "" : textOf(user);
  if (asked.startsWith("TA ")) {
    return calls(fauxToolCall("ta", JSON.parse(asked.slice(3)) as JsonObject));
  }
  if (asked.startsWith("CALL ")) {
    return calls(fauxToolCall(asked.slice(5), {}));
  }
  return says(`noted: ${asked}`);
}

type Shown = { text: string; error: boolean };

/** An owner of its own on the faux-model class, signed in, and the binder's calls on it. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: Origin | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin, ...(origin === null ? {} : { source: MCP }), ...extra });
  const { session: cookie } = await stub.sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => bind(null, { session: cookie });
  return {
    owner, stub, bind, at,
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

/** A thread for `stamp` (null: the owner's), its session, the model answering by `step`. */
async function threadFor(instance: FauxOwnerDO, stamp: Stamped | null): Promise<string> {
  instance.script = Array.from({ length: 80 }, () => step);
  const thread = await instance.agent.thread("main", stamp);
  return String(thread.conversation_id);
}

/** `ta` called with `args` in `session` (the root without one): its result as shown. */
async function ta(instance: FauxOwnerDO, args: unknown, session?: string): Promise<Shown> {
  const answer = await instance.agent.harness.prompt(`TA ${JSON.stringify(args)}`,
                                                     session === undefined ? {} : { session });
  expect(answer.status).toBe("done");
  return JSON.parse(answer.text!) as Shown;
}

/** A successful `ta` call's value. */
async function value(instance: FauxOwnerDO, args: unknown, session?: string): Promise<any> {
  const shown = await ta(instance, args, session);
  expect(shown.error).toBe(false);
  return JSON.parse(shown.text);
}

const call = (name: string, args: Record<string, unknown> = {}) =>
  ({ op: "call", name, arguments: args });

describe("the model's tools", () => {
  it("are exactly read, write, edit, bash and ta on the root, a thread, a fork and a " +
     "subagent's conversation, a model-callable package tool and a deferred one never among " +
     "them", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 80 }, () => step);
      instance.probes.holdGate = Promise.resolve();
      const pi = await instance.agent.harness.pi();
      const direct = {
        sixth: await instance.agent.harness.prompt("CALL sixth"),
        deferred: await instance.agent.harness.prompt("CALL weather_lookup"),
      };
      const session = await threadFor(instance, null);
      await instance.agent.harness.prompt("hello thread", { session });
      const spawned = await value(instance, call("spawn", { work: "hello child" }), session);
      await (await pi.submission(instance.probes.submitted[0] as never, BG))!.wait(BG);
      const conversation = (await pi.conversation(Number(session) as ConversationId, BG))!;
      const at = (await conversation.context(BG)).entries.at(-1)!.id;
      const fork = await conversation.fork(at, { ownership: { kind: "ownerless" } }, BG);
      await instance.agent.harness.prompt("hello fork", { session: String(fork.id) });
      const offered: Record<string, string[]> = {};
      for (const [what, id] of [["root", 1], ["thread", Number(session)],
                                ["subagent", Number(spawned)], ["fork", fork.id]] as const) {
        const agent = await (await pi.conversation(id as ConversationId, BG))!.agent(BG);
        offered[what] = agent.tools.map((tool) => tool.name);
      }
      const root = await (await pi.conversation(1 as ConversationId, BG))!.agent(BG);
      const tool = root.tools.find((offered) => offered.name === "ta")!;
      return { direct, offered, requests: instance.offered,
               ta: { callers: tool.callers, replay: tool.replay,
                     callable: root.callable.some((offered) => offered.name === "ta") } };
    });
    expect(MODEL_TOOLS).toEqual(FIVE);
    // ta is the model's alone (no nested call reaches it) and never runs twice.
    expect(out.ta).toEqual({ callers: ["model"], replay: "unsafe", callable: false });
    expect(out.offered).toEqual({ root: FIVE, thread: FIVE, subagent: FIVE, fork: FIVE });
    // Every request any of them made offered the five, in that order, and nothing else.
    expect(out.requests.length).toBeGreaterThanOrEqual(8);
    expect(new Set(out.requests.map((tools) => tools.join()))).toEqual(new Set([FIVE.join()]));
    // The model's own call of a tool it is not offered never reaches the tool.
    for (const answer of [out.direct.sixth, out.direct.deferred]) {
      const shown = JSON.parse(answer.text!) as Shown;
      expect(shown.error).toBe(true);
      expect(shown.text).toMatch(/is not available/);
    }
  });
});

describe("the model's tools, whatever a parent drifted to", () => {
  it("are fixed again on a fork's and a subagent's conversation", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const session = await threadFor(instance, null);
      // The drift reaches the call itself: pi settles it as a tool no longer offered.
      const widened = await ta(instance, call("widen"), session);
      const child = instance.probes.spawned!;
      const pi = await instance.agent.harness.pi();
      const conversation = (await pi.conversation(Number(session) as ConversationId, BG))!;
      const at = (await conversation.context(BG)).entries.at(-1)!.id;
      const fork = await conversation.fork(at, { ownership: { kind: "ownerless" } }, BG);
      const tools = async (id: number) => (await (await pi.conversation(
        id as ConversationId, BG))!.agent(BG)).tools.map((tool) => tool.name);
      return { widened, drifted: await tools(Number(session)), child: await tools(child),
               fork: await tools(fork.id) };
    });
    // The test tool drifted its own conversation; nothing the object creates inherits it.
    expect(out.drifted).toEqual(["read", "sixth"]);
    expect(out.widened.error).toBe(true);
    expect([out.child, out.fork]).toEqual([FIVE, FIVE]);
  });
});

describe("ta's catalog", () => {
  it("lists the platform capabilities, then the tools callable by tools, deferred ones " +
     "marked, each with its schema; never the file tools, ta or a model-only tool", async () => {
    const r = await rig();
    const page = await r.in(async (instance) => value(instance, { op: "catalog", limit: 100 },
                                                      await threadFor(instance, null)));
    const names = page.capabilities.map((entry: { name: string }) => entry.name);
    expect(names).toEqual(["policy:show", "policy:propose", "wake:list", "files:usage", "model",
                           "hold", "fork", "nest", "spawn", "background", "widen",
                           "weather_lookup", "pass"]);
    expect(page.total).toBe(names.length);
    expect(page.next_offset).toBeUndefined();
    const of = (name: string) => page.capabilities.find((entry: { name: string }) =>
      entry.name === name);
    expect([of("policy:show").effect, of("policy:propose").effect, of("wake:list").effect,
            of("files:usage").effect, of("model").effect, of("hold").effect])
      .toEqual([false, true, false, false, true, true]);
    expect(of("weather_lookup").deferred).toBe(true);
    expect(of("hold").deferred).toBeUndefined();
    for (const entry of page.capabilities) {
      expect(entry.parameters.type).toBe("object");
      expect(typeof entry.description).toBe("string");
    }
    expect(of("policy:propose").parameters.required).toEqual(["change"]);
    expect(of("weather_lookup").parameters.properties.city.description).toBe("The city's name");
  });

  it("ranks by a query as tool search does, and pages from an offset", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const session = await threadFor(instance, null);
      return {
        weather: await value(instance, { op: "catalog", query: "weather forecast for a city" },
                             session),
        policy: await value(instance, { op: "catalog", query: "policy" }, session),
        nothing: await value(instance, { op: "catalog", query: "zebra" }, session),
        first: await value(instance, { op: "catalog", limit: 5 }, session),
        second: await value(instance, { op: "catalog", limit: 5, offset: 5 }, session),
        last: await value(instance, { op: "catalog", limit: 5, offset: 10 }, session),
      };
    });
    const names = (page: { capabilities: { name: string }[] }) =>
      page.capabilities.map((entry) => entry.name);
    expect(names(out.weather)[0]).toBe("weather_lookup");
    expect(names(out.policy).slice(0, 2).sort()).toEqual(["policy:propose", "policy:show"]);
    expect(out.nothing).toEqual({ capabilities: [], total: 0 });
    expect([names(out.first), out.first.total, out.first.next_offset]).toEqual([
      ["policy:show", "policy:propose", "wake:list", "files:usage", "model"], 13, 5]);
    expect([names(out.second), out.second.next_offset])
      .toEqual([["hold", "fork", "nest", "spawn", "background"], 10]);
    expect([names(out.last), out.last.next_offset])
      .toEqual([["widen", "weather_lookup", "pass"], undefined]);
  });

  it("lists to a scoped client only what its grant reaches, and to a full one all but the " +
     "owner's policy", async () => {
    const r = await rig();
    await r.admit(MUSE);
    await r.admit(DOT);
    const generation = await r.scoped(MUSE, ["read_files"]);
    await r.admit(CLAW);
    const proposing = await r.scoped(CLAW, ["propose_policy"]);
    const out = await r.in(async (instance) => ({
      scoped: await value(instance, { op: "catalog", limit: 100 },
                          await threadFor(instance, r.stampOf(MUSE, generation))),
      proposing: await value(instance, { op: "catalog", limit: 100 },
                             await threadFor(instance, r.stampOf(CLAW, proposing))),
      usage: await ta(instance, call("files:usage"),
                      await threadFor(instance, r.stampOf(CLAW, proposing))),
      full: await value(instance, { op: "catalog", limit: 100 },
                        await threadFor(instance, r.stampOf(DOT, 1))),
      fullShows: await ta(instance, call("policy:show"),
                          await threadFor(instance, r.stampOf(DOT, 1))),
    }));
    const names = (page: { capabilities: { name: string }[] }) =>
      page.capabilities.map((entry) => entry.name);
    expect(names(out.scoped)).toEqual(["wake:list", "files:usage", "model"]);
    expect(names(out.proposing)).toEqual(["policy:propose", "wake:list", "model"]);
    expect(out.usage).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect(out.fullShows).toEqual({ text: blocked(NOT_OWNER), error: true });
    expect(names(out.full)).toEqual(["policy:propose", "wake:list", "files:usage", "model",
                                     "hold", "fork", "nest", "spawn", "background",
                                     "widen", "weather_lookup", "pass"]);
  });
});

describe("ta's call", () => {
  it("runs a platform read: the owner's policy, its file usage, its own scheduled work",
     async () => {
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      instance.script = Array.from({ length: 80 }, () => step);
      const thread = await instance.agent.thread("main", null);
      const session = String(thread.conversation_id);
      const gate = new WriteGate(state.storage);
      const threads = new Threads(gate.storage);
      const work = new Scheduled(gate.storage, new Grants(gate.storage, new Policy(gate.storage)),
                                 threads);
      const { work_id: workId } = await gate.write(() => work.add(
        "turn", clock() + 3600, { thread: thread.thread_id, input: "later, please" }, null,
        clock()));
      return {
        workId,
        policy: await value(instance, call("policy:show"), session),
        usage: await value(instance, call("files:usage"), session),
        wakes: await value(instance, call("wake:list"), session),
      };
    });
    const read = await r.stub.policyRead(r.bind(null));
    expect(out.policy).toEqual(read);
    expect(out.usage).toEqual(await r.stub.fileUsage(r.bind(null)));
    expect(out.wakes.work).toEqual([expect.objectContaining({
      work_id: out.workId, kind: "turn", state: "pending",
      payload: expect.objectContaining({ input: "later, please" }) })]);
  });

  it("runs a platform effect: the owner's proposal held under its card, never applied",
     async () => {
    const r = await rig();
    const change = { note: "more wakes" };
    const filed = await r.in(async (instance) => value(
      instance, call("policy:propose", { change, request_id: "card-ta" }),
      await threadFor(instance, null)));
    expect(filed).toEqual({ request_digest: policyDigest(change), generation: 1 });
    expect(await r.stub.policyProposal(r.bind(null), { request_id: "card-ta" }))
      .toEqual({ change, digest: filed.request_digest });
    expect((await r.stub.policyRead(r.bind(null))).generation).toBe(1);
  });

  it("refuses policy:propose to an outside client its grant does not reach, holds it for " +
     "one with propose_policy, and policy:show to every outside client", async () => {
    const r = await rig();
    await r.admit(MUSE);
    await r.admit(DOT);
    const narrow = await r.scoped(MUSE, ["read_files"]);
    const proposing = await r.scoped(DOT, ["propose_policy"]);
    const change = { note: "from outside" };
    const out = await r.in(async (instance) => {
      const muse = await threadFor(instance, r.stampOf(MUSE, narrow));
      const dot = await threadFor(instance, r.stampOf(DOT, proposing));
      return {
        museProposes: await ta(instance, call("policy:propose", { change }), muse),
        museShows: await ta(instance, call("policy:show"), muse),
        dotProposes: await ta(instance, call("policy:propose",
                                             { change, request_id: "card-dot" }), dot),
        dotShows: await ta(instance, call("policy:show"), dot),
      };
    });
    expect(out.museProposes).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect(out.museShows).toEqual({ text: blocked(NOT_OWNER), error: true });
    expect(out.dotShows).toEqual({ text: blocked(NOT_OWNER), error: true });
    expect(JSON.parse(out.dotProposes.text)).toEqual({ request_digest: policyDigest(change),
                                                       generation: 1 });
    expect((await r.stub.policyProposal(r.bind(null), { request_id: "card-dot" })).change)
      .toEqual(change);
    const refused = (await r.stub.policyReceipts(r.bind(null))).receipts
      .filter((receipt) => receipt.rule === AUTHORITY_RULE)
      .map((receipt) => receipt.request_digest);
    expect(refused.sort()).toEqual([
      [MUSE, NOT_GRANTED], [MUSE, NOT_OWNER], [DOT, NOT_OWNER],
    ].map(([client, reason]) => policyDigest({ action: "tool_call", request: {
      client: (client as Origin).client, tool: "ta", reason } })).sort());
  });

  it("reaches a tool callable by tools as a nested call, which ta.authority gates with its " +
     "parent; a scoped client is refused it, and an effect capability it lacks", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["read_files"]);
    const out = await r.in(async (instance) => {
      instance.probes.holdGate = Promise.resolve();
      const own = await threadFor(instance, null);
      const held = await ta(instance, call("hold"), own);
      const weather = await ta(instance, call("weather_lookup", { city: "Paris" }), own);
      const invalid = await ta(instance, call("weather_lookup", { town: "Paris" }), own);
      const gatedOwn = [...instance.probes.gated];
      const muse = await threadFor(instance, r.stampOf(MUSE, generation));
      return { held, weather, invalid, gatedOwn,
               museHold: await ta(instance, call("hold"), muse),
               museModel: await ta(instance, call("policy:propose", { change: { a: 1 } }), muse) };
    });
    expect(out.held).toEqual({ text: "held", error: false });
    expect(out.weather).toEqual({ text: "sunny in Paris", error: false });
    expect(out.invalid.error).toBe(true);
    // An invalid nested call is refused by pi's validation, before any hook sees it.
    expect(out.invalid.text).toMatch(/city/);
    expect(out.gatedOwn).toEqual([{ name: "ta", nested: false }, { name: "hold", nested: true },
                                  { name: "ta", nested: false },
                                  { name: "weather_lookup", nested: true },
                                  { name: "ta", nested: false }]);
    expect(out.museHold).toEqual({ text: blocked(NOT_GRANTED), error: true });
    expect(out.museModel).toEqual({ text: blocked(NOT_GRANTED), error: true });
  });

  it("refuses an unknown name, a file tool, a model-only tool, a reserved name and a " +
     "request with a field it does not take, loudly", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const session = await threadFor(instance, null);
      return {
        unknown: await ta(instance, call("nothing:here"), session),
        bash: await ta(instance, call("bash", { command: "ls" }), session),
        read: await ta(instance, call("read", { path: "notes.md" }), session),
        sixth: await ta(instance, call("sixth"), session),
        itself: await ta(instance, call("ta"), session),
        reserved: await ta(instance, call("ta:catalog"), session),
        crossed: await ta(instance, { op: "catalog", name: "policy:show" }, session),
        missing: await ta(instance, { op: "call", name: "policy:show" }, session),
        grant: await ta(instance, { op: "catalog", grant: "*" }, session),
        badArgs: await ta(instance, call("policy:show", { extra: 1 }), session),
      };
    });
    for (const name of ["nothing:here", "bash", "read", "sixth", "ta"]) {
      const key = name === "nothing:here" ? "unknown" : name === "ta" ? "itself" : name;
      expect([key, out[key as keyof typeof out]])
        .toEqual([key, { text: threw(`${UNKNOWN_CAPABILITY}: ${name}`), error: true }]);
    }
    expect(out.reserved).toEqual({ text: threw(RESERVED_NAME), error: true });
    expect(out.crossed).toEqual({ text: threw(INVALID_REQUEST), error: true });
    expect(out.missing).toEqual({ text: threw(INVALID_REQUEST), error: true });
    expect(out.grant.error).toBe(true);  // the schema itself takes no other field
    expect(out.badArgs.error).toBe(true);
    expect(out.badArgs.text).toMatch(/extra|additional/i);
  });
});

describe("ta's own check in execute", () => {
  it("re-derives the need from the arguments a later hook rewrote, and refuses them",
     async () => {
    const r = await rig();
    await r.admit(MUSE);
    const generation = await r.scoped(MUSE, ["read_files"]);
    const out = await r.in(async (instance, state) => {
      const session = await threadFor(instance, r.stampOf(MUSE, generation));
      const rewritten = async (to: unknown) => {
        instance.probes.rewrite = (seen) => (seen.name === "ta" ? to : undefined);
        try {
          return await ta(instance, call("files:usage"), session);
        } finally {
          instance.probes.rewrite = null;
        }
      };
      return {
        // A read the client may not make, and an effect it may not make.
        show: await rewritten(call("policy:show")),
        propose: await rewritten(call("policy:propose", { change: { rewritten: true } })),
        proposals: state.storage.sql.exec("SELECT COUNT(*) AS n FROM policy_proposals").one().n,
      };
    });
    expect(out.show).toEqual({ text: threw(NOT_OWNER), error: true });
    expect(out.propose).toEqual({ text: threw(NOT_GRANTED), error: true });
    expect(out.proposals).toBe(0);
  });

  it("re-checks an effect's authority in its own unit, which a revoke landing first refuses",
     async () => {
    const r = await rig();
    await r.admit(DOT);
    const generation = await r.scoped(DOT, ["propose_policy"]);
    const out = await r.in(async (instance, state) => {
      const session = await threadFor(instance, r.stampOf(DOT, generation));
      let release!: () => void;
      instance.probes.writeGate = new Promise<void>((resolve) => { release = resolve; });
      const before = instance.probes.writes;
      const asked = instance.agent.harness.prompt(`TA ${JSON.stringify(call("policy:propose", {
        change: { late: true }, request_id: "card-late" }))}`, { session });
      await until(() => instance.probes.writes > before, "the proposal's unit to be asked for");
      // ta's own check passed; a revoke lands before its unit is queued.
      new Grants(state.storage, new Policy(state.storage)).revoke(DOT.client, clock());
      instance.probes.writeGate = null;
      release();
      return JSON.parse((await asked).text!) as Shown;
    });
    expect(out).toEqual({ text: threw(AUTHORITY_CHANGED), error: true });
    expect((await failure(() => r.stub.policyProposal(r.bind(null),
                                                      { request_id: "card-late" }))).message)
      .toBe("no pending proposal for this card");
  });

  it("re-checks the stamp, which a revoke after the early gate no longer stands on",
     async () => {
    const r = await rig();
    await r.admit(MUSE);
    const out = await r.in(async (instance, state) => {
      const session = await threadFor(instance, r.stampOf(MUSE, 1));
      let release!: () => void;
      instance.probes.toolGate = new Promise<void>((resolve) => { release = resolve; });
      const asked = instance.agent.harness.prompt(`TA ${JSON.stringify(call("files:usage"))}`,
                                                  { session });
      await until(() => instance.probes.gated.some((seen) => seen.name === "ta"),
                  "the early gate to approve ta");
      // A grant change made around the doors: only ta's own check can see it now.
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
      release();
      instance.probes.toolGate = null;
      return JSON.parse((await asked).text!) as Shown;
    });
    expect(out).toEqual({ text: threw(AUTHORITY_CHANGED), error: true });
  });

  it("classifies a capability an effect unless it is named a read", () => {
    expect(["ta:catalog", "policy:show", "wake:list", "files:usage"].map(kindOf))
      .toEqual(["read", "read", "read", "read"]);
    expect(["policy:propose", "model", "agent:run", "anything:new"].map(kindOf))
      .toEqual(["effect", "effect", "effect", "effect"]);
    expect(READ_WRITES).toBe("a read capability writes nothing");
  });
});

describe("ta's model capability", () => {
  it("asks the object's model source on the conversation's session and its run's turn",
     async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const session = await threadFor(instance, null);
      const asked = await value(instance, call("model", {
        system: "be brief", messages: [{ role: "user", text: "NESTED question" }] }), session);
      const missing = await ta(instance, call("model", {
        model: "no-such-model", messages: [{ role: "user", text: "hi" }] }), session);
      return { asked, missing, turns: [...instance.turns] };
    });
    expect(out.asked).toMatchObject({ text: "noted: NESTED question", stop_reason: "stop" });
    expect(out.missing).toEqual({ text: threw("no connected model no-such-model"), error: true });
    // The run's three requests, the nested one in the middle, are one turn; the refused
    // call's run made two, and no nested one.
    expect(out.turns).toHaveLength(5);
    expect(new Set(out.turns.slice(0, 3)).size).toBe(1);
    expect(out.turns[3]).not.toBe(out.turns[0]);
  });

  it("reserves and settles through the gateway on its run's turn, counted against its caps",
     async () => {
    const r = await gated();
    await r.connect("sk-ta-model");
    const out = await r.in(async (instance) => {
      const first = await instance.agent.harness.prompt("cmd:ta-model");
      const later = await instance.agent.harness.prompt("cmd:echo");
      return { first: first.status, later: later.status };
    });
    expect(out).toEqual({ first: "done", later: "done" });
    const rows = await r.sql("SELECT turn, state FROM model_reservations ORDER BY seq");
    expect(rows.map((row) => row["state"])).toEqual(["succeeded", "succeeded", "succeeded",
                                                     "succeeded"]);
    const [run, nested, answered, later] = rows.map((row) => row["turn"]);
    expect([nested, answered]).toEqual([run, run]);
    expect(later).not.toBe(run);
    expect(await r.sql("SELECT turn, requests FROM model_turns ORDER BY requests"))
      .toEqual([{ turn: later, requests: 1 }, { turn: run, requests: 3 }]);
    // The nested call went through the gateway as its own request, asking what ta asked.
    const bodies = instanceCalls(await r.in(async (instance) => instance.calls));
    expect(bodies).toHaveLength(4);
    expect(bodies[1]).toContain("cmd:echo");
    expect(bodies[1]).not.toContain("cmd:ta-model");
  }, 30_000);

  it("is refused by its run's caps, as the run's own calls are", async () => {
    const r = await gated();
    await r.connect("sk-ta-capped");
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                            policy: { ...policy, models: { turn_requests: 1 } } });
    const shown = await r.in(async (instance) => {
      await instance.agent.harness.prompt("cmd:ta-model");
      const messages = (await instance.agent.harness.messages())
        .flatMap((entry) => (entry.model ?? []) as Message[]);
      const result = messages.find((message) => message.role === "toolResult")!;
      return { text: textOf(result), error: result.role === "toolResult" && result.isError };
    });
    expect(shown).toEqual({ text: threw("the model call failed: model_budget: turn_requests"),
                            error: true });
    const rows = await r.sql("SELECT state, rule FROM model_reservations ORDER BY seq");
    expect(rows.slice(0, 2)).toEqual([{ state: "succeeded", rule: null },
                                      { state: "refused", rule: "turn_requests" }]);
  }, 30_000);
});

/** The request bodies the gated object sent the gateway, in order. */
function instanceCalls(calls: { method: string; text: string }[]): string[] {
  return calls.filter((seen) => seen.method === "fetch")
    .map((seen) => (JSON.parse(seen.text) as { body: string }).body);
}
