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
 * - `forward`: raw passthrough for sandbox egress (`forward.ts`).
 * - `reseal`: a blob resealed under the active kid at the next version (the sweep that
 *   retires a kid).
 *
 * Every method takes the owner, connection and version the blob is bound to from its
 * caller, and the seal holds the call to them: the gateway never chooses an owner. Each
 * refusal is a fixed code (`failure.ts`), and nothing the gateway catches is rethrown as
 * it was.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { builtinCatalog } from "./catalog";
import { type Credential, secret, source } from "./credential";
import { codeOf, Failure, failureResponse } from "./failure";
import { forward, type Verified, verify } from "./forward";
import { binding, type KeyringEnv, open, reseal, type Resealed, seal, type Sealed } from "./keyring";
import { messages } from "./messages";
import { type Exchanged, exchange, type Refreshed, refresh, type Revoked,
         revokeAtProvider } from "./oauth";

/** The gateway's one binding: its keyring secret (`wrangler.jsonc`). */
export type GatewayEnv = KeyringEnv;

/** Runs an RPC method, letting only a fixed code out of it. */
async function guarded<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw new Failure(codeOf(error, "internal"));
  }
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
    return guarded(async () => {
      const bound = binding(owner, connection, version);
      const checked = source(record, false);
      const plaintext: Credential = { kind: "api_key", secret: secret(key),
                                      upstream: checked.upstream, endpoints: checked.endpoints,
                                      sealed_at: Date.now() };
      return seal(this.env, bound, plaintext);
    });
  }

  /** Exchanges an OAuth authorization code and seals the tokens. */
  async exchange(owner: string, connection: string, version: number, grant: unknown,
                 record: unknown): Promise<Exchanged> {
    return guarded(() => exchange(this.env, binding(owner, connection, version), grant, record));
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
    return guarded(() => verify(this.env, binding(owner, connection, version), blob,
                                builtinCatalog()));
  }

  /** Forwards one sandbox egress request under the sealed endpoints. */
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
