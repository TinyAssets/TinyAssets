/**
 * The owner suite's consumer of the erasure queue (`ERASURES`, slice L), bound by config in
 * the doors' place (`vitest.config.ts`): it records each `{owner}` the owner's object sends
 * and acks it. It confirms nothing: a test is the doors, and confirms the outside record
 * through the object's own door (`erasureOutsideDone`) when it chooses. A test reads what was
 * sent through `Sent`, an entrypoint bound to the test Worker alone.
 *
 * A copy of this Worker runs in each workerd process and outlives a test, so the record is
 * kept by owner, and each test names a fresh one. It is transpiled alone (`ci/workers.ts`
 * `fake()`), so it imports types only.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import type { OwnerMessage } from "../../contract/src/queues";

const sent = new Map<string, OwnerMessage[]>();

export default {
  async queue(batch: MessageBatch<OwnerMessage>): Promise<void> {
    for (const message of batch.messages) {
      const owner = message.body.owner;
      sent.set(owner, [...(sent.get(owner) ?? []), message.body]);
      message.ack();
    }
  },
};

/** The test's handle on the record. */
export class Sent extends WorkerEntrypoint {
  /** Every message sent for `owner` so far, oldest first. */
  async of(owner: string): Promise<OwnerMessage[]> {
    return [...(sent.get(owner) ?? [])];
  }
}
