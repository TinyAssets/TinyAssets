/**
 * The owner app's socket tickets (A6, salvaged; `relay.ts`): each one's SHA-256 alone, the
 * conversation it is for, the first-party grant its token was issued under and that token's
 * expiry, single-use and good for `SOCKET_TICKET_S`, at most `SOCKET_TICKETS` outstanding
 * (migration 12's trigger; the minting unit forgets the expired ones first).
 */
import { HEX64 } from "../../contract/src/ids";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import { SOCKET_TICKET_S, TICKET_REFUSED } from "../../contract/src/socket-shape";


export class Tickets {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  /** When a ticket minted `now` for a token expiring `tokenExpires` expires, in whole seconds. */
  static expiry(now: number, tokenExpires: number): number {
    return Math.floor(Math.min(now + SOCKET_TICKET_S, tokenExpires));
  }

  /** In a unit: a ticket's hash kept for `conversation`; when it expires. */
  mint(hash: string, conversation: string, grant: string, tokenExpires: number, now: number):
      number {
    if (!HEX64.test(hash) || typeof grant !== "string" || grant === "" || grant.length > 128
        || !Number.isFinite(tokenExpires)) {
      throw new Refusal("invalid", "a socket ticket is bound to its token's grant and expiry");
    }
    if (tokenExpires <= now) {
      throw new Refusal("ticket_refused", TICKET_REFUSED);
    }
    const sql = this.#storage.sql;
    sql.exec("DELETE FROM socket_tickets WHERE expires_at <= ? OR token_expires_at <= ?",
             now, now);
    const expires = Tickets.expiry(now, tokenExpires);
    if (expires <= now) {
      throw new Refusal("ticket_refused", TICKET_REFUSED);
    }
    sql.exec("INSERT INTO socket_tickets (hash, conversation, grant_id, expires_at, " +
               "token_expires_at) VALUES (?, ?, ?, ?, ?)", hash, conversation, grant, expires,
             tokenExpires);
    return expires;
  }

  /**
   * In a unit: the ticket consumed, whatever comes of it; its grant and token expiry, or
   * refused alike: unknown, used, expired, or for another conversation.
   */
  consume(hash: unknown, conversation: string, now: number):
      { grant: string; token_expires_at: number } {
    const ticket = typeof hash === "string" && HEX64.test(hash)
      ? this.#storage.sql.exec<{ conversation: string; grant_id: string; expires_at: number;
                                 token_expires_at: number }>(
        "DELETE FROM socket_tickets WHERE hash = ? RETURNING conversation, grant_id, " +
          "expires_at, token_expires_at", hash).toArray()[0] : undefined;
    if (ticket === undefined || ticket.conversation !== conversation
        || ticket.expires_at <= now || ticket.token_expires_at <= now) {
      throw new Refusal("ticket_refused", TICKET_REFUSED);
    }
    return { grant: ticket.grant_id, token_expires_at: ticket.token_expires_at };
  }

  /** In a unit: a signed-out grant's outstanding tickets forgotten. */
  endGrant(grant: string): void {
    this.#storage.sql.exec("DELETE FROM socket_tickets WHERE grant_id = ?", grant);
  }
}
