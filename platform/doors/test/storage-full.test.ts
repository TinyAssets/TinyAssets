/**
 * The one wall at the doors: a `storage_full` refusal is 507 Insufficient Storage, its
 * message (used, total, what to free, the upgrade path) handed on whole; other refusals keep
 * 403, and an invalid request 400.
 */
import { describe, expect, it } from "vitest";

import { storageFull, storageFullMessage } from "../../owner/src/storage-full";
import { refusal, refusalHeaders } from "../src/tools";

function named(name: string, message: string): Error {
  const error = new Error(message);
  error.name = name;
  return error;
}

describe("a refusal's status", () => {
  it("answers storage_full 507 with its whole message", () => {
    const message = storageFullMessage(storageFull(10, 2, 0, 10, null, null));
    expect(refusal(named("Refused", message))).toEqual({ status: 507, message });
  });

  it("keeps 403 for other refusals and 400 for an invalid request", () => {
    expect(refusal(named("Refused", "file_quota: the owner's file entries are at their quota")))
      .toMatchObject({ status: 403 });
    expect(refusal(named("Invalid", "storage_full: not a refusal"))).toMatchObject({ status: 400 });
    expect(refusal(new Error("a bug"))).toBeNull();
  });
});

describe("open uploads alone", () => {
  it("answer 409, never the full storage's 507", () => {
    const message = storageFullMessage(storageFull(0, 6, 8, 10, null, null));
    expect(refusal(named("Refused", message))).toEqual({ status: 409, message });
  });
});

describe("a post at the hot mail ceiling (slice STORE item 2)", () => {
  it("answers storage_busy 503 with its Retry-After, never the wall's 507", () => {
    const message = "storage_busy: your storage is catching up, moving older mail out; try " +
      'again in 30 seconds. {"retry_after_s":30}';
    const answer = refusal(named("Refused", message))!;
    expect(answer).toEqual({ status: 503, message, retry_after_s: 30 });
    expect(refusalHeaders(answer)).toEqual({ "Retry-After": "30" });
    expect(refusalHeaders(refusal(named("Refused", "file_quota: x"))!)).toEqual({});
  });
});
