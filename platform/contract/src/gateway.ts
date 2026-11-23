/**
 * The model gateway's RPC contract (`platform/gateway`,
 * `docs/decisions/untangle-2026-11-wave-1.md` A1): each of its entrypoints' methods, as their
 * callers bind them, and the values that cross them. The gateway implements each entrypoint
 * against it (`gateway/src/index.ts`), and the owner's object and the doors bind it from here, so
 * no caller copies a gateway type.
 *
 * - `GatewayService`: the default entrypoint, as the owner's object binds it (`GATEWAY`).
 * - `ConnectService`: `ConnectGateway`, the doors' connect steps (`GATEWAY` of the doors).
 * - `EgressService`: `EgressGateway`, the egress door's one `forward` (`EGRESS`).
 *
 * Every refusal is the contract's one refusal (`refusal.ts`), its code one of
 * `GATEWAY_CODES`. A metered call's outcome crosses as one JSON line on its own stream
 * (`outcomeLine`, `outcomeOf`), and a blob resealed meanwhile in the gateway's own header
 * (`RESEALED_HEADER`, `egress.ts`).
 *
 * Each value one side builds and the gateway reads has its checker beside its type
 * (`metering`, `doorbellInput`, `ringRequest`; a credit read's, `CreditRead`, and a
 * strength read's, `StrengthRead`, are `egress-scope.ts`'s, as the source records are checked
 * by them too), so its builder and its reader hold one rule.
 */
import { keyed, object, whole } from "./checks.ts";
import { taken } from "./handed.ts";
import type {
  CreditRead, EgressScope, ModelScore, ServiceSource, Source, StrengthRead,
} from "./egress-scope.ts";
import type { ModelRecord } from "./model-record.ts";
import { Refusal } from "./refusal.ts";
import { AUTH_HEADER } from "./wake-records.ts";

/** A sealed blob and the kid it is under. */
export interface Sealed {
  readonly blob: string;
  readonly kid: string;
}

/** A credential resealed under the active kid, at the next version, for the object to store. */
export interface Resealed extends Sealed {
  readonly version: number;
}

/** What the browser's callback brought back, and the flow's PKCE verifier. */
export interface AuthorizationGrant {
  readonly code: string;
  readonly verifier: string;
  readonly redirect_uri: string;
}

/** What an authorization code bought, sealed. */
export interface Exchanged extends Sealed {
  readonly expires_at: number | null;
  readonly scope?: string;
}

/** An OAuth credential refreshed, bound to the next version. */
export interface Refreshed extends Resealed {
  readonly expires_at: number | null;
}

/** Whether the provider was asked to revoke: false for an API key or a source with no endpoint. */
export interface Revoked {
  readonly revoked: boolean;
}

/**
 * What a setup check found: the credential works, the models it can be asked for, and
 * where sandbox egress may forward it (`egress`, its sealed base URL and endpoints), which
 * the owner's object keeps to refuse an endpoint not sealed before any forward.
 */
export interface Verified {
  readonly models: readonly ModelRecord[];
  readonly egress: EgressScope;
  readonly resealed?: Resealed;
}

/**
 * What a metered call is held to: the output cap its reservation leaves, its body's size,
 * the model and tier (its tier option's value, or null) it was reserved for, and whether
 * that model is a router that may route to a priced model (`routed`): one its vendor bills as
 * the model it routes each call to, so the call is priced by the model its answer says served
 * it, never the router's own listed price (`sources/src/routers.ts`).
 */
export interface Metering {
  readonly max_output_tokens: number;
  readonly input_bytes: number;
  readonly model: string;
  readonly tier: string | null;
  readonly routed: boolean;
}

/** A model id or a tier option's value, as a call names it and a reservation holds it. */
export const MODEL_NAME = /^[\x21-\x7e]{1,256}$/;

function named(value: unknown): value is string {
  return typeof value === "string" && MODEL_NAME.test(value);
}

/** A reservation's metering, checked; anything else is the owner's object's bug (`internal`). */
export function metering(value: unknown): Metering {
  if (!keyed(value, ...taken("metering"))
      || !whole(value["input_bytes"], 1) || !whole(value["max_output_tokens"], 1)
      || !named(value["model"]) || (value["tier"] !== null && !named(value["tier"]))
      || (value["routed"] !== undefined && typeof value["routed"] !== "boolean")) {
    throw new Refusal("internal");
  }
  // `routed` is handed `always` (`handed.json`): an owner's version before hands none, and
  // reserved no router but at its listed price.
  return Object.freeze({ input_bytes: value["input_bytes"],
                         max_output_tokens: value["max_output_tokens"], model: value["model"],
                         tier: value["tier"] as string | null,
                         routed: value["routed"] === true });
}

/** How a metered call ended, as the owner's object settles it. */
export type MeterOutcome =
  | { readonly state: "succeeded"; readonly tokens: number; readonly cost: number;
      /** The model the answer says served it, when other than the one the call named. */
      readonly served?: string }
  | { readonly state: "failed" }
  | { readonly state: "unknown" };

/** What the owner's object answered a reserve: the call's metering, or its refusal's code. */
export type Reservation = { readonly metering: Metering } | { readonly refused: string };

/**
 * The egress door's callback (over RPC, a stub to the door): the owner's object reserves the
 * admitted call for the model and tier its body names, as the gateway's one parse read them.
 */
export type Reserve = (call: { readonly model: string; readonly tier: string | null }) =>
  Promise<Reservation>;

/** A metered call's outcome as its one line on the outcome stream. */
export function outcomeLine(outcome: MeterOutcome): string {
  return `${JSON.stringify(outcome)}\n`;
}

/** The outcome a line says: a success or a failure as written, anything else `unknown`. */
export function outcomeOf(text: string): MeterOutcome {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { state: "unknown" };
  }
  const state = typeof parsed === "object" && parsed !== null
    ? (parsed as { state?: unknown }).state : undefined;
  return state === "succeeded" || state === "failed" ? parsed as MeterOutcome
    : { state: "unknown" };
}

/** A pasted or subscribed callback: its wake kind's record id, its URL and its secret. */
export interface DoorbellInput {
  readonly record: string;
  readonly url: string;
  readonly secret: string;
}

/**
 * A doorbell input's shape, checked (`invalid_request`); its URL and secret are then held to
 * its record's own rules where it is sealed.
 */
export function doorbellInput(value: unknown): DoorbellInput {
  if (!keyed(value, ...taken("doorbell_input")) || typeof value["record"] !== "string"
      || typeof value["url"] !== "string" || typeof value["secret"] !== "string") {
    throw new Refusal("invalid_request");
  }
  return Object.freeze({ record: value["record"], url: value["url"], secret: value["secret"] });
}

/** An MCP Events callback, verified by its signed challenge and sealed, or why not. */
export type DoorbellVerified =
  | { readonly verified: true; readonly sealed: Sealed }
  | { readonly verified: false; readonly reason: string };

/**
 * The part of one wake's request the object builds, from its record (`wake-kinds.ts`
 * `doorbellRequest`): its headers (never the auth, which the gateway adds from the sealed
 * secret), its body bytes and the wake's own id, kept across a retry.
 */
export interface RingRequest {
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
  readonly event_id: string;
}

/** A wake's own id, as a ring sends it (`webhook-id`). */
export const EVENT_ID = /^[A-Za-z0-9_-]{1,128}$/;

/**
 * A ring's request, checked (`invalid_request`): text headers, none of them the auth's,
 * which the gateway adds; its body; its event id.
 */
export function ringRequest(value: unknown): RingRequest {
  const headers = object(value) ? value["headers"] : undefined;
  if (!keyed(value, ...taken("ring_request")) || typeof value["body"] !== "string"
      || typeof value["event_id"] !== "string" || !EVENT_ID.test(value["event_id"])
      || !object(headers) || !Object.entries(headers).every(([name, text]) =>
        typeof text === "string" && !AUTH_HEADER.test(name))) {
    throw new Refusal("invalid_request");
  }
  return Object.freeze({ headers: Object.freeze({ ...headers as Record<string, string> }),
                         body: value["body"], event_id: value["event_id"] });
}

/** The gateway's answer to one ring: the callback's status, or that it was not sent. */
export type RingAnswer =
  | { readonly state: "answered"; readonly status: number; readonly retry_after: string | null;
      readonly resealed?: Resealed }
  | { readonly state: "refused" }
  | { readonly state: "unknown" };

/** What a key's limits answer said: whether it can pay for a priced model. */
export interface KeyCredit {
  readonly pays: boolean;
}

/** What a strength listing said: each scored model's score, higher stronger. */
export interface ModelStrength {
  readonly scores: readonly ModelScore[];
}

/** The default entrypoint, as the owner's object binds it (`GATEWAY`). */
export interface GatewayService {
  /**
   * What a connection's key can pay for, read where its source reports it (`CreditRead`),
   * with the sealed secret, at a `GET` its sealed endpoints allow (`endpoint_refused` where
   * they do not: a connection sealed before its record named the read).
   */
  credit(owner: string, connection: string, version: number, blob: string,
         read: CreditRead): Promise<KeyCredit>;
  /**
   * How strong each model is, as a connection's source's vendor lists it (`StrengthRead`),
   * read with the sealed secret, at a `GET` its sealed endpoints allow (`endpoint_refused`
   * where they do not), as `credit` is.
   */
  strength(owner: string, connection: string, version: number, blob: string,
           read: StrengthRead): Promise<ModelStrength>;
  /** An OAuth credential refreshed; the result is bound to `version + 1`. */
  refresh(owner: string, connection: string, version: number, blob: string):
    Promise<Refreshed>;
  /** The provider asked to revoke an OAuth credential (RFC 7009). */
  revokeAtProvider(owner: string, connection: string, version: number, blob: string):
    Promise<Revoked>;
  /** A blob resealed under the active kid, bound to `version + 1` (the owner's sweep). */
  reseal(owner: string, connection: string, version: number, blob: string): Promise<Resealed>;
  /**
   * A doorbell subscription's blob resealed under the active kid, bound to `version + 1`:
   * the owner's sweep reseals its subscriptions' blobs as it does its connections', so a kid
   * that sealed a doorbell can be retired too.
   */
  resealDoorbell(owner: string, subscription: string, version: number, blob: string):
    Promise<Resealed>;
  /**
   * One wake sent with a doorbell subscription's sealed secret: the gateway opens
   * the blob (and the replaced one's, inside its rotation window) and makes the send.
   */
  ringDoorbell(owner: string, subscription: string, version: number, blob: string,
               previous: { readonly version: number; readonly blob: string } | null,
               request: RingRequest): Promise<RingAnswer>;
}

/**
 * `ConnectGateway`, as the doors bind it: the connect steps alone (no model call, no egress
 * forwarding, no keyring). Every failure is one of its fixed codes.
 */
export interface ConnectService {
  /**
   * A pasted API key, sealed for the connection with its source record's values, or a
   * service's (`serviceSource`).
   */
  seal(owner: string, connection: string, version: number, key: string,
       record: Source | ServiceSource): Promise<Sealed>;
  /** An authorization code exchanged, and what it buys sealed. */
  exchange(owner: string, connection: string, version: number, grant: AuthorizationGrant,
           record: Source): Promise<Exchanged>;
  /** A sealed credential checked against its provider, and its models listed. */
  verify(owner: string, connection: string, version: number, blob: string): Promise<Verified>;
  /** A wake token the owner pasted, sealed for its subscription. */
  sealDoorbell(owner: string, subscription: string, version: number, input: DoorbellInput):
    Promise<Sealed>;
  /** An MCP Events callback, verified by a signed challenge, then sealed. */
  verifyDoorbell(owner: string, subscription: string, version: number, input: DoorbellInput):
    Promise<DoorbellVerified>;
}

/**
 * `EgressGateway`, as the egress door binds it: one sandbox call forwarded under the sealed
 * endpoints of the blob the owner's object handed out for it, its body streamed; a model
 * call's reservation made through `reserve` once the gateway's one parse read the body's
 * model and tier, null for a free call; its metered outcome comes as one line on `outcome`
 * (`outcomeLine`), and closing `stop` aborts it. Every refusal is a fixed-code answer; the
 * provider's own answers pass through.
 */
export interface EgressService {
  forward(owner: string, connection: string, version: number, blob: string, request: Request,
          reserve: Reserve | null, stop: ReadableStream<Uint8Array>):
    Promise<{ response: Response; outcome: ReadableStream<Uint8Array> }>;
}
