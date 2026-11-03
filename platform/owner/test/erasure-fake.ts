/**
 * A recording fake behind the owner Worker's erasure queue (`ERASURES`, slice L), in tests
 * only: it records each `{owner}` the object's erasure sends and, when `confirm` is set,
 * confirms the outside record back to the object as the doors' consumer does once its
 * directory, grants and identity steps are done (`doors/src/erase.ts`). The doors' own tests
 * run the real consumer.
 */
export class RecordingErasures {
  readonly sent: { owner: string }[] = [];
  /** What confirms the outside record, as the doors would; unset, nothing confirms. */
  confirm: ((owner: string) => Promise<unknown>) | undefined;

  async send(message: { owner: string }): Promise<void> {
    this.sent.push(message);
    const confirm = this.confirm;
    if (confirm !== undefined) {
      queueMicrotask(() => {
        confirm(message.owner).catch(() => undefined);
      });
    }
  }
}
