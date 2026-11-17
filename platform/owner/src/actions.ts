/**
 * The owner's action rules decided (R5 slice 2; d3 §N2, §N4; `r5_research.md` §3, §5.2): the
 * one decision, the calls it holds for the owner, the owner's answers and their receipts.
 *
 * - **The decision** (`decide`) is of an agent's call: a run's (its agent, and the client that
 *   started it, if one did) or an outside client's (`decidedActor`). The owner itself is never
 *   decided: the rules are the owner's on its agents. The rules on the call's act that hold
 *   for its actor are read from the policy at the call (`ruleOn`: a rule naming an `agent`
 *   holds for that agent's runs alone), and the strictest wins: `never` refuses
 *   (`action_never`), `ask` holds the call for the owner (`held_for_owner`, the card's id in
 *   the refusal's facts) unless the owner said yes to this very call, and no rule lets it be.
 * - **A held call is one row of `action_requests`**: its card, what the owner is shown (the
 *   act, the door, the actor and the request as asked, cut to `shownChars`), the card's
 *   digest, which the owner's answer names, and the call's, which a yes admits once: the
 *   door, the request as asked (its bytes by their digest) and the actor. The same call held
 *   again answers the same card. A yes keeps the row as the call's once-approval for
 *   `approvalTtlS`, which its one use deletes; a no deletes it.
 * - **Generation-bound**: a row asked under another policy generation is neither shown,
 *   answered nor admitted, so a change of the owner's rules decides every held call afresh.
 * - **Bounded and swept**: `held` rows in all, `perClient` of them one outside client's; at
 *   each decision and each answer, rows past their life (`cardTtlS` pending, `approvalTtlS`
 *   approved) and rows of another generation go first.
 * - **Receipted** in a bucket of their own, `action_receipts`, kept to the policy's
 *   `receiptsPerBucket` as its other buckets are, only where a rule decided: `denied`, `held`
 *   (once a card), `admitted`, and the owner's `approved` and `refused`. A call no rule
 *   decides writes nothing.
 *
 * Every change runs in one `transactionSync`; a refusal is thrown once what it receipts is
 * written, in the caller's unit, so the card and its receipt stand.
 */
import { actNamed } from "../../contract/src/acts";
import { canonical } from "../../contract/src/canonical";
import { sha256Hex } from "../../contract/src/digest";
import { randomHex } from "../../contract/src/ids";
import {
  type ActionRule, policyDigest, type PolicyDocument, POLICY_LIMITS,
} from "../../contract/src/policy-document";
import { Refusal } from "../../contract/src/refusal";
import { clock } from "./clock";
import { type Account, type Call, type LiveSession, proven } from "./door";
import type { OwnerStorage } from "./gate";
import type { Notices } from "./notices";

/**
 * How many calls wait, and how long. A pending card waits 30 days: Claude Code keeps a
 * session that long (`cleanupPeriodDays`), which bounds the resume an answer starts (P-resume).
 * A yes admits its call within a day: a main agent retries when its user says so. 200 rows in
 * all, pending or approved, and 50 of them one outside client's, so no client fills the set
 * the owner's own agents ask in; each card shows at most 8,000 code points of its request.
 */
interface ActionLimits {
  readonly held: number;
  readonly perClient: number;
  readonly cardTtlS: number;
  readonly approvalTtlS: number;
  readonly shownChars: number;
}

export const ACTION_LIMITS: ActionLimits = Object.freeze({
  held: 200,
  perClient: 50,
  cardTtlS: 30 * 86_400,
  approvalTtlS: 86_400,
  shownChars: 8_000,
});

/** Who a decided call is: one of the owner's agents' runs, or an outside client. */
export interface Actor {
  /** The agent a run acts as; null for an outside client's own call. */
  readonly agent: string | null;
  /** The outside client the call is under: its own, or the one that started the run. */
  readonly client: string | null;
}

/**
 * The actor of `account`'s call, or null for the owner itself and for another owner visiting
 * (origin null, not a run): neither is one of the owner's agents.
 */
export function decidedActor(account: Account): Actor | null {
  if (account.run !== null) {
    return { agent: account.run.agent, client: account.origin?.client ?? null };
  }
  return account.origin === null ? null : { agent: null, client: account.origin.client };
}

/** The strictest of `document`'s rules on `act` that hold for `actor`, or null for none. */
export function ruleOn(document: PolicyDocument, act: string, actor: Actor):
    ActionRule["do"] | null {
  const holding = (document.actions ?? []).filter((rule) => rule.act === act
    && (rule.agent === undefined || rule.agent === actor.agent));
  return holding.some((rule) => rule.do === "never") ? "never"
    : holding.length > 0 ? "ask" : null;
}

/** What a call asks: the door it came to, and its request as asked. */
export interface Asked {
  readonly door: string;
  readonly request: unknown;
}

/** A held call, as the owner is shown it (`actionRequest`). */
export interface ActionCard {
  readonly card_id: string;
  readonly act: string;
  /** The act's words (`acts.json`). */
  readonly says: string;
  readonly door: string;
  readonly agent: string | null;
  readonly client: string | null;
  /** The request as asked, canonical JSON, its bytes as their text; `cut` past `shownChars`. */
  readonly request: string;
  readonly cut: boolean;
  readonly generation: number;
  readonly filed_at: number;
  readonly expires_at: number;
}

/** A decision a rule made, or the owner's answer to a held call, as `policyReceipts` lists it. */
export type ActionReceipt = {
  readonly at: number;
  readonly act: string;
  readonly decision: ActionRule["do"];
  readonly outcome: "denied" | "held" | "admitted" | "approved" | "refused";
  readonly call_digest: string;
  readonly card_id: string | null;
  readonly agent: string | null;
  readonly client: string | null;
  readonly generation: number;
};

/** The policy as the decision reads it: its generation and its document. */
type PolicyRead = () => { readonly generation: number; readonly document: PolicyDocument };

const CARD_ID = /^act_[0-9a-f]{32}$/;

/**
 * `value` as JSON holds it, each of its bytes (`Uint8Array`, a file's content) as `bytes`
 * makes them: their text for the card, their digest for the call's own digest.
 */
function asJson(value: unknown, bytes: (value: Uint8Array) => string): unknown {
  if (value instanceof Uint8Array) {
    return bytes(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => asJson(item, bytes));
  }
  return typeof value === "object" && value !== null
    ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, asJson(item, bytes)]))
    : value;
}

type Row = {
  card_id: string; digest: string; call_digest: string; card: string; client: string | null;
  generation: number; filed_at: number; approved_at: number | null;
};

export class Actions {
  readonly #storage: OwnerStorage;
  readonly #policy: PolicyRead;
  readonly #notices: Notices;

  constructor(storage: OwnerStorage, policy: PolicyRead, notices: Notices) {
    this.#storage = storage;
    this.#policy = policy;
    this.#notices = notices;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** Whether any rule names `act`: only then is a call of it decided at all. */
  ruled(act: string): boolean {
    return (this.#policy().document.actions ?? []).some((rule) => rule.act === act);
  }

  /** Rows past their life, and rows asked under another generation, deleted. */
  #sweep(generation: number, now: number): void {
    this.#sql.exec("DELETE FROM action_requests WHERE generation != ? OR (approved_at IS NULL " +
                     "AND filed_at < ?) OR approved_at < ?", generation,
                   now - ACTION_LIMITS.cardTtlS, now - ACTION_LIMITS.approvalTtlS);
  }

  /** One receipt; the bucket keeps its newest `receiptsPerBucket`. */
  #receipt(receipt: ActionReceipt): void {
    const sql = this.#sql;
    sql.exec("INSERT INTO action_receipts (at, act, decision, outcome, call_digest, card_id, " +
               "agent, client, generation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", receipt.at,
             receipt.act, receipt.decision, receipt.outcome, receipt.call_digest,
             receipt.card_id, receipt.agent, receipt.client, receipt.generation);
    const over = sql.exec<{ n: number }>("SELECT action_receipts AS n FROM policy_usage").one().n
      - POLICY_LIMITS.receiptsPerBucket;
    if (over > 0) {
      sql.exec("DELETE FROM action_receipts WHERE seq IN (SELECT seq FROM action_receipts " +
                 "ORDER BY seq LIMIT ?)", over);
    }
  }

  /** The last `receiptsListed`, newest first. */
  receipts(): ActionReceipt[] {
    return this.#sql.exec<ActionReceipt>(
      "SELECT at, act, decision, outcome, call_digest, card_id, agent, client, generation " +
        "FROM action_receipts ORDER BY seq DESC LIMIT ?", POLICY_LIMITS.receiptsListed,
    ).toArray();
  }

  /**
   * In the caller's unit: `actor`'s call of `act` decided. Returns when it may be done;
   * throws `action_never` when a rule never lets it be, and `held_for_owner` when it waits for
   * the owner's yes, its card filed (once), receipted and noticed.
   */
  decide(act: string, actor: Actor, asked: Asked, now: number): void {
    const { generation, document } = this.#policy();
    const rule = ruleOn(document, act, actor);
    if (rule === null) {
      return;
    }
    const request = canonical(asJson(asked.request, (bytes) => new TextDecoder().decode(bytes)));
    const call = policyDigest({
      door: asked.door, agent: actor.agent, client: actor.client,
      request: asJson(asked.request, (bytes) => `sha256:${sha256Hex(bytes)}`) });
    const receipt = (outcome: ActionReceipt["outcome"], cardId: string | null) => this.#receipt({
      at: now, act, decision: rule, outcome, call_digest: call, card_id: cardId,
      agent: actor.agent, client: actor.client, generation });
    if (rule === "never") {
      this.#storage.transactionSync(() => receipt("denied", null));
      throw new Refusal("action_never",
                        `the owner's rule never lets an agent do this (${act})`);
    }
    const sql = this.#sql;
    const held = this.#storage.transactionSync((): string | null => {
      this.#sweep(generation, now);
      const row = sql.exec<Row>("SELECT * FROM action_requests WHERE call_digest = ?", call)
        .toArray()[0];
      if (row !== undefined && row.approved_at !== null) {
        sql.exec("DELETE FROM action_requests WHERE card_id = ?", row.card_id);
        receipt("admitted", row.card_id);
        return null;
      }
      if (row !== undefined) {
        return row.card_id;
      }
      this.#room(actor.client);
      const cardId = "act_" + randomHex(16);
      const shown = [...request];
      const card: ActionCard = {
        card_id: cardId, act, says: actNamed(act)!.says, door: asked.door, agent: actor.agent,
        client: actor.client, request: shown.slice(0, ACTION_LIMITS.shownChars).join(""),
        cut: shown.length > ACTION_LIMITS.shownChars, generation, filed_at: now,
        expires_at: now + ACTION_LIMITS.cardTtlS,
      };
      sql.exec("INSERT INTO action_requests (card_id, digest, call_digest, card, client, " +
                 "generation, filed_at) VALUES (?, ?, ?, ?, ?, ?, ?)", cardId,
               policyDigest(card), call, canonical(card), actor.client, generation, now);
      receipt("held", cardId);
      this.#notices.add("action_request", `${actor.agent ?? "an outside agent"} asks to ` +
                        `${act} at ${asked.door}: card ${cardId}`, now);
      return cardId;
    });
    if (held !== null) {
      throw new Refusal("held_for_owner", `held for your owner's answer: card ${held}. The ` +
                        "owner answers it in the TinyAssets app; once they say yes, make " +
                        "this same call again", { facts: { card_id: held } });
    }
  }

  /** Room for one more held call: `held` in all, `perClient` of them `client`'s. */
  #room(client: string | null): void {
    const sql = this.#sql;
    if (client !== null && sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM action_requests WHERE client = ?", client).one().n
        >= ACTION_LIMITS.perClient) {
      throw new Refusal("refused", "this client's calls held for the owner are at their limit");
    }
    if (sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM action_requests").one().n
        >= ACTION_LIMITS.held) {
      throw new Refusal("refused", "too many calls held for the owner");
    }
  }

  /** The pending card under `cardId`, of the policy's generation and unexpired, or null. */
  #pending(cardId: unknown, generation: number, now: number): Row | null {
    if (typeof cardId !== "string" || !CARD_ID.test(cardId)) {
      return null;
    }
    return this.#sql.exec<Row>(
      "SELECT * FROM action_requests WHERE card_id = ? AND approved_at IS NULL AND " +
        "generation = ? AND filed_at >= ?", cardId, generation, now - ACTION_LIMITS.cardTtlS,
    ).toArray()[0] ?? null;
  }

  /** The pending card under `cardId`, and the digest its answer names, or null. */
  card(cardId: unknown, now: number): { card: ActionCard; digest: string } | null {
    const row = this.#pending(cardId, this.#policy().generation, now);
    return row === null ? null : { card: JSON.parse(row.card) as ActionCard, digest: row.digest };
  }

  /**
   * The owner's answer, in its session: the pending card with the digest it was shown, yes
   * (kept as the call's once-approval) or no (deleted), receipted.
   */
  answer(session: LiveSession, cardId: unknown, shown: unknown, yes: boolean, now: number):
      { card_id: string; approved: boolean } {
    proven(session, "session");
    return this.#storage.transactionSync(() => {
      const { generation } = this.#policy();
      this.#sweep(generation, now);
      const row = this.#pending(cardId, generation, now);
      if (row === null || typeof shown !== "string" || row.digest !== shown) {
        throw new Refusal("refused", "action request is not the pending card shown");
      }
      if (yes) {
        this.#sql.exec("UPDATE action_requests SET approved_at = ? WHERE card_id = ?", now,
                       row.card_id);
      } else {
        this.#sql.exec("DELETE FROM action_requests WHERE card_id = ?", row.card_id);
      }
      const card = JSON.parse(row.card) as ActionCard;
      this.#receipt({ at: now, act: card.act, decision: "ask",
                      outcome: yes ? "approved" : "refused", call_digest: row.call_digest,
                      card_id: row.card_id, agent: card.agent, client: card.client,
                      generation });
      return { card_id: row.card_id, approved: yes };
    });
  }
}

export interface ActionCardRequest {
  readonly card_id: string;
}
export interface ActionAnswerRequest {
  readonly card_id: string;
  readonly digest: string;
}

// -- THE DOORS: the `actions` rows' handlers (`owner/doors.json`) --------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#call`) hands what its
// doors reach (`ActionsDoorNeeds`) once the row's steps held: the bind, the account, the
// scope and the fields.

/** What the `actions` doors reach: the held calls. */
export interface ActionsDoorNeeds {
  readonly actions: Actions;
}

/** The `actions` doors' handlers, by door. */
export const actionsDoors = Object.freeze({
  /** The pending card the owner is shown, and the digest its answer names. */
  actionRequest(o: ActionsDoorNeeds, { r }: Call<ActionCardRequest>):
      { card: ActionCard; digest: string } {
    const held = o.actions.card(r["card_id"], clock());
    if (held === null) {
      throw new Refusal("refused", "no pending action request for this card");
    }
    return held;
  },
  /** The owner's yes, in its session: the held call admitted once, within a day. */
  actionApprove(o: ActionsDoorNeeds, { session, r }: Call<ActionAnswerRequest, "session">):
      { card_id: string; approved: boolean } {
    return o.actions.answer(session, r["card_id"], r["digest"], true, clock());
  },
  /** The owner's no, in its session: the held call's card gone. */
  actionRefuse(o: ActionsDoorNeeds, { session, r }: Call<ActionAnswerRequest, "session">):
      { card_id: string; approved: boolean } {
    return o.actions.answer(session, r["card_id"], r["digest"], false, clock());
  },
});
