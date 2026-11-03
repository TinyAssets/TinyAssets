/**
 * The file door's bytes (tasks 2, "Owed by 6.1"; slice L): the binder streams an upload's
 * chunks to `FILES` itself. Decided in 6.1: the binder is the trust root and already
 * reaches every owner's object, so its reading an owner's chunk namespace from that owner's
 * object weakens no isolation, and a file's bytes never pass through the owner's object.
 *
 * The owner's app, on the owner surface, with its own token:
 * 1. `files/upload/begin` (JSON): the object's `fileUploadBegin`, the file's size and the
 *    SHA-256 of each 4 MiB of it, as `fileUploadBegin` takes them; its intent.
 * 2. `PUT files/upload/<intent_id>` (`application/octet-stream`): the file's bytes, whole
 *    and in order. The door asks the object for the intent's plan and the owner's namespace
 *    (`fileUploadTarget`), cuts the stream into the plan's chunks, holding one chunk at a
 *    time, checks each against its declared SHA-256, puts each the intent still needs to
 *    `FILES` (two at a time, R2 checking each hash again) and reports them stored
 *    (`fileUploadStored`). The whole body's SHA-256 is computed as it streams and served as
 *    `Repr-Digest` (RFC 9530); a `Repr-Digest` the client sent is checked against it.
 * 3. `files/upload/commit` (JSON): the object's `fileUploadCommit`.
 *
 * Bytes that are not the declared chunk at their place are refused (`400`) and nothing of
 * them is reported stored; a body shorter or longer than the declared file, too. The
 * object's chunk doors (`fileUploadChunks`) stay for in-object callers (the environment,
 * the sync agent's small files). A body is bounded by the intent's declared size, which the
 * owner's `max_file_bytes` bounds, and by Cloudflare's own request size.
 */
import { createHash } from "node:crypto";

import { CHUNK_BYTES } from "../../files/src/keys";

import type { Bound } from "./binder";
import type { DoorsEnv } from "./config";
import { refusal } from "./tools";

/** The owner-surface paths of the upload, after `/app/api/`. */
export const UPLOAD_PREFIX = "files/upload/";
export const UPLOAD_BEGIN = "files/upload/begin";
export const UPLOAD_COMMIT = "files/upload/commit";
const INTENT = /^[0-9a-f]{32}$/;
/** Chunks one `FILES` call carries: two, as the files Worker's batch takes. */
const PER_CALL = 2;

function failure(status: number, message: string, headers: HeadersInit = {}): Response {
  return Response.json({ error: message }, { status, headers: { "Cache-Control": "no-store",
                                                               ...headers } });
}

/** Reads a stream in exact-size pieces, holding at most one piece and one read. */
class Pieces {
  readonly #reader: ReadableStreamDefaultReader<Uint8Array>;
  #held: Uint8Array = new Uint8Array(0);
  #done = false;

  constructor(body: ReadableStream<Uint8Array> | null) {
    this.#reader = (body ?? new ReadableStream<Uint8Array>({ start: (c) => c.close() }))
      .getReader();
  }

  /** Exactly `size` bytes, or null when the stream ends first. */
  async take(size: number): Promise<Uint8Array | null> {
    const out = new Uint8Array(size);
    let at = 0;
    while (at < size) {
      if (this.#held.byteLength === 0) {
        if (this.#done) {
          return null;
        }
        const { done, value } = await this.#reader.read();
        if (done) {
          this.#done = true;
          continue;
        }
        this.#held = value;
      }
      const part = this.#held.subarray(0, size - at);
      out.set(part, at);
      at += part.byteLength;
      this.#held = this.#held.subarray(part.byteLength);
    }
    return out;
  }

  /** Whether the stream holds no more bytes. */
  async ended(): Promise<boolean> {
    while (this.#held.byteLength === 0 && !this.#done) {
      const { done, value } = await this.#reader.read();
      if (done) {
        this.#done = true;
      } else {
        this.#held = value;
      }
    }
    return this.#held.byteLength === 0;
  }

  async cancel(): Promise<void> {
    await this.#reader.cancel().catch(() => undefined);
  }
}

/** A `Repr-Digest` member for SHA-256 (RFC 9530): `sha-256=:<base64>:`. */
export function reprDigest(digest: Buffer): string {
  return `sha-256=:${digest.toString("base64")}:`;
}

/** The SHA-256 a client's `Repr-Digest` names, or undefined when it names none. */
function claimed(request: Request): string | undefined {
  const header = request.headers.get("Repr-Digest");
  return header === null ? undefined
    : /(?:^|,)\s*sha-256=:([A-Za-z0-9+/=]+):/.exec(header)?.[1] ?? "";
}

/** `PUT files/upload/<intent_id>`: the file's bytes, streamed to `FILES` by the binder. */
export async function streamUpload(request: Request, env: DoorsEnv, { binding, stub }: Bound,
                                   intentId: string): Promise<Response> {
  if (request.method !== "PUT") {
    return failure(405, "method not allowed");
  }
  if ((request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase()
      !== "application/octet-stream") {
    return failure(415, "an upload's bytes are application/octet-stream");
  }
  if (!INTENT.test(intentId)) {
    return failure(404, "no such upload");
  }
  let target: Awaited<ReturnType<typeof stub.fileUploadTarget>>;
  try {
    target = await stub.fileUploadTarget(binding, { intent_id: intentId });
  } catch (error) {
    const refused = refusal(error);
    if (refused === null) {
      throw error;
    }
    return failure(refused.status, refused.message);
  }
  const pending = new Set(target.pending);
  const whole = createHash("sha256");
  const pieces = new Pieces(request.body);
  let batch: { sha256: string; bytes: Uint8Array }[] = [];
  let stored = 0;
  const flush = async (): Promise<void> => {
    if (batch.length === 0) {
      return;
    }
    const sent = batch;
    batch = [];
    await env.FILES.putChunks(target.namespace, sent);
    await stub.fileUploadStored(binding, { intent_id: intentId,
                                           chunks: sent.map(({ sha256 }) => sha256) });
    stored += sent.length;
  };
  for (const [index, chunk] of target.chunks.entries()) {
    if (chunk.size > CHUNK_BYTES) {
      throw new Error("an upload's plan names a chunk larger than a chunk");
    }
    const bytes = await pieces.take(chunk.size);
    if (bytes === null) {
      await flush();
      return failure(400, "the body ended before the declared file did");
    }
    whole.update(bytes);
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    if (sha256 !== chunk.sha256) {
      await pieces.cancel();
      await flush();
      return failure(400, `the bytes of chunk ${index} are not the chunk the upload declared`);
    }
    if (pending.delete(sha256)) {
      batch.push({ sha256, bytes });
      if (batch.length === PER_CALL) {
        await flush();
      }
    }
  }
  await flush();
  if (!await pieces.ended()) {
    await pieces.cancel();
    return failure(400, "the body is longer than the declared file");
  }
  const digest = whole.digest();
  const headers = { "Repr-Digest": reprDigest(digest) };
  const sent = claimed(request);
  if (sent !== undefined && sent !== digest.toString("base64")) {
    return failure(400, "the body's SHA-256 is not the Repr-Digest it was sent with", headers);
  }
  return Response.json({ intent_id: intentId, chunks: target.chunks.length, stored },
                       { headers: { "Cache-Control": "no-store", ...headers } });
}
