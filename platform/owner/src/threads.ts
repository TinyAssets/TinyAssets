/**
 * The agent's threads (agent research §4.1, §8; tasks 5, sub-slices A2 and A4): each
 * top-level pi conversation the object makes for an agent, with the stamp it was made
 * under, what it is for and when it was last active.
 *
 * A thread is made where its work begins, by the agent's doors (`agentSubmit`, `agentRun`
 * and `ta`'s `agent:run`; scheduled work names one when it is made, `scheduled.ts`): pi
 * creates the conversation and writes its authority into the `ta.authority` document in
 * the creating commit, then this row records it, in a unit of the write gate that
 * re-checks the stamp's standing. A row's stamp never changes (a trigger); only its
 * activity moves on. It is the object's index of threads, by our id, by caller and by
 * activity: the conversation's own authority, which every conversation below it
 * inherits, is what every check reads (`authority.ts`). A conversation pi made whose row
 * a crash left unwritten holds no input and no work: nothing names it.
 *
 * Two kinds: a `converse` thread is the one conversation an agent holds with one caller
 * (the owner, or an outside client, in one universe), found again by those
 * (`conversing`, a unique index); a `run` thread is made fresh for each launch.
 *
 * Every door addresses work by a thread's id, resolved against the calling client's own
 * rows (`mine`), never by a pi session string: an operation id is the thread's id and
 * pi's request id, and a forged or another caller's resolves to nothing. No door hands
 * pi a session it did not take from a row, so no door can reach the root conversation,
 * the owner's own, which a submit without a session would.
 *
 * Bounds: a client's threads hold its grant row (the schema's `held`), as its stamped work
 * does, so the free-row bound never forgets the row they are checked against. A grant
 * change drops its client's threads in the change's own transaction (`endClient`): their
 * stamps no longer stand, so they can do nothing more, and dropping them releases the row.
 * Past `perClient` threads of one client, `clients` of every client's, or `owner` of the
 * owner's, a new one is refused loudly, so the rows (and the grant rows they hold) are
 * bounded however many clients register. A thread is never the root conversation, the
 * owner's own (`conversation_id > 1`, by schema).
 *
 * Idle retention (the `threads` lane, `retainThreads`): a thread no door, run or scheduled
 * work has touched for `idleS` is dropped, so threads that stopped being used free their
 * bound (16 full clients at 64 threads each no longer hold the shared 1,024 for good). A
 * thread is never dropped while it is live, pi holding live work in its conversation, as
 * pi does while any conversation below it works (`OwnerAgent.liveThreads`): its activity
 * moves on instead; a thread unsettled scheduled work names is never idle, and its
 * activity moves on when the work is claimed (`Scheduled.claim`), so a cancel releases
 * it. A drop
 * re-checks, in its own unit, the activity the pass read, so a door that touched the
 * thread since keeps it. Declared difference from the research: pi-durable 1.4.0 and
 * PiHarness 0.31.0 have no way to delete a conversation (no delete on `Harness`,
 * `Session`, `Storage` or `PiSessions`; pi's SQLite store deletes only document
 * revisions), so a dropped thread's conversation stays in pi's tables, holding no live
 * work and named by no row, door or work, until pi offers a delete (open in tasks 5).
 */
import { agentId, type AuthorityState, authorityOf, type Stamped, stamped } from "./authority";
import { Refused } from "./errors";
import type { OwnerStorage, Write } from "./gate";

/** What a thread is for: one caller's conversation with an agent, or one run. */
export type ThreadKind = "converse" | "run";

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
  readonly kind: ThreadKind;
  readonly last_active: number;
};

/** What scheduled work reads of the threads: one, by its id; and moves on its activity. */
export interface ThreadReader {
  get(threadId: unknown): Thread | null;
  /** In a unit: the thread is active until at least `at`; whether it is still there. */
  touch(threadId: string, at: number): boolean;
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

/** Idle retention: how long a thread may go untouched, and how many one pass takes. */
export interface ThreadRetention {
  readonly idleS: number;
  readonly perPass: number;
}

export const THREAD_RETENTION: ThreadRetention = Object.freeze({ idleS: 7 * 86_400,
                                                                 perPass: 32 });

const THREAD_ID = /^thread_[0-9a-f]{32}$/;


/** A thread's authority, as its conversation's `ta.authority` document holds it. */
export function threadAuthority(thread: Thread): AuthorityState {
  return authorityOf(thread.client === null ? null
    : { client: thread.client, generation: thread.generation!,
        authenticated_at: thread.authenticated_at, universe: thread.universe,
        agent: thread.agent_id });
}

/** The SQL condition that what holds a thread (its open work) names the thread in `column`. */
export type HeldBy = (column: string) => string;

export class Threads implements ThreadReader {
  readonly #storage: OwnerStorage;
  readonly #limits: ThreadLimits;
  readonly #retention: ThreadRetention;
  /** A thread idle retention may take: nothing holds it (`held`, the scheduled work's). */
  readonly #unheld: string;

  /**
   * `held` is the scheduled work's condition (`namedByOpenWork`, `scheduled.ts`), which the
   * object hands in; with none, nothing holds a thread (a test's threads alone).
   */
  constructor(storage: OwnerStorage, limits: ThreadLimits = THREAD_LIMITS,
              retention: ThreadRetention = THREAD_RETENTION, held?: HeldBy) {
    this.#storage = storage;
    this.#limits = limits;
    this.#retention = retention;
    this.#unheld = held === undefined ? "1" : `NOT ${held("agent_threads.thread_id")}`;
  }

  /**
   * In a unit: record the thread pi made as `conversationId`, for `agent` under `stamp`
   * (null: the owner's own), whose standing the caller re-checks in the same unit; refused
   * past its bound. A `converse` thread is the caller's one with the agent: when one was
   * recorded meanwhile (a concurrent first message), that one is answered, and the
   * conversation pi made for this call is left holding nothing.
   */
  record(conversationId: number, agent: unknown, stamp: unknown, now: number,
         kind: ThreadKind = "run"): Thread {
    if (!Number.isSafeInteger(conversationId) || conversationId <= 1) {
      throw new Error("a thread is a conversation pi made, never the root");
    }
    const id = agentId(agent);
    const by = stamped(stamp);
    if (by !== null && by.agent !== id) {
      throw new Error("a thread's stamp names its own agent");
    }
    if (kind !== "converse" && kind !== "run") {
      throw new Error("a thread is a caller's conversation or a run");
    }
    if (kind === "converse") {
      const held = this.conversing(by, id);
      if (held !== null) {
        return held;
      }
    }
    this.room(by);
    const sql = this.#storage.sql;
    const raw = new Uint8Array(16);
    crypto.getRandomValues(raw);
    const threadId = `thread_${Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("")}`;
    return sql.exec<Thread>(
      "INSERT INTO agent_threads (thread_id, conversation_id, agent_id, client, generation, " +
        "authenticated_at, universe, created_at, kind, last_active) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
      threadId, conversationId, id, by?.client ?? null, by?.generation ?? null,
      by?.authenticated_at ?? null, by?.universe ?? null, now, kind, now).one();
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
   * The `converse` thread `agent` holds with the caller `by` (null: the owner) where its
   * stamp acts, or null. A read.
   */
  conversing(by: Stamped | null, agent: string): Thread | null {
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads WHERE kind = 'converse' AND IFNULL(client, '') = ? " +
        "AND agent_id = ? AND IFNULL(universe, '') = ?",
      by?.client ?? "", agent, by?.universe ?? "").toArray()[0] ?? null;
  }

  /**
   * The thread `threadId` names when it is the caller's own: the owner's (`client` null)
   * or that client's. Anything else, another caller's, a forged id or a pi session string,
   * is null, alike. A read.
   */
  mine(threadId: unknown, client: string | null): Thread | null {
    const thread = this.get(threadId);
    return thread !== null && thread.client === client ? thread : null;
  }

  touch(threadId: string, at: number): boolean {
    return this.#storage.sql.exec(
      "UPDATE agent_threads SET last_active = MAX(last_active, ?) WHERE thread_id = ? " +
        "RETURNING thread_id", at, threadId).toArray().length > 0;
  }

  /**
   * When the longest idle thread no unsettled work names reaches its idle age, never before
   * `now`; null for none. A read, by the activity index and the open work's thread index.
   */
  nextIdle(now: number): number | null {
    const oldest = this.#storage.sql.exec<{ at: number }>(
      `SELECT last_active AS at FROM agent_threads WHERE ${this.#unheld} ORDER BY last_active ` +
        "LIMIT 1").toArray()[0]?.at;
    return oldest === undefined ? null : Math.max(oldest + this.#retention.idleS, now);
  }

  /**
   * The threads idle at `now` that no unsettled work names, the longest idle first, at most
   * `perPass`. A read.
   */
  idle(now: number): Thread[] {
    return this.#storage.sql.exec<Thread>(
      `SELECT * FROM agent_threads WHERE last_active <= ? AND ${this.#unheld} ` +
        "ORDER BY last_active LIMIT ?",
      now - this.#retention.idleS, this.#retention.perPass).toArray();
  }

  /**
   * In a unit: drop the thread when it is still as idle as `seen`, its activity as the
   * pass read it; the conversation it was, or null when a door touched it since.
   */
  drop(threadId: string, seen: number): number | null {
    return this.#storage.sql.exec<{ conversation_id: number }>(
      "DELETE FROM agent_threads WHERE thread_id = ? AND last_active <= ? " +
        "RETURNING conversation_id", threadId, seen).toArray()[0]?.conversation_id ?? null;
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

/**
 * One `threads` lane pass (idle retention): each idle thread, the longest idle first, is
 * dropped in a unit of its own, unless pi holds live work in its conversation (`live`),
 * when its activity moves on to now instead. It takes no new thread once the wall clock reaches `until`. How many it dropped.
 */
export async function retainThreads(threads: Threads,
                                    live: () => Promise<ReadonlySet<number>>,
                                    clock: () => number, write: Write,
                                    until: number = Infinity): Promise<number> {
  const idle = threads.idle(clock());
  if (idle.length === 0) {
    return 0;
  }
  const busy = await live();
  let dropped = 0;
  for (const thread of idle) {
    if (Date.now() >= until) {
      break;  // the rest wait for the lane's next pass, which its job's set brings at once
    }
    if (busy.has(thread.conversation_id)) {
      await write(() => threads.touch(thread.thread_id, clock()));
    } else if (await write(() => threads.drop(thread.thread_id, thread.last_active)) !== null) {
      dropped++;
    }
  }
  return dropped;
}
