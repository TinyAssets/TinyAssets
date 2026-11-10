/**
 * The files Worker (`tinyassets-files`): a stateless blob pipe for owner file bytes
 * (ADR-032, plan §6).
 *
 * It holds the platform's only R2 binding, `BLOBS`, one bucket of immutable chunks of at
 * most 4 MiB, each under its owner's random namespace and named by an HMAC of its SHA-256
 * under the owner's data key (`o/<ns>/c/<name>`, `keys.ts`, `datakey.ts` `chunkName`), so a
 * listing of the bucket confirms no known file. It takes only hex values and bytes: it never sees a
 * path, never learns or chooses an owner, and keeps nothing between calls. Which owner's
 * namespace a call names, and whether a chunk is wanted at all, is the owner object's
 * manifest's to know (`owner/src/files.ts`); the namespace is held only there, so no
 * caller can name another owner's chunks without first being that owner's object.
 *
 * Each method takes a batch, so a large read or write is few calls: each call is a
 * subrequest of its caller's invocation, of which Workers Paid allows 10,000 by default
 * ("Subrequests to internal services: matches configured limit", default 10,000; up to
 * 10M with `limits.subrequests`, which the owner's config sets: Workers limits, updated
 * 2026-10-29). One call moves up to `BATCH_BYTES` (two chunks), well under the 32 MiB a
 * serialized RPC may carry.
 *
 * - `putChunks` is create-once (`etagDoesNotMatch: "*"`) and passes each hash to R2's
 *   `put` `sha256` option, which verifies the bytes (a mismatch is R2's `BadDigest`,
 *   refused here as invalid). It relies on the binding's check, never on S3 checksum
 *   headers (R2's S3 API lists SHA-256 as a composite checksum only). An existing key is
 *   a no-op only when it holds exactly these bytes; anything else under a hash key fails
 *   loudly instead of counting as a deduplicated hit.
 * - `getChunks` reads chunks whole, rechecking each hash, or byte ranges of them: a range
 *   read with `verify` reads its whole chunk, rechecks its hash and answers the range alone,
 *   so the caller holds only the range and still reads verified bytes.
 * - `deleteChunks` removes up to 1,000 chunks in one call (R2's batch limit), named by their
 *   hashes under the owner's key; the owner object's garbage collection is its only caller,
 *   for keys in its own chunk table.
 * - `deleteNamespace` deletes every object under one owner's namespace prefix, listing it
 *   1,000 keys at a time: the owner's erasure alone, so a chunk whose row was lost, or
 *   whose put landed late, goes with the rest. It is the only list of the bucket, and only
 *   ever of one namespace's prefix.
 *
 * Every chunk is put and read under its owner's data key (SSE-C, R2's `ssecKey`;
 * `datakey.ts`): each call presents the owner's wrapped key, which this Worker opens with
 * its own keyring (`FILES_KEYRING`) for that call alone and never keeps. A put or get with
 * no key, or a key that does not open for the namespace, is refused before R2 is reached.
 * A delete by hash presents it too (the key names the chunk); the prefix delete needs none.
 * So the bytes of an erased owner, whose wrapped key went with their object, are unreadable
 * wherever a chunk outlived the prefix delete, and their names say nothing.
 *
 * The nightly manifest (`owner/src/backup.ts`) is the only other kind of object: its parts
 * (`o/<ns>/m/<gen>/<n>`) and log segments (`o/<ns>/m/<gen>/l/<a>-<b>`), under the same data
 * key, and its root (`o/<ns>/m/<gen>`), a small
 * JSON carrying the owner's wrapped key, always under the ring's active kid, and the parts'
 * hashes and sizes sealed under the data key (AES-256-GCM, the root's own R2 key as
 * additional data), so a recovery needs no object state but the keyring, and the bucket
 * alone says nothing of the parts. The root is written last, after every part. A key's
 * rotation re-wraps it in place (`rewrapManifestRoot`): the data key is the same, so no part
 * and no sealed list changes.
 *
 * It serves no HTTP: no fetch handler, no `workers.dev` or preview URL, no route. Owner
 * objects reach it by the `FILES` service binding alone, which adds no request fee. Owed
 * live (slice L): the probe of `ssecKey` with `onlyIf` and `sha256` on a put, and of a
 * get with another key or none on an SSE-C object (miniflare ignores `ssecKey`, so the
 * tests hold this Worker to R2's documented answers through `test/ssec.ts`).
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { type Cipher, cipherOf, open, seal } from "./cipher";
import { chunkName, type KeyringSecret, mint, rewrap, unwrap } from "./datakey";
import { base64, fromBase64, hex } from "../../contract/src/envelope";
import { BATCH_BYTES, CHUNK_BYTES, DELETE_BATCH, MANIFEST_PARTS } from "../../contract/src/limits";
import {
  chunkHash, chunkKey, chunkPrefix, generation, listedChunk, listedManifest, type ManifestAt,
  manifestKey, manifestObjectKey, manifestPart, manifestPrefix, namespace, segmentKey, segmentSpan,
} from "./keys";
import { Refusal } from "../../contract/src/refusal";
import type {
  ChunkGet, ChunkPut, ChunkStore, ChunkUpload, ManifestObject, ManifestRoot, ManifestSegment,
} from "../../contract/src/files";
/** How a binding reports R2's `BadDigest`: the code, or its message. */
const BAD_DIGEST = /\(10037\)|did not match what we received/;

export interface FilesEnv {
  readonly BLOBS: R2Bucket;
  /** The data keys' keyring, a Worker secret read only by `datakey.ts`. */
  readonly FILES_KEYRING?: KeyringSecret;
  /** How the data key encrypts (`cipher.ts`): `ssec` (the default) or `aes-gcm`. */
  readonly FILES_CIPHER?: string;
}

/** A root as the bucket holds it: the wrapped key in the clear, the lists and span sealed. */
interface StoredRoot {
  readonly format: "tinyassets-manifest";
  readonly version: 2;
  readonly gen: string;
  readonly key: string;
  readonly sealed: string;
}

function storedRoot(text: string, gen: string): StoredRoot {
  let root: unknown;
  try {
    root = JSON.parse(text);
  } catch {
    throw new Error("a manifest root is out of shape");
  }
  const r = root as Record<string, unknown>;
  if (typeof root !== "object" || root === null
      || Object.keys(root).sort().join() !== "format,gen,key,sealed,version"
      || r["format"] !== "tinyassets-manifest" || r["version"] !== 2 || r["gen"] !== gen
      || typeof r["key"] !== "string" || typeof r["sealed"] !== "string") {
    throw new Error("a manifest root is out of shape");
  }
  return root as StoredRoot;
}

function rootBytes(text: string): Uint8Array {
  try {
    return fromBase64(text);
  } catch {
    throw new Error("a manifest root is out of shape");
  }
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
}

function list<T>(value: unknown, what: string): T[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Refusal("invalid", `${what} is a list of at least one chunk`);
  }
  return value as T[];
}

function chunkBytes(bytes: unknown): Uint8Array {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength === 0
      || bytes.byteLength > CHUNK_BYTES) {
    throw new Refusal("invalid", `a chunk is 1 to ${CHUNK_BYTES} bytes`);
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
    throw new Refusal("invalid", "a chunk range is an offset and a length inside one chunk");
  }
  return { range: { offset, length }, budget: length };
}

export default class FilesService extends WorkerEntrypoint<FilesEnv> implements ChunkStore {
  /** A new data key for the namespace `ns`, wrapped: what its owner's object keeps. */
  async mintKey(ns: string): Promise<{ key: string }> {
    return { key: await mint(this.env.FILES_KEYRING, ns) };
  }

  /** The data key `key` wrapped again under the active kid, refused as a read is. */
  async rewrapKey(ns: string, key: string): Promise<{ key: string }> {
    return { key: await rewrap(this.env.FILES_KEYRING, ns, key) };
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
        throw new Refusal("invalid", "a put is a hash and its bytes");
      }
      return { sha256: chunkHash(chunk.sha256), bytes: chunkBytes(chunk.bytes) };
    });
    if (new Set(puts.map((put) => put.sha256)).size !== puts.length
        || puts.reduce((sum, put) => sum + put.bytes.byteLength, 0) > BATCH_BYTES) {
      throw new Refusal("invalid",
                        `a put names distinct chunks of at most ${BATCH_BYTES} bytes in all`);
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
      throw new Refusal("invalid",
                        "the chunk's bytes do not match its sha256");  // R2 sees sealed bytes
    }
    const name = await chunkName(dataKey, sha256);
    const stored = await this.#stored(dataKey, chunkKey(ns, name), bytes, sha256);
    try {
      put = await this.env.BLOBS.put(chunkKey(ns, name), stored.body,
                                     { ...stored.options, onlyIf: { etagDoesNotMatch: "*" } });
    } catch (error) {
      if (BAD_DIGEST.test(String((error as Error)?.message))) {
        throw new Refusal("invalid", "the chunk's bytes do not match its sha256");
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
      throw new Refusal("invalid", "the chunk's bytes do not match its sha256");
    }
    let held: Uint8Array;
    try {
      const object = await this.env.BLOBS.get(chunkKey(ns, name), this.#reading(dataKey));
      held = object === null ? new Uint8Array(0)
        : await this.#plain(dataKey, chunkKey(ns, name), object);
    } catch {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    if (held.byteLength !== bytes.byteLength || await sha256Hex(held) !== sha256) {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    return false;
  }

  /**
   * Chunks' bytes, in order: each whole (its hash rechecked) or a range of it. A range costs
   * its length under SSE-C, which R2 slices; under `aes-gcm` it costs a whole chunk, which
   * this Worker fetches and opens before it slices, so a batch never pulls more than
   * `BATCH_BYTES` into the isolate.
   */
  async getChunks(ns: string, key: string, reads: ChunkGet[]): Promise<Uint8Array[]> {
    namespace(ns);
    const whole = this.#cipher() !== "ssec";
    const gets = list<ChunkGet>(reads, "a get").map((read) => {
      if (typeof read !== "object" || read === null) {
        throw new Refusal("invalid", "a get is a hash and an optional range");
      }
      const at = range(read);
      if (read.verify !== undefined && typeof read.verify !== "boolean") {
        throw new Refusal("invalid", "a get's verify is true or false");
      }
      // A verified range moves only its length to the caller (the whole chunk is held here).
      return { sha256: chunkHash(read.sha256), ...at, verify: read.verify === true,
               budget: whole && read.verify !== true ? CHUNK_BYTES : at.budget };
    });
    if (gets.reduce((sum, get) => sum + get.budget, 0) > BATCH_BYTES) {
      throw new Refusal("invalid", `a get moves at most ${BATCH_BYTES} bytes`);
    }
    const dataKey = await this.#key(ns, key);
    return Promise.all(gets.map(async (get) => {
      const name = await chunkName(dataKey, get.sha256);
      const ranged = get.verify ? undefined : get.range;
      const object = await this.env.BLOBS.get(chunkKey(ns, name),
                                              this.#reading(dataKey, ranged));
      if (object === null) {
        throw new Error("a chunk the manifest holds is missing from the bucket");
      }
      const bytes = await this.#plain(dataKey, chunkKey(ns, name), object, ranged);
      if (ranged === undefined && await sha256Hex(bytes) !== get.sha256) {
        throw new Error("a chunk's bytes no longer match its hash");
      }
      return get.verify && get.range !== undefined
        ? bytes.slice(get.range.offset, get.range.offset + get.range.length) : bytes;
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

  /**
   * Delete chunks (at most 1,000), by hash under the owner's key, which names them; a key
   * already gone is not an error.
   */
  async deleteChunks(ns: string, key: string, sha256s: string[]): Promise<{ deleted: number }> {
    namespace(ns);
    if (!Array.isArray(sha256s) || sha256s.length === 0 || sha256s.length > DELETE_BATCH
        || new Set(sha256s).size !== sha256s.length) {
      throw new Refusal("invalid", `a delete names 1 to ${DELETE_BATCH} distinct chunks`);
    }
    const hashes = sha256s.map((sha256) => chunkHash(sha256));
    const dataKey = await this.#key(ns, key);
    const names = await Promise.all(hashes.map((sha256) => chunkName(dataKey, sha256)));
    await this.env.BLOBS.delete(names.map((name) => chunkKey(ns, name)));
    return { deleted: hashes.length };
  }

  /** A generation's object's bytes put under the owner's key at `at`, verified by R2. */
  async #putObject(ns: string, key: string, gen: string, at: ManifestAt, bytes: Uint8Array):
      Promise<ManifestObject> {
    const body = chunkBytes(bytes);
    const sha256 = await sha256Hex(body);
    const dataKey = await this.#key(ns, key);
    const stored = await this.#stored(dataKey, manifestObjectKey(ns, gen, at), body, sha256);
    await this.env.BLOBS.put(manifestObjectKey(ns, gen, at), stored.body, stored.options);
    return { sha256, size: body.byteLength };
  }

  /** A generation's object's bytes read under the key `key` (the object's, or the root's). */
  async #getObject(ns: string, key: string, gen: string, at: ManifestAt, sha256: string):
      Promise<Uint8Array> {
    const dataKey = await this.#key(ns, key);
    const object = await this.env.BLOBS.get(manifestObjectKey(ns, gen, at), this.#reading(dataKey));
    if (object === null) {
      throw new Error("a manifest object its root names is missing from the bucket");
    }
    const bytes = await this.#plain(dataKey, manifestObjectKey(ns, gen, at), object);
    if (await sha256Hex(bytes) !== chunkHash(sha256)) {
      throw new Error("a manifest object's bytes no longer match its hash");
    }
    return bytes;
  }

  /**
   * One base part of the owner's generation `gen`, under the owner's key, verified by R2. A
   * part is written again when a pass that wrote it did not record it.
   */
  async putManifestPart(ns: string, key: string, gen: string, part: number,
                        bytes: Uint8Array): Promise<ManifestObject> {
    return this.#putObject(ns, key, gen, part, bytes);
  }

  /**
   * One log segment of the generation `gen`: the change log's seqs after `a` through `b`,
   * under the owner's key. Put again with the same bytes when its acknowledgement was lost.
   */
  async putManifestSegment(ns: string, key: string, gen: string, a: number, b: number,
                           bytes: Uint8Array): Promise<ManifestObject> {
    return this.#putObject(ns, key, gen, { a, b }, bytes);
  }

  /**
   * The generation's root, written once every part and segment through `e` is: the owner's
   * wrapped key, under the active kid whatever kid `key` was, so a pass that began before a
   * rotation writes no root under a retiring one; and the parts' and segments' hashes and
   * sizes in order, with the log's span, sealed under the data key, so a recovery needs only
   * the keyring.
   */
  async putManifestRoot(ns: string, key: string, gen: string, parts: readonly ManifestObject[],
                        segments: readonly ManifestSegment[], span: { s: number; e: number }):
      Promise<void> {
    const wrapped = await rewrap(this.env.FILES_KEYRING, ns, key);
    const dataKey = await this.#key(ns, wrapped);
    const object = (item: unknown, keys: string) => typeof item === "object" && item !== null
      && Object.keys(item).sort().join() === keys
      && typeof (item as ManifestObject).size === "number"
      && Number.isSafeInteger((item as ManifestObject).size)
      && (item as ManifestObject).size >= 1 && (item as ManifestObject).size <= CHUNK_BYTES;
    if (!Array.isArray(parts) || !Array.isArray(segments) || parts.length === 0
        || parts.length + segments.length > MANIFEST_PARTS
        || !parts.every((part) => object(part, "sha256,size"))
        || !segments.every((segment) => object(segment, "a,b,sha256,size"))
        || typeof span !== "object" || span === null || !Number.isSafeInteger(span.s)
        || !Number.isSafeInteger(span.e) || span.s < 1 || span.e < span.s - 1) {
      throw new Refusal("invalid", `a manifest has 1 to ${MANIFEST_PARTS} objects and a log span`);
    }
    const listed = JSON.stringify({
      parts: parts.map((part) => ({ sha256: chunkHash(part.sha256), size: part.size })),
      segments: segments.map((segment) => {
        const [a, b] = segmentSpan(segment.a, segment.b);
        return { a, b, sha256: chunkHash(segment.sha256), size: segment.size };
      }),
      s: span.s, e: span.e, at: Date.now() });
    const sealed = await seal(dataKey, manifestKey(ns, gen), new TextEncoder().encode(listed));
    const root: StoredRoot = { format: "tinyassets-manifest", version: 2, gen: generation(gen),
                               key: wrapped, sealed: base64(sealed) };
    await this.env.BLOBS.put(manifestKey(ns, gen), JSON.stringify(root));
  }

  /** A generation's root, its lists opened under the key it carries, or null for none. */
  async getManifestRoot(ns: string, gen: string): Promise<ManifestRoot | null> {
    const object = await this.env.BLOBS.get(manifestKey(ns, gen));
    if (object === null) {
      return null;
    }
    const root = storedRoot(await object.text(), gen);
    const dataKey = await this.#key(ns, root.key);
    const listed = JSON.parse(new TextDecoder().decode(await open(
      dataKey, manifestKey(ns, gen), rootBytes(root.sealed)))) as
      Pick<ManifestRoot, "parts" | "segments" | "s" | "e" | "at">;
    return { format: root.format, version: root.version, gen: root.gen, key: root.key,
             parts: listed.parts, segments: listed.segments, s: listed.s, e: listed.e,
             at: listed.at };
  }

  /**
   * A key rotation's step for one generation: its root's wrapped key wrapped again under the
   * active kid, written in place only if the root did not change meanwhile (a pass writing
   * it again writes the active kid itself). The key the root now carries, or null for none.
   */
  async rewrapManifestRoot(ns: string, gen: string): Promise<{ key: string } | null> {
    const object = await this.env.BLOBS.get(manifestKey(ns, gen));
    if (object === null) {
      return null;
    }
    const root = storedRoot(await object.text(), gen);
    const key = await rewrap(this.env.FILES_KEYRING, ns, root.key);
    if (key !== root.key) {
      const put = await this.env.BLOBS.put(manifestKey(ns, gen), JSON.stringify({ ...root, key }),
                                           { onlyIf: { etagMatches: object.etag } });
      if (put === null) {
        throw new Error("a manifest root changed while its key was wrapped again; run it again");
      }
    }
    return { key };
  }

  /** One base part's bytes, under the key `key` (the object's, or the root's), checked. */
  async getManifestPart(ns: string, key: string, gen: string, part: number,
                        sha256: string): Promise<Uint8Array> {
    return this.#getObject(ns, key, gen, part, sha256);
  }

  /** One log segment's bytes, under the key `key`, checked. */
  async getManifestSegment(ns: string, key: string, gen: string, a: number, b: number,
                           sha256: string): Promise<Uint8Array> {
    return this.#getObject(ns, key, gen, { a, b }, sha256);
  }

  /**
   * A generation deleted (`parts` of its base parts, the `segments` named, and its root): the
   * root first, so none dangles.
   */
  async deleteManifest(ns: string, gen: string, parts: number,
                       segments: readonly { a: number; b: number }[]): Promise<void> {
    if (parts !== 0) {
      manifestPart(parts - 1);
    }
    await this.env.BLOBS.delete(manifestKey(ns, gen));
    const all = Array.from({ length: parts }, (_, part) => part);
    for (let at = 0; at < all.length; at += DELETE_BATCH) {
      await this.env.BLOBS.delete(all.slice(at, at + DELETE_BATCH)
        .map((part) => manifestKey(ns, gen, part)));
    }
    for (let at = 0; at < segments.length; at += DELETE_BATCH) {
      await this.env.BLOBS.delete(segments.slice(at, at + DELETE_BATCH)
        .map((segment) => segmentKey(ns, gen, segment.a, segment.b)));
    }
  }
}

/**
 * The upload door's entrypoint (review M5-1): the doors bind only this, so the internet-
 * facing Worker, which carries an uploading owner's wrapped data key, can put that owner's
 * chunks and read nothing: no chunk, no generation, no key call. It serves `putChunks`
 * alone, as `FilesService` does it.
 */
export class FilesUpload extends WorkerEntrypoint<FilesEnv> implements ChunkUpload {
  /** Store chunks under their hashes, each verified by R2 (`FilesService.putChunks`). */
  async putChunks(ns: string, key: string, chunks: ChunkPut[]): Promise<{ created: number }> {
    return new FilesService(this.ctx, this.env).putChunks(ns, key, chunks);
  }
}
