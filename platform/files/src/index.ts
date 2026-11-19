/**
 * The files Worker (`platform/files`): a stateless blob pipe for owner file bytes
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
 * - `checkChunks` reads chunks whole as `getChunks` does and answers each by what it found
 *   (`ChunkCheck`): stored as its hash, missing, or broken. A chunk not read is an answer, so
 *   what it throws is only a refusal, a lost answer or a bug (the import's check).
 * - `deleteChunks` removes up to 1,000 chunks in one call (R2's batch limit), named by their
 *   hashes under the owner's key; the owner object's garbage collection is its only caller,
 *   for keys in its own chunk table.
 * - `deleteNamespace` deletes every object under one owner's namespace prefix, listing it
 *   1,000 keys at a time: the owner's erasure and the cutover's discard alone, so a chunk
 *   whose row was lost, or whose put landed late, goes with the rest. First, in the same
 *   call, it makes the namespace's mark (`e/<ns>`, `contract/src/files.ts` `MARKS`), once and
 *   never deleted: the record of the erasure that the nightly copy carries off the account,
 *   so no copy back ever writes the namespace again (`deploy/offsite.ts`). A failure after
 *   the mark leaves it standing, and the delete is repeated. An erasure marks it at its start
 *   too (`markNamespace`), a day before its delete. Each namespace it holds (`o/<ns>/h/<held>`,
 *   `HOLDS`: another of the same owner, which no object adopted) is marked after it and
 *   deleted before its own objects, and its holds go last, so an owner's erasure takes every
 *   namespace of it the bucket records (`r8-erased-orphans.md`).
 * - `holdNamespace` records one namespace as held by another, once, and `rootNamespace`
 *   deletes every hold of a namespace by one it holds, so no cycle runs through it: a
 *   restore's `restoreHold` alone, once the owner's object has held the namespace's generation
 *   to its own owner.
 * - `sweepManifests` lists one page of one namespace's generations and deletes every object
 *   of a generation the owner's object no longer names, each root before its objects: the
 *   manifest lane's sweep alone.
 * - `chunkNames`, `listChunks` and `deleteListed`: a restore's sweep of the chunks no row of
 *   its owner names (`owner/src/restore.ts`, its `names` and `sweep` steps), while the restore
 *   holds the owner, so no upload can make a row for a name meanwhile. `chunkNames` answers the
 *   names of up to 1,000 hashes under the owner's key, with no R2 call; `listChunks` lists one
 *   page of the namespace's chunk names after a name; `deleteListed` deletes up to 1,000 of
 *   them by name, under no key, as the erasure's prefix delete takes none.
 *
 * - `FilesLocate`, for the operator channel alone, when no object names a namespace (a restore
 *   after an account's loss, `deploy/offsite.ts` `recover`, or of a lost object): `locate`, a
 *   namespace's owner read from its newest generation's part 0 header; `namespaces`, the
 *   namespaces the bucket holds, a page at a time; and `erase`, one deleted whole for an owner
 *   the doors record erased (`r8-lost-object.md`).
 *
 * `deleteNamespace`, `markNamespace` and `rootNamespace` (of a namespace's holds),
 * `sweepManifests`, `listChunks` and `locate` are the only lists of the bucket but one, each
 * only ever of one namespace's prefix; `namespaces` lists the namespaces themselves, no object
 * of any.
 *
 * Every object of a namespace is sealed in this Worker under its owner's data key
 * (`datakey.ts`), by the envelope's per-object seal (`contract/src/envelope.ts` `sealObject`:
 * AES-256-GCM, the object's own R2 key as additional data), so the bucket holds only
 * ciphertext but the marks and the holds, which hold nothing but what they are, and bytes
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
import { keyed } from "../../contract/src/checks";
import { base64, fromBase64, hex, openObject, sealObject } from "../../contract/src/envelope";
import { exportHeader } from "../../contract/src/export-header";
import { BATCH_BYTES, CHUNK_BYTES, DELETE_BATCH, MANIFEST_PARTS } from "../../contract/src/limits";
import {
  chunkHash, chunkKey, chunkPrefix, generation, listedChunk, listedGeneration, listedManifest,
  holdKey, holdPrefix, listedHold, listedNamespace, type ManifestAt, manifestKey,
  manifestObjectKey, manifestPrefix, markKey, namespacesPrefix, segmentKey,
} from "./keys";
import { Refusal } from "../../contract/src/refusal";
import {
  type ChunkCheck, type ChunkGet, type ChunkLocate, type ChunkPut, type ChunkStore,
  type ChunkUpload, GET_READS, type NamespaceFound, type ManifestLists, manifestLists, MARK_BODY,
  type ManifestObject, type ManifestRoot, type ManifestSegment, namespace, type NamespacePage,
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
  if (!keyed(root, ["format", "version", "gen", "key", "sealed"])
      || root["format"] !== "tinyassets-manifest" || root["version"] !== 2 || root["gen"] !== gen
      || typeof root["key"] !== "string" || typeof root["sealed"] !== "string") {
    throw new Error("a manifest root is out of shape");
  }
  return root as unknown as StoredRoot;
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

const OPENS_NOT = "a stored object does not open under its owner's key";

/** The plain bytes of `stored`, the object at `objectKey`; any other key or byte fails loudly. */
async function opened(dataKey: ArrayBuffer, objectKey: string,
                      stored: Uint8Array): Promise<Uint8Array> {
  const plain = await openObject(dataKey, objectKey, stored);
  if (plain === null) {
    throw new Error(OPENS_NOT);
  }
  return plain;
}

/**
 * `value` as 1 to `DELETE_BATCH` distinct chunk hashes or names (64 lowercase hex, `keys.ts`),
 * or refused, before any is acted on.
 */
function distinct(value: unknown, what: string): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > DELETE_BATCH
      || new Set(value).size !== value.length) {
    throw new Refusal("invalid", `${what} names 1 to ${DELETE_BATCH} distinct chunks`);
  }
  return value.map((each) => chunkHash(each));
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
    // The key exists. Every put here was checked and sealed, so it is stored as its hash,
    // unless something wrote around this Worker: that fails loudly, never as a dedup hit.
    // Bytes sealed under another key, or none, do not open, so they fail here too. A bucket
    // read that fails is its own error, never this one.
    if (!("bytes" in await this.#chunk(ns, dataKey, sha256))) {
      throw new Error("a chunk key holds bytes this Worker did not verify");
    }
    return false;
  }

  /**
   * Chunks' bytes, in order: each whole or a range of it. Each read fetches, opens and
   * rechecks its whole chunk, which it holds until it answers, so each is charged a whole
   * chunk, whatever its range. A chunk not read fails loudly: its manifest holds it.
   */
  async getChunks(ns: string, key: string, reads: ChunkGet[]): Promise<Uint8Array[]> {
    namespace(ns);
    const gets = list<ChunkGet>(reads, "a get").map((read) => {
      if (typeof read !== "object" || read === null) {
        throw new Refusal("invalid", "a get is a hash and an optional range");
      }
      return { sha256: chunkHash(read.sha256), range: range(read) };
    });
    const dataKey = await this.#reading(ns, key, gets.length);
    return Promise.all(gets.map(async (get) => {
      const read = await this.#chunk(ns, dataKey, get.sha256);
      if (!("bytes" in read)) {
        throw new Error(read.why);
      }
      return get.range === undefined ? read.bytes
        : read.bytes.slice(get.range.offset, get.range.offset + get.range.length);
    }));
  }

  /** Chunks read whole as `getChunks` reads them, each answered, in order, by what it found. */
  async checkChunks(ns: string, key: string, sha256s: string[]): Promise<ChunkCheck[]> {
    namespace(ns);
    const hashes = list<unknown>(sha256s, "a check").map((sha256) => chunkHash(sha256));
    const dataKey = await this.#reading(ns, key, hashes.length);
    return Promise.all(hashes.map(async (sha256) => {
      const read = await this.#chunk(ns, dataKey, sha256);
      return "bytes" in read ? "stored" : read.check;
    }));
  }

  /** The data key for a read of `reads` whole chunks, refused past `GET_READS`. */
  #reading(ns: string, key: string, reads: number): Promise<ArrayBuffer> {
    if (reads > GET_READS) {
      throw new Refusal("invalid", `a get reads at most ${GET_READS} chunks, `
                        + `${BATCH_BYTES} bytes of whole chunks`);
    }
    return this.#key(ns, key);
  }

  /** The chunk `sha256` of `ns`, fetched and opened whole and its hash rechecked, or why not. */
  async #chunk(ns: string, dataKey: ArrayBuffer, sha256: string): Promise<
      { bytes: Uint8Array } | { check: Exclude<ChunkCheck, "stored">; why: string }> {
    const name = await chunkName(dataKey, sha256);
    const object = await this.env.BLOBS.get(chunkKey(ns, name));
    if (object === null) {
      return { check: "missing", why: "a chunk the manifest holds is missing from the bucket" };
    }
    const bytes = await openObject(dataKey, chunkKey(ns, name),
                                   new Uint8Array(await object.arrayBuffer()));
    if (bytes === null) {
      return { check: "broken", why: OPENS_NOT };
    }
    return await sha256Hex(bytes) === sha256 ? { bytes }
      : { check: "broken", why: "a chunk's bytes no longer match its hash" };
  }

  /**
   * Mark `ns` deleted on purpose, once, before anything else, then each namespace it holds (and
   * each those hold): a mark again finds it and leaves its one version. A copy back leaves out
   * a namespace a marked one holds, so one cut short between the two is left out too.
   */
  async markNamespace(ns: string): Promise<void> {
    await this.#mark(ns, new Set());
  }

  async #mark(ns: string, seen: Set<string>): Promise<void> {
    seen.add(ns);
    await this.env.BLOBS.put(markKey(ns), MARK_BODY, { onlyIf: { etagDoesNotMatch: "*" } });
    for (const held of await this.#holds(ns)) {
      if (!seen.has(held)) {
        await this.#mark(held, seen);
      }
    }
  }

  /** Record `held` as held by `ns`, once (`HOLDS`): the owner's object checked it is its own. */
  async holdNamespace(ns: string, held: string): Promise<void> {
    await this.env.BLOBS.put(holdKey(ns, held), "", { onlyIf: { etagDoesNotMatch: "*" } });
  }

  /**
   * Every hold of `ns` by a namespace it holds, or one those hold, deleted, so `ns` holds all it
   * did and no cycle of holds runs through it: the namespace an owner's object adopts holds the
   * rest, and none holds it (`restoreHold`). Repeatable.
   */
  async rootNamespace(ns: string): Promise<void> {
    const seen = new Set([ns]);
    for (const queue = await this.#holds(ns); queue.length > 0;) {
      const held = queue.pop()!;
      if (!seen.has(held)) {
        seen.add(held);
        const holds = await this.#holds(held);
        if (holds.includes(ns)) {
          await this.env.BLOBS.delete(holdKey(held, ns));
        }
        queue.push(...holds);
      }
    }
  }

  /** Every namespace `ns` holds, listed under its holds' prefix page by page. */
  async #holds(ns: string): Promise<string[]> {
    const held: string[] = [];
    let cursor: string | undefined;
    do {
      const page = await this.env.BLOBS.list({ prefix: holdPrefix(ns),
                                               ...(cursor === undefined ? {} : { cursor }) });
      held.push(...page.objects.map((object) => listedHold(ns, object.key)));
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor !== undefined);
    return held;
  }

  /**
   * Mark `ns` deleted on purpose (`markNamespace`), then delete each namespace it holds as it
   * is, then every chunk and every manifest object under its prefixes, listed 1,000 at a time,
   * and its holds last, so a delete cut short finds them again; repeatable.
   */
  async deleteNamespace(ns: string): Promise<{ deleted: number }> {
    await this.markNamespace(ns);
    return this.#delete(ns, new Set());
  }

  async #delete(ns: string, seen: Set<string>): Promise<{ deleted: number }> {
    seen.add(ns);
    let deleted = 0;
    const holds = await this.#holds(ns);
    for (const held of holds.filter((each) => !seen.has(each))) {
      deleted += (await this.#delete(held, seen)).deleted;
    }
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
        break;
      }
      await this.env.BLOBS.delete(keys.map((key) => listedManifest(ns, key)));
      deleted += keys.length;
    }
    for (let at = 0; at < holds.length; at += DELETE_BATCH) {
      await this.env.BLOBS.delete(holds.slice(at, at + DELETE_BATCH)
        .map((held) => holdKey(ns, held)));
    }
    return { deleted: deleted + holds.length };
  }

  /**
   * Delete chunks (at most 1,000), by hash under the owner's key, which names them; a key
   * already gone is not an error.
   */
  async deleteChunks(ns: string, key: string, sha256s: string[]): Promise<{ deleted: number }> {
    const names = await this.#names(ns, key, distinct(sha256s, "a delete"));
    await this.env.BLOBS.delete(names.map((name) => chunkKey(ns, name)));
    return { deleted: names.length };
  }

  /** The names of chunks (at most 1,000), by hash under the owner's key, in order; no R2 call. */
  async chunkNames(ns: string, key: string, sha256s: string[]): Promise<string[]> {
    return this.#names(ns, key, distinct(sha256s, "a lookup"));
  }

  /** The R2 names of `hashes` under the owner's key `key`, refused unless it opens for `ns`. */
  async #names(ns: string, key: string, hashes: readonly string[]): Promise<string[]> {
    namespace(ns);
    const dataKey = await this.#key(ns, key);
    return Promise.all(hashes.map((sha256) => chunkName(dataKey, sha256)));
  }

  /**
   * One page of `ns`'s chunk names, in key order, after the name `after` (null: from the
   * first), at most 1,000 (R2's page); and whether more follow.
   */
  async listChunks(ns: string, after: string | null): Promise<{ names: string[]; more: boolean }> {
    const listed = await this.env.BLOBS.list({
      prefix: chunkPrefix(ns), limit: DELETE_BATCH,
      ...(after === null ? {} : { startAfter: chunkKey(ns, after) }) });
    return { names: listed.objects.map((object) => listedChunk(ns, object.key)),
             more: listed.truncated };
  }

  /** Chunks of `ns` (at most 1,000) deleted by their names; a name already gone is no error. */
  async deleteListed(ns: string, names: string[]): Promise<{ deleted: number }> {
    namespace(ns);
    const listed = distinct(names, "a delete");
    await this.env.BLOBS.delete(listed.map((name) => chunkKey(ns, name)));
    return { deleted: listed.length };
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
   * it again writes the active kid itself). The key the root now carries; `key` null when it
   * changed meanwhile, so the owner's sweep counts it left and asks again; null for no root.
   */
  async rewrapManifestRoot(ns: string, gen: string): Promise<{ key: string | null } | null> {
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
        return { key: null };
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

  /** A generation's log segments deleted: a finished one's tail past E, which no root names. */
  async deleteSegments(ns: string, gen: string,
                       segments: readonly { a: number; b: number }[]): Promise<void> {
    for (let at = 0; at < segments.length; at += DELETE_BATCH) {
      await this.env.BLOBS.delete(segments.slice(at, at + DELETE_BATCH)
        .map((segment) => segmentKey(ns, gen, segment.a, segment.b)));
    }
  }

  /**
   * One page of a sweep of `ns`'s generations: the next `DELETE_BATCH` keys under its manifest
   * prefix after `cursor`, and every one of a generation `keep` does not name deleted, the
   * roots first. The first generation on a continuation can have a root kept on an earlier
   * page: the owner reads its retained generations again between pages. Where the next
   * page starts, or null after the last.
   */
  async sweepManifests(ns: string, keep: string[], cursor: string | null):
      Promise<{ deleted: number; cursor: string | null }> {
    namespace(ns);
    if (!Array.isArray(keep) || (cursor !== null && typeof cursor !== "string")) {
      throw new Refusal("invalid", "a sweep names the generations it keeps and where it goes on");
    }
    const kept = new Set(keep.map((gen) => generation(gen)));
    const listed = await this.env.BLOBS.list({ prefix: manifestPrefix(ns), limit: DELETE_BATCH,
                                               ...(cursor === null ? {} : { cursor }) });
    const page = listed.objects.map((object) => listedGeneration(ns, object.key));
    const gone = page.filter((at) => !kept.has(at.gen));
    const roots = gone.filter((at) => at.root).map((at) => at.key);
    const first = page[0];
    if (cursor !== null && first && !first.root && !kept.has(first.gen)) {
      roots.push(manifestKey(ns, first.gen));
    }
    const objects = gone.filter((at) => !at.root).map((at) => at.key);
    for (const keys of [roots, objects].filter((each) => each.length > 0)) {
      await this.env.BLOBS.delete(keys.map((key) => listedManifest(ns, key)));
    }
    return { deleted: gone.length, cursor: listed.truncated ? listed.cursor : null };
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

/**
 * The operator channel's entrypoint (`restore.locate`, `restore.namespaces`,
 * `doors/src/operator.ts`): the doors bind it for the platform's own ops alone, so they read a
 * namespace's owner and nothing else of its objects, and hold no key. It serves `locate`,
 * `namespaces` and `erase`, through `FilesService`'s own reads and delete, as `FilesUpload`
 * serves its put.
 */
export class FilesLocate extends WorkerEntrypoint<FilesEnv> implements ChunkLocate {
  /**
   * The owner of `ns`, as its newest finished generation names it, that generation and when its
   * root was written; `erased` when the bucket marks it deleted on purpose (`markKey`), read
   * first, so nothing of an erased owner is opened; null when the bucket holds no root of it.
   * Its roots are listed (the keys directly under its manifest prefix) and each opened under
   * the wrapped key it carries; the newest's part 0 is read under that key and held to the
   * root's hash, and its first line to the export header's shape, naming that generation.
   * Nothing of the part but the header's owner is answered. A root or a part that does not
   * open, or a part 0 with no header of its own generation, fails loudly: every generation
   * names its owner.
   */
  async locate(ns: string): Promise<NamespaceFound> {
    if (await this.env.BLOBS.head(markKey(ns)) !== null) {
      return "erased";
    }
    const files = new FilesService(this.ctx, this.env);
    const roots: ManifestRoot[] = [];
    let cursor: string | undefined;
    do {
      const page = await this.env.BLOBS.list({ prefix: manifestPrefix(namespace(ns)),
                                               delimiter: "/", ...(cursor === undefined ? {}
                                                 : { cursor }) });
      for (const root of await Promise.all(page.objects.map((object) =>
        files.getManifestRoot(ns, listedGeneration(ns, object.key).gen)))) {
        if (root !== null) {
          roots.push(root);
        }
      }
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor !== undefined);
    const newest = roots.sort((a, b) => b.at - a.at || (a.gen < b.gen ? 1 : -1))[0];
    if (newest === undefined) {
      return null;
    }
    const part = await files.getManifestPart(ns, newest.key, newest.gen, 0,
                                             newest.parts[0]!.sha256);
    const end = part.indexOf(0x0a);
    let first: unknown;
    try {
      first = JSON.parse(new TextDecoder().decode(part.subarray(0, end < 0 ? part.length : end)));
    } catch {
      first = null;
    }
    const header = exportHeader(first);
    if (header === null || header.gen !== newest.gen) {
      throw new Error(`generation ${newest.gen}'s first part holds no export header of it`);
    }
    return { owner: header.owner, gen: newest.gen, at: newest.at };
  }

  /**
   * One page of the namespaces the bucket holds any object of (the prefixes directly under
   * `namespacesPrefix()`), from `cursor` (null: the first), and the next page's cursor: a lost
   * object's restore finds its owner's among them (`deploy/deploy.ts` mode `restore`).
   */
  async namespaces(cursor: string | null): Promise<NamespacePage> {
    const page = await this.env.BLOBS.list({ prefix: namespacesPrefix(), delimiter: "/",
                                             ...(cursor === null ? {} : { cursor }) });
    return { namespaces: page.delimitedPrefixes.map(listedNamespace),
             cursor: page.truncated ? page.cursor : null };
  }

  /**
   * `ns` deleted whole, its mark first (`FilesService.deleteNamespace`): a namespace the doors
   * found naming an owner their directory records erased, which that owner's erasure did not
   * reach (`restore.locate`). Repeatable.
   */
  async erase(ns: string): Promise<{ deleted: number }> {
    return new FilesService(this.ctx, this.env).deleteNamespace(ns);
  }
}
