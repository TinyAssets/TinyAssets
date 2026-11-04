/**
 * The doors' answers (`answers.ts`): a tool's result is its JSON once, held to
 * `RESULT_CHARS` (past it a bug, failed loudly), and a tool call is served under one grant
 * of the isolate's memory, `503` with `Retry-After` when there is no room now.
 */
import { describe, expect, it, vi } from "vitest";

import { RESULT_CHARS } from "../../owner/src/encoded";
import { BUSY, grant, GUARD_BYTES, memoryHeld } from "../../owner/src/memory";
import { ANSWER_GRANT, answered, REQUEST_FACTOR, requestGrant, resultText } from "../src/answers";

describe("a tool's answer", () => {
  it("is its result's JSON once, and past one answer a loud bug, never cut", () => {
    const result = { text: "\"".repeat(1000) };
    expect(resultText("t", result)).toBe(JSON.stringify(result));
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => resultText("big", { text: String.fromCharCode(1).repeat(RESULT_CHARS / 6) }))
        .toThrow("internal error");
      expect(logged.mock.calls[0]![0]).toMatch(/^the big tool answered \d+ bytes of JSON/);
    } finally {
      logged.mockRestore();
    }
  });

  it("is served under its request's grant, and refused 503 while the isolate is full",
     async () => {
    const request = (length: number) => new Request("https://doors/api", {
      method: "POST", headers: { "Content-Length": `${length}` }, body: "x".repeat(length) });
    expect(requestGrant(request(1000), 16)).toBe(ANSWER_GRANT);  // refused 413 unread
    expect(requestGrant(request(1000), 4096)).toBe(REQUEST_FACTOR * 1000 + ANSWER_GRANT);
    let held = 0;
    const ok = await answered(request(10), 4096, async () => {
      held = memoryHeld().held;
      return new Response("ok");
    });
    expect([ok.status, held >= REQUEST_FACTOR * 10 + ANSWER_GRANT, memoryHeld().held])
      .toEqual([200, true, 0]);
    const full = grant(Math.floor((GUARD_BYTES - memoryHeld().reserved) / 2), "a full isolate");
    try {
      let served = false;
      const busy = await answered(request(10), 4096, async () => {
        served = true;
        return new Response("ok");
      });
      expect([busy.status, busy.headers.get("Retry-After"), served]).toEqual([503, "1", false]);
      expect(await busy.json()).toEqual({ error: BUSY });
    } finally {
      full.release();
    }
  });
});
