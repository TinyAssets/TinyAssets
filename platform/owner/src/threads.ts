/**
 * The agent's threads (agent research §4.1; tasks 5, sub-slice A2): each top-level pi
 * conversation the object makes for an agent, with the stamp it was made under.
 *
 * A thread is made where its work begins (A4's doors; scheduled work names one when it is
 * made, `scheduled.ts`): pi creates the conversation and writes its stamp into the
 * `ta.authority` document in the creating commit, then this row records it, in a unit of
 * the write gate that re-checks the stamp's standing. A row never changes. It is the
 * object's index of threads, by our id and by client: the conversation's own
 * stamp, which every conversation below it inherits, is what the authority gate reads
 * (`authority.ts`). A conversation pi made whose row a crash left unwritten holds no
 * input and no work: nothing names it.
 */
import { agentId, stamped } from "./authority";
import type { OwnerStorage } from "./gate";

/** One thread as stored. */
export type Thread = {
  readonly thread_id: string;
  readonly conversation_id: number;
  readonly agent_id: string;
  readonly client: string | null;
  readonly generation: number | null;
  readonly authenticated_at: number | null;
  readonly universe: string | null;
  readonly created_at: number;
};

/** What scheduled work reads of the threads: one, by its id. */
export interface ThreadReader {
  get(threadId: unknown): Thread | null;
}

const THREAD_ID = /^thread_[0-9a-f]{32}$/;

export class Threads implements ThreadReader {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  /**
   * In a unit: record the thread pi made as `conversationId`, for `agent` under `stamp`
   * (null: the owner's own), whose standing the caller re-checks in the same unit.
   */
  record(conversationId: number, agent: unknown, stamp: unknown, now: number): Thread {
    if (!Number.isSafeInteger(conversationId) || conversationId < 1) {
      throw new Error("a thread is a conversation pi made");
    }
    const id = agentId(agent);
    const by = stamped(stamp);
    if (by !== null && by.agent !== id) {
      throw new Error("a thread's stamp names its own agent");
    }
    const raw = new Uint8Array(16);
    crypto.getRandomValues(raw);
    const threadId = `thread_${Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("")}`;
    return this.#storage.sql.exec<Thread>(
      "INSERT INTO agent_threads (thread_id, conversation_id, agent_id, client, generation, " +
        "authenticated_at, universe, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
      threadId, conversationId, id, by?.client ?? null, by?.generation ?? null,
      by?.authenticated_at ?? null, by?.universe ?? null, now).one();
  }

  get(threadId: unknown): Thread | null {
    if (typeof threadId !== "string" || !THREAD_ID.test(threadId)) {
      return null;
    }
    return this.#storage.sql.exec<Thread>("SELECT * FROM agent_threads WHERE thread_id = ?",
                                          threadId).toArray()[0] ?? null;
  }

  /** A client's threads, oldest first. */
  ofClient(client: string): Thread[] {
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads WHERE client = ? ORDER BY created_at, thread_id", client)
      .toArray();
  }
}
