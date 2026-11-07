/** Text and byte rules every Worker reads, matching the Python trusted side's. */
import { Invalid } from "./errors";

/** Characters Python's `str.isprintable()` refuses: other, and separators but the space. */
const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]|[^\P{Zs} ]/u;

/** Python's `len()`: code points, not UTF-16 units. */
export function codePoints(value: string): number {
  let count = 0;
  for (const _ of value) {
    count++;
  }
  return count;
}

/** The UTF-8 bytes of `text`, without encoding it; a lone surrogate as U+FFFD (3). */
export function utf8Bytes(text: string): number {
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

/**
 * A strict UTF-8 decoder: malformed bytes throw. `keep` leaves a leading byte-order mark in
 * the text, as a stored file's bytes are read back verbatim (Fact 9); `strip` drops it, as a
 * parser reads a body, a name or a document.
 */
export function utf8Decoder(bom: "keep" | "strip"): TextDecoder {
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: bom === "keep" });
}

/** `bytes` as strict UTF-8 (`utf8Decoder`), whole. */
export function utf8Text(bytes: ArrayBuffer | ArrayBufferView, bom: "keep" | "strip"): string {
  return utf8Decoder(bom).decode(bytes);
}

export function isPrintable(value: string): boolean {
  return !NOT_PRINTABLE.test(value);
}

/**
 * A model source record's id, which a connection names its source by (`connections.ts`)
 * and owner policy keys a source's ceilings by (`policy.ts`): the Python's
 * `source_ceilings` key, so a source's ceilings outlive any one connection of it.
 */
export const SOURCE_ID = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

/**
 * Whether `list` (variable names and `PREFIX_*` patterns, as a source record's
 * `forbid_env` holds them) names `name`.
 */
export function forbids(list: readonly string[], name: string): boolean {
  return list.some((entry) => entry.endsWith("*") ? name.startsWith(entry.slice(0, -1))
    : entry === name);
}

/** A collection, row or writer name: 1 to 256 printable characters. */
export function name(value: unknown, what: string): string {
  if (typeof value !== "string" || !value || codePoints(value) > 256 || !isPrintable(value)) {
    throw new Invalid(`${what} is 1 to 256 printable characters`);
  }
  return value;
}

/** An owner or caller, as `validate_principal`: trimmed, 1 to 512 printable characters. */
export function principal(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value !== value.trim()
      || codePoints(value) > 512 || !isPrintable(value)) {
    throw new Invalid("invalid owner principal");
  }
  return value;
}
