/**
 * Sandbox egress at the owner's object (gateway research §5.4 step 4; tasks 4 G4): each
 * request the sandbox's model clients send to the egress door is admitted here, at open,
 * and settled here once its bytes stopped flowing. The door (the binder) streams the bytes
 * through the gateway's `forward` in between, so the object does nothing meanwhile.
 *
 * **Open** (`open`, in the door's one unit, after the binding check admitted the owner for
 * `work`): the request's lease must be the live one, by its id and the hash of its token;
 * the generation its network's transform carries must be the owner's current policy
 * generation (`egress_policy_stale`), and so must the generation the sandbox's network was
 * last applied under (`egress_network_stale`), so a policy write leaves egress refused,
 * fail-closed, until the sandbox slice applies the new network; the connection must be the
 * owner's, active, with its credential usable; the method and path must be among the
 * connection's sealed endpoints (`egressTarget`, the gateway's own rule); and the owner
 * must be within the rate (`EGRESS_LIMITS`). Then one open reservation row, the blob at
 * its version, and the reservation's end signal. A refusal is one of the fixed codes
 * (`EgressCode`) and is kept as a receipt row, in the same unit.
 *
 * **Ending** (`end`): a policy write, a suspension, the owner's erasure, the connection's
 * revoke and the lease's close each end the in-flight streams they cover, once their own
 * unit committed: the object closes each one's end signal (a byte stream the open handed
 * the door, which the door watches and stops the stream on), and marks its row with why.
 * A stream whose signal is lost (this instance gone) is ended by the door too, since an
 * end signal that errors is an end.
 *
 * **Settle** (`settle`, `reduce`): the door reports each reservation's end once: `complete`,
 * `aborted` (the client stopped reading), `failed` (the gateway failed), `deadline` (the
 * door's own cut), or `ended` (the object's signal), with the provider's status and the
 * bytes the door streamed. A reservation never settled (the door's invocation lost) is
 * settled `lost` by the next open or settle past `LOST_AFTER_S`.
 *
 * Every row lives in `egress_reservations` (migration 14): at most `EGRESS_LIMITS.open`
 * open at once, and the newest `receiptsKept` others kept as receipts.
 *
 * Declared difference from research §6, which let a sandbox response already streaming
 * finish within the gateway's 5-minute deadline: here a policy write ends it, as it ends
 * the owner's in-flight turns.
 */
import { randomBytes } from "node:crypto";

import { type EgressScope, egressTarget } from "../../gateway/src/credential";

import { Invalid } from "./errors";
import type { OwnerStorage } from "./gate";
import type { SandboxLease } from "./files";
import { EGRESS_DEADLINE_S, type EgressCode, type Outcome, OUTCOMES } from "./network";

export { EGRESS_DEADLINE_S, type EgressCode, type Outcome };

/**
 * How much egress one owner holds: 64 requests open at once (a sandbox's clients stream a
 * few), 600 opened a minute, and the newest 2,000 closed rows kept as receipts.
 */
export interface EgressLimits {
  readonly open: number;
  readonly perMinute: number;
  readonly receiptsKept: number;
  readonly receiptsListed: number;
}

export const EGRESS_LIMITS: EgressLimits = Object.freeze({
  open: 64,
  perMinute: 600,
  receiptsKept: 2_000,
  receiptsListed: 50,
});

/** An open reservation older than this was its door's lost invocation's. */
export const LOST_AFTER_S = EGRESS_DEADLINE_S + 60;
/** The longest path an egress request names, as its row keeps it. */
export const EGRESS_PATH_MAX = 2048;

/** Why the object ended a stream. */
export type EndedBy = "policy" | "lease" | "connection" | "admission" | "erasure";

/** What the binder knows of the sandbox from its token (`Binding.sandbox`). */
export interface SandboxBinding {
  /** The lease id the directory's entry names. */
  readonly lease: string;
  /** The SHA-256 hex of the lease token the request presented. */
  readonly token: string;
  /** The policy generation the network's transform set on the request. */
  readonly generation: number;
}

export interface EgressRequest {
  readonly connection: string;
  readonly method: string;
  /** The path (and query) after `/c/<connection>`, as the provider's API names it. */
  readonly path: string;
  /** The request body's declared length, or null. */
  readonly bytes_hint: number | null;
}

/** An admitted request: what the door forwards with, and the object's end signal. */
export interface EgressOpened {
  readonly ok: true;
  readonly reservation: string;
  readonly connection: string;
  readonly blob: string;
  readonly version: number;
  readonly generation: number;
  /** Closed (or errored) when the object ends the stream; the door watches it. */
  readonly ended: ReadableStream<Uint8Array>;
}

export interface EgressRefusal {
  readonly ok: false;
  readonly code: EgressCode;
  /** The refusal's receipt row. */
  readonly receipt: string;
}

export interface SettleRequest {
  readonly reservation: string;
  readonly outcome: Outcome;
  readonly status: number | null;
  readonly bytes: number;
}

/** What one reservation row shows its owner. */
export interface EgressReceipt {
  readonly reservation: string;
  readonly connection: string;
  readonly method: string;
  readonly path: string;
  readonly state: "open" | "settled" | "refused";
  readonly code: string | null;
  readonly ended: EndedBy | null;
  readonly outcome: string | null;
  readonly status: number | null;
  readonly bytes: number | null;
  readonly opened_at: number;
  readonly settled_at: number | null;
}

/** What `open` reads beside its own rows, each at the open's unit. */
export interface EgressDeps {
  readonly lease: (now: number) => SandboxLease | null;
  readonly generation: () => number;
  /**
   * The connection's blob at its version, or null for one that is not the owner's, not
   * active, or whose credential has expired.
   */
  readonly credential: (connection: string, nowMs: number) =>
    { readonly blob: string; readonly version: number } | null;
  readonly scope: (connection: string) => EgressScope | null;
}

const METHODS: readonly string[] = Object.freeze(["GET", "HEAD", "POST", "PUT", "PATCH",
                                                  "DELETE"]);
const CONNECTION = /^c[0-9a-f]{24}$/;
const HEX32 = /^[0-9a-f]{32}$/;
const HEX64 = /^[0-9a-f]{64}$/;

type Row = {
  id: string; lease_id: string; epoch: number | null; connection_id: string;
  version: number | null; method: string; path: string; generation: number;
  bytes_hint: number | null; opened_at: number; state: EgressReceipt["state"];
  code: string | null; ended: EndedBy | null; outcome: string | null; status: number | null;
  bytes: number | null; settled_at: number | null;
};

/** The request's fields, exactly, or `Invalid`. */
export function egressRequest(value: unknown): EgressRequest {
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || Object.keys(value).sort().join() !== "bytes_hint,connection,method,path") {
    throw new Invalid("invalid egress request");
  }
  const { connection, method, path, bytes_hint: hint } = value as Record<string, unknown>;
  if (typeof connection !== "string" || !CONNECTION.test(connection)
      || typeof method !== "string" || !METHODS.includes(method)
      || typeof path !== "string" || !path.startsWith("/") || path.length > EGRESS_PATH_MAX
      || (hint !== null && (typeof hint !== "number" || !Number.isSafeInteger(hint) || hint < 0))) {
    throw new Invalid("invalid egress request");
  }
  return { connection, method, path, bytes_hint: hint };
}

/** The binding's sandbox fields, exactly, or `Invalid`. */
export function sandboxBinding(value: unknown): SandboxBinding {
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || Object.keys(value).sort().join() !== "generation,lease,token") {
    throw new Invalid("invalid sandbox binding");
  }
  const { lease, token, generation } = value as Record<string, unknown>;
  if (typeof lease !== "string" || !HEX32.test(lease) || typeof token !== "string"
      || !HEX64.test(token) || typeof generation !== "number"
      || !Number.isSafeInteger(generation) || generation < 1) {
    throw new Invalid("invalid sandbox binding");
  }
  return { lease, token, generation };
}

/** The settle's fields, exactly, or `Invalid`. */
export function settleRequest(value: unknown): SettleRequest {
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || Object.keys(value).sort().join() !== "bytes,outcome,reservation,status") {
    throw new Invalid("invalid egress settle");
  }
  const { reservation, outcome, status, bytes } = value as Record<string, unknown>;
  if (typeof reservation !== "string" || !HEX32.test(reservation)
      || typeof outcome !== "string" || !OUTCOMES.includes(outcome)
      || (status !== null && (typeof status !== "number" || !Number.isSafeInteger(status)
                              || status < 100 || status > 599))
      || typeof bytes !== "number" || !Number.isSafeInteger(bytes) || bytes < 0) {
    throw new Invalid("invalid egress settle");
  }
  return { reservation, outcome: outcome as Outcome, status: status as number | null, bytes };
}

export class Egress {
  readonly #storage: OwnerStorage;
  readonly #deps: EgressDeps;
  readonly #limits: EgressLimits;
  /** Each open reservation's end signal, by reservation, with what it covers. */
  readonly #live = new Map<string, { readonly connection: string; readonly lease: string;
                                     readonly end: WritableStreamDefaultWriter<Uint8Array> }>();

  constructor(storage: OwnerStorage, deps: EgressDeps, limits: EgressLimits = EGRESS_LIMITS) {
    this.#storage = storage;
    this.#deps = deps;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /**
   * In the door's unit: the request admitted, an open reservation and its end signal; or
   * refused, with its fixed code, kept as a receipt row.
   */
  open(sandbox: SandboxBinding, request: EgressRequest, now: number):
      EgressOpened | EgressRefusal {
    this.#recover(now);
    const lease = this.#deps.lease(now);
    const generation = this.#deps.generation();
    const refuse = (code: EgressCode): EgressRefusal =>
      ({ ok: false, code, receipt: this.#record(sandbox, request, lease, generation, now,
                                                { code }) });
    if (lease === null || lease.lease_id !== sandbox.lease || lease.token !== sandbox.token) {
      return refuse("egress_lease_closed");
    }
    if (sandbox.generation !== generation) {
      return refuse("egress_policy_stale");
    }
    if (lease.network_generation !== generation) {
      return refuse("egress_network_stale");
    }
    const credential = this.#deps.credential(request.connection, now * 1000);
    const scope = this.#deps.scope(request.connection);
    if (credential === null || scope === null) {
      return refuse("egress_connection_refused");
    }
    if (egressTarget(scope, request.method, request.path) === null) {
      return refuse("egress_endpoint_refused");
    }
    if (this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'open'")
          >= this.#limits.open
        || this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE opened_at > ? " +
                         "AND state != 'refused'", now - 60) >= this.#limits.perMinute) {
      return refuse("egress_rate");
    }
    const reservation = this.#record(sandbox, request, lease, generation, now,
                                     { version: credential.version });
    const { readable, writable } = new IdentityTransformStream();
    this.#live.set(reservation, { connection: request.connection, lease: lease.lease_id,
                                  end: writable.getWriter() });
    return { ok: true, reservation, connection: request.connection, blob: credential.blob,
             version: credential.version, generation, ended: readable };
  }

  /**
   * In the door's unit: a reservation's one end, as the door saw it. False for one already
   * settled, lost or refused, or not this lease's.
   */
  settle(sandbox: SandboxBinding, request: SettleRequest, now: number):
      { settled: false } | { settled: true; connection: string; version: number } {
    this.#recover(now);
    const row = this.#sql.exec<Row>("SELECT * FROM egress_reservations WHERE id = ?",
                                    request.reservation).toArray()[0];
    if (row === undefined || row.state !== "open" || row.lease_id !== sandbox.lease
        || row.version === null) {
      return { settled: false };
    }
    this.#sql.exec("UPDATE egress_reservations SET state = 'settled', outcome = ?, status = ?, " +
                     "bytes = ?, settled_at = ? WHERE id = ?",
                   request.outcome, request.status, request.bytes, now, request.reservation);
    this.#release(request.reservation);
    this.#prune();
    return { settled: true, connection: row.connection_id, version: row.version };
  }

  /**
   * In a unit, once the change that ends them committed: every open stream `covers` takes
   * (all of them by default) ended, its signal closed and its row marked `why`.
   */
  end(why: EndedBy, covers: (live: { connection: string; lease: string }) => boolean =
        () => true): number {
    let ended = 0;
    for (const [reservation, live] of [...this.#live]) {
      if (!covers(live)) {
        continue;
      }
      this.#sql.exec("UPDATE egress_reservations SET ended = ? WHERE id = ? AND state = 'open' " +
                       "AND ended IS NULL", why, reservation);
      this.#release(reservation);
      ended++;
    }
    return ended;
  }

  /**
   * Every end signal closed, writing nothing: the owner's erasure, whose wipe takes the
   * rows. Each door then settles into a refusal, and its stream is over.
   */
  drop(): void {
    for (const reservation of [...this.#live.keys()]) {
      this.#release(reservation);
    }
  }

  /** The newest receipts, newest first: what was admitted, refused and how each ended. */
  receipts(): EgressReceipt[] {
    return this.#sql.exec<Row>("SELECT * FROM egress_reservations ORDER BY opened_at DESC, " +
                                 "rowid DESC LIMIT ?", this.#limits.receiptsListed).toArray()
      .map((row) => ({ reservation: row.id, connection: row.connection_id, method: row.method,
                       path: row.path, state: row.state, code: row.code, ended: row.ended,
                       outcome: row.outcome, status: row.status, bytes: row.bytes,
                       opened_at: row.opened_at, settled_at: row.settled_at }));
  }

  #count(statement: string, ...params: SqlStorageValue[]): number {
    return this.#sql.exec<{ n: number }>(statement, ...params).one().n;
  }

  #record(sandbox: SandboxBinding, request: EgressRequest, lease: SandboxLease | null,
          generation: number, now: number,
          fields: { code?: EgressCode; version?: number }): string {
    const id = randomBytes(16).toString("hex");
    this.#sql.exec(
      "INSERT INTO egress_reservations (id, lease_id, epoch, connection_id, version, method, " +
        "path, generation, bytes_hint, opened_at, state, code, settled_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      id, sandbox.lease, lease?.lease_id === sandbox.lease ? lease.epoch : null,
      request.connection, fields.version ?? null, request.method, request.path, generation,
      request.bytes_hint, now, fields.code === undefined ? "open" : "refused",
      fields.code ?? null, fields.code === undefined ? null : now);
    this.#prune();
    return id;
  }

  /** Its end signal closed and forgotten; the door sees the close as the end. */
  #release(reservation: string): void {
    const live = this.#live.get(reservation);
    this.#live.delete(reservation);
    live?.end.close().catch(() => undefined);  // a door that stopped reading is gone already
  }

  /** Open rows past `LOST_AFTER_S`: their doors' invocations were lost. */
  #recover(now: number): void {
    const lost = this.#sql.exec<{ id: string }>(
      "UPDATE egress_reservations SET state = 'settled', outcome = 'lost', settled_at = ? " +
        "WHERE state = 'open' AND opened_at <= ? RETURNING id", now, now - LOST_AFTER_S)
      .toArray();
    for (const { id } of lost) {
      this.#release(id);
    }
  }

  /** The closed rows past the newest `receiptsKept`, oldest first. */
  #prune(): void {
    const over = this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE state != 'open'")
      - this.#limits.receiptsKept;
    if (over > 0) {
      this.#sql.exec("DELETE FROM egress_reservations WHERE id IN (SELECT id FROM " +
                       "egress_reservations WHERE state != 'open' ORDER BY opened_at, rowid " +
                       "LIMIT ?)", over);
    }
  }
}
