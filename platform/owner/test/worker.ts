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
 * request's turn, as the object's agent reads it from pi's records (`OwnerAgent.turnOf`)
 * when the request is made, is kept on `turns`, as the gateway provider reads it.
 */
import type { Context } from "@earendil-works/chord/context";
import {
  type AssistantMessage, createModels, fauxAssistantMessage, type FauxProviderHandle,
  fauxProvider, type FauxResponseFactory, type Message, type Provider, Type,
} from "@earendil-works/pi-ai";
import {
  defineExtension, defineTask, defineTool, type Extension, type ToolRegistration, wrapTool,
} from "@earendil-works/pi-durable";
import { CodingTools } from "@earendil-works/pi-durable/tools";

import { type AgentParts, gatewayAgent, OwnerAgent } from "../src/agent";
import type { GatewayService, Resealed } from "../src/connections";
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
  /** The conversation `spawn` last made, when it has. */
  spawned: number | null;
}

function answers(messages: readonly Message[]): number {
  return messages.filter((message) => message.role === "assistant").length;
}

/**
 * Background work that makes a conversation of its own and ends, as a package's background
 * task would; its result is the conversation's id. A test creates it conversation-owned
 * with `background: true`.
 */
type Spawning = { phase: "run" } | { phase: "made"; child: number };

export const BackgroundSpawn = defineTask<Record<string, never>, Spawning, number>({
  name: "test.background",
  version: 1,
  initial: () => ({ phase: "run" }),
  phases: {
    // The conversation is made while the task runs, then the task ends with its id.
    run: async (_task, runtime, context) => runtime.commit(async (tx) => {
      const child = await tx.createConversation({
        ownership: { kind: "task", taskId: runtime.taskId } });
      return { status: "running", checkpoint: { phase: "made", child: child.id } };
    }, context),
    made: async (task, runtime, context) => runtime.commit(() => ({
      status: "terminal", outcome: { status: "completed", result: task.state.checkpoint.child },
    }), context),
  },
  abort: async (_task, runtime, context) => runtime.commit(() => ({
    status: "terminal", outcome: { status: "failed", error: { message: "aborted" } } }), context),
});

/** The test tools: `hold` (replay-unsafe), `spawn` (a task-owned conversation), `read`'s gate. */
function testExtensions(probes: () => Probes): Extension[] {
  const read = CodingTools.tools!.find((tool) => tool.name === "read")! as ToolRegistration;
  return [defineExtension({
    name: "test.tools",
    tasks: [BackgroundSpawn],
    tools: [
      defineTool({
        name: "hold",
        description: "Hold until the test lets go.",
        parameters: Type.Object({}),
        execute: async () => {
          probes().holds++;
          await probes().holdGate;
          return { output: [{ type: "text", text: "held" }] };
        },
      }),
      defineTool({
        name: "spawn",
        description: "Make a conversation this call owns, as a subagent tool does.",
        parameters: Type.Object({}),
        execute: async (_args, api, context: Context) => {
          const child = await api.commit((tx) => tx.createConversation({
            ownership: { kind: "task", taskId: api.taskId } }), context);
          probes().spawned = child.id;
          return { output: [{ type: "text", text: String(child.id) }] };
        },
      }),
    ],
    wraps: [wrapTool(read, (tool) => ({
      ...tool,
      execute: async (args, api, context) => {
        probes().reads++;
        await probes().readGate;
        return tool.execute(args, api, context);
      },
    }))],
  })];
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

/**
 * `provider`, with `seen` told each request's `sessionId` as the request is made, before
 * it is answered.
 */
function seeing(provider: Provider, seen: (sessionId: string | undefined) => void): Provider {
  return new Proxy(provider, {
    get(target, key, receiver) {
      const value: unknown = Reflect.get(target, key, receiver);
      if ((key === "stream" || key === "streamSimple") && typeof value === "function") {
        return (...args: unknown[]) => {
          seen((args[2] as { sessionId?: string } | undefined)?.sessionId);
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
  readonly faux: FauxProviderHandle;
  script: Step[] = [];
  readonly probes: Probes = { readGate: null, reads: 0, holdGate: null, holds: 0,
                              spawned: null };
  /** Each request's turn, or `error: <message>` when the agent found none; in order made. */
  readonly turns: string[] = [];

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: OwnerAgent; faux?: FauxProviderHandle; self?: FauxOwnerDO } = {};
    super(ctx, env, (parts) => {
      made.faux = scripted(() => {
        const self = made.self!;
        return self.script.length > 0 ? self.script : storedScript(ctx);
      });
      const models = createModels();
      models.setProvider(seeing(made.faux.provider, (sessionId) => {
        const turns = made.self!.turns;
        const at = turns.push("pending") - 1;
        made.agent!.turnOf(sessionId).then((turn) => { turns[at] = turn; },
                                           (error: Error) => { turns[at] = `error: ${error.message}`; });
      }));
      made.agent = new OwnerAgent(parts, {
        models: () => models, extensions: testExtensions(() => made.self!.probes) });
      return made.agent;
    });
    made.self = this;
    this.agent = made.agent!;
    this.faux = made.faux!;
  }
}

/** The owner object with a package pi refuses to install: two tools of one name. */
export class MalformedOwnerDO extends OwnerDO {
  readonly agent: OwnerAgent;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: OwnerAgent } = {};
    super(ctx, env, (parts) => {
      const models = createModels();
      models.setProvider(scripted(() => []).provider);
      const twice = defineTool({ name: "twice", description: "One of two.",
                                 parameters: Type.Object({}),
                                 execute: async () => ({ output: [] }) });
      made.agent = new OwnerAgent(parts, { models: () => models, extensions: [
        defineExtension({ name: "pkg:broken", tools: [twice, twice] }),
        ...testExtensions(() => ({ readGate: null, reads: 0, holdGate: null, holds: 0,
                                   spawned: null })),
      ] });
      return made.agent;
    });
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
 * The production agent (`gatewayAgent`), and the turn of a test's own call to the
 * provider outside any pi run, set by its session id (`direct`): every other request's
 * turn is pi's, as in production.
 */
export class GatedAgent extends OwnerAgent {
  readonly direct = new Map<string, string>();

  override turnOf(sessionId: string | undefined): Promise<string> {
    const turn = sessionId === undefined ? undefined : this.direct.get(sessionId);
    return turn === undefined ? super.turnOf(sessionId) : Promise.resolve(turn);
  }
}

/**
 * The owner object as production builds it, on the gateway provider, with its agent and
 * parts in reach, its GATEWAY calls watched, and the faults a test sets on them.
 */
export class GatedOwnerDO extends OwnerDO {
  readonly agent: GatedAgent;
  readonly parts: AgentParts;
  calls: GatewayCall[] = [];
  readonly faults: GatewayFaults = { refreshGate: null, refreshed: null, revokeGate: null };

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: GatedAgent; parts?: AgentParts; self?: GatedOwnerDO } = {};
    const pending: GatewayCall[] = [];
    super(ctx, { ...env, GATEWAY: watched(env.GATEWAY, () => made.self?.calls ?? pending,
                                          () => made.self?.faults) },
          (parts) => {
            made.parts = parts;
            made.agent = gatewayAgent(parts, GatedAgent);
            return made.agent;
          });
    made.self = this;
    this.calls.push(...pending);
    this.agent = made.agent!;
    this.parts = made.parts!;
  }
}
