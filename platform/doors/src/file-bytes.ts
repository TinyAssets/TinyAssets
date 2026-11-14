/**
 * The file door's bytes (tasks 2, "Owed by 6.1"; slice L, after its review): the binder
 * streams an upload's chunks to `FILES` itself. Decided in 6.1: the binder is the trust
 * root and already reaches every owner's object, so its reading an owner's chunk namespace
 * from that owner's object weakens no isolation, and a file's bytes never pass through the
 * owner's object.
 *
 * The owner's app, on the owner surface, with its own token:
 * 1. `files/upload/begin` (JSON): the object's `fileUploadBegin`, the file's size and the
 *    SHA-256 of each 4 MiB of it; its intent, and the chunks it still needs.
 * 2. `PUT files/upload/<intent_id>/<index>` (`application/octet-stream`), one per chunk of
 *    the plan, in any order and again after any failure: exactly that chunk's bytes. The
 *    door reads the plan, the owner's namespace and its wrapped data key, which only `FILES`
 *    opens (`fileUploadTarget`), checks the bytes
 *    against the chunk's declared SHA-256, puts a chunk the intent still needs to `FILES`
 *    (R2 checks its hash again) and reports it (`fileUploadStored`). It answers the chunk's
 *    SHA-256 as `Content-Digest` (RFC 9530) and checks one the client sent. A chunk sent
 *    again is answered without a second put, so an upload resumes where it stopped.
 * 3. `files/upload/commit` (JSON): the object's `fileUploadCommit`.
 *
 * So a file is bounded by the owner's policy (`max_file_bytes`, the intent's plan), never
 * by one request: each request carries one chunk, at most 4 MiB, far inside Cloudflare's
 * per-request body size (100 MB on Free and Pro, by its Workers limits page). A refusal at
 * any step (an expired intent, the erasure's mark, a chunk R2 refused) answers its fixed
 * code. The object's chunk doors (`fileUploadChunks`) stay for in-object callers.
 */
import { sha256 } from "../../contract/src/digest";
import { CHUNK_BYTES } from "../../contract/src/limits";

import type { Bound } from "./binder";
import type { DoorsEnv } from "./env";
import { answer, boundedBytes, failure, mediaType } from "./kit";

const CHUNK_PATH = /^([0-9a-f]{32})\/(0|[1-9][0-9]{0,5})$/;

/** A `Content-Digest` member for SHA-256 (RFC 9530): `sha-256=:<base64>:`. */
export function contentDigest(digest: Buffer): string {
  return `sha-256=:${digest.toString("base64")}:`;
}

/** The SHA-256 a client's `Content-Digest` names, or undefined when it names none. */
function claimed(request: Request): string | undefined {
  const header = request.headers.get("Content-Digest");
  return header === null ? undefined
    : /(?:^|,)\s*sha-256=:([A-Za-z0-9+/=]+):/.exec(header)?.[1] ?? "";
}

/** Bytes that are the chunk their upload's plan declares (`held`): what `putChunk` takes. */
export interface HeldChunk {
  readonly sha256: string;
  readonly bytes: Uint8Array;
}

/**
 * `bytes` held to `chunk` as its upload's plan declares it, the one check before a chunk's
 * bytes are put: `size` when they are not its size (or there are none), `hash` when they are
 * not its SHA-256, else the chunk held; their digest rides each answer past the size. A plan
 * that names a chunk larger than a chunk is a fault: no plan makes one.
 */
export function held(chunk: { readonly sha256: string; readonly size: number },
                     bytes: Uint8Array | null):
    { readonly problem: "size" }
    | { readonly problem: "hash"; readonly digest: Buffer }
    | { readonly problem: null; readonly digest: Buffer; readonly chunk: HeldChunk } {
  if (chunk.size > CHUNK_BYTES) {
    throw new Error("an upload's plan names a chunk larger than a chunk");
  }
  if (bytes === null || bytes.byteLength !== chunk.size) {
    return { problem: "size" };
  }
  const digest = sha256(bytes);
  return digest.toString("hex") !== chunk.sha256 ? { problem: "hash", digest }
    : { problem: null, digest, chunk: { sha256: chunk.sha256, bytes } };
}

/**
 * A held chunk's bytes put to `FILES` under its upload's namespace and wrapped key, never
 * through the owner's object: the doors' one put of a file's bytes.
 */
export async function putChunk(env: DoorsEnv, target: { namespace: string; key: string },
                               chunk: HeldChunk): Promise<void> {
  await env.FILES.putChunks(target.namespace, target.key, [chunk]);
}

/** `PUT files/upload/<intent_id>/<index>`: one chunk's bytes, put to `FILES` by the binder. */
export async function streamUpload(request: Request, env: DoorsEnv, { binding, stub }: Bound,
                                   path: string): Promise<Response> {
  if (request.method !== "PUT") {
    return failure(405, "method not allowed");
  }
  if (mediaType(request) !== "application/octet-stream") {
    return failure(415, "an upload's bytes are application/octet-stream");
  }
  const named = CHUNK_PATH.exec(path);
  if (named === null) {
    return failure(404, "an upload's bytes go to files/upload/<intent_id>/<chunk index>");
  }
  const intentId = named[1]!;
  const index = Number(named[2]);
  const target = await stub.fileUploadTarget(binding, { intent_id: intentId });
  const chunk = target.chunks[index];
  if (chunk === undefined) {
    return failure(404, `the upload has ${target.chunks.length} chunks`);
  }
  const bytes = held(chunk, await boundedBytes(request, chunk.size));
  if (bytes.problem === "size") {
    return failure(400, `chunk ${index} is ${chunk.size} bytes`);
  }
  const headers = { "Content-Digest": contentDigest(bytes.digest) };
  const sent = claimed(request);
  if (sent !== undefined && sent !== bytes.digest.toString("base64")) {
    return failure(400, "the chunk's SHA-256 is not the Content-Digest it was sent with",
                   headers);
  }
  if (bytes.problem === "hash") {
    return failure(400, `the bytes of chunk ${index} are not the chunk the upload declared`,
                   headers);
  }
  const needed = target.pending.includes(chunk.sha256);
  if (needed) {
    await putChunk(env, target, bytes.chunk);
    await stub.fileUploadStored(binding, { intent_id: intentId, chunks: [chunk.sha256] });
  }
  return answer(200, { intent_id: intentId, index, stored: needed,
                       pending: target.pending.length - (needed ? 1 : 0) }, headers);
}
