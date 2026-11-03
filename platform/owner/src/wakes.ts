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
 * RPC. It sends every vendor's wake from that vendor's record (`wake-kinds.ts`): ChatGPT's
 * MCP Events, Claude's `/fire`, Grok's webhook routines; Muse has no push, so nothing is
 * sent to it. The outside kill switch reaches it through `DOORBELL`, the
 * `tinyassets-doorbell` Worker, which reads the directory's `c:control` record: the lane
 * asks `open()` once a pass, before it claims, and claims nothing on closed or a failure.
 */
import { Invalid } from "./errors";
import type { Write } from "./gate";
import { type Ledger, type Outcome, outcome, type WakeTarget } from "./ledger";
import { inTime, LANE_HUNG_S, LANE_PASS_S } from "./schedule";
import { type DoorbellRequest, doorbellRequest, kindNamed, type WakeContent } from "./wake-kinds";

/** At most this many rows per pass per lane run; a run with more due runs again at once. */
export const WAKES_PER_ALARM = 16;
/**
 * How long a row whose pass failed in process waits before the lane takes it again, and
 * how long the lane holds its job when the kill switch is closed (a flip reaches every
 * location within KV's 60 s).
 */
export const WAKE_RETRY_S = 60;
/**
 * How long the lane waits on one send or revoke, and on the kill switch's answer: what is
 * left of the lane's hung timeout once the pass stops taking rows, so a pass ends inside
 * it whatever a sender does.
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
  /**
   * Erase the agent's callbacks tagged at or before `generation` (the subscription
   * generation each subscribe returned), and never a newer one: the agent was disabled,
   * moved to another delivery method or deleted, and may since have been re-enabled and
   * subscribed again. Erasing twice is harmless.
   */
  revoke(agentId: string, generation: number): Promise<void>;
}

/**
 * One lane pass; returns how many wakes were accepted, or `CLOSED` when the kill switch
 * held every due row back.
 *
 * First the recover pass: each claim past its lease, by its own index, whether or not
 * there is a sender, so open work ahead of it can never starve it. Then, with a sender and
 * due rows, the kill switch, asked once (`open()`, bounded at `sendMs`): closed, failed or
 * late claims nothing. Then the claim pass: each due open row once, a wake sent or a
 * revoke carried out, taking none once the wall clock reaches `until` (`schedule.ts`,
 * `LANE_PASS_S`). One failing row (the sender's call, or the row's own step) is logged and
 * backed off `WAKE_RETRY_S` in its row, so it never stops the others, never makes the pass
 * throw and never loops the alarm. Only a failure to write that backoff, a storage fault,
 * throws.
 *
 * The lane bounds each send and revoke itself, at `sendMs`, rather than trust every
 * sender to: a late one is left to its lease. A
 * late send is marked first, since a byte may have left, so its lapsed claim recovers as
 * `unknown`, never sent again; a late revoke recovers due again, since erasing twice is
 * harmless. Every write (a recovery, a claim, a mark, a settle, a backoff) is one unit
 * through `write`, the object's gate (`gate.ts`), between the sends, never the whole pass.
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
      await write(() => ledger.defer(lapsed.wake_id, clock() + WAKE_RETRY_S));
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
      if (claim.outcome === "revoke") {
        const revoking = sender.revoke(claim.target.agent_id, claim.target.generation);
        if (await inTime(revoking, sendMs) === "late") {
          revoking.catch(() => undefined);
          console.error("a doorbell revoke ran past its bound; it is left to its lease");
          continue;
        }
        await write(() => ledger.settle(claim.target.wake_id, claim.target.claim_id,
                                        { state: "sent" }, clock()));
        continue;
      }
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
      await write(() => ledger.defer(wake.wake_id, clock() + WAKE_RETRY_S));
    }
  }
  return accepted;
}

/** The kill switch's Worker, as the owner Worker binds it (`DOORBELL`). */
export interface DoorbellService {
  /** Whether the directory's control record lets outside work for `owner` go now. */
  open(owner: string): Promise<boolean>;
}

/** How a doorbell request leaves: production's global `fetch`, a recording fake in tests. */
export type Transport = (request: Request) => Promise<Response>;

/** What the doorbell reads and calls: the object's, handed in by the object. */
export interface DoorbellDeps {
  readonly ledger: Ledger;
  /** Whether an outside client's grant still stands (`Grants.live`), read at each send. */
  readonly live: (client: string) => boolean;
  readonly write: Write;
  readonly clock: () => number;
  /** The kill switch: `DOORBELL.open(owner)`; closed when the binding is absent. */
  readonly gate: () => Promise<boolean>;
  readonly transport: Transport;
}

/** What a wake says, and the digests that answer just its change. */
export function wakeContent(target: WakeTarget): WakeContent {
  const read = [
    ...(target.changes.includes("mail") || target.changes.includes("rooms")
      ? ["inbox_digest"] : []),
    ...(target.changes.includes("board") ? ["board_digest"] : []),
  ];
  return { agent_id: target.agent_id, command_center_id: target.command_center_id,
           changes: target.changes, cursor: target.cursor, after: target.after, read };
}

/** A 429's `Retry-After`, in seconds: a delta or an HTTP date; null when unreadable. */
function retryAfterOf(value: string | null, now: number): number | null {
  if (value === null) {
    return null;
  }
  if (/^\d{1,9}$/.test(value.trim())) {
    return Number(value.trim());
  }
  const at = Date.parse(value);
  return Number.isNaN(at) ? null : Math.max(0, at / 1000 - now);
}

/**
 * The object's doorbell: one sender for every wake kind, from that kind's record.
 *
 * A send reads its client's grant and the agent's live callback (`Ledger.callback`: stored
 * past the agent's newest scheduled revoke, for its current kind, unexpired), builds the
 * record's request (refused before any byte, `failed` `not_sent`, when the callback no
 * longer passes the record), marks the row, then reads both again immediately before the
 * byte: a revoke, a disable or a grant change that landed meanwhile wins, and nothing is
 * sent. No callback, or a grant that no longer stands, is `suppressed`: the agent catches
 * up through its digests on its own schedule. The request never follows a redirect, and
 * its answer's body is never read. A transport failure after the mark is `unknown`, never
 * replayed; a 2xx is `sent`; a 429 `rate_limited`, with its `Retry-After`; a status the
 * record names `gone` erases the callback; anything else is `failed` with its status.
 */
export class Doorbell implements WakeSender {
  readonly #deps: DoorbellDeps;

  constructor(deps: DoorbellDeps) {
    this.#deps = deps;
  }

  async open(): Promise<boolean> {
    try {
      return await this.#deps.gate() === true;
    } catch (error) {
      console.error("the doorbell's kill switch could not be read; it is closed", error);
      return false;
    }
  }

  async send(target: WakeTarget, mark: () => Promise<void>): Promise<Outcome> {
    const { ledger, live, clock } = this.#deps;
    const now = clock();
    if (!live(target.client)) {
      return { state: "suppressed", reason: "grant" };
    }
    const callback = ledger.callback(target.agent_id, now);
    const kind = callback === null ? null : kindNamed(callback.kind);
    if (callback === null || kind === null || kind.push === null) {
      return { state: "suppressed", reason: "no_callback" };
    }
    let request: DoorbellRequest;
    try {
      request = doorbellRequest(kind, callback, wakeContent(target), target.wake_id, now);
    } catch (error) {
      if (error instanceof Invalid) {
        return { state: "failed", reason: "not_sent" };
      }
      throw error;
    }
    await mark();
    // Immediately before the byte: a revoke, disable or grant change since wins.
    const again = ledger.callback(target.agent_id, clock());
    if (!live(target.client) || again === null || again.generation !== callback.generation) {
      return { state: "failed", reason: "not_sent" };
    }
    let status: number;
    let retry: string | null;
    try {
      const response = await this.#deps.transport(new Request(request.url, {
        method: "POST", headers: request.headers, body: request.body, redirect: "manual",
      }));
      status = response.status;
      retry = response.headers.get("Retry-After");
      await response.body?.cancel();
    } catch {
      return { state: "unknown" };  // a byte may have left: never replayed
    }
    if (status >= 200 && status < 300) {
      return { state: "sent", status };
    }
    if (status === 429) {
      return { state: "rate_limited", status, retry_after: retryAfterOf(retry, clock()) };
    }
    if (kind.push.gone.includes(status)) {
      await this.#deps.write(() => ledger.eraseCallbacks(target.agent_id, callback.generation));
      return { state: "failed", status, reason: "gone" };
    }
    return { state: "failed", status };
  }

  async revoke(agentId: string, generation: number): Promise<void> {
    const { ledger, write } = this.#deps;
    await write(() => ledger.eraseCallbacks(agentId, generation));
  }
}
