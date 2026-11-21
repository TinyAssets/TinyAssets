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
 *
 * A door with a body is placed, read or reread here (`placing`, `reading`, `rereading`,
 * `rereadingPage`), each step a unit of the gate's `write` and the R2 calls between; a door
 * that rings an agent syncs the schedule itself, after.
 */
import { utf8Decoder } from "../../contract/src/text";
import { sha256Hex } from "../../contract/src/digest";

import { FIT_SLACK, RESULT_CHARS } from "../../contract/src/encoded";
import { Refusal } from "../../contract/src/refusal";
import type { Files } from "./files";
import { streamChunks } from "./segments";
import type { OwnerStorage, Write } from "./gate";
import { bodyUnits, ownerSlots, WINDOW_UNITS } from "./holds";
import { BODY_BYTES_MAX, CHUNK_BYTES, PLACE_BYTES } from "../../contract/src/limits";
import { slotted } from "../../contract/src/slots";
import { OWNER } from "./authority";
import type { Shown, Wants } from "./pages";


/**
 * The most body bytes a digest's rows name past its first (heads show only each body's
 * start, so their bodies' sizes are not the page's): the bucket reads one digest makes, a
 * window of each placed body's first chunk, stay within two chunks (`BATCH_BYTES`).
 */
export const READ_PAGE_BYTES = 2 * CHUNK_BYTES;

/** The bytes a page fits its items to: the answer, less what its last fields may add. */
export const PAGE_BYTES = RESULT_CHARS - FIT_SLACK;

/**
 * How often a mailbox read reads its placed bodies again when mail that came between its
 * read of them and its unit put an unread placed body first (ADR-035).
 */
export const READ_TRIES = 3;

/**
 * The window of `bytes` from `offset`, at most `max` bytes, cut back to a UTF-8 character
 * boundary: its end, or null when `offset` is inside a character. A window that holds less
 * than one whole character is widened to hold it. It lives here, a leaf of the pages and
 * the search that read it: in the search, it made an import cycle (pages, bodies, search)
 * that set the search's answer bytes from the pages' before they were set.
 */
export function windowEnd(bytes: Uint8Array, offset: number, max: number): number | null {
  const continuation = (at: number) => at < bytes.byteLength && (bytes[at]! & 0xc0) === 0x80;
  if (continuation(offset)) {
    return null;
  }
  let end = Math.min(bytes.byteLength, offset + max);
  while (end > offset && continuation(end)) {
    end -= 1;
  }
  if (end === offset && offset < bytes.byteLength) {
    end = offset + 1;
    while (continuation(end)) {
      end += 1;
    }
  }
  return end;
}

/**
 * The most bytes of placed bodies' windows one read holds: the rows past a page's first fit
 * `RESULT_CHARS` whole, and a head's window is at most four bytes a character it shows, so
 * a page never needs more; the windows past it are not read, and the page ends before them.
 */
export const WINDOWS_BYTES = 4 * RESULT_CHARS;

/**
 * A window of a body's text: its UTF-8 bytes from `offset` to `end`, cut at characters, of a
 * body of `size` bytes. A whole body is the window from 0 to its size.
 */
export interface Window {
  readonly text: string;
  readonly offset: number;
  readonly end: number;
  readonly size: number;
}

const BODY_OFFSET_INSIDE =
  "body_offset is inside a character: start at a body_next_offset an answer gave";

/** A body its row keeps, as the window from `offset` to its end. */
export function inlineWindow(body: string, offset: number): Window {
  const bytes = bodyBytes(body);
  if (offset === 0) {
    return { text: body, offset: 0, end: bytes.byteLength, size: bytes.byteLength };
  }
  if (offset > bytes.byteLength) {
    throw new Refusal("invalid", "body_offset is past the body's end");
  }
  if (offset < bytes.byteLength && (bytes[offset]! & 0xc0) === 0x80) {
    throw new Refusal("invalid", BODY_OFFSET_INSIDE);
  }
  return { text: utf8Decoder("keep")
             .decode(bytes.subarray(offset)),
           offset, end: bytes.byteLength, size: bytes.byteLength };
}

/** A read's `body_offset`: a whole number of bytes, 0 when omitted. */
export function bodyOffset(value: unknown): number {
  if (value === undefined) {
    return 0;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Refusal("invalid", "body_offset is a whole number of bytes");
  }
  return value;
}

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

/** Placed contents' chunks in order and their bytes, by content id (`Bodies.locate`). */
export type Located = ReadonlyMap<string, { readonly chunks: readonly string[];
                                            readonly size: number }>;

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
    chunks.push({ sha256: sha256Hex(piece), bytes: piece });
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
                           adopt: Adopt, name: (contentId: string | null) => T): T {
  const bytes = bodyBytes(body);
  if (!placed(bytes.byteLength)) {
    return name(null);
  }
  if (place === null || place === undefined) {
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
      throw new Refusal("invalid", `a body is at most ${BODY_BYTES_MAX} bytes`);
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
   * A write door with a body (ADR-035). Its first unit checks its caller and request
   * (`prepare`, the body and the writer it is placed as); a body its row keeps is written
   * in that same unit (`act`). A body past `PLACE_BYTES` is begun there instead (a chunk of
   * it the sweep's delete in flight takes waits for that delete, `Files.deleteLanded`; one a
   * failed delete left is refused, `chunk_deleting`, to begin again later), sent to R2,
   * and written in a second unit, which checks the caller again and adopts it; its upload
   * is released after, whatever the unit did (none once adopted). A conflict's current row,
   * placed, is read back as a read door reads it (`shown`).
   */
  async placing<T>(prepare: () => { body: unknown; writer: string } | { done: T },
                   act: (placed: Placed | null) => T, shown?: Shown<T>): Promise<T> {
    const located = (value: T) => ({ value, located: this.locate(
      shown === undefined ? [] : shown.contents(value)) });
    type First = { done: { value: T; located: Located } } | { placing: Placing };
    // Whose files scope the write's bodies count in: the tier's one storage number holds
    // them, inline or placed, retention making room first (`Files.held`).
    let writer = OWNER;
    let first: First | { landing: Promise<void> };
    for (;;) {
      first = await this.#write((): First | { landing: Promise<void> } => {
        const prepared = prepare();
        if ("done" in prepared) {
          return { done: located(prepared.done) };
        }
        writer = prepared.writer;
        let placing: Placing | null;
        try {
          placing = this.begin(prepared.body as string, prepared.writer);
        } catch (error) {
          // Read in the unit that met it: the delete that takes the chunk, or none (left by
          // one that failed), never a delete that landed since.
          const landing = error instanceof Refusal && error.code === "chunk_deleting"
            ? this.#files.deleteLanded(chunked(bodyBytes(prepared.body as string))
              .map((chunk) => chunk.sha256))
            : null;
          if (landing === null) {
            throw error;
          }
          return { landing };
        }
        return placing === null ? { done: located(this.#files.held(writer, () => act(null))) }
          : { placing };
      });
      if (!("landing" in first)) {
        break;
      }
      await first.landing;
    }
    let done: { value: T; located: Located };
    if ("done" in first) {
      done = first.done;
    } else {
      const placed = await this.send(first.placing);
      try {
        done = await this.#write(() => located(this.#files.held(writer, () => act(placed))));
      } finally {
        await this.release(placed);
      }
    }
    return shown === undefined || done.located.size === 0 ? done.value
      : shown.fill(done.value, await this.windows(done.located, () => PAGE_BYTES));
  }

  /**
   * A read door whose unit marks what it returns (a mailbox read, ADR-035): the placed
   * bodies it would return located in a unit of their own (`locate`, its caller checked),
   * read from R2 (`fetch`: windows for a page, or whole for a run's input), then its unit
   * (`read`, the caller checked again) handed them; it reads again, a few times at most,
   * while `again` says mail between put an unread placed body first.
   */
  async rereading<T, B>(locate: () => readonly string[], read: (bodies: B) => T,
                        again: (value: T) => boolean,
                        fetch: (located: Located) => Promise<B>): Promise<T> {
    for (let tries = 0; ; tries++) {
      const located = await this.#write(() => this.locate(locate()));
      const bodies = await fetch(located);
      const value = await this.#write(() => read(bodies));
      if (!again(value) || tries >= READ_TRIES) {
        return value;
      }
    }
  }

  /**
   * `rereading` for a page: its bodies' windows (`Wants`: each at most `maxOf` bytes, from
   * `offsetOf`).
   */
  rereadingPage<T>(locate: () => Wants, read: (windows: ReadonlyMap<string, Window>) => T,
                   again: (value: T) => boolean): Promise<T> {
    let wants: Wants = { contents: [] };
    return this.rereading(() => {
      wants = locate();
      return wants.contents;
    }, read, again, (located) => this.windows(located, wants.maxOf ?? (() => PAGE_BYTES),
                                              wants.offsetOf));
  }

  /**
   * A read door's unit, then the windows of the placed bodies its rows name read from R2
   * (ADR-035), and handed to `fill`, which fits the page to one answer: `unit` returns its
   * result and the content ids it names, located in the same unit, so each is the content
   * its row named.
   */
  async reading<T, U = T>(unit: () => { value: T } & Wants,
                          fill: (value: T, windows: ReadonlyMap<string, Window>) => U):
      Promise<U> {
    const { value, located, wants } = await this.#write(() => {
      const read = unit();
      return { value: read.value, located: this.locate(read.contents), wants: read };
    });
    return fill(value, located.size === 0 ? new Map()
      : await this.windows(located, wants.maxOf ?? (() => PAGE_BYTES), wants.offsetOf));
  }

  /**
   * The chunks each placed content in `contents` is cut into, and its bytes, by content id:
   * a read, run in the same unit as the read of the rows that name them, so each is the
   * content they named.
   */
  locate(contents: readonly string[]): Located {
    const located = new Map<string, { chunks: readonly string[]; size: number }>();
    for (const contentId of new Set(contents)) {
      const found = this.#storage.sql.exec<{ sha256: string }>(
        "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx",
        contentId).toArray().map((row) => row.sha256);
      const size = this.#storage.sql.exec<{ size: number }>(
        "SELECT size FROM file_contents WHERE content_id = ?", contentId).toArray()[0]?.size;
      if (found.length === 0 || size === undefined) {
        throw new Error("a placed body's content is not one the object holds");
      }
      located.set(contentId, { chunks: found, size });
    }
    return located;
  }

  /**
   * The located bodies read whole, by content id (a run's mail input): their chunks read
   * from R2 (`FILES` rechecks each hash) and decoded as the UTF-8 they were written as.
   * Outside any unit.
   */
  async fetch(located: Located): Promise<Map<string, string>> {
    const bodies = new Map<string, string>();
    for (const [contentId, { chunks }] of located) {
      // A body of at most two chunks (`BODY_BYTES_MAX`), its bytes and its text held in a
      // step weighted by its chunks (`bodyUnits`, `holds.ts`).
      bodies.set(contentId, await slotted(ownerSlots(), bodyUnits(chunks.length), async () => {
        const decoder = utf8Decoder("keep");
        const parts = await this.#files.fetch(chunks.map((sha256) => ({ sha256 })));
        let text = "";
        for (const part of parts) {
          text += decoder.decode(part, { stream: true });
        }
        return text + decoder.decode();
      }));
    }
    return bodies;
  }

  /**
   * A window of each located body, by content id, in order: its text from `offsetOf(id)`,
   * at most `maxOf(id)` bytes, cut back to a character, read by the verified ranges it spans
   * (`FILES` rechecks each whole chunk's hash and answers the range). The windows
   * stop once they hold `WINDOWS_BYTES`, always past the first: a body not read ends its
   * page before it. Outside any unit.
   */
  async windows(located: Located, maxOf: (contentId: string) => number,
                offsetOf: (contentId: string) => number = () => 0): Promise<Map<string, Window>> {
    const windows = new Map<string, Window>();
    if (located.size === 0) {
      return windows;
    }
    // The windows read in one step of `WINDOW_UNITS` (`holds.ts`): each is at most a page,
    // its chunks read by verified ranges (the files Worker holds the whole chunk), so a unit
    // is more than they hold, and a one-unit hold never waits behind larger ones.
    return slotted(ownerSlots(), WINDOW_UNITS, async () => {
      let held = 0;
      for (const [contentId, { chunks, size }] of located) {
        if (windows.size > 0 && held >= WINDOWS_BYTES) {
          break;
        }
        const window = await this.windowAt(chunks, 0, size, offsetOf(contentId),
                                           maxOf(contentId));
        held += window.end - window.offset;
        windows.set(contentId, window);
      }
      return windows;
    });
  }

  /**
   * A window of a body of `size` bytes that starts `base` bytes into the content its
   * chunks make (0 for a placed body; an archived one's place in its segment): its text from
   * `offset`, at most `max` bytes, cut back to a character, read by the verified ranges it
   * spans. Outside any unit.
   */
  async windowAt(chunks: readonly string[], base: number, size: number, offset: number,
                 max: number): Promise<Window> {
    if (offset > size) {
      throw new Refusal("invalid", "body_offset is past the body's end");
    }
    const end = Math.min(size, offset + max + 3);  // a character's tail past the window
    if (end === offset) {
      return { text: "", offset, end, size };
    }
    const bytes = await this.#files.range(streamChunks(chunks), base + offset, base + end);
    const cut = windowEnd(bytes, 0, end === size ? end - offset : max);
    if (cut === null) {
      throw new Refusal("invalid", BODY_OFFSET_INSIDE);
    }
    return { text: utf8Decoder("keep")
               .decode(bytes.subarray(0, cut)),
             offset, end: offset + cut, size };
  }
}
