/**
 * Inbound events (tasks 2, `/hooks/inbound`; slice L), ported from the Python's
 * `webhook_inbound.py`, `storage/inbound_events.py` and `inbound_routing.py`: the owner's
 * inbound bindings, each an event source that mails one of the owner's own agents, and the
 * one door that accepts an event (`OwnerDO.inboundAccept`).
 *
 * A binding is one of:
 * - `capability`: a capability URL, `/hooks/inbound/<token>`, which the doors mint and show
 *   the owner once; the directory routes the token's SHA-256 (`h:`) to this owner and this
 *   binding at its revision. The owner may add a proof of its own (`inbound-proof.ts`: a
 *   sender's HMAC signature, or a token header), whose secret the owner holds here.
 * - `shared`: an installation of a platform shared app (Slack), whose requests the binder
 *   proves by the platform's signing secret; the directory routes the installation (`i:`)
 *   here. Its install flow, which proves the owner holds the workspace, is owed (tasks 2),
 *   and until it lands `create` refuses every shared binding (`INSTALL_PROOF`).
 *
 * **Config and revision.** A binding names its agent and whether it is enabled; every
 * change moves its revision, and the directory's entry carries the revision it routes, so
 * an entry the owner has since changed or withdrawn is refused here (`binding_withdrawn`),
 * whatever KV's lag.
 *
 * **Dedupe.** An event is the owner's mail to the binding's agent (`dm:<agent>`), keyed by
 * the binding, its revision and the event's own id, so a sender's retry is the same mail
 * (the ledger's own client-message dedupe) and is answered `deduped` with its receipt; the
 * same id with other bytes is refused. The mail starts the agent's run as any mail does
 * (slice ID). Its body is the event's raw bytes, verbatim (Fact 9), base64 in a JSON
 * envelope; the ledger's own body bound refuses a larger one (`too_large`).
 *
 * **The breaker** (the Python's `DeliveryBreakers`): a shared app's events refused because
 * the agent's mailbox is full count as failures; `BREAKER_FAILURES` within
 * `BREAKER_WINDOW_S` trip that binding at that revision, after which its events are an
 * authenticated no-op, so one owner's full mailbox never spends the shared app's failure
 * budget with its vendor (Slack disables an app's event subscriptions past it). The owner
 * re-arms it by any change of the binding (a new revision). A capability URL has no
 * breaker: its sender retries on its own `429`.
 */
import { HEX32, randomHex } from "../../contract/src/ids";

import { Refusal } from "../../contract/src/refusal";
import { INBOUND_ROUTE_MAX, INBOUND_SECRET_MAX } from "../../contract/src/limits";
import type { OwnerStorage } from "./gate";
import { type Profile, profile as checkedProfile, proves } from "../../contract/src/inbound-proof";
import type {
  Call, Door, OwnerState,
} from "./owner-do";
import type { InboundAnswer } from "../../contract/src/binding";
import type {
  BindingRequest, InboundCreateRequest, InboundSetRequest,
} from "../../contract/src/owner";
import { nativeId } from "../../contract/src/binding";
import { clock } from "./clock";
import { codePoints } from "../../contract/src/text";
import { sha256Hex } from "../../contract/src/digest";

/** The bindings an owner holds at most: a technical ceiling (the 10 GB, guard terms). */
export const MAX_BINDINGS = 1_000;
/** A shared app's failures that trip its breaker, and the window they are counted in. */
export const BREAKER_FAILURES = 3;
const BREAKER_WINDOW_S = 60;

const NOT_CONFIGURED = "binding_not_configured: no such inbound binding";
const WITHDRAWN = "binding_withdrawn: the inbound binding was changed or withdrawn";
const PROOF_INVALID = "inbound_proof_invalid: the event's proof does not verify";
export const INSTALL_PROOF = "install_proof_required: a shared app's installation is made by " +
  "its install flow alone";

const BINDING_ID = HEX32;
const ROUTE = /^(?:h:[0-9a-f]{64}|i:[a-z][a-z0-9_]{0,31}:(?:workspace|enterprise):[0-9a-f]{64})$/;
const AGENT = /^[a-z0-9][a-z0-9_-]{0,63}$/;

type BindingKind = "capability" | "shared";

/** A binding as the owner is shown it: never its secret. */
export interface InboundBinding {
  readonly binding_id: string;
  readonly kind: BindingKind;
  readonly agent: string;
  readonly enabled: boolean;
  readonly revision: number;
  /** The directory entry that routes it, which the doors write and delete. */
  readonly route: string;
  readonly proof: string | null;
  readonly tripped: boolean;
  readonly created_at: number;
  readonly changed_at: number;
}

interface Row {
  readonly binding_id: string;
  readonly kind: BindingKind;
  readonly agent: string;
  readonly enabled: number;
  readonly revision: number;
  readonly route: string;
  readonly profile: string | null;
  readonly secret: string | null;
  readonly failures: string;
  readonly tripped: number;
  readonly created_at: number;
  readonly changed_at: number;
}

function shown(row: Row): InboundBinding {
  return { binding_id: row.binding_id, kind: row.kind, agent: row.agent,
           enabled: row.enabled === 1, revision: row.revision, route: row.route,
           proof: row.profile === null ? null : (JSON.parse(row.profile) as Profile).method,
           tripped: row.tripped === 1, created_at: row.created_at,
           changed_at: row.changed_at };
}

export function agentName(value: unknown): string {
  if (typeof value !== "string" || !AGENT.test(value)) {
    throw new Refusal("invalid", "an inbound binding names one of the owner's agents");
  }
  return value;
}

export function bindingId(value: unknown): string {
  if (typeof value !== "string" || !BINDING_ID.test(value)) {
    throw new Refusal("invalid", "binding_id is 32 hex");
  }
  return value;
}

/** A binding's new config, as the owner sets it. */
export interface Created {
  readonly kind: BindingKind;
  readonly agent: string;
  readonly route: string;
  readonly profile: Profile | null;
  readonly secret: string | null;
}

/** A create request checked whole. A shared binding takes no proof of its own. */
export function created(request: unknown): Created {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Refusal("invalid", "an inbound binding is an object");
  }
  const r = request as Record<string, unknown>;
  const keys = Object.keys(r);
  if (keys.some((key) => !["kind", "agent", "route", "profile", "secret"].includes(key))) {
    throw new Refusal("invalid",
                      "an inbound binding is its kind, agent, route, and a proof and secret");
  }
  if (r["kind"] !== "capability" && r["kind"] !== "shared") {
    throw new Refusal("invalid",
                      "an inbound binding is a capability URL or a shared app's install");
  }
  if (r["kind"] === "shared") {
    // Only a shared app's install flow, which proves the owner holds the workspace, may
    // name an installation; until it lands no owner may claim one (another workspace's
    // team id would route that workspace's events here).
    throw new Refusal("install_proof_required", INSTALL_PROOF);
  }
  if (typeof r["route"] !== "string" || r["route"].length > INBOUND_ROUTE_MAX
      || !ROUTE.test(r["route"])
      || (r["kind"] === "capability") !== r["route"].startsWith("h:")) {
    throw new Refusal("invalid", "an inbound binding's route is its directory entry");
  }
  const proof = r["profile"] === undefined || r["profile"] === null ? null
    : checkedProfile(r["profile"]);
  const secret = r["secret"] === undefined || r["secret"] === null ? null : r["secret"];
  if (secret !== null && (typeof secret !== "string" || secret.length < 16
                          || secret.length > INBOUND_SECRET_MAX)) {
    throw new Refusal("invalid",
                      `an inbound proof secret is 16 to ${INBOUND_SECRET_MAX} characters`);
  }
  if ((proof?.method === "hmac-sha256") !== (secret !== null)) {
    throw new Refusal("invalid", "an HMAC proof holds a secret, and only an HMAC proof does");
  }
  return { kind: r["kind"], agent: agentName(r["agent"]), route: r["route"], profile: proof,
           secret: secret as string | null };
}

export class Inbound {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  #row(id: string): Row | undefined {
    return this.#storage.sql.exec<Row & Record<string, SqlStorageValue>>(
      "SELECT * FROM inbound_bindings WHERE binding_id = ?", id).toArray()[0];
  }

  list(): InboundBinding[] {
    return this.#storage.sql.exec<Row & Record<string, SqlStorageValue>>(
      "SELECT * FROM inbound_bindings ORDER BY created_at, binding_id").toArray().map(shown);
  }

  /** The directory routes of every binding: what an erasure deletes. */
  routes(): string[] {
    return this.#storage.sql.exec<{ route: string }>("SELECT route FROM inbound_bindings")
      .toArray().map((row) => row.route);
  }

  /** In a unit: a new binding, enabled, at revision 1. */
  create(change: Created, now: number): InboundBinding {
    const sql = this.#storage.sql;
    if (sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM inbound_bindings").one().n
        >= MAX_BINDINGS) {
      throw new Refusal("refused", "too many inbound bindings; revoke one first");
    }
    if (sql.exec("SELECT 1 FROM inbound_bindings WHERE route = ?", change.route).toArray()
          .length > 0) {
      throw new Refusal("refused", "this route is bound already");
    }
    const id = randomHex(16);
    sql.exec("INSERT INTO inbound_bindings (binding_id, kind, agent, enabled, revision, route, " +
               "profile, secret, created_at, changed_at) VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, ?)",
             id, change.kind, change.agent, change.route,
             change.profile === null ? null : JSON.stringify(change.profile), change.secret,
             now, now);
    return shown(this.#row(id)!);
  }

  /**
   * In a unit: the owner's change of a binding against its revision (`enabled`, `agent`);
   * the revision moves, which re-arms a tripped breaker and refuses every entry routing an
   * older one.
   */
  set(request: unknown, now: number): InboundBinding {
    const r = (request ?? {}) as Record<string, unknown>;
    if (Object.keys(r).some((key) => !["binding_id", "expected_revision", "enabled",
                                       "agent"].includes(key))) {
      throw new Refusal("invalid",
                        "an inbound change names its binding, its revision, and what moves");
    }
    const row = this.#row(bindingId(r["binding_id"]));
    if (row === undefined) {
      throw new Refusal("binding_not_configured", NOT_CONFIGURED);
    }
    if (r["expected_revision"] !== row.revision) {
      throw new Refusal("refused", "inbound binding changed");
    }
    if (r["enabled"] !== undefined && typeof r["enabled"] !== "boolean") {
      throw new Refusal("invalid", "enabled is true or false");
    }
    const enabled = r["enabled"] === undefined ? row.enabled : r["enabled"] ? 1 : 0;
    const agent = r["agent"] === undefined ? row.agent : agentName(r["agent"]);
    this.#storage.sql.exec(
      "UPDATE inbound_bindings SET enabled = ?, agent = ?, revision = revision + 1, " +
        "failures = '[]', tripped = 0, changed_at = ? WHERE binding_id = ?",
      enabled, agent, now, row.binding_id);
    return shown(this.#row(row.binding_id)!);
  }

  /** In a unit: the binding gone; its route, for the doors to delete. */
  revoke(request: unknown): { binding_id: string; route: string | null } {
    const id = bindingId((request as { binding_id?: unknown } | null)?.binding_id);
    const row = this.#row(id);
    this.#storage.sql.exec("DELETE FROM inbound_bindings WHERE binding_id = ?", id);
    return { binding_id: id, route: row?.route ?? null };
  }

  /**
   * The binding an event names, at the revision its route carried, or refused: none
   * (`NOT_CONFIGURED`), or changed or disabled since (`WITHDRAWN`).
   */
  current(id: string, revision: unknown): Row {
    const row = this.#row(id);
    if (row === undefined) {
      throw new Refusal("binding_not_configured", NOT_CONFIGURED);
    }
    if (row.revision !== revision || row.enabled !== 1) {
      throw new Refusal("binding_withdrawn", WITHDRAWN);
    }
    return row;
  }

  /** In a unit: a shared app's event refused for a full mailbox; whether it now trips. */
  failed(row: Row, now: number): boolean {
    const kept = (JSON.parse(row.failures) as number[])
      .filter((at) => at > now - BREAKER_WINDOW_S).slice(-(BREAKER_FAILURES - 1));
    kept.push(now);
    const trips = kept.length >= BREAKER_FAILURES;
    this.#storage.sql.exec(
      "UPDATE inbound_bindings SET failures = ?, tripped = ? WHERE binding_id = ?",
      JSON.stringify(kept), trips ? 1 : row.tripped, row.binding_id);
    return trips && row.tripped !== 1;
  }

  /** In a unit: an event accepted; the breaker's count starts again. */
  succeeded(row: Row): void {
    if (row.failures !== "[]") {
      this.#storage.sql.exec("UPDATE inbound_bindings SET failures = '[]' WHERE binding_id = ?",
                             row.binding_id);
    }
  }
}

/** The proof an owner's capability binding holds, and its secret, or null. */
function ownerProof(row: Row): { profile: Profile; secret: string | null } | null {
  return row.profile === null ? null
    : { profile: JSON.parse(row.profile) as Profile, secret: row.secret };
}

export type { Row as InboundRow };

/** An inbound event as the binder hands it: its id and time, its raw bytes, its headers. */
export interface InboundEvent {
  readonly event_id: string;
  readonly event_at: number;
  readonly body: Uint8Array;
  readonly headers: Readonly<Record<string, string>>;
}

// -- THE DOORS: the `inbound` rows' handlers (`contract/doors.json`) ------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/**
 * An inbound event checked in its unit (its route's binding at the route's revision,
 * enabled, as the call bound it): an owner-held proof on the raw bytes, and its mail's
 * room, key and envelope; or the no-op a tripped breaker answers.
 */
function inboundEvent(o: OwnerState, { route, r }: Call, now: number):
    { done: InboundAnswer }
    | { row: Row; room: string; cmid: string; envelope: string } {
  const row = route!;
  const eventId = r["event_id"];
  const eventAt = r["event_at"];
  const body = r["body"];
  const headers = r["headers"];
  if (typeof eventId !== "string" || !eventId.trim() || codePoints(eventId) > 256
      || typeof eventAt !== "number" || !Number.isSafeInteger(eventAt) || eventAt < 0
      || eventAt > 999_999_999_999 || !(body instanceof Uint8Array)
      || typeof headers !== "object" || headers === null || Array.isArray(headers)) {
    throw new Refusal("invalid",
                      "an inbound event is its id, its time, its bytes and its headers");
  }
  if (row.kind === "shared" && row.tripped === 1) {
    return { done: { state: "no_op", error: "breaker_tripped" } };
  }
  const proof = ownerProof(row);
  if (proof !== null && !proves(proof.profile, headers as Record<string, string>, body,
                                proof.secret, Math.floor(now))) {
    throw new Refusal("inbound_proof_invalid", PROOF_INVALID);
  }
  // An HMAC with no timestamp signs the body alone, so a captured body could come again
  // under a new event id: such an event is keyed by its bytes, a replay always deduped.
  const unstamped = proof?.profile.method === "hmac-sha256"
    && proof.profile.timestamp_header === undefined;
  const identity = unstamped
    ? `body:${sha256Hex(body)}` : `id:${eventId}`;
  const key = sha256Hex(`tinyassets-inbound\0${row.binding_id}\0${row.revision}\0${identity}`);
  const envelope = JSON.stringify({ inbound: {
    binding: row.binding_id, event_id: eventId, event_at: eventAt,
    raw_base64: Buffer.from(body).toString("base64") } });
  return { row, room: `dm:${nativeId(row.agent)}`, cmid: `inb_${key}`, envelope };
}

/**
 * An inbound event the agent's mail refused: too much unread mail (`ledger_full`) counts
 * toward a shared app's breaker (and its trip is the owner's notice); the hot ceiling while
 * older mail moves out (`storage_busy`) is retried and counts nothing; the rest are the
 * event's own refusals.
 */
function inboundRefused(o: OwnerState, row: Row, error: unknown, now: number): InboundAnswer {
  const refused = error instanceof Refusal ? error.code : null;
  // The hot ceiling, clearing by itself or held by a full storage until the owner frees
  // space (the ledger's wall, marked `ceiling`): the event is sent again, never lost.
  if (refused === "storage_busy"
      || (error instanceof Refusal && error.facts?.["ceiling"] === true)) {
    return { state: "refused", error: "mailbox_full", retry: true };
  }
  if (error instanceof Refusal && error.transient) {
    throw error;  // for the moment only (a full pool): never the event's own refusal
  }
  if (refused === "ledger_full") {
    if (row.kind === "shared" && o.inbound.failed(row, Math.floor(now))) {
      o.notices.add("inbound", `An inbound app's events to ${row.agent} were paused: ` +
                        "its mailbox was full. Change the binding to resume.", now);
    }
    return { state: "refused", error: "mailbox_full", retry: true };
  }
  if (refused === "post_conflict") {
    return { state: "refused", error: "event_conflict" };
  }
  if (refused === "message_body") {
    return { state: "refused", error: "too_large" };
  }
  if (error instanceof Refusal && !error.invalid) {
    return { state: "refused", error: "agent_unavailable" };
  }
  throw error;
}

/** The `inbound` doors' handlers, by door. */
export const inboundDoors = Object.freeze({
  inboundList(o: OwnerState, _call: Call<undefined>): { bindings: InboundBinding[] } {
    return { bindings: o.inbound.list() };
  },
  inboundCreate(o: OwnerState, { request }: Call<InboundCreateRequest>): InboundBinding {
    return o.inbound.create(created(request), clock());
  },
  inboundSet(o: OwnerState, { request }: Call<InboundSetRequest>): InboundBinding {
    return o.inbound.set(request, clock());
  },
  inboundRevoke(o: OwnerState, { request }: Call<BindingRequest>):
      { binding_id: string; route: string | null } {
    return o.inbound.revoke(request);
  },
  async inboundAccept(o: OwnerState, door: Door<InboundEvent>): Promise<InboundAnswer> {
    return o.placing((): {
        body: unknown; writer: string } | { done: InboundAnswer } => {
      const now = clock();
      const event = inboundEvent(o, door.check(), now);
      if ("done" in event) {
        return event;
      }
      try {
        const resend = o.ledger.prepare(null, event.room, event.cmid, event.envelope, null);
        if (resend !== null) {
          return { done: { state: "admitted", receipt: resend.message_id, deduped: true } };
        }
      } catch (error) {
        return { done: inboundRefused(o, event.row, error, now) };
      }
      return { body: event.envelope, writer: "owner" };
    }, (placed) => {
      const now = clock();
      const event = inboundEvent(o, door.check(), now);
      if ("done" in event) {
        return event.done;
      }
      o.keepTiers(door.binding);
      try {
        const posted = o.ledger.post(null, event.room, event.cmid, event.envelope, null,
                                         now, null, placed, "owner");
        o.inbound.succeeded(event.row);
        return { state: "admitted", receipt: posted.message_id, deduped: false };
      } catch (error) {
        return inboundRefused(o, event.row, error, now);
      }
    }, { synced: true });
  },
});
