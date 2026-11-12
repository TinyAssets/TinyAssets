/**
 * The platform's reserved caller (tasks 2, "Owner admission and erasure"; F1): the owner's
 * object takes `admissionSet` only from the caller named `PLATFORM_CALLER` (the contract's,
 * `contract/src/binding.ts`), and refuses that name at every other door and as an owner. So
 * no source may spell the name but where it is defined, and no source but the binder, which
 * sends it (the sign-up's explicit admit, slice L), and the owner's object, which reads it,
 * may name the constant.
 *
 * In every Worker source, this guard finds the name spelled as a string (a literal, a
 * template with no substitution, or the obvious split spellings: literals joined by `+`, a
 * template whose every part is literal, an array of literals `join`ed), and the constant's
 * identifier. The name is spelled once, in its definition in the contract; the constant is
 * named only there, in `owner-do.ts`, whose binding check and `admissionSet` read it, and in
 * the binder. A tripwire for our own code's mistakes, as the alarm guard is.
 *
 * And the doors the caller reaches, by class (`contract/doors.json`): the platform's own
 * (`admission`, the reserved and erasure callers'), and the cutover's import doors (`import`,
 * its import line's alone, with an import binding while the owner is pending, `import.ts`),
 * which the binder sends as it sends the admit (`bindImport`).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { doorTable } from "../../contract/src/doors";
import { parse, reporter, workerSources } from "../../ci/kit/sources";

/** The reserved name, as this guard spells it apart from the sources it reads. */
const NAME = ["plat", "form"].join("");
/** The constant that holds it, its one definition, and the one source that reads it. */
const CONSTANT = "PLATFORM_CALLER";
const DEFINED = "contract/src/binding.ts";
const READER = "owner/src/owner-do.ts";
/** The binder, which sends it. */
const SENDER = "doors/src/binder.ts";

/** A string expression's value when every part of it is literal, else undefined. */
function literal(node: ts.Node): string | undefined {
  if (ts.isStringLiteralLike(node)) {
    return node.text;
  }
  if (ts.isParenthesizedExpression(node)) {
    return literal(node.expression);
  }
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const [left, right] = [literal(node.left), literal(node.right)];
    return left === undefined || right === undefined ? undefined : left + right;
  }
  if (ts.isTemplateExpression(node)) {
    let text = node.head.text;
    for (const span of node.templateSpans) {
      const part = literal(span.expression);
      if (part === undefined) {
        return undefined;
      }
      text += part + span.literal.text;
    }
    return text;
  }
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.name.text === "join"
      && ts.isArrayLiteralExpression(node.expression.expression)) {
    const parts = node.expression.expression.elements.map(literal);
    const by = node.arguments.length === 0 ? "," : literal(node.arguments[0]!);
    return by === undefined || parts.some((part) => part === undefined) ? undefined
      : parts.join(by);
  }
  return undefined;
}

/** Whether `source` names the constant's identifier anywhere. */
function names(fileName: string, source: string): boolean {
  let found = false;
  const visit = (node: ts.Node): void => {
    found ||= ts.isIdentifier(node) && node.text === CONSTANT;
    ts.forEachChild(node, visit);
  };
  visit(parse(fileName, source));
  return found;
}

/** Each place `source` spells the reserved caller or names its constant out of place. */
export function callerReach(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  let definitions = 0;
  const visit = (node: ts.Node): void => {
    const value = ts.isExpression(node) ? literal(node) : undefined;
    const outer = node.parent !== undefined && ts.isExpression(node.parent)
      && literal(node.parent) !== undefined;
    if (value === NAME && !outer) {
      const defines = fileName === DEFINED && ts.isVariableDeclaration(node.parent)
        && node.parent.name.getText(file) === CONSTANT && ts.isStringLiteral(node);
      if (defines) {
        definitions++;
      } else {
        report(node, "spells the platform's reserved caller");
      }
    }
    if (ts.isIdentifier(node) && node.text === CONSTANT && fileName !== DEFINED
        && fileName !== READER && fileName !== SENDER) {
      report(node, `names ${CONSTANT}, which only the object reads and the binder sends`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  if (definitions > 1) {
    problems.push(`${fileName}: defines the reserved caller more than once`);
  }
  return problems;
}

describe("admission caller guard", () => {
  it("finds the platform's reserved caller spelled only where it is defined", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain(DEFINED);
    expect([...sources].flatMap(([path, source]) => callerReach(path, source))).toEqual([]);
    expect(sources.get(DEFINED)).toContain(`export const ${CONSTANT} = "${NAME}";`);
    // The object reads the constant, and the binder sends it (the sign-up's admit).
    expect([...sources].filter(([path, source]) => path !== DEFINED && names(path, source))
      .map(([path]) => path).sort()).toEqual([SENDER, READER]);
  });

  it.each([
    `export const b = { owner: o, caller: "${NAME}", origin: null };`,
    `export const c = '${NAME}';`,
    `export const c = \`${NAME}\`;`,
    'export const c = "plat" + "form";',
    'export const c = `plat${"form"}`;',
    'export const c = ["plat", "form"].join("");',
    'export const c = ("pla" + "t") + "form";',
    `import { ${CONSTANT} } from "../../owner/src/admission";`,
  ])("sees %s", (source) => {
    expect(callerReach("doors/src/probe.ts", source)).not.toEqual([]);
  });

  it("sees a second definition, and the name beside its definition", () => {
    expect(callerReach(DEFINED, `export const ${CONSTANT} = "${NAME}";\n` +
                                `const again = "${NAME}";`)).toHaveLength(1);
    expect(callerReach(DEFINED, `export const ${CONSTANT} = "${NAME}";\n` +
                                `export const OTHER = "${NAME}";`)).toHaveLength(1);
  });

  it.each([
    'export const c = "the platform sets it";',
    'export const c = "platforms";',
    "export const PLATFORM = 1;",
  ])("allows %s", (source) => {
    expect(callerReach("doors/src/probe.ts", source)).toEqual([]);
  });

  it("pins the tombstone's shape, which an erased owner's database keeps unmigrated", () => {
    // A tombstone is never migrated, and migration 10 and the erasure share the table's
    // text: changing either is a new migration and a tombstone reader that still reads this.
    // (Slice L added `pending` in place: nothing is deployed, so no tombstone exists yet.)
    const schema = readFileSync(join(PLATFORM, "owner", "src", "schema.ts"), "utf8");
    expect(schema).toContain(
      'export const ADMISSION_TABLE = "CREATE TABLE owner_admission (only INTEGER PRIMARY KEY " +\n' +
      '  "CHECK (only = 1), state TEXT NOT NULL CHECK (state IN (\'pending\', \'admitted\', " +\n' +
      '  "\'suspended\', \'erased\')), reasons TEXT NOT NULL CHECK (reasons IN (\'[]\', " +\n');
    const admission = readFileSync(join(PLATFORM, "owner", "src", "admission.ts"), "utf8");
    expect(admission).toContain('sql.exec<{ state: string }>("SELECT state FROM owner_admission")');
    expect(admission).toContain("INSERT INTO owner_admission (only, state, reasons) VALUES " +
                                "(1, 'erased', '[]')");
  });

  it("reaches the platform's own doors and the import's alone, each by its class", () => {
    const rows = doorTable();
    const by = (test: (row: (typeof rows)[number]) => boolean) =>
      rows.filter(test).map((row) => row.door).sort();
    expect(by((row) => row.class === "admission"))
      .toEqual(by((row) => row.caller === "reserved" || row.caller === "erasure"));
    expect(by((row) => row.class === "import"))
      .toEqual(by((row) => row.caller === "import" || row.caller === "discard"));
    expect(by((row) => row.class === "import")).toEqual(["importCheck", "importDiscard",
      "importFileBegin", "importFileCommit", "importFileStored", "importFiles", "importNotice",
      "importRows"]);
    // The discard alone takes a rehearsal's name past pending.
    expect(by((row) => row.caller === "discard")).toEqual(["importDiscard"]);
    expect(rows.filter((row) => row.class === "import").every((row) => row.handler === "import"))
      .toBe(true);
    // The caller's constant is the binding check's, and its sender the binder's import too.
    expect(workerSources().get(SENDER)).toContain("export function bindImport(");
  });
});
