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
 * - `putChunks` checks each chunk's bytes against its hash, seals them, and puts the sealed
 *   bytes create-once (`etagDoesNotMatch: "*"`) with their own hash as R2's `put` `sha256`
 *   option, which verifies them in transit (a mismatch is R2's `BadDigest`, a transfer fault
 *   left loud as R2 raised it, never refused as the caller's bytes). It relies on the binding's check, never on S3 checksum headers (R2's S3 API
 *   lists SHA-256 as a composite checksum only). An existing key is a no-op only when it
 *   opens to exactly these bytes; anything else under a hash key fails loudly instead of
 *   counting as a deduplicated hit.
 * - `getChunks` reads chunks whole or byte ranges of them. Every read fetches and opens its
 *   whole chunk (GCM authenticates the whole) and rechecks its hash, then answers the range
 *   alone, so the caller holds only the range and still reads verified bytes. Each read is
 *   charged the whole chunk it holds meanwhile, so a call holds at most `BATCH_BYTES`.
 * - `deleteChunks` removes up to 1,000 chunks in one call (R2's batch limit), named by their
 *   hashes under the owner's key; the owner object's garbage collection is its only caller,
 *   for keys in its own chunk table.
 * - `deleteNamespace` deletes every object under one owner's namespace prefix, listing it
 *   1,000 keys at a time: the owner's erasure alone, so a chunk whose row was lost, or
 *   whose put landed late, goes with the rest. It is the only list of the bucket, and only
 *   ever of one namespace's prefix.
 *
 * Every object is sealed in this Worker under its owner's data key (`datakey.ts`), by the
 * envelope's per-object seal (`contract/src/envelope.ts` `sealObject`: AES-256-GCM, the
 * object's own R2 key as additional data), so the bucket holds only ciphertext, and bytes
 * moved under another key or namespace fail to open. Each call presents the owner's wrapped
 * key, which this Worker opens with its own keyring (`FILES_KEYRING`) for that call alone and
 * never keeps. A put or get with no key, or a key that does not open for the namespace, is
 * refused before R2 is reached. A delete by hash presents it too (the key names the chunk);
 * the prefix delete needs none. So the bytes of an erased owner, whose wrapped key went with
 * their object, are unreadable wherever a chunk outlived the prefix delete, and their names
 * say nothing. And a copy of the bucket is a byte copy: it needs no key and sees no
 * plaintext, which is what lets an off-account copy exist (d3 §G).
 *
 * The nightly manifest (`owner/src/backup.ts`) is the only other kind of object: its parts
 * (`o/<ns>/m/<gen>/<n>`) and log segments (`o/<ns>/m/<gen>/l/<a>-<b>`), under the same data
 * key, and its root (`o/<ns>/m/<gen>`), a small
 * JSON carrying the owner's wrapped key, always under the ring's active kid, and the parts'
 * hashes and sizes sealed under the data key (the same seal, at the root's own R2 key), so
 * a recovery needs no object state but the keyring, and the bucket
 * alone says nothing of the parts. The root is written last, after every part. A key's
 * rotation re-wraps it in place (`rewrapManifestRoot`): the data key is the same, so no part
 * and no sealed list changes.
 *
 * It serves no HTTP: no fetch handler, no `workers.dev` or preview URL, no route. Owner
 * objects reach it by the `FILES` service binding alone, which adds no request fee.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { chunkName, mint, rewrap, unwrap } from "./datakey";
import type { FilesEnv } from "./env";
import { base64, fromBase64, hex, openObject, sealObject } from "../../contract/src/envelope";
import { BATCH_BYTES, CHUNK_BYTES, DELETE_BATCH, MANIFEST_PARTS } from "../../contract/src/limits";
import {
  chunkHash, chunkKey, chunkPrefix, generation, listedChunk, listedManifest, type ManifestAt,
  manifestKey, manifestObjectKey, manifestPart, manifestPrefix, segmentKey,
} from "./keys";
import { Refusal } from "../../contract/src/refusal";
import {
  type ChunkGet, type ChunkPut, type ChunkStore, type ChunkUpload, GET_READS, type ManifestLists,
  manifestLists, type ManifestObject, type ManifestRoot, type ManifestSegment, namespace,
} from "../../contract/src/files";

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

/** A root's opened lists, held to their checker; anything else fails loudly. */
function openedLists(plain: Uint8Array): ManifestLists {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(plain));
  } catch {
    throw new Error("a manifest root is out of shape");
  }
  const listed = manifestLists(parsed);
  if (listed === null) {
    throw new Error("a manifest root is out of shape");
  }
  return listed;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
}

/** `plain` sealed for the object at `objectKey`, and the sealed bytes' hash, which R2 checks. */
async function sealed(dataKey: ArrayBuffer, objectKey: string, plain: Uint8Array):
    Promise<{ body: Uint8Array; sha256: string }> {
  const body = await sealObject(dataKey, objectKey, plain);
  return { body, sha256: await sha256Hex(body) };
}

/** The plain bytes of `stored`, the object at `objectKey`; any other key or byte fails loudly. */
async function opened(dataKey: ArrayBuffer, objectKey: string,
                      stored: Uint8Array): Promise<Uint8Array> {
  const plain = await openObject(dataKey, objectKey, stored);
  if (plain === null) {
    throw new Error("a stored object does not open under its owner's key");
  }
  return plain;
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

/** A read's range, or none for the whole chunk. */
function range(read: ChunkGet): { offset: number; length: number } | undefined {
  const { offset, length } = read;
  if (offset === undefined && length === undefined) {
    return undefined;
  }
  if (typeof offset !== "number" || typeof length !== "number" || !Number.isSafeInteger(offset)
      || !Number.isSafeInteger(length) || offset < 0 || length < 1
      || offset + length > CHUNK_BYTES) {
    throw new Refusal("invalid", "a chunk range is an offset and a length inside one chunk");
  }
  return { offset, length };
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

  /** Store chunks under their hashes, each checked here; `created` counts the new ones. */
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
    if (await sha256Hex(bytes) !== sha256) {
      throw new Refusal("invalid", "the chunk's bytes do not match its sha256");
    }
    const name = await chunkName(dataKey, sha256);
    const stored = await sealed(dataKey, chunkKey(ns, name), bytes);
    const put = await this.env.BLOBS.put(chunkKey(ns, name), stored.body,
                                         { sha256: stored.sha256,
                                           onlyIf: { etagDoesNotMatch: "*" } });
    if (put !== null) {
      return true;
    }
    // The key exists. Every put here was checked and sealed, so it opens to these bytes,
    // unless something wrote around this Worker: that fails loudly, never as a dedup hit.
    // Bytes sealed under another key, or none, fail to open, so they fail here too.
    let held: Uint8Array;
    try {
      const object = await this.env.BLOBS.get(chunkKey(ns, name));
      held = object === null ? new Uint8Array(0)
        : await opened(dataKey, chunkKey(ns, name), new Uint8Array(await object.arrayBuffer()));
    } catch {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    if (held.byteLength !== bytes.byteLength || await sha256Hex(held) !== sha256) {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    return false;
  }

  /**
   * Chunks' bytes, in order: each whole or a range of it. Each read fetches, opens and
   * rechecks its whole chunk, which it holds until it answers, so each is charged a whole
   * chunk, whatever its range.
   */
  async getChunks(ns: string, key: string, reads: ChunkGet[]): Promise<Uint8Array[]> {
    namespace(ns);
    const gets = list<ChunkGet>(reads, "a get").map((read) => {
      if (typeof read !== "object" || read === null) {
        throw new Refusal("invalid", "a get is a hash and an optional range");
      }
      return { sha256: chunkHash(read.sha256), range: range(read) };
    });
    if (gets.length > GET_READS) {
      throw new Refusal("invalid", `a get reads at most ${GET_READS} chunks, `
                        + `${BATCH_BYTES} bytes of whole chunks`);
    }
    const dataKey = await this.#key(ns, key);
    return Promise.all(gets.map(async (get) => {
      const name = await chunkName(dataKey, get.sha256);
      const object = await this.env.BLOBS.get(chunkKey(ns, name));
      if (object === null) {
        throw new Error("a chunk the manifest holds is missing from the bucket");
      }
      const bytes = await opened(dataKey, chunkKey(ns, name),
                                 new Uint8Array(await object.arrayBuffer()));
      if (await sha256Hex(bytes) !== get.sha256) {
        throw new Error("a chunk's bytes no longer match its hash");
      }
      return get.range === undefined ? bytes
        : bytes.slice(get.range.offset, get.range.offset + get.range.length);
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

  /** A generation's object's bytes sealed under the owner's key at `at`, verified by R2. */
  async #putObject(ns: string, key: string, gen: string, at: ManifestAt, bytes: Uint8Array):
      Promise<ManifestObject> {
    const body = chunkBytes(bytes);
    const dataKey = await this.#key(ns, key);
    const stored = await sealed(dataKey, manifestObjectKey(ns, gen, at), body);
    await this.env.BLOBS.put(manifestObjectKey(ns, gen, at), stored.body,
                             { sha256: stored.sha256 });
    return { sha256: await sha256Hex(body), size: body.byteLength };
  }

  /** A generation's object's bytes read under the key `key` (the object's, or the root's). */
  async #getObject(ns: string, key: string, gen: string, at: ManifestAt, sha256: string):
      Promise<Uint8Array> {
    const dataKey = await this.#key(ns, key);
    const object = await this.env.BLOBS.get(manifestObjectKey(ns, gen, at));
    if (object === null) {
      throw new Error("a manifest object its root names is missing from the bucket");
    }
    const bytes = await opened(dataKey, manifestObjectKey(ns, gen, at),
                               new Uint8Array(await object.arrayBuffer()));
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
    const { s, e } = typeof span === "object" && span !== null ? span : { s: null, e: null };
    const listed = manifestLists({ parts, segments, s, e, at: Date.now() });
    if (listed === null) {
      throw new Refusal("invalid", `a manifest has 1 to ${MANIFEST_PARTS} objects and a log span`);
    }
    const lists = await sealObject(dataKey, manifestKey(ns, gen),
                                   new TextEncoder().encode(JSON.stringify(listed)));
    const root: StoredRoot = { format: "tinyassets-manifest", version: 2, gen: generation(gen),
                               key: wrapped, sealed: base64(lists) };
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
    const listed = openedLists(await opened(dataKey, manifestKey(ns, gen),
                                            rootBytes(root.sealed)));
    return { format: root.format, version: root.version, gen: root.gen, key: root.key,
             ...listed };
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
