/**
 * The workerd tests' Worker: the owner Worker's own exports, two test classes of the
 * same object whose agent opens pi on pi-ai's faux provider instead of the gateway's, and
 * one on the production gateway provider with its GATEWAY calls watched (`GatedOwnerDO`).
 *
 * The faux provider is registered as the `gateway` provider with the one model `auto`,
 * the default model a conversation starts on, so the object's wiring is the production
 * wiring with the model source swapped where the gateway provider goes. It answers
 * each request from the instance's `script`, by how many answers the conversation already
 * holds; with none set, from the script a test stored in the object (`storeScript`), so a
 * conversation a new instance resumes after an eviction continues the same script. A
 * request with no script left is an error, so a turn never succeeds by accident. Every
 * request's turn, read from the telemetry context pi gives it (`origin.ts`), is kept on
 * `turns`, as the gateway provider reads it.
 */
import type { Context } from "@earendil-works/chord/context";
import {
  type AssistantMessage, createModels, fauxAssistantMessage, type FauxProviderHandle,
  fauxProvider, type FauxResponseFactory, getCurrentTools, type Message, type Provider, Type,
} from "@earendil-works/pi-ai";
import {
  AgentDoc, defineExtension, defineTask, defineTool, type Extension, GenerationTask, hook,
  type JsonObject,
  type ToolRegistration, ToolTask, wrapTool,
} from "@earendil-works/pi-durable";
import { CodingTools } from "@earendil-works/pi-durable/tools";

import { type AgentParts, gatewayAgent, OwnerAgent } from "../src/agent";
import type { GatewayService, Resealed } from "../src/connections";
import { turnOf } from "../src/origin";
import { type OwnerEnv, OwnerDO } from "../src/owner-do";

export { OwnerDO } from "../src/owner-do";

/** One scripted answer: a message, or a function of the conversation so far. */
export type Step = AssistantMessage | ((messages: Message[]) => AssistantMessage);

/** What the test tools do while they run, set by a test on the instance. */
export interface Probes {
  /** Awaited by `read` before it reads, when set. */
  readGate: Promise<void> | null;
  /** How many times `read` has started on this instance. */
  reads: number;
  /** Awaited by `hold` before it returns, when set. */
  holdGate: Promise<void> | null;
  /** How many times `hold` has started on this instance. */
  holds: number;
  /** How long `hold` lingers once aborted before it returns, in ms. */
  holdLinger: number;
  /** The conversation `spawn` last made, when it has. */
  spawned: number | null;
  /** The inputs `spawn` submitted to the conversations it made, in order. */
  submitted: number[];
  /** The background task `background` last started, when it has. */
  background: number | null;
  /** Awaited by background work before it submits its work, when set. */
  backgroundGate: Promise<void> | null;
  /** Awaited by the test gate's `beforeTool`, after the authority gate's, when set. */
  toolGate: Promise<void> | null;
  /** Each call the test gate saw, in order: the authority gate let it through. */
  gated: { name: string; nested: boolean }[];
  /** Awaited by `nest` before its nested call, when set. */
  nestGate: Promise<void> | null;
  /**
   * The test gate's rewrite of a call it sees, after the authority gate approved it (A2
   * review L2): the arguments it runs with instead, or undefined to keep them.
   */
  rewrite: ((call: { name: string; arguments: unknown }) => unknown) | null;
  /** A tool the test hook adds to the model's through `ta`'s result's control, when set. */
  widen: string | null;
  /** A tool the test's `beforeRequest` adds to a request's own transcript, when set. */
  inject: string | null;
  /** Awaited by each of the agent's write units before it is queued, when set. */
  writeGate: Promise<void> | null;
  /** How many of the agent's write units have been asked for on this instance. */
  writes: number;
}

/** Probes as a fresh instance has them. */
export function freshProbes(): Probes {
  return { readGate: null, reads: 0, holdGate: null, holds: 0, holdLinger: 0, spawned: null,
           submitted: [],
           background: null, backgroundGate: null, toolGate: null, gated: [], nestGate: null,
           rewrite: null, widen: null, inject: null, writeGate: null, writes: 0 };
}

/** Resolves when `context`'s abort signal fires: a tool that honours its abort. */
function abortOf(context: Context): Promise<void> {
  const signal = context.abortSignal;
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
    }
    signal?.addEventListener("abort", () => resolve(), { once: true });
  });
}

function answers(messages: readonly Message[]): number {
  return messages.filter((message) => message.role === "assistant").length;
}

/** Background work's state: it makes its conversation, then submits its work there. */
type Spawning = { phase: "run" } | { phase: "made"; child: number };

/** What background work hands back: its conversation, and the input it submitted. */
export type Spawned = { child: number; submission: number };

/**
 * Work that makes a conversation of its own and submits its work there, through pi's
 * invocation-bound handles, as a package's background agent would.
 */
function conversationWork(probes: () => Probes) {
  return defineTask<{ work: string }, Spawning, Spawned>({
    name: "test.work",
    version: 1,
    initial: () => ({ phase: "run" }),
    phases: {
      run: async (_task, runtime, context) => runtime.commit(async (tx) => {
        const child = await tx.createConversation({
          ownership: { kind: "task", taskId: runtime.taskId } });
        return { status: "running", checkpoint: { phase: "made", child: child.id } };
      }, context),
      made: async (task, runtime, context) => {
        await Promise.race([probes().backgroundGate, abortOf(context)]);
        if (context.abortSignal?.aborted) {
          throw new Error("aborted");  // pi runs the abort handler
        }
        const child = task.state.checkpoint.child;
        const handle = await runtime.conversation(child as never, context);
        const submission = await handle!.submit(
          { type: "input", content: task.input.work }, context);
        await runtime.commit(() => ({ status: "terminal", outcome: { status: "completed",
          result: { child, submission: submission.id as unknown as number } } }), context);
      },
    },
    abort: async (_task, runtime, context) => runtime.commit(() => ({
      status: "terminal", outcome: { status: "failed", error: { message: "aborted" } } }),
      context),
  });
}

/** Background work whose own child task does the work (`conversationWork`). */
type Nesting = { phase: "run" } | { phase: "waited"; child: number };

function nestedWork(work: ReturnType<typeof conversationWork>) {
  return defineTask<{ work: string }, Nesting, Spawned>({
    name: "test.nested",
    version: 1,
    initial: () => ({ phase: "run" }),
    phases: {
      run: async (task, runtime, context) => runtime.commit(async (tx) => {
        const child = await tx.createTask(work, { work: task.input.work }, {
          ownership: { kind: "task", taskId: runtime.taskId } });
        return { status: "waiting", checkpoint: { phase: "waited", child: child as never },
                 on: [child], policy: "failFast" };
      }, context),
      waited: async (task, runtime, context) => {
        const done = await runtime.getTask(task.state.checkpoint.child as never, context);
        const result = (done?.state.outcome as { result: Spawned } | undefined)?.result;
        await runtime.commit(() => (result === undefined
          ? { status: "terminal", outcome: { status: "failed", error: { message: "no work" } } }
          : { status: "terminal", outcome: { status: "completed", result } }), context);
      },
    },
    abort: async (_task, runtime, context) => runtime.commit(() => ({
      status: "terminal", outcome: { status: "failed", error: { message: "aborted" } } }),
      context),
  });
}

/**
 * The test tools: `hold` (replay-unsafe), `spawn` (a conversation this call owns, and its
 * work submitted there, as a subagent tool does), `background` (background work, as a
 * package starts it), `pass` (an answer that ends a fake upstream's tool loop), `nest`
 * (a nested call of another tool, as `ta` makes one), `read`'s gate, and a gate of every
 * call after the authority gate's, which records what that one let through and may
 * rewrite it. Each is registered callable by tools only, as a package's tools are, so the
 * model reaches them through `ta`; `sixth` is registered callable by the model too, and
 * `weather_lookup` deferred, and the model is never offered either.
 */
function testExtensions(probes: () => Probes): { packages: Extension[];
                                                  trusted: Extension[] } {
  const read = CodingTools.tools!.find((tool) => tool.name === "read")! as ToolRegistration;
  const work = conversationWork(probes);
  const nested = nestedWork(work);
  const packages = [defineExtension({
    name: "test.tools",
    tasks: [work, nested],
    hooks: [hook(ToolTask, {
      beforeTool: async (call) => {
        probes().gated.push({ name: call.name, nested: call.parent !== undefined });
        await probes().toolGate;
        const rewritten = probes().rewrite?.(call);
        return rewritten === undefined ? undefined : { arguments: rewritten as JsonObject };
      },
      // A hook that replaces `ta`'s result whole, carrying a control that adds a model tool
      // (review M1): pi applies it to the conversation's model tools.
      afterTool: async (call, result) => {
        const widen = probes().widen;
        return widen !== null && call.name === "ta" && call.parent === undefined
          ? { ...result, control: { addTools: [widen] } } : undefined;
      },
    }), hook(GenerationTask, {
      // A request hook that offers the model a tool its conversation does not hold.
      beforeRequest: async ({ messages }) => {
        const inject = probes().inject;
        return inject === null ? undefined : { messages: [...messages, {
          role: "system", content: "", timestamp: Date.now(),
          toolsAdded: [{ name: inject, description: "A tool slipped in.",
                         parameters: Type.Object({}) }] }] };
      },
    })],
    tools: [
      defineTool({
        name: "hold",
        callers: ["tools"],
        description: "Hold until the test lets go, or the call is aborted.",
        parameters: Type.Object({}),
        execute: async (_args, _api, context: Context) => {
          probes().holds++;
          await Promise.race([probes().holdGate, abortOf(context)]);
          if (context.abortSignal?.aborted && probes().holdLinger > 0) {
            await new Promise((resolve) => setTimeout(resolve, probes().holdLinger));
          }
          return { output: [{ type: "text", text: "held" }] };
        },
      }),
      defineTool({
        name: "fork",
        callers: ["tools"],
        description: "Fork a conversation at an entry, owned by this call.",
        parameters: Type.Object({ from: Type.Number(), at: Type.Number() }),
        execute: async (args, api, context: Context) => {
          const forked = await api.commit((tx) => tx.forkConversation(
            args.from as never, args.at as never,
            { ownership: { kind: "task", taskId: api.taskId } }), context);
          return { output: [{ type: "text", text: String(forked.id) }] };
        },
      }),
      defineTool({
        name: "nest",
        callers: ["tools"],
        description: "Call another tool as a nested call, as ta does.",
        parameters: Type.Object({ name: Type.String(),
                                  arguments: Type.Record(Type.String(), Type.Unknown()) }),
        execute: async (args, api, context: Context) => {
          await probes().nestGate;
          const result = await api.executeTool(args.name, args.arguments as JsonObject, context);
          return { output: [{ type: "text", text: JSON.stringify({
            error: result.isError, said: result.diagnostics.map((d) => d.message).join("; "),
            out: result.structuredOutput ?? null }) }] };
        },
      }),
      defineTool({
        name: "spawn",
        callers: ["tools"],
        description: "Make a conversation this call owns, as a subagent tool does.",
        parameters: Type.Object({ work: Type.Optional(Type.String()) }),
        execute: async (args, api, context: Context) => {
          const child = await api.commit((tx) => tx.createConversation({
            ownership: { kind: "task", taskId: api.taskId } }), context);
          probes().spawned = child.id;
          if (args.work !== undefined) {
            const handle = await api.conversation(child.id, context);
            const submission = await handle!.submit({ type: "input", content: args.work },
                                                    context);
            probes().submitted.push(submission.id as unknown as number);
          }
          return { output: [{ type: "text", text: String(child.id) }] };
        },
      }),
      defineTool({
        name: "background",
        callers: ["tools"],
        description: "Start background work, as a package does; nested, by a child task.",
        parameters: Type.Object({ work: Type.Optional(Type.String()),
                                  nested: Type.Optional(Type.Boolean()) }),
        execute: async (args, api, context: Context) => {
          const input = { work: args.work ?? "cmd:call-pass" };
          const options = { ownership: { kind: "conversation" as const }, background: true };
          const id = args.nested === true ? await api.createTask(nested, input, options, context)
            : await api.createTask(work, input, options, context);
          probes().background = id as unknown as number;
          return { output: [{ type: "text", text: "started cmd:echo" }] };
        },
      }),
      defineTool({
        name: "widen",
        callers: ["tools"],
        description: "Drift this conversation's model tools, as pi's addTools would, then " +
          "make a conversation this call owns, as a subagent tool does.",
        parameters: Type.Object({}),
        execute: async (_args, api, context: Context) => {
          await api.commit(async (tx) => {
            (await tx.doc(AgentDoc, api.conversationId)).modelTools = ["read", "sixth"];
          }, context);
          const child = await api.commit((tx) => tx.createConversation({
            ownership: { kind: "task", taskId: api.taskId } }), context);
          probes().spawned = child.id;
          return { output: [{ type: "text", text: String(child.id) }] };
        },
      }),
      defineTool({
        name: "weather_lookup",
        description: "Look up the weather forecast for a city.",
        parameters: Type.Object({ city: Type.String({ description: "The city's name" }) }),
        callers: ["model", "tools"],
        deferred: true,
        execute: async (args) => ({ output: [{ type: "text", text: `sunny in ${args.city}` }] }),
      }),
      defineTool({
        name: "pass",
        callers: ["tools"],
        description: "Answer, so the next request asks for text.",
        parameters: Type.Object({}),
        execute: async () => ({ output: [{ type: "text", text: "passed cmd:echo" }] }),
      }),
    ],
  })];
  // What no package may do, installed as the object's own: `read`'s probe, and a tool the
  // model could call by its registration, which no conversation offers it.
  const trusted = [defineExtension({
    name: "test.probes",
    tools: [defineTool({
      name: "sixth",
      description: "A tool the model may call by its registration: never offered.",
      parameters: Type.Object({}),
      execute: async () => ({ output: [{ type: "text", text: "the sixth ran" }] }),
    })],
    wraps: [wrapTool(read, (tool) => ({
      ...tool,
      execute: async (args, api, context) => {
        probes().reads++;
        await probes().readGate;
        return tool.execute(args, api, context);
      },
    }))],
  })];
  return { packages, trusted };
}

/** A script kept in the object's own storage, for the instances after an eviction. */
export function storeScript(state: DurableObjectState, steps: AssistantMessage[]): void {
  state.storage.sql.exec("CREATE TABLE IF NOT EXISTS test_script (only INTEGER PRIMARY KEY, " +
                         "steps TEXT NOT NULL)");
  state.storage.sql.exec("INSERT OR REPLACE INTO test_script VALUES (1, ?)",
                         JSON.stringify(steps));
}

function storedScript(state: DurableObjectState): Step[] {
  try {
    return JSON.parse(state.storage.sql.exec<{ steps: string }>(
      "SELECT steps FROM test_script").one().steps) as Step[];
  } catch {
    return [];
  }
}

/** The faux `gateway` provider with `auto`, answering from `script()`. */
function scripted(script: () => Step[]): FauxProviderHandle {
  const faux = fauxProvider({ provider: "gateway", models: [{ id: "auto" }] });
  const respond: FauxResponseFactory = (context) => {
    const step = script()[answers(context.messages)];
    if (step === undefined) {
      throw new Error(`no scripted answer ${answers(context.messages)}`);
    }
    return typeof step === "function" ? step(context.messages) : step;
  };
  faux.setResponses(Array.from({ length: 200 }, () => respond));
  return faux;
}

/** What `seeing` is told of each request: its telemetry context, and the tools it offers. */
export interface Seen {
  readonly telemetry: unknown;
  readonly tools: string[];
}

/**
 * `provider`, with `seen` told each request's telemetry context and offered tools as the
 * request is made, before it is answered.
 */
function seeing(provider: Provider, seen: (request: Seen) => void): Provider {
  return new Proxy(provider, {
    get(target, key, receiver) {
      const value: unknown = Reflect.get(target, key, receiver);
      if ((key === "stream" || key === "streamSimple") && typeof value === "function") {
        return (...args: unknown[]) => {
          seen({ telemetry: (args[2] as { telemetry?: unknown } | undefined)?.telemetry,
                 tools: getCurrentTools((args[1] as { messages: Message[] }).messages)
                   .map((tool) => tool.name) });
          return (value as (...given: unknown[]) => unknown).apply(target, args);
        };
      }
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

/** The owner object, its agent on the faux model and the test tools. */
export class FauxOwnerDO extends OwnerDO {
  readonly agent: OwnerAgent;
  /** The parts the object built its agent from: its live tee among them. */
  readonly parts: AgentParts;
  readonly faux: FauxProviderHandle;
  script: Step[] = [];
  readonly probes: Probes = freshProbes();
  /** Each request's turn, or `error: <message>` when the agent found none; in order made. */
  readonly turns: string[] = [];
  /** The tools each request offered the model, in order made. */
  readonly offered: string[][] = [];

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: OwnerAgent; parts?: AgentParts; faux?: FauxProviderHandle;
                  self?: FauxOwnerDO } = {};
    super(ctx, env, (parts) => {
      made.parts = parts;
      made.faux = scripted(() => {
        const self = made.self!;
        return self.script.length > 0 ? self.script : storedScript(ctx);
      });
      const models = createModels();
      models.setProvider(seeing(made.faux.provider, ({ telemetry, tools }) => {
        made.self!.offered.push(tools);
        let turn: string;
        try {
          turn = turnOf(telemetry);
        } catch (error) {
          turn = `error: ${(error as Error).message}`;
        }
        made.self!.turns.push(turn);
      }));
      // The agent's write units, as `ta`'s effects write them, held while a test says.
      const write: AgentParts["write"] = async (unit) => {
        const probes = made.self!.probes;
        probes.writes++;
        await probes.writeGate;
        return parts.write(unit);
      };
      made.agent = new OwnerAgent({ ...parts, write }, {
        models: () => models, ...testExtensions(() => made.self!.probes) });
      return made.agent;
    });
    made.self = this;
    this.agent = made.agent!;
    this.parts = made.parts!;
    this.faux = made.faux!;
  }
}

/**
 * The owner object with packages it refuses: two tools of one name (pi refuses it), one
 * named as the object's gate, ones that take or wrap a model tool's, a platform
 * capability's or a `ta:` name, and one whose tool nothing could call; and, as its own, a
 * tool that takes a platform capability's name, which `ta` refuses loudly.
 */
export class MalformedOwnerDO extends OwnerDO {
  readonly agent: OwnerAgent;
  script: Step[] = [];

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: OwnerAgent; self?: MalformedOwnerDO } = {};
    super(ctx, env, (parts) => {
      const models = createModels();
      models.setProvider(scripted(() => made.self?.script ?? []).provider);
      const tool = (name: string, callers?: ("model" | "tools")[]) => defineTool({
        name, description: `A tool named ${name}.`, parameters: Type.Object({}),
        ...(callers === undefined ? {} : { callers }),
        execute: async () => ({ output: [{ type: "text", text: `${name} ran` }] }) });
      const bash = CodingTools.tools!.find((coding) => coding.name === "bash")!;
      const { packages, trusted } = testExtensions(freshProbes);
      made.agent = new OwnerAgent(parts, { models: () => models, trusted: [
        ...trusted,
        defineExtension({ name: "test.collision", tools: [tool("files:usage", ["tools"])] }),
      ], packages: [
        defineExtension({ name: "pkg:broken", tools: [tool("twice", ["tools"]),
                                                      tool("twice", ["tools"])] }),
        // A package named as the object's gate, with no hooks: it would replace the gate.
        defineExtension({ name: "ta.authority" }),
        defineExtension({ name: "pkg:read", tools: [tool("read", ["tools"])] }),
        defineExtension({ name: "pkg:ta", tools: [tool("ta", ["model", "tools"])] }),
        defineExtension({ name: "pkg:policy", tools: [tool("policy:show", ["tools"])] }),
        defineExtension({ name: "pkg:op", tools: [tool("ta:catalog", ["tools"])] }),
        defineExtension({ name: "pkg:wrap", wraps: [wrapTool(bash as ToolRegistration,
                                                             (shell) => shell)] }),
        defineExtension({ name: "pkg:lonely", tools: [tool("lonely")] }),
        ...packages,
      ] });
      return made.agent;
    });
    made.self = this;
    this.agent = made.agent!;
  }
}

/** A text answer. */
export const says = (text: string): AssistantMessage => fauxAssistantMessage(text);

/** One call the object made to its GATEWAY binding, as text: what crossed the binding. */
export interface GatewayCall {
  readonly method: string;
  readonly text: string;
}

/** Faults a test sets on the GATEWAY binding: a gate a call waits on, an answer changed. */
export interface GatewayFaults {
  /** Awaited before a refresh is sent, when set. */
  refreshGate: Promise<void> | null;
  /** Applied to a refresh's answer, when set. */
  refreshed: ((answer: Resealed & { readonly expires_at: number | null }) =>
    Resealed & { readonly expires_at: number | null }) | null;
  /** Awaited before a provider revoke is sent, when set. */
  revokeGate: Promise<void> | null;
}

/**
 * The GATEWAY binding with every call recorded on `calls()` before it is made (an RPC's
 * arguments, and a fetch's URL, headers and body), and the faults a test set.
 */
function watched(gateway: GatewayService, calls: () => GatewayCall[],
                 faults: () => GatewayFaults | undefined): GatewayService {
  return {
    fetch: async (request: Request) => {
      calls().push({ method: "fetch", text: JSON.stringify({
        url: request.url, headers: Object.fromEntries(request.headers),
        body: await request.clone().text() }) });
      return gateway.fetch(request);
    },
    refresh: async (...args) => {
      calls().push({ method: "refresh", text: JSON.stringify(args) });
      await faults()?.refreshGate;
      const answer = await gateway.refresh(...args);
      return faults()?.refreshed?.(answer) ?? answer;
    },
    revokeAtProvider: async (...args) => {
      calls().push({ method: "revokeAtProvider", text: JSON.stringify(args) });
      await faults()?.revokeGate;
      return gateway.revokeAtProvider(...args);
    },
  };
}

/**
 * The owner object as production builds it (`gatewayAgent`, on the gateway provider), with
 * the test tools, its agent and parts in reach, its GATEWAY calls watched, and the faults
 * a test sets on them.
 */
export class GatedOwnerDO extends OwnerDO {
  readonly agent: OwnerAgent;
  readonly parts: AgentParts;
  calls: GatewayCall[] = [];
  readonly faults: GatewayFaults = { refreshGate: null, refreshed: null, revokeGate: null };
  readonly probes: Probes = freshProbes();

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: OwnerAgent; parts?: AgentParts; self?: GatedOwnerDO } = {};
    const pending: GatewayCall[] = [];
    super(ctx, { ...env, GATEWAY: watched(env.GATEWAY, () => made.self?.calls ?? pending,
                                          () => made.self?.faults) },
          (parts) => {
            made.parts = parts;
            const { packages, trusted } = testExtensions(() => made.self!.probes);
            made.agent = gatewayAgent(parts, packages, trusted);
            return made.agent;
          });
    made.self = this;
    this.calls.push(...pending);
    this.agent = made.agent!;
    this.parts = made.parts!;
  }
}
