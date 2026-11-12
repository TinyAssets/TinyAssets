/**
 * Every file a generator writes is what `npm run generate` writes from its sources now: the
 * door requests, the source records' list, each Worker's binding types, the starter's bundle
 * and the plugins. One guard for all of them, so an edit to any source fails here until the
 * one command has been run.
 */
import { describe, expect, it } from "vitest";

import { platformCopy, regenerated, removeCopy } from "../kit/generated";

describe("the generated files", () => {
  it("are what npm run generate writes from their sources", { tags: ["large"] }, () => {
    const copy = platformCopy();
    try {
      expect(regenerated(copy), "run npm run generate in platform/").toEqual([]);
    } finally {
      removeCopy(copy);
    }
  });
});
