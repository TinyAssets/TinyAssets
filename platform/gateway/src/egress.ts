/**
 * Every upstream request a provider adapter makes goes through the call's endpoint
 * guard, whether or not the adapter takes a custom `fetch`.
 *
 * pi-ai's Google adapters refuse `options.fetch` (`google-generative-ai.js:36-38`,
 * `google-vertex.js:40`): `@google/genai` calls the global `fetch`, which follows
 * redirects. So the global `fetch` is replaced, once, by an interposer: inside a call's
 * scope (`AsyncLocalStorage`, which follows the adapter's own async work) it is that
 * call's guard; outside any scope it is the runtime's own fetch, unchanged. The guards
 * themselves call `nativeFetch`, never the global, so they cannot reach themselves.
 *
 * This is the one module the platform's module-state guard exempts, for exactly the
 * three lines it pins (`owner/guards/module-state.test.ts`, `EXEMPT`): the scope, the
 * captured runtime fetch, and the one write of the global. The scope holds no data
 * between calls (each call's guard lives only in its own async context), and the
 * interposer is the same function for every owner.
 *
 * The scope follows promises and timers, not events: a listener registered with an
 * `EventTarget` inside a scope but dispatched from outside it runs unscoped, so a fetch
 * it makes reaches the network unguarded. An adapter that fetches from an event
 * listener must be checked in review before its API joins `SUPPORTED_APIS`.
 */
import type { FetchFunction } from "@earendil-works/pi-ai";
import { AsyncLocalStorage } from "node:async_hooks";

const SCOPE = new AsyncLocalStorage<FetchFunction>();
const NATIVE: FetchFunction = globalThis.fetch;

/** The runtime's own fetch, for the guards to send with. */
export function nativeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return NATIVE(input, init);
}

function interposed(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const guard = SCOPE.getStore();
  return guard === undefined ? NATIVE(input, init) : guard(input, init);
}

/** Runs `start` with `guard` as the global fetch for it and all the work it starts. */
export function within<T>(guard: FetchFunction, start: () => T): T {
  if (globalThis.fetch !== interposed) {
    globalThis.fetch = interposed;
  }
  return SCOPE.run(guard, start);
}
