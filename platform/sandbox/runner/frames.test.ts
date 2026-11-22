/**
 * A job's posts (`frames.ts` `Poster`), on the fake clock against a stubbed door: they are its
 * heartbeat, so once none is taken for a lease's TTL its job is refused, whether the owner's
 * object cannot be reached, answers busy, or the posts are held (a renewal never ended), and
 * a heartbeat taken within it keeps the job going however long it runs.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { LEASE_TTL_S } from "../../contract/src/limits";

import { HEARTBEAT_MS, Outbox, Poster } from "./frames";

const TTL_MS = LEASE_TTL_S * 1000;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** A poster whose door answers by `door`, its refusals kept, its first heartbeat taken. */
async function posting(door: { answer: () => Promise<Response> }) {
  vi.useFakeTimers();
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.stubGlobal("fetch", vi.fn(() => door.answer()));
  const refused: string[] = [];
  const poster = new Poster("https://egress.test/events", "token", new Outbox(),
                            (why) => refused.push(why));
  await poster.tick();
  expect(poster.refused).toBeNull();
  return { poster, refused };
}

const taken = async () => new Response("{}", { status: 200 });

describe("a job's posts", () => {
  for (const [why, answer] of [
    ["the object cannot be reached", async () => { throw new TypeError("fetch failed"); }],
    ["the object answers busy", async () => new Response("busy", { status: 503 })],
  ] as const) {
    it(`lapse a lease's TTL after the last one taken when ${why}, and the job ends`,
       async () => {
      const door = { answer: taken as () => Promise<Response> };
      const { poster, refused } = await posting(door);
      door.answer = answer;
      poster.outbox.push({ type: "ta.line", text: "the end" });
      const finished = poster.finish(0).then(() => true);
      await vi.advanceTimersByTimeAsync(TTL_MS - 1_000);
      expect(poster.refused).toBeNull();
      // Within a retry's longest wait past the TTL, it is refused, once, by its lapse.
      await vi.advanceTimersByTimeAsync(1_000 + HEARTBEAT_MS);
      expect(await finished).toBe(true);
      expect(poster.lapsed).toBe(true);
      expect(refused).toHaveLength(1);
      expect(refused[0]).toMatch(/^heartbeat_lapsed:/);
    });
  }

  it("lapse while held, a renewal's resume never told", async () => {
    const { poster, refused } = await posting({ answer: taken });
    poster.holding = true;
    await vi.advanceTimersByTimeAsync(TTL_MS);
    await poster.tick();
    expect(poster.lapsed).toBe(true);
    expect(refused).toHaveLength(1);
  });

  it("never lapse while a heartbeat is taken within the TTL, however long the job runs",
     async () => {
    const { poster, refused } = await posting({ answer: taken });
    for (let beat = 0; beat < (3 * TTL_MS) / HEARTBEAT_MS; beat += 1) {
      await vi.advanceTimersByTimeAsync(HEARTBEAT_MS);
      await poster.tick();
    }
    expect(poster.refused).toBeNull();
    expect(refused).toEqual([]);
  });

  it("are refused by the object at once, which is no lapse", async () => {
    const door = { answer: taken };
    const { poster, refused } = await posting(door);
    door.answer = async () => new Response("run_ended", { status: 404 });
    poster.outbox.push({ type: "ta.line", text: "more" });
    await poster.tick();
    expect(refused).toEqual(["run_ended"]);
    expect(poster.lapsed).toBe(false);
  });
});
