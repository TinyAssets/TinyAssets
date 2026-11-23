/**
 * Values that carry a place opaquely under the object's id key (`ordered.ts`): a mail's id its
 * seq, a collection cursor its place in its order.
 */
import { describe, expect, it } from "vitest";

import { cursorAt, cursorPlace, idKey, idOrder, orderedId } from "../src/ordered";

describe("ordered values", () => {
  it("give ids that carry their seq, opaque without the object's key", () => {
    const key = idKey();
    const ids = new Set<string>();
    for (const seq of [1, 2, 3, 2 ** 40, Number.MAX_SAFE_INTEGER]) {
      const id = orderedId(key, seq);
      expect(id).toMatch(/^[0-9a-f]{32}$/);
      expect(idOrder(key, id)).toBe(seq);
      expect(idOrder(idKey(), id)).not.toBe(seq);
      ids.add(id);
      ids.add(orderedId(key, seq));  // the same seq, a different id: eight random bytes
    }
    expect(ids.size).toBe(10);
  });

  it("give cursors that carry their place and incarnation in their order, and say nothing " +
     "of either", () => {
    const key = idKey();
    const incarnation = "0123456789abcdef";
    // A migrated history keys its older entries below 0.
    for (const place of [-3, 0, 1, 2 ** 40, Number.MAX_SAFE_INTEGER]) {
      const [one, two] = [cursorAt(key, "changes", place, incarnation),
                          cursorAt(key, "changes", place, incarnation)];
      expect(one).toMatch(/^[0-9a-f]{64}$/);
      expect([cursorPlace(key, "changes", one), cursorPlace(key, "changes", two)])
        .toEqual([{ place, incarnation }, { place, incarnation }]);
      expect(cursorPlace(key, "changes", cursorAt(key, "changes", place, "f".repeat(16))))
        .toEqual({ place, incarnation: "f".repeat(16) });
      // Two cursors at one place differ where the place is: it is masked under each one's
      // own random bytes, never written out.
      expect(one.slice(16, 48)).not.toBe(two.slice(16, 48));
      // Another order's, another key's, or one altered: none is read.
      expect(cursorPlace(key, "another order", one)).toBeNull();
      expect(cursorPlace(idKey(), "changes", one)).toBeNull();
      const altered = one.slice(0, 20) + (one[20] === "0" ? "1" : "0") + one.slice(21);
      expect(cursorPlace(key, "changes", altered)).toBeNull();
    }
    expect(cursorPlace(key, "changes", "7")).toBeNull();
  });
});
