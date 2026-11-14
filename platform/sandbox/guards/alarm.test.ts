/**
 * The sandbox Worker's objects keep their own clocks (slice S): `SandboxControl`, whose alarm
 * is its sandboxes' look before their runtime ceiling, and `SandboxSlots`, whose alarm rings
 * the platform's line again. Neither has a Lifecycle or an owner's data, so each sets and
 * clears its own alarm, and only the control object's erasure of an owner deletes its
 * storage whole. No other sandbox source names an alarm call or `deleteAll`.
 */
import { describe, expect, it } from "vitest";

import { sourcesOf } from "../../ci/kit/sources";

/** Each source that may name these calls, and which. */
const CLOCKS: Readonly<Record<string, readonly string[]>> = Object.freeze({
  "sandbox/src/control.ts": Object.freeze(["setAlarm", "deleteAlarm", "deleteAll"]),
  "sandbox/src/slots.ts": Object.freeze(["setAlarm", "deleteAlarm"]),
});

/** Each alarm call or wipe `source` (at `path`) names that is not its own to make. */
export function clockProblems(path: string, source: string): string[] {
  return [...new Set([...source.matchAll(/\b(?:setAlarm|deleteAlarm|getAlarm|deleteAll)\b/g)]
    .map(([name]) => name))].filter((name) => !(CLOCKS[path] ?? []).includes(name))
    .map((name) => `${path} names ${name}, which only its objects' own clocks call`);
}

describe("the sandbox Worker's clocks", () => {
  it("are its two objects' own, and only the control object wipes its storage", () => {
    const sources = sourcesOf("sandbox");
    expect(Object.keys(CLOCKS).filter((path) => !sources.has(path))).toEqual([]);
    expect([...sources].flatMap(([path, source]) => clockProblems(path, source))).toEqual([]);
  });

  it("sees an alarm call or a wipe anywhere else", () => {
    expect(clockProblems("sandbox/src/index.ts", "await storage.setAlarm(1);"))
      .toEqual(["sandbox/src/index.ts names setAlarm, which only its objects' own clocks call"]);
    expect(clockProblems("sandbox/src/slots.ts", "await this.ctx.storage.deleteAll();"))
      .toHaveLength(1);
    expect(clockProblems("sandbox/src/control.ts", "return storage.getAlarm();"))
      .toHaveLength(1);
    expect(clockProblems("sandbox/src/control.ts", "await storage.setAlarm(1);")).toEqual([]);
  });
});
