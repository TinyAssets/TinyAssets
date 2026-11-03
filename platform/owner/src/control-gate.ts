/**
 * The platform's gates, as code mode's calls read them in the object (slice M review M1).
 *
 * The doors read the control record (`c:control`, its own `CONTROL` namespace) once per
 * request (`doors/src/control.ts`). A `code` call then runs its script's tool calls in the
 * object for up to `CODEMODE_TIMEOUT_MS`, so the object reads the same record again at
 * each of them, with the doors' own rules: an outside client's calls need outside access
 * on and the maintenance gate to admit its owner (`outsideOpen`, fail-closed), a run's or
 * the owner's the maintenance gate alone (`ownerOpen`). The namespace holds the control
 * record alone, so binding it reaches no owner's state; the object only reads it.
 *
 * One read serves a script's calls for `CONTROL_FRESH_MS`, far within KV's own 60 s for a
 * flip to reach every location, so a script of many calls does not cost a KV read each.
 */
import { MAINTENANCE, OUTSIDE_OFF, outsideOpen, ownerOpen } from "../../doors/src/control";
import { type ControlRead, readControl } from "../../doors/src/directory";
import { bundledSources, signinsOn } from "../../doors/src/sources";

/** How long one read of the control record serves a script's calls, in ms. */
export const CONTROL_FRESH_MS = 1_000;

/** Which of the doors' gates a caller passed: an outside client's, or the owner's own. */
export type Gate = "outside" | "owner";

/** One script's view of the control record, read at most once a `freshMs`. */
export class ControlGate {
  readonly #store: KVNamespace;
  readonly #freshMs: number;
  #last: { readonly at: number; readonly read: ControlRead } | null = null;

  constructor(store: KVNamespace, freshMs: number = CONTROL_FRESH_MS) {
    this.#store = store;
    this.#freshMs = freshMs;
  }

  async #read(): Promise<ControlRead> {
    const now = Date.now();
    if (this.#last === null || now - this.#last.at >= this.#freshMs) {
      this.#last = { at: now, read: await readControl(this.#store) };
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
   * The served records whose vendor sign-in is switched on, by the doors' own rule, for the
   * runs lane (slice SIGNIN review N1): a record that reads well formed gives its `signins`
   * (else each record's cited verdict); none at all, the verdicts, as the doors hand them; a
   * record that is malformed or unreadable, none (fail-closed).
   */
  async signins(): Promise<string[]> {
    const read = await this.#read();
    return read.kind === "read" ? signinsOn(bundledSources(), read.control.signins)
      : read.kind === "missing" ? signinsOn(bundledSources(), null) : [];
  }
}
