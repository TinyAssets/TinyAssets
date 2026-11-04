/** Reading one body through by range, as a client reads a cut one (`pages.ts`). */
import { expect } from "vitest";

import { jsonBytes, RESULT_CHARS } from "../src/encoded";

/** An item as a page answers it: its body, and when cut, where the rest starts. */
export interface Bodied {
  readonly body: string | null;
  readonly body_bytes?: number;
  readonly body_next_offset?: number | null;
  readonly truncated?: boolean;
}

/**
 * The whole body `read` answers part by part, from offset 0 on by each answer's
 * `body_next_offset`, each answer held to one answer's size; the parts joined.
 */
export async function readOn(read: (offset: number | undefined) => Promise<Bodied>):
    Promise<string> {
  const parts: string[] = [];
  let offset: number | undefined;
  for (let n = 0; ; n++) {
    const item = await read(offset);
    expect(jsonBytes(item)).toBeLessThanOrEqual(RESULT_CHARS);
    parts.push(item.body!);
    const next = item.body_next_offset;
    if (next === undefined || next === null) {
      return parts.join("");
    }
    expect(next).toBeGreaterThan(offset ?? 0);
    expect(item.truncated).toBe(true);
    offset = next;
  }
}

/**
 * A read's `body_offset` field, absent for the first part, with what names the item read on
 * (a message's `message_id`, a row's `etag`: reviews L2 and L3).
 */
export function at(offset: number | undefined, names: Record<string, string> = {}):
    Record<string, unknown> {
  return offset === undefined ? {} : { body_offset: offset, ...names };
}
