/**
 * The contract's banned names, checked by the platform's guard kit (`ci/kit/`).
 *
 * The contract is the vocabulary every Worker speaks (ids and grammars, limits, the egress
 * wire, slots, wake records, run events, the source grammar), so it depends on nothing that
 * depends on it: the import-layer table (`ci/kit/layers.ts`) lets it import nothing but its
 * own sources, its data files and `node:crypto`. It is pure: no binding, no storage, no
 * network and no state of its own (the module-state guard walks it as every Worker's source,
 * `ci/guards/module-state.test.ts`), so no name of the runtime's I/O appears in it. A module
 * may be handed the I/O it does, as a parameter typed by the contract's own interface, never
 * a binding it opens: `readControl` takes its store (`ControlStore`) and `migrate` an
 * object's (`MigrationStorage`).
 *
 */
import type { ImportRules } from "../../ci/kit/imports";
import { kernels, type Owed } from "../../ci/kit/owed";

export const IMPORTS: ImportRules = Object.freeze({
  worker: "contract",
  called: "the contract",
  banned: Object.freeze([{
    pattern: /^(?:fetch|process|node:process|globalThis|caches|WebSocket|DurableObject|WorkerEntrypoint|getByName)$/,
    says: "names %s, the runtime's I/O",
  }]),
});

/**
 * What the contract still owes the platform's guards (`ci/kit/owed.ts`): the import
 * message's two SHA-256s beside `digest.ts` (`ci/kit/kernels.ts`), until lane I's B7 adopts
 * them.
 */
export const OWED: readonly Owed[] = Object.freeze([
  ...kernels("B7", "SHA-256", { "platform/contract/src/import-message.ts": 2 }),
]);
