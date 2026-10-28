/**
 * The gateway's one root keyring, and the credential seal (research §1.2, §1.3).
 *
 * `GATEWAY_KEYRING` is a Worker secret on this Worker alone, and this module is the only
 * code that reads it (guards/isolation.test.ts). It is versioned JSON:
 * `{"active":"k2","keys":{"k1":"<base64 of 32 bytes>","k2":"..."}}`. A missing or
 * malformed ring, or an active kid it does not hold, fails every call loudly
 * (`keyring_unavailable`): there is no default key and no unsealed mode.
 *
 * Each credential is sealed with AES-256-GCM under a key for its owner alone,
 * HKDF-SHA256(IKM = ring[kid], salt = "tinyassets/gateway/v1", info = "cred\0" + owner)
 * (RFC 5869), with a fresh 96-bit random nonce per seal (SP 800-38D §8.2.2). The
 * additional data binds the blob to its owner, its connection, its version and its kid,
 * so a blob opened under any other one of them fails the tag, as does any changed byte:
 *
 *     aad  = "ta-cred/v1\0" + owner + "\0" + connection + "\0" + version + "\0" + kid
 *     blob = "v1." + kid + "." + b64url(nonce) + "." + b64url(ciphertext and tag)
 *
 * The gateway never chooses the owner: the caller names it (the owner object by its own
 * name, the binder from a verified credential), and the seal holds the caller to it.
 *
 * Rotation: `open` accepts every kid in the ring, and when a blob's kid is not the active
 * one it also returns the credential resealed under the active kid at the next version,
 * which the owner object stores by compare-and-swap. A kid leaves the ring only after a
 * sweep has resealed every blob under it.
 */
import { principal } from "../../owner/src/text";

import { type Credential, credential } from "./credential";
import { Failure } from "./failure";

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

/** A sealed blob and the kid it is under. */
export interface Sealed {
  readonly blob: string;
  readonly kid: string;
}

/** A credential resealed under the active kid, for the owner object to store. */
export interface Resealed extends Sealed {
  readonly version: number;
}

export interface Opened {
  readonly credential: Credential;
  readonly kid: string;
  /** Set when the blob's kid is not the active one. */
  readonly resealed?: Resealed;
}

const KID = /^[A-Za-z0-9_-]{1,32}$/;
const KEY = /^[A-Za-z0-9+/]{43}=$/;
const CONNECTION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const B64URL = /^[A-Za-z0-9_-]+$/;
const SALT = "tinyassets/gateway/v1";
const KEY_BYTES = 32;
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
const BLOB_MAX = 64 * 1024;
const KEYRING_MAX = 5 * 1024;
const KIDS_MAX = 64;

interface Ring {
  readonly active: string;
  readonly keys: ReadonlyMap<string, Uint8Array>;
}

function base64(text: string): Uint8Array {
  return Uint8Array.from(atob(text), (char) => char.charCodeAt(0));
}

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64url(text: string): Uint8Array {
  if (!B64URL.test(text) || text.length % 4 === 1) {
    throw new Failure("credential_refused");
  }
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  return base64(padded + "=".repeat((4 - (padded.length % 4)) % 4));
}

/** The ring, read from the one secret; anything short of a usable ring is refused loudly. */
function ring(env: KeyringEnv): Ring {
  const raw = env.GATEWAY_KEYRING;
  if (typeof raw !== "string" || raw.length > KEYRING_MAX) {
    throw new Failure("keyring_unavailable");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Failure("keyring_unavailable");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)
      || Object.keys(parsed).sort().join() !== "active,keys") {
    throw new Failure("keyring_unavailable");
  }
  const { active, keys } = parsed as { active: unknown; keys: unknown };
  if (typeof keys !== "object" || keys === null || Array.isArray(keys)) {
    throw new Failure("keyring_unavailable");
  }
  const entries = Object.entries(keys);
  if (entries.length > KIDS_MAX) {
    throw new Failure("keyring_unavailable");
  }
  const decoded = new Map<string, Uint8Array>();
  for (const [kid, key] of entries) {
    if (!KID.test(kid) || typeof key !== "string" || !KEY.test(key)) {
      throw new Failure("keyring_unavailable");
    }
    const bytes = base64(key);
    if (bytes.byteLength !== KEY_BYTES) {
      throw new Failure("keyring_unavailable");
    }
    decoded.set(kid, bytes);
  }
  if (typeof active !== "string" || !decoded.has(active)) {
    throw new Failure("keyring_unavailable");
  }
  return { active, keys: decoded };
}

/** The binding, checked: the owner grammar is the owner object's own (`text.ts`). */
export function binding(owner: unknown, connection: unknown, version: unknown): Binding {
  try {
    principal(owner);
  } catch {
    throw new Failure("invalid_binding");
  }
  if (typeof connection !== "string" || !CONNECTION.test(connection)
      || typeof version !== "number" || !Number.isSafeInteger(version) || version < 1) {
    throw new Failure("invalid_binding");
  }
  return Object.freeze({ owner: owner as string, connection, version });
}

function aad(bound: Binding, kid: string): Uint8Array {
  return new TextEncoder().encode(
    `ta-cred/v1\0${bound.owner}\0${bound.connection}\0${bound.version}\0${kid}`);
}

/** The owner's AES-256-GCM key under the root `root`. */
async function ownerKey(root: Uint8Array, owner: string): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", root, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode(SALT),
      info: new TextEncoder().encode(`cred\0${owner}`) },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

async function sealWith(keys: Ring, kid: string, bound: Binding,
                        plaintext: Credential): Promise<Sealed> {
  const root = keys.keys.get(kid);
  if (root === undefined) {
    throw new Failure("keyring_unavailable");
  }
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: aad(bound, kid), tagLength: TAG_BYTES * 8 },
    await ownerKey(root, bound.owner), new TextEncoder().encode(JSON.stringify(plaintext)));
  const blob = `v1.${kid}.${base64url(nonce)}.${base64url(new Uint8Array(sealed))}`;
  if (blob.length > BLOB_MAX) {
    throw new Failure("invalid_source");
  }
  return Object.freeze({ blob, kid });
}

/** Seals `plaintext` for `bound` under the active kid. */
export async function seal(env: KeyringEnv, bound: Binding,
                           plaintext: Credential): Promise<Sealed> {
  const keys = ring(env);
  return sealWith(keys, keys.active, bound, credential(plaintext));
}

/**
 * Opens `blob` for `bound`. Any mismatch (owner, connection, version, kid), any changed
 * byte, a kid the ring no longer holds or a plaintext that no longer fits is refused as
 * `credential_refused`, with no detail of which.
 */
export async function open(env: KeyringEnv, bound: Binding, blob: unknown): Promise<Opened> {
  const keys = ring(env);
  if (typeof blob !== "string" || blob.length > BLOB_MAX) {
    throw new Failure("credential_refused");
  }
  const parts = blob.split(".");
  const [version, kid, nonceText, sealedText] = parts;
  if (parts.length !== 4 || version !== "v1" || kid === undefined || !KID.test(kid)
      || nonceText === undefined || sealedText === undefined) {
    throw new Failure("credential_refused");
  }
  const root = keys.keys.get(kid);
  const nonce = fromBase64url(nonceText);
  const sealed = fromBase64url(sealedText);
  if (root === undefined || nonce.byteLength !== NONCE_BYTES || sealed.byteLength < TAG_BYTES) {
    throw new Failure("credential_refused");
  }
  let plain: ArrayBuffer;
  try {
    plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: nonce, additionalData: aad(bound, kid), tagLength: TAG_BYTES * 8 },
      await ownerKey(root, bound.owner), sealed);
  } catch {
    throw new Failure("credential_refused");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(plain));
  } catch {
    throw new Failure("credential_refused");
  }
  const opened = credential(parsed);
  if (kid === keys.active) {
    return Object.freeze({ credential: opened, kid });
  }
  return Object.freeze({ credential: opened, kid,
                         resealed: await resealWith(keys, bound, opened) });
}

async function resealWith(keys: Ring, bound: Binding, opened: Credential): Promise<Resealed> {
  const next = Object.freeze({ ...bound, version: bound.version + 1 });
  const sealed = await sealWith(keys, keys.active, next,
                                Object.freeze({ ...opened, sealed_at: Date.now() }));
  return Object.freeze({ ...sealed, version: next.version });
}

/**
 * Seals an opened credential for the next version of its connection, under the active
 * kid: the reseal after a refresh or an endpoint change, and the sweep that retires a kid.
 */
export async function reseal(env: KeyringEnv, bound: Binding,
                             plaintext: Credential): Promise<Resealed> {
  return resealWith(ring(env), bound, credential(plaintext));
}
