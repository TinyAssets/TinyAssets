/**
 * What one answer takes as the text a client reads, counted exactly without building it.
 *
 * Every tool answer is one `text` content block holding the result's JSON (`doors/src/mcp.ts`).
 * A read pages by that JSON's size, in UTF-8 bytes, to `RESULT_CHARS`: a page stops before an
 * item that would take it past, and always returns its first item, whose body is cut at a
 * character to fit and says so (`body_bytes`, `body_next_offset`, `truncated`); a read by
 * range (`body_offset`, as `file_read`'s `offset`) returns the rest, verbatim.
 *
 * The count is the bytes `JSON.stringify` would make, from the value itself: no page is
 * serialized to be measured, so a page never holds the copy it is measuring.
 */

/**
 * The most UTF-8 bytes of JSON one answer's text holds: the strictest documented client
 * default, a technical ceiling. Claude Code keeps a tool's text result inline only up to
 * 50,000 characters and saves a longer one to a file the model reads by reference
 * (code.claude.com/docs/en/mcp, retrieved 2026-11-04; `limits-audit.md`). A character is at
 * least one byte, so a page within this many bytes is within that many characters. ChatGPT
 * documents no figure (developers.openai.com/apps-sdk/reference). Raising it through a
 * client's own metadata would be provider-specific (Direction 2).
 */
export const RESULT_CHARS = 50_000;

/**
 * What a page's fit leaves for the fields it sets once its items are chosen (a cursor's
 * digits, `more`, `next`): the fit counts them at their widest, so the page is checked
 * against `RESULT_CHARS` less this.
 */
export const FIT_SLACK = 64;

/** The UTF-8 bytes of `text`, without encoding it; a lone surrogate as U+FFFD (3). */
export function utf8Length(text: string): number {
  let bytes = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c < 0x80) {
      bytes += 1;
    } else if (c < 0x800) {
      bytes += 2;
    } else if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length
               && (text.charCodeAt(i + 1) & 0xfc00) === 0xdc00) {
      bytes += 4;
      i++;
    } else {
      bytes += 3;
    }
  }
  return bytes;
}

/** The UTF-8 bytes `JSON.stringify(text)` makes, quotes included, without making it. */
export function escapedBytes(text: string): number {
  let bytes = 2;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    bytes += unitBytes(text, i, c);
    if (c >= 0xd800 && c <= 0xdbff && paired(text, i)) {
      i++;
    }
  }
  return bytes;
}

function paired(text: string, i: number): boolean {
  return i + 1 < text.length && (text.charCodeAt(i + 1) & 0xfc00) === 0xdc00;
}

/** What the unit at `i` (with its pair) takes in `JSON.stringify`'s output. */
function unitBytes(text: string, i: number, c: number): number {
  if (c === 0x22 || c === 0x5c) {
    return 2;
  }
  if (c < 0x20) {
    return c === 8 || c === 9 || c === 10 || c === 12 || c === 13 ? 2 : 6;
  }
  if (c < 0x80) {
    return 1;
  }
  if (c < 0x800) {
    return 2;
  }
  if (c >= 0xd800 && c <= 0xdfff) {
    // A pair is one 4-byte character; a lone surrogate is written as `\udxxx`.
    return c <= 0xdbff && paired(text, i) ? 4 : 6;
  }
  return 3;
}

/**
 * The UTF-8 bytes `JSON.stringify(value)` makes, without making it: the same rules (a
 * `toJSON` honoured, `undefined` and functions dropped from objects and `null` in arrays,
 * a non-finite number `null`).
 */
export function jsonBytes(value: unknown): number {
  const bytes = valueBytes(value, true);
  if (bytes === null) {
    throw new Error("an answer is a JSON value");
  }
  return bytes;
}

function valueBytes(value: unknown, top: boolean): number | null {
  if (value !== null && typeof value === "object"
      && typeof (value as { toJSON?: unknown }).toJSON === "function") {
    return valueBytes((value as { toJSON: () => unknown }).toJSON(), top);
  }
  switch (typeof value) {
    case "string":
      return escapedBytes(value);
    case "number":
      return Number.isFinite(value) ? String(value).length : 4;
    case "boolean":
      return value ? 4 : 5;
    case "bigint":
      throw new TypeError("a bigint has no JSON");
    case "undefined":
    case "function":
    case "symbol":
      return null;
    default:
      break;
  }
  if (value === null) {
    return 4;
  }
  if (Array.isArray(value)) {
    let bytes = 2 + Math.max(0, value.length - 1);
    for (const item of value) {
      bytes += valueBytes(item, false) ?? 4;
    }
    return bytes;
  }
  let bytes = 2;
  let first = true;
  for (const key of Object.keys(value as object)) {
    const item = valueBytes((value as Record<string, unknown>)[key], false);
    if (item === null) {
      continue;
    }
    bytes += (first ? 0 : 1) + escapedBytes(key) + 1 + item;
    first = false;
  }
  return bytes;
}

/**
 * The longest start of `text`, cut at a character, whose JSON string (quotes included) takes
 * at most `room` bytes, and its UTF-8 bytes; empty when not even one character fits.
 */
export function fitted(text: string, room: number): { text: string; bytes: number } {
  let used = 2;
  let bytes = 0;
  let i = 0;
  while (i < text.length) {
    const c = text.charCodeAt(i);
    const pair = c >= 0xd800 && c <= 0xdbff && paired(text, i);
    const cost = unitBytes(text, i, c);
    if (used + cost > room) {
      break;
    }
    used += cost;
    bytes += pair ? 4 : c < 0x80 ? 1 : c < 0x800 ? 2 : 3;
    i += pair ? 2 : 1;
  }
  return { text: text.slice(0, i), bytes };
}

/**
 * The index in `text` of its UTF-8 byte `offset`, or null when the offset is inside a
 * character or past the end.
 */
export function unitAt(text: string, offset: number): number | null {
  let bytes = 0;
  let i = 0;
  while (bytes < offset && i < text.length) {
    const c = text.charCodeAt(i);
    const pair = c >= 0xd800 && c <= 0xdbff && paired(text, i);
    bytes += pair ? 4 : c < 0x80 ? 1 : c < 0x800 ? 2 : 3;
    i += pair ? 2 : 1;
  }
  return bytes === offset ? i : null;
}
