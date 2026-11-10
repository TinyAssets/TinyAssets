/**
 * How the owner's data key encrypts a stored object: R2's SSE-C (`ssec`), or, should slice
 * L's live probe find SSE-C does not combine with the create-once put or R2's `sha256`
 * check, AES-256-GCM in this Worker (`aes-gcm`). Both are built and tested, so the switch is
 * the config's `FILES_CIPHER` alone (required: the config declares it); objects are never
 * mixed (it is chosen before the first real byte, and a bucket holds one or the other).
 * Each is one `Cipher`, chosen once per call, so the Worker's paths never ask which.
 *
 * `aes-gcm`: a stored object is a fresh 96-bit nonce and the ciphertext with its 128-bit tag
 * (SP 800-38D §8.2.2), the object's own R2 key as additional data, so bytes moved to another
 * key, or another owner's namespace, fail their tag. R2's `sha256` then checks the stored
 * bytes in transit, and this Worker checks the plain bytes' hash itself, as it does on every
 * whole read. A range read fetches the whole object (at most 4 MiB) and slices it: GCM
 * authenticates the whole.
 */
import { hex } from "../../contract/src/envelope";
import { CHUNK_BYTES } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";

const NONCE_BYTES = 12;
const TAG_BITS = 128;

/** A byte range of one object. */
export interface Range {
  readonly offset: number;
  readonly length: number;
}

/** How a put stores an object and a get reads it back, under the owner's data key. */
export interface Cipher {
  /**
   * What a put of `plain` (whose hash is `sha256`) to `objectKey` stores, and its options,
   * so the bytes R2 receives are checked; refused when `plain` is not `sha256`'s bytes.
   */
  stored(dataKey: ArrayBuffer, objectKey: string, plain: Uint8Array, sha256: string):
    Promise<{ body: Uint8Array; options: R2PutOptions }>;
  /** A get's options, for the whole object or `at`. */
  reading(dataKey: ArrayBuffer, at?: Range): R2GetOptions;
  /** The plain bytes a get answered, the whole object's or `at`'s. */
  plain(dataKey: ArrayBuffer, objectKey: string, object: R2ObjectBody, at?: Range):
    Promise<Uint8Array>;
  /** The bytes a read of `length` bytes of a chunk pulls into the isolate. */
  rangeCost(length: number): number;
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
}

/** R2 holds the key and checks the plain bytes' hash, and slices a range itself. */
const SSEC: Cipher = Object.freeze({
  stored: async (dataKey: ArrayBuffer, _objectKey: string, plain: Uint8Array, sha256: string) =>
    ({ body: plain, options: { sha256, ssecKey: dataKey } }),
  reading: (dataKey: ArrayBuffer, at?: Range): R2GetOptions =>
    at === undefined ? { ssecKey: dataKey } : { ssecKey: dataKey, range: at },
  plain: async (_dataKey: ArrayBuffer, _objectKey: string, object: R2ObjectBody) =>
    new Uint8Array(await object.arrayBuffer()),
  rangeCost: (length: number) => length,
});

/** This Worker seals: it checks the plain hash itself (R2 sees sealed bytes), and reads whole. */
const AES_GCM: Cipher = Object.freeze({
  stored: async (dataKey: ArrayBuffer, objectKey: string, plain: Uint8Array, sha256: string) => {
    if (await sha256Hex(plain) !== sha256) {
      throw new Refusal("invalid", "the chunk's bytes do not match its sha256");
    }
    const body = await seal(dataKey, objectKey, plain);
    return { body, options: { sha256: await sha256Hex(body) } };
  },
  reading: (): R2GetOptions => ({}),
  plain: async (dataKey: ArrayBuffer, objectKey: string, object: R2ObjectBody, at?: Range) => {
    const plain = await open(dataKey, objectKey, new Uint8Array(await object.arrayBuffer()));
    return at === undefined ? plain : plain.slice(at.offset, at.offset + at.length);
  },
  rangeCost: () => CHUNK_BYTES,
});

/** The configured cipher; anything else, a missing one included, fails loudly. */
export function cipherOf(value: unknown): Cipher {
  if (value === "ssec") {
    return SSEC;
  }
  if (value === "aes-gcm") {
    return AES_GCM;
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
    throw new Refusal("invalid", "a stored object is shorter than its nonce and tag");
  }
  try {
    return new Uint8Array(await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: stored.subarray(0, NONCE_BYTES), additionalData: aad(objectKey),
        tagLength: TAG_BITS }, await aesKey(dataKey), stored.subarray(NONCE_BYTES)));
  } catch {
    throw new Error("a stored object does not open under its owner's key");
  }
}
