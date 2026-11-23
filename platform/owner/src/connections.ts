/**
 * The owner's connections (gateway research §1.4, §2.6, §7), each a model source's or a
 * service's key (a connector, `SERVICE_PROVIDER`: no models, never metered, its scope one URL
 * of an outside service, bound to a run by `network.ts` `connectorsOf`): what each one is,
 * the blob the gateway sealed its credential into, the OAuth flows that connect one, the
 * refresh lease, what each one's key can pay for, the models it reaches now (the gateway's
 * catalog, as its vendor serves them and how strong it lists them), and the `connections`
 * lane's housekeeping.
 *
 * **No plaintext enters the object.** The binder hands a pasted key to `GATEWAY.seal`
 * and an OAuth code to `GATEWAY.exchange`; the object stores only what comes back, the
 * sealed blob, which only the gateway can open, and only for this owner, connection and
 * version (the seal's additional data).
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
 *    its whole pi-ai capability record, which the object has no catalog for: `ModelRecord`;
 *    its models until the lane's first read of them),
 *    where sandbox egress may forward its credential (`egress`: the sealed base URLs and
 *    endpoints the check read from the blob, no secret; the egress door's admission reads
 *    it) and, for OAuth, the access expiry in clear.
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
 * and one the provider issued gets an owner notice to delete it there. A refresh that lands after
 * the revoke keeps its rotated tokens for the lane to revoke too, whether or not the lane already
 * revoked the old ones. A call that already holds the blob completes; the bound is one call.
 * Account revocation (`sessionCloseAll`) and a session's sign-out cancel the session's open flows
 * in their own unit, so a late callback finds no flow.
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
 * **What a key can pay for.** A source whose record says how its key reports it (`credit`)
 * has each active connection's answer kept (`connection_credit`): whether the key pays for a
 * priced model, null until known. The lane reads it once a connection is active, a connection
 * made before this was kept among them, so none needs a reconnect: the gateway's `credit`, at
 * the record's URL, where the sealed endpoints allow it. A model call its provider refused
 * with one of the record's `refused` statuses (`refused`, at its settle) says the key pays for
 * nothing too, which is how a connection sealed before its record named the read learns it.
 * A key that pays for nothing is read again every `creditRecheckS`, so credit bought shows
 * within that (on a blob with no read, a refused call's mark lapses then, learned again at
 * the next refusal); one that pays is not read again until a refusal says otherwise. `auto`
 * reads it (`usable`; `sources/src/ranking.ts` `pickable`), and so does the owner's view.
 *
 * **The models it reaches now.** The catalog is the gateway's, so each active model
 * connection's models are read through it (`connection_models`): the gateway's `models`, the
 * records its blob reaches by the catalog now (only on the APIs the blob was sealed for: a new
 * API is a reconnect, the owner's consent), each kept with the score its vendor lists it at, or
 * none; where its source record says where its vendor lists the models it serves a key
 * (`listing`), only the ones the gateway's `listing` lists, where the sealed endpoints allow
 * that read. The lane reads them as it reads credit (`LaneRead`): once a connection is active,
 * the ones from before among them, so a catalog bump reaches every connection with no
 * reconnect; again every `modelsRecheckS`, and `modelsRetryS` after a failed read. Once read,
 * they are a connection's models (`modelsOf`), for every reader: what `auto` ranks and scores
 * (`usable`; `auto.ts` `ranked`), what a run's named model must be, what the egress door
 * reserves by (`egressOf`) and what the owner sees (`list`); until then, the ones its setup
 * check listed. A model its vendor stops serving goes at the next read, and one it serves again
 * comes back.
 *
 * Every write is a unit of the object's gate, and each unit reads what it acts on: a door's
 * body, or a step between the lane's or a call's outside calls. Bounds: `maxConnections`
 * live (pending, active or failed) connections; the newest `maxRevokedKept` revoked ones
 * that hold no blob; `maxFlowRows` flow rows, swept at their expiry.
 */
import { keyed, object, whole } from "../../contract/src/checks";
import { CONNECTION_ID, randomHex } from "../../contract/src/ids";
import { sha256, sha256Hex } from "../../contract/src/digest";
import { randomBytes } from "node:crypto";


import {
  type CreditRead, egressScope, type EgressScope, type ListingRead, SERVICE_PROVIDER,
} from "../../contract/src/egress-scope";
import { base64url, KID, parseBlob } from "../../contract/src/envelope";
import { fields } from "../../contract/src/doors";
import { type ShapeName, taken } from "../../contract/src/handed";
import { checkedRecord, MODEL_ID, type ModelRecord } from "../../contract/src/model-record";
import { bundledSources } from "../../sources/src/catalog";
import type { SourceRecord } from "../../sources/src/schema";

import type {
  GatewayService, KeyCredit, ModelListing, Refreshed, SealedModels,
} from "../../contract/src/gateway";
import type {
  ConnectionCommitRequest, ConnectionPrepareRequest, ConnectionRequest, FlowFinishRequest,
  ResealAnswer, Swept,
} from "../../contract/src/owner";
import { resealAnswer } from "../../contract/src/owner";
import { isGatewayCode, orRefused, Refusal, refusalOf } from "../../contract/src/refusal";
import {
  CONNECTION_BLOB_MAX, EGRESS_JSON_MAX, MODELS_JSON_MAX, REDIRECT_URI_MAX,
} from "../../contract/src/limits";
import type { OwnerStorage, Write } from "./gate";
import type { Notices } from "./notices";
import { LANE_CALL_MS } from "./schedule";
import { printable, SOURCE_ID, utf8Bytes } from "../../contract/src/text";
import type { Call, Door, DoorUnits } from "./door";
import type { Egress } from "./egress";
import type { Doorbell } from "./wakes";
import { clock, clockMs, inTime } from "./clock";
import type { Usable } from "./auto";

interface ConnectionLimits {
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
  /**
   * How long a key that pays for nothing, or whose read failed, waits before it is read
   * again: credit bought is seen within it, at one read an hour while it pays for nothing.
   */
  readonly creditRecheckS: number;
  /**
   * How long a connection's models are kept before they are read again: a vendor drops a free
   * model when its promotion ends and scores one when it is benchmarked, and the gateway's
   * catalog moves at a pin bump, about monthly, so a day sees each soon enough.
   */
  readonly modelsRecheckS: number;
  /** How long a failed read of a connection's models waits before it is read again. */
  readonly modelsRetryS: number;
  /** How long the lane waits on one read of a source (`LaneRead`): the rest of its hung timeout. */
  readonly readMs: number;
  /** Reads of each kind one lane pass takes. */
  readonly readsPerPass: number;
}

/**
 * Flows last 10 minutes and at most 10 are open; a refresh
 * starts 3 minutes before the access token expires (research §2.6); a lease outlasts the
 * gateway's 15 s token-endpoint bound. A connection keeps up to 1,024 models: twice pi-ai's
 * largest built-in catalog at least (the connections test finds it by a scan, and holds
 * `MODELS_JSON_MAX` to 1,024 of the largest built-in record).
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
  revokeMs: LANE_CALL_MS,
  revokesPerPass: 8,
  creditRecheckS: 3600,
  modelsRecheckS: 86_400,
  modelsRetryS: 3600,
  readMs: LANE_CALL_MS,
  readsPerPass: 8,
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
  /** Its models (`modelsOf`): the gateway's catalog now, as its vendor serves them, once read. */
  readonly models: string[];
  readonly expires_at: number | null;
  readonly created_at: number;
  readonly revoked_at: number | null;
  /** Whether its provider can be asked to revoke its credential (RFC 7009). */
  readonly provider_revocable: boolean;
  readonly provider_revoke_pending: boolean;
  /**
   * Whether its key can pay for a priced model, as its source reports it or a refused call
   * said: false, `auto` picks on it only the models its source serves such a key; null, not
   * known (or its source reports nothing).
   */
  readonly pays: boolean | null;
}

/** A prepared connection; for OAuth, the flow begun with it. */
export interface Prepared {
  readonly connection: string;
  readonly version: number;
  readonly expires_at: number;
  readonly flow?: { readonly state: string; readonly code_challenge: string;
                    readonly code_challenge_method: "S256"; readonly expires_at: number };
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
  egress: string | null; pays: number | null; read: string | null;
};

/**
 * Every connection's row, with what its key pays for (`connection_credit`) and its models as
 * last read (`connection_models`).
 */
const ROWS = "SELECT c.*, k.pays AS pays, m.models AS read FROM connections c " +
  "LEFT JOIN connection_credit k ON k.connection = c.id " +
  "LEFT JOIN connection_models m ON m.connection = c.id";

/** The tables of the lane's reads of a connection's source: a row each active connection. */
type ReadTable = "connection_credit" | "connection_models";
const READ_TABLES: readonly ReadTable[] = Object.freeze(["connection_credit",
                                                         "connection_models"]);

/**
 * One read the `connections` lane makes of each active connection's source, where its record
 * names one: its table (its `due` the next read), the record's read, the gateway's call, the
 * answer checked (null for one of another shape), what an outcome keeps (in a unit, for the
 * connection at the version read still active), and how long a read waits when the credential
 * cannot be sent yet.
 */
interface LaneRead<R, A> {
  readonly name: string;
  readonly table: ReadTable;
  readonly retryS: number;
  of(record: SourceRecord | undefined): R | undefined;
  call(credential: Credential, read: R): Promise<unknown>;
  answer(value: unknown): A | null;
  kept(id: string, outcome: A | "failed", now: number): void;
}

/**
 * A read's answer, or null where the blob reaches no such read (`endpoint_refused`): one
 * sealed before its record named the read, which has nothing to read on it.
 */
function reached<T>(reading: Promise<T>): Promise<T | null> {
  return reading.catch((error: unknown) => {
    if (refusalOf(error)?.code === "endpoint_refused") {
      return null;
    }
    throw error;
  });
}

/** How a provider revoke ended: confirmed, impossible (no endpoint), or not confirmed. */
type RevokeOutcome = "revoked" | "not_revocable" | "failed";

const CONNECTION_UNAVAILABLE = "connection_unavailable";
const CREDENTIAL_EXPIRED = "credential_expired";

const PROVIDER = /^[a-z0-9][a-z0-9-]{0,63}$/;
const STATE = /^[A-Za-z0-9_-]{43}$/;

const INVALID_REQUEST = "invalid connection request";

/** A connection door's request held to its shape (`handed.json`), or `INVALID_REQUEST`. */
function requested(request: unknown, name: ShapeName): Record<string, unknown> {
  return fields(request, ...taken(name), INVALID_REQUEST);
}

function grammar(value: unknown, pattern: RegExp, what: string): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new Refusal("invalid", `invalid ${what}`);
  }
  return value;
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

/**
 * A connection's models as the gateway lists them, at its setup check or at a read of them
 * (`models`): at most `maxModels`, each whole (`modelInfo`), none twice, within
 * `MODELS_JSON_MAX`.
 */
function checkedModels(models: unknown): ModelRecord[] {
  if (!Array.isArray(models) || models.length > CONNECTION_LIMITS.maxModels) {
    throw new Refusal("invalid", "invalid connection models");
  }
  const checked = models.map(modelInfo);
  if (new Set(checked.map((model) => model.id)).size !== checked.length
      || utf8Bytes(JSON.stringify(checked)) > MODELS_JSON_MAX) {
    throw new Refusal("invalid", "invalid connection models");
  }
  return checked;
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
    models: modelsOf(row).map((model) => model.id),
    expires_at: row.expires_at, created_at: row.created_at, revoked_at: row.revoked_at,
    provider_revocable: row.revocable === 1, provider_revoke_pending: row.revoke_due !== null,
    pays: paysOf(row),
  };
}

function paysOf(row: Row): boolean | null {
  return row.pays === null ? null : row.pays === 1;
}

/** A connection's models as `connection_models` keeps them: each record, with its score. */
type Served = readonly (readonly [ModelRecord, number | null])[];

/** A row's models as last read, each with its score or null; null when none were read. */
function readOf(row: Row): Served | null {
  return row.read === null ? null : JSON.parse(row.read) as Served;
}

/** A row's models: the ones last read (`connection_models`), its setup check's until then. */
function modelsOf(row: Row): ModelRecord[] {
  const read = readOf(row);
  return read === null ? JSON.parse(row.models) as ModelRecord[]
    : read.map(([model]) => model);
}

/** A row's models' scores, by model id; empty when none were read or scored. */
function scoresOf(row: Row): ReadonlyMap<string, number> {
  return new Map((readOf(row) ?? []).flatMap(([{ id }, score]) =>
    score === null ? [] : [[id, score] as const]));
}

/** What one read of a connection's models found: its blob's records, and its vendor's listing. */
interface ModelsRead {
  readonly sealed: SealedModels;
  /** Null where its record names no listing, or its blob reaches no such read. */
  readonly listing: ModelListing | null;
}

/**
 * The records of `read` its listing lists (every one where it has none), each with the score
 * it lists it at, in the records' order, as `connection_models` keeps them; `unmatched` for a
 * listing that lists none of them, `failed` for no record or more than `MODELS_JSON_MAX` kept.
 */
function servedJson({ sealed, listing }: ModelsRead): string | "unmatched" | "failed" {
  const listed = new Map<string, number | null>();
  for (const { id, score } of listing?.models ?? []) {
    if (!listed.has(id)) {
      listed.set(id, score);
    }
  }
  const kept: Served = sealed.models.flatMap((model): Served =>
    listing === null ? [[model, null]]
      : listed.has(model.id) ? [[model, listed.get(model.id)!]] : []);
  const json = JSON.stringify(kept);
  if (kept.length > 0 && utf8Bytes(json) <= MODELS_JSON_MAX) {
    return json;
  }
  const unmatched = kept.length === 0 && sealed.models.length > 0;
  console.error(unmatched ? "a connection's listing read listed none of its models; it is " +
                  "read again later"
                : `a connection's models read kept ${kept.length} models in ` +
                  `${utf8Bytes(json)} bytes; it is read again later`);
  return unmatched ? "unmatched" : "failed";
}

/** The gateway's `models` answer, its models held to the commit's rule (`checkedModels`). */
function sealedAnswer(value: unknown): SealedModels | null {
  try {
    return { models: checkedModels(object(value) ? value["models"] : undefined) };
  } catch {
    return null;
  }
}

/**
 * The gateway's listing answer, checked: each model a model id and its score a finite number
 * or null (`listed_model`).
 */
function listingAnswer(value: unknown): ModelListing | null {
  const models = object(value) ? value["models"] : undefined;
  return Array.isArray(models) && models.every((one) =>
    keyed(one, ...taken("listed_model")) && typeof one["id"] === "string"
      && MODEL_ID.test(one["id"])
      && (one["score"] === null
        || (typeof one["score"] === "number" && Number.isFinite(one["score"]))))
    ? { models: models as ModelListing["models"] } : null;
}

export class Connections {
  readonly #storage: OwnerStorage;
  readonly #write: Write;
  readonly #gateway: GatewayService;
  readonly #owner: () => string;
  readonly #notices: Notices;
  /** The refreshes this instance runs, by connection: what concurrent callers wait on. */
  readonly #refreshing = new Map<string, Promise<void>>();
  /** Bumped by every unit that changes a connection, so `usable` re-reads only then. */
  #revision = 0;
  #usable: { revision: number; usable: readonly Usable[] } | null = null;
  /** What each key can pay for (`credit`). */
  readonly #credit: LaneRead<CreditRead, KeyCredit | "none"> = {
    name: "credit", table: "connection_credit", retryS: CONNECTION_LIMITS.creditRecheckS,
    of: (record) => record?.credit === undefined ? undefined
      : { url: record.credit.url, none_when: record.credit.none_when },
    call: (credential, read) => reached(this.#gateway.credit(
      this.#owner(), credential.connection, credential.version, credential.blob, read)),
    answer: (value) => value === null ? "none"
      : object(value) && typeof value["pays"] === "boolean" ? { pays: value["pays"] } : null,
    kept: (id, outcome, now) => this.#credited(id, outcome, now),
  };
  /**
   * The models its blob reaches by the gateway's catalog now (`models`), and of them the ones
   * its vendor serves it, with their scores (`listing`), for a gateway source's connection.
   */
  readonly #models: LaneRead<{ readonly listing: ListingRead | null }, ModelsRead> = {
    name: "models", table: "connection_models", retryS: CONNECTION_LIMITS.modelsRetryS,
    of: (record) => {
      if (record?.upstream === undefined) {
        return undefined;
      }
      if (record.listing === undefined) {
        return { listing: null };
      }
      const { url, rows, id, score } = record.listing;
      return { listing: { url, rows, id, ...score === undefined ? {} : { score } } };
    },
    call: async (credential, read) => {
      const { connection, version, blob } = credential;
      const sealed = await this.#gateway.models(this.#owner(), connection, version, blob);
      // With no listing to read on its blob, its models are every one it reaches.
      const listing = read.listing === null ? null : await reached(this.#gateway.listing(
        this.#owner(), connection, version, blob, read.listing));
      return { sealed, listing };
    },
    answer: (value) => {
      const { sealed, listing } = value as { sealed: unknown; listing: unknown };
      const models = sealedAnswer(sealed);
      const listed = listing === null ? null : listingAnswer(listing);
      return models === null || (listing !== null && listed === null) ? null
        : { sealed: models, listing: listed };
    },
    kept: (id, outcome, now) => this.#served(id, outcome, now),
  };

  constructor(storage: OwnerStorage, write: Write, gateway: GatewayService,
              owner: () => string, notices: Notices) {
    this.#storage = storage;
    this.#write = write;
    this.#gateway = gateway;
    this.#owner = owner;
    this.#notices = notices;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #row(id: string): Row | undefined {
    return this.#sql.exec<Row>(`${ROWS} WHERE c.id = ?`, id).toArray()[0];
  }

  #changed(): void {
    this.#revision++;
  }

  // -- the doors' bodies, each run inside the door's unit ---------------------------

  /**
   * A pending connection, for the binder to seal a credential for at version 1; for OAuth,
   * its flow begun in the same unit, bound to `sessionId`. Every check runs before any
   * write, so a refusal leaves nothing behind.
   */
  prepare(sessionId: string, request: unknown, now: number): Prepared {
    const r = requested(request, "connection_prepare");
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
    const name = printable(r["display_name"], 128, "display_name");
    const live = this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM connections WHERE status != 'revoked'").one().n;
    if (live >= CONNECTION_LIMITS.maxConnections) {
      throw new Refusal("refused", "the owner's connections are at their limit");
    }
    if (redirect !== null) {
      const flows = this.#sql.exec<{ open: number; rows: number }>(
        "SELECT COALESCE(SUM(status = 'open' AND expires_at > ?), 0) AS open, " +
          "COUNT(*) AS rows FROM connection_flows", now).one();
      if (flows.open >= CONNECTION_LIMITS.maxOpenFlows
          || flows.rows >= CONNECTION_LIMITS.maxFlowRows) {
        throw new Refusal("refused", "too many connection flows; finish or wait for one");
      }
    }
    const id = `c${randomHex(12)}`;
    const until = now + CONNECTION_LIMITS.pendingTtlS;
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
    const r = requested(request, "connection_commit");
    const id = grammar(r["connection"], CONNECTION_ID, "connection");
    const kid = grammar(r["kid"], KID, "kid");
    const blob = blobOf(r["blob"], kid);
    const checked = checkedModels(r["models"]);
    const row = this.#row(id);
    if (row === undefined || row.status !== "pending" || row.version !== r["version"]
        || row.pending_until === null || row.pending_until <= now) {
      throw new Refusal("refused", "no pending connection at this version");
    }
    const expires = r["expires_at"] ?? null;
    if (row.kind === "api_key" ? expires !== null
      : expires !== null && !whole(expires, 0)) {
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
    // A service's key is committed as one, and only it: its scope and its provider say so.
    if ((row.provider === SERVICE_PROVIDER) !== ("kind" in JSON.parse(egress))) {
      throw new Refusal("invalid", "a service's key has a service's scope, and only it");
    }
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
   * In a unit: every pending or active connection marked failed, for its owner to connect
   * again: a restore's fence (`restore.ts`), whose sealed credentials were never in the backup.
   */
  secretsLost(): void {
    this.#sql.exec("UPDATE connections SET status = 'error', pending_until = NULL WHERE " +
                     "status IN ('pending', 'active')");
    for (const table of READ_TABLES) {
      this.#sql.exec(`DELETE FROM ${table}`);
    }
  }

  /**
   * The connection, unusable from this unit on: a revocable credential's blob kept only
   * for the lane's provider revoke, any other deleted now (an issued one with an owner
   * notice to delete it at the provider). Revoking twice changes nothing.
   */
  revoke(request: unknown, now: number): { connection: string; revoked: boolean;
                                           provider_revoke: boolean } {
    const r = requested(request, "connection_revoke");
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
      for (const table of READ_TABLES) {
        this.#sql.exec(`DELETE FROM ${table} WHERE connection = ?`, id);
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
                     "ORDER BY revoked_at DESC LIMIT -1 OFFSET ?)",
                   CONNECTION_LIMITS.maxRevokedKept);
  }

  /** Every connection, newest first, without its blob. */
  list(): ConnectionView[] {
    return this.#sql.exec<Row>(`${ROWS} ORDER BY c.created_at DESC, c.id`).toArray().map(view);
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
    const challenge = sha256(verifier).toString("base64url");
    const until = now + CONNECTION_LIMITS.flowTtlS;
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
    const r = requested(request, "flow_finish");
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
      `${ROWS} WHERE c.status = 'active' ORDER BY c.created_at, c.id`).toArray()
      .map((row) => Object.freeze({
        connection: row.id, source: row.source, provider: row.provider,
        display_name: row.display_name, created_at: row.created_at,
        models: Object.freeze(modelsOf(row)),
        pays: paysOf(row), scores: scoresOf(row),
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
   * Where an active connection's credential may be forwarded (its sealed base URLs and
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
             provider: row.provider, models: modelsOf(row) };
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
      if (!this.#refreshing.has(id) && !this.#expiring(id, clockMs())) {
        return;  // most calls: nothing to refresh, so no unit is taken for it
      }
      const running = this.#refreshing.get(id);
      if (running !== undefined) {
        await running;
        continue;
      }
      const holder = randomHex(12);
      const step = await this.#write(() => this.#lease(id, holder, clockMs()));
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
      && row.expires_at - nowMs <= CONNECTION_LIMITS.refreshMarginMs;
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
        || row.expires_at === null || row.expires_at - nowMs > CONNECTION_LIMITS.refreshMarginMs) {
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
                   holder, nowMs + CONNECTION_LIMITS.leaseMs, id);
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
      refreshed = await this.#gateway.refresh(this.#owner(), id, from.version, from.blob);
    } catch (error) {
      // The gateway's fixed code, as its refusal carries it; anything else is `internal`.
      const refused = refusalOf(error)?.code;
      const code = isGatewayCode(refused) ? refused : "internal";
      await this.#write(() => this.#unlease(id, holder, code));
      throw new Refusal("credential_refresh_failed", `credential_refresh_failed: ${code}`);
    }
    const kept = await this.#write(() => this.#refreshed(id, holder, from.version, refreshed,
                                                         clock()));
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
      && (expires === null || whole(expires, 0));
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
    return this.#keepResealed(id, version, resealed, nowMs,
                              (row) => row.status === "active");
  }

  /**
   * In a unit: `resealed` stored by the reseal's compare-and-swap, when it is the gateway's
   * reseal of `version` and the row, at `version`, is not being refreshed and `holds`.
   */
  #keepResealed(id: string, version: number, resealed: unknown, nowMs: number,
                holds: (row: Row) => boolean): boolean {
    if (!keyed(resealed, ...taken("resealed"))
        || typeof resealed["kid"] !== "string" || !KID.test(resealed["kid"])
        || resealed["version"] !== version + 1) {
      return false;
    }
    const row = this.#row(id);
    if (row === undefined || !holds(row) || row.version !== version
        || this.#refreshing.has(id) || (row.lease_until !== null && row.lease_until > nowMs)) {
      return false;
    }
    const blob = blobOf(resealed["blob"], resealed["kid"]);
    this.#storage.transactionSync(() => this.#swap(id, version, resealed["kid"] as string, blob));
    return true;
  }

  /**
   * The connections' half of the platform's sweep before a gateway kid leaves the ring
   * (`gatewayReseal`): every blob the object holds, an idle connection's and one kept for
   * its provider revoke included, resealed by `GATEWAY.reseal` and stored at the next
   * version by the reseal's compare-and-swap, each in a unit of its own. A blob the gateway
   * found under its active kid already is left as it is; one that lost the swap to a refresh
   * or a revoke meanwhile is under no kid the sweep knows.
   */
  async reseal(): Promise<Swept[]> {
    const held = this.#sql.exec<{ connection_id: string; version: number; kid: string;
                                  blob: string }>(
      "SELECT connection_id, version, kid, blob FROM connection_secrets " +
        "ORDER BY connection_id").toArray();
    const swept: Swept[] = [];
    for (const { connection_id: id, version, kid, blob } of held) {
      const answer = await orRefused(this.#gateway.reseal(this.#owner(), id, version, blob),
                                     null);
      if (answer === null) {
        swept.push({ active: null, under: null, resealed: false });
        continue;
      }
      const kept = answer.kid !== kid && await this.#write(() => this.#keepResealed(
        id, version, answer, clockMs(), (row) => this.#sql.exec(
          "SELECT 1 FROM connection_secrets WHERE connection_id = ? AND version = ? " +
            "AND blob = ?", row.id, version, blob).toArray().length > 0));
      swept.push({ active: answer.kid, under: answer.kid === kid || kept ? answer.kid : null,
                   resealed: kept });
    }
    return swept;
  }

  // -- the `connections` lane ------------------------------------------------------

  /**
   * When the lane next has work: a flow or pending row to sweep, a revoke due, an active
   * connection a read of its source (`LaneRead`) never considered, or such a read due.
   */
  next(now: number): number | null {
    const at = (query: string) => this.#sql.exec<{ at: number | null }>(query).one().at;
    const ats = [at("SELECT MIN(at) AS at FROM (SELECT MIN(expires_at) AS at FROM " +
                      "connection_flows UNION ALL SELECT MIN(pending_until) FROM connections " +
                      "WHERE pending_until IS NOT NULL UNION ALL SELECT MIN(revoke_due) FROM " +
                      "connections WHERE revoke_due IS NOT NULL)"),
                 ...READ_TABLES.map((table) => at(
                   "SELECT MIN(at) AS at FROM (SELECT MIN(c.created_at) AS at FROM connections " +
                     `c WHERE c.status = 'active' AND NOT EXISTS (SELECT 1 FROM ${table} k ` +
                     `WHERE k.connection = c.id) UNION ALL SELECT MIN(due) FROM ${table} ` +
                     "WHERE due IS NOT NULL)"))]
      .filter((one) => one !== null);
    return ats.length === 0 ? null : Math.max(Math.min(...ats), now);
  }

  /**
   * One pass: sweep expired flows and unclaimed pending connections in a unit, then ask
   * the provider to revoke each due OAuth credential, each call bounded by `revokeMs` and
   * its outcome kept in its row: revoked, the blob deleted; not revocable (the source has
   * no revocation endpoint), the blob deleted, with a notice; failed, backed off, and after
   * `revokeTries` the blob deleted anyway, with a notice. Then each due read of a source,
   * credit and then models (`#reads`).
   */
  async run(_now: number, until: number): Promise<void> {
    await this.#write(() => this.#sweep(clock()));
    for (const id of this.#dueRevokes(clock())) {
      if (clock() >= until) {
        break;
      }
      const owed = await this.#write(() => this.#owed(id, clock()));
      if (owed === null) {
        continue;
      }
      let outcome: RevokeOutcome = "failed";
      try {
        const revoking = this.#gateway.revokeAtProvider(this.#owner(), id, owed.version,
                                                         owed.blob);
        const answer = await inTime(revoking, CONNECTION_LIMITS.revokeMs);
        if (answer === "late") {
          revoking.catch(() => undefined);
        } else {
          outcome = answer.revoked === true ? "revoked" : "not_revocable";
        }
      } catch {
        console.error("a connection's provider revoke failed; it is tried again later");
      }
      await this.#write(() => this.#revokeOutcome(id, owed.version, outcome, clock()));
    }
    await this.#reads(this.#credit, until);
    await this.#reads(this.#models, until);
  }

  /**
   * Each due `read`, each bounded by `readMs`, its outcome kept in its connection's row
   * (`LaneRead.kept`) while the connection is still active at the version read.
   */
  async #reads<R, A>(read: LaneRead<R, A>, until: number): Promise<void> {
    for (const id of await this.#write(() => this.#dueReads(read.table, clock()))) {
      if (clock() >= until) {
        break;
      }
      const owed = await this.#write(() => this.#readOwed(read, id, clock()));
      if (owed === null) {
        continue;
      }
      let outcome: A | "failed" = "failed";
      try {
        const reading = read.call(owed.credential, owed.read);
        const answer = await inTime(reading, CONNECTION_LIMITS.readMs);
        if (answer === "late") {
          reading.catch(() => undefined);
        } else {
          outcome = read.answer(answer) ?? "failed";
          if (outcome === "failed") {
            console.error(`a connection's ${read.name} read answered no verdict`);
          }
        }
      } catch (error) {
        console.error(`a connection's ${read.name} read failed ` +
                      `(${refusalOf(error)?.code ?? "no code"}); it is read again later`);
      }
      await this.#write(() => {
        if (this.current(id, owed.credential.version)) {
          read.kept(id, outcome, clock());
        }
      });
    }
  }

  /**
   * In a unit: each active connection never considered gets its row in `table`, due now, and
   * the connections whose read is due, oldest due first, at most `readsPerPass`.
   */
  #dueReads(table: ReadTable, now: number): string[] {
    this.#sql.exec(`INSERT INTO ${table} (connection, due) SELECT id, ? FROM connections ` +
                     `WHERE status = 'active' AND id NOT IN (SELECT connection FROM ${table})`,
                   now);
    return this.#sql.exec<{ connection: string }>(
      `SELECT connection FROM ${table} WHERE due IS NOT NULL AND due <= ? ORDER BY due ` +
        "LIMIT ?", now, CONNECTION_LIMITS.readsPerPass).toArray()
      .map((row) => row.connection);
  }

  /**
   * In a unit: a due read's blob and its source record's read, re-read; null, and no read
   * due, for a connection no longer active or a source whose record names no such read.
   */
  #readOwed<R, A>(read: LaneRead<R, A>, id: string, now: number):
      { credential: Credential; read: R } | null {
    const due = this.#sql.exec<{ due: number | null }>(
      `SELECT due FROM ${read.table} WHERE connection = ?`, id).toArray()[0]?.due ?? null;
    if (due === null || due > now) {
      return null;
    }
    const row = this.#row(id);
    const named = row === undefined ? undefined : read.of(bundledSources().get(row.source));
    if (row?.status !== "active" || named === undefined) {
      this.#sql.exec(`UPDATE ${read.table} SET due = NULL WHERE connection = ?`, id);
      return null;
    }
    try {
      return { credential: this.credential(id, clockMs()), read: named };
    } catch (error) {
      // An OAuth token expired meanwhile: read again once a call has refreshed it.
      if (refusalOf(error)?.code !== "credential_expired") {
        throw error;
      }
      this.#sql.exec(`UPDATE ${read.table} SET due = ? WHERE connection = ?`,
                     now + read.retryS, id);
      return null;
    }
  }

  /**
   * In a unit: a credit read's outcome: a verdict kept, read again after `creditRecheckS`
   * when it pays for nothing; nothing to read on its blob (`none`), not known, and no read
   * due, so what a refused call said lapses once its recheck is due and credit bought is seen
   * at the next call; a failure, read again after `creditRecheckS`.
   */
  #credited(id: string, outcome: KeyCredit | "none" | "failed", now: number): void {
    if (outcome === "failed") {
      this.#sql.exec("UPDATE connection_credit SET due = ? WHERE connection = ?",
                     now + CONNECTION_LIMITS.creditRecheckS, id);
      return;
    }
    if (outcome === "none") {
      if (this.#row(id)!.pays !== null) {
        this.#changed();
      }
      this.#sql.exec("UPDATE connection_credit SET pays = NULL, due = NULL WHERE connection = ?",
                     id);
      return;
    }
    this.#pays(id, outcome.pays, now);
  }

  /**
   * In a unit: a models read's outcome: its blob's records its listing lists (every one where
   * its record names none, or its blob reaches no such read), each with its score, kept and
   * read again after `modelsRecheckS`; a failure, or a read that leaves no model (a vendor's
   * renamed ids would otherwise drop every one), its models kept as they were, read again after
   * `modelsRetryS`. The owner is told when its vendor lists none of them, once while the notice
   * is kept, since a key that lost every one reads the same.
   */
  #served(id: string, outcome: ModelsRead | "failed", now: number): void {
    const row = this.#row(id)!;
    const served = outcome === "failed" ? outcome : servedJson(outcome);
    if (served === "unmatched") {
      this.#notices.once("connection_listing_unmatched", `${row.display_name} (${id}): its ` +
                           "vendor lists it none of its models, so they are kept as they were " +
                           "and read again hourly; if its key lost access, check it at the " +
                           "provider", now);
    }
    if (served === "failed" || served === "unmatched") {
      this.#sql.exec("UPDATE connection_models SET due = ? WHERE connection = ?",
                     now + CONNECTION_LIMITS.modelsRetryS, id);
      return;
    }
    if (served !== row.read) {
      this.#changed();
    }
    this.#sql.exec("UPDATE connection_models SET models = ?, due = ? WHERE connection = ?",
                   served, now + CONNECTION_LIMITS.modelsRecheckS, id);
  }

  /**
   * In a unit: the connection's key pays, or pays for nothing, as learned at `now`: then it
   * is read again after `creditRecheckS`, and the owner is told when it starts to.
   */
  #pays(id: string, pays: boolean, now: number): void {
    const row = this.#row(id)!;
    this.#sql.exec("UPDATE connection_credit SET pays = ?, due = ? WHERE connection = ?",
                   pays ? 1 : 0, pays ? null : now + CONNECTION_LIMITS.creditRecheckS, id);
    if (paysOf(row) !== pays) {
      this.#changed();
      if (!pays) {
        this.#notices.add("connection_free_only",
                          `${row.display_name} (${id}): its key pays for no priced model, so ` +
                            "auto runs only the models its source serves free on it; add " +
                            "credit at the provider", now);
      }
    }
  }

  /**
   * In a unit: a call on the connection ended with the provider's `status`; one of its source
   * record's `refused` statuses says the key pays for nothing, as a read would.
   */
  refused(id: string, status: number | null, now: number): void {
    const row = this.#row(id);
    const credit = row === undefined ? undefined : bundledSources().get(row.source)?.credit;
    if (row?.status !== "active" || status === null || credit === undefined
        || !credit.refused.includes(status)) {
      return;
    }
    this.#sql.exec("INSERT INTO connection_credit (connection) VALUES (?) " +
                     "ON CONFLICT (connection) DO NOTHING", id);
    this.#pays(id, false, now);
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
        "ORDER BY revoke_due LIMIT ?", now, CONNECTION_LIMITS.revokesPerPass).toArray()
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
    if (outcome === "failed" && tries < CONNECTION_LIMITS.revokeTries) {
      this.#sql.exec("UPDATE connections SET revoke_tries = ?, revoke_due = ? WHERE id = ?",
                     tries, now + CONNECTION_LIMITS.revokeRetryS * tries, id);
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

// -- THE DOORS: the `connections` rows' handlers (`owner/doors.json`) -----------------------------

/**
 * What the `connections` doors reach: the connections, the sandbox egress a revoke ends,
 * and the doorbell subscriptions a kid's sweep reseals.
 */
export interface ConnectionsDoorNeeds extends Pick<DoorUnits, "door" | "synced"> {
  readonly connections: Connections;
  readonly egress: Pick<Egress, "end">;
  readonly doorbell: Pick<Doorbell, "reseal">;
}
//
// Each door is the owner's own, in its live session: an outside client's call carries
// no session and is refused, with a refusal receipt. The binder seals and exchanges with
// the gateway between them; no plaintext secret reaches the object.

export const connectionsDoors = Object.freeze({
  /** A pending connection for the binder to seal a credential for; for OAuth, its flow. */
  connectionPrepare(o: ConnectionsDoorNeeds,
                    { session, request }: Call<ConnectionPrepareRequest, "session">): Prepared {
    return o.connections.prepare(session.hash, request, clock());
  },
  /** The sealed blob the gateway returned, and the models its setup check listed. */
  connectionCommit(o: ConnectionsDoorNeeds, { request }: Call<ConnectionCommitRequest>):
      ConnectionView {
    return o.connections.commit(request, clock());
  },
  /**
   * A connection unusable at once; its credential deleted here, and at its provider. Its
   * sandbox egress streams end once the revoke committed.
   */
  async connectionRevoke(o: ConnectionsDoorNeeds, door: Door<ConnectionRequest>):
      Promise<{ connection: string; revoked: boolean; provider_revoke: boolean }> {
    const revoked = await o.synced(() => door.within(() =>
      o.connections.revoke(door.request, clock())));
    await o.door(() => o.egress.end("connection",
                                            (live) => live.connection === revoked.connection));
    return revoked;
  },
  /**
   * The platform's sweep before a gateway kid leaves the ring: every blob the object holds
   * under it, its connections' and its doorbell subscriptions', an idle one's included, since
   * nothing else reseals it. The platform sweeps again until none is left, and only then
   * retires the kid.
   */
  async gatewayReseal(o: ConnectionsDoorNeeds, door: Door<undefined>): Promise<ResealAnswer> {
    await o.door(() => door.check());
    return resealAnswer([...await o.connections.reseal(), ...await o.doorbell.reseal()]);
  },
  /** Every connection, newest first, never its blob. */
  connectionList(o: ConnectionsDoorNeeds, _call: Call<undefined>):
      { connections: ConnectionView[] } {
    return ({ connections: o.connections.list() });
  },
  /** The callback's state, in the session that began the flow: its verifier, used up. */
  flowFinish(o: ConnectionsDoorNeeds, { session, request }: Call<FlowFinishRequest, "session">):
      { connection: string; version: number; verifier: string; redirect_uri: string;
                source: string } {
    return o.connections.flowFinish(session.hash, request, clock());
  },
});
