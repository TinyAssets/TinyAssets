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
import { createHash } from "node:crypto";

import { CHUNK_BYTES } from "../../contract/src/limits";
import { refusalOf } from "../../contract/src/refusal";

import type { Bound } from "./binder";
import type { DoorsEnv } from "./config";
import { refusalResponse } from "./refused";

/** The owner-surface paths of the upload, after `/app/api/`. */
export const UPLOAD_PREFIX = "files/upload/";
export const UPLOAD_BEGIN = "files/upload/begin";
export const UPLOAD_COMMIT = "files/upload/commit";
export const UPLOAD_ABORT = "files/upload/abort";
const CHUNK_PATH = /^([0-9a-f]{32})\/(0|[1-9][0-9]{0,5})$/;

function failure(status: number, message: string, headers: HeadersInit = {}): Response {
  return Response.json({ error: message }, { status, headers: { "Cache-Control": "no-store",
                                                               ...headers } });
}

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

/** The body's bytes when they are exactly `size`, or null for a shorter or longer one. */
async function exactly(request: Request, size: number): Promise<Uint8Array | null> {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && declared !== String(size)) {
    return null;
  }
  const out = new Uint8Array(size);
  let at = 0;
  const reader = (request.body ?? new ReadableStream<Uint8Array>({ start: (c) => c.close() }))
    .getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    if (at + value.byteLength > size) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    out.set(value, at);
    at += value.byteLength;
  }
  return at === size ? out : null;
}

/** `PUT files/upload/<intent_id>/<index>`: one chunk's bytes, put to `FILES` by the binder. */
export async function streamUpload(request: Request, env: DoorsEnv, { binding, stub }: Bound,
                                   path: string): Promise<Response> {
  if (request.method !== "PUT") {
    return failure(405, "method not allowed");
  }
  if ((request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase()
      !== "application/octet-stream") {
    return failure(415, "an upload's bytes are application/octet-stream");
  }
  const named = CHUNK_PATH.exec(path);
  if (named === null) {
    return failure(404, "an upload's bytes go to files/upload/<intent_id>/<chunk index>");
  }
  const intentId = named[1]!;
  const index = Number(named[2]);
  try {
    const target = await stub.fileUploadTarget(binding, { intent_id: intentId });
    const chunk = target.chunks[index];
    if (chunk === undefined) {
      return failure(404, `the upload has ${target.chunks.length} chunks`);
    }
    if (chunk.size > CHUNK_BYTES) {
      throw new Error("an upload's plan names a chunk larger than a chunk");
    }
    const bytes = await exactly(request, chunk.size);
    if (bytes === null) {
      return failure(400, `chunk ${index} is ${chunk.size} bytes`);
    }
    const digest = createHash("sha256").update(bytes).digest();
    const headers = { "Content-Digest": contentDigest(digest) };
    const sent = claimed(request);
    if (sent !== undefined && sent !== digest.toString("base64")) {
      return failure(400, "the chunk's SHA-256 is not the Content-Digest it was sent with",
                     headers);
    }
    if (digest.toString("hex") !== chunk.sha256) {
      return failure(400, `the bytes of chunk ${index} are not the chunk the upload declared`,
                     headers);
    }
    const needed = target.pending.includes(chunk.sha256);
    if (needed) {
      await env.FILES.putChunks(target.namespace, target.key,
                                [{ sha256: chunk.sha256, bytes }]);
      await stub.fileUploadStored(binding, { intent_id: intentId, chunks: [chunk.sha256] });
    }
    return Response.json({ intent_id: intentId, index, stored: needed,
                           pending: target.pending.length - (needed ? 1 : 0) },
                         { headers: { "Cache-Control": "no-store", ...headers } });
  } catch (error) {
    const refused = refusalOf(error);
    if (refused === null) {
      throw error;
    }
    return refusalResponse(refused, { "Cache-Control": "no-store" });
  }
}
