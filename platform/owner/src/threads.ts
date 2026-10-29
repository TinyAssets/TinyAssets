/**
 * The agent's threads (agent research §4.1; tasks 5, sub-slice A2): each top-level pi
 * conversation the object makes for an agent, with the stamp it was made under.
 *
 * A thread is made where its work begins (A4's doors; scheduled work names one when it is
 * made, `scheduled.ts`): pi creates the conversation and writes its authority into the
 * `ta.authority` document in the creating commit, then this row records it, in a unit of
 * the write gate that re-checks the stamp's standing. A row never changes. It is the
 * object's index of threads, by our id and by client: the conversation's own authority,
 * which every conversation below it inherits, is what every check reads
 * (`authority.ts`). A conversation pi made whose row a crash left unwritten holds no
 * input and no work: nothing names it.
 *
 * Bounds: a client's threads hold its grant row (the schema's `held`), as its stamped work
 * does, so the free-row bound never forgets the row they are checked against. A grant
 * change drops its client's threads in the change's own transaction (`endClient`): their
 * stamps no longer stand, so they can do nothing more, and dropping them releases the row.
 * Past `perClient` threads of one client, `clients` of every client's, or `owner` of the
 * owner's, a new one is refused loudly, so the rows (and the grant rows they hold) are
 * bounded however many clients register. A thread is never the root conversation, the
 * owner's own (`conversation_id > 1`, by schema). Idle-age retention, which also removes
 * the pi conversations a drop leaves behind, lands with A4's doors.
 */
import { agentId, type AuthorityState, authorityOf, type Stamped, stamped } from "./authority";
import { Refused } from "./errors";
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

export interface ThreadLimits {
  /** Threads one outside client may hold at once. */
  readonly perClient: number;
  /** Threads every outside client together may hold at once. */
  readonly clients: number;
  /** The owner's own threads. */
  readonly owner: number;
}

export const THREAD_LIMITS: ThreadLimits = Object.freeze({ perClient: 64, clients: 1_024,
                                                           owner: 10_000 });

const THREAD_ID = /^thread_[0-9a-f]{32}$/;

/** A thread's authority, as its conversation's `ta.authority` document holds it. */
export function threadAuthority(thread: Thread): AuthorityState {
  return authorityOf(thread.client === null ? null
    : { client: thread.client, generation: thread.generation!,
        authenticated_at: thread.authenticated_at, universe: thread.universe,
        agent: thread.agent_id });
}

export class Threads implements ThreadReader {
  readonly #storage: OwnerStorage;
  readonly #limits: ThreadLimits;

  constructor(storage: OwnerStorage, limits: ThreadLimits = THREAD_LIMITS) {
    this.#storage = storage;
    this.#limits = limits;
  }

  /**
   * In a unit: record the thread pi made as `conversationId`, for `agent` under `stamp`
   * (null: the owner's own), whose standing the caller re-checks in the same unit; refused
   * past its bound.
   */
  record(conversationId: number, agent: unknown, stamp: unknown, now: number): Thread {
    if (!Number.isSafeInteger(conversationId) || conversationId <= 1) {
      throw new Error("a thread is a conversation pi made, never the root");
    }
    const id = agentId(agent);
    const by = stamped(stamp);
    if (by !== null && by.agent !== id) {
      throw new Error("a thread's stamp names its own agent");
    }
    this.room(by);
    const sql = this.#storage.sql;
    const raw = new Uint8Array(16);
    crypto.getRandomValues(raw);
    const threadId = `thread_${Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("")}`;
    return sql.exec<Thread>(
      "INSERT INTO agent_threads (thread_id, conversation_id, agent_id, client, generation, " +
        "authenticated_at, universe, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
      threadId, conversationId, id, by?.client ?? null, by?.generation ?? null,
      by?.authenticated_at ?? null, by?.universe ?? null, now).one();
  }

  /** Refuse loudly a new thread under `by` (null: the owner's) past its bounds; a read. */
  room(by: Stamped | null): void {
    const sql = this.#storage.sql;
    const count = (where: string, ...bound: (string | null)[]) => sql.exec<{ n: number }>(
      `SELECT COUNT(*) AS n FROM agent_threads WHERE ${where}`, ...bound).one().n;
    if (by === null) {
      if (count("client IS ?", null) >= this.#limits.owner) {
        throw new Refused("the owner's threads are at their limit");
      }
      return;
    }
    if (count("client IS ?", by.client) >= this.#limits.perClient) {
      throw new Refused("this client's threads are at their limit");
    }
    if (count("client IS NOT NULL") >= this.#limits.clients) {
      throw new Refused("the outside clients' threads are at their limit");
    }
  }

  get(threadId: unknown): Thread | null {
    if (typeof threadId !== "string" || !THREAD_ID.test(threadId)) {
      return null;
    }
    return this.#storage.sql.exec<Thread>("SELECT * FROM agent_threads WHERE thread_id = ?",
                                          threadId).toArray()[0] ?? null;
  }

  /**
   * In the grant change's unit: drop the client's threads, whose stamps the change ended;
   * the conversations they were, so their live views can be closed once it commits.
   */
  endClient(client: string): number[] {
    return this.#storage.sql.exec<{ conversation_id: number }>(
      "DELETE FROM agent_threads WHERE client = ? RETURNING conversation_id", client).toArray()
      .map((row) => row.conversation_id);
  }
}
