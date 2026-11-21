/**
 * The platform's gates, as code mode's calls read them in the object.
 *
 * The doors read the control record (`contract/src/control.ts`, its own `CONTROL` namespace)
 * once per request (`doors/src/control.ts`), and hand its tier numbers and switched-on
 * sign-ins in the binding (`doors/src/binder.ts`, `withAccount`). A `code` call then runs its
 * script's tool calls in the object for up to `CODEMODE_TIMEOUT_MS`, so the object reads the same
 * record again at each of them, with the doors' own rules: an outside client's calls need outside
 * access on and the maintenance gate to admit its owner (`outsideOpen`, fail-closed), a run's or
 * the owner's the maintenance gate alone (`ownerOpen`). The namespace holds the control record
 * alone, so binding it reaches no owner's state; the object only reads it.
 *
 * One read serves a script's calls for `CONTROL_FRESH_MS`, far within KV's own 60 s for a
 * flip to reach every location, so a script of many calls does not cost a KV read each.
 *
 * Each read keeps its copy in the object's own SQLite (`objectKeeper`), which serves it when
 * KV can't be read, as the doors' keeper serves theirs.
 */
import {
  type ControlKeeper, type ControlRead, type ControlStore, MAINTENANCE, OUTSIDE_OFF,
  outsideOpen, ownerOpen, readControl, SqlKeeper,
} from "../../contract/src/control";
import { bundledSources } from "../../sources/src/catalog";
import { signinsRead } from "../../sources/src/signin";
import { clockMs } from "./clock";
import type { OwnerStorage, Write } from "./gate";

/** How long one read of the control record serves a script's calls, in ms. */
export const CONTROL_FRESH_MS = 1_000;

/** Which of the doors' gates a caller passed: an outside client's, or the owner's own. */
export type Gate = "outside" | "owner";

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

  async #read(): Promise<ControlRead> {
    const now = clockMs();
    if (this.#last === null || now - this.#last.at >= CONTROL_FRESH_MS) {
      this.#last = { at: now, read: await readControl(this.#store, this.#keeper) };
    }
    return this.#last.read;
  }

  /** Why the gate refuses `owner`'s call now, or null when it admits it. */
  async refusal(gate: Gate, owner: string): Promise<string | null> {
    const read = await this.#read();
    if (gate === "outside") {
      return outsideOpen(read, owner) ? null : OUTSIDE_OFF;
    }
    return ownerOpen(read, owner) ? null : MAINTENANCE;
  }

  /**
   * The served records whose vendor sign-in is switched on, by the doors' own rule
   * (`signinsRead`), for the runs lane: a record that reads well
   * formed gives its `signins` (else each record's cited verdict); none at all, the verdicts,
   * as the doors hand them; a record that is malformed, none (fail-closed); one not read
   * fresh, no list, so the lane keeps the one it holds.
   */
  async signins(): Promise<string[] | undefined> {
    return signinsRead(bundledSources(), await this.#read());
  }
}
