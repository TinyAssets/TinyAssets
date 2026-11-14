/**
 * The sandbox line's doorbell (slice S; `src/ring.ts`), in workerd through the real owner
 * object (its `SANDBOX` a recording fake, `entry.ts`): a ring reaches the owner it names,
 * whose object, with no run waiting, leaves the line; a ring out of shape is dropped, never
 * guessed at, and acknowledged so the queue does not retry it. The doors' queue entry takes
 * each batch by its queue's name (`QUEUES`) and fails loudly on any other.
 */
import { recorded } from "./support";
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { QUEUES } from "../../contract/src/queue-names";
import type { DoorsEnv } from "../src/env";
import worker from "../src/index";
import { ringQueue } from "../src/ring";

function batch(bodies: unknown[], queue: string = QUEUES.RING) {
  const acked: number[] = [];
  const retried: number[] = [];
  const messages = bodies.map((body, i) => ({
    id: String(i), timestamp: new Date(), attempts: 1, body,
    ack: () => acked.push(i), retry: () => retried.push(i),
  }));
  return { batch: { queue, messages, ackAll: () => undefined,
                    retryAll: () => undefined } as unknown as MessageBatch<unknown>,
           acked, retried };
}

describe("the ring queue's consumer", () => {
  it("rings the owner a message names; one with nothing waiting leaves the line", async () => {
    const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const rung = batch([{ owner: who }]);
    await ringQueue(rung.batch, env as unknown as DoorsEnv);
    expect(rung.acked).toEqual([0]);
    const unwaits = (await recorded(who)).unwaits;
    expect(unwaits).toEqual([who]);
  });

  it("drops a ring out of shape, acknowledged, reaching no object", async () => {
    const dropped = batch([null, "user_x", { owner: "user_x", extra: 1 }, { owner: "" }]);
    await ringQueue(dropped.batch, env as unknown as DoorsEnv);
    expect(dropped.acked).toEqual([0, 1, 2, 3]);
    expect(dropped.retried).toEqual([]);
  });
});

describe("the doors' queue entry", () => {
  it("takes a ring by its producer's queue name", async () => {
    const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const rung = batch([{ owner: who }]);
    await worker.queue(rung.batch, env as unknown as DoorsEnv);
    expect(rung.acked).toEqual([0]);
    expect((await recorded(who)).unwaits).toEqual([who]);
  });

  it("fails loudly on a queue it does not consume, taking none of its messages", async () => {
    const stray = batch([{ owner: "user_x" }], "tinyassets-other");
    await expect(worker.queue(stray.batch, env as unknown as DoorsEnv))
      .rejects.toThrow("the doors consume no queue");
    expect([stray.acked, stray.retried]).toEqual([[], []]);
  });
});
