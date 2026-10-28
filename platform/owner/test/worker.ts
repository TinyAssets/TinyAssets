/**
 * The workerd tests' Worker: the owner Worker's own exports, and two test classes of the
 * same object whose agent opens pi on pi-ai's faux provider instead of an empty catalog.
 *
 * The faux provider is registered as the `gateway` provider with the one model `auto`,
 * the default model a conversation starts on, so the object's wiring is the production
 * wiring with the model source swapped where the gateway provider (A1) will go. It answers
 * each request from the instance's `script`, by how many answers the conversation already
 * holds; with none set, from the script a test stored in the object (`storeScript`), so a
 * conversation a new instance resumes after an eviction continues the same script. A
 * request with no script left is an error, so a turn never succeeds by accident.
 */
import type { Context } from "@earendil-works/chord/context";
import {
  type AssistantMessage, createModels, fauxAssistantMessage, type FauxProviderHandle,
  fauxProvider, type FauxResponseFactory, type Message, Type,
} from "@earendil-works/pi-ai";
import {
  defineExtension, defineTool, type Extension, type ToolRegistration, wrapTool,
} from "@earendil-works/pi-durable";
import { CodingTools } from "@earendil-works/pi-durable/tools";

import { OwnerAgent } from "../src/agent";
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
}

function answers(messages: readonly Message[]): number {
  return messages.filter((message) => message.role === "assistant").length;
}

/** The test tools: `hold` (replay-unsafe), `spawn` (a task-owned conversation), `read`'s gate. */
function testExtensions(probes: () => Probes): Extension[] {
  const read = CodingTools.tools!.find((tool) => tool.name === "read")! as ToolRegistration;
  return [defineExtension({
    name: "test.tools",
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

/** The owner object, its agent on the faux model and the test tools. */
export class FauxOwnerDO extends OwnerDO {
  readonly agent: OwnerAgent;
  readonly faux: FauxProviderHandle;
  script: Step[] = [];
  readonly probes: Probes = { readGate: null, reads: 0, holdGate: null, holds: 0 };

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { agent?: OwnerAgent; faux?: FauxProviderHandle; self?: FauxOwnerDO } = {};
    super(ctx, env, (parts) => {
      made.faux = scripted(() => {
        const self = made.self!;
        return self.script.length > 0 ? self.script : storedScript(ctx);
      });
      const models = createModels();
      models.setProvider(made.faux.provider);
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
        ...testExtensions(() => ({ readGate: null, reads: 0, holdGate: null, holds: 0 })),
      ] });
      return made.agent;
    });
    this.agent = made.agent!;
  }
}

/** A text answer. */
export const says = (text: string): AssistantMessage => fauxAssistantMessage(text);
