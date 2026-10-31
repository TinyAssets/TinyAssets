/**
 * A scheduled launch is a pi task sleeping in its run's own Session (ADR-032 §4; tasks 5,
 * F1): `ta.scheduled`, installed with the object's own extensions (`agent.ts`), never a
 * lane of the owner's schedule. pi gives what the `work` lane hand-built: a phase that ends
 * asleep (`sleepUntil`) holds no invocation, survives evictions and restarts and resumes at
 * its checkpoint, an abort mark does not wait for a sleep, and a submission retried with
 * the same `requestId` is the same submission.
 *
 * - **Made at the launch** (`agentRun` with `at` or `cron`, and `ta`'s `agent:run`): the
 *   run's thread is recorded first, naming its store and its work (`threads.ts`), then the
 *   task is made in that thread's root, owned by the conversation, `background: true` (it
 *   is no run's work, so a conversation's ordinary abort leaves it) and with no telemetry
 *   carrier (`trace: "root"`: it holds no turn), its input the work's id, its thread, when
 *   it is due, the stamp the thread was made under, the input and its recurrence. The host
 *   makes it inside the thread's wake (`ThreadHost.admitted`): the thread's job is
 *   scheduled before the commit, so the task never sits with no job to wake it. This is
 *   the one place the owner's code makes a pi task (the create-task guard).
 * - **Phase `wait`** sleeps until `at`. The sleep's wake time reaches the thread's live row
 *   (its `wake_at`) through its Session's `onWake` and the host's per-thread job, so
 *   `ta`'s `wake:list`, retention and the run bounds read owner rows, never a store.
 * - **Phase `deliver`** runs in the object (`Delivery.deliver`, `OwnerAgent`): in one unit of
 *   the write gate it re-checks the stamp's standing and scope (`Grants.standing`, through
 *   the authority), the owner's run policy as it stands (a policy write leaves a sleeper
 *   alone, so its delivery is where the policy holds it) and the owner's admission
 *   (`require_work`); then it submits the input with `requestId` the occurrence's, and the
 *   entry `ta.work`, through the host's admission, which holds it to the thread's open
 *   inputs (`THREAD_INPUTS`) and keeps its turn as its own (a fresh origin, `origin.ts`).
 *   A delivery an eviction cut short after its submit is found by its request id and not
 *   re-checked: pi's dedupe makes it one submission. A thread or the object too full to
 *   take it (`thread_inputs_full`, `sessions_full`) sleeps again, backing off from
 *   `RETRY_FIRST_MS` to `RETRY_LAST_MS`, so the wake is never lost; a refusal ends the
 *   task terminal (`failed`, the refusal its message), which its status reports.
 * - **Recurrence** (`cron`, `cron.ts`): a delivery ends by sleeping until the next
 *   occurrence, each delivered under its own request id (`requestOf`), until the grant,
 *   the policy or the admission refuses one, or it is cancelled.
 * - **Cancel** is `abortTask`: the abort handler ends it `aborted`, at once from a sleep.
 *   A grant change drops the thread and wipes its store, the sleeper with it.
 */
import type { JsonValue } from "@earendil-works/chord";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type Conversation, defineTask, type Task, type TaskId, type TaskRecord,
} from "@earendil-works/pi-durable";

import type { Stamped } from "./authority";
import { nextOccurrence, parseCron } from "./cron";

/** The task's registered name. */
export const SCHEDULED = "ta.scheduled";
/** The entry a delivered input is placed as. */
export const WORK_ENTRY = "ta.work";
/** The first wait of a delivery that found no room, and the longest, in ms. */
export const RETRY_FIRST_MS = 30_000;
export const RETRY_LAST_MS = 10 * 60_000;
/** How a cancelled schedule's status reads. */
export const CANCELLED = "cancelled";

/** What a scheduled launch is made with: its task's input. */
export interface ScheduledInput {
  readonly work_id: string;
  /** The run thread it delivers into: the one its Session holds it in. */
  readonly thread: string;
  /** When its first delivery is due, in seconds. */
  readonly at: number;
  /** The authority its thread was made under; null for the owner's own. */
  readonly stamp: Stamped | null;
  readonly input: string;
  /** Its recurrence, a cron expression (`cron.ts`), or null for one delivery. */
  readonly cron: string | null;
}

/** Where a scheduled launch is: asleep until it is due, or delivering occurrence `n`. */
export type ScheduledState =
  | { readonly phase: "wait" }
  | { readonly phase: "deliver"; readonly n: number; readonly tries: number };

/** What a scheduled launch ends with: how many occurrences it delivered. */
export interface ScheduledResult {
  readonly deliveries: number;
}

/** How one delivery went. */
export type Delivered =
  | { readonly kind: "delivered" }
  /** The thread or the object had no room for it: it sleeps, and tries again. */
  | { readonly kind: "full" }
  /** A re-check refused it: the task ends with the reason. */
  | { readonly kind: "refused"; readonly reason: string };

/** What delivers an occurrence: the owner's agent, in the object (`OwnerAgent`). */
export interface Delivery {
  deliver(input: ScheduledInput, n: number): Promise<Delivered>;
}

/** The `ta.scheduled` task's type. */
export type ScheduledTask = Task<ScheduledInput, ScheduledState, ScheduledResult, object>;

/** The request id occurrence `n` of `workId` is submitted under: the first is the work's own. */
export function requestOf(workId: string, n: number): string {
  return n <= 1 ? workId : `${workId}.${n}`;
}

/** How long a delivery that found no room waits before its `tries`-th try again. */
export function retryDelay(tries: number): number {
  return Math.min(RETRY_FIRST_MS * 2 ** tries, RETRY_LAST_MS);
}

/** The deliveries a schedule made, from where it stands. */
function delivered(state: ScheduledState): number {
  return state.phase === "wait" ? 0 : state.n - 1;
}

/** The `ta.scheduled` task, delivering through `delivery`. */
export function scheduledTask(delivery: Delivery): ScheduledTask {
  return defineTask<ScheduledInput, ScheduledState, ScheduledResult>({
    name: SCHEDULED,
    version: 1,
    initial: () => ({ phase: "wait" }),
    phases: {
      wait: (task, runtime, context) => runtime.sleepUntil(
        task.input.at * 1000, { phase: "deliver", n: 1, tries: 0 }, context),
      deliver: async (task, runtime, context) => {
        const { n, tries } = task.state.checkpoint;
        const answer = await delivery.deliver(task.input, n);
        if (answer.kind === "full") {
          await runtime.sleepUntil(runtime.now() + retryDelay(tries),
                                   { phase: "deliver", n, tries: tries + 1 }, context);
          return;
        }
        if (answer.kind === "refused") {
          await runtime.commit(() => ({
            status: "terminal",
            outcome: { status: "failed", error: { message: answer.reason,
                                                  detail: { deliveries: n - 1 } } },
          }), context);
          return;
        }
        const cron = task.input.cron;
        const next = cron === null ? null
          : nextOccurrence(parseCron(cron), runtime.now() / 1000);
        await runtime.commit(() => (next === null
          ? { status: "terminal", outcome: { status: "completed", result: { deliveries: n } } }
          : { status: "sleeping", until: next * 1000,
              checkpoint: { phase: "deliver", n: n + 1, tries: 0 } }), context);
      },
    },
    abort: (task, runtime, context) => runtime.commit(() => ({
      status: "terminal",
      outcome: { status: "aborted", reason: CANCELLED,
                 result: { deliveries: delivered(task.state.checkpoint) } },
    }), context),
  });
}

/**
 * Make the scheduled launch `input` in `root`, its thread's root conversation, in one
 * commit: owned by the conversation, background work, and carrying no telemetry origin.
 * The one place the owner's code makes a pi task (the create-task guard).
 */
export function createScheduled(root: Conversation, task: ScheduledTask,
                                input: ScheduledInput): Promise<TaskId<ScheduledResult>> {
  return root.commit((tx) => tx.createTask(task, input, {
    ownership: { kind: "conversation" }, background: true, trace: "root",
  }), BACKGROUND_CONTEXT);
}

/** How a scheduled launch stands, from its task record alone. */
export interface Standing {
  /** How many occurrences it delivered. */
  readonly delivered: number;
  /** When it next delivers, in seconds; null while it delivers, or once it ended. */
  readonly due: number | null;
  /** How it ended, once it has: null for a completed one, else why. */
  readonly ended: { readonly reason: string | null } | null;
}

/** A scheduled launch's standing, from its task record (a store's pure read). */
export function standing(record: TaskRecord<JsonValue, JsonValue, JsonValue>): Standing {
  const input = record.input as unknown as ScheduledInput;
  const state = record.state;
  if (state.status === "terminal" || state.status === "completing") {
    const outcome = state.outcome;
    const result = (outcome.result ?? (outcome.status === "failed"
      ? outcome.error.detail : undefined)) as { deliveries?: unknown } | undefined;
    const deliveries = typeof result?.deliveries === "number" ? result.deliveries : 0;
    return { delivered: deliveries, due: null,
             ended: { reason: outcome.status === "completed" ? null
               : outcome.status === "failed" ? outcome.error.message
                 : outcome.status === "aborted" ? CANCELLED : outcome.status } };
  }
  const checkpoint = state.checkpoint as unknown as ScheduledState;
  const due = state.status === "sleeping" ? state.until / 1000
    : checkpoint.phase === "wait" ? input.at : null;
  return { delivered: delivered(checkpoint), due, ended: null };
}
