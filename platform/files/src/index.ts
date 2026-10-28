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
 * (Workers service bindings): one call moves up to `BATCH_BYTES`, under the 32 MiB a
 * serialized RPC may carry.
 *
 * - `putChunks` is create-once (`etagDoesNotMatch: "*"`) and passes each hash to R2's
 *   `put` `sha256` option, which verifies the bytes (a mismatch is R2's `BadDigest`,
 *   refused here as invalid). It relies on the binding's check, never on S3 checksum
 *   headers (R2's S3 API lists SHA-256 as a composite checksum only). An existing key is
 *   a no-op only when it holds exactly these bytes; anything else under a hash key fails
 *   loudly instead of counting as a deduplicated hit.
 * - `getChunks` reads chunks whole, rechecking each hash, or byte ranges of them.
 * - `deleteChunks` removes up to 1,000 chunks in one call (R2's batch limit); the owner
 *   object's garbage collection is its only caller, for keys in its own chunk table, so
 *   nothing ever lists the bucket.
 *
 * It serves no HTTP: no fetch handler, no `workers.dev` or preview URL, no route. Owner
 * objects reach it by the `FILES` service binding alone, which adds no request fee. Owed
 * before cutover: a per-owner SSE-C data key on every get and put (the binding documents
 * `ssecKey` on `get`, `put` and `createMultipartUpload`; what a keyless `head` or `get`
 * returns, and whether it combines with `onlyIf` and `sha256`, needs a live probe, and
 * miniflare does not emulate it).
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import {
  BATCH_BYTES, CHUNK_BYTES, chunkHash, chunkKey, DELETE_BATCH, Invalid, namespace,
} from "./keys";
/** How a binding reports R2's `BadDigest`: the code, or its message. */
const BAD_DIGEST = /\(10037\)|did not match what we received/;

export interface FilesEnv {
  readonly BLOBS: R2Bucket;
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
  /** Store chunks under their hashes, each verified by R2; `created` counts the new ones. */
  async putChunks(ns: string, chunks: ChunkPut[]): Promise<{ created: number }> {
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
    const created = await Promise.all(puts.map((put) => this.#put(ns, put.sha256, put.bytes)));
    return { created: created.filter(Boolean).length };
  }

  async #put(ns: string, sha256: string, bytes: Uint8Array): Promise<boolean> {
    let put: R2Object | null;
    try {
      put = await this.env.BLOBS.put(chunkKey(ns, sha256), bytes,
                                     { sha256, onlyIf: { etagDoesNotMatch: "*" } });
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
    // something wrote around this Worker: that fails loudly, never as a dedup hit.
    if (await sha256Hex(bytes) !== sha256) {
      throw new Invalid("the chunk's bytes do not match its sha256");
    }
    const head = await this.env.BLOBS.head(chunkKey(ns, sha256));
    if (head === null || head.size !== bytes.byteLength || hex(head.checksums.sha256) !== sha256) {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    return false;
  }

  /** Chunks' bytes, in order: each whole (its hash rechecked) or a range of it. */
  async getChunks(ns: string, reads: ChunkGet[]): Promise<Uint8Array[]> {
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
    return Promise.all(gets.map(async (get) => {
      const object = await this.env.BLOBS.get(chunkKey(ns, get.sha256),
                                              get.range === undefined ? {} : { range: get.range });
      if (object === null) {
        throw new Error("a chunk the manifest holds is missing from the bucket");
      }
      const bytes = new Uint8Array(await object.arrayBuffer());
      if (get.range === undefined && await sha256Hex(bytes) !== get.sha256) {
        throw new Error("a chunk's bytes no longer match its hash");
      }
      return bytes;
    }));
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
}
