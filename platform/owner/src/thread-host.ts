/**
 * The owner object's thread host (ADR-032 §4, amended 2026-10-30; tasks 5, S3): many pi
 * Sessions in one object, one per agent thread, each over its own store
 * (`thread-store.ts`, `remote-storage.ts`), woken by a job of its own on the object's one
 * Lifecycle. It replaces agents' PiHarness, which hosts exactly one Session per object
 * (one `pi_` prefix over the object's own database, one wake job, one capability name).
 *
 * What pi cannot do on a Durable Object is wake itself: its scheduler runs in memory, and an
 * evicted object has no memory. The host is that wake, per thread: job `pi-wake:<thread>`,
 * driven by that thread's Session's `onWake`. Input goes to pi once, after the thread's job
 * is scheduled (`#admit`). While the Session has work running, its job waits on it,
 * rescheduling itself as a heartbeat; while its tasks sleep, the job is due when the first
 * wakes; with nothing to do, the job completes. An eviction mid-run leaves the job due, so
 * its alarm restarts the object, the thread's Session reopens from its store and resumes
 * its own tasks, and the job waits again. Each change of a Session's wake is also kept in
 * the owner's rows (its live row, `threads.ts`: active, and when it next wakes), in a unit, so
 * retention and the end of a client's or the owner's work read rows, never every store.
 * Lifecycle runs jobs one at a time, but a step only opens a Session and resumes it: a wait
 * on running work is handed off and tracked (`trackAlarmWork`), never awaited inline.
 *
 * Sessions open on demand: a door's call, or the thread's own job. None opens at startup.
 * At most `openMax` are open at once. Past it, the least recently used idle Session is
 * closed to make room; with none idle (every one working, waiting, admitting or pinned),
 * the open is refused at once, loudly (`SESSIONS_FULL`), and never waits for a slot: a
 * wait could deadlock when every open Session is working on a tool that opens another
 * thread (`agent:run`). A Session idle for `idleCloseMs` is closed at the host's next
 * activity. A watched Session (A6) is pinned open and counted. Closing never loses work:
 * what was committed is in the store, and a sleeping task's job reopens its Session when it
 * is due.
 *
 * A Session fails for good at its first storage error (a store reset, or a stale holder
 * refused by the store's fence); its job, or the next call, opens it again once the failed
 * one has closed, over what it committed, and pi resumes its tasks (an unsafe tool cut off
 * comes back `interrupted`). A wait the failure cut short waits again on the new Session.
 *
 * A task pi's restart limit parked (cut off `restartLimit` times in a row without progress)
 * would hold its thread's run forever: its input placed, its job gone, every follow-up
 * queued behind it. Each open looks for one (`inspect`) before it resumes the Session; one
 * found is aborted with the run of its own conversation (`#endParked`), so that run's
 * input settles `unanswered`, and the owner is told (`PARKED`). While it ends, the
 * operation's status says so (`reason: "parked"`).
 *
 * Each open Session holds its store's one RPC session (`remote-storage.ts`), released when
 * the Session closes, however it closes. Holding it keeps the store in memory, billed: a
 * pinned (watched, A6) Session pins its store's duration for as long as it is pinned.
 *
 * Ported from agents 0.31.0 `harness/pi` (PiHarness): `_admit`, `_wake`/`_wakeFor`,
 * `_wakeStep`, `_waitWhileActive`, `onJobError` with its backoff, `_open`/`_doOpen`/
 * `_piClosing`, `enqueue`, `withdraw`, `settled` and `_parked`, each keyed by thread.
 * Dropped: the `sessions` API, `prompt`, `messages`, `events()` (removed on pi main),
 * `retryTask` (removed on pi main: a parked run is ended, not retried), `skills()` (it adds
 * model tools). Each agents release: diff PiHarness and carry the fixes that apply
 * (tasks 5).
 *
 * The ported parts: Copyright (c) 2025 Cloudflare, Inc. MIT License. Permission is hereby
 * granted, free of charge, to any person obtaining a copy of this software and associated
 * documentation files (the "Software"), to deal in the Software without restriction,
 * including without limitation the rights to use, copy, modify, merge, publish, distribute,
 * sublicense, and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions: The above copyright
 * notice and this permission notice shall be included in all copies or substantial portions
 * of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
 * PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
 * BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT
 * OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 */
import { BACKGROUND_CONTEXT, type Context, withAbortSignal } from "@earendil-works/chord/context";
import {
  type ConversationId, type Cursor, type EntryRecord, type Harness, type JsonObject, type Page,
  ROOT_CONVERSATION_ID,
  type SettledSubmissionRecord, type Storage, type TaskId, type UsageState, type Wake,
} from "@earendil-works/pi-durable";
import {
  LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";

import { Refused } from "./errors";
import { COMPACTION, JOINED } from "./origin";
import { inTime } from "./schedule";
import { assistantText } from "./thread-store";
import { THREAD_INPUTS } from "./schema";

const BG = BACKGROUND_CONTEXT;
const ROOT = ROOT_CONVERSATION_ID;

/** The host's Lifecycle capability id. */
export const HOST = "ta-threads";
/** The wake jobs' function. */
export const WAKE_FN = "wake";
/** A thread's wake job id. */
export function wakeJob(thread: string): string {
  return `pi-wake:${thread}`;
}
const JOB = /^pi-wake:(.+)$/;

/**
 * Sessions open at once, past which an idle one is closed or the open refused. Live probe P4
 * (`store-probe.md`) held 128 Sessions of about 316K characters of context each in one
 * isolate with no memory or CPU error; half of that, since an isolate may host other
 * owners' objects too and the break point is not measured.
 */
export const OPEN_MAX = 64;
/** How long an idle Session stays open. */
export const IDLE_CLOSE_MS = 2 * 60_000;
/**
 * The longest one wake waits on a Session. It waits inside an alarm invocation, which has a
 * 15-minute wall-time limit, so a longer run is waited on across several alarms.
 */
export const WAIT_BUDGET_MS = 10 * 60_000;
/**
 * A wake job's heartbeat while it waits, and how often it re-checks. If the object is
 * evicted, the job is still due and its alarm restarts the object.
 */
export const HEARTBEAT_MS = 30_000;
/** The longest a wake job whose dispatch keeps failing waits before it tries again. */
export const MAX_WAKE_BACKOFF_MS = 10 * 60_000;
/** How long a wait that failed gives its Session to close before it gives up. */
const SESSION_CLOSE_MS = 1_000;
/** An open refused past `openMax` with no idle Session to close. */
export const SESSIONS_FULL = "too many of the agent's threads are working at once";
/** The refusal of a submit whose thread was dropped while it was admitted. */
export const THREAD_DROPPED = "the thread was dropped";
/**
 * The refusal of a new input to a settled run (`HostParts.settled`): a run that settled
 * takes no more work, so it holds no turn and no live row; going on is a conversation's.
 */
export const RUN_SETTLED = "run_settled: the run has settled and takes no more input";
/** The owner notice a thread's open owners past its bound leave as it opens. */
export const TURNS_UNKEPT = "turns_unkept";
const TURNS_UNKEPT_DETAIL = "a thread held more open inputs or live compactions than it may " +
  "as its Session opened; the calls of those past the bound are refused turn_ended";
/** The owner notice a thread's usage that could not be kept at its close leaves. */
export const USAGE_UNKEPT = "usage_unkept";
const USAGE_UNKEPT_DETAIL = "a thread's model usage could not be kept as its Session " +
  "closed; the usage view counts it as it last closed";
/**
 * The refusal of an input past `THREAD_INPUTS` open ones a thread holds (`schema.ts`), at
 * once, never waited on: each owns a turn's counters while it is open.
 */
export const THREAD_INPUTS_FULL = "thread_inputs_full: the thread holds as many open inputs " +
  "as it may; wait for one to be answered";
/** The task states that are still live, as pi's scheduler holds them. */
const LIVE_TASKS = Object.freeze(["pending", "running", "waiting", "sleeping", "completing"] as const);
const SCAN_PAGE = 100;

/** The owner notice a parked task's ended run leaves (`#endParked`). */
export const PARKED = "run_parked";
const PARKED_DETAIL = "a task was cut off too many times in a row without progress, so " +
  "pi parked it; the run it was in was ended, and its input left unanswered";

/** The host's timings and bound; production's are the constants, a test's shorter. */
export interface HostTiming {
  readonly openMax: number;
  readonly idleCloseMs: number;
  readonly waitBudgetMs: number;
  readonly heartbeatMs: number;
}

export const HOST_TIMING: HostTiming = Object.freeze({
  openMax: OPEN_MAX, idleCloseMs: IDLE_CLOSE_MS, waitBudgetMs: WAIT_BUDGET_MS,
  heartbeatMs: HEARTBEAT_MS,
});

/** A thread's opened Session: pi, over the storage it was opened on. */
export interface Opened {
  readonly pi: Harness;
  readonly storage: Storage;
}

/** What the host runs on: the owner agent's opener and the owner's rows. */
export interface HostParts {
  /**
   * Open `thread`'s Session wired to `onWake`, its root made when absent; throws for a
   * thread with no row.
   */
  open(thread: string, onWake: (wake: Wake) => void): Promise<Opened>;
  /** Whether `thread` has a row: a dropped thread is never opened again. */
  exists(thread: string): boolean;
  /** Whether `thread` is a run that settled: a new input to it is refused (`RUN_SETTLED`). */
  settled(thread: string): boolean;
  /**
   * Keep `thread`'s wake in the owner's rows, in a unit of the write gate; `opened` is the
   * Session that reported it, when it is open (a run's settling is read from it), and
   * `quiet`, read in the settling unit, whether no input is being admitted: a run settles
   * only then, so an input admitted is never refused for a settling that came after it.
   */
  live(thread: string, wake: Wake, opened?: Opened, quiet?: () => boolean): Promise<void>;
  /** Keep `thread`'s usage (pi's `pi.usage`) as its Session closes, in a unit. */
  usage(thread: string, usage: UsageState): Promise<void>;
  /**
   * The turn owners of `thread` that opened or ended in a commit, in a unit run at once
   * (`Usage.turnOwners`), and all of them as its store holds them when it opens
   * (`Usage.turnsReconciled`).
   */
  turns: {
    owned(thread: string, owners: readonly TurnOwner[]): Promise<void>;
    /** How many owners past the thread's bound it passed over. */
    reconciled(thread: string, open: readonly TurnOwner[]): Promise<number>;
    /** How many open inputs `thread`'s turn owners hold (`Usage.openInputs`). */
    inputs(thread: string): number;
  };
  /** Keep an owner notice (`notices.ts`). */
  notice(type: string, detail: string): void;
}

/** An operation, as pi's submission record says it stands. */
export interface OperationResult {
  readonly operationId: string;
  readonly status: "queued" | "running" | "done" | "unanswered";
  readonly reason?: string;
  readonly text?: string;
}

/** How an input is submitted to a thread. */
export interface SubmitOptions {
  readonly operationId: string;
  readonly whenBusy?: "followUp" | "steer";
  /** The entry the input is placed as (its kind and data), when not pi's plain input. */
  readonly entry?: { readonly kind: string; readonly data: JsonObject };
  /**
   * Awaited once the input is admitted, past the open Sessions' and the thread's open
   * inputs' bounds, before pi's submit commits it; a throw stops the submit, and so does
   * the thread's drop meanwhile.
   */
  readonly admitted?: () => Promise<void>;
}

/** A task pi's restart limit parked: its id, its conversation, and whether background. */
interface Parked {
  readonly task: TaskId;
  readonly conversation: ConversationId;
  readonly background: boolean;
}

/** One thread's place in the host: its Session, and what holds it open. */
class Slot {
  opening: Promise<Opened> | undefined;
  /** Set once `opening` resolved, while it is the slot's current Session. */
  opened: Opened | undefined;
  /** The last Session's close: a new one opens only once the old one closed. */
  lastClose: Promise<unknown> = Promise.resolve();
  /** The wake job's wait on running work, while it runs. */
  waiting: Promise<void> | undefined;
  /** Ends the wait's current sleep: the Session's wake changed, or it is closing. */
  changed: (() => void) | undefined;
  admitting = 0;
  leases = 0;
  pins = 0;
  /** Endings in flight (`end`): the job never resumes the Session under one. */
  ending = 0;
  /** A parked task's run is being ended (`#endParked`). */
  parked = false;
  lastUsed = Date.now();
}

/** Whether `error`, or anything in its causes, is a memory-limit reset. */
export function memoryLimitReset(error: unknown): boolean {
  let current: unknown = error;
  for (let depth = 0; depth < 8 && current !== undefined && current !== null; depth++) {
    const message = current instanceof Error ? current.message
      : typeof current === "string" ? current : "";
    if (/exceeded its memory limit/i.test(message)) {
      return true;
    }
    current = current instanceof Error ? current.cause : undefined;
  }
  return false;
}

export class ThreadHost extends LifecycleCapability {
  readonly #parts: HostParts;
  readonly #timing: HostTiming;
  readonly #slots = new Map<string, Slot>();
  /** Sessions closing, so a wait that sees one end blames no one. */
  readonly #closing = new WeakSet<Harness>();

  constructor(parts: HostParts, timing: HostTiming = HOST_TIMING) {
    super(HOST);
    this.#parts = parts;
    this.#timing = timing;
  }

  // -- Lifecycle ---------------------------------------------------------------------

  /** A thread's wake job is due: one step of its wake. */
  async onJob({ job }: LifecycleJobContext): Promise<LifecycleJobOutcome> {
    const thread = job.fn === WAKE_FN ? JOB.exec(job.id)?.[1] : undefined;
    if (thread === undefined) {
      return undefined;
    }
    const outcome = await this.#step(thread);
    const kv = this.lifecycle.storage.kv;
    if (kv.get(errorsKey(thread)) !== undefined) {
      kv.delete(errorsKey(thread));
    }
    return outcome;
  }

  /**
   * A wake step that failed through Lifecycle's retries, as when the thread's Session
   * cannot be opened. Lifecycle would complete the job, and the thread's work would never
   * be woken again, so it tries again later: at the heartbeat, doubling while the failures
   * continue, up to 10 minutes. Each thread's backoff is its own.
   */
  onJobError({ job }: LifecycleJobContext, _error: unknown): LifecycleJobOutcome {
    const thread = job.fn === WAKE_FN ? JOB.exec(job.id)?.[1] : undefined;
    if (thread === undefined) {
      return undefined;
    }
    console.error("a thread's wake failed; it is tried again with backoff");  // no ids
    const kv = this.lifecycle.storage.kv;
    const errors = (kv.get<number>(errorsKey(thread)) ?? 0) + 1;
    kv.put(errorsKey(thread), errors);
    const delay = Math.min(this.#timing.heartbeatMs * 2 ** (errors - 1), MAX_WAKE_BACKOFF_MS);
    return { rescheduleAt: Date.now() + delay };
  }

  /** Close every Session's in-memory resources. Durable state is untouched. */
  async dispose(): Promise<void> {
    await Promise.all([...this.#slots.values()].map((slot) => this.#closeSlot(slot)));
  }

  // -- The doors' calls --------------------------------------------------------------

  /** Durably submit `input` to `thread`'s root. Resolves before the model runs. */
  submit(thread: string, input: string, options: SubmitOptions):
      Promise<{ operationId: string; accepted: boolean }> {
    const { operationId } = options;
    return this.#use(thread, async ({ pi, storage }, slot) => {
      const conversation = await pi.conversation(ROOT, BG);
      if (conversation === undefined) {
        throw new Error("a thread's Session has no root");
      }
      const accepted = await this.#admit(thread, slot, async () => {
        const fresh = await storage.submissionByRequest(ROOT, operationId, BG) === undefined;
        // A settled run takes no new input, refused here, before pi commits one (a resend
        // of its own is pi's duplicate, and commits nothing).
        if (fresh && this.#parts.settled(thread)) {
          throw new Refused(RUN_SETTLED);
        }
        // The thread's open inputs as its turn owners hold them, with every admission in
        // flight beside this one, so none passes the cap.
        if (fresh && this.#parts.turns.inputs(thread) + slot.admitting - 1 >= THREAD_INPUTS) {
          throw new Refused(THREAD_INPUTS_FULL);
        }
        if (options.admitted !== undefined) {
          await options.admitted();
          if (!this.#parts.exists(thread)) {
            // Dropped while it was admitted (a grant change): nothing reaches its store.
            throw new Refused(THREAD_DROPPED);
          }
        }
        await conversation.submit({
          type: "input", content: input, whenBusy: options.whenBusy ?? "followUp",
          requestId: operationId, ...(options.entry === undefined ? {} : { entry: options.entry }),
        }, BG);
        return fresh;
      });
      return { operationId, accepted };
    });
  }

  /** Withdraw a queued input, or abort the run it joined; false when nothing was. */
  withdraw(thread: string, operationId: string): Promise<boolean> {
    return this.use(thread, async ({ pi, storage }) => {
      const record = await storage.submissionByRequest(ROOT, operationId, BG);
      if (record === undefined || record.status === "done" || record.status === "unanswered") {
        return false;
      }
      switch (await pi.abortSubmission(record.id, BG, ROOT)) {
        case "aborted":
          return true;
        case "already_placed":
          await (await pi.conversation(ROOT, BG))?.abort(BG);
          return true;
        default:
          return false;
      }
    });
  }

  /**
   * How an operation stands, from pi's submission record alone (one read, and the answer's
   * text once done): `not_found` for one pi never saw.
   */
  status(thread: string, operationId: string): Promise<OperationResult> {
    return this.use(thread, async ({ pi, storage }) => {
      const record = await storage.submissionByRequest(ROOT, operationId, BG);
      switch (record?.status) {
        case undefined:
          return { operationId, status: "unanswered", reason: "not_found" };
        case "queued":
          return { operationId, status: "queued" };
        case "placed":
          return this.#slots.get(thread)?.parked === true
            ? { operationId, status: "running", reason: "parked" }
            : { operationId, status: "running" };
        default:
          return this.#result(pi, operationId, record as SettledSubmissionRecord, BG);
      }
    });
  }

  /**
   * Wait for pi to settle an operation, until `signal`. A Session that fails while it waits
   * is opened again and waited on again, once.
   */
  async settled(thread: string, operationId: string, signal?: AbortSignal):
      Promise<OperationResult> {
    const context = signal === undefined ? BG : withAbortSignal(signal, BG);
    for (let attempt = 0; ; attempt++) {
      let waitedOn: Harness | undefined;
      try {
        return await this.use(thread, async ({ pi, storage }) => {
          waitedOn = pi;
          const record = await storage.submissionByRequest(ROOT, operationId, context);
          const submission = record === undefined ? undefined
            : await pi.submission(record.id, context);
          if (submission === undefined) {
            return { operationId, status: "unanswered" as const, reason: "not_found" };
          }
          return this.#result(pi, operationId, await submission.wait(context), context);
        });
      } catch (error) {
        // Only a wait the Session's own failure ended waits again, once: the Session
        // closes at its first failed storage call (`closed` settles then).
        if (signal?.aborted === true || attempt > 0 || waitedOn === undefined
            || await inTime(waitedOn.closed.then(() => true, () => true),
                            SESSION_CLOSE_MS) === "late") {
          throw error;
        }
      }
    }
  }

  /**
   * Run `act` on `thread`'s Session, opened if it is not: the Session counts as in use, so
   * it is never closed idle, until `act` settles.
   */
  use<T>(thread: string, act: (opened: Opened) => Promise<T>): Promise<T> {
    return this.#use(thread, (opened) => act(opened));
  }

  async #use<T>(thread: string, act: (opened: Opened, slot: Slot) => Promise<T>,
                ending = false): Promise<T> {
    const { opened, slot } = await this.#open(thread, ending);
    try {
      return await act(opened, slot);
    } finally {
      this.#release(slot);
    }
  }

  /** Give back a lease `#open` took. */
  #release(slot: Slot): void {
    slot.leases--;
    slot.lastUsed = Date.now();
  }

  /**
   * End `thread`'s work with `act` (which only aborts): its Session opened if need be, past
   * `openMax`, since an ending is never refused, and never resumed before `act` ran, so a
   * stale run starts no model request first. A Session opened only to end closes after.
   */
  async end(thread: string, act: (opened: Opened) => Promise<void>): Promise<void> {
    const wasOpen = this.#slots.get(thread)?.opening !== undefined;
    await this.#use(thread, async (opened, slot) => {
      slot.ending++;
      try {
        await act(opened);
      } finally {
        slot.ending--;
      }
    }, true);
    const slot = this.#slots.get(thread);
    if (!wasOpen && slot !== undefined && this.#idle(slot)) {
      await this.#closeSlot(slot);
    }
  }

  /**
   * A dropping thread's Session ended and closed (a drop's second step, `threads.ts`): its
   * work ended by `act` (which only aborts) in the Session as it is, past `openMax` and
   * never resumed, then the Session closed, however it was held open. One not open is
   * never opened: nothing of it runs here, and its store is about to be wiped. Resolves
   * once it closed; an open that was in flight is awaited first, so its store is made, if
   * it is, before the drop wipes it.
   */
  async drop(thread: string, act: (opened: Opened) => Promise<void>): Promise<void> {
    if (this.#slots.get(thread)?.opening !== undefined) {
      try {
        await this.#use(thread, async (opened, slot) => {
          slot.ending++;
          try {
            await act(opened);
          } finally {
            slot.ending--;
          }
        }, true);
      } catch (error) {
        if (this.#slots.get(thread)?.opened !== undefined) {
          throw error;  // its work could not be ended
        }
        // The open in flight failed, or the Session closed meanwhile: nothing runs.
      }
    }
    const slot = this.#slots.get(thread);
    if (slot !== undefined) {
      slot.pins = 0;  // a watch ends with its thread
      await this.#closeSlot(slot);
    }
  }

  /**
   * Inside the drop's last unit: `thread`'s wake job and its failure count, forgotten with
   * its row; the job row goes in the unit, and the alarm is re-armed after it (the answer).
   */
  forgetWake(thread: string): Promise<unknown> {
    const kv = this.lifecycle.storage.kv;
    if (kv.get(errorsKey(thread)) !== undefined) {
      kv.delete(errorsKey(thread));
    }
    return this.lifecycle.jobs.cancel(wakeJob(thread));
  }

  /** `thread`'s Session, opened if it is not (a test's, and the agent's brief reads). */
  async pi(thread: string): Promise<Harness> {
    const { opened, slot } = await this.#open(thread);
    this.#release(slot);
    return opened.pi;
  }

  /** Sessions open at once, past which an open makes room or is refused. */
  get openMax(): number {
    return this.#timing.openMax;
  }

  /** `thread`'s Session if it is open now; never opens one. */
  peek(thread: string): Opened | undefined {
    return this.#slots.get(thread)?.opened;
  }

  /** The threads whose Sessions are open now. */
  openThreads(): string[] {
    return [...this.#slots].filter(([, slot]) => slot.opening !== undefined)
      .map(([thread]) => thread);
  }

  /** Pin `thread`'s Session open (a watch, A6): never closed idle, still counted. */
  async pin(thread: string): Promise<void> {
    const { slot } = await this.#open(thread);
    slot.pins++;
    this.#release(slot);
  }

  unpin(thread: string): void {
    const slot = this.#slots.get(thread);
    if (slot !== undefined && slot.pins > 0) {
      slot.pins--;
      slot.lastUsed = Date.now();
    }
  }

  /** Close `thread`'s Session, if open; resolves once it closed. */
  close(thread: string): Promise<void> {
    const slot = this.#slots.get(thread);
    return slot === undefined ? Promise.resolve() : this.#closeSlot(slot);
  }

  // -- Opening and closing -------------------------------------------------------------

  /**
   * `thread`'s Session, opened, with a lease on it taken before anything can run, so no
   * other call's room-making closes it between its open and its use; the caller gives the
   * lease back (`#release`). Waits for Lifecycle's startup first, so a Session is never
   * opened inside it. An open that fails is not kept, so the next call tries again. An
   * ending's open (`end`) takes no room, and does not resume the Session.
   */
  async #open(thread: string, ending = false): Promise<{ opened: Opened; slot: Slot }> {
    await this.lifecycle.ready();
    let slot = this.#slots.get(thread);
    if (slot === undefined) {
      slot = new Slot();
      this.#slots.set(thread, slot);
    }
    slot.lastUsed = Date.now();
    const current = slot;
    if (current.opening === undefined) {
      try {
        this.#sweep();
        if (!ending) {
          this.#room();
        }
      } catch (error) {
        this.#forget(thread, current);
        throw error;
      }
      const opening: Promise<Opened> = this.#doOpen(thread, current, () => opening, ending)
        .catch((error: unknown) => {
          if (current.opening === opening) {
            current.opening = undefined;
            this.#forget(thread, current);
          }
          throw error;
        });
      current.opening = opening;
    }
    current.leases++;
    try {
      return { opened: await current.opening, slot: current };
    } catch (error) {
      current.leases--;
      this.#forget(thread, current);
      throw error;
    }
  }

  async #doOpen(thread: string, slot: Slot, current: () => Promise<Opened>,
                ending: boolean): Promise<Opened> {
    await slot.lastClose;
    const opened = await this.#parts.open(thread, (wake) => this.#onWake(thread, slot, wake));
    const { pi } = opened;
    try {
      await this.#ownTurns(thread, opened);
      const closed = pi.closed;
      pi.subscribeClose(() => this.#piClosing(thread, slot, current(), opened, closed));
      const parked = await this.#parked(opened);
      if (parked.length > 0) {
        this.#endParked(slot, pi, parked);
      }
      if (!ending) {
        pi.resume();
        await new Promise((resolve) => setTimeout(resolve, 0));
        await this.#wakeFor(thread, pi.wake(), opened, slot);
      }
    } catch (error) {
      pi.close(BG).catch(() => {});
      throw error;
    }
    if (slot.opening === current()) {
      slot.opened = opened;
    }
    return opened;
  }

  /**
   * Keep `thread`'s turn owners as its Session commits them, from before it resumes: each
   * commit's in a unit at once (`subscribeCommits` runs on adoption, synchronously), and
   * first all of them as its store holds them, so one a lost unit missed (the object died
   * between the store's commit and its unit) is healed here. Nothing commits while the
   * Session is closed (the store's holder fence), and a commit while this scans is kept
   * after it, idempotently, so none is lost.
   */
  async #ownTurns(thread: string, { pi, storage }: Opened): Promise<void> {
    const turns = {
      owned: (at: string, owners: readonly TurnOwner[]) => this.#parts.turns.owned(at, owners),
      reconciled: async (at: string, open: readonly TurnOwner[]) => {
        const skipped = await this.#parts.turns.reconciled(at, open);
        if (skipped > 0) {
          this.#parts.notice(TURNS_UNKEPT, TURNS_UNKEPT_DETAIL);
        }
        return skipped;
      },
    };
    await keepTurns(thread, (listener) => pi.subscribeCommits(listener), turns,
                    () => openOwners(thread, storage), () => {
                      // Loudly: closed, its next open reconciles what this lost.
                      console.error("a thread's turns could not be kept; its Session closes");
                      pi.close(BG).catch(() => {});
                    });
  }

  /**
   * The tasks pi's restart limit parked, each with its own conversation: the run it holds
   * up. A parked task's own conversation is the narrowest end that frees what waits on it:
   * at the root, the thread's input; in a conversation background work made, that work's
   * own input, which nothing in the root waits on (a subagent a tool starts is a nested
   * `ta` call, which pi abandons at its first restart, so its tasks never park).
   */
  async #parked({ pi }: Opened): Promise<Parked[]> {
    const inspection = await pi.inspect(BG);
    return inspection.tasks
      .filter(({ state }) => state.kind === "blocked" && state.reason === "restart_limit")
      .map(({ record }) => ({ task: record.id, conversation: record.conversationId,
                              background: record.background === true }));
  }

  /**
   * End what each parked task holds up: the task itself marked aborted (pi runs its abort
   * handler, never its phase again), and, for a task of its conversation's run, that run
   * aborted (pi withdraws its queued inputs and settles its placed one `unanswered`); a
   * parked background task holds up no run, so it alone ends, and the conversation's own
   * run and inputs go on. An owner notice is kept. Work elsewhere in the thread, the root's
   * background work included, goes on. Off the
   * open's line: an abort resolves once the run is idle. One that fails is told loudly; the
   * next open finds the task still parked and tries again.
   */
  #endParked(slot: Slot, pi: Harness, parked: readonly Parked[]): void {
    slot.parked = true;
    this.#parts.notice(PARKED, PARKED_DETAIL);
    void Promise.all(parked.map(async ({ task, conversation, background }) => {
      await pi.abortTask(task, BG);
      if (background) {
        return;
      }
      await (await pi.conversation(conversation, BG))?.abort(BG);
    }))
      .catch(() => console.error("a parked task's run could not be ended"))  // no ids
      .finally(() => {
        slot.parked = false;
      });
  }

  /**
   * A Session began closing: on its own, as a storage error makes it, or by the host. Its
   * store's session is released once it closed.
   */
  #piClosing(thread: string, slot: Slot, opening: Promise<Opened>, opened: Opened,
             closed: Promise<unknown>): void {
    const { pi } = opened;
    this.#closing.add(pi);
    void closed.catch(() => undefined)
      .then(() => opened.storage.close(BG))
      .catch(() => console.error("a closed Session's store could not be released"));
    slot.changed?.();
    if (slot.opening !== opening) {
      return;
    }
    slot.opening = undefined;
    slot.opened = undefined;
    slot.lastClose = closed.catch(() => undefined);
    void slot.lastClose.then(() => this.#forget(thread, slot));
  }

  /** Close a slot's Session; resolves once it closed (running task code returned). */
  #closeSlot(slot: Slot): Promise<void> {
    const opening = slot.opening;
    if (opening === undefined) {
      return slot.lastClose.then(() => undefined);
    }
    const thread = [...this.#slots].find(([, held]) => held === slot)?.[0];
    const wake = slot.opened?.pi.wake();
    if (thread !== undefined && wake !== undefined && !wake.active && wake.at === undefined) {
      // Nothing runs and nothing sleeps: the thread's job is stale (one a heartbeat left
      // due), and is cancelled, so it never reopens the Session, taking a place among the
      // open, only to find nothing. A job with work to wake for is kept.
      this.lifecycle.jobs.cancel(wakeJob(thread))
        .catch(() => console.error("a thread's stale wake could not be cancelled"));  // no ids
    }
    slot.opening = undefined;
    slot.opened = undefined;
    const closing = opening.then(async ({ pi, storage }) => {
      this.#closing.add(pi);
      try {
        if (thread !== undefined) {
          await this.#keepUsage(thread, pi);
        }
        await pi.close(BG);
      } finally {
        await storage.close(BG);  // its store's session, released
      }
    }).catch(() => undefined);
    slot.lastClose = closing;
    slot.changed?.();
    void closing.then(() => {
      if (thread !== undefined) {
        this.#forget(thread, slot);
      }
    });
    return closing;
  }

  /**
   * Keep `thread`'s usage as its Session closes, so the owner's usage view reads rows, never
   * a closed Session. One that cannot be kept is told loudly; the row keeps the last.
   */
  async #keepUsage(thread: string, pi: Harness): Promise<void> {
    try {
      await this.#parts.usage(thread, await pi.usage(BG));
    } catch {
      console.error("a thread's usage could not be kept");  // no ids
      this.#parts.notice(USAGE_UNKEPT, USAGE_UNKEPT_DETAIL);
    }
  }

  /** Drop a slot that holds nothing any more: no Session, no wait, no caller. */
  #forget(thread: string, slot: Slot): void {
    if (slot.opening === undefined && slot.waiting === undefined && slot.admitting === 0
        && slot.leases === 0 && slot.pins === 0 && this.#slots.get(thread) === slot) {
      this.#slots.delete(thread);
    }
  }

  /** Whether a slot's Session could be closed now without cutting anything off. */
  #idle(slot: Slot): boolean {
    return slot.opened !== undefined && slot.leases === 0 && slot.pins === 0
      && slot.admitting === 0 && slot.waiting === undefined && !slot.opened.pi.wake().active;
  }

  /** Close each Session idle for `idleCloseMs`. */
  #sweep(): void {
    const now = Date.now();
    for (const slot of this.#slots.values()) {
      if (this.#idle(slot) && now - slot.lastUsed >= this.#timing.idleCloseMs) {
        void this.#closeSlot(slot);
      }
    }
  }

  /**
   * Room for one more Session: past `openMax`, the least recently used idle one is closed;
   * with none idle, the open is refused at once, never waited on.
   */
  #room(): void {
    const open = [...this.#slots.values()].filter((slot) => slot.opening !== undefined);
    if (open.length < this.#timing.openMax) {
      return;
    }
    const idle = open.filter((slot) => this.#idle(slot))
      .sort((a, b) => a.lastUsed - b.lastUsed)[0];
    if (idle === undefined) {
      throw new Refused(SESSIONS_FULL);
    }
    void this.#closeSlot(idle);
  }

  // -- Waking ------------------------------------------------------------------------

  /**
   * Hand pi work inside the thread's wake. Its job is scheduled first, so if the object
   * dies after pi has the work, that job restarts it; while `hand` is in flight the step
   * will not complete. The job steps again after, so it sees the work even if it just
   * completed.
   */
  async #admit<T>(thread: string, slot: Slot, hand: () => Promise<T>): Promise<T> {
    slot.admitting++;
    let result: T;
    try {
      await this.#wake(thread);
      result = await hand();
    } finally {
      slot.admitting--;
    }
    await this.#wake(thread);
    return result;
  }

  /** Schedule `thread`'s wake job now, or at `time`. */
  #wake(thread: string, time: number = Date.now()): Promise<unknown> {
    return this.lifecycle.jobs.push({ id: wakeJob(thread), fn: WAKE_FN, time,
                                      singleflight: true, recoveryLoop: true });
  }

  /**
   * Schedule the thread's job for `wake`, if it needs one, and then keep `wake` in the
   * owner's rows: the job first, so a row that fails to land leaves a job that finds the
   * work itself, and never a live row with no job to wake it.
   */
  async #wakeFor(thread: string, wake: Wake, opened: Opened, slot: Slot): Promise<void> {
    if (wake.active) {
      await this.#wake(thread);
    } else if (wake.at !== undefined) {
      await this.#wake(thread, wake.at);
    }
    // The wake as it stands once the job is in place, not as it stood before: a Session
    // whose work ended meanwhile (an open's reconcile pass) never has a stale "running"
    // kept after its own later report.
    await this.#parts.live(thread, opened.pi.wake(), opened, quietOf(slot));
  }

  /** A Session's `onWake`: off its line, never blocking. */
  #onWake(thread: string, slot: Slot, wake: Wake): void {
    slot.lastUsed = Date.now();
    slot.changed?.();
    const { opened } = slot;
    const kept = opened === undefined ? this.#parts.live(thread, wake)
      : slot.waiting !== undefined ? this.#parts.live(thread, wake, opened, quietOf(slot))
        : this.#wakeFor(thread, wake, opened, slot);
    kept.catch(() => console.error("a thread's wake could not be kept"));  // no ids
  }

  /**
   * One step of `thread`'s wake job. It never admits or replays anything: it reads the
   * Session's `wake()`. Running work is waited on; a sleep reschedules the job for when it
   * ends; no work completes the job. A thread with no row (dropped) is never opened again,
   * and its job completes.
   */
  async #step(thread: string): Promise<LifecycleJobOutcome> {
    const heartbeat = { rescheduleAt: Date.now() + this.#timing.heartbeatMs };
    const held = this.#slots.get(thread);
    if (held?.waiting !== undefined || (held?.ending ?? 0) > 0) {
      return heartbeat;
    }
    if (!this.#parts.exists(thread)) {
      return undefined;
    }
    const { opened, slot } = await this.#open(thread);
    const { pi } = opened;
    this.#release(slot);
    pi.resume();
    const wake = pi.wake();
    if (wake.active) {
      const wait = this.#waitWhileActive(slot, pi).then((ended) => {
        slot.waiting = undefined;
        if (ended) {
          this.#wake(thread).catch(() => console.error("a thread's wake could not be set"));
        }
      }, (error: unknown) => {
        slot.waiting = undefined;
        throw error;
      });
      slot.waiting = wait;
      if (!this.lifecycle.trackAlarmWork(wait)) {
        wait.catch(() => {});
      }
      return heartbeat;
    }
    if (slot.admitting > 0) {
      return heartbeat;
    }
    if (wake.at !== undefined) {
      return { rescheduleAt: wake.at };
    }
    // Nothing runs and nothing sleeps: the live row is rewritten from the Session (a stale
    // "running" one cleared, a run that settled kept so) before the job completes; a write
    // whose read does not answer within a heartbeat leaves the job to try again.
    const kept = this.#parts.live(thread, wake, opened, quietOf(slot))
      .then(() => true, () => false);
    if (await inTime(kept, this.#timing.heartbeatMs) !== true) {
      console.error("a thread's wake could not be kept; its job tries again");  // no ids
      return heartbeat;
    }
    return undefined;
  }

  /**
   * Wait while the Session has work running, up to the wait budget. A change of its wake,
   * or its closing, ends each sleep early. A task sleeping beside the running work (a retry
   * backoff) also ends the sleep when it is due, and the wait resumes the Session to run
   * it: with `onWake`, pi arms no timer for it, and the job's alarm is not set for it while
   * the job waits. Whether the wait ended, idle or at the budget, rather than failing.
   * Rejects with a memory-limit reset, so the alarm's breaker counts it.
   */
  async #waitWhileActive(slot: Slot, pi: Harness): Promise<boolean> {
    const deadline = Date.now() + this.#timing.waitBudgetMs;
    try {
      let wake = pi.wake();
      while (wake.active) {
        const now = Date.now();
        const left = deadline - now;
        if (left <= 0) {
          return true;
        }
        const sleep = wake.at === undefined ? left : Math.min(left, wake.at - now);
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, sleep);
          slot.changed = () => {
            clearTimeout(timer);
            resolve();
          };
        });
        slot.changed = undefined;
        if (wake.at !== undefined && Date.now() >= wake.at) {
          pi.resume();
        }
        wake = pi.wake();
      }
      return true;
    } catch (error) {
      if (!this.#closing.has(pi)) {
        console.error("a thread's wake failed while it waited");  // no ids
      }
      if (memoryLimitReset(error)) {
        throw error;
      }
      return false;
    }
  }

  /** An operation's result from its settled record: an answer's text once done. */
  async #result(pi: Harness, operationId: string, settled: SettledSubmissionRecord,
                context: Context): Promise<OperationResult> {
    if (settled.status === "unanswered") {
      return { operationId, status: "unanswered", reason: settled.reason };
    }
    if (settled.type !== "input") {
      return { operationId, status: "done" };
    }
    const conversation = await pi.conversation(ROOT, context);
    const answer = (await conversation?.entries({ minEntryId: settled.answer,
                                                  maxEntryId: settled.answer }, 1, undefined,
                                                context))?.items[0];
    return { operationId, status: "done", text: assistantText(answer) };
  }
}

/** A thread's count of wake failures in a row, kept across restarts. */
function errorsKey(thread: string): string {
  return `${HOST}:wake-errors:${thread}`;
}

/**
 * One pi record that owns a turn, in a thread: an input submission (`s:<id>`) or a
 * compaction task (`t:<id>`), and its turn while it is open, null once it is not.
 */
export interface TurnOwner {
  readonly owner: string;
  readonly turn: string | null;
  /**
   * An owner that joined another's turn (a linked input, a run's compaction): it keeps
   * the turn, never makes it again.
   */
  readonly joined?: boolean;
}

/** Whether `slot`'s thread has no input being admitted, read in a settling unit. */
function quietOf(slot: Slot): () => boolean {
  return () => slot.admitting === 0;
}

/** A commit's change, as pi publishes it. */
type Change = Parameters<Parameters<Harness["subscribeCommits"]>[0]>[0]["changes"][number];

/** An input submission's turn, the origin its admission's carrier holds, and if it joined it. */
function originOf(trace: unknown): { turn: string; joined: boolean } | undefined {
  const carrier = typeof trace === "object" && trace !== null
    ? trace as Record<string, unknown> : {};
  const origin = carrier["ta.origin"];
  return typeof origin === "string" ? { turn: origin, joined: carrier[JOINED] === "1" }
    : undefined;
}

/**
 * The turn owners a commit opened or ended in `thread`: each input submission while it is
 * queued or placed, and each compaction task while it is live, with its turn; with none
 * once it settled or ended. A compaction a run made joins the run's turn (its carrier's
 * origin, as its calls are charged); one made outside any run is its own.
 */
export function turnOwners(thread: string, changes: readonly Change[]): TurnOwner[] {
  const owners: TurnOwner[] = [];
  for (const change of changes) {
    if (change.type === "submission" && change.value.type === "input") {
      const origin = originOf(change.value.trace);
      if (origin !== undefined) {
        const open = change.value.status === "queued" || change.value.status === "placed";
        owners.push(open ? { owner: `s:${change.value.id}`, ...origin }
          : { owner: `s:${change.value.id}`, turn: null });
      }
    } else if (change.type === "task" && change.value.kind === COMPACTION) {
      const open = (LIVE_TASKS as readonly string[]).includes(change.value.state.status);
      const run = originOf(change.value.trace);
      owners.push(!open ? { owner: `t:${change.value.id}`, turn: null }
        : run === undefined ? { owner: `t:${change.value.id}`,
                                turn: `task:${thread}:${change.value.id}` }
          : { owner: `t:${change.value.id}`, turn: run.turn, joined: true });
    }
  }
  return owners;
}

/**
 * Keep `thread`'s turn owners from its commits, as `subscribe` publishes them, after first
 * reconciling them to `open`: a commit published before that is done is held, then kept
 * after it, so the reconcile never undoes it. Resolves once the reconcile is done. A
 * commit's owners that cannot be kept are `lost`, at once.
 */
export async function keepTurns(
  thread: string, subscribe: Harness["subscribeCommits"],
  turns: Pick<HostParts["turns"], "owned" | "reconciled">,
  open: () => Promise<readonly TurnOwner[]>, lost: () => void,
): Promise<void> {
  let held: TurnOwner[][] | undefined = [];
  const keep = (owners: readonly TurnOwner[]) => {
    turns.owned(thread, owners).catch(lost);
  };
  subscribe((publication) => {
    const owners = turnOwners(thread, publication.changes);
    if (owners.length === 0) {
      return;
    }
    if (held === undefined) {
      keep(owners);
    } else {
      held.push(owners);
    }
  });
  await turns.reconciled(thread, await open());
  const later = held;
  held = undefined;
  for (const owners of later) {
    keep(owners);
  }
}

/** Every page of a scan. */
async function scanned<T>(scan: (cursor: Cursor | undefined) => Promise<Page<T, Cursor>>):
    Promise<T[]> {
  const items: T[] = [];
  let cursor: Cursor | undefined;
  do {
    const page = await scan(cursor);
    items.push(...page.items);
    cursor = page.next;
  } while (cursor !== undefined);
  return items;
}

/** The input submissions a thread's store holds open. */
async function openSubmissions(storage: Storage) {
  return (await Promise.all((["queued", "placed"] as const).map((status) =>
    scanned((cursor) => storage.scanSubmissions({ status }, SCAN_PAGE, cursor, BG)))))
    .flat().filter((record) => record.type === "input");
}

/** Every turn owner a thread's store holds open: its open inputs and live compactions. */
async function openOwners(thread: string, storage: Storage): Promise<TurnOwner[]> {
  const submissions = await openSubmissions(storage);
  const compactions = (await Promise.all(LIVE_TASKS.map((status) =>
    scanned((cursor) => storage.scanTasks({ kind: COMPACTION, status }, SCAN_PAGE, cursor,
                                          BG))))).flat();
  return turnOwners(thread, [
    ...submissions.map((value) => ({ type: "submission" as const, value })),
    ...compactions.map((value) => ({ type: "task" as const, value })),
  ] as Change[]);
}
