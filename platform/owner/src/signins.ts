/**
 * The vendor's own sign-in inside a sandbox (slice SIGNIN; ADR-031 decision 2, ADR-033,
 * ADR-034): a subscription is used only through the vendor's unmodified CLI, signed in inside
 * the owner's own sandbox (or one client's own, for that client), and a login is never copied
 * between sandboxes.
 *
 * A sign-in (`sandbox_signins`, one live a sandbox: an authority) holds its sandbox's lease
 * with no run: the object opens the lease (or joins the live one), the sandbox Worker starts
 * the record's `cli.login` there as a user of its own, and the runner posts what the record's
 * patterns find in its output (the vendor's URL to open, a code to type there, whether it
 * takes a pasted code) as `ta.signin` frames on the sign-in's own event door, which renew the
 * lease as a run's heartbeats do. The object keeps the latest prompt on the row and relays
 * each frame to the owner's app sockets on the sign-in's id. The end's frame ends the row,
 * and the lease goes once no run holds it; the engine's presence comes on the runner's
 * sign-in report (`signinReport`), never a credential. A sign-out is the same door, its
 * runner deleting the engine's `secret_paths` from the sandbox's store.
 *
 * A row whose runner went quiet past the lease's TTL is gone at the next look: its lease
 * lapsed with it.
 */
import { randomBytes } from "node:crypto";

import { Invalid, Refused } from "./errors";
import { LEASE_TTL_S, OWNER_AUTHORITY } from "./files";
import type { OwnerStorage } from "./gate";
import { isPrintable } from "./text";

/** `si_` and 32 hex: as long as a conversation's id, so an app socket's ticket names it. */
export const SIGNIN_ID = /^si_[0-9a-f]{32}$/;

/** A live sign-in is the authority's one at a time. */
export const SIGNIN_LIVE = "signin_live: a sign-in is already in progress in that sandbox";
export const NO_SIGNIN = "no such sign-in";

export type SigninMode = "in" | "out";

export interface SigninRow {
  readonly id: string;
  readonly authority: string;
  readonly engine: string;
  readonly mode: SigninMode;
  readonly lease_id: string;
  readonly epoch: number;
  readonly state: string;
  readonly url: string | null;
  readonly code: string | null;
  readonly accepts_code: number;
  readonly started_at: number;
  readonly beat_at: number;
}

/** What the owner's app shows of a sign-in: the vendor's own prompt, never a token. */
export interface SigninView {
  readonly id: string;
  readonly authority: string;
  readonly engine: string;
  readonly mode: SigninMode;
  readonly state: string;
  readonly url: string | null;
  readonly code: string | null;
  readonly accepts_code: boolean;
  readonly started_at: number;
}

export function signinView(row: SigninRow): SigninView {
  return { id: row.id, authority: row.authority, engine: row.engine, mode: row.mode,
           state: row.state, url: row.url, code: row.code,
           accepts_code: row.accepts_code === 1, started_at: row.started_at };
}

/** The states a sign-in's end frame names. */
const ENDS: ReadonlySet<string> = new Set(["signed_in", "signed_out", "failed", "cancelled",
                                          "refused"]);

/** A frame's event, as the runner posts a sign-in's (`ta.signin`). */
function signinEvent(event: unknown): Record<string, unknown> | null {
  return typeof event === "object" && event !== null && !Array.isArray(event)
    && (event as Record<string, unknown>)["type"] === "ta.signin"
    ? event as Record<string, unknown> : null;
}

export class Signins {
  readonly #sql: OwnerStorage["sql"];

  constructor(storage: OwnerStorage) {
    this.#sql = storage.sql;
  }

  /** A new sign-in's id. */
  static id(): string {
    return `si_${randomBytes(16).toString("hex")}`;
  }

  #row(id: unknown): SigninRow | undefined {
    return typeof id === "string" && SIGNIN_ID.test(id)
      ? this.#sql.exec<SigninRow & Record<string, SqlStorageValue>>(
        "SELECT * FROM sandbox_signins WHERE id = ?", id).toArray()[0] : undefined;
  }

  /** The live sign-in `id`, the owner's to see. A read. */
  get(id: unknown): SigninRow {
    const row = this.#row(id);
    if (row === undefined) {
      throw new Refused(NO_SIGNIN);
    }
    return row;
  }

  /** Every live sign-in, newest first. A read. */
  list(): SigninRow[] {
    return this.#sql.exec<SigninRow & Record<string, SqlStorageValue>>(
      "SELECT * FROM sandbox_signins ORDER BY started_at DESC").toArray();
  }

  /** Whether a live sign-in holds `leaseId`: the lease stays while it does. A read. */
  holds(leaseId: string): boolean {
    return this.#sql.exec("SELECT 1 FROM sandbox_signins WHERE lease_id = ?", leaseId)
      .toArray().length > 0;
  }

  /**
   * In a unit: the sign-ins whose runner went quiet past the lease's TTL, or whose lease is
   * no longer live (`live`, the live leases' ids), gone. Their ids.
   */
  lost(now: number, live: ReadonlySet<string>): string[] {
    return this.#sql.exec<{ id: string }>(
      "SELECT id, lease_id, beat_at FROM sandbox_signins").toArray()
      .filter((row) => !live.has((row as unknown as SigninRow).lease_id)
              || (row as unknown as SigninRow).beat_at + LEASE_TTL_S <= now)
      .map((row) => {
        this.#sql.exec("DELETE FROM sandbox_signins WHERE id = ?", row.id);
        return row.id;
      });
  }

  /** In a unit: a sign-in begun in `authority`'s sandbox under its lease. Refused while one is. */
  begin(authority: string, engine: string, mode: SigninMode,
        lease: { lease_id: string; epoch: number }, now: number): SigninRow {
    if (this.#sql.exec("SELECT 1 FROM sandbox_signins WHERE authority = ?", authority)
      .toArray().length > 0) {
      throw new Refused(SIGNIN_LIVE);
    }
    const id = Signins.id();
    this.#sql.exec("INSERT INTO sandbox_signins (id, authority, engine, mode, lease_id, epoch, " +
                     "state, started_at, beat_at) VALUES (?, ?, ?, ?, ?, ?, 'starting', ?, ?)",
                   id, authority, engine, mode, lease.lease_id, lease.epoch, now, now);
    return this.get(id);
  }

  /** In a unit: the sign-in gone (its start had no sandbox, or it ended). */
  end(id: string): SigninRow | null {
    const row = this.#row(id) ?? null;
    this.#sql.exec("DELETE FROM sandbox_signins WHERE id = ?", id);
    return row;
  }

  /**
   * In a unit: the runner's frames of sign-in `id`, under the lease that holds it: the latest
   * prompt kept, its beat renewed; an end frame (or the post's end) ends it. Its row as it
   * stands, and whether it ended.
   */
  frames(id: unknown, lease: { lease_id: string; epoch: number },
         events: readonly unknown[], ended: boolean, now: number):
      { row: SigninRow; ended: boolean } {
    const row = this.#row(id);
    if (row === undefined || row.lease_id !== lease.lease_id || row.epoch !== lease.epoch) {
      throw new Refused(NO_SIGNIN);
    }
    let state = row.state;
    let url = row.url;
    let code = row.code;
    let accepts = row.accepts_code;
    for (const event of events.map(signinEvent)) {
      if (event === null || typeof event["state"] !== "string") {
        continue;
      }
      state = event["state"];
      if (state === "open") {
        url = typeof event["url"] === "string" ? event["url"] : null;
        code = typeof event["code"] === "string" ? event["code"] : null;
        accepts = event["accepts_code"] === true ? 1 : 0;
      }
    }
    this.#sql.exec("UPDATE sandbox_signins SET state = ?, url = ?, code = ?, accepts_code = ?, " +
                     "beat_at = ? WHERE id = ?", state, url, code, accepts, now, row.id);
    const after = this.get(row.id);
    if (ended || ENDS.has(state)) {
      this.end(row.id);
      return { row: after, ended: true };
    }
    return { row: after, ended: false };
  }
}

/** A pasted code: the vendor's own, one line of printable text. */
export function pastedCode(value: unknown): string {
  if (typeof value !== "string" || value.trim() === "" || /[\r\n]/.test(value)
      || !isPrintable(value)) {
    throw new Invalid("a pasted code is one line of printable text");
  }
  return value.trim();
}

/** The authority a sign-in is for: the owner's own sandbox, or one client's. */
export function signinAuthority(value: unknown): string {
  if (value === undefined || value === OWNER_AUTHORITY) {
    return OWNER_AUTHORITY;
  }
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Invalid("a sign-in's sandbox is the owner's own, or one client's id");
  }
  return value;
}
