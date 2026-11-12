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

import { bundledSources } from "../../sources/src/catalog";
import {
  SIGNIN_REFUSALS, SIGNIN_STATES, type SigninRefusal, signinShape,
} from "../../sources/src/signin";

import { Refusal } from "../../contract/src/refusal";
import {
 ENGINE_SIGNINS, LEASE_TTL_S, SIGNIN_LIST_CHARS, SIGNIN_PASTE_MAX,
} from "../../contract/src/limits";
import { OWNER_AUTHORITY } from "./files";
import type { OwnerStorage } from "./gate";
import { isPrintable } from "../../contract/src/text";
import type {
  Call, Door, OwnerState,
} from "./owner-do";
import { EGRESS_ORIGIN, runUrls } from "../../contract/src/egress";
import { FRAME_VERSION } from "../../contract/src/run-events";
import { SANDBOX_ABSENT, started } from "./sandbox";
import { clock } from "./clock";
import { sealRoute } from "../../contract/src/routing-mac";
import type { Runs } from "./runs";
import { eventsRequest, signinRequest } from "./reports";
import type { Started } from "../../contract/src/sandbox";
import type { SigninEventsRequest } from "../../contract/src/owner";

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
  const login = bundledSources().get(row.engine)?.cli?.login;
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
      throw new Refusal("refused", NO_SIGNIN);
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
      throw new Refusal("signin_live", SIGNIN_LIVE);
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
      throw new Refusal("refused", NO_SIGNIN);
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
        accepts = rec.cli!.login!.accepts_code === true ? 1 : 0;
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
      throw new Refusal("refused", NO_CODE_WANTED);
    }
    return row;
  }
}

/** A pasted code: the vendor's own, one line of printable text, a terminal line at most. */
export function pastedCode(value: unknown): string {
  if (typeof value !== "string" || value.trim() === "" || /[\r\n]/.test(value)
      || !isPrintable(value) || value.trim().length > SIGNIN_PASTE_MAX) {
    throw new Refusal("invalid", `a pasted code is one line of printable text, at most ${
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
    throw new Refusal("invalid", "the switched-on sign-ins are served records that sign in");
  }
  const engines = [...new Set(handed as string[])].sort().join(",");
  if (engines.length > SIGNIN_LIST_CHARS) {
    throw new Refusal("invalid",
                      `the switched-on sign-ins are at most ${SIGNIN_LIST_CHARS} characters`);
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
    throw new Refusal("invalid", "a sign-in's sandbox is the owner's own, or one client's id");
  }
  return value;
}

/** The owner's ask to sign in to an engine inside a sandbox (`signinStart`). */
export interface SigninStartRequest {
  readonly engine: string;
  /** `owner` (the default), or one client's id: that client's own sandbox. */
  readonly authority?: string;
  /** The owner's choice among the record's `cli.regions`, when it has any. */
  readonly region?: string;
  readonly mode?: SigninMode;
}
export interface SigninRequestById {
  readonly id: string;
}
export interface SigninCodeRequest extends SigninRequestById {
  readonly code: string;
}
/**
 * The runner's report of the sandbox's engines' sign-ins (`signinReport`): for each engine
 * that signs in with the vendor's own login, whether its record's `status_argv` succeeded,
 * and the models it read the engine offers. Presence only: never a credential.
 */
export interface SigninRequest {
  readonly signins: readonly { readonly engine: string; readonly signed_in: boolean;
                               readonly models?: readonly string[] }[];
}

// -- THE DOORS: the `signins` rows' handlers (`contract/doors.json`) ------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** Whether `id` is a live sign-in's: an app socket may watch it (the owner's own). A read. */
export function signinOwned(o: OwnerState, id: unknown): boolean {
  if (typeof id !== "string" || !SIGNIN_ID.test(id)) {
    return false;
  }
  try {
    o.signins.get(id);
    return true;
  } catch {
    return false;
  }
}

/** The `signins` doors' handlers, by door. */
export const signinsDoors = Object.freeze({
  /** A vendor sign-in begun, its call checked in its first unit (`signinStart`). */
  async signinStart(o: OwnerState, door: Door<SigninStartRequest>):
      Promise<SigninView | { started: false; code: string }> {
    const begun = await o.door(() => {
      const { r } = door.check();
      const rec = typeof r["engine"] === "string" ? bundledSources().get(r["engine"]) : undefined;
      const cli = rec?.cli;
      if (rec === undefined || cli === undefined || rec.unwired !== undefined
          || cli.login === undefined || !cli.credential.includes("vendor_login_in_sandbox")) {
        throw new Refusal("invalid",
                          "a sign-in names an engine that signs in with the vendor's own login");
      }
      const mode = (r["mode"] ?? "in") as SigninMode;
      if (mode !== "in" && mode !== "out") {
        throw new Refusal("invalid", "a sign-in's mode is in or out");
      }
      // A sign-in sits behind its source's switch; a sign-out never does, so a login is
      // always removable (review H1).
      if (mode === "in" && !switchedOn(o.storage, door.binding.signins, clock()).has(rec.id)) {
        throw new Refusal("signin_off", SIGNIN_OFF);
      }
      const regions = cli.regions === undefined ? [] : Object.keys(cli.regions);
      const region = r["region"] ?? null;
      if (mode === "in" ? (regions.length === 0 ? region !== null
                                                : !regions.includes(region as string))
          : region !== null) {
        throw new Refusal("invalid", regions.length === 0 ? "this sign-in takes no region"
          : `this sign-in takes the owner's choice of region: ${regions.join(", ")}`);
      }
      const authority = signinAuthority(r["authority"]);
      if (authority !== OWNER_AUTHORITY && !o.grants.live(authority)) {
        throw new Refusal("refused",
                          "no such client: a client's own sandbox is signed in for a client " +
                          "the owner has connected");
      }
      const now = clock();
      o.signins.lost(now, new Set(o.files.liveLeases(now).map((each) => each.lease_id)));
      const live = o.files.sandboxLease(now, authority);
      const opened = live === null ? o.launcher.openLease(now, authority) : null;
      const lease = live ?? opened!.lease;
      const row = o.signins.begin(authority, rec.id, mode, region as string | null,
                                      { lease_id: lease.lease_id, epoch: lease.epoch }, now);
      return { row, opened, region: region as string | null,
               lease: { lease_id: lease.lease_id, epoch: lease.epoch } };
    });
    let answer: Started | null = null;
    try {
      answer = started(await o.env.SANDBOX!.signin(o.owner(), {
        id: begun.row.id, engine: begun.row.engine, region: begun.region, mode: begun.row.mode,
        authority: begun.row.authority, lease: begun.lease,
        tokens: begun.opened?.tokens ?? null, network: begun.opened?.network ?? null,
        route: sealRoute(o.env.ROUTING_MAC_KEY, { owner: o.owner(),
                                                     authority: begun.row.authority }),
        urls: { events: `${EGRESS_ORIGIN}/signin/${begun.row.id}/events`,
                signin: runUrls(`run_${"0".repeat(32)}`, "0".repeat(64)).signin } }));
    } catch {
      console.error("a sign-in's start had no answer");
      answer = null;
    }
    if (answer?.started === true) {
      return signinView(begun.row);
    }
    await o.door(() => {
      o.signins.end(begun.row.id);
      o.launcher.leaseFreed(begun.lease.lease_id, begun.lease.epoch);
    });
    return { started: false, code: answer === null ? "start_lost" : answer.code };
  },
  /** A sign-in's frames, its call checked in its unit (`signinEvents`). */
  async signinEvents(o: OwnerState, door: Door<SigninEventsRequest>):
      Promise<{ accepted: number; ended: boolean }> {
    const { row, ended, stopped, events, frames } = await o.door(() => {
      const lease = door.check().lease!;
      const r = eventsRequest({ frames: door.request.frames, ...(door.request.end === undefined ? {}
        : { end: door.request.end }) });
      const now = clock();
      const done = o.signins.frames(door.request.id, lease, r.frames.map((frame) => frame.event),
                                        r.end !== null, now);
      o.files.leaseRenew(lease.lease_id, lease.epoch);
      if (done.ended) {
        o.launcher.leaseFreed(lease.lease_id, lease.epoch);
      }
      return { ...done, frames: r.frames };
    });
    // Only what the trusted side kept: each frame's own, checked event.
    const last = frames.at(-1);
    o.relay.relay(row.id, events.map((event, i) => ({
      v: FRAME_VERSION, run: row.id, seq: (last?.seq ?? 0) - events.length + i + 1,
      at: last?.at ?? clock(), kind: "event" as const, event })));
    if (ended) {
      o.relay.settled(row.id);  // its tail goes with it
    }
    if (stopped) {
      // Its CLI stopped: what it showed is not the vendor's page, or it is past its deadline.
      await o.env.SANDBOX?.signinCancel(o.owner(), { authority: row.authority,
                                                                id: row.id })
        .catch(() => console.error("a refused sign-in's stop did not reach the sandbox"));
    }
    return { accepted: frames.length, ended };
  },
  async signinCode(o: OwnerState, door: Door<SigninCodeRequest>): Promise<{ delivered: boolean }> {
    const { row, code } = await o.door(() => {
      const { r } = door.check();
      const code = pastedCode(r["code"]);
      const row = o.signins.wantsCode(r["id"], clock());
      if (!switchedOn(o.storage, door.binding.signins, clock()).has(row.engine)) {
        throw new Refusal("signin_off", SIGNIN_OFF);
      }
      return { row, code };
    });
    const sandbox = o.env.SANDBOX;
    if (sandbox === undefined) {
      throw new Refusal("sandbox_absent", SANDBOX_ABSENT);
    }
    return sandbox.signinInput(o.owner(), { authority: row.authority, id: row.id,
                                                    code });
  },
  async signinCancel(o: OwnerState, door: Door<SigninRequestById>):
      Promise<{ cancelled: boolean }> {
    const row = await o.door(() => o.signins.get(door.check().r["id"]));
    const sandbox = o.env.SANDBOX;
    if (sandbox === undefined) {
      throw new Refusal("sandbox_absent", SANDBOX_ABSENT);
    }
    return sandbox.signinCancel(o.owner(), { authority: row.authority, id: row.id });
  },
  signinList(o: OwnerState, _call: Call<undefined>): { signins: SigninView[];
                                           engines: ReturnType<Runs["signins"]> } {
    const now = clock();
    o.signins.lost(now, new Set(o.files.liveLeases(now).map((each) => each.lease_id)));
    return { signins: o.signins.list().map(signinView), engines: o.runs.signins() };
  },
  signinReport(o: OwnerState, { lease: held, r }: Call<SigninRequest>):
      { recorded: number; lapsed: string[] } {
    const lease = held!;
    const reports = signinRequest(r);
    const lapsed: string[] = [];
    if (lease.authority !== OWNER_AUTHORITY) {
      // An outside client's sandbox's own sign-ins, kept on its lease: a login is never
      // copied between sandboxes, so none of the owner's own reaches it.
      const now = new Set(lease.signins);
      for (const report of reports) {
        if (report.signed_in) {
          now.add(report.engine);
        } else {
          now.delete(report.engine);
        }
      }
      const joined = [...now].sort().join(",");
      if (joined.length > SIGNIN_LIST_CHARS) {
        throw new Refusal("invalid",
                          `a sandbox's signed-in engines are at most ${SIGNIN_LIST_CHARS} ` +
                          "characters joined");
      }
      const before = o.files.leaseSignins(lease.lease_id, [...now]);
      for (const engine of before.filter((each) => !now.has(each))) {
        lapsed.push(engine);
        o.notices.add("signin_lapsed",
                          `${engine}: sign in again inside ${lease.authority}'s sandbox`, clock());
      }
      return { recorded: reports.length, lapsed };
    }
    for (const report of reports) {
      if (o.runs.reportSignin(report.engine, report.signed_in, report.models, clock())
            .lapsed) {
        lapsed.push(report.engine);
        o.notices.add("signin_lapsed", `${report.engine}: sign in again`, clock());
      }
    }
    return { recorded: reports.length, lapsed };
  },
});
