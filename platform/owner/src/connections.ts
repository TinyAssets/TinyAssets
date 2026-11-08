/**
 * The owner's model connections (gateway research §1.4, §2.6, §7; tasks 4 G2): what each
 * one is, the blob the gateway sealed its credential into, the OAuth flows that connect
 * one, the refresh lease, and the `connections` lane's housekeeping.
 *
 * **No plaintext enters the object.** The binder hands a pasted key to `GATEWAY.seal`
 * and an OAuth code to `GATEWAY.exchange`; the object stores only what comes back, the
 * sealed blob, which only the gateway can open, and only for this owner, connection and
 * version (the seal's additional data). The Python's door-only exchange had the same
 * rule (`broker/model_connect.py`: "no credential leaves the broker").
 *
 * The steps of a connect, each a door in the owner's live session (`owner-do.ts`):
 * 1. `prepare`: a pending row, its id and version 1, which the binder seals for, and
 *    whether its provider can be asked to revoke what it issues (`revocable`, from the
 *    source record). A row not committed within `pendingTtlS` is swept by the lane. For
 *    OAuth, the same unit begins the flow, bound to the session that prepares it: the
 *    SHA-256 of a fresh state and a fresh PKCE verifier (RFC 7636), for `flowTtlS`, at most
 *    `maxOpenFlows` open, so a refused flow leaves no pending row. The binder builds the
 *    authorize URL from the state and the challenge.
 * 2. For OAuth, the callback's `flowFinish`, in the same session, uses the flow up and
 *    hands back the verifier for the gateway's exchange.
 * 3. `commit`: the sealed blob, its kid, the models the gateway's setup check listed (each
 *    its whole pi-ai capability record, which the object has no catalog for: `ModelRecord`),
 *    where sandbox egress may forward its credential (`egress`: the sealed base URL and
 *    endpoints the check read from the blob, no secret; the egress door's admission reads
 *    it, G4) and, for OAuth, the access expiry in clear.
 *    A credential the provider issued whose setup check then failed is committed `failed`:
 *    the connection is in `error`, and a revocable one's blob is kept for the lane's
 *    provider revoke; an unrevocable one's is deleted, with an owner notice to delete it
 *    at the provider. A minted credential is never dropped silently.
 *
 * A revoke makes the connection unusable in its own unit. An API key's blob is deleted
 * there and then (crypto-delete: nothing can open what is gone). An OAuth blob holds the
 * only copy of the tokens the provider must be asked to revoke (RFC 7009), so it is kept,
 * unusable, until the lane's `GATEWAY.revokeAtProvider` succeeds, or `revokeTries` attempts
 * have failed (then it is deleted anyway, with an owner notice: the provider may still
 * hold a live token). A credential its provider cannot be asked to revoke (`revocable`
 * false: a pasted key, or a key-exchange source's key) is deleted in the revoke's unit,
 * and one the provider issued gets an owner notice to delete it there. A refresh that lands after the revoke keeps its rotated tokens for the lane to
 * revoke too, whether or not the lane already revoked the old ones. A call that already
 * holds the blob completes; the bound is one call.
 * Account revocation (`sessionCloseAll`) and a session's sign-out cancel the session's
 * open flows in their own unit, as the Python's `revoke_owner` did, so a late callback
 * finds no flow.
 *
 * **The refresh lease.** An OAuth access token expiring within `refreshMarginMs` is
 * refreshed before a call. The first caller takes the lease on the row (its id and
 * `leaseMs`) in a unit and runs `GATEWAY.refresh`; concurrent callers in the object wait
 * on that one refresh, so a rotating refresh token is sent once (pi-ai's own rule, moved
 * into the owner's single-threaded object). The result is stored by compare-and-swap at
 * the next version, under the lease. A lease no refresh of this instance holds was a dead
 * instance's (an object has one live instance), so the next caller takes it over. A
 * blob the gateway resealed under a newer kid (`x-ta-resealed`) is stored by the same
 * compare-and-swap, never while a refresh holds the lease, so a reseal of the old tokens
 * can never overwrite the refreshed ones.
 *
 * Every write is a unit of the object's gate, and each unit reads what it acts on: a door's
 * body, or a step between the lane's or a call's outside calls. Bounds: `maxConnections`
 * live (pending, active or failed) connections; the newest `maxRevokedKept` revoked ones
 * that hold no blob; `maxFlowRows` flow rows, swept at their expiry.
 */
import { CONNECTION_ID, randomHex } from "../../contract/src/ids";
import { sha256Hex } from "../../contract/src/digest";
import { createHash, randomBytes } from "node:crypto";


import { egressScope, type EgressScope } from "../../contract/src/egress-scope";
import { base64url, KID, parseBlob } from "../../contract/src/envelope";
import { checkedRecord, type ModelRecord } from "../../contract/src/model-record";

import type { GatewayService, Refreshed } from "../../contract/src/gateway";
import { isGatewayCode, Refusal, refusalOf } from "../../contract/src/refusal";
import {
  CONNECTION_BLOB_MAX, EGRESS_JSON_MAX, MODELS_JSON_MAX, REDIRECT_URI_MAX,
} from "../../contract/src/limits";
import type { OwnerStorage, Write } from "./gate";
import type { Notices } from "./notices";
import { inTime, LANE_HUNG_S, LANE_PASS_S } from "./schedule";
import { codePoints, isPrintable, SOURCE_ID, utf8Bytes } from "../../contract/src/text";
import type {
  Call, Door, OwnerState,
} from "./owner-do";
import { clock } from "./clock";

export interface ConnectionLimits {
  readonly maxConnections: number;
  readonly maxRevokedKept: number;
  readonly pendingTtlS: number;
  readonly flowTtlS: number;
  readonly maxOpenFlows: number;
  readonly maxFlowRows: number;
  readonly maxModels: number;
  readonly refreshMarginMs: number;
  readonly leaseMs: number;
  readonly revokeTries: number;
  readonly revokeRetryS: number;
  /** How long the lane waits on one provider revoke: the rest of its hung timeout. */
  readonly revokeMs: number;
  /** Provider revokes one lane pass takes. */
  readonly revokesPerPass: number;
}

/**
 * Flows last 10 minutes and at most 10 are open (the Python's `MAX_PER_OWNER`); a refresh
 * starts 3 minutes before the access token expires (research §2.6); a lease outlasts the
 * gateway's 15 s token-endpoint bound. A connection keeps up to 1,024 models: the largest
 * built-in catalog of pi-ai 1.4.0 has 386 (the connections test finds it by a scan, and
 * holds `MODELS_JSON_MAX` to 1,024 of the largest built-in record).
 */
export const CONNECTION_LIMITS: ConnectionLimits = Object.freeze({
  maxConnections: 50,
  maxRevokedKept: 50,
  pendingTtlS: 600,
  flowTtlS: 600,
  maxOpenFlows: 10,
  maxFlowRows: 50,
  maxModels: 1_024,
  refreshMarginMs: 3 * 60 * 1000,
  leaseMs: 60 * 1000,
  revokeTries: 5,
  revokeRetryS: 300,
  revokeMs: (LANE_HUNG_S - LANE_PASS_S) * 1000,
  revokesPerPass: 8,
});

/** What the owner is shown of a connection: never its blob. */
export interface ConnectionView {
  readonly connection: string;
  readonly source: string;
  readonly kind: "api_key" | "oauth2";
  readonly display_name: string;
  readonly provider: string;
  readonly status: "pending" | "active" | "revoked" | "error";
  readonly version: number;
  readonly models: string[];
  readonly expires_at: number | null;
  readonly created_at: number;
  readonly revoked_at: number | null;
  /** Whether its provider can be asked to revoke its credential (RFC 7009). */
  readonly provider_revocable: boolean;
  readonly provider_revoke_pending: boolean;
}

/** A prepared connection; for OAuth, the flow begun with it. */
export interface Prepared {
  readonly connection: string;
  readonly version: number;
  readonly expires_at: number;
  readonly flow?: { readonly state: string; readonly code_challenge: string;
                    readonly code_challenge_method: "S256"; readonly expires_at: number };
}

/** A connection a model call may use: its models, as `getModels` lists them. */
export interface Usable {
  readonly connection: string;
  /** Its source record, which its spend is counted and capped under. */
  readonly source: string;
  readonly provider: string;
  readonly display_name: string;
  readonly created_at: number;
  readonly models: readonly ModelRecord[];
}

/** The credential a call carries in its envelope. */
export interface Credential {
  readonly connection: string;
  readonly version: number;
  readonly blob: string;
}

type Row = {
  id: string; source: string; kind: "api_key" | "oauth2"; display_name: string;
  provider: string; models: string; status: ConnectionView["status"]; version: number;
  expires_at: number | null; lease_id: string | null; lease_until: number | null;
  created_at: number; pending_until: number | null; revoked_at: number | null;
  revoke_due: number | null; revoke_tries: number; revocable: number;
  egress: string | null;
};

/** How a provider revoke ended: confirmed, impossible (no endpoint), or not confirmed. */
type RevokeOutcome = "revoked" | "not_revocable" | "failed";

export const CONNECTION_UNAVAILABLE = "connection_unavailable";
export const CREDENTIAL_EXPIRED = "credential_expired";

const PROVIDER = /^[a-z0-9][a-z0-9-]{0,63}$/;
const STATE = /^[A-Za-z0-9_-]{43}$/;

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Exactly `required`, plus any of `optional`, none `undefined`. */
function fields(request: unknown, required: readonly string[],
                optional: readonly string[] = []): Record<string, unknown> {
  if (!object(request)) {
    throw new Refusal("invalid", "invalid connection request");
  }
  const keys = Object.keys(request);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))
      || Object.values(request).some((value) => value === undefined)) {
    throw new Refusal("invalid", "invalid connection request");
  }
  return request;
}

function text(value: unknown, max: number, what: string): string {
  if (typeof value !== "string" || !value || codePoints(value) > max || !isPrintable(value)) {
    throw new Refusal("invalid", `${what} is 1 to ${max} printable characters`);
  }
  return value;
}

function grammar(value: unknown, pattern: RegExp, what: string): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new Refusal("invalid", `invalid ${what}`);
  }
  return value;
}

function count(value: unknown, max: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
}

/**
 * One model of the gateway's setup check, its whole record checked by the one rule the
 * gateway seals by (`contract/src/model-record.ts`), and kept as the gateway sent it.
 */
function modelInfo(value: unknown): ModelRecord {
  try {
    checkedRecord(value);
  } catch {
    throw new Refusal("invalid", "invalid connection model");
  }
  return value as ModelRecord;
}

/** A sealed blob as the gateway writes it, under `kid` (`contract/src/envelope.ts`). */
function blobOf(value: unknown, kid: string): string {
  if (parseBlob(value, CONNECTION_BLOB_MAX)?.kid !== kid) {
    throw new Refusal("invalid", "invalid sealed credential");
  }
  return value as string;
}

/**
 * A committed connection's egress scope as stored: the gateway's own grammar
 * (`egressScope`), whole, within `EGRESS_JSON_MAX`.
 */
function scopeJson(value: unknown): string {
  let scope: EgressScope;
  try {
    scope = egressScope(value);
  } catch {
    throw new Refusal("invalid", "invalid connection egress scope");
  }
  const json = JSON.stringify(scope);
  if (utf8Bytes(json) > EGRESS_JSON_MAX) {
    throw new Refusal("invalid", "invalid connection egress scope");
  }
  return json;
}

/** An https URL with no fragment, as an OAuth redirect URI must be (RFC 6749 §3.1.2). */
function redirectUri(value: unknown): string {
  if (typeof value !== "string" || value.length > REDIRECT_URI_MAX) {
    throw new Refusal("invalid", "invalid redirect URI");
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Refusal("invalid", "invalid redirect URI");
  }
  if (url.protocol !== "https:" || url.hash !== "" || value.includes("#")) {
    throw new Refusal("invalid", "invalid redirect URI");
  }
  return value;
}

function view(row: Row): ConnectionView {
  return {
    connection: row.id, source: row.source, kind: row.kind, display_name: row.display_name,
    provider: row.provider, status: row.status, version: row.version,
    models: (JSON.parse(row.models) as ModelRecord[]).map((model) => model.id),
    expires_at: row.expires_at, created_at: row.created_at, revoked_at: row.revoked_at,
    provider_revocable: row.revocable === 1, provider_revoke_pending: row.revoke_due !== null,
  };
}

export class Connections {
  readonly #storage: OwnerStorage;
  readonly #write: Write;
  readonly #gateway: () => GatewayService | undefined;
  readonly #owner: () => string;
  readonly #notices: Notices;
  readonly #clock: () => number;
  readonly #limits: ConnectionLimits;
  /** The refreshes this instance runs, by connection: what concurrent callers wait on. */
  readonly #refreshing = new Map<string, Promise<void>>();
  /** Bumped by every unit that changes a connection, so `usable` re-reads only then. */
  #revision = 0;
  #usable: { revision: number; usable: readonly Usable[] } | null = null;

  constructor(storage: OwnerStorage, write: Write, gateway: () => GatewayService | undefined,
              owner: () => string, notices: Notices, clock: () => number,
              limits: ConnectionLimits = CONNECTION_LIMITS) {
    this.#storage = storage;
    this.#write = write;
    this.#gateway = gateway;
    this.#owner = owner;
    this.#notices = notices;
    this.#clock = clock;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #row(id: string): Row | undefined {
    return this.#sql.exec<Row>("SELECT * FROM connections WHERE id = ?", id).toArray()[0];
  }

  #changed(): void {
    this.#revision++;
  }

  #service(): GatewayService {
    const gateway = this.#gateway();
    if (gateway === undefined) {
      throw new Error("the GATEWAY service is not bound");  // a config fault: loud
    }
    return gateway;
  }

  // -- the doors' bodies, each run inside the door's unit ---------------------------

  /**
   * A pending connection, for the binder to seal a credential for at version 1; for OAuth,
   * its flow begun in the same unit, bound to `sessionId`. Every check runs before any
   * write, so a refusal leaves nothing behind.
   */
  prepare(sessionId: string, request: unknown, now: number): Prepared {
    const r = fields(request, ["source", "kind", "display_name", "provider", "revocable"],
                     ["redirect_uri"]);
    const source = grammar(r["source"], SOURCE_ID, "source");
    const provider = grammar(r["provider"], PROVIDER, "provider");
    const kind = r["kind"];
    if (kind !== "api_key" && kind !== "oauth2") {
      throw new Refusal("invalid", "a connection is an api_key or an oauth2");
    }
    const revocable = r["revocable"];
    if (typeof revocable !== "boolean" || (kind === "api_key" && revocable)) {
      throw new Refusal("invalid", "revocable is a boolean, and false for a pasted key");
    }
    if ((kind === "oauth2") !== (r["redirect_uri"] !== undefined)) {
      throw new Refusal("invalid",
                        "an OAuth connection begins its flow at a redirect URI; a key has none");
    }
    const redirect = kind === "oauth2" ? redirectUri(r["redirect_uri"]) : null;
    const name = text(r["display_name"], 128, "display_name");
    const live = this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM connections WHERE status != 'revoked'").one().n;
    if (live >= this.#limits.maxConnections) {
      throw new Refusal("refused", "the owner's connections are at their limit");
    }
    if (redirect !== null) {
      const flows = this.#sql.exec<{ open: number; rows: number }>(
        "SELECT COALESCE(SUM(status = 'open' AND expires_at > ?), 0) AS open, " +
          "COUNT(*) AS rows FROM connection_flows", now).one();
      if (flows.open >= this.#limits.maxOpenFlows || flows.rows >= this.#limits.maxFlowRows) {
        throw new Refusal("refused", "too many connection flows; finish or wait for one");
      }
    }
    const id = `c${randomHex(12)}`;
    const until = now + this.#limits.pendingTtlS;
    this.#sql.exec("INSERT INTO connections (id, source, kind, display_name, provider, " +
                     "status, version, created_at, pending_until, revocable) " +
                     "VALUES (?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?)",
                   id, source, kind, name, provider, now, until, revocable ? 1 : 0);
    this.#changed();
    const prepared = { connection: id, version: 1, expires_at: until };
    return redirect === null ? prepared
      : { ...prepared, flow: this.#beginFlow(id, sessionId, redirect, now) };
  }

  /**
   * A pending connection's sealed blob and models: the connection is active. Committed
   * `failed` (the setup check's code), a credential the provider issued is kept out of
   * use: the connection is in `error`, a revocable blob kept for the lane's provider
   * revoke, an unrevocable one deleted with an owner notice.
   */
  commit(request: unknown, now: number): ConnectionView {
    const r = fields(request, ["connection", "version", "blob", "kid", "models"],
                     ["expires_at", "failed", "egress"]);
    const id = grammar(r["connection"], CONNECTION_ID, "connection");
    const kid = grammar(r["kid"], KID, "kid");
    const blob = blobOf(r["blob"], kid);
    const models = r["models"];
    if (!Array.isArray(models) || models.length > this.#limits.maxModels) {
      throw new Refusal("invalid", "invalid connection models");
    }
    const checked = models.map(modelInfo);
    if (new Set(checked.map((model) => model.id)).size !== checked.length
        || utf8Bytes(JSON.stringify(checked)) > MODELS_JSON_MAX) {
      throw new Refusal("invalid", "invalid connection models");
    }
    const row = this.#row(id);
    if (row === undefined || row.status !== "pending" || row.version !== r["version"]
        || row.pending_until === null || row.pending_until <= now) {
      throw new Refusal("refused", "no pending connection at this version");
    }
    const expires = r["expires_at"] ?? null;
    if (row.kind === "api_key" ? expires !== null
      : expires !== null && !count(expires, Number.MAX_SAFE_INTEGER)) {
      throw new Refusal("invalid",
                        "an OAuth connection's expiry is epoch ms or null; a key has none");
    }
    const failed = r["failed"];
    if (failed !== undefined) {
      // A setup check's failure: one of the gateway's fixed codes.
      if (!isGatewayCode(failed)
          || checked.length > 0
          || row.kind !== "oauth2") {
        throw new Refusal("invalid",
                          "a failed setup commits an issued credential, its code and no models");
      }
      if (r["egress"] !== undefined) {
        throw new Refusal("invalid", "a failed setup commits no egress scope");
      }
      return this.#storage.transactionSync(() => this.#failed(row, kid, blob, expires, failed,
                                                              now));
    }
    const egress = scopeJson(r["egress"]);
    return this.#storage.transactionSync(() => {
      this.#sql.exec("DELETE FROM connection_secrets WHERE connection_id = ?", id);
      this.#sql.exec("INSERT INTO connection_secrets (connection_id, version, kid, blob) " +
                       "VALUES (?, ?, ?, ?)", id, row.version, kid, blob);
      this.#sql.exec("UPDATE connections SET status = 'active', pending_until = NULL, " +
                       "models = ?, expires_at = ?, egress = ? WHERE id = ?",
                     JSON.stringify(checked), expires, egress, id);
      this.#sql.exec("UPDATE connection_flows SET status = 'cancelled', verifier = NULL " +
                       "WHERE connection_id = ? AND status = 'open'", id);
      this.#changed();
      return view(this.#row(id)!);
    });
  }

  /**
   * In the commit's unit: an issued credential whose setup failed. A revocable one's blob
   * is kept, its provider revoke due now; an unrevocable one's is never stored, and the
   * owner is told to delete it at the provider.
   */
  #failed(row: Row, kid: string, blob: string, expires: unknown, code: string,
          now: number): ConnectionView {
    const owed = row.revocable === 1;
    this.#sql.exec("DELETE FROM connection_secrets WHERE connection_id = ?", row.id);
    if (owed) {
      this.#sql.exec("INSERT INTO connection_secrets (connection_id, version, kid, blob) " +
                       "VALUES (?, ?, ?, ?)", row.id, row.version, kid, blob);
    } else {
      this.#notices.add("connection_setup_failed",
                        `${row.id}: the setup check failed (${code}) after the provider ` +
                          "issued a credential; it cannot be revoked from here, so delete " +
                          "it at the provider", now);
    }
    this.#sql.exec("UPDATE connections SET status = 'error', pending_until = NULL, " +
                     "expires_at = ?, revoke_due = ?, revoke_tries = 0 WHERE id = ?",
                   expires, owed ? now : null, row.id);
    this.#sql.exec("UPDATE connection_flows SET status = 'cancelled', verifier = NULL " +
                     "WHERE connection_id = ? AND status = 'open'", row.id);
    this.#changed();
    return view(this.#row(row.id)!);
  }

  /**
   * The connection, unusable from this unit on: a revocable credential's blob kept only
   * for the lane's provider revoke, any other deleted now (an issued one with an owner
   * notice to delete it at the provider). Revoking twice changes nothing.
   */
  revoke(request: unknown, now: number): { connection: string; revoked: boolean;
                                           provider_revoke: boolean } {
    const r = fields(request, ["connection"]);
    const id = grammar(r["connection"], CONNECTION_ID, "connection");
    const row = this.#row(id);
    if (row === undefined) {
      throw new Refusal("refused", "no such connection");
    }
    if (row.status === "revoked") {
      return { connection: id, revoked: false, provider_revoke: row.revoke_due !== null };
    }
    const held = this.#sql.exec("SELECT 1 FROM connection_secrets WHERE connection_id = ?", id)
      .toArray().length > 0;
    const owed = held && row.revocable === 1;
    return this.#storage.transactionSync(() => {
      if (held && !owed && row.kind === "oauth2") {
        this.#notices.add("connection_revoke_unsupported",
                          `${id}: the provider cannot be asked to revoke this credential; ` +
                            "it was deleted here, so delete it at the provider", now);
      }
      this.#sql.exec("UPDATE connections SET status = 'revoked', revoked_at = ?, " +
                       "pending_until = NULL, lease_id = NULL, lease_until = NULL, egress = NULL, " +
                       "revoke_due = ?, revoke_tries = 0 WHERE id = ?",
                     now, owed ? now : null, id);
      if (!owed) {
        this.#sql.exec("DELETE FROM connection_secrets WHERE connection_id = ?", id);
      }
      this.#sql.exec("UPDATE connection_flows SET status = 'cancelled', verifier = NULL " +
                       "WHERE connection_id = ? AND status = 'open'", id);
      this.#pruneRevoked();
      this.#changed();
      return { connection: id, revoked: true, provider_revoke: owed };
    });
  }

  /** Revoked rows past the newest `maxRevokedKept`, once they hold no blob. */
  #pruneRevoked(): void {
    this.#sql.exec("DELETE FROM connections WHERE id IN (SELECT id FROM connections " +
                     "WHERE status = 'revoked' AND revoke_due IS NULL " +
                     "ORDER BY revoked_at DESC LIMIT -1 OFFSET ?)", this.#limits.maxRevokedKept);
  }

  /** Every connection, newest first, without its blob. */
  list(): ConnectionView[] {
    return this.#sql.exec<Row>("SELECT * FROM connections ORDER BY created_at DESC, id")
      .toArray().map(view);
  }

  /**
   * In the prepare's unit: the OAuth flow of the connection just prepared, bound to the
   * session that prepares it: the state and the PKCE challenge the binder's authorize URL
   * carries.
   */
  #beginFlow(id: string, sessionId: string, redirect: string, now: number):
      NonNullable<Prepared["flow"]> {
    const state = base64url(randomBytes(32));
    const verifier = base64url(randomBytes(32));
    const challenge = createHash("sha256").update(verifier, "ascii").digest("base64url");
    const until = now + this.#limits.flowTtlS;
    this.#sql.exec("INSERT INTO connection_flows (flow, connection_id, session_id, verifier, " +
                     "redirect_uri, created_at, expires_at, status) " +
                     "VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
                   sha256Hex(state), id, sessionId, verifier, redirect, now, until);
    return { state, code_challenge: challenge, code_challenge_method: "S256", expires_at: until };
  }

  /**
   * The callback's state, in the session that began its flow: the flow is used up, and
   * the verifier and redirect URI go to the gateway's exchange, with the source record
   * the connection was prepared for.
   */
  flowFinish(sessionId: string, request: unknown, now: number):
      { connection: string; version: number; verifier: string; redirect_uri: string;
        source: string } {
    const r = fields(request, ["state"]);
    const state = grammar(r["state"], STATE, "state");
    const flow = this.#sql.exec<{ connection_id: string; session_id: string; verifier: string;
                                  redirect_uri: string }>(
      "SELECT connection_id, session_id, verifier, redirect_uri FROM connection_flows " +
        "WHERE flow = ? AND status = 'open' AND expires_at > ?", sha256Hex(state), now)
      .toArray()[0];
    if (flow === undefined || flow.session_id !== sessionId) {
      throw new Refusal("refused", "no open connection flow for this state in this session");
    }
    const row = this.#row(flow.connection_id);
    if (row === undefined || row.status !== "pending") {
      throw new Refusal("refused", "no open connection flow for this state in this session");
    }
    this.#sql.exec("UPDATE connection_flows SET status = 'used', verifier = NULL WHERE flow = ?",
                   sha256Hex(state));
    return { connection: row.id, version: row.version, source: row.source,
             verifier: flow.verifier, redirect_uri: flow.redirect_uri };
  }

  /** Cancel the open flows of one session, or of every session (`null`). */
  cancelFlows(sessionId: string | null): number {
    return this.#sql.exec("UPDATE connection_flows SET status = 'cancelled', verifier = NULL " +
                            "WHERE status = 'open' AND (? IS NULL OR session_id = ?) " +
                            "RETURNING flow", sessionId, sessionId).toArray().length;
  }

  // -- what a model call reads ------------------------------------------------------

  /** The active connections and their models, oldest first; re-read only after a change. */
  usable(): readonly Usable[] {
    if (this.#usable?.revision === this.#revision) {
      return this.#usable.usable;
    }
    const usable = Object.freeze(this.#sql.exec<Row>(
      "SELECT * FROM connections WHERE status = 'active' ORDER BY created_at, id").toArray()
      .map((row) => Object.freeze({
        connection: row.id, source: row.source, provider: row.provider,
        display_name: row.display_name, created_at: row.created_at,
        models: Object.freeze(JSON.parse(row.models) as ModelRecord[]),
      })));
    this.#usable = { revision: this.#revision, usable };
    return usable;
  }

  /**
   * The credential a call carries, read in the unit that reserves the call: the active
   * connection's blob at its current version. Refused for a connection gone, revoked or
   * failed, and for an OAuth token already expired.
   */
  credential(id: string, nowMs: number): Credential {
    const row = this.#row(id);
    if (row === undefined || row.status !== "active") {
      throw new Refusal("connection_unavailable", CONNECTION_UNAVAILABLE);
    }
    if (row.expires_at !== null && row.expires_at <= nowMs) {
      throw new Refusal("credential_expired", CREDENTIAL_EXPIRED);
    }
    const secret = this.#sql.exec<{ blob: string }>(
      "SELECT blob FROM connection_secrets WHERE connection_id = ? AND version = ?",
      id, row.version).toArray()[0];
    if (secret === undefined) {
      throw new Refusal("connection_unavailable", CONNECTION_UNAVAILABLE);
    }
    return { connection: id, version: row.version, blob: secret.blob };
  }

  /**
   * Where an active connection's credential may be forwarded (its sealed base URL and
   * endpoints, as its setup check listed them), or null: no such active connection.
   */
  egressScope(id: string): EgressScope | null {
    return this.egressOf(id)?.scope ?? null;
  }

  /**
   * What a sandbox call on an active connection is admitted and reserved by: its egress
   * scope, its source record (whose spend it counts under) and its models (whose prices
   * bound it); null for no such active connection, or one with no scope stored.
   */
  egressOf(id: string): { readonly scope: EgressScope; readonly source: string;
                          readonly provider: string;
                          readonly models: readonly ModelRecord[] } | null {
    const row = this.#row(id);
    if (row === undefined || row.status !== "active" || row.egress === null) {
      return null;
    }
    return { scope: egressScope(JSON.parse(row.egress)), source: row.source,
             provider: row.provider, models: JSON.parse(row.models) as ModelRecord[] };
  }

  /** The active connections the sandbox may reach, oldest first, each with its scope. */
  egressible(): readonly { readonly connection: string; readonly provider: string;
                          readonly egress: EgressScope }[] {
    return this.#sql.exec<Row>("SELECT * FROM connections WHERE status = 'active' AND " +
                                 "egress IS NOT NULL ORDER BY created_at, id").toArray()
      .map((row) => Object.freeze({ connection: row.id, provider: row.provider,
                                    egress: egressScope(JSON.parse(row.egress!)) }));
  }

  /** Whether the connection is still active at `version` (a dispatch's re-check). */
  current(id: string, version: number): boolean {
    const row = this.#row(id);
    return row !== undefined && row.status === "active" && row.version === version;
  }

  /**
   * Refresh the connection's OAuth credential first when it expires within the margin,
   * under the lease: one refresh at a time per connection, which concurrent calls wait on.
   * Resolves when the credential is fresh, or there is nothing to refresh; a failed
   * refresh rejects every call that waited on it.
   */
  async fresh(id: string): Promise<void> {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (!this.#refreshing.has(id) && !this.#expiring(id, Date.now())) {
        return;  // most calls: nothing to refresh, so no unit is taken for it
      }
      const running = this.#refreshing.get(id);
      if (running !== undefined) {
        await running;
        continue;
      }
      const holder = randomHex(12);
      const step = await this.#write(() => this.#lease(id, holder, Date.now()));
      if (step === null) {
        return;  // fresh, or not an OAuth credential, or not usable (the reserve refuses)
      }
      if (step === "busy") {
        continue;
      }
      try {
        await this.#refresh(id, holder, step.credential);
        step.settle(undefined);
      } catch (error) {
        step.settle(error);
        throw error;
      } finally {
        this.#refreshing.delete(id);
      }
      return;
    }
    throw new Refusal("credential_expired", CREDENTIAL_EXPIRED);
  }

  /** Whether an OAuth credential expires within the margin; the lease's unit reads again. */
  #expiring(id: string, nowMs: number): boolean {
    const row = this.#row(id);
    return row !== undefined && row.kind === "oauth2" && row.expires_at !== null
      && row.expires_at - nowMs <= this.#limits.refreshMarginMs;
  }

  /**
   * In a unit: nothing to do (null), a refresh another caller of this instance runs
   * ("busy"), or the lease taken for `holder`, the credential to refresh, and how to settle
   * the refresh every other caller now waits on: it is in `#refreshing` before this unit
   * ends, so no unit after it takes the lease again.
   */
  #lease(id: string, holder: string, nowMs: number):
      { credential: Credential; settle: (error: unknown) => void } | "busy" | null {
    const row = this.#row(id);
    if (row === undefined || row.status !== "active" || row.kind !== "oauth2"
        || row.expires_at === null || row.expires_at - nowMs > this.#limits.refreshMarginMs) {
      return null;
    }
    if (this.#refreshing.has(id)) {
      return "busy";
    }
    const secret = this.#sql.exec<{ blob: string }>(
      "SELECT blob FROM connection_secrets WHERE connection_id = ? AND version = ?",
      id, row.version).toArray()[0];
    if (secret === undefined) {
      return null;
    }
    this.#sql.exec("UPDATE connections SET lease_id = ?, lease_until = ? WHERE id = ?",
                   holder, nowMs + this.#limits.leaseMs, id);
    let settle!: (error: unknown) => void;
    const done = new Promise<void>((resolve, reject) => {
      settle = (error) => (error === undefined ? resolve() : reject(error));
    });
    done.catch(() => undefined);  // the holder's own call carries the failure
    this.#refreshing.set(id, done);
    return { credential: { connection: id, version: row.version, blob: secret.blob }, settle };
  }

  async #refresh(id: string, holder: string, from: Credential): Promise<void> {
    let refreshed: Refreshed;
    try {
      refreshed = await this.#service().refresh(this.#owner(), id, from.version, from.blob);
    } catch (error) {
      // The gateway's fixed code, as its refusal carries it; anything else is `internal`.
      const refused = refusalOf(error)?.code;
      const code = isGatewayCode(refused) ? refused : "internal";
      await this.#write(() => this.#unlease(id, holder, code));
      throw new Refusal("credential_refresh_failed", `credential_refresh_failed: ${code}`);
    }
    const kept = await this.#write(() => this.#refreshed(id, holder, from.version, refreshed,
                                                         this.#clock()));
    if (kept === "malformed") {
      throw new Refusal("credential_refresh_failed", "credential_refresh_failed: malformed");
    }
  }

  /**
   * In a unit: the lease dropped after a failed refresh. A refused grant (the provider no
   * longer honours the refresh token) fails the connection until the owner reconnects.
   */
  #unlease(id: string, holder: string, code: string): void {
    this.#sql.exec("UPDATE connections SET lease_id = NULL, lease_until = NULL, " +
                     "status = CASE WHEN ? AND status = 'active' THEN 'error' ELSE status END " +
                     "WHERE id = ? AND lease_id = ?",
                   code === "oauth_invalid_grant" || code === "not_refreshable" ? 1 : 0,
                   id, holder);
    this.#changed();
  }

  /**
   * In a unit: a refreshed blob stored by compare-and-swap, under the lease, at the next
   * version. A connection revoked meanwhile keeps the newest tokens, and its provider
   * revoke is owed for them, due now if the lane already sent the old ones' (the provider
   * may honour the rotated tokens either way). One changed in any other way drops them,
   * and the lease with them.
   */
  #refreshed(id: string, holder: string, version: number,
             refreshed: Refreshed, now: number):
      "kept" | "dropped" | "malformed" {
    const expires = refreshed.expires_at;
    const ok = object(refreshed) && refreshed.version === version + 1
      && typeof refreshed.kid === "string" && KID.test(refreshed.kid)
      && (expires === null || count(expires, Number.MAX_SAFE_INTEGER));
    let blob: string | undefined;
    try {
      blob = ok ? blobOf(refreshed.blob, refreshed.kid) : undefined;
    } catch {
      blob = undefined;
    }
    if (blob === undefined) {
      this.#unlease(id, holder, "malformed");
      return "malformed";
    }
    const row = this.#row(id);
    const revoked = row?.status === "revoked";
    if (row === undefined || row.version !== version
        || (!revoked && (row.status !== "active" || row.lease_id !== holder))) {
      this.#sql.exec("UPDATE connections SET lease_id = NULL, lease_until = NULL " +
                       "WHERE id = ? AND lease_id = ?", id, holder);
      return "dropped";
    }
    this.#storage.transactionSync(() => {
      this.#swap(id, version, refreshed.kid, blob);
      this.#sql.exec("UPDATE connections SET expires_at = ?, lease_id = NULL, " +
                       "lease_until = NULL WHERE id = ?", expires, id);
      if (revoked) {
        this.#sql.exec("UPDATE connections SET revoke_tries = CASE WHEN revoke_due IS NULL " +
                         "THEN 0 ELSE revoke_tries END, revoke_due = coalesce(revoke_due, ?) " +
                         "WHERE id = ?", now, id);
      }
    });
    return "kept";
  }

  /** The connection's blob replaced at `version + 1`, and its version moved with it. */
  #swap(id: string, version: number, kid: string, blob: string): void {
    this.#sql.exec("DELETE FROM connection_secrets WHERE connection_id = ?", id);
    this.#sql.exec("INSERT INTO connection_secrets (connection_id, version, kid, blob) " +
                     "VALUES (?, ?, ?, ?)", id, version + 1, kid, blob);
    this.#sql.exec("UPDATE connections SET version = ? WHERE id = ?", version + 1, id);
    this.#changed();
  }

  /**
   * In a unit: a blob the gateway resealed under its active kid, stored by compare-and-
   * swap on (connection, version), never while a refresh holds the lease. A reseal that
   * lost the race is dropped; the gateway reseals again at the next call.
   */
  resealed(id: string, version: number, resealed: unknown, nowMs: number): boolean {
    if (!object(resealed) || Object.keys(resealed).sort().join() !== "blob,kid,version"
        || typeof resealed["kid"] !== "string" || !KID.test(resealed["kid"])
        || resealed["version"] !== version + 1) {
      return false;
    }
    const row = this.#row(id);
    if (row === undefined || row.status !== "active" || row.version !== version
        || this.#refreshing.has(id) || (row.lease_until !== null && row.lease_until > nowMs)) {
      return false;
    }
    const blob = blobOf(resealed["blob"], resealed["kid"]);
    this.#storage.transactionSync(() => this.#swap(id, version, resealed["kid"] as string, blob));
    return true;
  }

  // -- the `connections` lane ------------------------------------------------------

  /** When the lane next has work: a flow or pending row to sweep, or a revoke due. */
  next(now: number): number | null {
    const next = this.#sql.exec<{ at: number | null }>(
      "SELECT MIN(at) AS at FROM (SELECT MIN(expires_at) AS at FROM connection_flows " +
        "UNION ALL SELECT MIN(pending_until) FROM connections WHERE pending_until IS NOT NULL " +
        "UNION ALL SELECT MIN(revoke_due) FROM connections WHERE revoke_due IS NOT NULL)")
      .one().at;
    return next === null ? null : Math.max(next, now);
  }

  /**
   * One pass: sweep expired flows and unclaimed pending connections in a unit, then ask
   * the provider to revoke each due OAuth credential, each call bounded by `revokeMs` and
   * its outcome kept in its row: revoked, the blob deleted; not revocable (the source has
   * no revocation endpoint), the blob deleted, with a notice; failed, backed off, and after
   * `revokeTries` the blob deleted anyway, with a notice.
   */
  async run(_now: number, until: number): Promise<void> {
    await this.#write(() => this.#sweep(this.#clock()));
    for (const id of this.#dueRevokes(this.#clock())) {
      if (Date.now() >= until) {
        break;
      }
      const owed = await this.#write(() => this.#owed(id, this.#clock()));
      if (owed === null) {
        continue;
      }
      let outcome: RevokeOutcome = "failed";
      try {
        const revoking = this.#service().revokeAtProvider(this.#owner(), id, owed.version,
                                                          owed.blob);
        const answer = await inTime(revoking, this.#limits.revokeMs);
        if (answer === "late") {
          revoking.catch(() => undefined);
        } else {
          outcome = answer.revoked === true ? "revoked" : "not_revocable";
        }
      } catch {
        console.error("a connection's provider revoke failed; it is tried again later");
      }
      await this.#write(() => this.#revokeOutcome(id, owed.version, outcome, this.#clock()));
    }
  }

  /** In a unit: flows past their expiry, and pending connections never committed. */
  #sweep(now: number): void {
    this.#sql.exec("DELETE FROM connection_flows WHERE expires_at <= ?", now);
    const stale = this.#sql.exec<{ id: string }>(
      "DELETE FROM connections WHERE pending_until IS NOT NULL AND pending_until <= ? " +
        "RETURNING id", now).toArray();
    for (const { id } of stale) {
      this.#sql.exec("DELETE FROM connection_secrets WHERE connection_id = ?", id);
      this.#sql.exec("DELETE FROM connection_flows WHERE connection_id = ?", id);
    }
    if (stale.length > 0) {
      this.#changed();
    }
  }

  #dueRevokes(now: number): string[] {
    return this.#sql.exec<{ id: string }>(
      "SELECT id FROM connections WHERE revoke_due IS NOT NULL AND revoke_due <= ? " +
        "ORDER BY revoke_due LIMIT ?", now, this.#limits.revokesPerPass).toArray()
      .map((row) => row.id);
  }

  /** In a unit: the blob a due provider revoke sends, re-read; null when none is owed. */
  #owed(id: string, now: number): Credential | null {
    const row = this.#row(id);
    if (row === undefined || row.revoke_due === null || row.revoke_due > now) {
      return null;
    }
    const secret = this.#sql.exec<{ blob: string; version: number }>(
      "SELECT blob, version FROM connection_secrets WHERE connection_id = ?", id).toArray()[0];
    if (secret === undefined) {
      this.#sql.exec("UPDATE connections SET revoke_due = NULL WHERE id = ?", id);
      return null;
    }
    return { connection: id, version: secret.version, blob: secret.blob };
  }

  /** In a unit: a provider revoke's outcome, kept in the connection's row. */
  #revokeOutcome(id: string, version: number, outcome: RevokeOutcome, now: number): void {
    const row = this.#row(id);
    if (row === undefined || row.revoke_due === null) {
      return;
    }
    const tries = row.revoke_tries + 1;
    if (outcome === "failed" && tries < this.#limits.revokeTries) {
      this.#sql.exec("UPDATE connections SET revoke_tries = ?, revoke_due = ? WHERE id = ?",
                     tries, now + this.#limits.revokeRetryS * tries, id);
      return;
    }
    this.#storage.transactionSync(() => {
      this.#sql.exec("DELETE FROM connection_secrets WHERE connection_id = ? AND version <= ?",
                     id, version);
      const left = this.#sql.exec("SELECT 1 FROM connection_secrets WHERE connection_id = ?", id)
        .toArray().length > 0;
      // A refresh that landed after the revoke stored newer tokens: revoke those too.
      this.#sql.exec("UPDATE connections SET revoke_tries = ?, revoke_due = ? WHERE id = ?",
                     left ? 0 : tries, left ? now : null, id);
      if (outcome === "failed") {
        this.#notices.add("connection_revoke_failed",
                          `${id}: the provider did not confirm the revoke; the credential ` +
                            "was deleted here, and the provider may still honour it", now);
      } else if (outcome === "not_revocable") {
        this.#notices.add("connection_revoke_unsupported",
                          `${id}: the provider has no revocation endpoint; the credential ` +
                            "was deleted here, and the provider honours its token until it " +
                            "expires", now);
      }
      this.#pruneRevoked();
    });
  }
}

/**
 * A connection to prepare: its source record's id, kind, display name and pi-ai provider,
 * whether its provider can be asked to revoke what it issues, and, for OAuth, the redirect
 * URI its flow (begun in the same unit) returns to.
 */
export interface ConnectionPrepareRequest {
  readonly source: string;
  readonly kind: "api_key" | "oauth2";
  readonly display_name: string;
  readonly provider: string;
  readonly revocable: boolean;
  readonly redirect_uri?: string;
}
/** A prepared connection's sealed blob, its kid, its models and, for OAuth, its expiry. */
export interface ConnectionCommitRequest {
  readonly connection: string;
  readonly version: number;
  readonly blob: string;
  readonly kid: string;
  readonly models: unknown[];
  readonly expires_at?: number | null;
  /** The setup check's code, when it failed after the provider issued the credential. */
  readonly failed?: string;
  /**
   * Where the credential may be forwarded (the setup check's `egress`: its sealed base URL
   * and endpoints); every commit but a failed one carries it.
   */
  readonly egress?: unknown;
}
export interface ConnectionRequest {
  readonly connection: string;
}
export interface FlowFinishRequest {
  readonly state: string;
}

// -- THE DOORS: the `connections` rows' handlers (`contract/doors.json`) --------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `connections` doors' handlers, by door. */
export const connectionsDoors = Object.freeze({
  connectionPrepare(o: OwnerState, { session, request }: Call<ConnectionPrepareRequest>): Prepared {
    return o.connections.prepare(session, request, clock());
  },
  connectionCommit(o: OwnerState, { request }: Call<ConnectionCommitRequest>): ConnectionView {
    return o.connections.commit(request, clock());
  },
  async connectionRevoke(o: OwnerState, door: Door<ConnectionRequest>):
      Promise<{ connection: string; revoked: boolean; provider_revoke: boolean }> {
    const revoked = await o.synced(() => door.within(() =>
      o.connections.revoke(door.request, clock())));
    await o.door(() => o.egress.end("connection",
                                            (live) => live.connection === revoked.connection));
    return revoked;
  },
  connectionList(o: OwnerState, _call: Call<undefined>): { connections: ConnectionView[] } {
    return ({ connections: o.connections.list() });
  },
  flowFinish(o: OwnerState, { session, request }: Call<FlowFinishRequest>):
      { connection: string; version: number; verifier: string; redirect_uri: string;
                source: string } {
    return o.connections.flowFinish(session, request, clock());
  },
});
