/**
 * A recording fake behind the owner Worker's erasure queue (`ERASURES`, slice L), in tests
 * only: it records each `{owner}` the object's erasure sends and, when `confirm` is set,
 * confirms the outside record back to the object as the doors' consumer does once its
 * directory, grants and identity steps are done (`doors/src/erase.ts`). The doors' own tests
 * run the real consumer. The object only sends, one message at a time, so the queue's other
 * calls fail loudly here.
 */
import type { OwnerMessage } from "../../contract/src/queues";

/** An empty queue's figures, as a send answers them. */
const METRICS = Object.freeze({ backlogCount: 0, backlogBytes: 0 });

export class RecordingErasures implements Queue<OwnerMessage> {
  readonly sent: OwnerMessage[] = [];
  /** What confirms the outside record, as the doors would; unset, nothing confirms. */
  confirm: ((owner: string) => Promise<unknown>) | undefined;

  async send(message: OwnerMessage): Promise<QueueSendResponse> {
    this.sent.push(message);
    const confirm = this.confirm;
    if (confirm !== undefined) {
      queueMicrotask(() => {
        confirm(message.owner).catch(() => undefined);
      });
    }
    return { metadata: { metrics: { ...METRICS } } };
  }

  sendBatch(): Promise<QueueSendBatchResponse> {
    throw new Error("the owner's object sends one erasure at a time");
  }

  metrics(): Promise<QueueMetrics> {
    throw new Error("the owner's object reads no queue metrics");
  }
}
