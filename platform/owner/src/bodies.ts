/**
 * The placement rule (ADR-035): where a large body lives is the object's own rule, built in,
 * with no switch. A collection row's body, a history entry's and a message's stay in their
 * row up to `PLACE_BYTES` UTF-8 bytes; past it a body leaves the object's SQLite for R2,
 * through the files plane's own path: an upload of one chunk (`Files.begin`, the bytes to
 * `FILES`, recorded stored), then, in the writing unit, the content made and named by the
 * row (`Files.adopt`), whose triggers count the reference (migration 13). So the files
 * plane's quota counts the bytes against the tier's storage (`chunk_bytes`, `max_bytes`),
 * its housekeeping deletes content nothing names, and the erasure deletes every chunk.
 *
 * The order keeps a crash harmless: the bytes reach R2 first, held by an open upload that
 * expires (`INTENT_TTL_S`); the row's reference is committed only after, in one unit with
 * the row, so no row ever names bytes R2 does not hold. A crash between the two leaves an
 * upload nobody adopts: housekeeping releases it at its expiry, its chunk becomes garbage,
 * and the sweep deletes it from R2 after `GC_GRACE_S`. A write the unit refuses or loses
 * its compare-and-swap leaves the same, released at once (`release`).
 *
 * A read takes the rows' references in its own read, then the bodies from `FILES`, each
 * chunk rechecked against its hash, outside any unit: a body a later write let go stays in
 * R2 for its grace, so a read that raced the write still finds it.
 */
import { createHash } from "node:crypto";

import { Invalid } from "./errors";
import type { Files } from "./files";
import type { OwnerStorage, Write } from "./gate";
import { CHUNK_BYTES, PLACE_BYTES } from "./manifest";

export { PLACE_BYTES } from "./manifest";

/**
 * The most body bytes one page of a read returns: a pull, a history or a mailbox read stops
 * before a body that would take it past, and returns at least one. A page, not a bound on
 * what is held: two chunks, a `FILES` call's own most (`BATCH_BYTES`), so a page's bodies
 * are one call and fit the isolate's memory with their copies.
 */
export const READ_PAGE_BYTES = 2 * CHUNK_BYTES;

/** A body placed in R2 for a write still to commit: its upload, and the chunk it holds. */
export interface Placed {
  readonly intent_id: string;
  readonly sha256: string;
  readonly bytes: number;
}

/** A placed body begun, its bytes still to send (`Bodies.begin`). */
export interface Placing extends Placed {
  readonly data: Uint8Array;
  /** Whether its bytes are to be sent: not when R2 holds them for its writer already. */
  readonly send: boolean;
}

/** Placed contents' chunks, by content id (`Bodies.locate`). */
export type Located = ReadonlyMap<string, string>;

/** What a writing unit does with a placed body: its content adopted, the id to name. */
export interface Adopt {
  (placed: Placed, writer: string): string;
}

/** A chunk's hash: its bytes' SHA-256, as `FILES` names and checks it. */
function sha256Hex(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/** A body's UTF-8 bytes. */
export function bodyBytes(body: string): Uint8Array {
  return new TextEncoder().encode(body);
}

/** Whether a body of `bytes` UTF-8 bytes is placed in R2 rather than kept in its row. */
export function placed(bytes: number): boolean {
  return bytes > PLACE_BYTES;
}

/**
 * In a unit, the content id a row names for `body`: its placed upload adopted, held to be
 * that body's own; null for a body its row keeps.
 */
export function adopted(body: string, place: Placed | null | undefined, writer: string,
                        adopt: Adopt | undefined): string | null {
  const bytes = bodyBytes(body);
  if (!placed(bytes.byteLength)) {
    return null;
  }
  if (place === null || place === undefined || adopt === undefined) {
    throw new Error(`a body of more than ${PLACE_BYTES} bytes is placed before its write`);
  }
  if (place.bytes !== bytes.byteLength || place.sha256 !== sha256Hex(bytes)) {
    throw new Error("a placed body is not the body written");
  }
  return adopt(place, writer);
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
  readonly adopt: Adopt = (place, writer) => this.#files.adopt(place.intent_id, writer);

  /**
   * In a unit (the door's first, its caller checked): a body past `PLACE_BYTES` begun as
   * `writer`'s upload of one chunk, held and bounded before any byte is sent; null for a
   * body its row keeps. `send` then sends it, its write adopts it, and `release` ends it
   * whatever the write did.
   */
  begin(body: string, writer: string): Placing | null {
    const bytes = bodyBytes(body);
    if (!placed(bytes.byteLength)) {
      return null;
    }
    if (bytes.byteLength > CHUNK_BYTES) {
      throw new Invalid(`a body is at most ${CHUNK_BYTES} bytes`);
    }
    const sha256 = sha256Hex(bytes);
    const { intent_id: intentId, missing } = this.#files.begin(bytes.byteLength, [sha256],
                                                               writer);
    return { intent_id: intentId, sha256, bytes: bytes.byteLength, data: bytes,
             send: missing.length > 0 };
  }

  /** Outside a unit: a begun body's bytes to R2 (`FILES`), recorded stored in a unit. */
  async send(placing: Placing): Promise<Placed> {
    const place: Placed = { intent_id: placing.intent_id, sha256: placing.sha256,
                            bytes: placing.bytes };
    try {
      if (placing.send) {
        await this.#files.upload(placing.intent_id, [{ sha256: placing.sha256,
                                                       bytes: placing.data }]);
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
   * The chunk each placed content in `contents` is, by content id: a read, run in the
   * same unit or turn as the read of the rows that name them, so each is the content they
   * named.
   */
  locate(contents: readonly string[]): Located {
    const located = new Map<string, string>();
    for (const contentId of new Set(contents)) {
      const found = this.#storage.sql.exec<{ sha256: string }>(
        "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx",
        contentId).toArray();
      if (found.length !== 1) {
        throw new Error("a placed body's content is not one chunk the object holds");
      }
      located.set(contentId, found[0]!.sha256);
    }
    return located;
  }

  /**
   * The located bodies, by content id: each one chunk read from R2 (`FILES` rechecks its
   * hash), decoded as the UTF-8 it was written as. Outside any unit; a body a later write
   * let go stays in R2 for its garbage grace, so a read that raced the write still finds it.
   */
  async fetch(located: Located): Promise<Map<string, string>> {
    if (located.size === 0) {
      return new Map();
    }
    const ids = [...located.keys()];
    const read = await this.#files.fetch(ids.map((contentId) => ({
      sha256: located.get(contentId)! })));
    const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
    return new Map(ids.map((contentId, index) => [contentId, decoder.decode(read[index])]));
  }
}
