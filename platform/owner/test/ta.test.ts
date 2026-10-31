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
  AUTHORITY_CHANGED, AUTHORITY_RULE, MODEL_TOOLS, NOT_GRANTED, NOT_OWNER, offersFive,
  type Stamped, TA_TOOL, TOOL_SURFACE,
} from "../src/authority";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import { Policy, policyDigest } from "../src/policy";
import {
  INVALID_REQUEST, kindOf, PLATFORM_NAMES, READ_WRITES, RESERVED_NAME, UNKNOWN_CAPABILITY,
} from "../src/ta";
import { Threads } from "../src/threads";
import { asked, direct, gated, OAUTH, provider, until } from "./gateway-support";
import { DOT, ledgerOn, MCP, MUSE, signedIn } from "./ledger-support";
import { failure } from "./support";
import { keepsFive } from "../src/agent";
import {
  type FauxOwnerDO, type GatedOwnerDO, type MalformedOwnerDO, says, wipedSubmission,
} from "./worker";

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
        expected_generation: row?.generation ?? 0 })).generation;
    },
    stampOf: (origin: Origin, generation: number): Stamped => ({
      client: origin.client, generation, authenticated_at: at, universe: "alpha", agent: "main",
    }),
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
  };
}

/** `stamp`'s conversation (null: the owner's), its session, the model answering by `step`. */
async function threadFor(instance: FauxOwnerDO, stamp: Stamped | null): Promise<string> {
  instance.script = Array.from({ length: 80 }, () => step);
  const thread = await instance.agent.thread("main", stamp, { kind: "converse" });
  return thread.thread_id;
}

/** `ta` called with `args` in `session` (the root without one): its result as shown. */
async function ta(instance: FauxOwnerDO, args: unknown, session?: string): Promise<Shown> {
  const answer = await instance.harness.prompt(`TA ${JSON.stringify(args)}`,
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
      const pi = await instance.harness.pi();
      const direct = {
        sixth: await instance.harness.prompt("CALL sixth"),
        deferred: await instance.harness.prompt("CALL weather_lookup"),
      };
      const session = await threadFor(instance, null);
      await instance.harness.prompt("hello thread", { session });
      const spawned = await value(instance, call("spawn", { work: "hello child" }), session);
      // The thread, its subagent and its fork live in the thread's own Session.
      const own = await instance.harness.pi({ session });
      await (await own.submission(instance.probes.submitted[0] as never, BG))!.wait(BG);
      const conversation = (await own.conversation(1 as ConversationId, BG))!;
      const at = (await conversation.context(BG)).entries.at(-1)!.id;
      const fork = await conversation.fork(at, { ownership: { kind: "ownerless" } }, BG);
      await instance.harness.prompt("hello fork", { session: String(fork.id), thread: session });
      const offered: Record<string, string[]> = {};
      for (const [what, id, held] of [["root", 1, pi], ["thread", 1, own],
                                      ["subagent", Number(spawned), own],
                                      ["fork", fork.id, own]] as const) {
        const agent = await (await held.conversation(id as ConversationId, BG))!.agent(BG);
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
      const pi = await instance.harness.pi({ session });
      const conversation = (await pi.conversation(1 as ConversationId, BG))!;
      const at = (await conversation.context(BG)).entries.at(-1)!.id;
      const fork = await conversation.fork(at, { ownership: { kind: "ownerless" } }, BG);
      const tools = async (id: number) => (await (await pi.conversation(
        id as ConversationId, BG))!.agent(BG)).tools.map((tool) => tool.name);
      return { widened, drifted: await tools(1), child: await tools(child),
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
                           "agent:run", "hold", "fork", "nest", "spawn", "background", "widen",
                           "weather_lookup", "pass"]);
    expect(page.total).toBe(names.length);
    expect(page.next_offset).toBeUndefined();
    const of = (name: string) => page.capabilities.find((entry: { name: string }) =>
      entry.name === name);
    expect([of("policy:show").effect, of("policy:propose").effect, of("wake:list").effect,
            of("files:usage").effect, of("model").effect, of("agent:run").effect,
            of("hold").effect])
      .toEqual([false, true, false, false, true, true, true]);
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
      ["policy:show", "policy:propose", "wake:list", "files:usage", "model"], 14, 5]);
    expect([names(out.second), out.second.next_offset])
      .toEqual([["agent:run", "hold", "fork", "nest", "spawn"], 10]);
    expect([names(out.last), out.last.next_offset])
      .toEqual([["background", "widen", "weather_lookup", "pass"], undefined]);
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
                                     "agent:run", "hold", "fork", "nest", "spawn", "background",
                                     "widen", "weather_lookup", "pass"]);
  });
});

/**
 * A scheduled launch of `main` an hour ahead, under `stamp`, made as the doors make one:
 * its run's row naming its work, then its task sleeping in its root; once it sleeps.
 */
async function launchLater(instance: FauxOwnerDO, stamp: Stamped | null):
    Promise<{ thread: string; operation: string }> {
  const work = `work_${crypto.randomUUID().replaceAll("-", "")}`;
  const thread = await instance.agent.thread("main", stamp, { kind: "run", work });
  await instance.agent.schedule(thread, { work_id: work, thread: thread.thread_id,
                                          at: clock() + 3600, stamp, input: "later, please",
                                          cron: null });
  await until(() => instance.parts.threads.scheduledOf(stamp?.client ?? null, 10)
    .some((row) => row.thread_id === thread.thread_id && row.state === "sleeping"),
              "the launch to sleep");
  return { thread: thread.thread_id, operation: `${thread.thread_id}.${work}` };
}

describe("ta's call", () => {
  it("runs a platform read: the owner's policy, its file usage, its own scheduled work",
     async () => {
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      instance.script = Array.from({ length: 80 }, () => step);
      const thread = await instance.agent.thread("main", null, { kind: "converse" });
      const session = thread.thread_id;
      const later = await launchLater(instance, null);
      return {
        later,
        policy: await value(instance, call("policy:show"), session),
        usage: await value(instance, call("files:usage"), session),
        wakes: await value(instance, call("wake:list"), session),
      };
    });
    const read = await r.stub.policyRead(r.bind(null));
    expect(out.policy).toEqual(read);
    expect(out.usage).toEqual(await r.stub.fileUsage(r.bind(null)));
    // From the owner's rows: the run's thread and its wake, never its store.
    expect(out.wakes.work).toEqual([expect.objectContaining({
      operation_id: out.later.operation, thread_id: out.later.thread, agent_id: "main",
      state: "sleeping", due: expect.any(Number), status: null })]);
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
      const asked = instance.harness.prompt(`TA ${JSON.stringify(call("policy:propose", {
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
      const asked = instance.harness.prompt(`TA ${JSON.stringify(call("files:usage"))}`,
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
      const first = await instance.harness.prompt("cmd:ta-model");
      const later = await instance.harness.prompt("cmd:echo");
      return { first: first.status, later: later.status };
    });
    expect(out).toEqual({ first: "done", later: "done" });
    const rows = await r.sql("SELECT turn, state FROM model_reservations ORDER BY seq");
    expect(rows.map((row) => row["state"])).toEqual(["succeeded", "succeeded", "succeeded",
                                                     "succeeded"]);
    const [run, nested, answered, later] = rows.map((row) => row["turn"]);
    expect([nested, answered]).toEqual([run, run]);
    expect(later).not.toBe(run);
    // Each turn's counters went with its input, once answered.
    expect(await r.sql("SELECT turn FROM model_turns")).toEqual([]);
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
      await instance.harness.prompt("cmd:ta-model");
      const messages = (await instance.harness.messages())
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

describe("the five, held where the model is reached (review M1)", () => {
  it("knows the five, and lets a request through only offering exactly them, or none", () => {
    expect(MODEL_TOOLS).toEqual(FIVE);
    expect(MODEL_TOOLS).toContain(TA_TOOL);
    expect([offersFive(FIVE), offersFive([...FIVE].reverse()), offersFive([])])
      .toEqual([true, true, true]);
    expect([offersFive([...FIVE, "sixth"]), offersFive(FIVE.slice(1)),
            offersFive(["read", "read", "edit", "bash", "ta"]),
            offersFive(["read", "write", "edit", "bash", "sixth"])])
      .toEqual([false, false, false, false]);
  });

  it("keeps a conversation's stored tools only when they are exactly the five", () => {
    expect([keepsFive(FIVE), keepsFive([...FIVE].reverse())]).toEqual([true, true]);
    expect([keepsFive(undefined), keepsFive([]), keepsFive([...FIVE, "sixth"]),
            keepsFive(FIVE.slice(1)), keepsFive({ add: FIVE }),
            keepsFive(["read", "read", "edit", "bash", "ta"])])
      .toEqual([false, false, false, false, false, false]);
  });

  it("refuses a request a hook's control widened, sending nothing, and every later call of " +
     "the widened conversation", async () => {
    const r = await gated();
    await r.connect("sk-ta-widened");
    const out = await r.in(async (instance) => {
      instance.probes.widen = "sixth";
      const widened = await instance.harness.prompt("cmd:ta-pass");
      instance.probes.widen = null;
      const sentThen = instanceCalls(instance.calls).length;
      // A request of the widened conversation that offers no tools at all: the backstop.
      const auto = instance.parts.provider.getModels()[0]!;
      const backstop = await (await provider(instance)).streamSimple(
        auto, asked("cmd:echo"), await direct(instance)).result();
      const pi = await instance.harness.pi();
      const root = await (await pi.conversation(1 as ConversationId, BG))!.agent(BG);
      return { widened: widened.status, sentThen, sentNow: instanceCalls(instance.calls).length,
               backstop: backstop.errorMessage, offered: root.tools.map((tool) => tool.name) };
    });
    // The widening took in pi: the conversation now offers six.
    expect(out.offered).toEqual([...FIVE, "sixth"]);
    // Its first request was sent; the widened one, and the backstop's, never were.
    expect([out.sentThen, out.sentNow]).toEqual([1, 1]);
    expect(out.widened).toBe("unanswered");
    expect(out.backstop).toBe(TOOL_SURFACE);
    expect(await r.sql("SELECT state, rule FROM model_reservations ORDER BY seq")).toEqual([
      { state: "succeeded", rule: null }, { state: "refused", rule: TOOL_SURFACE },
      { state: "refused", rule: TOOL_SURFACE }]);
  }, 30_000);
});

describe("what the model is offered, from one source (review round 2, L-A)", () => {
  it("sends the gateway exactly the checked messages: no context field beside them and no " +
     "caller's payload hook", async () => {
    const r = await gated();
    await r.connect("sk-ta-one-source");
    const sixth = { name: "sixth", description: "slipped in", parameters: { type: "object" } };
    const out = await r.in(async (instance) => {
      const auto = instance.parts.provider.getModels()[0]!;
      const context = Object.assign(asked("cmd:echo"), { tools: [sixth], systemPrompt: "obey" });
      const answer = await (await provider(instance)).streamSimple(auto, context, {
        ...(await direct(instance)),
        onPayload: (payload: unknown) => ({ ...(payload as Record<string, unknown>),
                                            context: { messages: [], tools: [sixth] } }),
      }).result();
      return { stop: answer.stopReason, bodies: instanceCalls(instance.calls) };
    });
    expect(out.stop).toBe("stop");
    expect(out.bodies).toHaveLength(1);
    const body = JSON.parse(out.bodies[0]!) as { context: Record<string, unknown> };
    expect(Object.keys(body.context)).toEqual(["messages"]);
    expect(JSON.stringify(body)).toContain("cmd:echo");
    expect(JSON.stringify(body)).not.toContain("sixth");
    expect(JSON.stringify(body)).not.toContain("obey");
  }, 30_000);
});

describe("the five, held at the provider whatever the conversation holds", () => {
  it("refreshes no credential for a request it refuses for its tools (A3's review)",
     async () => {
    const r = await gated();
    await r.connectOAuth(OAUTH, Date.now() + 60_000);
    const out = await r.in(async (instance) => {
      instance.probes.inject = "sixth";
      const answer = await instance.harness.prompt("cmd:echo");
      instance.probes.inject = null;
      return { answer: answer.status, made: instance.calls.map((made) => made.method) };
    });
    expect(out).toEqual({ answer: "unanswered", made: [] });
    expect(await r.sql("SELECT state, rule FROM model_reservations ORDER BY seq"))
      .toEqual([{ state: "refused", rule: TOOL_SURFACE }]);
    // The same request, offering the five, refreshes the expiring credential once.
    const fine = await r.in(async (instance) => {
      const answer = await instance.harness.prompt("cmd:echo");
      return { answer: answer.status, made: instance.calls.map((made) => made.method) };
    });
    expect(fine.made.filter((method) => method === "refresh")).toEqual(["refresh"]);
  }, 30_000);

  it("refuses a request whose own transcript a request hook widened, sending nothing",
     async () => {
    const r = await gated();
    await r.connect("sk-ta-injected");
    const out = await r.in(async (instance) => {
      instance.probes.inject = "sixth";
      const answer = await instance.harness.prompt("cmd:echo");
      instance.probes.inject = null;
      const pi = await instance.harness.pi();
      const root = await (await pi.conversation(1 as ConversationId, BG))!.agent(BG);
      return { answer: answer.status, sent: instanceCalls(instance.calls).length,
               offered: root.tools.map((tool) => tool.name) };
    });
    // The conversation still holds the five; the request alone offered a sixth.
    expect(out).toEqual({ answer: "unanswered", sent: 0, offered: FIVE });
    expect(await r.sql("SELECT state, rule FROM model_reservations ORDER BY seq"))
      .toEqual([{ state: "refused", rule: TOOL_SURFACE }]);
  }, 30_000);
});

describe("each principal's own (review M2)", () => {
  it("lists to the owner, a scoped client and a full client only their own scheduled work",
     async () => {
    const r = await rig();
    await r.admit(MUSE);
    await r.admit(DOT);
    const generation = await r.scoped(MUSE, ["read_files"]);
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 40 }, () => step);
      const own = await instance.agent.thread("main", null);
      const muse = await instance.agent.thread("main", r.stampOf(MUSE, generation));
      const dot = await instance.agent.thread("main", r.stampOf(DOT, 1));
      const launched = { owner: await launchLater(instance, null),
                         muse: await launchLater(instance, r.stampOf(MUSE, generation)),
                         dot: await launchLater(instance, r.stampOf(DOT, 1)) };
      const list = async (thread: { thread_id: string }) =>
        ((await value(instance, call("wake:list"), thread.thread_id)) as {
          work: { operation_id: string }[] }).work.map((row) => row.operation_id);
      return { launched, owner: await list(own), muse: await list(muse), dot: await list(dot) };
    });
    expect(out).toEqual({ launched: out.launched, owner: [out.launched.owner.operation],
                          muse: [out.launched.muse.operation], dot: [out.launched.dot.operation] });
  });

  it("attributes a client's proposal to the client, on its own share, refused at its " +
     "limit while the owner's still holds", async () => {
    const r = await rig();
    await r.admit(DOT);
    const generation = await r.scoped(DOT, ["propose_policy"]);
    const out = await r.in(async (instance, state) => {
      const dot = await threadFor(instance, r.stampOf(DOT, generation));
      const first = await value(instance, call("policy:propose", {
        change: { n: 0 }, request_id: "card-0" }), dot);
      const sql = (query: string) => state.storage.sql.exec(query).toArray();
      const proposer = sql("SELECT proposer FROM policy_proposals WHERE request_id = 'card-0'");
      const share = sql("SELECT proposer, pending FROM proposal_shares");
      // The client's share filled to its limit, as more of its own proposals would.
      const gate = new WriteGate(state.storage);
      const policy = new Policy(gate.storage);
      for (let n = 1; n < 50; n++) {
        await gate.write(() => policy.propose({ n }, `card-${n}`, DOT.client, clock()));
      }
      const refused = await ta(instance, call("policy:propose", {
        change: { n: 50 }, request_id: "card-50" }), dot);
      const own = await ta(instance, call("policy:propose", {
        change: { n: 51 }, request_id: "card-51" }), await threadFor(instance, null));
      return { first, proposer, share, refused, own,
               owners: sql("SELECT proposer FROM policy_proposals WHERE request_id = 'card-51'") };
    });
    expect(out.first.generation).toBe(1);
    expect(out.proposer).toEqual([{ proposer: DOT.client }]);
    expect(out.share).toEqual([{ proposer: DOT.client, pending: 1 }]);
    expect(out.refused).toEqual({
      text: threw("this client's pending policy proposals are at their limit"), error: true });
    expect(out.own.error).toBe(false);
    expect(out.owners).toEqual([{ proposer: "owner" }]);
  });
});

describe("packages and names (review L1, L2, L3)", () => {
  it("refuses a catalog loudly where a tool of the object's own takes a platform name",
     async () => {
    const stub = env.MALFORMED.getByName(`owner-${crypto.randomUUID()}`);
    const out = await runInDurableObject(stub, async (instance: MalformedOwnerDO) => {
      instance.script = Array.from({ length: 20 }, () => step);
      const catalog = JSON.parse((await instance.harness.prompt(
        `TA ${JSON.stringify({ op: "catalog" })}`)).text!) as Shown;
      const usage = JSON.parse((await instance.harness.prompt(
        `TA ${JSON.stringify(call("files:usage"))}`)).text!) as Shown;
      return { catalog, usage };
    });
    expect(out.catalog).toEqual({
      text: threw("the tool files:usage takes a platform capability's name"), error: true });
    // A call of the name is the platform's: the capability, never the shadow.
    expect(out.usage.error).toBe(false);
    expect(JSON.parse(out.usage.text)).toHaveProperty("limits");
  });

  it("installs a package as it was checked, whatever its getters return later", async () => {
    const stub = env.MALFORMED.getByName(`owner-${crypto.randomUUID()}`);
    const out = await runInDurableObject(stub, async (instance: MalformedOwnerDO) => {
      const root = await (await instance.harness.pi()).root(BG);
      const agent = await root.agent(BG);
      return {
        benign: agent.callable.some((tool) => tool.name === "benign"),
        read: agent.tools.find((tool) => tool.name === "read")!.description,
      };
    });
    expect(out.benign).toBe(true);
    expect(out.read).not.toBe("A tool named read.");
    expect((await stub.noticeList({ owner: stub.name!, caller: stub.name!, origin: null }))
      .notices.map((notice) => notice.detail).filter((detail) => detail.includes("getter")))
      .toEqual([]);
  });

  it("installs a package's tools as they were checked, whatever is pushed onto its own " +
     "array later (A3's review)", async () => {
    const stub = env.MALFORMED.getByName(`owner-${crypto.randomUUID()}`);
    const out = await runInDurableObject(stub, async (instance: MalformedOwnerDO) => {
      const pi = await instance.harness.pi();
      const callable = async () => (await (await pi.root(BG)).agent(BG)).callable
        .map((tool) => tool.name).filter((name) => name === "early" || name === "pushed");
      const before = await callable();
      instance.late.push(instance.pushed);
      return { before, after: await callable() };
    });
    expect(out).toEqual({ before: ["early"], after: ["early"] });
  });

  it("names the platform capabilities no package may take, as the catalog lists them",
     async () => {
    const r = await rig();
    const page = await r.in(async (instance) => value(instance, { op: "catalog" },
                                                      await threadFor(instance, null)));
    expect(page.capabilities.slice(0, PLATFORM_NAMES.length)
      .map((entry: { name: string }) => entry.name)).toEqual(PLATFORM_NAMES);
    expect(PLATFORM_NAMES).toHaveLength(6);
  });

  it("refuses a call that carries a catalog field, a name that is not text and arguments " +
     "that are not an object; a page past the end is empty", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      const session = await threadFor(instance, null);
      return {
        extra: await ta(instance, { ...call("files:usage"), query: "x" }, session),
        stringName: await ta(instance, { op: "call", name: 5, arguments: {} }, session),
        nullArgs: await ta(instance, { op: "call", name: "files:usage", arguments: null },
                           session),
        past: await value(instance, { op: "catalog", offset: 1000 }, session),
      };
    });
    expect(out.extra).toEqual({ text: threw(INVALID_REQUEST), error: true });
    expect(out.stringName.error).toBe(true);
    expect(out.nullArgs.error).toBe(true);
    expect(out.past).toEqual({ capabilities: [], total: 14 });
  });
});

describe("ta's model call through the gateway (review L4)", () => {
  it("sends its system prompt and its output cap, and a revoke mid-stream cancels the " +
     "upstream", async () => {
    const r = await gated();
    await r.connect("sk-ta-stream");
    const at = signedIn();
    await r.stub.outsideAgents(r.bind({ origin: MUSE, source: MCP, authenticated_at: at }));
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
    const cancelled = async () => ((await (await fetch(`https://status.test/cancelled?id=${id}`))
      .json()) as { cancelled: boolean | null }).cancelled;
    const out = await r.in(async (instance: GatedOwnerDO) => {
      const thread = await instance.agent.thread("main", {
        client: MUSE.client, generation: 1, authenticated_at: at, universe: null,
        agent: "main" });
      const session = thread.thread_id;
      const { operationId } = await instance.harness.submit(`cmd:ta-model-slow-${id}`,
                                                                  { session });
      await until(async () => (await cancelled()) === false, "the nested stream to open");
      await instance.grantRevoke(r.bind(), { client: MUSE.client });
      // The revoke dropped the thread: its store, as it was wiped, says how the turn ended.
      const settled = wipedSubmission(instance.probes, thread.store, { request: operationId })!;
      return { settled: [settled.status, settled.reason], bodies: instanceCalls(instance.calls) };
    });
    expect(out.settled).toEqual(["unanswered", "aborted"]);
    await until(async () => (await cancelled()) === true, "the nested upstream to be cancelled");
    expect(out.bodies).toHaveLength(2);
    const nested = JSON.parse(out.bodies[1]!) as Record<string, unknown>;
    expect(JSON.stringify(nested)).toContain("answer in one word");
    expect(JSON.stringify(nested)).toMatch(/"max_?[tT]okens":77\b/);
    expect(JSON.stringify(nested)).toContain(`cmd:slow-${id}`);
  }, 30_000);
});
