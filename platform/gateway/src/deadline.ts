/**
 * The gateway's one way to time out: a signal aborted `ms` from now with a `TimeoutError`,
 * as `AbortSignal.timeout`'s, but from `setTimeout`, the timer the idle cut uses
 * (`forward.ts`). Vitest's fake timers drive `setTimeout` in workerd, not
 * `AbortSignal.timeout`, so every deadline here can be held by a test.
 */
export function timeout(ms: number): AbortSignal {
  const timed = new AbortController();
  setTimeout(() => timed.abort(new DOMException("The operation timed out.", "TimeoutError")), ms);
  return timed.signal;
}
