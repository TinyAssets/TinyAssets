/**
 * How the owner's data key encrypts a stored object: R2's SSE-C (`ssec`, the default), or,
 * should slice L's live probe find SSE-C does not combine with the create-once put or R2's
 * `sha256` check, AES-256-GCM in this Worker (`aes-gcm`). Both are built and tested, so the
 * switch is the config's `FILES_CIPHER` alone; objects are never mixed (it is chosen before
 * the first real byte, and a bucket holds one or the other).
 *
 * `aes-gcm`: a stored object is a fresh 96-bit nonce and the ciphertext with its 128-bit tag
 * (SP 800-38D §8.2.2), the object's own R2 key as additional data, so bytes moved to another
 * key, or another owner's namespace, fail their tag. R2's `sha256` then checks the stored
 * bytes in transit, and this Worker checks the plain bytes' hash itself, as it does on every
 * whole read. A range read fetches the whole object (at most 4 MiB) and slices it: GCM
 * authenticates the whole.
 */
import { Invalid } from "../../contract/src/errors";

export type Cipher = "ssec" | "aes-gcm";

const NONCE_BYTES = 12;
const TAG_BITS = 128;

/** The configured cipher; anything else fails loudly. */
export function cipherOf(value: unknown): Cipher {
  if (value === undefined || value === "ssec") {
    return "ssec";
  }
  if (value === "aes-gcm") {
    return "aes-gcm";
  }
  throw new Error("FILES_CIPHER is ssec or aes-gcm");
}

async function aesKey(dataKey: ArrayBuffer): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", dataKey, "AES-GCM", false, ["encrypt", "decrypt"]);
}

function aad(objectKey: string): Uint8Array {
  return new TextEncoder().encode(`ta-object/v1\0${objectKey}`);
}

/** `plain` sealed for the object at `objectKey`: nonce, then ciphertext and tag. */
export async function seal(dataKey: ArrayBuffer, objectKey: string, plain: Uint8Array):
    Promise<Uint8Array> {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const sealed = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: aad(objectKey), tagLength: TAG_BITS },
    await aesKey(dataKey), plain));
  const out = new Uint8Array(NONCE_BYTES + sealed.byteLength);
  out.set(nonce);
  out.set(sealed, NONCE_BYTES);
  return out;
}

/** The plain bytes of an object sealed for `objectKey`; any other key or byte fails loudly. */
export async function open(dataKey: ArrayBuffer, objectKey: string, stored: Uint8Array):
    Promise<Uint8Array> {
  if (stored.byteLength < NONCE_BYTES + TAG_BITS / 8) {
    throw new Invalid("a stored object is shorter than its nonce and tag");
  }
  try {
    return new Uint8Array(await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: stored.subarray(0, NONCE_BYTES), additionalData: aad(objectKey),
        tagLength: TAG_BITS }, await aesKey(dataKey), stored.subarray(NONCE_BYTES)));
  } catch {
    throw new Error("a stored object does not open under its owner's key");
  }
}
