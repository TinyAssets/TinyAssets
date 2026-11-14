/**
 * The doors' one answer for a refusal (`src/refused.ts`), by its code alone: its status from
 * the code list (`contract/src/refusal.ts`), its `Retry-After` from the code or the refusal
 * itself, and its message handed on whole as `{error}`. The one wall (`storage_full`) is 507
 * Insufficient Storage; open uploads alone (`uploads_open`) and an append that lost every try
 * (`file_busy`) are 409; a post at the hot mail ceiling (`storage_busy`) and a full slot pool
 * (`busy`) are 503 with their `Retry-After`; other refusals keep 403, an invalid request 400.
 * The same answers through the real object's RPC are `api-keys.test.ts`'s.
 */
import { describe, expect, it } from "vitest";

import { Refusal, type RefusalCode } from "../../contract/src/refusal";
import { refusalResponse, retryAfter, shownCode } from "../src/refused";

async function answered(code: RefusalCode, message: string, retryAfterS?: number):
    Promise<[number, string | null, unknown]> {
  const response = refusalResponse(new Refusal(code, message,
                                               retryAfterS === undefined ? {} : { retryAfterS }));
  return [response.status, response.headers.get("Retry-After"), await response.json()];
}

describe("a refusal's answer", () => {
  it.each([
    ["storage_full", "storage_full: your storage is full", 507, null],
    ["uploads_open", "uploads_open: held by uploads still open", 409, null],
    ["file_busy", "file_busy: /workspace/a changed under each of 3 tries", 409, null],
    ["storage_busy", "storage_busy: your storage is catching up", 503, "30"],
    ["busy", "busy: this server is holding as much as it may at once", 503, "1"],
    ["events_busy", "events_busy: the object holds as many runner posts as it may", 503, "1"],
    ["file_quota", "file_quota: the file is larger than the owner's file size quota", 403, null],
    ["refused", "agent not found", 403, null],
    ["chunk_deleting", "a chunk of this upload is being deleted; begin again", 403, null],
    ["invalid", "storage_full: not a refusal", 400, null],
    ["message_body", "a message body is well-formed text", 400, null],
  ] as const)("answers %s by its code", async (code, message, status, retry) => {
    expect(await answered(code, message)).toEqual([status, retry, { error: message }]);
  });

  it("carries a refusal's own Retry-After over its code's", async () => {
    expect(await answered("storage_busy", "storage_busy: the archive moved this mail", 1))
      .toEqual([503, "1", { error: "storage_busy: the archive moved this mail" }]);
    expect(retryAfter(new Refusal("refused"))).toEqual({});
  });

  it("keeps a surface's own headers beside it", async () => {
    const response = refusalResponse(new Refusal("busy", "busy: full"),
                                     { "Cache-Control": "no-store" });
    expect([response.headers.get("Cache-Control"), response.headers.get("Retry-After")])
      .toEqual(["no-store", "1"]);
  });
});

describe("the code a protocol shows", () => {
  it("is the refusal's own when its message names it, else its message", () => {
    expect(shownCode(new Refusal("inbound_proof_invalid",
                                 "inbound_proof_invalid: the event's proof does not verify")))
      .toBe("inbound_proof_invalid");
    expect(shownCode(new Refusal("erasing", "this owner is being erased")))
      .toBe("this owner is being erased");
    expect(shownCode(new Refusal("refused", "agent not found"))).toBe("agent not found");
  });
});
