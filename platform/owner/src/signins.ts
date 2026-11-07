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
 * lapsed with it. So is one past its deadline (`deadline`): the vendor's code's lifetime
 * (the record's `expires`, cited), and one lease TTL for its runner's last frame.
 *
 * Only what the trusted side checked leaves this module for the owner's app (review M1): a
 * sign-in's fixed states (`SIGNIN_STATES`), the record's own URL and code (`signinShape`,
 * again here, outside the VM), and a refusal's fixed reason (`SIGNIN_REFUSALS`).
 *
 * Each source's sign-in sits behind its own switch (review H1): the served records whose
 * sign-in the platform's control record switches on, as the binder hands them with every
 * call (`Binding.signins`), kept for the runs the object's own lane starts
 * (`platform_signins`). None kept, none is on.
 */
import { HEX64, randomHex, SIGNIN_ID } from "../../contract/src/ids";

import { bundledSources, type LoginSpec, SIGNIN_REFUSALS, SIGNIN_STATES, type SigninRefusal,
         signinShape } from "../../doors/src/sources";

import { Invalid, Refused } from "../../contract/src/errors";
import { LEASE_TTL_S } from "../../contract/src/limits";
import { OWNER_AUTHORITY } from "./files";
import type { OwnerStorage } from "./gate";
import { ENGINE_SIGNINS, SIGNIN_LIST_CHARS, SIGNIN_PASTE_MAX } from "../../contract/src/run-rows";
import { isPrintable } from "../../contract/src/text";

/** `si_` and 32 hex: as long as a conversation's id, so an app socket's ticket names it. */

/** A live sign-in is the authority's one at a time. */
export const SIGNIN_LIVE = "signin_live: a sign-in is already in progress in that sandbox";
export const NO_SIGNIN = "no such sign-in";
/** A source whose sign-in the platform's control record has switched off. */
export const SIGNIN_OFF = "signin_off: signing in to this source inside a sandbox is switched " +
  "off until its vendor's terms for hosted use are verified";
/** A pasted code for a sign-in not now asking for one. */
export const NO_CODE_WANTED = "this sign-in is not asking for a pasted code now";

export type SigninMode = "in" | "out";

export interface SigninRow {
  readonly id: string;
  readonly authority: string;
  readonly engine: string;
  readonly mode: SigninMode;
  readonly region: string | null;
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
  /** The host the URL sends the owner to, in plain words beside it. */
  readonly host: string | null;
  readonly code: string | null;
  readonly accepts_code: boolean;
  readonly started_at: number;
  /** When the trusted side ends it `expired` (`deadline`). */
  readonly expires_at: number;
}

export function signinView(row: SigninRow): SigninView {
  return { id: row.id, authority: row.authority, engine: row.engine, mode: row.mode,
           state: row.state, url: row.url, host: row.url === null ? null : new URL(row.url).hostname,
           code: row.code,
           accepts_code: row.accepts_code === 1, started_at: row.started_at,
           expires_at: deadline(row) };
}

/** The states that end a sign-in. */
const ENDS: readonly string[] = Object.freeze(["signed_in", "signed_out", "failed",
                                               "cancelled", "refused", "expired"]);

/**
 * When a sign-in is ended `expired` on the trusted side: the vendor's code's lifetime (its
 * record's `expires`, cited, which the runner stops its login at too), and one lease TTL for
 * its runner's last frame to land. A sign-out has the same bound.
 */
export function deadline(row: { engine: string; started_at: number }): number {
  const login = bundledSources().get(row.engine)?.cli?.login as LoginSpec | undefined;
  return row.started_at + (login?.expires.s ?? 0) + LEASE_TTL_S;
}

function refusal(value: unknown): value is SigninRefusal {
  return typeof value === "string" && Object.hasOwn(SIGNIN_REFUSALS, value);
}

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
    return `si_${randomHex(16)}`;
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
   * In a unit: the sign-ins whose runner went quiet past the lease's TTL, whose lease is no
   * longer live (`live`, the live leases' ids), or past their deadline, gone. Their rows.
   */
  lost(now: number, live: ReadonlySet<string>): SigninRow[] {
    return this.list()
      .filter((row) => !live.has(row.lease_id) || row.beat_at + LEASE_TTL_S <= now
              || deadline(row) <= now)
      .map((row) => {
        this.#sql.exec("DELETE FROM sandbox_signins WHERE id = ?", row.id);
        return row;
      });
  }

  /** In a unit: a sign-in begun in `authority`'s sandbox under its lease. Refused while one is. */
  begin(authority: string, engine: string, mode: SigninMode, region: string | null,
        lease: { lease_id: string; epoch: number }, now: number): SigninRow {
    if (this.#sql.exec("SELECT 1 FROM sandbox_signins WHERE authority = ?", authority)
      .toArray().length > 0) {
      throw new Refused(SIGNIN_LIVE);
    }
    const id = Signins.id();
    this.#sql.exec("INSERT INTO sandbox_signins (id, authority, engine, mode, region, lease_id, " +
                     "epoch, state, started_at, beat_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'starting', " +
                     "?, ?)", id, authority, engine, mode, region, lease.lease_id, lease.epoch, now,
                   now);
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
   * prompt kept, its beat renewed; an end frame (or the post's end) ends it, and so does its
   * deadline (`expired`). Only a sign-in's fixed states pass; a refusal keeps only its fixed
   * reason, and no other frame any text (review M1). A prompt that is not the record's own
   * shape (`signinShape`: the pinned page, a device code, nothing credential-shaped) is
   * checked here, outside the VM, and refused, as is a state no sign-in reports: the sign-in
   * ends `refused`, the prompt never kept nor relayed (a hijacked engine shows no look-alike
   * page). Its row as it stands, whether it ended, whether the trusted side stopped it
   * (refused, or past its deadline: its CLI is then stopped in the sandbox), and the events
   * to relay.
   */
  frames(id: unknown, lease: { lease_id: string; epoch: number },
         events: readonly unknown[], ended: boolean, now: number):
      { row: SigninRow; ended: boolean; stopped: boolean; events: unknown[] } {
    const row = this.#row(id);
    if (row === undefined || row.lease_id !== lease.lease_id || row.epoch !== lease.epoch) {
      throw new Refused(NO_SIGNIN);
    }
    let state = row.state;
    let url = row.url;
    let code = row.code;
    let accepts = row.accepts_code;
    let stopped = false;
    const relayed: unknown[] = [];
    const rec = bundledSources().get(row.engine)!;
    const refuse = (reason: SigninRefusal) => {
      stopped = true;
      state = "refused";
      url = null;
      code = null;
      relayed.push({ type: "ta.signin", state, reason });
    };
    for (const event of events.map(signinEvent)) {
      if (event === null || stopped || ENDS.includes(state)) {
        continue;
      }
      const next = event["state"];
      if (typeof next !== "string" || !(SIGNIN_STATES as readonly string[]).includes(next)) {
        refuse("state");
        continue;
      }
      if (next === "open") {
        const nextUrl = typeof event["url"] === "string" ? event["url"] : null;
        const nextCode = typeof event["code"] === "string" ? event["code"] : null;
        const why = nextUrl === null && nextCode === null ? "state"
          : signinShape(rec, row.region, nextUrl, nextCode);
        if (why !== null) {
          refuse(why);
          continue;
        }
        url = nextUrl;
        code = nextCode;
        // Whether it takes a pasted code is the record's, never the frame's.
        accepts = (rec.cli?.login as unknown as LoginSpec).accepts_code === true ? 1 : 0;
        relayed.push({ type: "ta.signin", state: "open", url, code,
                       accepts_code: accepts === 1 });
        state = "open";
        continue;
      }
      state = next;
      relayed.push({ type: "ta.signin", state,
                     ...(next === "refused" && refusal(event["reason"])
                       ? { reason: event["reason"] } : {}) });
    }
    if (!ENDS.includes(state) && deadline(row) <= now) {
      // Past the vendor's code's lifetime: what it showed is dead (review M4).
      stopped = true;
      state = "expired";
      url = null;
      code = null;
      relayed.push({ type: "ta.signin", state });
    }
    this.#sql.exec("UPDATE sandbox_signins SET state = ?, url = ?, code = ?, accepts_code = ?, " +
                     "beat_at = ? WHERE id = ?", state, url, code, accepts, now, row.id);
    const after = this.get(row.id);
    if (ended || stopped || ENDS.includes(state)) {
      this.end(row.id);
      return { row: after, ended: true, stopped, events: relayed };
    }
    return { row: after, ended: false, stopped, events: relayed };
  }

  /**
   * The live sign-in `id`, now asking for a pasted code (review L1): its record takes one,
   * it is showing the vendor's prompt (or took a code already, which the vendor may ask
   * again), and it is within its deadline. A read.
   */
  wantsCode(id: unknown, now: number): SigninRow {
    const row = this.get(id);
    if (row.accepts_code !== 1 || !["open", "code_sent"].includes(row.state)
        || deadline(row) <= now) {
      throw new Refused(NO_CODE_WANTED);
    }
    return row;
  }
}

/** A pasted code: the vendor's own, one line of printable text, a terminal line at most. */
export function pastedCode(value: unknown): string {
  if (typeof value !== "string" || value.trim() === "" || /[\r\n]/.test(value)
      || !isPrintable(value) || value.trim().length > SIGNIN_PASTE_MAX) {
    throw new Invalid(`a pasted code is one line of printable text, at most ${
      SIGNIN_PASTE_MAX} characters`);
  }
  return value.trim();
}

/**
 * In a unit: the served records whose sign-in is switched on, as the binder handed them
 * (`Binding.signins`), kept (written only when they changed) for the runs the lane starts;
 * a door that handed none reads the kept ones. None kept, none is on: a switch is never
 * taken as on unless the control record, as the doors read it, says so.
 */
export function switchedOn(storage: OwnerStorage, handed: unknown, now: number): Set<string> {
  const sql = storage.sql;
  const kept = sql.exec<{ engines: string }>("SELECT engines FROM platform_signins")
    .toArray()[0];
  if (handed === undefined) {
    return new Set(kept === undefined || kept.engines === "" ? [] : kept.engines.split(","));
  }
  const sources = bundledSources();
  if (!Array.isArray(handed) || handed.length > ENGINE_SIGNINS
      || !handed.every((id) => typeof id === "string"
        && sources.get(id)?.cli?.login !== undefined)) {
    throw new Invalid("the switched-on sign-ins are served records that sign in");
  }
  const engines = [...new Set(handed as string[])].sort().join(",");
  if (engines.length > SIGNIN_LIST_CHARS) {
    throw new Invalid(`the switched-on sign-ins are at most ${SIGNIN_LIST_CHARS} characters`);
  }
  if (kept?.engines !== engines) {
    sql.exec("INSERT INTO platform_signins (only, engines, read_at) VALUES (1, ?, ?) " +
               "ON CONFLICT (only) DO UPDATE SET engines = excluded.engines, " +
               "read_at = excluded.read_at", engines, now);
  }
  return new Set(engines === "" ? [] : engines.split(","));
}

/** The authority a sign-in is for: the owner's own sandbox, or one client's. */
export function signinAuthority(value: unknown): string {
  if (value === undefined || value === OWNER_AUTHORITY) {
    return OWNER_AUTHORITY;
  }
  if (typeof value !== "string" || !HEX64.test(value)) {
    throw new Invalid("a sign-in's sandbox is the owner's own, or one client's id");
  }
  return value;
}
