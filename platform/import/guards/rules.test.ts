/**
 * Rule 0's own table (`rules.ts`): it names nothing twice (it would not load), every rule
 * says why, a shared store's table is never archived into one owner's tree (it holds every
 * owner's rows), and a name is matched by its exact rule or a family the old stack minted,
 * never by a default; and every table the old stack's own DDL makes in a store ruled table
 * by table has its rule.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  ACCOUNT, CENTER, type EntryRule, type Level, ROOT, ruleOf, SESSIONS, SHARED, tableUse,
} from "../rules";

function rules(level: Level, at = ""): [string, EntryRule][] {
  return Object.entries(level).flatMap(([name, rule]) => [[`${at}${name}`, rule] as [string, EntryRule],
    ...(rule.kind === "dir" ? rules(rule.entries, `${at}${name}/`) : [])]);
}

describe("rule 0", () => {
  it("gives every entry a reason", () => {
    for (const level of [SHARED, ACCOUNT, ROOT, CENTER, SESSIONS]) {
      for (const [name, rule] of rules(level)) {
        expect(rule.why.length, name).toBeGreaterThan(8);
      }
    }
  });

  it("never archives a shared store's table into an owner's tree", () => {
    for (const [name, rule] of rules(SHARED)) {
      if (rule.kind === "store") {
        expect(rule.tables === "archive" || (typeof rule.tables === "object"
          && Object.values(rule.tables).includes("archive")), name).toBe(false);
      }
    }
  });

  it("carries live only the admission, tier, tombstones, policy and collections", () => {
    const carried = [SHARED, ACCOUNT, ROOT, CENTER, SESSIONS].flatMap((level) => rules(level))
      .flatMap(([name, rule]) => rule.kind === "store" && typeof rule.tables === "object"
        ? Object.entries(rule.tables).filter(([, use]) => use === "carry")
          .map(([table]) => `${name}/${table}`) : []);
    expect(carried.sort()).toEqual([
      ".broker/credential-custody.db/deleted_principals", ".broker/plans.db/plans",
      ".broker/state/owner-identities.db/owner_admission",
      ".broker/state/owner-identities.db/owner_identities",
      ".router-deleted-owners.sqlite3/deleted_owners", "conversations.db/collection_rows",
      "policy.db/policy",
    ]);
  });

  it("matches a store's old backup copy and the minted heartbeats, and nothing else by pattern", () => {
    expect(ruleOf(CENTER, ".conversation_memory.db.bak-premigrate-20261002"))
      .toBe(CENTER[".conversation_memory.db"]);
    expect(ruleOf(CENTER, ".worker_supervisor.queue-7.json")?.kind).toBe("drop");
    expect(ruleOf(CENTER, "AGENTS.md")?.kind).toBe("user");
    expect(ruleOf(CENTER, "chapter-1.md")).toBeNull();
    expect(ruleOf(CENTER, "AGENTS.md.bak-1")).toBeNull();
    expect(ruleOf(ROOT, "novel")).toBeNull();
    const policy = ACCOUNT["policy.db"]!;
    expect(policy.kind === "store" && [tableUse(policy, "policy"), tableUse(policy, "receipts"),
      tableUse(policy, "once_approvals"), tableUse(policy, "coupons")])
      .toEqual(["carry", "archive", "drop", null]);
  });
});

/**
 * The old stack's DDL by store (`old-tables.json`): rule 0's sweep, every `CREATE TABLE` of
 * `tinyassets/` placed in the store its module opens (`import12_report.md` says how). A
 * store's older names (`.workflow.db`, `.author_server.db`) hold the main store's tables.
 */
const OLD = (JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)),
                                          "old-tables.json"), "utf8")) as
  { stores: Record<string, { tables: string[] }> }).stores;
const LEVELS = { SHARED, ACCOUNT, ROOT, CENTER, SESSIONS } as const;
const ALIASES: Readonly<Record<string, string>> = { ".workflow.db": ".tinyassets.db",
                                                    ".author_server.db": ".tinyassets.db" };
const swept = (store: string): string => {
  const [where, name] = store.split(":") as [string, string];
  return `${where}:${ALIASES[name] ?? name}`;
};

describe("rule 0 against the old stack's own DDL", () => {
  const mapped = Object.entries(LEVELS).flatMap(([where, level]) => rules(level)
    .filter(([, rule]) => rule.kind === "store" && typeof rule.tables === "object")
    .map(([name, rule]) => [`${where}:${name}`, rule] as const));

  it("names every table each table-mapped store's old DDL makes", () => {
    const missing = mapped.flatMap(([store, rule]) => (OLD[swept(store)]?.tables ?? [])
      .filter((table) => rule.kind === "store" && tableUse(rule, table) === null)
      .map((table) => `${store}/${table}`));
    expect(missing).toEqual([]);
  });

  it("swept every table-mapped store, and rules each swept store table by table", () => {
    const keys = new Set(mapped.map(([store]) => swept(store)));
    expect([...keys].filter((key) => OLD[key] === undefined)).toEqual([]);
    expect(Object.keys(OLD).filter((key) => !keys.has(key))).toEqual([]);
  });
});
