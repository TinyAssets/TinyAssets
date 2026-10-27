/**
 * S34 revision-primitive: one compare-and-swap primitive on the platform.
 *
 * Ported from `test_one_revision_primitive` (`tests/test_revision_primitive.py`,
 * branch `s5b-board`). An etag compare-and-swap store is a persisting module that
 * compares a stored etag with an expected one (a line naming `etag` and `expected`
 * with `==` or `!=`, or SQL `etag = ?`), or a revision the same way beside a `seq`.
 * The collection primitive is the only one; app rooms and the board are row sets in
 * it. That list only shrinks.
 */
import { describe, expect, it } from "vitest";

import { workerSources } from "./sources";

const PRIMITIVES = new Set(["owner/src/collections.ts"]);
const CAS = /^(?=[^\n]*etag)(?=[^\n]*expected)(?=[^\n]*[!=]=)[^\n]*$|\betag\s*=\s*\?/im;
const REVISION_CAS =
  /^(?=[^\n]*revision)(?=[^\n]*expected)(?=[^\n]*[!=]=)[^\n]*$|\brevision\s*=\s*\?/im;
const SEQ = /\bseq\b/;
const PERSISTS = /\.exec\(|\btransactionSync\(|\.put\(|\.sql\b/;

export function primitives(sources: Map<string, string>): Set<string> {
  const found = new Set<string>();
  for (const [path, source] of sources) {
    const swaps = CAS.test(source) || (REVISION_CAS.test(source) && SEQ.test(source));
    if (swaps && PERSISTS.test(source)) {
      found.add(path);
    }
  }
  return found;
}

describe("revision-primitive guard", () => {
  it("finds one revision primitive", () => {
    expect(primitives(workerSources())).toEqual(PRIMITIVES);
  });

  it.each([
    "function cas(sql, body, expectedEtag) {\n  if (current !== expectedEtag) throw new Error();\n" +
      "  sql.exec('UPDATE rows SET body = ?', body);\n}\n",
    "function cas(sql, body, etag) {\n  sql.exec('UPDATE rows SET body = ? WHERE id = 1 AND " +
      "etag = ?', body, etag);\n}\n",
    "function move(sql, body, expectedRevision, seq) {\n  sql.exec('UPDATE board SET body = ?, " +
      "seq = ? WHERE id = 1 AND revision = ?', body, seq, expectedRevision);\n}\n",
  ])("sees a second room store", (source) => {
    expect(primitives(new Map([["owner/src/rooms.ts", source]])))
      .toEqual(new Set(["owner/src/rooms.ts"]));
  });
});
