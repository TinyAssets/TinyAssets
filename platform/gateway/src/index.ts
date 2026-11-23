/**
 * The model gateway Worker (ADR-032 Decision 3; research §12 G1). Its config has no routes
 * and no URL, so only the service bindings of the owner Worker and the doors Worker (the
 * binder) reach it, and it binds nothing, so it reaches no object.
 *
 * - `seal`: a pasted API key, sealed with its source record (§7.2 inline connect).
 * - `exchange`, `refresh`, `revokeAtProvider`: OAuth connections (`oauth.ts`).
 * - `verify`: the connection's setup check, and the models it can be asked for.
 * - `credit`: what a connection's key can pay for, where its source reports it.
 * - `models`: the models a connection's blob reaches by the catalog now (`forward.ts`).
 * - `listing`: the models a connection's source's vendor lists its key served, with their
 *   scores (`forward.ts`).
 * - `forward`: raw passthrough for sandbox egress (`forward.ts`).
 * - `reseal`, `resealDoorbell`: a connection's or a doorbell subscription's blob resealed
 *   under the active kid at the next version (the sweep that retires a kid).
 * - `sealDoorbell`, `verifyDoorbell`, `ringDoorbell`: a wake's secret sealed, an MCP Events
 *   callback verified and sealed, and one wake sent with it (`doorbell.ts`).
 *
 * Each entrypoint serves one caller. The doors Worker binds `ConnectGateway`, the steps of
 * a connect (`seal`, `exchange`, `verify`, and a doorbell's seal and verification), and
 * `EgressGateway`, which serves only `forward`: the egress door (the binder) streams a
 * sandbox's request through it once the owner's object admitted the request, so the
 * object sleeps while the bytes flow (research §5.4 step 4). The owner Worker binds the
 * default entrypoint: `refresh`, `revokeAtProvider`, `credit`, `models`, `listing`, `reseal`,
 * `resealDoorbell` and `ringDoorbell`. No entrypoint serves another's methods.
 *
 * Every method takes the owner, connection and version the blob is bound to from its
 * caller, and the seal holds the call to them: the gateway never chooses an owner. Each
 * refusal is a fixed code (`failure.ts`), and nothing the gateway catches is rethrown as
 * it was.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { builtinCatalog } from "./catalog";
import { type Credential } from "./credential";
import { keySource, secret } from "../../contract/src/egress-scope";
import {
  type ConnectService, type DoorbellVerified, type EgressService, type Exchanged,
  type GatewayService, type KeyCredit, type MeterOutcome, type ModelListing, outcomeLine,
  type Refreshed, type Resealed, type Reserve, type Revoked, type RingAnswer, type Sealed,
  type SealedModels, type Verified,
} from "../../contract/src/gateway";
import { resealDoorbell, ringDoorbell, sealDoorbell, verifyDoorbell } from "./doorbell";
import { codeOf, failureResponse, guarded } from "./failure";
import { credit, forwardMetered, forwardRead, listing, models, verify } from "./forward";
import type { GatewayEnv } from "./env";
import { binding, open, reseal, seal } from "./keyring";
import { exchange, refresh, revokeAtProvider } from "./oauth";

/**
 * What the doors Worker (the binder) binds, as the named entrypoint `ConnectGateway`: the
 * connect steps (a key's seal, a code's exchange, a credential's check, and a doorbell's seal
 * and verification) and nothing else. It cannot make a model call, forward egress, refresh,
 * reseal or revoke, and the keyring stays here (doors research §7.2,
 * `doors/guards/config.test.ts`).
 */
export class ConnectGateway extends WorkerEntrypoint<GatewayEnv> implements ConnectService {
  async seal(owner: string, connection: string, version: number, key: string,
             record: unknown): Promise<Sealed> {
    return guarded(async () => {
      const bound = binding(owner, connection, version);
      const checked = keySource(record);
      const plaintext: Credential = { kind: "api_key", secret: secret(key),
                                      upstream: checked.upstream, endpoints: checked.endpoints,
                                      sealed_at: Date.now() };
      return seal(this.env, bound, plaintext);
    });
  }

  async exchange(owner: string, connection: string, version: number, grant: unknown,
                 record: unknown): Promise<Exchanged> {
    return guarded(() => exchange(this.env, binding(owner, connection, version), grant,
                                  record));
  }

  async verify(owner: string, connection: string, version: number,
               blob: string): Promise<Verified> {
    return guarded(() => verify(this.env, binding(owner, connection, version), blob,
                                builtinCatalog()));
  }

  async sealDoorbell(owner: string, subscription: string, version: number,
                     input: unknown): Promise<Sealed> {
    return guarded(() => sealDoorbell(this.env, owner, subscription, version, input));
  }

  async verifyDoorbell(owner: string, subscription: string, version: number,
                       input: unknown): Promise<DoorbellVerified> {
    return guarded(() => verifyDoorbell(this.env, owner, subscription, version, input));
  }
}

/**
 * What the egress door binds, as the named entrypoint `EgressGateway`: `forward` and
 * nothing else. The caller names the owner, connection and version the owner's object
 * admitted the request under and hands the blob the object returned; the seal holds the
 * call to them, and the sealed endpoints to where it may go.
 */
export class EgressGateway extends WorkerEntrypoint<GatewayEnv> implements EgressService {
  async forward(owner: string, connection: string, version: number, blob: string,
                request: Request, reserve: Reserve | null, stop: ReadableStream<Uint8Array>):
      Promise<{ response: Response; outcome: ReadableStream<Uint8Array> }> {
    let sent: { response: Response; outcome: Promise<MeterOutcome> };
    try {
      const bound = binding(owner, connection, version);
      sent = reserve === null
        ? await forwardRead(this.env, bound, blob, request, stop)
        : await forwardMetered(this.env, bound, blob, request, reserve, stop,
                               builtinCatalog());
    } catch (error) {
      sent = { response: failureResponse(codeOf(error, "internal")),
               outcome: Promise.resolve({ state: "unknown" }) };
    }
    // A door that stopped reading the outcome (its client left) ends the write: nothing waits.
    const { readable, writable } = new IdentityTransformStream();
    const writer = writable.getWriter();
    void sent.outcome.then((outcome) => writer.write(new TextEncoder().encode(
      outcomeLine(outcome))).then(() => writer.close())).catch(() => undefined);
    return { response: sent.response, outcome: readable };
  }
}

/** What the owner's object binds (`GATEWAY`): its connections' upkeep and its wakes. */
export default class Gateway extends WorkerEntrypoint<GatewayEnv>
    implements GatewayService {
  /** No model call is served over HTTP (ADR-034 decision 13): egress is the model path. */
  override async fetch(): Promise<Response> {
    return failureResponse("not_found");
  }

  async refresh(owner: string, connection: string, version: number,
                blob: string): Promise<Refreshed> {
    return guarded(() => refresh(this.env, binding(owner, connection, version), blob));
  }

  async ringDoorbell(owner: string, subscription: string, version: number, blob: string,
                     previous: { readonly version: number; readonly blob: string } | null,
                     request: unknown): Promise<RingAnswer> {
    return guarded(() => ringDoorbell(this.env, owner, subscription, version, blob, previous,
                                      request));
  }

  async revokeAtProvider(owner: string, connection: string, version: number,
                         blob: string): Promise<Revoked> {
    return guarded(() => revokeAtProvider(this.env, binding(owner, connection, version), blob));
  }

  async credit(owner: string, connection: string, version: number, blob: string,
               read: unknown): Promise<KeyCredit> {
    return guarded(() => credit(this.env, binding(owner, connection, version), blob, read));
  }

  async models(owner: string, connection: string, version: number,
               blob: string): Promise<SealedModels> {
    return guarded(() => models(this.env, binding(owner, connection, version), blob,
                                builtinCatalog()));
  }

  async listing(owner: string, connection: string, version: number, blob: string,
                read: unknown): Promise<ModelListing> {
    return guarded(() => listing(this.env, binding(owner, connection, version), blob, read));
  }

  async reseal(owner: string, connection: string, version: number,
               blob: string): Promise<Resealed> {
    return guarded(async () => {
      const bound = binding(owner, connection, version);
      const { credential } = await open(this.env, bound, blob);
      return reseal(this.env, bound, credential);
    });
  }

  async resealDoorbell(owner: string, subscription: string, version: number,
                       blob: string): Promise<Resealed> {
    return guarded(() => resealDoorbell(this.env, owner, subscription, version, blob));
  }
}
