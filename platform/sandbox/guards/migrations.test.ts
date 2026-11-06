/**
 * The sandbox Worker's two objects' migrations held to their locks,
 * `migrations/SandboxControl.json` and `migrations/SandboxSlots.json` (`owner/guards/lock.ts`,
 * which says what a lock holds and how a deploy pins it).
 */
import { describe, expect, it } from "vitest";

import { holdLocks } from "../../owner/guards/lock";
import { CONTROL_MIGRATIONS, SLOTS_MIGRATIONS } from "../src/schema";

describe("append-only migrations", () => {
  it("hold every deployed migration of both objects to its pinned text", () => {
    expect(holdLocks({ "sandbox/migrations/SandboxControl.json": CONTROL_MIGRATIONS,
                       "sandbox/migrations/SandboxSlots.json": SLOTS_MIGRATIONS })).toEqual([]);
  });
});
