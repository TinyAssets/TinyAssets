/**
 * Stamped scheduled work: the `work` lane of the owner's one schedule (tasks 3.6).
 *
 * A scheduled turn or automation is durable work the object runs later on someone's
 * authority. The owner's own carries none; one made through an outside client carries a
 * stamp of that client's authority as it was admitted (`Stamp`: the client, its grant's
 * generation and when its credential was signed in), and the Python's
 * `outside_standing` rule holds: it is rechecked with `Grants.standing` when it is made
 * and again before it runs, so a changed, revoked or fenced grant refuses it. A grant
 * change refuses the client's pending work in its own transaction (`endClient`), as it
 * ends the client's agents' wakes. Open work holds its client's grant row (the schema's
 * `held`, as an agent holds it), so the free-row bound never forgets that row and turns a
 * standing grant into "no grant" (slice 3's review N3).
 *
 * The lane only hands work off; it never runs a turn (slice 5's review L1). Its pass is
 * inline in Lifecycle's drive loop, which awaits each job in turn under one alarm's
 * 15-minute wall, beside PiHarness's wake job, so a runner (the agent's, tasks 5) marks
 * the row, hands the turn to pi's durable submit and returns. pi runs it, and its status
 * is pi's receipts; the row says only `handed`. A hand-off is fenced as a wake's send is
 * (`wakes.ts`): a claim takes a `WORK_LEASE_S` lease, which bounds the hand-off alone; the
 * runner calls `mark()` before the work can have any effect, and stops if the mark
 * fails (the claim lapsed); a claim abandoned past its lease is `unknown` if it was
 * marked, never handed again, or else due again, at most `WORK_LIMITS.attempts` times. A
 * runner that holds its hand-off past `WORK_HANDOFF_MS` is reported, its row marked as
 * possibly handed and left to its lease, and the pass moves on. Until the agent
 * constructs a {@link WorkRunner}, pending work is held and gives the lane no job.
 *
 * A pass contains each row's failure in that row: a row whose step fails in process is
 * backed off `WORK_RETRY_S` in its own due time, so it neither stops the others nor
 * loops the alarm, and the pass throws only when that backoff cannot be written.
 *
 * The owner-wide bounds read counters the schema's triggers keep
 * (`policy_usage.open_work`, `settled_work`); a client's share is counted on its own
 * index, at most `perClient` rows. Every query has an index. Settled rows are kept
 * `settledRetentionS`, at most `maxSettled`.
 */
import { Invalid, Refused } from "./errors";
import { codePoints } from "./text";

export const WORK_KINDS: readonly string[] = Object.freeze(["turn"]);
/** A claimed hand-off's lease: past it, the may-have-been-handed mark decides. */
export const WORK_LEASE_S = 60;
/** At most this many rows per pass per lane run. */
export const WORK_PER_PASS = 16;
/** The longest a runner may hold one hand-off before the pass reports it and moves on. */
export const WORK_HANDOFF_MS = 5_000;
/** How long a row whose step failed in process waits before the lane takes it again. */
export const WORK_RETRY_S = 60;
const HEX64 = /^[0-9a-f]{64}$/;

export interface WorkLimits {
  readonly maxOpen: number;
  /** Open work one outside client may hold. */
  readonly perClient: number;
  readonly maxSettled: number;
  readonly settledRetentionS: number;
  /** Characters in a work row's payload, as JSON. */
  readonly maxPayload: number;
  /** Runs an unmarked claim may lapse before the work fails. */
  readonly attempts: number;
}

export const WORK_LIMITS: WorkLimits = Object.freeze({
  maxOpen: 1_000,
  perClient: 100,
  maxSettled: 1_000,
  settledRetentionS: 7 * 86_400,
  maxPayload: 16_384,
  attempts: 3,
});

/** The outside authority work was made under. */
export interface Stamp {
  readonly client: string;
  readonly generation: number;
  readonly authenticated_at: number | null;
}

/** What `standing` answers; the object's grants (`grants.ts`). */
export interface StandingReader {
  standing(client: unknown, generation: unknown, authenticatedAt: number | null): unknown;
}

/** A claimed run: what the runner is handed. */
export interface WorkTarget {
  readonly work_id: string;
  readonly claim_id: string;
  readonly kind: string;
  readonly payload: unknown;
  readonly stamp: Stamp | null;
}

/** A hand-off's answer: handed to pi (its status is pi's receipts), or refused before. */
export interface WorkAnswer {
  readonly state: "handed" | "failed";
  readonly reason?: string;
}

/**
 * What hands a due piece of work to pi: the agent's, from tasks 5. It never runs the turn
 * inline: it calls `mark()`, which throws if the claim lapsed (then it stops), hands the
 * turn to pi's durable submit, and returns within `WORK_HANDOFF_MS`. A throw leaves the
 * row to its lease.
 */
export interface WorkRunner {
  handOff(target: WorkTarget, mark: () => void): Promise<WorkAnswer>;
}

export type WorkClaim =
  | { readonly outcome: "claimed"; readonly target: WorkTarget }
  | { readonly outcome: "refused"; readonly reason: string }
  | { readonly outcome: "not_due" };

type Row = {
  work_id: string; kind: string; due: number; state: string; payload: string;
  client: string | null; generation: number | null; authenticated_at: number | null;
  claim_id: string | null; marked: number; attempts: number; created_at: number;
  settled_at: number | null; outcome: string | null;
};

const REASON = /^[a-z_]{1,64}$/;

function token(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

function stampOf(row: Row): Stamp | null {
  return row.client === null ? null
    : { client: row.client, generation: row.generation!, authenticated_at: row.authenticated_at };
}

/** A stamp as an admitted call gives it, or refused. */
export function stamp(value: unknown): Stamp | null {
  if (value === null) {
    return null;
  }
  if (typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join() !== "authenticated_at,client,generation") {
    throw new Invalid("a stamp is a client, its grant generation and its sign-in time");
  }
  const { client, generation, authenticated_at: at } = value as Record<string, unknown>;
  if (typeof client !== "string" || !HEX64.test(client) || typeof generation !== "number"
      || !Number.isSafeInteger(generation) || generation < 1
      || (at !== null && (typeof at !== "number" || !Number.isFinite(at) || at < 0))) {
    throw new Invalid("a stamp is a client, its grant generation and its sign-in time");
  }
  return { client, generation, authenticated_at: at as number | null };
}

export class Scheduled {
  readonly #storage: DurableObjectStorage;
  readonly #grants: StandingReader;
  readonly #limits: WorkLimits;

  constructor(storage: DurableObjectStorage, grants: StandingReader,
              limits: WorkLimits = WORK_LIMITS) {
    this.#storage = storage;
    this.#grants = grants;
    this.#limits = limits;
  }

  get #sql(): SqlStorage {
    return this.#storage.sql;
  }

  #usage(): { open_work: number; settled_work: number } {
    return this.#sql.exec<{ open_work: number; settled_work: number }>(
      "SELECT open_work, settled_work FROM policy_usage").one();
  }

  #row(workId: string): Row | undefined {
    return this.#sql.exec<Row>("SELECT * FROM scheduled_work WHERE work_id = ?", workId)
      .toArray()[0];
  }

  /**
   * Schedule one piece of work at `due`, on the owner's own authority (`stamp` null) or
   * the outside client's it was admitted under, which must stand now.
   */
  add(kind: unknown, due: unknown, payload: unknown, made: unknown, now: number):
      { work_id: string } {
    if (typeof kind !== "string" || !WORK_KINDS.includes(kind)) {
      throw new Invalid("unknown scheduled work kind");
    }
    if (typeof due !== "number" || !Number.isFinite(due) || due < 0) {
      throw new Invalid("due is a time in seconds");
    }
    const body = JSON.stringify(payload);
    if (body === undefined || codePoints(body) > this.#limits.maxPayload) {
      throw new Invalid(`a work payload is JSON of at most ${this.#limits.maxPayload} characters`);
    }
    const by = stamp(made);
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      if (by !== null) {
        this.#grants.standing(by.client, by.generation, by.authenticated_at);
        const held = sql.exec<{ n: number }>(
          "SELECT COUNT(*) AS n FROM scheduled_work WHERE client = ? AND settled_at IS NULL",
          by.client).one().n;
        if (held >= this.#limits.perClient) {
          throw new Refused("this client's scheduled work is at its limit");
        }
      }
      if (this.#usage().open_work >= this.#limits.maxOpen) {
        throw new Refused("the owner's scheduled work is at its limit");
      }
      const workId = "work_" + token(16);
      sql.exec("INSERT INTO scheduled_work (work_id, kind, due, state, payload, client, " +
                 "generation, authenticated_at, created_at) VALUES (?, ?, ?, 'pending', ?, ?, " +
                 "?, ?, ?)", workId, kind, due, body, by?.client ?? null,
               by?.generation ?? null, by?.authenticated_at ?? null, now);
      return { work_id: workId };
    });
  }

  /** A changed or revoked grant refuses its client's pending work; how many. */
  endClient(client: string, now: number): number {
    return this.#storage.transactionSync(() => {
      const ended = this.#sql.exec(
        "UPDATE scheduled_work SET state = 'refused', settled_at = ?, outcome = ? " +
          "WHERE client = ? AND state = 'pending' AND settled_at IS NULL RETURNING work_id",
        now, JSON.stringify({ reason: "grant_changed" }), client).toArray().length;
      this.#prune(now);
      return ended;
    });
  }

  /** Every row, oldest first: the owner's view, and the tests'. */
  list(): (Omit<Row, "payload"> & { payload: unknown })[] {
    return this.#sql.exec<Row>("SELECT * FROM scheduled_work ORDER BY created_at, work_id")
      .toArray().map((row) => ({ ...row, payload: JSON.parse(row.payload) as unknown }));
  }

  /** Pending work that is due, oldest first: what the claim pass takes. */
  dueOpen(now: number, limit: number): string[] {
    return this.#sql.exec<{ work_id: string }>(
      "SELECT work_id FROM scheduled_work WHERE state = 'pending' AND due <= ? " +
        "ORDER BY due LIMIT ?", now, limit).toArray().map((row) => row.work_id);
  }

  /** Claims whose lease has lapsed, oldest first: what the recover pass takes. */
  lapsed(now: number, limit: number): string[] {
    return this.#sql.exec<{ work_id: string }>(
      "SELECT work_id FROM scheduled_work WHERE state = 'running' AND due <= ? " +
        "ORDER BY due LIMIT ?", now, limit).toArray().map((row) => row.work_id);
  }

  /**
   * Before it runs: due pending work is rechecked against the authority it was made under,
   * refused if that no longer stands, else claimed under a lease, unmarked.
   */
  claim(workId: string, now: number): WorkClaim {
    return this.#storage.transactionSync((): WorkClaim => {
      const row = this.#sql.exec<Row>(
        "SELECT * FROM scheduled_work WHERE work_id = ? AND state = 'pending' AND due <= ?",
        workId, now).toArray()[0];
      if (row === undefined) {
        return { outcome: "not_due" };
      }
      const by = stampOf(row);
      if (by !== null) {
        try {
          this.#grants.standing(by.client, by.generation, by.authenticated_at);
        } catch (error) {
          if (!(error instanceof Refused)) {
            throw error;
          }
          this.#settle(row, "refused", { reason: "grant_changed" }, now);
          return { outcome: "refused", reason: error.message };
        }
      }
      const claimId = "claim_" + token(16);
      this.#sql.exec("UPDATE scheduled_work SET state = 'running', claim_id = ?, marked = 0, " +
                       "attempts = attempts + 1, due = ? WHERE work_id = ?",
                     claimId, now + WORK_LEASE_S, workId);
      return { outcome: "claimed", target: { work_id: workId, claim_id: claimId, kind: row.kind,
                                             payload: JSON.parse(row.payload), stamp: by } };
    });
  }

  /** A row whose step failed in process, held back to `until` (never earlier than it was). */
  defer(workId: string, until: number): void {
    this.#sql.exec("UPDATE scheduled_work SET due = MAX(due, ?) WHERE work_id = ? AND state IN " +
                     "('pending', 'running')", until, workId);
  }

  /** The durable may-have-run mark, written before the work can have any effect. */
  mark(workId: string, claimId: string): boolean {
    return this.#sql.exec("UPDATE scheduled_work SET marked = 1 WHERE work_id = ? AND " +
                            "claim_id = ? AND state = 'running' RETURNING work_id",
                          workId, claimId).toArray().length > 0;
  }

  /**
   * A claim abandoned past its lease: `unknown` if it may have run, never run again; else
   * due again, or `failed` once it has used its attempts.
   */
  recover(workId: string, now: number): string | null {
    return this.#storage.transactionSync(() => {
      const row = this.#sql.exec<Row>(
        "SELECT * FROM scheduled_work WHERE work_id = ? AND state = 'running' AND due <= ?",
        workId, now).toArray()[0];
      if (row === undefined) {
        return null;
      }
      if (row.marked === 1 || row.attempts >= this.#limits.attempts) {
        const state = row.marked === 1 ? "unknown" : "failed";
        this.#settle(row, state, { reason: "recovered" }, now);
        return state;
      }
      this.#sql.exec("UPDATE scheduled_work SET state = 'pending', claim_id = NULL, due = ? " +
                       "WHERE work_id = ?", now, workId);
      return "pending";
    });
  }

  /** A run's answer; only the claim that holds the row settles it. */
  settle(workId: string, claimId: string, answer: unknown, now: number): boolean {
    if (typeof answer !== "object" || answer === null
        || !Object.keys(answer).every((key) => key === "state" || key === "reason")) {
      throw new Invalid("invalid work answer");
    }
    const { state, reason } = answer as Record<string, unknown>;
    if ((state !== "handed" && state !== "failed")
        || (reason !== undefined && (typeof reason !== "string" || !REASON.test(reason)))) {
      throw new Invalid("invalid work answer");
    }
    return this.#storage.transactionSync(() => {
      const row = this.#sql.exec<Row>(
        "SELECT * FROM scheduled_work WHERE work_id = ? AND claim_id = ? AND state = 'running'",
        workId, claimId).toArray()[0];
      if (row === undefined) {
        return false;
      }
      this.#settle(row, state, reason === undefined ? {} : { reason }, now);
      return true;
    });
  }

  #settle(row: Row, state: string, outcome: Record<string, unknown>, now: number): void {
    this.#sql.exec("UPDATE scheduled_work SET state = ?, settled_at = ?, outcome = ? " +
                     "WHERE work_id = ?", state, now, JSON.stringify(outcome), row.work_id);
    this.#prune(now);
  }

  /** Settled rows kept for the retention, and at most `maxSettled`. */
  #prune(now: number): void {
    const sql = this.#sql;
    sql.exec("DELETE FROM scheduled_work WHERE settled_at IS NOT NULL AND settled_at < ?",
             now - this.#limits.settledRetentionS);
    const over = this.#usage().settled_work - this.#limits.maxSettled;
    if (over > 0) {
      sql.exec("DELETE FROM scheduled_work WHERE work_id IN (SELECT work_id FROM " +
                 "scheduled_work WHERE settled_at IS NOT NULL ORDER BY settled_at LIMIT ?)", over);
    }
  }

  /**
   * When the `work` lane next has work, never before `now`; null for none. A claim's lease
   * always counts; pending work only when there is a runner (`running`).
   */
  nextDue(now: number, running: boolean): number | null {
    // Literal states, so each reads its own partial index.
    const earliest = (query: string) => this.#sql.exec<{ due: number | null }>(query).one().due;
    const times = [earliest("SELECT MIN(due) AS due FROM scheduled_work WHERE state = 'running'"),
                   running ? earliest("SELECT MIN(due) AS due FROM scheduled_work " +
                                      "WHERE state = 'pending'") : null]
      .filter((due): due is number => due !== null);
    return times.length ? Math.max(Math.min(...times), now) : null;
  }
}

/** What a hand-off came to within its bound: its answer, or that it ran late. */
async function bounded(run: Promise<WorkAnswer>, ms: number): Promise<WorkAnswer | "late"> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<"late">((resolve) => {
    timer = setTimeout(() => resolve("late"), ms);
  });
  try {
    return await Promise.race([run, late]);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * One `work` lane pass; how many hand-offs finished. First the recover pass, whether or
 * not there is a runner; then, with one, each due pending row once. A row's failure is
 * kept in its row (`WORK_RETRY_S`); a hand-off past `handoffMs` is reported and left to
 * its lease, marked.
 */
export async function runWork(work: Scheduled, runner: WorkRunner | null, clock: () => number,
                              limit: number = WORK_PER_PASS,
                              handoffMs: number = WORK_HANDOFF_MS): Promise<number> {
  for (const workId of work.lapsed(clock(), limit)) {
    try {
      work.recover(workId, clock());
    } catch {
      console.error("scheduled work recovery failed");  // no ids or payload in the log
      work.defer(workId, clock() + WORK_RETRY_S);
    }
  }
  if (runner === null) {
    return 0;
  }
  let finished = 0;
  for (const workId of work.dueOpen(clock(), limit)) {
    try {
      const claim = work.claim(workId, clock());
      if (claim.outcome !== "claimed") {
        continue;
      }
      const { target } = claim;
      const handing = runner.handOff(target, () => {
        if (!work.mark(target.work_id, target.claim_id)) {
          throw new Error("the work's claim lapsed before its hand-off");
        }
      });
      const answer = await bounded(handing, handoffMs);
      if (answer === "late") {
        // It may yet hand the work over: never hand it again, and let its lease decide.
        handing.catch(() => undefined);
        work.mark(target.work_id, target.claim_id);
        console.error("a scheduled hand-off ran past its bound; it is left to its lease");
        continue;
      }
      if (work.settle(target.work_id, target.claim_id, answer, clock())) {
        finished++;
      }
    } catch {
      console.error("scheduled work failed");  // no ids or payload in the log
      work.defer(workId, clock() + WORK_RETRY_S);
    }
  }
  return finished;
}
