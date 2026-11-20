import { expect, it } from "vitest";

import { readSyncReport } from "./runner";

it("keeps every mirror answer including a missing file", () => {
  const known = { "/present": "a".repeat(64), "/missing": null };
  expect(readSyncReport(JSON.stringify({ known }), Object.keys(known))).toEqual({ known });
  expect(readSyncReport('{"ok":true}')).toEqual({ ok: true });
});

it("reports a cut-off reply without exposing its contents", () => {
  expect(() => readSyncReport('{"known": {"synthetic-path":')).toThrow(
    /^the sync agent returned an incomplete JSON reply$/);
});

it("rejects a missing mirror answer even when the reply is complete JSON", () => {
  for (const value of [{}, { known: [] }, { known: {} }, { known: { "/other": null } }]) {
    expect(() => readSyncReport(JSON.stringify(value), ["/missing"])).toThrow(
      "incomplete mirror lookup");
  }
  expect(() => readSyncReport("null")).toThrow("invalid report");
});
