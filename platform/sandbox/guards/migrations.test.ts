/** The sandbox Worker's two objects' migrations held to their locks (`ci/kit/locks.ts`). */
import { describe, expect, it } from "vitest";

import { holdLocks } from "../../ci/kit/locks";
import { LOCKS } from "./locks";

describe("append-only migrations", () => {
  it("hold every migration of both objects to its pin", () => {
    expect(holdLocks(LOCKS)).toEqual([]);
  });
});
