/**
 * Outside-agent wakes on the object's one alarm (S3b, W3; ADR-026 zero idle tail).
 *
 * Ported from `agent_wake` (`tinyassets/broker/message_ledger.py`) and `run_due`
 * (`tinyassets/control_plane/outside_wakes.py`). The Python ran one shared tick that
 * walked every owner's ledger with a fairness rotation; here each object is its own
 * owner and schedules itself with one self-rearming alarm (`OwnerDO.alarm`), so there is
 * no global tick and nothing to rotate.
 *
 * The op journal's may-have-sent record becomes the wake row's `marked` flag, written
 * by `mark()` before the doorbell's first byte. A send abandoned past its lease is
 * `unknown` if it was marked and is never replayed (the agent's next message opens its
 * next wake); an unmarked one is provably unsent and due again.
 *
 * The doorbell itself (the signed MCP event POST through the owner's egress) is a later
 * slice: it implements {@link WakeSender}. Until then the alarm recovers lapsed claims and
 * leaves open wakes pending, held, not lost.
 */
import { type Ledger, type Outcome, outcome, type WakeTarget } from "./ledger";

/** At most this many wakes per alarm run; a run with more due rearms at once. */
export const WAKES_PER_ALARM = 16;

/** What rings an outside agent's doorbell. IDs only: a target never holds a body (S31). */
export interface WakeSender {
  /**
   * Send one wake and report the answer. `mark()` must be called before the first byte
   * leaves; a throw after it leaves the wake to its lease, then `unknown`. A callback
   * refused before any connection is `failed` with reason `not_sent`.
   */
  send(target: WakeTarget, mark: () => void): Promise<Outcome>;
  /** Erase the agent's callbacks: its wake was suppressed (disabled, or off mcp_event). */
  revoke(agentId: string): Promise<void>;
}

/**
 * Send each due wake once, and recover each lapsed claim; return how many were accepted.
 *
 * One failing wake is logged and left to its ledger state; it never stops the others.
 */
export async function runWakes(ledger: Ledger, sender: WakeSender | null, clock: () => number,
                               limit: number = WAKES_PER_ALARM): Promise<number> {
  let accepted = 0;
  for (const wake of ledger.dueWakes(clock(), limit)) {
    try {
      if (wake.state === "sending") {
        ledger.recover(wake.wake_id, clock());
        continue;
      }
      if (sender === null) {
        continue;
      }
      const claim = ledger.claim(wake.wake_id, clock(), false);
      if (claim.outcome === "suppressed") {
        await sender.revoke(claim.agent_id);
      }
      if (claim.outcome !== "claimed") {
        continue;
      }
      const { target } = claim;
      const answer = await sender.send(target, () => {
        if (!ledger.mark(target.wake_id, target.claim_id)) {
          throw new Error("the wake's claim lapsed before its send");
        }
      });
      ledger.settle(target.wake_id, target.claim_id, outcome(answer), clock());
      accepted += answer.state === "sent" ? 1 : 0;
    } catch {
      console.error("outside-agent wake failed");  // no ids, URL or body in the log
    }
  }
  return accepted;
}
