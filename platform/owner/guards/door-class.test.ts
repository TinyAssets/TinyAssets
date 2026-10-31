/**
 * Every door of the owner's object declares its class (`admission.ts`'s `DoorClass`:
 * `work`, `read` or `reduce`), which decides what a suspension leaves it: a billing
 * suspension refuses only `work`, an abuse suspension every door. A door declares it in
 * its binding check, the one place admission is read: each call of the object's binding
 * helpers (`#bind`, `#account`, `#owner`, `#widening`, `#collector`, `#filer`) in a door
 * names the class as its second argument, a literal, and every door makes one. The
 * platform's own `admissionSet` alone binds as `admission`, which reads no admission.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, PLATFORM } from "./sources";

const OWNER_DO = "owner/src/owner-do.ts";
const HELPERS: ReadonlySet<string> = new Set(["#bind", "#account", "#owner", "#widening",
                                              "#collector", "#filer"]);
const CLASSES: ReadonlySet<string> = new Set(["work", "read", "reduce"]);
const ADMISSION_DOOR = "admissionSet";

/** Each door's classes, as its binding calls name them, and each problem found. */
export function doorClasses(source: string):
    { classes: Map<string, string[]>; problems: string[] } {
  const file = parse(OWNER_DO, source);
  const classes = new Map<string, string[]>();
  const problems: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isClassDeclaration(node) && node.name?.text === "OwnerDO") {
      for (const member of node.members) {
        if (!ts.isMethodDeclaration(member) || !ts.isIdentifier(member.name)
            || member.body === undefined
            || member.parameters[0]?.name.getText(file) !== "binding") {
          continue;
        }
        const door = member.name.text;
        const found: string[] = [];
        const look = (inner: ts.Node): void => {
          if (ts.isCallExpression(inner) && ts.isPropertyAccessExpression(inner.expression)
              && inner.expression.expression.kind === ts.SyntaxKind.ThisKeyword
              && HELPERS.has(inner.expression.name.getText(file))) {
            const arg = inner.arguments[1];
            const allowed = door === ADMISSION_DOOR ? new Set(["admission"]) : CLASSES;
            if (arg === undefined || !ts.isStringLiteral(arg) || !allowed.has(arg.text)) {
              problems.push(`${door}: ${inner.getText(file).slice(0, 60)} names no class`);
            } else {
              found.push(arg.text);
            }
          }
          ts.forEachChild(inner, look);
        };
        look(member.body);
        if (found.length === 0) {
          problems.push(`${door}: no binding check names its class`);
        }
        if (new Set(found).size > 1) {
          problems.push(`${door}: binds as more than one class`);
        }
        classes.set(door, found);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return { classes, problems };
}

describe("door-class guard", () => {
  const source = readFileSync(join(PLATFORM, OWNER_DO), "utf8");

  it("finds every door's class in its binding check", () => {
    const { classes, problems } = doorClasses(source);
    expect(problems).toEqual([]);
    expect(classes.size).toBeGreaterThan(60);
    expect(classes.get(ADMISSION_DOOR)).toEqual(["admission"]);
    // The work a billing suspension refuses, and some of what it leaves.
    for (const door of ["agentSubmit", "agentRun", "fileWrite", "fileUploadBegin",
                        "collectionWrite", "policyWrite"]) {
      expect(classes.get(door)?.[0], door).toBe("work");
    }
    for (const door of ["agentStatus", "fileRead", "policyRead", "sessionOpen"]) {
      expect(classes.get(door)?.[0], door).toBe("read");
    }
    for (const door of ["agentCancel", "grantRevoke", "fileRemove", "deleteAgent"]) {
      expect(classes.get(door)?.[0], door).toBe("reduce");
    }
  });

  it("sees a door with no class, a computed one and one bound twice", () => {
    const wrap = (body: string) => `export class OwnerDO { ${body} }`;
    expect(doorClasses(wrap("a(binding: B) { return this.#account(binding); }")).problems)
      .toHaveLength(2);
    expect(doorClasses(wrap("a(binding: B) { return this.#account(binding, k); }")).problems)
      .toHaveLength(2);
    expect(doorClasses(wrap("a(binding: B) { return 1; }")).problems).toHaveLength(1);
    expect(doorClasses(wrap('a(binding: B) { this.#owner(binding, "read", "x"); ' +
                            'this.#account(binding, "work"); }')).problems).toHaveLength(1);
    expect(doorClasses(wrap('a(binding: B) { this.#bind(binding, "admission"); }')).problems)
      .toHaveLength(2);
    expect(doorClasses(wrap('a(binding: B) { this.#filer(binding, "reduce", "x"); }')).problems)
      .toEqual([]);
  });
});
