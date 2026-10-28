/**
 * S30 ledger-boundary: the trusted ledger is reached through the owner object's door only.
 *
 * Ported from `tests/test_ledger_boundary.py` (origin/main 8b415f569) where it is not
 * about the Python box channel. There, only the broker server and account erasure could
 * import the ledger stores, and only those stores named their files. Here, only the
 * object and its alarm's wake runner import the ledger module, and only the ledger and
 * the schema name its tables, so no other module can read or write mail around the
 * ledger's rules. That list only shrinks.
 */
import { describe, expect, it } from "vitest";

import { workerSources } from "./sources";

const LEDGER = "owner/src/ledger.ts";
const IMPORTERS = new Set(["owner/src/owner-do.ts", "owner/src/wakes.ts"]);
const NAMERS = new Set([LEDGER, "owner/src/schema.ts"]);
const TABLES = Object.freeze(["outside_agents", "ledger_messages", "ledger_recipients",
  "ledger_receipts", "ledger_readers", "ledger_wakes", "ledger_rings", "ledger_usage",
  "message_bodies"]);
/** A static import or export from, or a dynamic import of, the ledger module. */
const IMPORT = /(?:\bfrom\s*|\bimport\s*\(?\s*)["'][^"']*\/ledger(?:\.[cm]?[jt]s)?["']/;

/** Every source importing the ledger, and every one naming a ledger table, but its own. */
export function boundary(sources: Map<string, string>): { importers: Set<string>;
                                                          namers: Set<string> } {
  const importers = new Set<string>();
  const namers = new Set<string>();
  for (const [path, source] of sources) {
    if (path !== LEDGER && IMPORT.test(source)) {
      importers.add(path);
    }
    if (TABLES.some((table) => new RegExp(`\\b${table}\\b`).test(source))) {
      namers.add(path);
    }
  }
  return { importers, namers };
}

describe("ledger-boundary guard", () => {
  it("lets only the object and its wake runner import the ledger, and only it name tables",
     () => {
    const { importers, namers } = boundary(workerSources());
    expect(importers).toEqual(IMPORTERS);
    expect(namers).toEqual(NAMERS);
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
