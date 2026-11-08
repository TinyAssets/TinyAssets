/**
 * S30 ledger-boundary: the trusted ledger is reached through the owner object's door only.
 *
 * Ported from `tests/test_ledger_boundary.py` (origin/main 8b415f569) where it is not
 * about the Python box channel. There, only the broker server and account erasure could
 * import the ledger stores, and only those stores named their files. Here, only the
 * object and its alarm's wake runner import the ledger module, and only the ledger and
 * the schema name its tables, so no other module can read or write mail around the
 * ledger's rules. That list only shrinks, but for the object's own lanes moved out of the
 * object to their modules (untangle A2), each reaching the ledger only through its methods,
 * as the object does: the archive lane and its reads of archived mail (`archive.ts`), and
 * the runs lane, its mail delivered as runs and their replies posted (`launch.ts`). The owner export
 * (`export.ts`, slice P) names
 * them too, only to read them for the owner (its source holds no SQL but `SELECT`, held by
 * the policy-boundary guard's read-only check).
 *
 * The doors' handlers sit in their modules, and reach the ledger through the state the
 * object hands them (`OwnerState`), not by an import (review F3). The modules that name the
 * state's ledger are held the same way (`REACHERS`): the ledger's own doors, the doors
 * whose rows act on mail too (a collection's, a file's, a grant's, an inbound hook's), and
 * the runs lane. That list only shrinks too. The policy-boundary guard holds the state to
 * be used only as `o.<member>` (or handed to a parameter named `o`), so no alias or
 * destructuring reaches the ledger past this list.
 */
import { describe, expect, it } from "vitest";

import { workerSources } from "../../ci/kit/sources";

const LEDGER = "owner/src/ledger.ts";
const IMPORTERS = new Set(["owner/src/owner-do.ts", "owner/src/wakes.ts",
                           "owner/src/archive.ts", "owner/src/launch.ts"]);
const NAMERS = new Set([LEDGER, "owner/src/schema.ts", "owner/src/export.ts"]);
const TABLES = Object.freeze(["outside_agents", "native_agents", "ledger_messages",
  "ledger_recipients",
  "ledger_receipts", "ledger_readers", "ledger_wakes", "ledger_usage",
  "message_bodies", "wake_callbacks"]);
/** The modules that name the state's ledger (`o.ledger`, the launcher's `this.#o.ledger`). */
const REACHERS = new Set([LEDGER, "owner/src/collections.ts", "owner/src/files.ts",
                          "owner/src/grants.ts", "owner/src/inbound.ts", "owner/src/launch.ts"]);
/** The state's ledger, as a handler, the launcher or a holder of the state names it. */
const REACH = /(?:\bo|\bstate|\bthis\.#o|\bthis\.#state)\.ledger\b/;
/** A static import or export from, or a dynamic import of, the ledger module. */
const IMPORT = /(?:\bfrom\s*|\bimport\s*\(?\s*)["'][^"']*\/ledger(?:\.[cm]?[jt]s)?["']/;

/** Every source importing the ledger, and every one naming a ledger table, but its own. */
export function boundary(sources: Map<string, string>): { importers: Set<string>;
                                                          namers: Set<string>;
                                                          reachers: Set<string> } {
  const importers = new Set<string>();
  const namers = new Set<string>();
  const reachers = new Set<string>();
  for (const [path, source] of sources) {
    if (REACH.test(source)) {
      reachers.add(path);
    }
    if (path !== LEDGER && IMPORT.test(source)) {
      importers.add(path);
    }
    if (TABLES.some((table) => new RegExp(`\\b${table}\\b`).test(source))) {
      namers.add(path);
    }
  }
  return { importers, namers, reachers };
}

describe("ledger-boundary guard", () => {
  it("lets only the object and its wake runner import the ledger, and only it name tables",
     () => {
    const { importers, namers } = boundary(workerSources());
    expect(importers).toEqual(IMPORTERS);
    expect(namers).toEqual(NAMERS);
  });

  it("lets only the mail's doors and the runs lane reach the ledger through the state", () => {
    expect(boundary(workerSources()).reachers).toEqual(REACHERS);
    for (const line of ["o.ledger.post(1);", "const { ledger } = this.#o.ledger;"]) {
      expect(boundary(new Map([["owner/src/probe.ts", line]])).reachers)
        .toEqual(new Set(["owner/src/probe.ts"]));
    }
    expect(boundary(new Map([["owner/src/probe.ts", "this.#ledger.post(1); go.ledger;"]]))
      .reachers).toEqual(new Set());
  });

  it.each([
    'import { Ledger } from "./ledger";',
    "import type { Agent } from './ledger.ts';",
    'export { OWNER } from "../src/ledger";',
    'const ledger = await import("./ledger");',
  ])("sees %s", (line) => {
    expect(boundary(new Map([["owner/src/probe.ts", line]])).importers)
      .toEqual(new Set(["owner/src/probe.ts"]));
  });

  it("sees a module reaching a ledger table around the ledger", () => {
    const probe = 'sql.exec("SELECT body FROM message_bodies");';
    expect(boundary(new Map([["owner/src/probe.ts", probe]])).namers)
      .toEqual(new Set(["owner/src/probe.ts"]));
    expect(boundary(new Map([["owner/src/probe.ts", 'import { x } from "./ledgers";']]))
      .importers).toEqual(new Set());
  });
});
