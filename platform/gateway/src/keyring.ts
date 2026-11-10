/**
 * The gateway's one root keyring, and the credential seal (research §1.2, §1.3).
 *
 * `GATEWAY_KEYRING` is a Worker secret on this Worker alone, and this module is the only
 * code that reads it (guards/isolation.test.ts). It is a ring in the sealed envelope's
 * versioned form (`contract/src/envelope.ts`):
 * `{"active":"k2","keys":{"k1":"<base64 of 32 bytes>","k2":"..."}}`. A missing or
 * malformed ring, or an active kid it does not hold, fails every call loudly
 * (`keyring_unavailable`): there is no default key and no unsealed mode.
 *
 * Each credential is sealed in the envelope under a key for its owner alone,
 * HKDF-SHA256(IKM = ring[kid], salt = "tinyassets/gateway/v1", info = "cred\0" + owner),
 * and the additional data binds the blob to its owner, its connection, its version and
 * its kid, so a blob opened under any other one of them fails the tag, as does any changed
 * byte:
 *
 *     aad  = "ta-cred/v1\0" + owner + "\0" + connection + "\0" + version + "\0" + kid
 *
 * The gateway never chooses the owner: the caller names it (the owner object by its own
 * name, the binder from a verified credential), and the seal holds the caller to it.
 *
 * Rotation: `open` accepts every kid in the ring, and when a blob's kid is not the active
 * one it also returns the credential resealed under the active kid at the next version,
 * which the owner object stores by compare-and-swap. A kid leaves the ring only after a
 * sweep has resealed every blob under it.
 */
import { type Domain, openBlob, parseRing, type Ring, sealBlob } from "../../contract/src/envelope";
import { CONNECTION_BLOB_MAX } from "../../contract/src/limits";
import { principal, utf8Decoder } from "../../contract/src/text";

import { type Credential, credential } from "./credential";
import type { Resealed, Sealed } from "../../contract/src/gateway";
import { Refusal } from "../../contract/src/refusal";

/** The one secret this Worker holds, as its config declares it. */
export interface KeyringEnv {
  readonly GATEWAY_KEYRING?: unknown;
}

/** What a blob is bound to: the caller names all three, and the seal holds it to them. */
export interface Binding {
  readonly owner: string;
  readonly connection: string;
  readonly version: number;
}

export interface Opened {
  readonly credential: Credential;
  readonly kid: string;
  /** Set when the blob's kid is not the active one. */
  readonly resealed?: Resealed;
}

const CONNECTION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SALT = "tinyassets/gateway/v1";

/** The ring, read from the one secret; anything short of a usable ring is refused loudly. */
function ring(env: KeyringEnv): Ring {
  const keys = parseRing(env.GATEWAY_KEYRING);
  if (keys === null) {
    throw new Refusal("keyring_unavailable");
  }
  return keys;
}

/** The binding, checked: the owner grammar is the contract's (`text.ts`). */
export function binding(owner: unknown, connection: unknown, version: unknown): Binding {
  try {
    principal(owner);
  } catch {
    throw new Refusal("invalid_binding");
  }
  if (typeof connection !== "string" || !CONNECTION.test(connection)
      || typeof version !== "number" || !Number.isSafeInteger(version) || version < 1) {
    throw new Refusal("invalid_binding");
  }
  return Object.freeze({ owner: owner as string, connection, version });
}

/** The owner's own key, and the additional data binding all three fields. */
function domain(bound: Binding): Domain {
  return { salt: SALT, info: `cred\0${bound.owner}`,
           context: `ta-cred/v1\0${bound.owner}\0${bound.connection}\0${bound.version}` };
}

async function sealWith(keys: Ring, bound: Binding, plaintext: unknown): Promise<Sealed> {
  const blob = await sealBlob(keys, domain(bound),
                              new TextEncoder().encode(JSON.stringify(plaintext)));
  if (blob.length > CONNECTION_BLOB_MAX) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze({ blob, kid: keys.active });
}

/** `value` sealed for the next version of `bound`'s connection, under the active kid. */
async function resealWith(keys: Ring, bound: Binding, value: unknown): Promise<Resealed> {
  const next = Object.freeze({ ...bound, version: bound.version + 1 });
  return Object.freeze({ ...(await sealWith(keys, next, value)), version: next.version });
}

/** Seals `plaintext` for `bound` under the active kid. */
export async function seal(env: KeyringEnv, bound: Binding,
                           plaintext: Credential): Promise<Sealed> {
  return sealWith(ring(env), bound, credential(plaintext));
}

/**
 * Opens `blob` for `bound`. Any mismatch (owner, connection, version, kid), any changed
 * byte, a kid the ring no longer holds or a plaintext that no longer fits is refused as
 * `credential_refused`, with no detail of which.
 */
export async function open(env: KeyringEnv, bound: Binding, blob: unknown): Promise<Opened> {
  const keys = ring(env);
  const { parsed, kid } = await openWith(keys, bound, blob);
  const opened = credential(parsed);
  if (kid === keys.active) {
    return Object.freeze({ credential: opened, kid });
  }
  return Object.freeze({ credential: opened, kid, resealed: await resealWith(
    keys, bound, Object.freeze({ ...opened, sealed_at: Date.now() })) });
}

/** A blob's plaintext as JSON, and its kid; `credential_refused` for any mismatch. */
async function openWith(keys: Ring, bound: Binding, blob: unknown):
    Promise<{ parsed: unknown; kid: string }> {
  const opened = await openBlob(keys, domain(bound), blob, CONNECTION_BLOB_MAX);
  if (opened === null) {
    throw new Refusal("credential_refused");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(utf8Decoder("strip").decode(opened.plain));
  } catch {
    throw new Refusal("credential_refused");
  }
  return { parsed, kid: opened.kid };
}

/**
 * A secret that is not a model credential (a doorbell's, `doorbell.ts`), sealed the same
 * way: `check` is its own shape, applied before the seal and again at every open, and its
 * binding's connection names its own key space (`wake.<subscription>`), so neither kind of
 * blob ever opens as the other.
 */
export async function sealValue<T>(env: KeyringEnv, bound: Binding, value: unknown,
                                   check: (value: unknown) => T): Promise<Sealed> {
  return sealWith(ring(env), bound, check(value));
}

/** Opens a `sealValue` blob; resealed under the active kid at the next version, as `open`. */
export async function openValue<T>(env: KeyringEnv, bound: Binding, blob: unknown,
                                   check: (value: unknown) => T):
    Promise<{ readonly value: T; readonly kid: string; readonly resealed?: Resealed }> {
  const keys = ring(env);
  const { parsed, kid } = await openWith(keys, bound, blob);
  let value: T;
  try {
    value = check(parsed);
  } catch {
    throw new Refusal("credential_refused");
  }
  if (kid === keys.active) {
    return Object.freeze({ value, kid });
  }
  return Object.freeze({ value, kid, resealed: await resealWith(keys, bound, value) });
}

/**
 * Seals an opened credential for the next version of its connection, under the active
 * kid: the reseal after a refresh or an endpoint change, and the sweep that retires a kid.
 */
export async function reseal(env: KeyringEnv, bound: Binding,
                             plaintext: Credential): Promise<Resealed> {
  return resealWith(ring(env), bound,
                    Object.freeze({ ...credential(plaintext), sealed_at: Date.now() }));
}
