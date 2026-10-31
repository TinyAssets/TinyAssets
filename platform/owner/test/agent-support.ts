/**
 * The tests' handle on the agent's threads: what A0-A3's tests drove through PiHarness's
 * one Session (`prompt`, `submit`, `wait`, `messages`, `abort`, `pi`), now over the thread
 * host, one Session per thread.
 *
 * A `session` names where an input goes, as PiHarness's did: a thread's id is that thread's
 * root; a number is a conversation in the given thread's Session (`thread`), by default the
 * owner's own thread with the `main` agent, made on first use as a `converse` thread, as the
 * owner's first message makes it, and found again by its row after an eviction.
 */
import { BACKGROUND_CONTEXT, withAbortSignal } from "@earendil-works/chord/context";
import {
  type ConversationId, type EntryRecord, type Harness, ROOT_CONVERSATION_ID,
} from "@earendil-works/pi-durable";

import type { AgentParts, OwnerAgent } from "../src/agent";
import type { OperationResult } from "../src/thread-host";

const BG = BACKGROUND_CONTEXT;

/** What a test reaches the agent through: the agent, and the parts it was built from. */
export interface AgentHolder {
  readonly agent: OwnerAgent;
  readonly parts: AgentParts;
}

/** Where an input goes: a thread's root (its id), or a conversation in a thread's Session. */
export interface Where {
  readonly session?: string;
  readonly thread?: string;
}

/** A settled operation, with where it ran. */
export type Result = OperationResult & { readonly session: string };

/** The owner's own thread with `main`: found by its row, else made. */
export async function ownerThread(holder: AgentHolder): Promise<string> {
  const found = holder.parts.threads.conversing(null, "main");
  if (found !== null) {
    return found.thread_id;
  }
  return (await holder.agent.thread("main", null, { kind: "converse" })).thread_id;
}

export class TestHarness {
  readonly #holder: AgentHolder;

  constructor(holder: AgentHolder) {
    this.#holder = holder;
  }

  /** The thread and conversation `where` names. */
  async #at(where: Where = {}): Promise<{ thread: string; conversation: ConversationId }> {
    const { session } = where;
    if (session !== undefined && session.startsWith("thread_")) {
      return { thread: session, conversation: ROOT_CONVERSATION_ID };
    }
    const thread = where.thread ?? await ownerThread(this.#holder);
    return { thread, conversation: (session === undefined ? ROOT_CONVERSATION_ID
      : Number(session)) as ConversationId };
  }

  /** The Session of the thread `where` names, opened. */
  async pi(where: Where = {}): Promise<Harness> {
    return this.#holder.agent.host.pi((await this.#at(where)).thread);
  }

  /** The thread `where` names. */
  async thread(where: Where = {}): Promise<string> {
    return (await this.#at(where)).thread;
  }

  /** Durably submit an input; resolves before the model runs. */
  async submit(input: string, options: Where & { operationId?: string;
                                                 whenBusy?: "followUp" | "steer" } = {}):
      Promise<{ operationId: string; session: string; accepted: boolean }> {
    const { thread, conversation } = await this.#at(options);
    const operationId = options.operationId ?? crypto.randomUUID();
    const session = options.session ?? thread;
    const host = this.#holder.agent.host;
    if (conversation === ROOT_CONVERSATION_ID) {
      const receipt = await host.submit(thread, input, {
        operationId, ...(options.whenBusy === undefined ? {} : { whenBusy: options.whenBusy }) });
      return { ...receipt, session };
    }
    return host.use(thread, async ({ pi, storage }) => {
      const accepted = await storage.submissionByRequest(conversation, operationId, BG)
        === undefined;
      const handle = await pi.conversation(conversation, BG);
      if (handle === undefined) {
        throw new Error(`no conversation ${conversation}`);
      }
      await handle.submit({ type: "input", content: input, requestId: operationId,
                            whenBusy: options.whenBusy ?? "followUp" }, BG);
      return { operationId, session, accepted };
    });
  }

  /** Wait for an operation to settle. */
  async wait(operationId: string, options: Where & { signal?: AbortSignal } = {}):
      Promise<Result> {
    const { thread, conversation } = await this.#at(options);
    const session = options.session ?? thread;
    const host = this.#holder.agent.host;
    if (conversation === ROOT_CONVERSATION_ID) {
      return { ...await host.settled(thread, operationId, options.signal), session };
    }
    const context = options.signal === undefined ? BG : withAbortSignal(options.signal, BG);
    return host.use(thread, async ({ pi, storage }) => {
      const record = await storage.submissionByRequest(conversation, operationId, context);
      const submission = record === undefined ? undefined
        : await pi.submission(record.id, context);
      if (submission === undefined) {
        return { operationId, status: "unanswered", reason: "not_found", session };
      }
      const settled = await submission.wait(context);
      if (settled.status === "unanswered") {
        return { operationId, status: "unanswered", reason: settled.reason, session };
      }
      const answer = settled.type === "input" ? (await (await pi.conversation(
        conversation, context))!.entries({ minEntryId: settled.answer,
                                           maxEntryId: settled.answer }, 1, undefined,
                                         context)).items[0] : undefined;
      return { operationId, status: "done", session, text: textOf(answer) };
    });
  }

  /** Submit and wait, then the conversation's active transcript. */
  async prompt(input: string, options: Where = {}):
      Promise<Result & { readonly messages: EntryRecord[] }> {
    const receipt = await this.submit(input, options);
    const settled = await this.wait(receipt.operationId, options);
    return { ...settled, messages: await this.messages(options) };
  }

  /** The conversation's active transcript, as pi's entries since its newest reset. */
  async messages(where: Where = {}): Promise<EntryRecord[]> {
    const { thread, conversation } = await this.#at(where);
    return this.#holder.agent.host.use(thread, async ({ pi }) =>
      [...(await (await pi.conversation(conversation, BG))!.context(BG)).entries]);
  }

  /** The thread's unsettled submissions with a request id, oldest first. */
  async pending(where: Where = {}):
      Promise<{ operationId: string; session: string; status: "queued" | "running" }[]> {
    const { thread } = await this.#at(where);
    const session = where.session ?? thread;
    return this.#holder.agent.host.use(thread, async ({ pi }) =>
      (await pi.inspect(BG)).submissions.flatMap((record) =>
        record.requestId === undefined ? []
          : [{ operationId: record.requestId, session,
               status: record.status === "queued" ? "queued" as const : "running" as const }]));
  }

  /** Withdraw one operation, or abort the conversation's work. */
  async abort(options: Where & { operationId?: string } = {}): Promise<boolean> {
    const { thread, conversation } = await this.#at(options);
    const host = this.#holder.agent.host;
    if (options.operationId !== undefined && conversation === ROOT_CONVERSATION_ID) {
      return host.withdraw(thread, options.operationId);
    }
    return host.use(thread, async ({ pi }) => {
      await (await pi.conversation(conversation, BG))!.abort(BG);
      return true;
    });
  }
}

/** The text of an assistant entry. */
function textOf(entry: EntryRecord | undefined): string {
  const message = entry?.model?.[0];
  if (message?.role !== "assistant") {
    return "";
  }
  return message.content.map((part) => (part.type === "text" ? part.text : "")).join("");
}

/** The tests' harness over `holder`'s agent. */
export function harnessOf(holder: AgentHolder): TestHarness {
  return new TestHarness(holder);
}
