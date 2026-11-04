/**
 * The placement rule (ADR-035): where a large body lives is the object's own rule, built in,
 * with no switch. A collection row's body, a history entry's and a message's stay in their
 * row up to `PLACE_BYTES` UTF-8 bytes; past it a body leaves the object's SQLite for R2,
 * through the files plane's own path: an upload cut into 4 MiB chunks (`Files.begin`, the
 * bytes to `FILES`, recorded stored), then, in the writing unit, the content made and named
 * by the row (`Files.adopt`), whose triggers count the reference on the content and in its
 * writer's files scope (migration 14). So the files plane's quota counts the bytes against
 * the tier's storage (`chunk_bytes`, `max_bytes`) and an outside writer's share of it
 * (`client_share`, `owner_reserve`), its housekeeping deletes content nothing names, and the
 * erasure deletes every chunk.
 *
 * The order keeps a crash harmless: the bytes reach R2 first, held by an open upload that
 * expires in minutes (`PLACE_TTL_S`); the row's reference is committed only after, in one
 * unit with the row, so no row ever names bytes R2 does not hold. A crash between the two
 * leaves an upload nobody adopts: housekeeping releases it at its expiry, its chunks become
 * garbage, and the sweep deletes them from R2 after `GC_GRACE_S`. A write the unit refuses
 * or loses its compare-and-swap leaves the same, released at once (`release`).
 *
 * A read locates the rows' contents in its own unit, then reads the bodies from `FILES`,
 * each chunk rechecked against its hash, outside any unit: a body a later write let go stays
 * in R2 for its grace, so a read that raced the write still finds it.
 */
import { createHash } from "node:crypto";

import { Invalid } from "./errors";
import type { Files } from "./files";
import type { OwnerStorage, Write } from "./gate";
import { CHUNK_BYTES, PLACE_BYTES } from "./manifest";

export { PLACE_BYTES } from "./manifest";

/**
 * The most bytes a body takes: half of a request body's one ceiling (`network.ts`'s
 * `EGRESS_BODY_MAX`, 16 MiB, the isolate's 128 MB), so the body with its JSON escapes fits
 * the tool call that carries it; two chunks. A technical ceiling; a larger thing is a file,
 * uploaded a chunk at a time.
 */
export const BODY_BYTES_MAX = 2 * CHUNK_BYTES;

/**
 * The most body bytes one page of a read returns: a pull, a history or a mailbox read stops
 * before a body that would take it past, and returns at least one. A page, not a bound on
 * what is held: two chunks, a `FILES` call's own most (`BATCH_BYTES`).
 */
export const READ_PAGE_BYTES = 2 * CHUNK_BYTES;

/** A body placed in R2 for a write still to commit: its upload, and the chunks it holds. */
export interface Placed {
  readonly intent_id: string;
  readonly chunks: readonly string[];
  readonly bytes: number;
}

/** A placed body begun, its bytes still to send (`Bodies.begin`). */
export interface Placing extends Placed {
  readonly data: Uint8Array;
  /** The chunks R2 does not yet hold for its writer: the ones to send. */
  readonly missing: readonly string[];
}

/** Placed contents' chunks in order, by content id (`Bodies.locate`). */
export type Located = ReadonlyMap<string, readonly string[]>;

/**
 * What a writing unit does with a placed body: its content adopted, and `name` run with the
 * content's id, the writer's row naming it, in the same change (`Files.adopt`).
 */
export type Adopt = <T>(placed: Placed, writer: string, name: (contentId: string) => T) => T;

/** A body's UTF-8 bytes. */
export function bodyBytes(body: string): Uint8Array {
  return new TextEncoder().encode(body);
}

/** Whether a body of `bytes` UTF-8 bytes is placed in R2 rather than kept in its row. */
export function placed(bytes: number): boolean {
  return bytes > PLACE_BYTES;
}

/** A body's bytes cut into 4 MiB chunks, each with its hash, as `FILES` names them. */
function chunked(bytes: Uint8Array): { sha256: string; bytes: Uint8Array }[] {
  const chunks: { sha256: string; bytes: Uint8Array }[] = [];
  for (let at = 0; at < bytes.byteLength; at += CHUNK_BYTES) {
    const piece = bytes.subarray(at, Math.min(at + CHUNK_BYTES, bytes.byteLength));
    chunks.push({ sha256: createHash("sha256").update(piece).digest("hex"), bytes: piece });
  }
  return chunks;
}

/** Whether `place` is `body`'s own placed upload: its bytes and its chunks' hashes. */
export function placedAs(place: Placed | null, body: string): boolean {
  const bytes = bodyBytes(body);
  return place !== null && place.bytes === bytes.byteLength
    && place.chunks.join() === chunked(bytes).map((chunk) => chunk.sha256).join();
}

/**
 * In a unit: `name` run with the content id a row names for `body`, its placed upload
 * adopted and held to be that body's own; with null for a body its row keeps.
 */
export function adopted<T>(body: string, place: Placed | null | undefined, writer: string,
                           adopt: Adopt | undefined, name: (contentId: string | null) => T): T {
  const bytes = bodyBytes(body);
  if (!placed(bytes.byteLength)) {
    return name(null);
  }
  if (place === null || place === undefined || adopt === undefined) {
    throw new Error(`a body of more than ${PLACE_BYTES} bytes is placed before its write`);
  }
  if (!placedAs(place, body)) {
    throw new Error("a placed body is not the body written");
  }
  return adopt(place, writer, name);
}

export class Bodies {
  readonly #files: Files;
  readonly #storage: OwnerStorage;
  readonly #write: Write;

  constructor(files: Files, storage: OwnerStorage, write: Write) {
    this.#files = files;
    this.#storage = storage;
    this.#write = write;
  }

  /** In a unit: a placed body's content made for the row naming it (`Files.adopt`). */
  readonly adopt: Adopt = (place, writer, name) =>
    this.#files.adopt(place.intent_id, writer, name);

  /**
   * In a unit (the door's first, its caller checked): a body past `PLACE_BYTES` begun as
   * `writer`'s upload, held and bounded before any byte is sent; null for a body its row
   * keeps. `send` then sends it, its write adopts it, and `release` ends it whatever the
   * write did.
   */
  begin(body: string, writer: string): Placing | null {
    const bytes = bodyBytes(body);
    if (!placed(bytes.byteLength)) {
      return null;
    }
    if (bytes.byteLength > BODY_BYTES_MAX) {
      throw new Invalid(`a body is at most ${BODY_BYTES_MAX} bytes`);
    }
    const chunks = chunked(bytes).map((chunk) => chunk.sha256);
    const { intent_id: intentId, missing } = this.#files.begin(bytes.byteLength, chunks, writer,
                                                               [], true);
    return { intent_id: intentId, chunks, bytes: bytes.byteLength, data: bytes, missing };
  }

  /** Outside a unit: a begun body's bytes to R2 (`FILES`), two chunks a call. */
  async send(placing: Placing): Promise<Placed> {
    const place: Placed = { intent_id: placing.intent_id, chunks: placing.chunks,
                            bytes: placing.bytes };
    try {
      const wanted = new Set(placing.missing);
      const puts = chunked(placing.data).filter((chunk) => wanted.delete(chunk.sha256));
      for (let at = 0; at < puts.length; at += 2) {
        await this.#files.upload(placing.intent_id, puts.slice(at, at + 2));
      }
    } catch (error) {
      await this.release(place);
      throw error;
    }
    return place;
  }

  /** After its write, whatever it did: a placed body's upload ended (none once adopted). */
  async release(place: Placed | null): Promise<void> {
    if (place !== null) {
      await this.#write(() => this.#files.abort(place.intent_id));
    }
  }

  /**
   * The chunks each placed content in `contents` is cut into, by content id: a read, run in
   * the same unit as the read of the rows that name them, so each is the content they named.
   */
  locate(contents: readonly string[]): Located {
    const located = new Map<string, readonly string[]>();
    for (const contentId of new Set(contents)) {
      const found = this.#storage.sql.exec<{ sha256: string }>(
        "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx",
        contentId).toArray().map((row) => row.sha256);
      if (found.length === 0) {
        throw new Error("a placed body's content is not one the object holds");
      }
      located.set(contentId, found);
    }
    return located;
  }

  /**
   * The located bodies, by content id: their chunks read from R2 (`FILES` rechecks each
   * hash), joined and decoded as the UTF-8 they were written as. Outside any unit.
   */
  async fetch(located: Located): Promise<Map<string, string>> {
    const bodies = new Map<string, string>();
    const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
    for (const [contentId, chunks] of located) {
      const parts = await this.#files.fetch(chunks.map((sha256) => ({ sha256 })));
      const whole = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
      let at = 0;
      for (const part of parts) {
        whole.set(part, at);
        at += part.byteLength;
      }
      bodies.set(contentId, decoder.decode(whole));
    }
    return bodies;
  }

  /**
   * The located bodies' bytes, by content id, as the chunks they were read in (never joined,
   * so a move holds each body once): read from R2, each chunk rechecked. Outside any unit.
   */
  async fetchParts(located: Located): Promise<Map<string, Uint8Array[]>> {
    const bodies = new Map<string, Uint8Array[]>();
    for (const [contentId, chunks] of located) {
      bodies.set(contentId, await this.#files.fetch(chunks.map((sha256) => ({ sha256 }))));
    }
    return bodies;
  }
}
