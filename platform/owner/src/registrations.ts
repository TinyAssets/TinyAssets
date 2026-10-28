/**
 * An MCP client's held agent registration (slice 3's review L2), mirroring a held policy
 * proposal (`policy.ts`).
 *
 * Registering an agent is the owner's act, in its protected session, but the identity an
 * MCP agent registers as is its client's verified origin, which only the client's own
 * verified call carries: the owner's browser has no source for it. So the client files a
 * card through its own call (`file`), and the object captures the origin the binder
 * verified, the call's source reference and the grant authority it was admitted under.
 * The owner reads the card (`card`) and applies it in session by its id and the digest
 * it was shown (`take`, then `Ledger.register`, in one transaction). An API key's agent
 * still registers from the owner's browser with the key, whose origin the object
 * re-derives from the key id.
 *
 * - One pending card per outside identity: filing again replaces it. A card lives
 *   `CARD_TTL_S`; expired ones are pruned at each filing and never applied.
 * - A card is stamped like scheduled work: `take` rechecks its client's grant with
 *   `standing`, and a grant change drops the client's cards in its own transaction
 *   (`endClient`). A pending card holds its client's grant row (the schema's `held`).
 * - A card never changes (the schema refuses an update), and is gone once applied.
 * - Bounded: `maxPending` cards (a counter the schema's triggers keep), `perClient` of
 *   them one outside client's (counted on its index), each request at most
 *   `maxRequest` characters.
 */
import { digest } from "./collections";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import type { Stamp, StandingReader } from "./scheduled";
import { codePoints } from "./text";

/** How long a filed card waits for the owner. */
export const CARD_TTL_S = 86_400;

export interface CardLimits {
  readonly maxPending: number;
  readonly perClient: number;
  readonly maxRequest: number;
}

export const CARD_LIMITS: CardLimits = Object.freeze({
  maxPending: 200, perClient: 10, maxRequest: 4096,
});

/** A registration request's flat fields, as the door took them. */
export type Fields = Readonly<Record<string, string | number | boolean | null>>;

const CARD_ID = /^card_[0-9a-f]{32}$/;
const HEX64 = /^[0-9a-f]{64}$/;

/** A held card, as the owner is shown it. */
export interface Card {
  readonly card_id: string;
  readonly client: string;
  readonly family: string;
  readonly source_ref: string;
  readonly request: Fields;
  readonly digest: string;
  readonly expires_at: number;
}

/** What an applied card registers: the captured identity, source and request. */
export interface Taken {
  readonly origin: { readonly client: string; readonly family: string };
  readonly source: { readonly kind: "mcp"; readonly ref: string };
  readonly request: Fields;
}

type Row = {
  card_id: string; client: string; family: string; source_ref: string; generation: number;
  authenticated_at: number | null; request: string; digest: string; filed_at: number;
};

function token(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

export class Registrations {
  readonly #storage: OwnerStorage;
  readonly #grants: StandingReader;
  readonly #limits: CardLimits;

  constructor(storage: OwnerStorage, grants: StandingReader,
              limits: CardLimits = CARD_LIMITS) {
    this.#storage = storage;
    this.#grants = grants;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #shown(row: Row): Card {
    return { card_id: row.card_id, client: row.client, family: row.family,
             source_ref: row.source_ref,
             request: JSON.parse(row.request) as Fields, digest: row.digest,
             expires_at: row.filed_at + CARD_TTL_S };
  }

  /**
   * File a card from the client's own verified call: `origin` as the binder verified it,
   * `sourceRef` its source's reference, `by` the grant authority it was admitted under.
   */
  file(origin: { client: string; family: string }, sourceRef: string, by: Stamp,
       request: Readonly<Record<string, unknown>>, now: number):
      { card_id: string; digest: string; expires_at: number } {
    if (!HEX64.test(origin.client) || !HEX64.test(origin.family) || by.client !== origin.client) {
      throw new Invalid("invalid outside origin");
    }
    if (Object.values(request).some((value) => value !== null && typeof value === "object")) {
      throw new Invalid("a registration's fields are flat values");
    }
    const body = JSON.stringify(request);
    if (codePoints(body) > this.#limits.maxRequest) {
      throw new Invalid(`a registration is at most ${this.#limits.maxRequest} characters`);
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      this.#grants.standing(by.client, by.generation, by.authenticated_at);
      sql.exec("DELETE FROM agent_cards WHERE filed_at < ?", now - CARD_TTL_S);
      sql.exec("DELETE FROM agent_cards WHERE client = ? AND family = ?", origin.client,
               origin.family);
      const mine = sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM agent_cards WHERE client = ?", origin.client).one().n;
      if (mine >= this.#limits.perClient) {
        throw new Refused("this client's pending registrations are at their limit");
      }
      if (sql.exec<{ pending_cards: number }>("SELECT pending_cards FROM policy_usage").one()
            .pending_cards >= this.#limits.maxPending) {
        throw new Refused("too many pending agent registrations");
      }
      const cardId = "card_" + token(16);
      const shown = digest(JSON.stringify([cardId, origin.client, origin.family, sourceRef,
                                           body]));
      sql.exec("INSERT INTO agent_cards (card_id, client, family, source_ref, generation, " +
                 "authenticated_at, request, digest, filed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               cardId, origin.client, origin.family, sourceRef, by.generation,
               by.authenticated_at, body, shown, now);
      return { card_id: cardId, digest: shown, expires_at: now + CARD_TTL_S };
    });
  }

  /** The pending, unexpired card under `cardId`, or null. */
  card(cardId: unknown, now: number): Card | null {
    if (typeof cardId !== "string" || !CARD_ID.test(cardId)) {
      return null;
    }
    const row = this.#sql.exec<Row>(
      "SELECT * FROM agent_cards WHERE card_id = ? AND filed_at >= ?", cardId, now - CARD_TTL_S,
    ).toArray()[0];
    return row === undefined ? null : this.#shown(row);
  }

  /**
   * The owner's apply: the pending, unexpired card with the digest the owner was shown,
   * whose client's authority still stands, consumed. The caller registers what it returns
   * in the same transaction, so a refused registration keeps the card.
   */
  take(cardId: unknown, shown: unknown, now: number): Taken {
    const row = typeof cardId === "string" && CARD_ID.test(cardId)
      ? this.#sql.exec<Row>("SELECT * FROM agent_cards WHERE card_id = ? AND filed_at >= ?",
                            cardId, now - CARD_TTL_S).toArray()[0]
      : undefined;
    if (row === undefined || typeof shown !== "string" || row.digest !== shown) {
      throw new Refused("registration is not the pending card shown");
    }
    this.#grants.standing(row.client, row.generation, row.authenticated_at);
    this.#sql.exec("DELETE FROM agent_cards WHERE card_id = ?", row.card_id);
    return { origin: { client: row.client, family: row.family },
             source: { kind: "mcp", ref: row.source_ref },
             request: JSON.parse(row.request) as Fields };
  }

  /** A changed or revoked grant drops its client's cards; how many. */
  endClient(client: string): number {
    return this.#sql.exec("DELETE FROM agent_cards WHERE client = ? RETURNING card_id", client)
      .toArray().length;
  }
}
