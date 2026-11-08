/**
 * The model gateway's RPC contract (`tinyassets-gateway`, `decisions.md` A1): each of its
 * entrypoints' methods, as their callers bind them, and the values that cross them. The
 * gateway implements each entrypoint against it (`gateway/src/index.ts`), and the owner's
 * object and the doors bind it from here, so no caller copies a gateway type.
 *
 * - `GatewayService`: the default entrypoint, as the owner's object binds it (`GATEWAY`).
 * - `ConnectService`: `ConnectGateway`, the doors' connect steps (`GATEWAY` of the doors).
 * - `EgressService`: `EgressGateway`, the egress door's one `forward` (`EGRESS`).
 *
 * Every refusal is the contract's one refusal (`refusal.ts`), its code one of
 * `GATEWAY_CODES`. A metered call's outcome crosses as one JSON line on its own stream
 * (`outcomeLine`, `outcomeOf`), and a blob resealed meanwhile in the gateway's own header
 * (`RESEALED_HEADER`, `egress.ts`).
 */
import type { EgressScope, Source } from "./egress-scope";
import type { ModelRecord } from "./model-record";

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
 * the owner's object keeps to refuse an endpoint not sealed before any forward (G4).
 */
export interface Verified {
  readonly models: readonly ModelRecord[];
  readonly egress: EgressScope;
  readonly resealed?: Resealed;
}

/**
 * What a metered call is held to: the output cap its reservation leaves, its body's size,
 * and the model and tier (its tier option's value, or null) it was reserved for.
 */
export interface Metering {
  readonly max_output_tokens: number;
  readonly input_bytes: number;
  readonly model: string;
  readonly tier: string | null;
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

/** The gateway's answer to one ring: the callback's status, or that it was not sent. */
export type RingAnswer =
  | { readonly state: "answered"; readonly status: number; readonly retry_after: string | null;
      readonly resealed?: Resealed }
  | { readonly state: "refused" }
  | { readonly state: "unknown" };

/** The default entrypoint, as the owner's object binds it (`GATEWAY`). */
export interface GatewayService {
  /** An OAuth credential refreshed; the result is bound to `version + 1`. */
  refresh(owner: string, connection: string, version: number, blob: string):
    Promise<Refreshed>;
  /** The provider asked to revoke an OAuth credential (RFC 7009). */
  revokeAtProvider(owner: string, connection: string, version: number, blob: string):
    Promise<Revoked>;
  /**
   * One wake sent with a doorbell subscription's sealed secret (slice W): the gateway opens
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
  /** A pasted API key, sealed for the connection with its source record's values. */
  seal(owner: string, connection: string, version: number, key: string, record: Source):
    Promise<Sealed>;
  /** An authorization code exchanged, and what it buys sealed. */
  exchange(owner: string, connection: string, version: number, grant: AuthorizationGrant,
           record: Source): Promise<Exchanged>;
  /** A sealed credential checked against its provider, and its models listed. */
  verify(owner: string, connection: string, version: number, blob: string): Promise<Verified>;
  /** A wake token the owner pasted, sealed for its subscription (slice W). */
  sealDoorbell(owner: string, subscription: string, version: number, input: DoorbellInput):
    Promise<Sealed>;
  /** An MCP Events callback, verified by a signed challenge, then sealed (slice W). */
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
