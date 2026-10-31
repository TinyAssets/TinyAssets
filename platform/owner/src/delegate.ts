/**
 * A subagent: a conversation a `ta` call owns, in its thread's own Session (tasks 5, A5 with
 * F5; pi-durable README "Abort and Subagents", example 22).
 *
 * `ta`'s `agent:delegate {agent?, task}` (`ta.ts`, run by `OwnerAgent`) makes its child here,
 * in one commit of the calling tool's (`api.commit`): owned by the call's own task
 * (`ownership: {kind: "task", taskId}`), so pi's ownership index finds it again on a rerun
 * (`scanConversations({ownerTaskId})`), aborting the call aborts it, and the parent is idle
 * only once it is. pi copies the calling conversation's agent into it (model, extensions,
 * tools); the object's `conversationCreated` then fixes its model tools to the five and gives
 * it the calling conversation's authority (`OwnerAgent.#inherit`); a named agent's
 * instructions (its `AGENTS.md`) replace the copied ones in the same commit, no agent named
 * keeping the caller's. This is the object's one conversation maker besides a thread's root
 * (the agent-authority guard), and the one write of a conversation's agent besides its model
 * tools; the create-task guard holds both creation points, this and a scheduled launch's.
 *
 * A thread holds at most `THREAD_CONVERSATIONS` conversations with live work at once, its
 * root among them, each with at most two live compactions (a background one and a run's
 * blocking one), so the owner's turn rows stay bounded (`schema.ts`, migration 10's trigger,
 * and the owner-bound guard's turn terms). A child is live while the call that owns
 * it is, or while it holds a live task of its own; the session's `ta.delegations` document
 * lists them, checked and pruned in each creating commit, which refuses past the cap with
 * the fixed code `thread_conversations_full`.
 */
import {
  AgentDoc, type ConversationId, defineDoc, type TaskId, type Tx,
} from "@earendil-works/pi-durable";

import { Refused } from "./errors";
import { THREAD_CONVERSATIONS } from "./schema";

/** The refusal past `THREAD_CONVERSATIONS` conversations with live work in a thread. */
export const THREAD_CONVERSATIONS_FULL = "thread_conversations_full: the thread holds as " +
  "many working conversations as it may; wait for a subagent to finish";
/** The task states pi still runs. */
const LIVE = Object.freeze(["pending", "running", "waiting", "sleeping", "completing"] as const);

/** The child's one input's request id: the owning call's task, so a rerun's is pi's duplicate. */
export function subagentRequest(taskId: TaskId): string {
  return `subagent:${taskId}`;
}

/** One live delegation: its child and the call's task that owns it. */
export type Delegation = { child: number; task: number };

/** `ta.delegations`: the Session's live delegations, as each creating commit leaves them. */
export function delegationsDoc() {
  return defineDoc<{ live: Delegation[] }>({
    kind: "ta.delegations",
    version: 1,
    scope: "session",
    initial: () => ({ live: [] }),
    checkpointWhen: () => true,
  });
}

/** Whether a delegation still has live work: its call, or a task of its child's. */
async function live(tx: Tx, delegation: Delegation): Promise<boolean> {
  const owner = await tx.task(delegation.task as TaskId);
  if (owner !== undefined && owner.state.status !== "terminal") {
    return true;
  }
  for (const status of LIVE) {
    const page = await tx.scanTasks({ conversationId: delegation.child as ConversationId,
                                      status }, 1);
    if (page.items.length > 0) {
      return true;
    }
  }
  return false;
}

/**
 * In the calling tool's commit: its child, found again by the ownership index, or made, owned
 * by `taskId`, with `instructions` (a named agent's; null keeps the caller's copied ones, and
 * undefined is an agent with none), within the thread's cap.
 */
export async function createChild(tx: Tx, taskId: TaskId, instructions: string | null | undefined,
                                  delegations: ReturnType<typeof delegationsDoc>):
    Promise<ConversationId> {
  const existing = (await tx.scanConversations({ ownerTaskId: taskId }, 1)).items[0];
  if (existing !== undefined) {
    return existing.id;
  }
  const kept = await tx.doc(delegations);
  const still: Delegation[] = [];
  for (const delegation of kept.live) {
    if (await live(tx, { child: delegation.child, task: delegation.task })) {
      still.push({ child: delegation.child, task: delegation.task });
    }
  }
  if (still.length + 1 >= THREAD_CONVERSATIONS) {
    throw new Refused(THREAD_CONVERSATIONS_FULL);
  }
  const made = await tx.createConversation({ ownership: { kind: "task", taskId } });
  if (instructions !== null) {
    const agent = await tx.doc(AgentDoc, made.id);
    delete agent.instructions;
    if (instructions !== undefined) {
      agent.instructions = instructions;
    }
  }
  kept.live = [...still, { child: Number(made.id), task: Number(taskId) }];
  return made.id;
}
