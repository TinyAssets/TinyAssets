/**
 * Each owner's data key: the SSE-C key every chunk of theirs is put and read under (R2's
 * `ssecKey` on `put` and `get`; developers.cloudflare.com/r2/examples/ssec and the Workers
 * API reference, read 2026-11-03).
 *
 * `FILES_KEYRING` is a Worker secret on this Worker alone, and this module is the only code
 * that reads it (`guards/r2-keys.test.ts`). It has the gateway keyring's versioned form,
 * `{"active":"k2","keys":{"k1":"<base64 of 32 bytes>",...}}`, but is a secret of its own,
 * so neither Worker's ring opens the other's blobs. A missing or malformed ring, or an
 * active kid it does not hold, fails every call loudly: there is no default key and no
 * keyless mode.
 *
 * Envelope (NIST SP 800-57 §6.1.1's key-wrapping key and data key):
 * - the **data key** is 32 random bytes, made by `mint` for one namespace, and never
 *   stored in plaintext anywhere: R2 receives it per request as the SSE-C key, and keeps
 *   only its MD5 (to identify it, not to decrypt);
 * - it is kept **wrapped**, in the owner's object (`file_space.data_key`), with
 *   AES-256-GCM under the namespace's own wrapping key,
 *   HKDF-SHA256(IKM = ring[kid], salt = "tinyassets/files/v1", info = "dek\0" + ns)
 *   (RFC 5869), a fresh 96-bit nonce per wrap, and additional data binding the blob to its
 *   namespace and kid:
 *
 *       aad  = "ta-files-key/v1\0" + ns + "\0" + kid
 *       blob = "v1." + kid + "." + b64url(nonce) + "." + b64url(ciphertext and tag)
 *
 * So the owner's object holds no key it could use, the ring alone opens nothing (it needs
 * the owner's wrapped key), and a blob presented with another namespace fails its tag.
 *
 * **Crypto-erasure.** The erasure wipes the owner's object, and with it the only wrapped
 * copy the object kept; it deletes the owner's nightly manifests (which carry one, for
 * recovery: `owner/src/backup.ts`) first. A chunk that outlives the prefix delete (a put
 * in flight past it) is then unreadable: no one holds the key it was put under, and R2
 * keeps none.
 *
 * **Rotation** (`rewrap`): `unwrap` accepts every kid in the ring, and `rewrap` opens a wrapped
 * key under its kid and wraps the same data key under the active one (the data key never
 * changes, so no chunk or part is rewritten). Each owner's sweep re-wraps its object's copy
 * and every manifest root's (`owner/src/files.ts` `rewrap`); a kid leaves the ring only
 * after every owner's sweep has run (tasks.md).
 *
 * **Names.** A chunk's R2 name is HMAC-SHA256 of its hash under a key derived from the data
 * key (`chunkName`), so listing the bucket cannot confirm that an owner holds a known file;
 * the owner's object keeps the plain hash, and only this Worker, holding the data key for a
 * call, turns one into the other.
 */
import { Invalid } from "../../contract/src/errors";
import { namespace } from "./keys";

/** The one secret this module reads. */
export type KeyringSecret = unknown;

const KID = /^[A-Za-z0-9_-]{1,32}$/;
const KEY = /^[A-Za-z0-9+/]{43}=$/;
const B64URL = /^[A-Za-z0-9_-]+$/;
const SALT = "tinyassets/files/v1";
const KEY_BYTES = 32;
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
const KEYRING_MAX = 5 * 1024;
/** The most kids a ring holds: far past any rotation schedule, under the secret's 5 KiB. */
export const KIDS_MAX = 64;
/** A wrapped key's most characters: "v1." + a kid + a nonce + 48 bytes, base64url. */
export const WRAPPED_MAX = 3 + 32 + 1 + 16 + 1 + 64;

/** What every refusal of a ring says; it names no kid and no part of it. */
export const KEYRING_UNAVAILABLE = "files_keyring_unavailable";
/** What a wrapped key that does not open says: never which part failed. */
export const KEY_REFUSED = "data_key_refused";

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
    throw new Invalid(KEY_REFUSED);
  }
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  return base64(padded + "=".repeat((4 - (padded.length % 4)) % 4));
}

/** The ring, read from the secret; anything short of a usable ring fails loudly. */
function ring(raw: KeyringSecret): Ring {
  if (typeof raw !== "string" || raw.length > KEYRING_MAX) {
    throw new Error(KEYRING_UNAVAILABLE);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(KEYRING_UNAVAILABLE);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)
      || Object.keys(parsed).sort().join() !== "active,keys") {
    throw new Error(KEYRING_UNAVAILABLE);
  }
  const { active, keys } = parsed as { active: unknown; keys: unknown };
  if (typeof keys !== "object" || keys === null || Array.isArray(keys)
      || Object.keys(keys).length > KIDS_MAX) {
    throw new Error(KEYRING_UNAVAILABLE);
  }
  const decoded = new Map<string, Uint8Array>();
  for (const [kid, key] of Object.entries(keys)) {
    if (!KID.test(kid) || typeof key !== "string" || !KEY.test(key)
        || base64(key).byteLength !== KEY_BYTES) {
      throw new Error(KEYRING_UNAVAILABLE);
    }
    decoded.set(kid, base64(key));
  }
  if (typeof active !== "string" || !decoded.has(active)) {
    throw new Error(KEYRING_UNAVAILABLE);
  }
  return { active, keys: decoded };
}

function aad(ns: string, kid: string): Uint8Array {
  return new TextEncoder().encode(`ta-files-key/v1\0${ns}\0${kid}`);
}

/** The namespace's wrapping key under the root `root`. */
async function wrapping(root: Uint8Array, ns: string): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", root, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode(SALT),
      info: new TextEncoder().encode(`dek\0${ns}`) },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

/** `data` wrapped for `ns` under the ring's active kid, with a fresh nonce. */
async function wrap(keys: Ring, ns: string, data: Uint8Array): Promise<string> {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: aad(ns, keys.active),
      tagLength: TAG_BYTES * 8 },
    await wrapping(keys.keys.get(keys.active)!, ns), data);
  return `v1.${keys.active}.${base64url(nonce)}.${base64url(new Uint8Array(sealed))}`;
}

/** A new data key for `ns`, returned wrapped under the active kid: its only form outside. */
export async function mint(secret: KeyringSecret, ns: unknown): Promise<string> {
  const space = namespace(ns);
  const keys = ring(secret);
  const data = crypto.getRandomValues(new Uint8Array(KEY_BYTES));
  try {
    return await wrap(keys, space, data);
  } finally {
    data.fill(0);
  }
}

/**
 * The data key `wrapped` holds for `ns`, wrapped again under the active kid; `wrapped` as it
 * is when its kid is the active one already. Refused as `unwrap` refuses.
 */
export async function rewrap(secret: KeyringSecret, ns: unknown, wrapped: unknown):
    Promise<string> {
  const space = namespace(ns);
  const keys = ring(secret);
  const data = new Uint8Array(await unwrap(secret, space, wrapped));
  try {
    return (wrapped as string).split(".")[1] === keys.active ? wrapped as string
      : await wrap(keys, space, data);
  } finally {
    data.fill(0);
  }
}

/**
 * A chunk's R2 name: HMAC-SHA256 of its hash under HKDF-SHA256(data key, salt, "name"), 64
 * lowercase hex. The same chunk of one owner has one name; another owner's differs.
 */
export async function chunkName(dataKey: ArrayBuffer, sha256: string): Promise<string> {
  const base = await crypto.subtle.importKey("raw", dataKey, "HKDF", false, ["deriveKey"]);
  const naming = await crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode(SALT),
      info: new TextEncoder().encode("name") },
    base, { name: "HMAC", hash: "SHA-256", length: 256 }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", naming,
                                                      new TextEncoder().encode(sha256)));
  return [...mac].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * The data key `wrapped` holds for `ns`, as R2's `ssecKey` takes it. Any mismatch (another
 * namespace, a changed byte, a kid the ring no longer holds) is refused as `KEY_REFUSED`,
 * with no detail of which.
 */
export async function unwrap(secret: KeyringSecret, ns: string, wrapped: unknown):
    Promise<ArrayBuffer> {
  const keys = ring(secret);
  if (typeof wrapped !== "string" || wrapped.length > WRAPPED_MAX) {
    throw new Invalid(KEY_REFUSED);
  }
  const parts = wrapped.split(".");
  const [version, kid, nonceText, sealedText] = parts;
  if (parts.length !== 4 || version !== "v1" || kid === undefined || !KID.test(kid)
      || nonceText === undefined || sealedText === undefined) {
    throw new Invalid(KEY_REFUSED);
  }
  const root = keys.keys.get(kid);
  const nonce = fromBase64url(nonceText);
  const sealed = fromBase64url(sealedText);
  if (root === undefined || nonce.byteLength !== NONCE_BYTES
      || sealed.byteLength !== KEY_BYTES + TAG_BYTES) {
    throw new Invalid(KEY_REFUSED);
  }
  try {
    return await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: nonce, additionalData: aad(ns, kid), tagLength: TAG_BYTES * 8 },
      await wrapping(root, ns), sealed);
  } catch {
    throw new Invalid(KEY_REFUSED);
  }
}
