/**
 * The model gateway Worker (ADR-032 Decision 3; research §12 G1). Not deployed: its
 * config has no routes and no URL, so only the service bindings of the owner Worker and
 * the doors Worker (the binder) reach it, and it binds nothing, so it reaches no object.
 *
 * - `fetch`: `POST /v1/messages`, a model call in pi-ai's pi-messages protocol, from the
 *   owner object's `gateway` provider (`messages.ts`).
 * - `seal`: a pasted API key, sealed with its source record (§7.2 inline connect).
 * - `exchange`, `refresh`, `revokeAtProvider`: OAuth connections (`oauth.ts`).
 * - `verify`: the connection's setup check, and the models it can be asked for.
 * - `forward`: raw passthrough for sandbox egress (`forward.ts`), also on its own
 *   entrypoint for the egress door.
 * - `reseal`: a blob resealed under the active kid at the next version (the sweep that
 *   retires a kid).
 *
 * The owner Worker binds the default entrypoint. The doors Worker binds `ConnectGateway`,
 * which serves only `seal`, `exchange` and `verify`, the steps of a connect, and
 * `EgressGateway`, which serves only `forward`: the egress door (the binder) streams a
 * sandbox's request through it once the owner's object admitted the request, so the
 * object sleeps while the bytes flow (research §5.4 step 4). Neither doors entrypoint can
 * make a model call, refresh, reseal or revoke. The owner's keeps `forward` for its own MCP
 * client (tasks 4 M, over G4's forward).
 *
 * Every method takes the owner, connection and version the blob is bound to from its
 * caller, and the seal holds the call to them: the gateway never chooses an owner. Each
 * refusal is a fixed code (`failure.ts`), and nothing the gateway catches is rethrown as
 * it was.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { builtinCatalog } from "./catalog";
import { type Credential, secret, source } from "./credential";
import { codeOf, Failure, failureResponse, guarded } from "./failure";
import { forward, forwardMetered, forwardRead, type MeterOutcome, type Metering,
         type Verified, verify } from "./forward";
import { binding, type KeyringEnv, open, reseal, type Resealed, seal, type Sealed } from "./keyring";
import { messages } from "./messages";
import { type Exchanged, exchange, type Refreshed, refresh, type Revoked,
         revokeAtProvider } from "./oauth";

/** The gateway's one binding: its keyring secret (`wrangler.jsonc`). */
export type GatewayEnv = KeyringEnv;

function sealKey(env: GatewayEnv, owner: string, connection: string, version: number,
                 key: string, record: unknown): Promise<Sealed> {
  return guarded(async () => {
    const bound = binding(owner, connection, version);
    const checked = source(record, false);
    const plaintext: Credential = { kind: "api_key", secret: secret(key),
                                    upstream: checked.upstream, endpoints: checked.endpoints,
                                    sealed_at: Date.now() };
    return seal(env, bound, plaintext);
  });
}

function exchangeCode(env: GatewayEnv, owner: string, connection: string, version: number,
                      grant: unknown, record: unknown): Promise<Exchanged> {
  return guarded(() => exchange(env, binding(owner, connection, version), grant, record));
}

function verifySealed(env: GatewayEnv, owner: string, connection: string, version: number,
                      blob: string): Promise<Verified> {
  return guarded(() => verify(env, binding(owner, connection, version), blob, builtinCatalog()));
}

/**
 * What the doors Worker (the binder) binds, as the named entrypoint `ConnectGateway`:
 * the three connect steps and nothing else. It cannot make a model call, forward
 * egress, refresh, reseal or revoke, and the keyring stays here (doors research §7.2,
 * `doors/guards/config.test.ts`).
 */
export class ConnectGateway extends WorkerEntrypoint<GatewayEnv> {
  /** Seals a pasted API key for the connection, with its source record's values. */
  async seal(owner: string, connection: string, version: number, key: string,
             record: unknown): Promise<Sealed> {
    return sealKey(this.env, owner, connection, version, key, record);
  }

  /** Exchanges an authorization code and seals what it buys. */
  async exchange(owner: string, connection: string, version: number, grant: unknown,
                 record: unknown): Promise<Exchanged> {
    return exchangeCode(this.env, owner, connection, version, grant, record);
  }

  /** Checks a sealed credential against its provider, and lists its models. */
  async verify(owner: string, connection: string, version: number,
               blob: string): Promise<Verified> {
    return verifySealed(this.env, owner, connection, version, blob);
  }
}

/**
 * What the egress door binds, as the named entrypoint `EgressGateway`: `forward` and
 * nothing else (G4). The caller names the owner, connection and version the owner's object
 * admitted the request under and hands the blob the object returned; the seal holds the
 * call to them, and the sealed endpoints to where it may go.
 */
export class EgressGateway extends WorkerEntrypoint<GatewayEnv> {
  /**
   * Forwards one sandbox call under the sealed endpoints: a model call metered
   * (`forwardMetered`), held to its reservation's output cap and body size, or, with no
   * `metering`, a read (`forwardRead`). Its answer streams back, and its outcome
   * (`MeterOutcome`, one JSON line) comes on `outcome` once the answer ended. `stop`,
   * closed or errored, aborts the provider's request.
   */
  async forward(owner: string, connection: string, version: number, blob: string,
                request: Request, metering: Metering | null, stop: ReadableStream<Uint8Array>):
      Promise<{ response: Response; outcome: ReadableStream<Uint8Array> }> {
    let sent: { response: Response; outcome: Promise<MeterOutcome> };
    try {
      const bound = binding(owner, connection, version);
      sent = metering === null
        ? await forwardRead(this.env, bound, blob, request, stop)
        : await forwardMetered(this.env, bound, blob, request, checkedMetering(metering), stop,
                               builtinCatalog());
    } catch (error) {
      sent = { response: failureResponse(codeOf(error, "internal")),
               outcome: Promise.resolve({ state: "unknown" }) };
    }
    const { readable, writable } = new IdentityTransformStream();
    const writer = writable.getWriter();
    void sent.outcome.then((outcome) => writer.write(new TextEncoder().encode(
      `${JSON.stringify(outcome)}\n`)).then(() => writer.close())).catch(() => undefined);
    return { response: sent.response, outcome: readable };
  }
}

/** The caller's metering, whole numbers, or `invalid_request`. */
function checkedMetering(value: unknown): Metering {
  const fields = value as Partial<Metering> | null;
  const whole = (n: unknown) => typeof n === "number" && Number.isSafeInteger(n) && n >= 1;
  if (typeof value !== "object" || value === null
      || Object.keys(value).sort().join() !== "input_bytes,max_output_tokens"
      || !whole(fields!.input_bytes) || !whole(fields!.max_output_tokens)) {
    throw new Failure("invalid_request");
  }
  return { input_bytes: fields!.input_bytes!, max_output_tokens: fields!.max_output_tokens! };
}

export default class Gateway extends WorkerEntrypoint<GatewayEnv> {
  override async fetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === "/v1/messages") {
      return messages(request, this.env, builtinCatalog());
    }
    return failureResponse("not_found");
  }

  /** Seals a pasted API key for the connection, with its source record's values. */
  async seal(owner: string, connection: string, version: number, key: string,
             record: unknown): Promise<Sealed> {
    return sealKey(this.env, owner, connection, version, key, record);
  }

  /** Exchanges an authorization code and seals what it buys. */
  async exchange(owner: string, connection: string, version: number, grant: unknown,
                 record: unknown): Promise<Exchanged> {
    return exchangeCode(this.env, owner, connection, version, grant, record);
  }

  /** Refreshes an OAuth credential; the result is bound to `version + 1`. */
  async refresh(owner: string, connection: string, version: number,
                blob: string): Promise<Refreshed> {
    return guarded(() => refresh(this.env, binding(owner, connection, version), blob,
                                 builtinCatalog()));
  }

  /** Checks a sealed credential against its provider, and lists its models. */
  async verify(owner: string, connection: string, version: number,
               blob: string): Promise<Verified> {
    return verifySealed(this.env, owner, connection, version, blob);
  }

  /** Forwards one request under the sealed endpoints, for the owner's own MCP client (M). */
  async forward(owner: string, connection: string, version: number, blob: string,
                request: Request): Promise<Response> {
    try {
      return await forward(this.env, binding(owner, connection, version), blob, request);
    } catch (error) {
      return failureResponse(codeOf(error, "internal"));
    }
  }

  /** Asks the provider to revoke an OAuth credential (RFC 7009). */
  async revokeAtProvider(owner: string, connection: string, version: number,
                         blob: string): Promise<Revoked> {
    return guarded(() => revokeAtProvider(this.env, binding(owner, connection, version), blob));
  }

  /** Reseals a blob under the active kid, bound to `version + 1`. */
  async reseal(owner: string, connection: string, version: number,
               blob: string): Promise<Resealed> {
    return guarded(async () => {
      const bound = binding(owner, connection, version);
      const { credential } = await open(this.env, bound, blob);
      return reseal(this.env, bound, credential);
    });
  }
}
