/**
 * Every public method of `OwnerDO` is a door of the table (`contract/doors.json`), and every
 * door of the table is one of its methods: read from the object's source, so a method with
 * no row, or a row with no method, fails here before any test runs it. The runtime's own
 * entries (`fetch`, `alarm` and the hibernating sockets', `override`s) are no doors.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { doorTable } from "../../contract/src/doors";
import { parse, workerSources } from "../../ci/kit/sources";

const OWNER_DO = "owner/src/owner-do.ts";

/** The public methods of `source`'s `OwnerDO`, but the runtime's own overrides. */
export function doorMethods(source: string): string[] {
  const file = parse(OWNER_DO, source);
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isClassDeclaration(node) && node.name?.text === "OwnerDO") {
      for (const member of node.members) {
        const modifiers = ts.canHaveModifiers(member) ? ts.getModifiers(member) ?? [] : [];
        if (ts.isMethodDeclaration(member) && ts.isIdentifier(member.name)
            && !modifiers.some((m) => m.kind === ts.SyntaxKind.OverrideKeyword
                                      || m.kind === ts.SyntaxKind.PrivateKeyword
                                      || m.kind === ts.SyntaxKind.ProtectedKeyword
                                      || m.kind === ts.SyntaxKind.StaticKeyword)) {
          found.push(member.name.text);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/** Each door of `rows` with no method, and each method with no row, as `name: what`. */
export function unmatched(methods: readonly string[], rows: readonly string[]): string[] {
  return [...methods.filter((name) => !rows.includes(name)).map((name) => `${name}: no row`),
          ...rows.filter((name) => !methods.includes(name)).map((name) => `${name}: no method`)];
}

describe("the object's doors", () => {
  const rows = doorTable().map((row) => row.door);

  it("are exactly the door table's rows, method for row", () => {
    const methods = doorMethods(workerSources().get(OWNER_DO)!);
    expect(unmatched(methods, rows)).toEqual([]);
    expect(methods.length).toBe(rows.length);
    expect(methods.length).toBeGreaterThan(100);
  });

  it.each([
    ["a method with no row", "export class OwnerDO { fileStat() {} fileProbe() {} }",
     ["fileStat"], ["fileProbe: no row"]],
    ["a row with no method", "export class OwnerDO { fileStat() {} }",
     ["fileStat", "fileProbe"], ["fileProbe: no method"]],
    ["no runtime entry, private member or override taken for a door",
     "export class OwnerDO { override fetch() {} #door() {} private x() {} fileStat() {} }",
     ["fileStat"], []],
  ])("sees %s", (_what, source, names, expected) => {
    expect(unmatched(doorMethods(source), names)).toEqual(expected);
  });
});
