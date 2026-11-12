/**
 * The contract's banned names, and its source records', checked by the platform's guard kit
 * (`ci/kit/`).
 *
 * The contract is the vocabulary every Worker speaks (ids and grammars, limits, the egress
 * wire, slots, wake records, run events, the source grammar), so it depends on nothing that
 * depends on it: the import-layer table (`ci/kit/layers.ts`) lets it import nothing but its
 * own sources, its data files and `node:crypto`. It is pure: no binding, no storage, no
 * network and no state of its own (the module-state guard walks it as every Worker's source,
 * `owner/guards/module-state.test.ts`), so no name of the runtime's I/O appears in it.
 *
 * One exception, by design: a module may be handed the I/O it does, as a parameter typed by
 * the contract's own interface, never a binding it opens. `readControl` takes its store
 * (`ControlStore`) and `migrate` an object's (`MigrationStorage`). That parameter is named
 * `store`, since `storage` is a banned name; the rename is this exception's, not a way round
 * the ban, which still refuses any reach of the runtime's own storage.
 *
 * The source records (`platform/sources/src/`: their schema, checker, catalog, run
 * environment and sign-in shapes) are a contract module beside their files: as pure, held
 * to the same names, and, by the layer table, reaching the contract and their own record
 * files alone. The contract never reaches them.
 */
import type { ImportRules } from "../../ci/kit/imports";

export const IMPORTS: ImportRules = Object.freeze({
  worker: "contract",
  called: "the contract",
  banned: Object.freeze([{
    pattern: /^(?:fetch|process|node:process|globalThis|caches|storage|WebSocket|DurableObject|WorkerEntrypoint|getByName)$/,
    says: "names %s, the runtime's I/O",
  }]),
});

export const SOURCES_IMPORTS: ImportRules = Object.freeze({
  worker: "sources",
  called: "the source records",
  banned: IMPORTS.banned,
});
