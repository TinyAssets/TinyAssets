/**
 * The service-binding calls one invocation of the owner's object may make (ADR-032 §5).
 *
 * "A single request has a maximum of 32 Worker invocations, and each call to a Service
 * binding counts towards this limit. Subsequent calls will throw" (Workers service
 * bindings). The unit is the invocation, not the tool call: a whole pi turn runs inside
 * one Lifecycle job of one alarm, so a budget per tool call would let a turn's tool calls
 * together pass the cap. So the object keeps one budget per invocation, which the files
 * Worker (`FILES`) draws from now and the gateway and hooks will draw from once they land
 * (tasks 4 and 5), and refuses before a call would cross it, loudly, naming the budget.
 *
 * Per entry, passed down; never a field of the object. Each entry makes its own
 * {@link Calls}: every RPC door that reaches `FILES`, and every Lifecycle job of the
 * object's schedule (`schedule.ts`), and hands it to whatever makes the calls (`files.ts`
 * takes it as an argument, the pi environment as an option). One counter on the object,
 * reset at each entry, would be wrong: the runtime delivers another RPC to the object
 * whenever the one in flight awaits I/O, so a second door's reset would hand the first
 * door's spent calls back, and two interleaved doors would draw on one count. The platform
 * counts each incoming request on its own, so each entry's own counter is its unit.
 * Lifecycle may drive several jobs in one alarm; a lane's pass makes at most one `FILES`
 * call (a chunk delete), which the headroom covers. The cap probe (tasks 6) confirms or
 * adjusts these numbers, and whether the cap applies per Durable Object event at all.
 */

/** The service-binding calls one invocation may make (Workers service bindings). */
export const INVOCATION_CALLS = 32;
/** Of those, kept for the gateway and hooks: `FILES` never draws into them. */
export const GATEWAY_HOOKS_HEADROOM = 8;
/** What `FILES` may draw in one invocation: the cap less the gateway's and hooks' room. */
export const FILES_CALLS = INVOCATION_CALLS - GATEWAY_HOOKS_HEADROOM;

/** What draws on an invocation's budget. */
export type CallKind = "files" | "gateway" | "hooks";

/** A call the invocation's budget does not hold: refused before it was made. */
export class CallsSpent extends Error {
  override readonly name = "CallsSpent";
}

/** One invocation's service-binding calls, shared by everything the invocation calls. */
export class Calls {
  #made = 0;

  /** The calls drawn so far, of every kind. */
  get made(): number {
    return this.#made;
  }

  /**
   * Draw `calls` more before making them, or refuse with none drawn. `FILES` may bring
   * the invocation's calls to `FILES_CALLS`; the gateway and hooks to `INVOCATION_CALLS`.
   */
  draw(kind: CallKind, calls: number): void {
    if (!Number.isSafeInteger(calls) || calls < 0) {
      throw new Error("a draw is a whole number of calls");
    }
    const ceiling = kind === "files" ? FILES_CALLS : INVOCATION_CALLS;
    if (this.#made + calls > ceiling) {
      throw new CallsSpent(
        `the invocation's call budget is spent: ${kind} calls may bring it to ${ceiling} of ` +
        `the ${INVOCATION_CALLS} service calls one invocation makes, ${this.#made} are made ` +
        `and ${calls} more were asked; read a smaller range, or fewer files at once`);
    }
    this.#made += calls;
  }
}
