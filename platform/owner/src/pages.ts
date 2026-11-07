/**
 * Every read's page, fitted to what one answer holds (`RESULT_CHARS`, `encoded.ts`).
 *
 * A page takes its items in order while their JSON fits, counted exactly without building
 * it (`jsonBytes`), and always its first. The first item's body, when it does not fit whole,
 * is cut at a character to fit and says so: `body_bytes` (the whole body's UTF-8 bytes),
 * `body_next_offset` (where the rest starts, null at the end) and `truncated`. A read by
 * range (`body_offset`) returns the rest, verbatim (Fact 9), so nothing is ever out of reach.
 * The item's other fields are never cut: one whose fields alone pass the answer (a path of
 * thousands of escaped characters, say) is refused, told (`result_too_large`), never cut
 * silently.
 */
import type { Window } from "./bodies";
import { FIT_SLACK, fitted, jsonBytes, RESULT_CHARS } from "../../contract/src/encoded";
import { Refused } from "../../contract/src/errors";

/** The bytes a page fits its items to: the answer, less what its last fields may add. */
export const PAGE_BYTES = RESULT_CHARS - FIT_SLACK;

export const RESULT_TOO_LARGE = `result_too_large: this item's fields alone pass the ${
  RESULT_CHARS} characters one answer holds`;

/** What a body cut to fit, or read from an offset, says of itself. */
export interface Cut {
  /** The whole body's UTF-8 bytes. */
  readonly body_bytes: number;
  /** The byte the rest starts at, for a read by `body_offset`; null at the end. */
  readonly body_next_offset: number | null;
  /** Whether the body goes on past what this answer holds. */
  readonly truncated: boolean;
}

/** An item as a page shows it, and the bytes of its JSON. */
export interface Fitted<T> {
  readonly item: T;
  readonly bytes: number;
}

/**
 * `item` with its `body` from `window`, in at most `room` bytes of JSON. Whole when it fits
 * and the window is the whole body; otherwise only the page's first is answered, its body
 * cut to fit (with `Cut`'s fields); a later one that does not fit whole is null, so the page
 * ends before it.
 */
export function bodied<T extends { readonly body: string | null }>(
    item: T, window: Window, room: number, first: boolean): Fitted<T & Partial<Cut>> | null {
  if (window.offset === 0 && window.end === window.size) {
    const whole = { ...item, body: window.text };
    const bytes = jsonBytes(whole);
    if (bytes <= room) {
      return { item: whole, bytes };
    }
  }
  if (!first) {
    return null;
  }
  // Its fields at their widest (review L1): `false` over `true`, and the wider of `null`
  // and the size's digits for where the rest starts.
  const meta = jsonBytes({ ...item, body: "", body_bytes: window.size,
                           body_next_offset: String(window.size).length >= 4 ? window.size
                             : null, truncated: false });
  const part = fitted(window.text, room - meta + 2);
  if (room - meta + 2 < 2 || (part.bytes === 0 && window.offset < window.size)) {
    throw new Refused(RESULT_TOO_LARGE);
  }
  const next = window.offset + part.bytes;
  const cut = { ...item, body: part.text, body_bytes: window.size,
                body_next_offset: next < window.size ? next : null,
                truncated: next < window.size };
  return { item: cut, bytes: jsonBytes(cut) };
}

/** An item answered as it is, when its JSON fits `room`. */
export function whole<T>(item: T, room: number): Fitted<T> | null {
  const bytes = jsonBytes(item);
  return bytes <= room ? { item, bytes } : null;
}

/**
 * The items of a page whose other fields take `fixed` bytes (its JSON with the list empty),
 * in order while they fit `PAGE_BYTES`, and always the first (`entry` cuts it to fit, or
 * the answer is refused `result_too_large`).
 */
export function fitPage<I, O>(fixed: number, items: readonly I[],
                              entry: (item: I, room: number, first: boolean) => Fitted<O> | null,
                              bytes: number = PAGE_BYTES): O[] {
  const page: O[] = [];
  let used = fixed;
  for (const item of items) {
    const comma = page.length > 0 ? 1 : 0;
    const room = bytes - used - comma;
    const fit = room > 0 ? entry(item, room, page.length === 0) : null;
    if (fit === null || fit.bytes > room) {
      if (page.length === 0) {
        throw new Refused(RESULT_TOO_LARGE);
      }
      break;
    }
    used += comma + fit.bytes;
    page.push(fit.item);
  }
  return page;
}

/**
 * A list answer fitted to `PAGE_BYTES`: `value` as it is when it fits; else its `items`
 * while they fit, at least one, in the answer `rebuild` makes of them (naming where the next
 * page starts after the last one kept), `empty` being the answer with no items.
 */
export function fitList<V, I>(value: V, items: readonly I[], empty: V,
                              rebuild: (kept: I[]) => V): V {
  if (items.length === 0 || jsonBytes(value) <= PAGE_BYTES) {
    return value;
  }
  const kept = fitPage(jsonBytes(empty), items, (item, room) => whole(item, room));
  return kept.length === items.length ? value : rebuild(kept);
}
