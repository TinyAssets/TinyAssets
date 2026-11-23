/**
 * The owner's protected interactive sessions, in the owner's own object (ADR-032). A
 * widening (registering, disabling or deleting an agent, an owner policy write, a grant)
 * needs the owner's live session, and the object checks that row itself: the binder
 * only forwards the cookie the browser presented, as a typed binding argument, and
 * never vouches for it.
 *
 * The edge binder runs the sign-in itself (server PKCE, `max_age=0`, the
 * browser-bound callback) and calls `open` with the verified sign-in's time; the object
 * opens a session only for a sign-in at most `signInMaxAgeS` old, mints the cookie, and
 * keeps only its SHA-256. A session lives 8 hours and slides while its browser renews.
 * The cookie names no owner: the binder takes the owner from its own verified
 * credential, and attaches the cookie only to the browser's same-origin requests; the
 * object refuses an outside client's call that carries one at every
 * door but an agent's registration (`owner-do.ts`).
 *
 * Signing out cancels the owner's open connection flows too: the object's `sessionCloseAll`
 * door cancels every one in the unit that signs the sessions out, and `sessionClose` the
 * flows its session began (`connections.ts`).
 *
 * What a session is:
 * - The proof is the cookie only: a stored hash is never a proof, so reading the store
 *   yields none.
 * - The object mints the cookie, so a session proof is always 32 random bytes.
 * - The row carries no identity: the object is its owner, so another owner's cookie is
 *   simply not in this object's store.
 * - Live sessions are bounded (`maxSessions`); a sign-in past the bound signs out the
 *   session nearest its expiry, so the owner is never locked out.
 */
import { sha256Hex } from "../../contract/src/digest";
import { randomBytes } from "node:crypto";

import { SESSION_TTL_S } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import { codePoints } from "../../contract/src/text";
import {
  type Call, type Door, type DoorUnits, type OwnerSelf, proven,
} from "./door";
import type { Relay } from "./relay";
import { clock } from "./clock";

interface SessionLimits {
  readonly maxSessions: number;
  /** How old a verified sign-in may be when its session opens: the login flow's 600 s. */
  readonly signInMaxAgeS: number;
  /** How far ahead of the object's clock a sign-in's time may read. */
  readonly clockMarginS: number;
}

export const SESSION_LIMITS: SessionLimits = Object.freeze({
  maxSessions: 64,
  signInMaxAgeS: 600,
  clockMarginS: 5,
});

const SESSION_REQUIRED = "interactive owner session required";

/** A cookie the browser presented: 32 to 512 characters. */
function proofOf(value: unknown): string | null {
  return typeof value === "string" && codePoints(value) >= 32 && codePoints(value) <= 512
    ? value : null;
}

export class Sessions {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /**
   * The owner's new session for a verified interactive sign-in at `authenticatedAt`: its
   * cookie, returned once and never stored, and when it expires.
   */
  open(owner: OwnerSelf, authenticatedAt: unknown, now: number):
      { session: string; expires_at: number } {
    proven(owner, "owner");
    const limits = SESSION_LIMITS;
    if (typeof authenticatedAt !== "number" || !Number.isSafeInteger(authenticatedAt)
        || authenticatedAt < now - limits.signInMaxAgeS
        || authenticatedAt > now + limits.clockMarginS) {
      throw new Refusal("refused", "a session opens only from a fresh interactive sign-in");
    }
    const session = randomBytes(32).toString("base64url");
    const expiresAt = now + SESSION_TTL_S;
    const sql = this.#sql;
    this.#storage.transactionSync(() => {
      sql.exec("DELETE FROM owner_sessions WHERE expires_at <= ?", now);
      const live = sql.exec<{ sessions: number }>("SELECT sessions FROM policy_usage").one()
        .sessions;
      if (live >= limits.maxSessions) {
        sql.exec("DELETE FROM owner_sessions WHERE session_hash IN (SELECT session_hash " +
                   "FROM owner_sessions ORDER BY expires_at LIMIT ?)",
                 live - limits.maxSessions + 1);
      }
      sql.exec("INSERT INTO owner_sessions (session_hash, created_at, expires_at) " +
                 "VALUES (?, ?, ?)", sha256Hex(session), now, expiresAt);
    });
    return { session, expires_at: expiresAt };
  }

  /**
   * The live session `proof` opens, by its hash (the receipts' session id), or refused: a
   * check, of whose hash the runner's session step alone makes a `LiveSession` (`#within`).
   */
  live(proof: unknown, now: number): string {
    const cookie = proofOf(proof);
    const row = cookie === null ? undefined : this.#sql.exec<{ session_hash: string }>(
      "SELECT session_hash FROM owner_sessions WHERE session_hash = ? AND expires_at > ?",
      sha256Hex(cookie), now,
    ).toArray()[0];
    if (row === undefined) {
      throw new Refusal("refused", SESSION_REQUIRED);
    }
    return row.session_hash;
  }

  /** The owner slides its live session while its own browser keeps renewing. */
  extend(owner: OwnerSelf, proof: unknown, now: number): { expires_at: number } {
    proven(owner, "owner");
    const expiresAt = now + SESSION_TTL_S;
    return this.#storage.transactionSync(() => {
      const hash = this.live(proof, now);
      this.#sql.exec("UPDATE owner_sessions SET expires_at = ? WHERE session_hash = ?",
                     expiresAt, hash);
      return { expires_at: expiresAt };
    });
  }

  /** The id a session's cookie is kept under (its hash), or null for no cookie. */
  id(proof: unknown): string | null {
    const cookie = proofOf(proof);
    return cookie === null ? null : sha256Hex(cookie);
  }

  /** Sign one session out; signing out a session that is gone changes nothing. */
  close(proof: unknown): { closed: number } {
    if (typeof proof !== "string") {
      throw new Refusal("invalid", "a session is the cookie its sign-in returned");
    }
    return { closed: this.#sql.exec("DELETE FROM owner_sessions WHERE session_hash = ? " +
                                      "RETURNING session_hash", sha256Hex(proof)).toArray().length };
  }

  /** Sign every session out. */
  closeAll(): { closed: number } {
    return { closed: this.#sql.exec("DELETE FROM owner_sessions RETURNING session_hash")
      .toArray().length };
  }
}

// -- THE DOORS: the `sessions` rows' handlers (`owner/doors.json`) --------------------------------

/**
 * What the `sessions` doors reach: the sessions, the connection flows a sign-out cancels, and
 * the app sockets signing every session out closes.
 */
export interface SessionsDoorNeeds extends Pick<DoorUnits, "synced"> {
  readonly sessions: Sessions;
  readonly connections: { cancelFlows(session: string | null): void };
  readonly relay: Pick<Relay, "closeAll">;
}

export const sessionsDoors = Object.freeze({
  /** A verified interactive sign-in opens a session; its cookie is returned once. */
  sessionOpen(o: SessionsDoorNeeds, call: Call<undefined, "owner">):
      { session: string; expires_at: number } {
    return o.sessions.open(call.self, call.binding.authenticated_at, clock());
  },
  sessionExtend(o: SessionsDoorNeeds, call: Call<undefined, "owner">): { expires_at: number } {
    return o.sessions.extend(call.self, call.binding.session, clock());
  },
  /** One session signed out, and the connection flows it began cancelled with it. */
  sessionClose(o: SessionsDoorNeeds, { binding }: Call<undefined>): { closed: number } {
    const id = o.sessions.id(binding.session);
    const closed = o.sessions.close(binding.session);
    if (id !== null) {
      o.connections.cancelFlows(id);
    }
    return closed;
  },
  /**
   * Every session signed out: account revocation and deletion (`revoke_owner`), every open
   * connection flow cancelled in the same unit, so a late callback finds none.
   */
  async sessionCloseAll(o: SessionsDoorNeeds, door: Door<undefined>): Promise<{ closed: number }> {
    const closed = await o.synced(() => door.within(() => {
      o.connections.cancelFlows(null);
      return o.sessions.closeAll();
    }));
    o.relay.closeAll("signedOut");  // account revocation: every device's sockets
    return closed;
  },
});
