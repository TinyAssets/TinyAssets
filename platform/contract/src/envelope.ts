/**
 * The sealed envelope: the one scheme both keyrings seal by. The gateway's ring
 * (`GATEWAY_KEYRING`) seals credentials and doorbells (`gateway/src/keyring.ts`), and the
 * files Worker's (`FILES_KEYRING`) wraps data keys (`files/src/datakey.ts`); the deploy kit
 * makes and extends both rings with it (`deploy/deploy.ts` `keyring-new`, `keyring-add`).
 *
 * A **ring** is versioned JSON, `{"active":"k2","keys":{"k1":"<base64 of 32 bytes>",...}}`:
 * at most 5 KiB (a Worker secret's size) and 64 kids, each kid 1 to 32 of `[A-Za-z0-9_-]`,
 * each key 32 bytes in padded base64, and the active kid among them. Anything short of that
 * is no ring (`null`), which each binding refuses loudly in its own words: there is no
 * default key and no unsealed mode.
 *
 * A **domain** is one use of a ring: the HKDF salt and info its keys are derived with, and
 * the context its additional data binds. A blob is sealed with AES-256-GCM under
 * HKDF-SHA256(IKM = ring[kid], salt, info) (RFC 5869), with a fresh 96-bit random nonce
 * per seal (SP 800-38D §8.2.2) and a 128-bit tag, and its additional data binds the
 * context and the kid:
 *
 *     aad  = context + "\0" + kid
 *     blob = "v1." + kid + "." + b64url(nonce) + "." + b64url(ciphertext and tag)
 *
 * So a blob opened under another domain, another context or another kid fails its tag, as
 * does any changed byte. Rotation is the bindings': a blob opens under any kid its ring
 * holds, and each binding reseals one whose kid is not the active one.
 *
 * Pure: it takes the ring's text, and so the key bytes, as input, holds no state and uses
 * only WebCrypto and the standard text codecs. It imports nothing, so the deploy kit runs
 * it under Node's type stripping just as the Workers bundle it.
 */

/** A kid, as a ring names it and a blob carries it. */
export const KID = /^[A-Za-z0-9_-]{1,32}$/;
/** A key's bytes: AES-256's, and HKDF's input. */
export const KEY_BYTES = 32;

const KEY = /^[A-Za-z0-9+/]{43}=$/;
const B64URL = /^[A-Za-z0-9_-]+$/;
/** A kid `ringWith` numbers: `k` and digits. */
const NUMBERED = /^k([0-9]+)$/;
const VERSION = "v1";
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
const RING_MAX = 5 * 1024;
/** The most kids a ring holds: far past any rotation schedule, under the secret's 5 KiB. */
const KIDS_MAX = 64;

/** A ring, read: its active kid and each kid's key. */
export interface Ring {
  readonly active: string;
  readonly keys: ReadonlyMap<string, Uint8Array>;
}

/** One use of a ring: its keys' HKDF salt and info, and the context its blobs bind. */
export interface Domain {
  readonly salt: string;
  readonly info: string;
  readonly context: string;
}

/** A blob's parts, decoded. */
export interface BlobParts {
  readonly kid: string;
  readonly nonce: Uint8Array<ArrayBuffer>;
  readonly sealed: Uint8Array<ArrayBuffer>;
}

/** An opened blob: its plaintext and the kid it was under. */
export interface Opened {
  readonly plain: Uint8Array<ArrayBuffer>;
  readonly kid: string;
}

/** `bytes` in padded base64. */
export function base64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

/** The bytes of padded base64 `text`; throws on text that is not base64. */
export function fromBase64(text: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(text), (char) => char.charCodeAt(0));
}

/** `bytes` in unpadded base64url (RFC 4648 §5). */
export function base64url(bytes: Uint8Array): string {
  return base64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The bytes of unpadded base64url `text`, or null when it is not that. */
function fromBase64url(text: string): Uint8Array<ArrayBuffer> | null {
  if (!B64URL.test(text) || text.length % 4 === 1) {
    return null;
  }
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  return fromBase64(padded + "=".repeat((4 - (padded.length % 4)) % 4));
}

/** `bytes` as lowercase hex. */
export function hex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** The ring `raw` holds, or null when it holds anything short of a usable one. */
export function parseRing(raw: unknown): Ring | null {
  if (typeof raw !== "string" || raw.length > RING_MAX) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)
      || Object.keys(parsed).sort().join() !== "active,keys") {
    return null;
  }
  const { active, keys } = parsed as { active: unknown; keys: unknown };
  if (typeof keys !== "object" || keys === null || Array.isArray(keys)
      || Object.keys(keys).length > KIDS_MAX) {
    return null;
  }
  const decoded = new Map<string, Uint8Array>();
  for (const [kid, key] of Object.entries(keys)) {
    if (!KID.test(kid) || typeof key !== "string" || !KEY.test(key)) {
      return null;
    }
    const bytes = fromBase64(key);
    if (bytes.byteLength !== KEY_BYTES) {
      return null;
    }
    decoded.set(kid, bytes);
  }
  if (typeof active !== "string" || !decoded.has(active)) {
    return null;
  }
  return Object.freeze({ active, keys: decoded });
}

/** `ring` as its secret's text, which `parseRing` reads back; throws on a ring it would not. */
export function ringText(ring: Ring): string {
  const text = JSON.stringify({ active: ring.active, keys: Object.fromEntries(
    [...ring.keys].map(([kid, key]) => [kid, base64(key)])) });
  if (parseRing(text) === null) {
    throw new Error("a ring is at most 64 kids, each a 32-byte key, its active kid among them");
  }
  return text;
}

/**
 * `ring` with `key` added, active, under the kid after its highest `k<n>` (k1 for a ring
 * with none): a kid name is never used twice, so a retired key kept offline can always go
 * back under its own name.
 */
export function ringWith(ring: Ring, key: Uint8Array): Ring {
  let highest = 0n;
  for (const kid of ring.keys.keys()) {
    const n = NUMBERED.exec(kid)?.[1];
    if (n !== undefined && BigInt(n) > highest) {
      highest = BigInt(n);
    }
  }
  const active = `k${highest + 1n}`;
  return Object.freeze({ active, keys: new Map([...ring.keys, [active, key]]) });
}

function aad(domain: Domain, kid: string): Uint8Array {
  return new TextEncoder().encode(`${domain.context}\0${kid}`);
}

/** The domain's AES-256-GCM key under the root `root`. */
async function derived(root: Uint8Array, domain: Domain): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", root, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode(domain.salt),
      info: new TextEncoder().encode(domain.info) },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

/** `plain` sealed for `domain` under the ring's active kid, with a fresh nonce. */
export async function sealBlob(ring: Ring, domain: Domain, plain: Uint8Array): Promise<string> {
  const root = ring.keys.get(ring.active);
  if (root === undefined) {
    throw new Error("a ring's active kid is among its keys");
  }
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: aad(domain, ring.active),
      tagLength: TAG_BYTES * 8 },
    await derived(root, domain), plain);
  return `${VERSION}.${ring.active}.${base64url(nonce)}.${base64url(new Uint8Array(sealed))}`;
}

/** `blob`'s parts, or null when it is not a blob of at most `max` characters. */
export function parseBlob(blob: unknown, max: number): BlobParts | null {
  if (typeof blob !== "string" || blob.length > max) {
    return null;
  }
  const parts = blob.split(".");
  const [version, kid, nonceText, sealedText] = parts;
  if (parts.length !== 4 || version !== VERSION || kid === undefined || !KID.test(kid)
      || nonceText === undefined || sealedText === undefined) {
    return null;
  }
  const nonce = fromBase64url(nonceText);
  const sealed = fromBase64url(sealedText);
  if (nonce === null || sealed === null || nonce.byteLength !== NONCE_BYTES
      || sealed.byteLength < TAG_BYTES) {
    return null;
  }
  return Object.freeze({ kid, nonce, sealed });
}

/**
 * `blob` opened for `domain`, or null for any mismatch (another domain or context, a changed
 * byte, a kid the ring does not hold, a blob out of shape or over `max` characters), with
 * no detail of which.
 */
export async function openBlob(ring: Ring, domain: Domain, blob: unknown,
                               max: number): Promise<Opened | null> {
  const parts = parseBlob(blob, max);
  const root = parts === null ? undefined : ring.keys.get(parts.kid);
  if (parts === null || root === undefined) {
    return null;
  }
  try {
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: parts.nonce, additionalData: aad(domain, parts.kid),
        tagLength: TAG_BYTES * 8 },
      await derived(root, domain), parts.sealed);
    return Object.freeze({ plain: new Uint8Array(plain), kid: parts.kid });
  } catch {
    return null;
  }
}
