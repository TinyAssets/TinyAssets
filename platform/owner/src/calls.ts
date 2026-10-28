/**
 * The service-binding calls one invocation of the owner's object may make (ADR-032 §5).
 *
 * "A single request has a maximum of 32 Worker invocations, and each call to a Service
 * binding counts towards this limit. Subsequent calls will throw" (Workers service
 * bindings). The unit is the platform's invocation, not the tool call: a whole pi turn runs
 * inside one Lifecycle job of one alarm, so a budget per tool call would let a turn's tool
 * calls together pass the cap. So the object keeps one budget per invocation, which the
 * files Worker (`FILES`) draws from now and the gateway and hooks will draw from once they
 * land (tasks 4 and 5), and refuses before a call would cross it, loudly, naming the budget.
 *
 * The budget is ambient. The object holds one {@link CallScope} (an `AsyncLocalStorage` on
 * the instance, never at module level), and each invocation enters a fresh scope: every
 * RPC door, and the alarm, which covers the Lifecycle jobs it drives. Whatever makes a call
 * reads the scope's budget (`CallScope.current`) rather than taking one as an argument, so
 * a factory handed no job, as PiHarness's environment factory is, can still draw. Which
 * budget a pi task draws on is not shown yet: pi-durable's scheduler starts tasks from a
 * long-lived drain loop kicked by `queueMicrotask`, so a task draws on the budget of the
 * invocation whose async context started that loop, which may not be the invocation that
 * kicked the task, and a loop started outside any scope throws on every task. The agent
 * slice owes those checks (tasks 5), and the cap probe whether workerd attributes a
 * service call the same way (tasks 6). The runtime delivers another RPC to the object
 * whenever one in flight awaits I/O; each runs in its own scope, so interleaved doors never
 * share a count. A draw outside any scope is a bug and throws (Fact 8: no fallback budget).
 * The cap probe before the gateway slice (tasks 6) confirms or adjusts these numbers, and
 * whether the cap applies per Durable Object event at all.
 */
import { AsyncLocalStorage } from "node:async_hooks";

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

/** The object's invocations, each with its own budget for everything it awaits. */
export class CallScope {
  readonly #scope = new AsyncLocalStorage<Calls>();

  /** Run `act` as one invocation, with a fresh budget. */
  run<T>(act: () => T): T {
    return this.#scope.run(new Calls(), act);
  }

  /** The budget of the invocation running now. Outside one it is a bug, thrown loudly. */
  current(): Calls {
    const calls = this.#scope.getStore();
    if (calls === undefined) {
      throw new Error("a service-binding call outside any invocation's scope: enter one at " +
                      "the door or the alarm");
    }
    return calls;
  }
}
