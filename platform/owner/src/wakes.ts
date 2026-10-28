/**
 * Outside-agent wakes, the `wakes` lane of the object's one schedule (S3b, W3; ADR-026
 * zero idle tail).
 *
 * Ported from `agent_wake` (`tinyassets/broker/message_ledger.py`) and `run_due`
 * (`tinyassets/control_plane/outside_wakes.py`). The Python ran one shared tick that
 * walked every owner's ledger with a fairness rotation; here each object is its own
 * owner and schedules itself on its own Lifecycle job queue (`schedule.ts`), so there is
 * no global tick and nothing to rotate.
 *
 * The op journal's may-have-sent record becomes the wake row's `marked` flag, written
 * by `mark()` before the doorbell's first byte. A send abandoned past its lease is
 * `unknown` if it was marked and is never replayed (the agent's next message opens its
 * next wake); an unmarked one is provably unsent and due again.
 *
 * The doorbell itself (the signed MCP event POST through the owner's egress) is a later
 * slice (tasks 7): it implements {@link WakeSender}, which the object calls in process,
 * so no wake can be claimed or settled over RPC. Until then the lane only recovers
 * lapsed claims, and open wakes and revokes stay pending, held, not lost.
 */
import { type Ledger, type Outcome, outcome, type WakeTarget } from "./ledger";
import { inTime, LANE_HUNG_S, LANE_PASS_S } from "./schedule";

/** At most this many rows per pass per lane run; a run with more due runs again at once. */
export const WAKES_PER_ALARM = 16;
/** How long a row whose pass failed in process waits before the lane takes it again. */
export const WAKE_RETRY_S = 60;
/**
 * How long the lane waits on one send or revoke: what is left of the lane's hung timeout
 * once the pass stops taking rows, so a pass ends inside it whatever a sender does.
 */
export const WAKE_SEND_MS = (LANE_HUNG_S - LANE_PASS_S) * 1000;

/**
 * What rings an outside agent's doorbell. IDs only: a target never holds a body (S31). Any
 * service-binding call it makes draws on its alarm's ambient budget (`calls.ts`).
 */
export interface WakeSender {
  /**
   * Send one wake and report the answer. `mark()` must be called before the first byte
   * leaves; a throw after it leaves the wake to its lease, then `unknown`. A callback
   * refused before any connection is `failed` with reason `not_sent`.
   */
  send(target: WakeTarget, mark: () => void): Promise<Outcome>;
  /**
   * Erase the agent's callbacks tagged at or before `generation` (the subscription
   * generation each subscribe returned), and never a newer one: the agent was disabled,
   * moved off mcp_event or deleted, and may since have been re-enabled and subscribed
   * again. Erasing twice is harmless.
   */
  revoke(agentId: string, generation: number): Promise<void>;
}

/**
 * One lane pass; returns how many wakes were accepted.
 *
 * First the recover pass: each claim past its lease, by its own index, whether or not
 * there is a sender, so open work ahead of it can never starve it. Then, with a sender,
 * the claim pass: each due open row once, a wake sent or a revoke carried out, taking
 * none once the wall clock reaches `until` (`schedule.ts`, `LANE_PASS_S`). One
 * failing row (the sender's call, or the row's own step) is logged and backed off
 * `WAKE_RETRY_S` in its row, so it never stops the others, never makes the pass throw and
 * never loops the alarm. Only a failure to write that backoff, a storage fault, throws.
 *
 * The lane bounds each send and revoke itself, at `sendMs`, rather than trust every
 * sender to: a late one is left to its lease, as a late hand-off is (`scheduled.ts`). A
 * late send is marked first, since a byte may have left, so its lapsed claim recovers as
 * `unknown`, never sent again; a late revoke recovers due again, since erasing twice is
 * harmless.
 */
export async function runWakes(ledger: Ledger, sender: WakeSender | null, clock: () => number,
                               limit: number = WAKES_PER_ALARM,
                               sendMs: number = WAKE_SEND_MS,
                               until: number = Infinity): Promise<number> {
  for (const lapsed of ledger.lapsedClaims(clock(), limit)) {
    try {
      ledger.recover(lapsed.wake_id, clock());
    } catch {
      console.error("outside-agent wake recovery failed");  // no ids, URL or body in the log
      ledger.defer(lapsed.wake_id, clock() + WAKE_RETRY_S);
    }
  }
  if (sender === null) {
    return 0;
  }
  let accepted = 0;
  for (const wake of ledger.dueOpen(clock(), limit)) {
    if (Date.now() >= until) {
      break;  // the rest wait for the lane's next pass, which the job's set brings at once
    }
    try {
      const claim = ledger.claim(wake.wake_id, clock());
      if (claim.outcome === "revoke") {
        const revoking = sender.revoke(claim.target.agent_id, claim.target.generation);
        if (await inTime(revoking, sendMs) === "late") {
          revoking.catch(() => undefined);
          console.error("a doorbell revoke ran past its bound; it is left to its lease");
          continue;
        }
        ledger.settle(claim.target.wake_id, claim.target.claim_id, { state: "sent" }, clock());
        continue;
      }
      if (claim.outcome !== "claimed") {
        continue;
      }
      const { target } = claim;
      const sending = sender.send(target, () => {
        if (!ledger.mark(target.wake_id, target.claim_id)) {
          throw new Error("the wake's claim lapsed before its send");
        }
      });
      const answer = await inTime(sending, sendMs);
      if (answer === "late") {
        // A byte may have left: never send it again, and let its lease decide.
        sending.catch(() => undefined);
        ledger.mark(target.wake_id, target.claim_id);
        console.error("a doorbell send ran past its bound; it is left to its lease");
        continue;
      }
      ledger.settle(target.wake_id, target.claim_id, outcome(answer), clock());
      accepted += answer.state === "sent" ? 1 : 0;
    } catch {
      console.error("outside-agent wake failed");  // no ids, URL or body in the log
      ledger.defer(wake.wake_id, clock() + WAKE_RETRY_S);
    }
  }
  return accepted;
}
