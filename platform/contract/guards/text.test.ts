/**
 * The one UTF-8 byte count (`utf8Bytes`), which bounds bodies and storage, is
 * `TextEncoder`'s own count on both its paths: a short text counted by hand, a long one
 * encoded through its window, whose edge a surrogate pair or a lone surrogate may straddle.
 */
import { describe, expect, it } from "vitest";

import { utf8Bytes } from "../src/text";

/** Mixed text of `units` UTF-16 units: ASCII, two- and three-byte characters, and pairs. */
function mixed(units: number): string {
  return "aé—\u{1F600}b".repeat(Math.ceil(units / 6)).slice(0, units);
}

const encoded = (text: string) => new TextEncoder().encode(text).length;

describe("utf8Bytes", () => {
  it.each([
    ["4,095 units, counted by hand", mixed(4095)],
    ["4,096 units, through the window", mixed(4096)],
    ["about 70,000 units, past one window", mixed(70_000)],
    // A pair that begins at the window's last unit and ends past it, and a lone high
    // surrogate there (each one 3-byte U+FFFD), so the window's edge falls inside them.
    ["a pair straddling unit 65,536", `${"x".repeat(65_535)}\u{1F600}${"y".repeat(4000)}`],
    ["a lone surrogate at unit 65,536", `${"x".repeat(65_535)}\ud800${mixed(4000)}`],
    ["a lone low surrogate", `\udc00${mixed(70_000)}`],
    ["ASCII alone", "z".repeat(70_000)],
  ])("is TextEncoder's count for %s", (_what, text) => {
    expect(utf8Bytes(text)).toBe(encoded(text));
  });
});
