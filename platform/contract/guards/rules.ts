/**
 * The contract's allow-list, and its source records', checked by the platform's guard kit
 * (`ci/kit/`).
 *
 * The contract is the vocabulary every Worker speaks (ids and grammars, limits, the egress
 * wire, slots, wake records, run events, the source grammar), so it depends on nothing that
 * depends on it: it imports nothing but its own sources, its wake kinds' file and the plain
 * standard modules the Workers runtime and Node both have (`node:crypto`), and its data
 * files. It is pure: no binding, no storage, no network and no state of its own (the
 * module-state guard walks it as every Worker's source, `owner/guards/module-state.test.ts`),
 * so no name of the runtime's I/O appears in it.
 *
 * One exception, by design: a module may be handed the I/O it does, as a parameter typed by
 * the contract's own interface, never a binding it opens. `readControl` takes its store
 * (`ControlStore`) and `migrate` an object's (`MigrationStorage`). That parameter is named
 * `store`, since `storage` is a banned name; the rename is this exception's, not a way round
 * the ban, which still refuses any reach of the runtime's own storage.
 *
 * The source records (`platform/sources/src/`: their schema, checker, catalog, run
 * environment and sign-in shapes) are a contract module beside their files: as pure, held
 * to the same rules, and reaching the contract and their own record files alone. The
 * contract never reaches them.
 */
import { readdirSync } from "node:fs";
import { join } from "node:path";

import type { ImportRules } from "../../ci/kit/imports";
import { PLATFORM } from "../../ci/paths";

/**
 * The contract's data files: the wake kinds, the owner's doors, the tool profiles, the AI
 * apps an owner may connect and the doors' public identity.
 */
const WAKE_KINDS = "contract/wake-kinds.json";
const DATA = Object.freeze([WAKE_KINDS, "contract/doors.json", "contract/profiles.json",
                            "contract/apps.json", "contract/identity.json"]);

export const IMPORTS: ImportRules = Object.freeze({
  worker: "contract",
  called: "the contract",
  outside: DATA,
  bare: Object.freeze({ "node:crypto": null }),
  banned: Object.freeze([{
    pattern: /^(?:fetch|process|node:process|globalThis|caches|storage|WebSocket|DurableObject|WorkerEntrypoint|getByName)$/,
    says: "names %s, the runtime's I/O",
  }]),
});

/** Each file in `platform/<dir>` named `*<extension>`, as an import of it resolves. */
function filesOf(dir: string, extension: string, strip: boolean): string[] {
  return readdirSync(join(PLATFORM, dir)).filter((name) => name.endsWith(extension)).sort()
    .map((name) => `${dir}/${strip ? name.slice(0, -extension.length) : name}`);
}

export const SOURCES_IMPORTS: ImportRules = Object.freeze({
  worker: "sources",
  called: "the source records",
  outside: Object.freeze([...filesOf("contract/src", ".ts", true), ...DATA,
                          ...filesOf("sources", ".json", false)]),
  bare: IMPORTS.bare,
  banned: IMPORTS.banned,
});
