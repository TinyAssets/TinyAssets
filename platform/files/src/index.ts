/**
 * The files Worker (`tinyassets-files`): a stateless blob pipe for owner file bytes
 * (ADR-032, plan §6).
 *
 * It holds the platform's only R2 binding, `BLOBS`, one bucket of immutable chunks of at
 * most 4 MiB, each named by its SHA-256 under its owner's random namespace
 * (`o/<ns>/c/<sha256>`, `keys.ts`). It takes only hex values and bytes: it never sees a
 * path, never learns or chooses an owner, and keeps nothing between calls. Which owner's
 * namespace a call names, and whether a chunk is wanted at all, is the owner object's
 * manifest's to know (`owner/src/files.ts`); the namespace is held only there, so no
 * caller can name another owner's chunks without first being that owner's object.
 *
 * Each method takes a batch, because a request may make at most 32 service-binding calls
 * (Workers service bindings): one call moves up to `BATCH_BYTES` (two chunks), well under
 * the 32 MiB a serialized RPC may carry and the memory an isolate shares.
 *
 * - `putChunks` is create-once (`etagDoesNotMatch: "*"`) and passes each hash to R2's
 *   `put` `sha256` option, which verifies the bytes (a mismatch is R2's `BadDigest`,
 *   refused here as invalid). It relies on the binding's check, never on S3 checksum
 *   headers (R2's S3 API lists SHA-256 as a composite checksum only). An existing key is
 *   a no-op only when it holds exactly these bytes; anything else under a hash key fails
 *   loudly instead of counting as a deduplicated hit.
 * - `getChunks` reads chunks whole, rechecking each hash, or byte ranges of them.
 * - `deleteChunks` removes up to 1,000 chunks in one call (R2's batch limit); the owner
 *   object's garbage collection is its only caller, for keys in its own chunk table.
 * - `deleteNamespace` deletes every object under one owner's namespace prefix, listing it
 *   1,000 keys at a time: the owner's erasure alone, so a chunk whose row was lost, or
 *   whose put landed late, goes with the rest. It is the only list of the bucket, and only
 *   ever of one namespace's prefix.
 *
 * Every chunk is put and read under its owner's data key (SSE-C, R2's `ssecKey`;
 * `datakey.ts`): each call presents the owner's wrapped key, which this Worker opens with
 * its own keyring (`FILES_KEYRING`) for that call alone and never keeps. A put or get with
 * no key, or a key that does not open for the namespace, is refused before R2 is reached.
 * A delete needs none: it reads no bytes. So the bytes of an erased owner, whose wrapped
 * key went with their object, are unreadable wherever a chunk outlived the prefix delete.
 *
 * The nightly manifest (`owner/src/backup.ts`) is the only other kind of object: its parts
 * (`o/<ns>/m/<day>/<n>`), under the same data key, and its root (`o/<ns>/m/<day>`), a small
 * JSON naming the parts' hashes and carrying the owner's wrapped key, so a recovery needs
 * no object state but the keyring. The root is written last, after every part.
 *
 * It serves no HTTP: no fetch handler, no `workers.dev` or preview URL, no route. Owner
 * objects reach it by the `FILES` service binding alone, which adds no request fee. Owed
 * live (slice L): the probe of `ssecKey` with `onlyIf` and `sha256` on a put, and of a
 * get with another key or none on an SSE-C object (miniflare ignores `ssecKey`, so the
 * tests hold this Worker to R2's documented answers through `test/ssec.ts`).
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { type Cipher, cipherOf, open, seal } from "./cipher";
import { type KeyringSecret, mint, unwrap } from "./datakey";
import {
  BATCH_BYTES, CHUNK_BYTES, chunkHash, chunkKey, chunkPrefix, DELETE_BATCH, Invalid, listedChunk,
  listedManifest, MANIFEST_PARTS, manifestDay, manifestKey, manifestPart, manifestPrefix,
  namespace,
} from "./keys";
/** How a binding reports R2's `BadDigest`: the code, or its message. */
const BAD_DIGEST = /\(10037\)|did not match what we received/;

export interface FilesEnv {
  readonly BLOBS: R2Bucket;
  /** The data keys' keyring, a Worker secret read only by `datakey.ts`. */
  readonly FILES_KEYRING?: KeyringSecret;
  /** How the data key encrypts (`cipher.ts`): `ssec` (the default) or `aes-gcm`. */
  readonly FILES_CIPHER?: string;
}

/** A nightly manifest's root, as `putManifestRoot` writes it and a recovery reads it. */
export interface ManifestRoot {
  readonly format: "tinyassets-manifest";
  readonly version: 1;
  readonly day: string;
  /** The owner's wrapped data key, which the parts are under. */
  readonly key: string;
  /** Each part's SHA-256 and size, in order. */
  readonly parts: readonly { readonly sha256: string; readonly size: number }[];
  readonly at: number;
}

/** One chunk to store: its hash and its bytes. */
export interface ChunkPut {
  readonly sha256: string;
  readonly bytes: Uint8Array;
}

/** One chunk to read: whole, or `length` bytes from `offset`. */
export interface ChunkGet {
  readonly sha256: string;
  readonly offset?: number;
  readonly length?: number;
}

function hex(buffer: ArrayBuffer | undefined): string | null {
  return buffer === undefined ? null : [...new Uint8Array(buffer)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", bytes)) as string;
}

function list<T>(value: unknown, what: string): T[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Invalid(`${what} is a list of at least one chunk`);
  }
  return value as T[];
}

function chunkBytes(bytes: unknown): Uint8Array {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength === 0
      || bytes.byteLength > CHUNK_BYTES) {
    throw new Invalid(`a chunk is 1 to ${CHUNK_BYTES} bytes`);
  }
  return bytes;
}

/** A read's range, or none for the whole chunk, and the bytes it may move. */
function range(read: ChunkGet): { range?: { offset: number; length: number }; budget: number } {
  const { offset, length } = read;
  if (offset === undefined && length === undefined) {
    return { budget: CHUNK_BYTES };
  }
  if (typeof offset !== "number" || typeof length !== "number" || !Number.isSafeInteger(offset)
      || !Number.isSafeInteger(length) || offset < 0 || length < 1
      || offset + length > CHUNK_BYTES) {
    throw new Invalid("a chunk range is an offset and a length inside one chunk");
  }
  return { range: { offset, length }, budget: length };
}

export default class FilesService extends WorkerEntrypoint<FilesEnv> {
  /** A new data key for the namespace `ns`, wrapped: what its owner's object keeps. */
  async mintKey(ns: string): Promise<{ key: string }> {
    return { key: await mint(this.env.FILES_KEYRING, ns) };
  }

  /** The data key `key` holds for `ns`, refused unless it opens for that namespace. */
  #key(ns: string, key: unknown): Promise<ArrayBuffer> {
    return unwrap(this.env.FILES_KEYRING, namespace(ns), key);
  }

  #cipher(): Cipher {
    return cipherOf(this.env.FILES_CIPHER);
  }

  /**
   * What a put of `plain` to `objectKey` stores, and its options: the plain bytes under the
   * SSE-C key, R2 checking their hash; or the sealed bytes, R2 checking theirs.
   */
  async #stored(dataKey: ArrayBuffer, objectKey: string, plain: Uint8Array, sha256: string):
      Promise<{ body: Uint8Array; options: R2PutOptions }> {
    if (this.#cipher() === "ssec") {
      return { body: plain, options: { sha256, ssecKey: dataKey } };
    }
    const body = await seal(dataKey, objectKey, plain);
    return { body, options: { sha256: await sha256Hex(body) } };
  }

  /** A get's options: the SSE-C key and the range; sealed objects are read whole. */
  #reading(dataKey: ArrayBuffer, at?: { offset: number; length: number }): R2GetOptions {
    if (this.#cipher() === "ssec") {
      return at === undefined ? { ssecKey: dataKey } : { ssecKey: dataKey, range: at };
    }
    return {};
  }

  /** The plain bytes a get answered: as they came, or opened and sliced to the range. */
  async #plain(dataKey: ArrayBuffer, objectKey: string, object: R2ObjectBody,
               at?: { offset: number; length: number }): Promise<Uint8Array> {
    const bytes = new Uint8Array(await object.arrayBuffer());
    if (this.#cipher() === "ssec") {
      return bytes;
    }
    const plain = await open(dataKey, objectKey, bytes);
    return at === undefined ? plain : plain.slice(at.offset, at.offset + at.length);
  }

  /** Store chunks under their hashes, each verified by R2; `created` counts the new ones. */
  async putChunks(ns: string, key: string, chunks: ChunkPut[]): Promise<{ created: number }> {
    namespace(ns);
    const puts = list<ChunkPut>(chunks, "a put").map((chunk) => {
      if (typeof chunk !== "object" || chunk === null) {
        throw new Invalid("a put is a hash and its bytes");
      }
      return { sha256: chunkHash(chunk.sha256), bytes: chunkBytes(chunk.bytes) };
    });
    if (new Set(puts.map((put) => put.sha256)).size !== puts.length
        || puts.reduce((sum, put) => sum + put.bytes.byteLength, 0) > BATCH_BYTES) {
      throw new Invalid(`a put names distinct chunks of at most ${BATCH_BYTES} bytes in all`);
    }
    const dataKey = await this.#key(ns, key);
    const created = await Promise.all(puts.map((put) => this.#put(ns, dataKey, put.sha256,
                                                                   put.bytes)));
    return { created: created.filter(Boolean).length };
  }

  async #put(ns: string, dataKey: ArrayBuffer, sha256: string, bytes: Uint8Array):
      Promise<boolean> {
    let put: R2Object | null;
    if (this.#cipher() === "aes-gcm" && await sha256Hex(bytes) !== sha256) {
      throw new Invalid("the chunk's bytes do not match its sha256");  // R2 sees sealed bytes
    }
    const stored = await this.#stored(dataKey, chunkKey(ns, sha256), bytes, sha256);
    try {
      put = await this.env.BLOBS.put(chunkKey(ns, sha256), stored.body,
                                     { ...stored.options, onlyIf: { etagDoesNotMatch: "*" } });
    } catch (error) {
      if (BAD_DIGEST.test(String((error as Error)?.message))) {
        throw new Invalid("the chunk's bytes do not match its sha256");
      }
      throw error;
    }
    if (put !== null) {
      return true;
    }
    // The key exists. Every put here is verified, so it holds these bytes, unless
    // something wrote around this Worker: that fails loudly, never as a dedup hit. It is
    // read back under the owner's key (what a keyless `head` of an SSE-C object answers is
    // unprobed), so bytes put under another key, or none, fail here too.
    if (await sha256Hex(bytes) !== sha256) {
      throw new Invalid("the chunk's bytes do not match its sha256");
    }
    let held: Uint8Array;
    try {
      const object = await this.env.BLOBS.get(chunkKey(ns, sha256), this.#reading(dataKey));
      held = object === null ? new Uint8Array(0)
        : await this.#plain(dataKey, chunkKey(ns, sha256), object);
    } catch {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    if (held.byteLength !== bytes.byteLength || await sha256Hex(held) !== sha256) {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    return false;
  }

  /** Chunks' bytes, in order: each whole (its hash rechecked) or a range of it. */
  async getChunks(ns: string, key: string, reads: ChunkGet[]): Promise<Uint8Array[]> {
    namespace(ns);
    const gets = list<ChunkGet>(reads, "a get").map((read) => {
      if (typeof read !== "object" || read === null) {
        throw new Invalid("a get is a hash and an optional range");
      }
      return { sha256: chunkHash(read.sha256), ...range(read) };
    });
    if (gets.reduce((sum, get) => sum + get.budget, 0) > BATCH_BYTES) {
      throw new Invalid(`a get moves at most ${BATCH_BYTES} bytes`);
    }
    const dataKey = await this.#key(ns, key);
    return Promise.all(gets.map(async (get) => {
      const object = await this.env.BLOBS.get(chunkKey(ns, get.sha256),
                                              this.#reading(dataKey, get.range));
      if (object === null) {
        throw new Error("a chunk the manifest holds is missing from the bucket");
      }
      const bytes = await this.#plain(dataKey, chunkKey(ns, get.sha256), object, get.range);
      if (get.range === undefined && await sha256Hex(bytes) !== get.sha256) {
        throw new Error("a chunk's bytes no longer match its hash");
      }
      return bytes;
    }));
  }

  /**
   * Delete every chunk and then every manifest object under `ns`'s prefixes, listed 1,000 at
   * a time; repeatable.
   */
  async deleteNamespace(ns: string): Promise<{ deleted: number }> {
    namespace(ns);
    let deleted = 0;
    for (;;) {
      const listed = await this.env.BLOBS.list({ prefix: chunkPrefix(ns), limit: DELETE_BATCH });
      const shas = listed.objects.map((object) => listedChunk(ns, object.key));
      if (shas.length === 0) {
        break;
      }
      await this.env.BLOBS.delete(shas.map((sha) => chunkKey(ns, sha)));
      deleted += shas.length;
    }
    for (;;) {
      const listed = await this.env.BLOBS.list({ prefix: manifestPrefix(ns),
                                                 limit: DELETE_BATCH });
      const keys = listed.objects.map((object) => object.key);
      if (keys.length === 0) {
        return { deleted };
      }
      await this.env.BLOBS.delete(keys.map((key) => listedManifest(ns, key)));
      deleted += keys.length;
    }
  }

  /** Delete chunks (at most 1,000); a key already gone is not an error. */
  async deleteChunks(ns: string, sha256s: string[]): Promise<{ deleted: number }> {
    namespace(ns);
    if (!Array.isArray(sha256s) || sha256s.length === 0 || sha256s.length > DELETE_BATCH
        || new Set(sha256s).size !== sha256s.length) {
      throw new Invalid(`a delete names 1 to ${DELETE_BATCH} distinct chunks`);
    }
    await this.env.BLOBS.delete(sha256s.map((sha256) => chunkKey(ns, chunkHash(sha256))));
    return { deleted: sha256s.length };
  }

  /**
   * One part of the owner's nightly manifest of `day`, under the owner's key, verified by
   * R2. A part is written again when a pass that wrote it did not record it.
   */
  async putManifestPart(ns: string, key: string, day: string, part: number,
                        bytes: Uint8Array): Promise<{ sha256: string; size: number }> {
    const body = chunkBytes(bytes);
    const sha256 = await sha256Hex(body);
    const dataKey = await this.#key(ns, key);
    const stored = await this.#stored(dataKey, manifestKey(ns, day, part), body, sha256);
    await this.env.BLOBS.put(manifestKey(ns, day, part), stored.body, stored.options);
    return { sha256, size: body.byteLength };
  }

  /**
   * The manifest's root, written once every part is: the parts' hashes and sizes in order,
   * and the owner's wrapped key, so a recovery needs only the keyring. It is plain JSON:
   * the key in it opens nothing without the keyring.
   */
  async putManifestRoot(ns: string, key: string, day: string,
                        parts: readonly { sha256: string; size: number }[]): Promise<void> {
    await this.#key(ns, key);
    if (!Array.isArray(parts) || parts.length === 0 || parts.length > MANIFEST_PARTS
        || parts.some((part) => typeof part !== "object" || part === null
                      || Object.keys(part).sort().join() !== "sha256,size"
                      || typeof part.size !== "number" || !Number.isSafeInteger(part.size)
                      || part.size < 1 || part.size > CHUNK_BYTES)) {
      throw new Invalid(`a manifest has 1 to ${MANIFEST_PARTS} parts`);
    }
    const root: ManifestRoot = {
      format: "tinyassets-manifest", version: 1, day: manifestDay(day), key,
      parts: parts.map((part) => ({ sha256: chunkHash(part.sha256), size: part.size })),
      at: Date.now(),
    };
    await this.env.BLOBS.put(manifestKey(ns, day), JSON.stringify(root));
  }

  /** A manifest's root, or null when there is none. */
  async getManifestRoot(ns: string, day: string): Promise<ManifestRoot | null> {
    const object = await this.env.BLOBS.get(manifestKey(ns, day));
    return object === null ? null : await object.json<ManifestRoot>();
  }

  /** One manifest part's bytes, under the key `key` (the object's, or the root's), checked. */
  async getManifestPart(ns: string, key: string, day: string, part: number,
                        sha256: string): Promise<Uint8Array> {
    const dataKey = await this.#key(ns, key);
    const object = await this.env.BLOBS.get(manifestKey(ns, day, part), this.#reading(dataKey));
    if (object === null) {
      throw new Error("a manifest part its root names is missing from the bucket");
    }
    const bytes = await this.#plain(dataKey, manifestKey(ns, day, part), object);
    if (await sha256Hex(bytes) !== chunkHash(sha256)) {
      throw new Error("a manifest part's bytes no longer match its hash");
    }
    return bytes;
  }

  /** A manifest deleted (`parts` of them and its root): the root first, so none dangles. */
  async deleteManifest(ns: string, day: string, parts: number): Promise<void> {
    if (parts !== 0) {
      manifestPart(parts - 1);
    }
    await this.env.BLOBS.delete(manifestKey(ns, day));
    const all = Array.from({ length: parts }, (_, part) => part);
    for (let at = 0; at < all.length; at += DELETE_BATCH) {
      await this.env.BLOBS.delete(all.slice(at, at + DELETE_BATCH)
        .map((part) => manifestKey(ns, day, part)));
    }
  }
}
