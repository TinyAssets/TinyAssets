/**
 * The global-fetch interposer (`egress.ts`): inside a scope, the scope's own fetch answers
 * every global fetch, so an adapter that takes no custom fetch (Google's two) is held to
 * the scope's guard, as the meter's learning runs hold it (`meter.ts`); outside any scope,
 * the runtime's fetch.
 */
import { describe, expect, it } from "vitest";

import { within } from "../src/egress";

describe("the interposer", () => {
  it("is the scope's guard inside a scope, and the runtime's fetch outside any", async () => {
    const refusing = async (): Promise<Response> => new Response("guarded", { status: 418 });
    const inside = await within(refusing, () => fetch("https://status.test/cancelled?id=x"));
    expect(inside.status).toBe(418);
    const after = await within(refusing, async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return fetch("https://status.test/cancelled?id=x");
    });
    expect(after.status).toBe(418);
    const outside = await fetch("https://status.test/cancelled?id=x");
    expect(outside.status).toBe(200);
  });
});
