/**
 * The services' checkers (untangle B6): each value one Worker builds and another reads is
 * held, beside its type, to one checker that takes its type's fixture and refuses each way
 * out of shape; and a builder's output is held to its reader's checker.
 */
import { describe, expect, it } from "vitest";

import { doorbellInput, metering, ringRequest } from "../src/gateway";
import { manifestLists, objectPrefix } from "../src/files";
import { MANIFEST_PARTS } from "../src/limits";
import { refusalOf } from "../src/refusal";
import { doorbellRequest, type WakeContent } from "../src/wake-kinds";
import { wakeKinds } from "../src/wake-records";

const code = (call: () => unknown): string | undefined => {
  try {
    call();
  } catch (error) {
    return refusalOf(error)?.code;
  }
  return undefined;
};

describe("metering", () => {
  const fixture = { max_output_tokens: 1024, input_bytes: 10, model: "claude-x", tier: null };
  it("takes its type's fixture, and a tier", () => {
    expect(metering(fixture)).toEqual(fixture);
    expect(metering({ ...fixture, tier: "priority" })).toEqual({ ...fixture, tier: "priority" });
  });
  it.each([
    [{ ...fixture, extra: 1 }], [{ ...fixture, input_bytes: 0 }], [{ ...fixture, model: "" }],
    [{ ...fixture, model: "a b" }], [{ ...fixture, tier: 7 }],
    [{ ...fixture, model: "m".repeat(257) }], [null], [[]],
  ])("refuses %j as the object's bug", (value) => {
    expect(code(() => metering(value))).toBe("internal");
  });
});

describe("doorbellInput", () => {
  const fixture = { record: "claude_routine_fire", url: "https://x.test/fire", secret: "s" };
  it("takes its type's fixture", () => {
    expect(doorbellInput(fixture)).toEqual(fixture);
  });
  it.each([[{ ...fixture, extra: 1 }], [{ ...fixture, url: 7 }], [{ record: "r", url: "u" }],
           [null]])("refuses %j", (value) => {
    expect(code(() => doorbellInput(value))).toBe("invalid_request");
  });
});

describe("ringRequest", () => {
  const wake: WakeContent = { agent_id: "a1", command_center_id: "cc1", changes: [], cursor: 0,
                              after: 0, read: [] };
  const pushing = wakeKinds().filter((kind) => kind.push !== null);
  it("takes what doorbellRequest builds for every kind that pushes", () => {
    expect(pushing.length).toBeGreaterThan(0);
    for (const kind of pushing) {
      const built = doorbellRequest(kind, "sub_x", wake, "wake_AbC-1_2", 1_800_000_000);
      expect(ringRequest(built)).toEqual(built);
    }
  });
  it.each(["", "a.b", "a".repeat(129), "wake 1"])(
    "refuses the event id %j where it is built, as the gateway would", (id) => {
      expect(code(() => doorbellRequest(pushing[0]!, "sub_x", wake, id, 1)))
        .toBe("invalid_request");
    });
  const fixture = { headers: { "content-type": "application/json" }, body: "{}", event_id: "w1" };
  it.each([[{ ...fixture, headers: { Authorization: "Bearer x" } }],
           [{ ...fixture, headers: { "webhook-id": "w1" } }],
           [{ ...fixture, headers: { n: 1 } }], [{ ...fixture, headers: [] }],
           [{ ...fixture, extra: 1 }], [{ ...fixture, body: 1 }]])("refuses %j", (value) => {
    expect(code(() => ringRequest(value))).toBe("invalid_request");
  });
});

describe("manifestLists", () => {
  const part = { sha256: "a".repeat(64), size: 10 };
  const fixture = { parts: [part], segments: [{ a: 0, b: 4, ...part }], s: 1, e: 4, at: 5 };
  it("takes its type's fixture, and a log with no change", () => {
    expect(manifestLists(fixture)).toEqual(fixture);
    expect(manifestLists({ ...fixture, segments: [], e: 0 })).toEqual({ ...fixture, segments: [],
                                                                         e: 0 });
  });
  it.each([
    [{ ...fixture, parts: [] }], [{ ...fixture, parts: [{ ...part, size: 0 }] }],
    [{ ...fixture, parts: [{ ...part, sha256: "A".repeat(64) }] }],
    [{ ...fixture, segments: [{ a: 4, b: 4, ...part }] }], [{ ...fixture, s: 0 }],
    [{ ...fixture, e: -1 }], [{ ...fixture, at: undefined }], [{ ...fixture, extra: 1 }],
    [{ ...fixture, parts: Array.from({ length: MANIFEST_PARTS }, () => part) }],
  ])("refuses %#", (value) => {
    expect(manifestLists(value)).toBeNull();
  });
});

describe("objectPrefix", () => {
  it("is an owner's namespace's prefix, and only of hex", () => {
    expect(objectPrefix("0123456789abcdef0123456789abcdef"))
      .toBe("o/0123456789abcdef0123456789abcdef/");
    for (const ns of ["../x", "A".repeat(32), 7]) {
      expect(code(() => objectPrefix(ns))).toBe("invalid");
    }
  });
});
