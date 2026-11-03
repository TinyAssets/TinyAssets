/**
 * R2's SSE-C answers, held around miniflare's bucket, in tests only.
 *
 * Miniflare's R2 binding ignores `ssecKey` (its bucket worker never reads it), so on its
 * own a test could not tell a chunk put under its owner's key from one put under none.
 * This wraps the test's real bucket with what Cloudflare documents for SSE-C
 * (developers.cloudflare.com/r2/examples/ssec, read 2026-11-03): an object put with a key
 * is read only with that key; R2 keeps the key's MD5 to identify it, never the key. Here
 * the identifying digest is kept in the object's custom metadata; a get with another key,
 * or none, of an object put with one fails, as does a get with a key of an object put
 * without one. What live R2 answers in each case is slice L's probe.
 */
const TAG = "ssec-test-digest";

async function digest(key: ArrayBuffer | string | undefined): Promise<string | null> {
  if (key === undefined) {
    return null;
  }
  const bytes = typeof key === "string" ? new TextEncoder().encode(key) : new Uint8Array(key);
  if (bytes.byteLength !== 32) {
    throw new Error("an SSE-C key is 32 bytes");
  }
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Every SSE-C key a put or get carried, in order: what a test asserts was presented. */
export interface Presented {
  readonly op: "put" | "get";
  readonly key: string;
  readonly digest: string | null;
}

export function ssec(bucket: R2Bucket, seen: Presented[] = []): R2Bucket {
  const check = async (key: string, presented: ArrayBuffer | string | undefined) => {
    const head = await bucket.head(key);
    if (head === null) {
      return;
    }
    const held = head.customMetadata?.[TAG] ?? null;
    if (held !== await digest(presented)) {
      throw new Error("SSE-C: the key presented is not the object's");
    }
  };
  return new Proxy(bucket, {
    get(target, name) {
      if (name === "put") {
        return async (key: string, value: unknown, options: R2PutOptions = {}) => {
          const tag = await digest(options.ssecKey);
          seen.push({ op: "put", key, digest: tag });
          const { ssecKey: _key, ...rest } = options;
          return target.put(key, value as never, tag === null ? rest
            : { ...rest, customMetadata: { ...rest.customMetadata, [TAG]: tag } });
        };
      }
      if (name === "get") {
        return async (key: string, options: R2GetOptions = {}) => {
          seen.push({ op: "get", key, digest: await digest(options.ssecKey) });
          await check(key, options.ssecKey);
          const { ssecKey: _key, ...rest } = options;
          return target.get(key, rest);
        };
      }
      const value = Reflect.get(target, name);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}
