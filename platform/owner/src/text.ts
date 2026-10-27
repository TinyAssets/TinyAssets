/** Text rules shared by the object's doors, matching the Python trusted side's. */
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

export function utf8Bytes(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

export function isPrintable(value: string): boolean {
  return !NOT_PRINTABLE.test(value);
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
