/**
 * The agent's threads (agent research §4.1, §8; tasks 5, sub-slices A2, A4, S3 and S4-S5):
 * each thread the object makes for an agent, with the stamp it was made under, the store
 * its Session lives in, what it is for and when it was last active.
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
 * inherits, is what every check reads (`authority.ts`). No thread is recorded while the
 * owner is being erased (`erasure.ts`): the recording unit reads the mark itself.
 *
 * `agent_live` holds each thread's wake as its Session last reported it (`onWake`, kept by
 * the host in a unit): whether it has work running, and when a sleeping task next wakes. It
 * is how retention and the end of the owner's work know a thread is live without opening
 * every store; a row goes with its thread. Each change moves the thread's activity on, and
 * so does its work's end (the row's removal), so a run's activity is when it settled.
 *
 * Two kinds: a `converse` thread is the one conversation an agent holds with one caller
 * (the owner, or an outside client, in one universe), found again by those
 * (`conversing`, a unique index); a `run` thread is made fresh for each launch (the
 * `run_graph` door, `ta`'s `agent:run`, and a scheduled launch, whose work names it).
 *
 * Every door addresses work by a thread's id, resolved against the calling client's own
 * rows (`mine`), never by a pi session string: an operation id is the thread's id and
 * pi's request id, and a forged or another caller's resolves to nothing. A row that is
 * `dropping` resolves to nothing either. A run whose store was wiped answers from its
 * receipt (`receipt`), the caller's own alike.
 *
 * Dropping a thread, for every reason (idle retention, a run's retention, a grant change
 * or revoke, the owner's erasure), is one path, each step resumable: (1) in one unit, the
 * row is marked `dropping`, so no door resolves it and nothing opens its Session again;
 * (2) its Session's work is ended and the Session closed (`ThreadHost.drop`); a run's
 * receipt is read from its store and kept (`keepReceipt`), since after the wipe nothing is
 * left to read; (3) its store is destroyed; (4) in one unit, its row is forgotten with
 * every row keyed to it (`forget`: its live and usage rows, by trigger, and its wake job),
 * a run leaving its receipt. The `threads` lane finishes any drop cut short
 * (`dropping`), each in turn, backed off in its row (`drop_retry`) when its store does not
 * answer: `destroy()` is idempotent, so a store is never forgotten before it is wiped.
 *
 * Bounds: a client's ready threads hold its grant row (the schema's `held`), as its stamped
 * work does, so the free-row bound never forgets the row they are checked against. A grant
 * change marks its client's threads dropping in the change's own transaction
 * (`endClient`), which releases the row: their stamps no longer stand, so they can do
 * nothing more; their drops finish after it commits. Past `perClient` threads of one
 * client, `clients` of every client's, `owner` of the owner's conversations or `runs` runs
 * (live stores), a new one is refused loudly, and so is a run once runs and receipts
 * together reach `receipts`, so every run's receipt has room: the rows (and the grant rows
 * they hold) are bounded however many clients register. A dropping row counts until it is
 * forgotten: its store is still there.
 *
 * Retention (the `threads` lane, `retainThreads`): a conversation no door, run or scheduled
 * work has touched for `idleS` is dropped, so threads that stopped being used free their
 * bound; a run's store is wiped `keepRunsS` after it settled (owner policy, `keep_runs`),
 * and its receipt is kept `keepReceiptsS` from then (`keep_run_receipts`). A thread is
 * never marked while it is live (an `agent_live` row: work running, or a task sleeping in
 * its Session): its activity moves on instead; a thread unsettled scheduled work names is
 * never taken, and its activity moves on when the work is claimed (`Scheduled.claim`), so
 * a cancel releases it. A mark re-checks, in its own unit, the activity the pass read and
 * the thread's liveness, so a door that touched the thread since keeps it.
 */
import { agentId, type AuthorityState, authorityOf, type Stamped, stamped } from "./authority";
import { refuseErasing } from "./erasure";
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
  /** A dropping thread's next try, once a drop of it did not finish; else null. */
  readonly drop_retry: number | null;
};

/** How a run settled, as its receipt keeps it once its store is wiped. */
export type Receipt = {
  readonly thread_id: string;
  readonly client: string | null;
  /** pi's request id of the run's input, or null when pi was never handed one. */
  readonly request: string | null;
  readonly status: "done" | "unanswered";
  readonly reason: string | null;
  /** The first `RECEIPT_TEXT_MAX` bytes of its answer's text. */
  readonly text: string;
  /** When the run settled (its thread's last activity). */
  readonly settled_at: number;
};

/** The most bytes a thread's kept usage (`keepUsage`) takes: 64 KiB of JSON. */
export const USAGE_ROW_MAX = 64 * 1024;
/** The most bytes every thread's kept usage takes together: 256 MiB. */
export const USAGE_SPACE_MAX = 256 * 1024 * 1024;
/** A receipt's text: the first 4 KiB of the run's answer. */
export const RECEIPT_TEXT_MAX = 4096;
/** A receipt's reason, at most, in characters. */
export const RECEIPT_REASON_MAX = 256;
/** How long one drop's outside calls may take in a lane pass before it is tried later. */
export const DROP_MS = 10_000;
/** How long a drop that did not finish waits before the lane tries it again. */
export const DROP_RETRY_S = 60;
/** Receipts one pass lets go. */
export const RECEIPTS_PER_PASS = 1_000;

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
  /** The owner's own conversations. */
  readonly owner: number;
  /** Run threads, every caller's, whose stores are kept (`runs_kept`). */
  readonly runs: number;
  /** Run threads and receipts together: every run's receipt has room. */
  readonly receipts: number;
}

export const THREAD_LIMITS: ThreadLimits = Object.freeze({ perClient: 64, clients: 1_024,
                                                           owner: 10_000, runs: 50_000,
                                                           receipts: 100_000 });

/** Idle retention: how long a conversation may go untouched, and how many one pass takes. */
export interface ThreadRetention {
  readonly idleS: number;
  readonly perPass: number;
}

export const THREAD_RETENTION: ThreadRetention = Object.freeze({ idleS: 7 * 86_400,
                                                                 perPass: 32 });

/** Run retention (owner policy, `runs`): a settled run's store, and its receipt. */
export interface RunRetention {
  /** `keep_runs`: how long a settled run's store is kept. */
  readonly keepRunsS: number;
  /** `keep_run_receipts`: how long its receipt is kept, from when it settled. */
  readonly keepReceiptsS: number;
}

/** Run retention at the policy's defaults (`RUN_DEFAULTS`). */
export const RUN_RETENTION: RunRetention = Object.freeze({ keepRunsS: 7 * 86_400,
                                                           keepReceiptsS: 90 * 86_400 });

const THREAD_ID = /^thread_[0-9a-f]{32}$/;

function randomHex(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** `text`'s first `max` bytes of UTF-8, cut at a character's end. */
export function utf8Prefix(text: string, max: number): string {
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= max) {
    return text;
  }
  let end = max;
  while (end > 0 && (bytes[end]! & 0xc0) === 0x80) {
    end--;
  }
  return new TextDecoder().decode(bytes.subarray(0, end));
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

/** What one retained thread's mark did. */
export type Retained = "dropped" | "live" | "touched";

export class Threads implements ThreadReader {
  readonly #storage: OwnerStorage;
  readonly #limits: ThreadLimits;
  readonly #retention: ThreadRetention;
  readonly #runs: () => RunRetention;
  /** A thread retention may take: nothing holds it (`held`, the scheduled work's). */
  readonly #unheld: string;

  /**
   * `held` is the scheduled work's condition (`namedByOpenWork`, `scheduled.ts`), which the
   * object hands in; with none, nothing holds a thread (a test's threads alone). `runs` is
   * read at each use: the object's reads owner policy.
   */
  constructor(storage: OwnerStorage, limits: ThreadLimits = THREAD_LIMITS,
              retention: ThreadRetention = THREAD_RETENTION, held?: HeldBy,
              runs: () => RunRetention = () => RUN_RETENTION) {
    this.#storage = storage;
    this.#limits = limits;
    this.#retention = retention;
    this.#runs = runs;
    this.#unheld = held === undefined ? "1" : `NOT ${held("agent_threads.thread_id")}`;
  }

  /**
   * In a unit: record a thread for `agent` under `stamp` (null: the owner's own), whose
   * standing the caller re-checks in the same unit, naming a fresh store; refused past its
   * bound, and while the owner is being erased. A `converse` thread is the caller's one with
   * the agent: when one was recorded meanwhile (a concurrent first message), that one is
   * answered, and nothing is made.
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
    refuseErasing(this.#storage);
    if (kind === "converse") {
      const held = this.conversing(by, id);
      if (held !== null) {
        return held;
      }
    }
    this.room(by, kind);
    return this.#storage.sql.exec<Thread>(
      "INSERT INTO agent_threads (thread_id, store, state, agent_id, client, generation, " +
        "authenticated_at, universe, created_at, kind, last_active) " +
        "VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
      `thread_${randomHex(16)}`, randomHex(16), id, by?.client ?? null,
      by?.generation ?? null, by?.authenticated_at ?? null, by?.universe ?? null, now, kind,
      now).one();
  }

  /**
   * Refuse loudly a new `kind` thread under `by` (null: the owner's) past its bounds; a
   * read. Every row counts, a dropping one too: its store is not wiped yet.
   */
  room(by: Stamped | null, kind: ThreadKind = "run"): void {
    const sql = this.#storage.sql;
    const count = (where: string, ...bound: (string | null)[]) => sql.exec<{ n: number }>(
      `SELECT COUNT(*) AS n FROM agent_threads WHERE ${where}`, ...bound).one().n;
    if (kind === "run") {
      const runs = count("kind = 'run'");
      if (runs >= this.#limits.runs) {
        throw new Refused("the agent's runs are at their limit");
      }
      const receipts = sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM run_receipts")
        .one().n;
      if (runs + receipts >= this.#limits.receipts) {
        throw new Refused("the runs' receipts are at their limit");
      }
    }
    if (by === null) {
      if (kind === "converse"
          && count("kind = 'converse' AND client IS ?", null) >= this.#limits.owner) {
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

  /** A thread by its id, whatever its state (a drop's own read), or null. */
  row(threadId: string): Thread | null {
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads WHERE thread_id = ?", threadId).toArray()[0] ?? null;
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

  /** The caller's own run receipt `threadId` names, or null, alike for any other. A read. */
  receipt(threadId: unknown, client: string | null): Receipt | null {
    if (typeof threadId !== "string" || !THREAD_ID.test(threadId)) {
      return null;
    }
    return this.#storage.sql.exec<Receipt>(
      "SELECT thread_id, client, request, status, reason, text, settled_at FROM " +
        "run_receipts WHERE thread_id = ? AND client IS ?", threadId, client)
      .toArray()[0] ?? null;
  }

  touch(threadId: string, at: number): boolean {
    return this.#storage.sql.exec(
      "UPDATE agent_threads SET last_active = MAX(last_active, ?) WHERE thread_id = ? " +
        "RETURNING thread_id", at, threadId).toArray().length > 0;
  }

  /**
   * In a unit: keep the thread's wake as its Session reported it at `now`, `active` with
   * work running and `wakeAt` (seconds) when a sleeping task next wakes; neither forgets
   * it. For a run, each change, and the forgetting of a kept wake (its work ended), moves
   * its activity on to `now`, so a run's activity is when it settled; a conversation's is
   * its callers' (`touch`). A thread with no row keeps nothing.
   */
  live(threadId: string, active: boolean, wakeAt: number | null, now: number): void {
    const sql = this.#storage.sql;
    if (!active && wakeAt === null) {
      if (sql.exec("DELETE FROM agent_live WHERE thread_id = ? RETURNING thread_id", threadId)
            .toArray().length > 0) {
        this.#settling(threadId, now);
      }
      return;
    }
    if (sql.exec("INSERT INTO agent_live (thread_id, active, wake_at) SELECT thread_id, ?, ? " +
                   "FROM agent_threads WHERE thread_id = ? " +
                   "ON CONFLICT (thread_id) DO UPDATE SET active = excluded.active, " +
                   "wake_at = excluded.wake_at RETURNING thread_id", active ? 1 : 0, wakeAt,
                 threadId).toArray().length > 0) {
      this.#settling(threadId, now);
    }
  }

  /** A run's work moved at `now`: its activity, which its retention reads, moves on. */
  #settling(threadId: string, now: number): void {
    this.#storage.sql.exec("UPDATE agent_threads SET last_active = MAX(last_active, ?) " +
                             "WHERE thread_id = ? AND kind = 'run'", now, threadId);
  }

  /**
   * In a unit: keep the thread's usage (pi's `pi.usage`, as JSON) as its Session closed,
   * over what was kept. Refused past `USAGE_ROW_MAX`, or past `USAGE_SPACE_MAX` for every
   * thread's together; a thread with no row keeps nothing.
   */
  keepUsage(threadId: string, usage: string): void {
    const bytes = new TextEncoder().encode(usage).length;
    if (bytes > USAGE_ROW_MAX) {
      throw new Error("a thread's usage is past its bound");
    }
    const sql = this.#storage.sql;
    const space = sql.exec<{ bytes: number }>("SELECT bytes FROM agent_usage_space").one().bytes;
    const was = sql.exec<{ bytes: number }>(
      "SELECT length(CAST(usage AS BLOB)) AS bytes FROM agent_usage WHERE thread_id = ?",
      threadId).toArray()[0]?.bytes ?? 0;
    if (space - was + bytes > USAGE_SPACE_MAX) {
      throw new Error("the threads' kept usage is past its bound");
    }
    sql.exec(
      "INSERT INTO agent_usage (thread_id, usage) SELECT thread_id, ? FROM agent_threads " +
        "WHERE thread_id = ? ON CONFLICT (thread_id) DO UPDATE SET usage = excluded.usage",
      usage, threadId);
  }

  /**
   * Each thread's usage as its Session last closed, as JSON: a dropping thread's too, until
   * its row goes, and its usage with it. A read.
   */
  keptUsage(): { thread_id: string; usage: string }[] {
    return this.#storage.sql.exec<{ thread_id: string; usage: string }>(
      "SELECT thread_id, usage FROM agent_usage").toArray();
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

  /** The oldest activity of a ready `kind` thread no unsettled work names, or undefined. */
  #oldest(kind: ThreadKind): number | undefined {
    return this.#storage.sql.exec<{ at: number }>(
      `SELECT last_active AS at FROM agent_threads WHERE kind = ? AND state = 'ready' AND ${
        this.#unheld} ORDER BY last_active LIMIT 1`, kind).toArray()[0]?.at;
  }

  /**
   * When the longest idle conversation no unsettled work names reaches its idle age, never
   * before `now`; null for none. A read, by the kind's activity index.
   */
  nextIdle(now: number): number | null {
    const oldest = this.#oldest("converse");
    return oldest === undefined ? null : Math.max(oldest + this.#retention.idleS, now);
  }

  /**
   * When the lane next has work: a drop to finish, a conversation's idle age, a run's
   * `keep_runs` or a receipt's `keep_run_receipts`, the earliest, never before `now`; null
   * for none. A read.
   */
  nextDue(now: number): number | null {
    const sql = this.#storage.sql;
    const { keepRunsS, keepReceiptsS } = this.#runs();
    const times: number[] = [];
    const retry = sql.exec<{ at: number | null }>(
      "SELECT MIN(IFNULL(drop_retry, 0)) AS at FROM agent_threads WHERE state = 'dropping'")
      .one().at;
    if (retry !== null) {
      times.push(retry);
    }
    const idle = this.nextIdle(now);
    if (idle !== null) {
      times.push(idle);
    }
    const run = this.#oldest("run");
    if (run !== undefined) {
      times.push(run + keepRunsS);
    }
    const receipt = sql.exec<{ at: number | null }>(
      "SELECT MIN(settled_at) AS at FROM run_receipts").one().at;
    if (receipt !== null) {
      times.push(receipt + keepReceiptsS);
    }
    return times.length === 0 ? null : Math.max(Math.min(...times), now);
  }

  /**
   * The conversations idle at `now` that no unsettled work names, the longest idle first,
   * at most `perPass`. A read.
   */
  idle(now: number): Thread[] {
    return this.#storage.sql.exec<Thread>(
      `SELECT * FROM agent_threads WHERE kind = 'converse' AND state = 'ready' AND ` +
        `last_active <= ? AND ${this.#unheld} ORDER BY last_active LIMIT ?`,
      now - this.#retention.idleS, this.#retention.perPass).toArray();
  }

  /**
   * The runs settled `keep_runs` ago at `now` that no unsettled work names, the longest
   * settled first, at most `perPass`. A read; a live one among them is kept by its mark.
   */
  settledRuns(now: number): Thread[] {
    return this.#storage.sql.exec<Thread>(
      `SELECT * FROM agent_threads WHERE kind = 'run' AND state = 'ready' AND ` +
        `last_active <= ? AND ${this.#unheld} ORDER BY last_active LIMIT ?`,
      now - this.#runs().keepRunsS, this.#retention.perPass).toArray();
  }

  /** The dropping threads due to be tried again at `now`, at most `perPass`. A read. */
  dropping(now: number): Thread[] {
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads WHERE state = 'dropping' AND IFNULL(drop_retry, 0) <= ? " +
        "ORDER BY drop_retry LIMIT ?", now, this.#retention.perPass).toArray();
  }

  /**
   * In a unit: retain one thread as the pass read it (`seen`, its activity then). A live
   * one (its `agent_live` row) has its activity moved on to `now`; one a door touched since
   * is kept as it is; any other is marked `dropping`, the first step of its drop.
   */
  retain(threadId: string, seen: number, now: number): Retained {
    const sql = this.#storage.sql;
    const live = sql.exec("SELECT 1 FROM agent_live WHERE thread_id = ?", threadId).toArray();
    if (live.length > 0) {
      this.touch(threadId, now);
      return "live";
    }
    return sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE thread_id = ? " +
                      "AND state = 'ready' AND last_active <= ? RETURNING thread_id",
                    threadId, seen).toArray().length > 0
      ? "dropped" : "touched";
  }

  /**
   * In the grant change's unit: mark the client's threads, whose stamps the change ended,
   * `dropping`, which releases its grant row; every dropping thread of the client, so their
   * drops can finish once it commits.
   */
  endClient(client: string): Thread[] {
    const sql = this.#storage.sql;
    sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE client = ? AND state = 'ready'",
             client);
    return sql.exec<Thread>("SELECT * FROM agent_threads WHERE client = ? AND " +
                              "state = 'dropping'", client).toArray();
  }

  /** In a unit: a drop of `threadId` that did not finish is tried again at `at`. */
  retry(threadId: string, at: number): void {
    this.#storage.sql.exec("UPDATE agent_threads SET drop_retry = ? WHERE thread_id = ? " +
                             "AND state = 'dropping'", at, threadId);
  }

  /** Whether `threadId` keeps a receipt. A read. */
  hasReceipt(threadId: string): boolean {
    return this.#storage.sql.exec("SELECT 1 FROM run_receipts WHERE thread_id = ?",
                                  threadId).toArray().length > 0;
  }

  /**
   * In a unit: keep a dropping run's receipt, as its store said it settled, by the run's
   * settling time (its last activity); a second keep keeps the first. Its room was kept
   * when the run was made (`room`): one past the bound is a bug, refused loudly.
   */
  keepReceipt(thread: Thread,
              settled: Pick<Receipt, "request" | "status" | "reason" | "text">): void {
    const sql = this.#storage.sql;
    const row = this.row(thread.thread_id);
    if (row === null || row.state !== "dropping" || row.kind !== "run") {
      throw new Error("a receipt is a dropping run's");
    }
    if (this.hasReceipt(row.thread_id)) {
      return;
    }
    if (sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM run_receipts").one().n
        >= this.#limits.receipts) {
      throw new Error("the runs' receipts are past their bound");
    }
    sql.exec("INSERT INTO run_receipts (thread_id, client, request, status, reason, text, " +
               "settled_at) VALUES (?, ?, ?, ?, ?, ?, ?)", row.thread_id, row.client,
             settled.request, settled.status,
             settled.reason === null ? null
               : [...settled.reason].slice(0, RECEIPT_REASON_MAX).join(""),
             utf8Prefix(settled.text, RECEIPT_TEXT_MAX), row.last_active);
  }

  /**
   * In a unit, the drop's last step, once its store was destroyed: forget the dropping
   * thread's row, and with it (by trigger) its live and usage rows; a run's receipt stays.
   * Whether there was one to forget.
   */
  forget(threadId: string): boolean {
    return this.#storage.sql.exec("DELETE FROM agent_threads WHERE thread_id = ? AND " +
                                    "state = 'dropping' RETURNING thread_id", threadId)
      .toArray().length > 0;
  }

  /** In the erasure's marking unit (`erasure.ts`): every thread marked `dropping`. */
  dropAll(): void {
    this.#storage.sql.exec("UPDATE agent_threads SET state = 'dropping' WHERE state = 'ready'");
  }

  /** Up to `limit` threads still named by a row, whatever their state (the erasure's). */
  remaining(limit: number): Thread[] {
    return this.#storage.sql.exec<Thread>(
      "SELECT * FROM agent_threads ORDER BY thread_id LIMIT ?", limit).toArray();
  }

  /** In a unit: let go of the receipts kept `keep_run_receipts` at `now`, at most a batch. */
  expireReceipts(now: number): number {
    return this.#storage.sql.exec(
      "DELETE FROM run_receipts WHERE thread_id IN (SELECT thread_id FROM run_receipts " +
        "WHERE settled_at <= ? ORDER BY settled_at LIMIT ?) RETURNING thread_id",
      now - this.#runs().keepReceiptsS, RECEIPTS_PER_PASS).toArray().length;
  }
}

/** `run`'s outcome within `ms`: whether it finished, failed, or is still going. */
async function within(run: Promise<void>, ms: number): Promise<"done" | "failed" | "late"> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<"late">((resolve) => {
    timer = setTimeout(() => resolve("late"), ms);
  });
  try {
    return await Promise.race([run.then(() => "done" as const, () => "failed" as const), late]);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * One `threads` lane pass: the receipts past `keep_run_receipts` let go; each drop cut short
 * finished (`drop`, the object's one drop path), the longest waiting first; then each idle
 * conversation and each run past `keep_runs`, the longest first, marked in a unit of its own
 * (unless it is live, when its activity moves on to now instead, or a door touched it since)
 * and dropped. A drop whose outside calls fail, or take past `DROP_MS`, is tried again
 * `DROP_RETRY_S` later, by its row; it never fails the pass. It takes no new thread once
 * the wall clock reaches `until`. How many threads it marked.
 */
export async function retainThreads(threads: Threads, clock: () => number, write: Write,
                                    drop: (thread: Thread) => Promise<void>,
                                    until: number = Infinity): Promise<number> {
  const finish = async (thread: Thread) => {
    const outcome = await within(drop(thread), DROP_MS);
    if (outcome !== "done") {
      console.error("a thread's drop did not finish; it is tried again");  // no ids
      await write(() => threads.retry(thread.thread_id, clock() + DROP_RETRY_S));
    }
  };
  await write(() => threads.expireReceipts(clock()));
  for (const thread of threads.dropping(clock())) {
    if (Date.now() >= until) {
      return 0;
    }
    await finish(thread);
  }
  let dropped = 0;
  const now = clock();
  for (const thread of [...threads.idle(now), ...threads.settledRuns(now)]) {
    if (Date.now() >= until) {
      break;  // the rest wait for the lane's next pass, which its job's set brings at once
    }
    const id = thread.thread_id;
    const seen = thread.last_active;
    if (await write(() => threads.retain(id, seen, clock())) === "dropped") {
      dropped++;
      await finish(threads.row(id)!);
    }
  }
  return dropped;
}
