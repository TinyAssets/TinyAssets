import { describe, expect, it } from "vitest";

import { E2b } from "../src/e2b";

function read(endAt: string, state: "running" | "paused" = "running") {
  const client = new E2b("test-key", async () => Response.json({ sandboxID: "box1", state, endAt }));
  return client.info("box1");
}

describe("the provider's sandbox expiry", () => {
  it.each([
    ["2028-02-29T23:59:59.123456789Z", "2028-02-29T23:59:59.123Z"],
    ["2028-03-01T00:29:59.123+00:30", "2028-02-29T23:59:59.123Z"],
    ["2000-02-29t00:00:00-05:30", "2000-02-29T05:30:00Z"],
    ["1967-01-01T00:00:00z", "1967-01-01T00:00:00Z"],
  ])("keeps the spelling and instant of %s, including an old pause", async (text, utc) => {
    expect(await read(text, "paused")).toMatchObject({ endAt: text, endAtMs: Date.parse(utc) });
  });

  it.each(["0", "", "tomorrow", "2026-11-19", "2026-11-19T12:00:00",
           "1900-02-29T01:00:00Z", "2026-02-30T00:00:00+03:00",
           "2026-11-19T24:00:00Z", "2026-11-19T12:00:00+01:60"])(
    "refuses an invalid expiry (%j)", async (text) => {
      await expect(read(text)).rejects.toMatchObject({ status: 502 });
    });
});
