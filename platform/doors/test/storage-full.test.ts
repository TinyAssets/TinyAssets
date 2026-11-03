/**
 * The one wall at the doors: a `storage_full` refusal is 507 Insufficient Storage, its
 * message (used, total, what to free, the upgrade path) handed on whole; other refusals keep
 * 403, and an invalid request 400.
 */
import { describe, expect, it } from "vitest";

import { storageFull, storageFullMessage } from "../../owner/src/storage-full";
import { refusal } from "../src/tools";

function named(name: string, message: string): Error {
  const error = new Error(message);
  error.name = name;
  return error;
}

describe("a refusal's status", () => {
  it("answers storage_full 507 with its whole message", () => {
    const message = storageFullMessage(storageFull(12, 10, null, null));
    expect(refusal(named("Refused", message))).toEqual({ status: 507, message });
  });

  it("keeps 403 for other refusals and 400 for an invalid request", () => {
    expect(refusal(named("Refused", "file_quota: the owner's file entries are at their quota")))
      .toMatchObject({ status: 403 });
    expect(refusal(named("Invalid", "storage_full: not a refusal"))).toMatchObject({ status: 400 });
    expect(refusal(new Error("a bug"))).toBeNull();
  });
});
