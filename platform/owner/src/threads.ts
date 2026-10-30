/**
 * The agent's threads (agent research §4.1, §8; tasks 5, sub-slices A2, A4 and S3): each
 * thread the object makes for an agent, with the stamp it was made under, the store its
 * Session lives in, what it is for and when it was last active.
 *
 * A thread is one pi Session (`thread-host.ts`) over its own store (`thread-store.ts`),
 * and the thread is that Session's root conversation: every conversation below it (a
 * subagent's, a fork, background work) lives in the same Session and store. A thread is
 * made where its work begins, by the agent's doors (`agentSubmit`, `agentRun` and `ta`'s
 * `agent:run`; scheduled work names one when it is made, `scheduled.ts`): this row first,
 * in a unit of the write gate that re-checks the stamp's standing, naming a fresh store
 * (128 random bits, `store`, unique); then the Session is opened, which makes the store at
 * its first call, and the root, with the thread's stamp written into its `ta.authority`
 * document in the root's creating commit. So a store is always named in a row before it
 * exists, and a crash between leaves a row whose store is empty or not yet made, which the
 * next open makes: no row names a store that cannot be made. A store's name is never
 * reused. A row's stamp and store never change (a trigger); only its activity moves on,
 * and its state from `ready` to `dropping`. It is the object's index of threads, by our id,
 * by caller and by activity: the root's own authority, which every conversation below it
 * inherits, is what every check reads (`authority.ts`).
 *
 * `agent_live` holds each thread's wake as its Session last reported it (`onWake`, kept by
 * the host in a unit): whether it has work running, and when a sleeping task next wakes. It
 * is how retention and the end of the owner's work know a thread is live without opening
 * every store; a row goes with its thread.
 *
 * Two kinds: a `converse` thread is the one conversation an agent holds with one caller
 * (the owner, or an outside client, in one universe), found again by those
 * (`conversing`, a unique index); a `run` thread is made fresh for each launch.
 *
 * Every door addresses work by a thread's id, resolved against the calling client's own
 * rows (`mine`), never by a pi session string: an operation id is the thread's id and
 * pi's request id, and a forged or another caller's resolves to nothing. A row that is
 * `dropping` resolves to nothing either.
 *
 * Bounds: a client's threads hold its grant row (the schema's `held`), as its stamped work
 * does, so the free-row bound never forgets the row they are checked against. A grant
 * change drops its client's threads in the change's own transaction (`endClient`): their
 * stamps no longer stand, so they can do nothing more, and dropping them releases the row.
 * Past `perClient` threads of one client, `clients` of every client's, or `owner` of the
 * owner's, a new one is refused loudly, so the rows (and the grant rows they hold) are
 * bounded however many clients register.
 *
 * Idle retention (the `threads` lane, `retainThreads`): a thread no door, run or scheduled
 * work has touched for `idleS` is dropped, so threads that stopped being used free their
 * bound. A thread is never dropped while it is live (an `agent_live` row: work running, or
 * a task sleeping in its Session): its activity moves on instead; a thread unsettled
 * scheduled work names is never idle, and its activity moves on when the work is claimed
 * (`Scheduled.claim`), so a cancel releases it. A drop re-checks, in its own unit, the
 * activity the pass read and the thread's liveness, so a door that touched the thread
 * since keeps it. A dropped thread's Session is closed, and nothing opens it again: no row
 * names it. Wiping its store is S4's (tasks 5): the row turns `dropping`, the store is
 * destroyed, then the row goes, so no store is forgotten before it is wiped. Until S4, a
 * drop deletes the row and leaves the store unwiped (nothing is deployed).
 */
import { agentId, type AuthorityState, authorityOf, type Stamped, stamped } from "./authority";
import { Refused } from "./errors";
import type { OwnerStorage, Write } from "./gate";

/** What a thread is for: one caller's conversation with an agent, or one run. */
export type ThreadKind = "converse" | "run";

/** One thread as stored. */
export type Thread = {
  readonly thread_id: string;
  /** Its store's name: 128 random bits, as hex, named here before the store exists. */
  readonly store: string;
  readonly state: "ready" | "dropping";
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

function randomHex(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A thread's authority, as its root's `ta.authority` document holds it. */
export function threadAuthority(thread: Thread): AuthorityState {
  return authorityOf(thread.client === null ? null
    : { client: thread.client, generation: thread.generation!,
        authenticated_at: thread.authenticated_at, universe: thread.universe,
        agent: thread.agent_id });
}

/** The SQL condition that what holds a thread (its open work) names the thread in `column`. */
export type HeldBy = (column: string) => string;

/** What one idle thread's retention did. */
export type Retained = "dropped" | "live" | "touched";

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
   * In a unit: record a thread for `agent` under `stamp` (null: the owner's own), whose
   * standing the caller re-checks in the same unit, naming a fresh store; refused past its
   * bound. A `converse` thread is the caller's one with the agent: when one was recorded
   * meanwhile (a concurrent first message), that one is answered, and nothing is made.
   */
  record(agent: unknown, stamp: unknown, now: number, kind: ThreadKind = "run"): Thread {
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
    return this.#storage.sql.exec<Thread>(
      "INSERT INTO agent_threads (thread_id, store, state, agent_id, client, generation, " +
        "authenticated_at, universe, created_at, kind, last_active) " +
        "VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
      `thread_${randomHex(16)}`, randomHex(16), id, by?.client ?? null,
      by?.generation ?? null, by?.authenticated_at ?? null, by?.universe ?? null, now, kind,
      now).one();
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

  /** A `ready` thread by its id, or null. */
  get(threadId: unknown): Thread | null {
    if (typeof threadId !== "string" || !THREAD_ID.test(threadId)) {
      return null;
    }
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads WHERE thread_id = ? AND state = 'ready'", threadId)
      .toArray()[0] ?? null;
  }

  /**
   * The `converse` thread `agent` holds with the caller `by` (null: the owner) where its
   * stamp acts, or null. A read.
   */
  conversing(by: Stamped | null, agent: string): Thread | null {
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads WHERE kind = 'converse' AND IFNULL(client, '') = ? " +
        "AND agent_id = ? AND IFNULL(universe, '') = ? AND state = 'ready'",
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
   * In a unit: keep the thread's wake as its Session reported it, `active` with work
   * running and `wakeAt` (seconds) when a sleeping task next wakes; neither forgets it. A
   * thread with no row keeps nothing.
   */
  live(threadId: string, active: boolean, wakeAt: number | null): void {
    const sql = this.#storage.sql;
    if (!active && wakeAt === null) {
      sql.exec("DELETE FROM agent_live WHERE thread_id = ?", threadId);
      return;
    }
    sql.exec("INSERT INTO agent_live (thread_id, active, wake_at) SELECT thread_id, ?, ? " +
               "FROM agent_threads WHERE thread_id = ? " +
               "ON CONFLICT (thread_id) DO UPDATE SET active = excluded.active, " +
               "wake_at = excluded.wake_at", active ? 1 : 0, wakeAt, threadId);
  }

  /** The threads with work running in their Sessions, as last reported. A read. */
  active(): string[] {
    return this.#storage.sql.exec<{ thread_id: string }>(
      "SELECT thread_id FROM agent_live WHERE active = 1").toArray()
      .map((row) => row.thread_id);
  }

  /** Every live thread: work running, or a task sleeping. A read. */
  liveThreads(): Set<string> {
    return new Set(this.#storage.sql.exec<{ thread_id: string }>(
      "SELECT thread_id FROM agent_live").toArray().map((row) => row.thread_id));
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
   * In a unit: retain one idle thread as the pass read it (`seen`, its activity then). A
   * live one (its `agent_live` row) has its activity moved on to `now`; one a door touched
   * since is kept as it is; any other is dropped.
   */
  retain(threadId: string, seen: number, now: number): Retained {
    const sql = this.#storage.sql;
    const live = sql.exec("SELECT 1 FROM agent_live WHERE thread_id = ?", threadId).toArray();
    if (live.length > 0) {
      this.touch(threadId, now);
      return "live";
    }
    return sql.exec("DELETE FROM agent_threads WHERE thread_id = ? AND last_active <= ? " +
                      "RETURNING thread_id", threadId, seen).toArray().length > 0
      ? "dropped" : "touched";
  }

  /**
   * In the grant change's unit: drop the client's threads, whose stamps the change ended;
   * the threads they were, so their Sessions can be ended once it commits.
   */
  endClient(client: string): Thread[] {
    return this.#storage.sql.exec<Thread>(
      "DELETE FROM agent_threads WHERE client = ? RETURNING *", client).toArray();
  }
}

/**
 * One `threads` lane pass (idle retention): each idle thread, the longest idle first, in a
 * unit of its own: dropped unless it is live, when its activity moves on to now instead,
 * or a door touched it since. A dropped thread's Session is then closed (`closed`). It takes
 * no new thread once the wall clock reaches `until`. How many it dropped.
 */
export async function retainThreads(threads: Threads, clock: () => number, write: Write,
                                    until: number = Infinity,
                                    closed: (thread: string) => Promise<void> = async () => {}):
    Promise<number> {
  let dropped = 0;
  for (const thread of threads.idle(clock())) {
    if (Date.now() >= until) {
      break;  // the rest wait for the lane's next pass, which its job's set brings at once
    }
    const id = thread.thread_id;
    const seen = thread.last_active;
    if (await write(() => threads.retain(id, seen, clock())) === "dropped") {
      dropped++;
      await closed(id);
    }
  }
  return dropped;
}
