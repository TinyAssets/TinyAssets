/**
 * Outside-agent wakes, the `wakes` lane of the object's one schedule (S3b, W3; ADR-026
 * zero idle tail), and the doorbell that rings them (slice W, tasks 7).
 *
 * Ported from `agent_wake` (`tinyassets/broker/message_ledger.py`), `run_due`
 * (`tinyassets/control_plane/outside_wakes.py`) and `send_wake`
 * (`tinyassets/broker/mcp_events.py`). The Python ran one shared tick that walked every
 * owner's ledger with a fairness rotation; here each object is its own owner and
 * schedules itself on its own Lifecycle job queue (`schedule.ts`), so there is no global
 * tick and nothing to rotate.
 *
 * The op journal's may-have-sent record becomes the wake row's `marked` flag, written
 * by `mark()` before the doorbell's first byte. A send abandoned past its lease is
 * `unknown` if it was marked and is never replayed (the agent's next change opens its
 * next wake); an unmarked one is provably unsent and due again.
 *
 * The doorbell ({@link Doorbell}) is the object's one {@link WakeSender}, which the object
 * constructs and the lane calls in process, so no wake can be claimed or settled over
 * RPC. It builds each subscription's request from its vendor's record (`wake-kinds.ts`):
 * ChatGPT's MCP Events, Claude's `/fire`, Grok's webhook routines; Muse has no push, so
 * nothing is sent to it. The object never holds a doorbell's secret: the gateway opens the
 * subscription's sealed blob, adds the record's auth and makes the send
 * (`gateway/src/doorbell.ts`). The outside kill switch is the platform's control record
 * (`c:control`, in the owner Worker's control namespace, `control-gate.ts`), read with the
 * doors' own rule: the lane asks `open()` once a pass, before it claims, and claims nothing
 * on closed, a failure or no answer.
 */
import { Refusal } from "../../contract/src/refusal";
import type { Write } from "./gate";
import {
  type Ledger, type LiveCallback, type Outcome, outcome, WAKE_LEASE_S, type WakeTarget,
} from "./ledger";
import { inTime, type Lane, LANE_HUNG_S, LANE_PASS_S } from "./schedule";
import {
  type Change, doorbellRequest, type WakeContent,
} from "../../contract/src/wake-kinds";
import type { RingAnswer, RingRequest } from "../../contract/src/gateway";
import { kindNamed } from "../../contract/src/wake-records";

/** At most this many rows per pass per lane run; a run with more due runs again at once. */
export const WAKES_PER_ALARM = 16;
/**
 * How long the lane holds its job when the kill switch is closed: the time a flip takes
 * to reach every location (KV's 60 s), so a sooner read sees the same record.
 */
export const WAKE_RETRY_S = 60;
/**
 * How long the lane waits on one send, and on the kill switch's answer: what is left of
 * the lane's hung timeout once the pass stops taking rows, so a pass ends inside it
 * whatever a sender does.
 */
export const WAKE_SEND_MS = (LANE_HUNG_S - LANE_PASS_S) * 1000;
/** What a pass answers when the kill switch was closed: it claimed nothing. */
export const CLOSED = "closed";

/** What rings an outside agent's doorbell. IDs only: a target never holds a body (S31). */
export interface WakeSender {
  /**
   * Whether wakes may go now: the outside kill switch on, and the maintenance gate
   * admitting this owner. A throw is closed.
   */
  open(): Promise<boolean>;
  /**
   * Send one wake and report the answer. `mark()` must have resolved before the first
   * byte leaves (it is a unit of the object's write gate, stored once it resolves); a
   * throw after it leaves the wake to its lease, then `unknown`. A callback refused before
   * any connection is `failed` with reason `not_sent`.
   */
  send(target: WakeTarget, mark: () => Promise<void>): Promise<Outcome>;
}

/**
 * One lane pass; returns how many wakes were accepted, or `CLOSED` when the kill switch
 * held every due row back.
 *
 * First the recover pass: each claim past its lease, by its own index, whether or not
 * there is a sender, so open work ahead of it can never starve it. Then, with a sender and
 * due rows, the kill switch, asked once (`open()`, bounded at `sendMs`): closed, failed or
 * late claims nothing. Then the claim pass: each due open row once, taking none once the
 * wall clock reaches `until` (`schedule.ts`, `LANE_PASS_S`). One failing row (the
 * sender's call, or the row's own step) is logged and backed off a lease in its row
 * (`WAKE_LEASE_S`, the lane's own smallest interval), so it never stops the others, never
 * makes the pass throw and never loops the alarm. Only a failure to write that backoff, a
 * storage fault, throws.
 *
 * The lane bounds each send itself, at `sendMs`, rather than trust every sender to: a late
 * one is marked first, since a byte may have left, and left to its lease, so its lapsed
 * claim recovers as `unknown`, never sent again. Every write (a recovery, a claim, a mark,
 * a settle, a backoff) is one unit through `write`, the object's gate (`gate.ts`), between
 * the sends, never the whole pass.
 */
export async function runWakes(ledger: Ledger, sender: WakeSender | null, clock: () => number,
                               write: Write, limit: number = WAKES_PER_ALARM,
                               sendMs: number = WAKE_SEND_MS,
                               until: number = Infinity): Promise<number | typeof CLOSED> {
  for (const lapsed of ledger.lapsedClaims(clock(), limit)) {
    try {
      await write(() => ledger.recover(lapsed.wake_id, clock()));
    } catch {
      console.error("outside-agent wake recovery failed");  // no ids, URL or body in the log
      await write(() => ledger.defer(lapsed.wake_id, clock() + WAKE_LEASE_S));
    }
  }
  if (sender === null) {
    return 0;
  }
  const due = ledger.dueOpen(clock(), limit);
  if (!due.length) {
    return 0;
  }
  const asking = sender.open().catch(() => false);
  if (await inTime(asking, sendMs) !== true) {
    return CLOSED;  // nothing claimed: the rows wait as they were
  }
  let accepted = 0;
  for (const wake of due) {
    if (Date.now() >= until) {
      break;  // the rest wait for the lane's next pass, which the job's set brings at once
    }
    try {
      const claim = await write(() => ledger.claim(wake.wake_id, clock()));
      if (claim.outcome !== "claimed") {
        continue;
      }
      const { target } = claim;
      const sending = sender.send(target, async () => {
        if (!await write(() => ledger.mark(target.wake_id, target.claim_id))) {
          throw new Error("the wake's claim lapsed before its send");
        }
      });
      const answer = await inTime(sending, sendMs);
      if (answer === "late") {
        // A byte may have left: never send it again, and let its lease decide.
        sending.catch(() => undefined);
        await write(() => ledger.mark(target.wake_id, target.claim_id));
        console.error("a doorbell send ran past its bound; it is left to its lease");
        continue;
      }
      await write(() => ledger.settle(target.wake_id, target.claim_id, outcome(answer),
                                      clock()));
      accepted += answer.state === "sent" ? 1 : 0;
    } catch {
      console.error("outside-agent wake failed");  // no ids, URL or body in the log
      await write(() => ledger.defer(wake.wake_id, clock() + WAKE_LEASE_S));
    }
  }
  return accepted;
}

/** One subscription's send through the gateway: its sealed blob, and the object's request. */
export type Ring = (subscription: string, version: number, blob: string,
                    previous: { readonly version: number; readonly blob: string } | null,
                    request: RingRequest) => Promise<RingAnswer>;

/** What the doorbell reads and calls: the object's, handed in by the object. */
interface DoorbellDeps {
  readonly ledger: Ledger;
  /** Whether an outside client's grant still stands (`Grants.live`), read at each send. */
  readonly live: (client: string) => boolean;
  /** Whether that client's grant reads its agent's command center (`read_graph`). */
  readonly reads: (client: string, center: string) => boolean;
  /** Whether any of these room collections changed past `after` (`Collections.touched`). */
  readonly touched: (rooms: readonly string[], after: number) => boolean;
  /** A notice for the owner (`notices.ts`), in a unit of its own. */
  readonly notice: (type: string, detail: string) => Promise<void>;
  readonly write: Write;
  readonly clock: () => number;
  /** Whether the owner is admitted (`require_work`): read at each use. */
  readonly admitted: () => boolean;
  /** The kill switch: whether the control record lets this owner's outside work go now. */
  readonly gate: () => Promise<boolean>;
  readonly ring: Ring;
}

/** What a wake says to one subscription: just the changes it follows. */
function wakeContent(target: WakeTarget, changes: readonly Change[]): WakeContent {
  const read = [
    ...(changes.includes("mail") || changes.includes("rooms") ? ["inbox_digest"] : []),
    ...(changes.includes("board") ? ["board_digest"] : []),
  ];
  return { agent_id: target.agent_id, command_center_id: target.command_center_id,
           changes, cursor: target.cursor, after: target.after, read };
}

/**
 * A 429's `Retry-After` as sent, in seconds (a delta, or an HTTP date); null when it is
 * unreadable or names no time after now (zero, a past date), so the wake backs off instead.
 */
export function retryAfterOf(value: string | null, now: number): number | null {
  if (value === null) {
    return null;
  }
  const wait = /^\d{1,15}$/.test(value.trim()) ? Number(value.trim())
    : Date.parse(value) / 1000 - now;
  return Number.isFinite(wait) && wait > 0 ? wait : null;
}

/** The mark a subscription is sent this wake under: its id and how many changes it holds. */
function rungOf(target: WakeTarget): string {
  return `${target.wake_id}.${target.fold}`;
}

/** One subscription's answer, as the wake's outcome ranks it. */
type Answered = { readonly status: number; readonly retry_after: number | null };

/**
 * The object's doorbell: one sender for every wake kind, from that kind's record, through
 * the gateway.
 *
 * A send reads its client's grant (it stands, and reads the agent's command center) and the
 * agent's live subscriptions, keeps those that follow the wake's changes (mail always; the
 * board if it follows the board; rooms if a room it names changed past the wake's cursor),
 * builds each one's request from its record, marks the row, then reads both again
 * immediately before the first byte: a revoke, a disable or a grant change that landed
 * meanwhile wins, and nothing is sent. No subscription left, or a grant that no longer
 * stands or reads, is `suppressed`: the agent catches up through its digests on its own
 * schedule. Each subscription is rung through the gateway, its blob resealed there when its
 * kid retired, and its outcome is its own: marked sent this wake before the byte, its answer
 * recorded, a 429 leaving it owed the wake again. The wake is `rate_limited` while any
 * subscription is owed it (for the longest 429's `Retry-After`; the next try rings only
 * those), else `unknown` if any send may have left without an answer, else `sent` if any
 * was accepted (2xx), else `failed` with the first status: a 410 or 413 keeps the
 * subscription (the MCP Events spec), and a status the record names `token_dead` leaves the
 * owner one notice that its pasted token no longer works, until it is pasted again.
 */
export class Doorbell implements WakeSender, Lane {
  readonly #deps: DoorbellDeps;

  constructor(deps: DoorbellDeps) {
    this.#deps = deps;
  }

  /**
   * The `wakes` lane's next work: lapsed claims, then due wakes and revokes; open rows count
   * only while the owner is admitted (`require_work`): a suspended owner's wait.
   */
  next(now: number): number | null {
    return this.#deps.ledger.nextDue(now, this.#deps.admitted());
  }

  /**
   * The `wakes` lane's pass, rung by this doorbell, handed the pass's end; the kill switch
   * closed claims nothing, and holds the job a retry out.
   */
  async run(_now: number, until: number): Promise<unknown> {
    const done = await runWakes(this.#deps.ledger, this, this.#deps.clock, this.#deps.write,
                                WAKES_PER_ALARM, WAKE_SEND_MS, until);
    return done === CLOSED ? { hold: this.#deps.clock() + WAKE_RETRY_S } : done;
  }

  async open(): Promise<boolean> {
    try {
      return await this.#deps.gate() === true;
    } catch (error) {
      console.error("the doorbell's kill switch could not be read; it is closed", error);
      return false;
    }
  }

  /** The changes `callback` follows of `target`'s. */
  #follows(callback: LiveCallback, target: WakeTarget): Change[] {
    return target.changes.filter((change) => change === "mail"
      || (change === "board" && callback.board)
      || (change === "rooms" && callback.rooms.length > 0
          && this.#deps.touched(callback.rooms, target.after)));
  }

  /**
   * The subscriptions to ring now, or why none: the grant, its reach, or none follows that
   * is still owed this wake.
   */
  #live(target: WakeTarget): LiveCallback[] | "grant" | "scope" | "no_callback" {
    const { ledger, live, reads, clock } = this.#deps;
    if (!live(target.client)) {
      return "grant";
    }
    if (!reads(target.client, target.command_center_id)) {
      return "scope";
    }
    const callbacks = ledger.callbacks(target.agent_id, clock())
      .filter((callback) => callback.rung !== rungOf(target)
              && this.#follows(callback, target).length > 0);
    return callbacks.length ? callbacks : "no_callback";
  }

  async send(target: WakeTarget, mark: () => Promise<void>): Promise<Outcome> {
    const found = this.#live(target);
    if (typeof found === "string") {
      return { state: "suppressed", reason: found };
    }
    let requests: { callback: LiveCallback; request: RingRequest }[];
    try {
      requests = found.map((callback) => {
        const kind = kindNamed(callback.kind);
        if (kind === null) {
          throw new Refusal("invalid", "this subscription's kind is gone");
        }
        return { callback, request: doorbellRequest(
          kind, callback.subscription_id, wakeContent(target, this.#follows(callback, target)),
          target.wake_id, target.at) };
      });
    } catch (error) {
      if (error instanceof Refusal && error.invalid) {
        return { state: "failed", reason: "not_sent" };
      }
      throw error;
    }
    await mark();
    // Immediately before the first byte: a revoke, disable or grant change since wins.
    const again = this.#live(target);
    if (typeof again === "string") {
      return { state: "failed", reason: "not_sent" };
    }
    const still = new Set(again.map((callback) => callback.subscription_id));
    const sending = requests.filter(({ callback }) => still.has(callback.subscription_id));
    const wake = rungOf(target);
    const { ledger, write } = this.#deps;
    await write(() => ledger.rung(sending.map(({ callback }) => callback.subscription_id), wake));
    const answers: Answered[] = [];
    let unknown = false;
    for (const { callback, request } of sending) {
      let rung: RingAnswer;
      try {
        rung = await this.#deps.ring(callback.subscription_id, callback.version, callback.blob,
                                     callback.previous, request);
      } catch {
        unknown = true;  // the gateway's answer was lost: a byte may have left
        continue;
      }
      if (rung.state === "unknown") {
        unknown = true;
        continue;
      }
      if (rung.state === "refused") {
        continue;  // never sent: its blob or URL no longer passes
      }
      if (rung.resealed !== undefined) {
        const { blob, kid, version } = rung.resealed;
        await this.#deps.write(() => this.#deps.ledger.resealed(callback.subscription_id, blob,
                                                                 kid, version));
      }
      const status = rung.status;
      await write(() => ledger.answered(callback.subscription_id, wake, status));
      answers.push({ status, retry_after: retryAfterOf(rung.retry_after, this.#deps.clock()) });
      const dead = kindNamed(callback.kind)?.push?.token_dead ?? [];
      if (callback.principal === "owner" && dead.includes(status)
          && !dead.includes(callback.status ?? 0)) {
        await this.#deps.notice("doorbell_token_refused",
                                `${target.agent_id}: ${callback.kind} answered ${rung.status}; ` +
                                "paste the token the vendor shows now");
      }
    }
    const accepted = answers.find((answer) => answer.status >= 200 && answer.status < 300);
    const limited = answers.filter((answer) => answer.status === 429);
    if (limited.length) {
      const waits = limited.map((answer) => answer.retry_after);
      return { state: "rate_limited", status: 429, accepted: accepted !== undefined,
               retry_after: waits.includes(null) ? null
                 : Math.max(...waits.map((wait) => wait ?? 0)) };
    }
    if (unknown) {
      return { state: "unknown" };
    }
    if (accepted !== undefined) {
      return { state: "sent", status: accepted.status };
    }
    const first = answers[0];
    return first === undefined ? { state: "failed", reason: "not_sent" }
      : { state: "failed", status: first.status };
  }
}
