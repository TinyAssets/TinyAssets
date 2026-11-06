/**
 * The sandbox line's doorbell (slice S; `sandbox/src/slots.ts`): the ring queue's consumer.
 * The sandbox Worker, which binds no `OWNER`, puts `{owner}` on the queue when a sandbox of
 * the platform frees and that owner is first in line; this rings that owner's object
 * (`sandboxRung`), whose waiting runs start again through their whole admission. A ring
 * grants nothing, so a malformed message is dropped loudly, never guessed at. A ring that
 * fails is retried by the queue; one the queue gives up on is rung again by the line's own
 * back-off.
 */
import { principal } from "../../contract/src/text";

import { ringOwner } from "./binder";
import type { DoorsEnv } from "./config";

export interface Ring {
  readonly owner: string;
}

export async function ringQueue(batch: MessageBatch<unknown>, env: DoorsEnv): Promise<void> {
  for (const message of batch.messages) {
    const body = message.body as Partial<Ring> | null;
    let owner: string;
    try {
      if (typeof body !== "object" || body === null || Object.keys(body).join() !== "owner") {
        throw new Error("out of shape");
      }
      owner = principal(body.owner);
    } catch {
      console.error("a sandbox ring is out of shape; dropped");
      message.ack();
      continue;
    }
    try {
      await ringOwner(env, owner).sandboxRung();
      message.ack();
    } catch (error) {
      console.error("a sandbox ring did not reach its owner; the queue tries again", error);
      message.retry();
    }
  }
}
