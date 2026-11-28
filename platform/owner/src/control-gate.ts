/**
 * The platform's gates, as code mode's calls read them in the object.
 *
 * The doors read the control record (`contract/src/control.ts`, its own `CONTROL` namespace)
 * once per request (`doors/src/control.ts`), and hand the record read fresh in the binding
 * (`doors/src/binder.ts`, `withAccount`). A `code` call
 * then runs its script's tool calls in the object for as long as its caller waits, so the
 * object reads the same record again at each of them, with the doors' own rules: an outside
 * client's calls need outside access on and the maintenance gate to admit its owner
 * (`gateClosed`'s `outside`, fail-closed), a run's or the owner's the maintenance gate alone
 * (its `owner`). The namespace holds the control record alone, so binding it reaches no
 * owner's state; the object only reads it.
 *
 * One read serves a script's calls for `CONTROL_FRESH_MS`, far within KV's own 60 s for a
 * flip to reach every location, so a script of many calls does not cost a KV read each.
 *
 * Each read keeps its copy in the object's own SQLite (`objectKeeper`), which serves it when
 * KV can't be read, as the doors' keeper serves theirs. It is the object's one copy: the
 * record a binding hands is kept there too (`owner-do.ts`, its bind), and the tier numbers and
 * the switched-on sign-ins are read from it (`admission.ts`, `signins.ts`).
 */
import {
  type ControlKeeper, type ControlRead, type ControlStore, type Gate, gateClosed, readControl,
  SqlKeeper,
} from "../../contract/src/control";
import { clockMs } from "./clock";
import type { OwnerStorage, Write } from "./gate";

/** How long one read of the control record serves a script's calls, in ms. */
export const CONTROL_FRESH_MS = 1_000;

/**
 * The object's keeper (`SqlKeeper`, its row `control_kept`), its writes quiet units of the
 * write gate (`quiet`): the record is the platform's, nothing an owner's manifest holds.
 */
export function objectKeeper(storage: OwnerStorage, quiet: Write): ControlKeeper {
  const kept = new SqlKeeper(storage.sql);
  return { kept: () => kept.kept(), keep: (text, at) => quiet(() => kept.keep(text, at)) };
}

/** One script's view of the control record, read at most once a `CONTROL_FRESH_MS`. */
export class ControlGate {
  readonly #store: ControlStore;
  readonly #keeper: ControlKeeper;
  #last: { readonly at: number; readonly read: ControlRead } | null = null;

  constructor(store: ControlStore, keeper: ControlKeeper) {
    this.#store = store;
    this.#keeper = keeper;
  }

  /** The record as read (`readControl`), its copy kept, so the object's tiers and switches move. */
  async read(): Promise<ControlRead> {
    const now = clockMs();
    if (this.#last === null || now - this.#last.at >= CONTROL_FRESH_MS) {
      this.#last = { at: now, read: await readControl(this.#store, this.#keeper) };
    }
    return this.#last.read;
  }

  /** Why the gate refuses `owner`'s call now, or null when it admits it. */
  async refusal(gate: Gate, owner: string): Promise<string | null> {
    return gateClosed(await this.read(), owner, gate);
  }
}
