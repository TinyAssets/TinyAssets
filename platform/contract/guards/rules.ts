/**
 * The contract's allow-list, checked by the platform's guard kit (`ci/kit/`).
 *
 * The contract is the vocabulary every Worker speaks (ids and grammars, limits, the egress
 * wire, slots, wake records, run events, the source grammar), so it depends on nothing that
 * depends on it: it imports nothing but its own sources, its wake kinds' file and the plain
 * standard modules the Workers runtime and Node both have (`node:crypto`). It is pure: no
 * binding, no storage, no network and no state of its own (the module-state guard walks it
 * as every Worker's source, `owner/guards/module-state.test.ts`), so no name of the runtime's
 * I/O appears in it.
 */
import type { ImportRules } from "../../ci/kit/imports";

export const IMPORTS: ImportRules = Object.freeze({
  worker: "contract",
  called: "the contract",
  outside: Object.freeze(["contract/wake-kinds.json"]),
  bare: Object.freeze({ "node:crypto": null }),
  banned: Object.freeze([{
    pattern: /^(?:fetch|process|node:process|globalThis|caches|storage|WebSocket|DurableObject|WorkerEntrypoint|getByName)$/,
    says: "names %s, the runtime's I/O",
  }]),
});
