/**
 * The `owed` ratchet (untangle wave 2): a guard can land before the fixes that make it pass.
 *
 * Each workspace's `guards/rules.ts` may export `OWED`, the violations it still owes, each
 * tagged with the fix that removes it. A guard fails on any violation its workspace does not
 * list, and on any listed entry that no longer violates, so a list only shrinks, and each
 * workspace edits only its own. A workspace with no `OWED` owes nothing.
 */
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { PLATFORM } from "../paths.ts";

export interface Owed {
  /** The guard that finds it (`"layers"`, the import-layer table's). */
  readonly guard: string;
  /** The violation, exactly as the guard names it. */
  readonly what: string;
  /** The fix that removes it (`"B23"`). */
  readonly fix: string;
}

/** A violation a guard found, in the workspace that owes it. */
export interface Found {
  readonly workspace: string;
  readonly what: string;
}

/**
 * What `from` owes the layer table (`layers.ts`) for each of its imports `to` lands on, until
 * `fix` removes them: paths from the repository root, as the table's guard names them.
 */
export function imports(fix: string, from: string, to: readonly string[]): Owed[] {
  return to.map((each) => Object.freeze({ guard: "layers", what: `${from} imports ${each}`, fix }));
}

/** The file that holds a workspace's `OWED`. */
export const rulesOf = (workspace: string): string => `platform/${workspace}/guards/rules.ts`;

/** Every workspace's `OWED`, read from its `guards/rules.ts`, by workspace. */
export async function owedLists(): Promise<Map<string, readonly Owed[]>> {
  const lists = new Map<string, readonly Owed[]>();
  for (const workspace of readdirSync(PLATFORM).sort()) {
    const path = join(PLATFORM, workspace, "guards", "rules.ts");
    if (existsSync(path)) {
      const { OWED } = await import(path) as { OWED?: readonly Owed[] };
      if (OWED !== undefined) {
        lists.set(workspace, OWED);
      }
    }
  }
  return lists;
}

/**
 * Each way `found`, `guard`'s violations, and what the workspaces owe disagree: a violation
 * its workspace does not owe, an owed one that no longer holds, an entry owed twice, and an
 * entry with no fix named.
 */
export function ratchet(guard: string, found: readonly Found[],
                        owed: ReadonlyMap<string, readonly Owed[]>): string[] {
  const problems: string[] = [];
  const held = new Set(found.map((each) => `${each.workspace}\n${each.what}`));
  const listed = new Set<string>();
  for (const [workspace, entries] of owed) {
    for (const entry of entries.filter((each) => each.guard === guard)) {
      const key = `${workspace}\n${entry.what}`;
      if (!/^B[1-9]\d*$/.test(entry.fix)) {
        problems.push(`${rulesOf(workspace)} owes "${entry.what}" with no fix named ` +
                      `("${entry.fix}")`);
      }
      if (listed.has(key)) {
        problems.push(`${rulesOf(workspace)} owes "${entry.what}" twice`);
      } else if (!held.has(key)) {
        problems.push(`${rulesOf(workspace)} owes "${entry.what}", which no longer holds: ` +
                      "delete the entry");
      }
      listed.add(key);
    }
  }
  for (const each of found) {
    if (!listed.has(`${each.workspace}\n${each.what}`)) {
      problems.push(`${each.what}: owed by no one (fix it; ${rulesOf(each.workspace)}'s ` +
                    "OWED lists only what the fix it names will remove)");
    }
  }
  return [...new Set(problems)];
}
