/**
 * The sandbox line's doorbell (slice S; `src/ring.ts`), in workerd through the real owner
 * object (its `SANDBOX` a recording fake, `entry.ts`): a ring reaches the owner it names,
 * whose object, with no run waiting, leaves the line; a ring out of shape is dropped, never
 * guessed at, and acknowledged so the queue does not retry it.
 */
import { env, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import type { DoorsEnv } from "../src/config";
import { ringQueue } from "../src/ring";
import type { OwnerDO } from "./entry";

function batch(bodies: unknown[]) {
  const acked: number[] = [];
  const retried: number[] = [];
  const messages = bodies.map((body, i) => ({
    id: String(i), timestamp: new Date(), attempts: 1, body,
    ack: () => acked.push(i), retry: () => retried.push(i),
  }));
  return { batch: { queue: "tinyassets-sandbox-ring", messages, ackAll: () => undefined,
                    retryAll: () => undefined } as unknown as MessageBatch<unknown>,
           acked, retried };
}

describe("the ring queue's consumer", () => {
  it("rings the owner a message names; one with nothing waiting leaves the line", async () => {
    const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const rung = batch([{ owner: who }]);
    await ringQueue(rung.batch, env as unknown as DoorsEnv);
    expect(rung.acked).toEqual([0]);
    const unwaits = await runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
      (instance as OwnerDO).recorder.unwaits);
    expect(unwaits).toEqual([who]);
  });

  it("drops a ring out of shape, acknowledged, reaching no object", async () => {
    const dropped = batch([null, "user_x", { owner: "user_x", extra: 1 }, { owner: "" }]);
    await ringQueue(dropped.batch, env as unknown as DoorsEnv);
    expect(dropped.acked).toEqual([0, 1, 2, 3]);
    expect(dropped.retried).toEqual([]);
  });
});
