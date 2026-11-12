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
import { type Window, inlineWindow } from "./bodies";
import type { Changed, Changes, Row } from "./collections";
import { FIT_SLACK, fitted, jsonBytes, RESULT_CHARS } from "../../contract/src/encoded";
import { Refusal } from "../../contract/src/refusal";

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
interface Fitted<T> {
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
    throw new Refusal("result_too_large", RESULT_TOO_LARGE);
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
        throw new Refusal("result_too_large", RESULT_TOO_LARGE);
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

/**
 * What a page's unit names to read from R2: the placed bodies' content ids, and, by content
 * id, the most bytes of each window (one answer's when omitted) and the byte it starts at
 * (0 when omitted: the page's first item's `body_offset`).
 */
export interface Wants {
  readonly contents: readonly string[];
  readonly maxOf?: (contentId: string) => number;
  readonly offsetOf?: (contentId: string) => number;
}

// -- The doors' pages: digests' heads, rows and entries read from their windows, runs, the
// agents list and a conflict's current row, each fitted to one answer.

/**
 * A row as a digest shows it, the head of its body and the body's UTF-8 bytes (`body_bytes`,
 * as a cut body's); or its deletion.
 */
export type RowHead =
  | (Omit<Extract<Changed, { deleted: false }>, "body">
     & { readonly head: string; readonly body_bytes: number })
  | Extract<Changed, { deleted: true }>;

/** Rows changed past a digest's cursor, and the next cursor; `reset` as `Changes`'. */
export interface Heads {
  readonly cursor: number;
  readonly rows: RowHead[];
  readonly reset?: true;
}

/** A digest's head of each body, in characters, unless the caller names another. */
const HEAD_CHARS = 512;

/** A digest's `head_chars`: a whole number of characters, `HEAD_CHARS` when omitted. */
export function headChars(value: unknown): number {
  if (value === undefined) {
    return HEAD_CHARS;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Refusal("invalid", "head_chars is a whole number of characters");
  }
  return value;
}

/** What each of an inbox digest's two parts, its mail and its rooms, fits: half an answer. */
export const INBOX_PART = (PAGE_BYTES - 32) / 2;

/** A head's window of a placed body: at most four bytes a character, within one answer. */
export function headWindow(chars: number): number {
  return Math.min(4 * chars, PAGE_BYTES);
}

/**
 * Changed rows as a digest shows them, fitted to one answer (`pages.ts`): each body cut to
 * its first `chars` characters, read from its row or its placed body's window; the rows in
 * order while they fit, at least one, the first's head cut further when it does not; the
 * page ends before a placed body not read, and when rows are left the cursor is the last
 * row's change.
 */
export function heads(changes: Changes, chars: number, windows: ReadonlyMap<string, Window>,
               bytes: number = PAGE_BYTES): Heads {
  const rows: { row: RowHead }[] = [];
  for (const row of changes.rows) {
    if (row.deleted) {
      rows.push({ row });
      continue;
    }
    const { body, content_id: contentId, ...rest } =
      row as Extract<Changed, { deleted: false }> & { content_id?: string };
    const window = contentId === undefined ? inlineWindow(body, 0) : windows.get(contentId);
    if (window === undefined) {
      break;
    }
    const head = [...window.text.slice(0, 2 * chars)].slice(0, chars).join("");
    rows.push({ row: { ...rest, head, body_bytes: window.size } });
  }
  const reset = changes.reset === true ? { reset: true as const } : {};
  const fixed = jsonBytes({ ...reset, cursor: changes.cursor, rows: [] });
  const kept = rows.length === 0 ? [] : fitPage(fixed, rows, ({ row }, room, first) => {
    const fit = whole(row, room);
    if (fit !== null || !first || row.deleted) {
      return fit;
    }
    const meta = jsonBytes({ ...row, head: "" });
    if (room - meta + 2 < 2) {
      throw new Refusal("result_too_large", RESULT_TOO_LARGE);
    }
    return whole({ ...row, head: fitted(row.head, room - meta + 2).text }, room);
  }, bytes);
  if (kept.length === 0 && changes.rows.length > 0) {
    throw new Error("a digest's first row is always read");
  }
  return { ...reset,
           cursor: kept.length === changes.rows.length ? changes.cursor : kept.at(-1)!.change,
           rows: kept };
}

/**
 * A row or entry, its body taken from its row or its placed body's window (`body_offset`
 * from `offset`), as a page fits it; null for a placed body not read.
 */
export function windowed<T extends { readonly body: string; readonly content_id?: string }>(
    row: T, windows: ReadonlyMap<string, Window>, offset = 0):
    { item: Omit<T, "content_id">; window: Window } | null {
  const { content_id: contentId, ...item } = row;
  const window = contentId === undefined ? inlineWindow(row.body, offset)
    : windows.get(contentId);
  return window === undefined ? null : { item, window };
}

/** Rows or entries with their windows, in order, up to the first placed body not read. */
export function windowedAll<T extends { readonly body: string; readonly content_id?: string }>(
    rows: readonly T[], windows: ReadonlyMap<string, Window>, offset = 0):
    { item: Omit<T, "content_id">; window: Window }[] {
  const out: { item: Omit<T, "content_id">; window: Window }[] = [];
  for (const [index, row] of rows.entries()) {
    const read = windowed(row, windows, index === 0 ? offset : 0);
    if (read === null) {
      break;
    }
    out.push(read);
  }
  return out;
}

/**
 * One row as an answer shows it, fitted (`pages.ts`), beside the answer's other fields
 * (`fixed` bytes).
 */
export function fittedRow<T extends { readonly body: string }>(
    read: { item: T; window: Window } | null, fixed: number): T & Partial<Cut> {
  if (read === null) {
    throw new Error("a placed body its row names was not read");
  }
  return bodied(read.item, read.window, PAGE_BYTES - fixed, true)!.item;
}

/** The agents list's three lists, in the order a page reads them. */
const AGENT_LISTS = Object.freeze(["agents", "owner_agents", "apps"] as const);

/** An agent, an owner's agent, or an app, as the agents list keys each. */
type Keyed = { readonly agent_id: string } | { readonly name: string };

/** Each list's stable key: an agent's id, an owner agent's id, an app's name. */
function agentKey(list: (typeof AGENT_LISTS)[number], item: Keyed): string {
  return list === "apps" ? (item as { readonly name: string }).name
    : (item as { readonly agent_id: string }).agent_id;
}

/**
 * The agents a caller sees, fitted to one answer after the cursor `after` (`outsideAgents`,
 * review M3 and its verification): the three lists read as one sequence, each by its stable
 * key, so an agent added or removed between pages is neither skipped nor answered twice;
 * `next`, the last one answered's `<list>:<key>`, when some are left.
 */
export function agentsPage<A extends { readonly agent_id: string },
                           N extends { readonly agent_id: string },
                           P extends { readonly name: string }>(
    all: { agents: A[]; owner_agents: N[]; apps: P[] }, after: string | null):
    { agents: A[]; owner_agents: N[]; apps: P[]; next?: string } {
  const at = after === null ? null : { list: AGENT_LISTS.indexOf(
    after.slice(0, after.indexOf(":")) as (typeof AGENT_LISTS)[number]),
                                       key: after.slice(after.indexOf(":") + 1) };
  if (at !== null && at.list < 0) {
    throw new Refusal("invalid", "after is a next an agents list answered");
  }
  const items = AGENT_LISTS.flatMap((list, index) => [...all[list]]
    .map((item) => ({ list, item, key: agentKey(list, item) }))
    .sort((x, y) => (x.key < y.key ? -1 : x.key > y.key ? 1 : 0))
    .filter((entry) => at === null || index > at.list
            || (index === at.list && entry.key > at.key)));
  const empty = { agents: [], owner_agents: [], apps: [] };
  if (items.length === 0) {
    return empty;
  }
  const kept = fitPage(jsonBytes({ ...empty, next: `owner_agents:${"\u0001".repeat(512)}` }),
                       items, (entry, room) => whole(entry.item, room));
  const page: { agents: A[]; owner_agents: N[]; apps: P[] } =
    { agents: [], owner_agents: [], apps: [] };
  for (const [index, entry] of kept.entries()) {
    (page[items[index]!.list] as unknown[]).push(entry);
  }
  const last = items[kept.length - 1]!;
  return kept.length === items.length ? page : { ...page, next: `${last.list}:${last.key}` };
}

/** A run as an answer shows it: its result cut further to fit one, `result_cut` then. */
export function fittedRun<
    T extends { readonly result: string | null; readonly result_cut: boolean }>(view: T): T {
  if (view.result === null || jsonBytes(view) <= PAGE_BYTES) {
    return view;
  }
  const meta = jsonBytes({ ...view, result: "", result_cut: true });
  if (meta + 2 > PAGE_BYTES) {
    throw new Refusal("result_too_large", RESULT_TOO_LARGE);
  }
  return { ...view, result: fitted(view.result, PAGE_BYTES - meta + 2).text, result_cut: true };
}

/** The placed contents a read's rows name (ADR-035). */
export function contentsOf(rows: readonly object[]): string[] {
  return rows.flatMap((row) => {
    const id = (row as { readonly content_id?: string }).content_id;
    return id === undefined ? [] : [id];
  });
}

/**
 * What a writing door's answer shows of placed bodies (ADR-035): the content ids it names,
 * located in its unit, and the answer with their windows read in.
 */
export interface Shown<T> {
  readonly contents: (value: T) => readonly string[];
  readonly fill: (value: T, windows: ReadonlyMap<string, Window>) => T;
}

/** A conflict's current row, read back as `get` shows it: fitted to one answer. */
export function conflictShown<T extends object>(): Shown<T> {
  return {
    contents: (value) => "conflict" in value && value.conflict !== null
      ? contentsOf([value.conflict as Row]) : [],
    fill: (value, windows) => "conflict" in value && value.conflict !== null
      ? { ...value, conflict: fittedRow(windowed(value.conflict as Row, windows),
                                        jsonBytes({ ...value, conflict: null }) - 4) } : value,
  };
}

