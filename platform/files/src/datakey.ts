/**
 * Each owner's data key: the key every object of theirs is sealed under, in this Worker, by
 * the envelope's per-object seal (`contract/src/envelope.ts` `sealObject`).
 *
 * `FILES_KEYRING` is a Worker secret on this Worker alone, and this module is the only code
 * that reads it (`guards/r2-keys.test.ts`). It is a ring in the sealed envelope's versioned
 * form (`contract/src/envelope.ts`), as the gateway's is,
 * `{"active":"k2","keys":{"k1":"<base64 of 32 bytes>",...}}`, but is a secret of its own,
 * so neither Worker's ring opens the other's blobs. A missing or malformed ring, or an
 * active kid it does not hold, fails every call loudly: there is no default key and no
 * keyless mode.
 *
 * Envelope (NIST SP 800-57 §6.1.1's key-wrapping key and data key):
 * - the **data key** is 32 random bytes, made by `mint` for one namespace, and never
 *   stored in plaintext anywhere: this Worker holds it for one call alone, and R2 sees only
 *   what it sealed;
 * - it is kept **wrapped**, in the owner's object (`file_space.data_key`), sealed in the
 *   envelope under the namespace's own wrapping key,
 *   HKDF-SHA256(IKM = ring[kid], salt = "tinyassets/files/v1", info = "dek\0" + ns),
 *   with additional data binding the blob to its namespace and kid:
 *
 *       aad  = "ta-files-key/v1\0" + ns + "\0" + kid
 *
 * So the owner's object holds no key it could use, the ring alone opens nothing (it needs
 * the owner's wrapped key), and a blob presented with another namespace fails its tag.
 *
 * **Crypto-erasure.** The erasure wipes the owner's object, and with it the only wrapped
 * copy the object kept; it deletes the owner's nightly manifests (which carry one, for
 * recovery: `owner/src/backup.ts`) first. A chunk that outlives the prefix delete (a put
 * in flight past it) is then unreadable: no one holds the key it was sealed under.
 *
 * **Rotation** (`rewrap`): `unwrap` accepts every kid in the ring, and `rewrap` opens a wrapped
 * key under its kid and wraps the same data key under the active one (the data key never
 * changes, so no chunk or part is resealed). Each owner's sweep re-wraps its object's copy
 * and every manifest root's (`owner/src/files.ts` `rewrap`); a kid leaves the ring only
 * after every owner's sweep has run (tasks.md).
 *
 * **Names.** A chunk's R2 name is HMAC-SHA256 of its hash under a key derived from the data
 * key (`chunkName`), so listing the bucket cannot confirm that an owner holds a known file;
 * the owner's object keeps the plain hash, and only this Worker, holding the data key for a
 * call, turns one into the other.
 */
import {
  blobMax, type Domain, hex, KEY_BYTES, openBlob, type Opened, parseRing, type Ring, sealBlob,
} from "../../contract/src/envelope";
import { Refusal } from "../../contract/src/refusal";
import { namespace } from "./keys";

const SALT = "tinyassets/files/v1";

/** What every refusal of a ring says; it names no kid and no part of it. */
export const KEYRING_UNAVAILABLE = "files_keyring_unavailable";
/** What a wrapped key that does not open says: never which part failed. */
export const KEY_REFUSED = "data_key_refused";

/** The ring, read from the secret; anything short of a usable ring fails loudly. */
function ring(raw: unknown): Ring {
  const keys = parseRing(raw);
  if (keys === null) {
    throw new Error(KEYRING_UNAVAILABLE);
  }
  return keys;
}

/** The namespace's own wrapping key, and the additional data binding the namespace. */
function domain(ns: string): Domain {
  return { salt: SALT, info: `dek\0${ns}`, context: `ta-files-key/v1\0${ns}` };
}

/** A new data key for `ns`, returned wrapped under the active kid: its only form outside. */
export async function mint(secret: unknown, ns: unknown): Promise<string> {
  const space = namespace(ns);
  const keys = ring(secret);
  const data = crypto.getRandomValues(new Uint8Array(KEY_BYTES));
  try {
    return await sealBlob(keys, domain(space), data);
  } finally {
    data.fill(0);
  }
}

/**
 * The data key `wrapped` holds for `ns`, wrapped again under the active kid; `wrapped` as it
 * is when its kid is the active one already. Refused as `unwrap` refuses.
 */
export async function rewrap(secret: unknown, ns: unknown, wrapped: unknown):
    Promise<string> {
  const space = namespace(ns);
  const keys = ring(secret);
  const { plain, kid } = await opened(keys, space, wrapped);
  try {
    return kid === keys.active ? wrapped as string : await sealBlob(keys, domain(space), plain);
  } finally {
    plain.fill(0);
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
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", naming,
                                                     new TextEncoder().encode(sha256))));
}

/**
 * The data key `wrapped` holds for `ns`, as the per-object seal takes it. Any mismatch (another
 * namespace, a changed byte, a kid the ring no longer holds) is refused as `KEY_REFUSED`,
 * with no detail of which.
 */
export async function unwrap(secret: unknown, ns: string, wrapped: unknown):
    Promise<ArrayBuffer> {
  return (await opened(ring(secret), ns, wrapped)).plain.buffer;
}

/** `wrapped` opened for `ns`: a data key's 32 bytes, or `KEY_REFUSED`. */
async function opened(keys: Ring, ns: string, wrapped: unknown): Promise<Opened> {
  // A wrapped key's most characters: a blob over a data key's bytes.
  const open = await openBlob(keys, domain(ns), wrapped, blobMax(KEY_BYTES));
  if (open === null || open.plain.byteLength !== KEY_BYTES) {
    throw new Refusal("data_key_refused", KEY_REFUSED);
  }
  return open;
}
