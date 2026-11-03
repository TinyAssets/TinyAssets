/**
 * The doorbell's kill switch (slice W, tasks 7): the owner Worker's `DOORBELL` binding.
 *
 * The owner's object has no env of its own to read the outside kill switch from, and binds
 * nothing that reaches the directory, so its `wakes` lane asks this Worker once a pass,
 * before it claims a wake: `open(owner)` is true only when the directory's `c:control`
 * record reads, well formed, with `outside` on and the maintenance gate admitting that
 * owner, exactly as the doors gate every outside call (`doors/src/control.ts`). A missing,
 * malformed or unreadable record is closed, and so is any failure here: the lane claims
 * nothing then. It binds only `DIRECTORY`, reads only `c:control`, writes nothing and
 * serves no URL.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { outsideOpen } from "../../doors/src/control";
import { readControl } from "../../doors/src/directory";

/** The one binding: the owner directory, as the doors Worker binds it. */
export interface DoorbellEnv {
  readonly DIRECTORY: KVNamespace;
}

export default class Doorbell extends WorkerEntrypoint<DoorbellEnv> {
  /** Whether outside work for `owner` may go now: the kill switch and maintenance gate. */
  async open(owner: string): Promise<boolean> {
    return outsideOpen(await readControl(this.env), owner);
  }
}
