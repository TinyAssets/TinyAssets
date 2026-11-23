/**
 * What a reader's source reads of the owner's answers, as its compiler finds it: the one
 * derivation of the fields the door table declares each answer with (`contract/doors.json`
 * `answer`), so the table says exactly what the readers a deploy pairs with the owner read
 * (`docs/decisions/untangle-2026-11/answer-readers.md`).
 *
 * The readers are compiled together, TypeScript and JavaScript alike (a JavaScript file's
 * types are its JSDoc's and what its compiler infers; nothing in it is reported as an error).
 * A value is a door's answer where its type's fields are the ones its row declares
 * (`contract/src/requests.ts` `DoorAnswers`, `UnreadAnswers`) or the narrowings of them the
 * doors' code reads (`contract/src/owner.ts` `DoorTypes` `answer`), at the answer's top level
 * (a field's own fields are left to the types alone). A field is read where the code
 *
 * - reads it: by name (`answer.field`, `answer["field"]`, a destructuring), or asks whether
 *   it is there (`"field" in answer`);
 * - or hands the answer to a type of its own that has the field (an argument, a return, a
 *   variable, a spread into an object literal, each by its type there), which reads it in
 *   turn. Handing it to the answer's own type, or to one with no fields (`unknown`, `object`),
 *   reads nothing: whatever reads it from there is found where it does.
 *
 * Two reads no type holds are refused outright (`unheld`): an `in` check on a bare `object`
 * (the answer of a row that declares none), and a cast of an answer to a type of its own.
 */
import { join, relative } from "node:path";

import ts from "typescript";

import { PLATFORM } from "../paths.ts";

/** One read of an answer's field: its door, the field, and where, `<path>:<line>`. */
export interface AnswerRead {
  readonly door: string;
  readonly field: string;
  readonly at: string;
}

export interface AnswerReads {
  /** Every read of a field an answer's type has (one marked `unread` among them). */
  readonly reads: readonly AnswerRead[];
  /** Every read of a field the answer's type does not have (an `in` check, a script's read). */
  readonly undeclared: readonly AnswerRead[];
  /** Every read no type holds, by where and what it is. */
  readonly unheld: readonly string[];
}

/** The modules the answers' types are declared in, from `platform/`. */
const ANSWER_TYPES = Object.freeze(["contract/src/requests.ts", "contract/src/owner.ts"]);

/**
 * The platform's compiler options (its `tsconfig.json`), with its scripts compiled too, and
 * `paths` where the doors serve their app's scripts at a path of their own.
 */
function options(paths: Readonly<Record<string, readonly string[]>>): ts.CompilerOptions {
  const read = ts.readConfigFile(join(PLATFORM, "tsconfig.json"), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, PLATFORM).options;
  return { ...parsed, allowJs: true, checkJs: false,
           paths: Object.fromEntries(Object.entries(paths).map(([key, to]) => [key, [...to]])) };
}

/**
 * The readers `roots` (paths from `platform/`) compiled together, each file `overlay` names
 * read as the text it gives (one not on disk among them, a probe), with `paths` as the
 * compiler's (a served path's files, by the folder they are in).
 */
export function readerProgram(roots: readonly string[],
                              paths: Readonly<Record<string, readonly string[]>> = {},
                              overlay: Readonly<Record<string, string>> = {}): ts.Program {
  const compiled = options(paths);
  const host = ts.createCompilerHost(compiled);
  const given = new Map(Object.entries(overlay).map(([path, text]) =>
    [join(PLATFORM, path), text]));
  const getSourceFile = host.getSourceFile.bind(host);
  const fileExists = host.fileExists.bind(host);
  const readFile = host.readFile.bind(host);
  host.getSourceFile = (name, version, ...rest) => {
    const text = given.get(name);
    return text === undefined ? getSourceFile(name, version, ...rest)
      : ts.createSourceFile(name, text, version, true);
  };
  host.fileExists = (name) => given.has(name) || fileExists(name);
  host.readFile = (name) => given.get(name) ?? readFile(name);
  const files = [...new Set([...roots, ...Object.keys(overlay)])]
    .map((path) => join(PLATFORM, path));
  return ts.createProgram(files, compiled, host);
}

/** The top-level types of each door's answer, by the node that declares their fields. */
function answerNodes(program: ts.Program, checker: ts.TypeChecker): Map<ts.Node, Set<string>> {
  const nodes = new Map<ts.Node, Set<string>>();
  const arms = (type: ts.TypeNode, door: string): void => {
    if (ts.isUnionTypeNode(type) || ts.isIntersectionTypeNode(type)) {
      type.types.forEach((each) => arms(each, door));
    } else if (ts.isParenthesizedTypeNode(type)) {
      arms(type.type, door);
    } else if (ts.isTypeLiteralNode(type)) {
      (nodes.get(type) ?? nodes.set(type, new Set()).get(type)!).add(door);
    } else if (ts.isTypeReferenceNode(type)) {
      // A named arm (`EgressRefusal`): its fields are each door's whose answer names it.
      const named = checker.getSymbolAtLocation(type.typeName);
      const symbol = named !== undefined && (named.flags & ts.SymbolFlags.Alias) !== 0
        ? checker.getAliasedSymbol(named) : named;
      for (const declaration of symbol?.declarations ?? []) {
        if (ts.isInterfaceDeclaration(declaration)) {
          (nodes.get(declaration) ?? nodes.set(declaration, new Set()).get(declaration)!)
            .add(door);
        } else if (ts.isTypeAliasDeclaration(declaration)) {
          arms(declaration.type, door);
        }
      }
    }
  };
  for (const path of ANSWER_TYPES) {
    const file = program.getSourceFile(join(PLATFORM, path));
    if (file === undefined) {
      throw new Error(`${path} is not among the readers' modules`);
    }
    for (const statement of file.statements) {
      if (!ts.isInterfaceDeclaration(statement)) {
        continue;
      }
      const name = statement.name.text;
      for (const member of statement.members) {
        if (!ts.isPropertySignature(member) || member.type === undefined
            || !ts.isIdentifier(member.name)) {
          continue;
        }
        const door = member.name.text;
        if (name === "DoorAnswers" || name === "UnreadAnswers") {
          arms(member.type, door);
        } else if (name === "DoorTypes" && ts.isTypeLiteralNode(member.type)) {
          for (const part of member.type.members) {
            if (ts.isPropertySignature(part) && part.type !== undefined
                && ts.isIdentifier(part.name) && part.name.text === "answer") {
              arms(part.type, door);
            }
          }
        }
      }
    }
  }
  return nodes;
}

/**
 * What the readers `files` (paths from `platform/`, each in `program`) read of the owner's
 * answers.
 */
export function answerReads(program: ts.Program, files: readonly string[]): AnswerReads {
  const checker = program.getTypeChecker();
  const nodes = answerNodes(program, checker);
  const reads: AnswerRead[] = [];
  const undeclared: AnswerRead[] = [];
  const unheld: string[] = [];

  /** The doors whose answer's top-level field `field` is. */
  const doorsOf = (field: ts.Symbol): Set<string> => new Set((field.declarations ?? [])
    .flatMap((declaration) => [...nodes.get(declaration.parent) ?? []]));
  /** Each object arm of `type`, a promise's value's for an awaited one. */
  const armsOf = (type: ts.Type, awaited: boolean): readonly ts.Type[] => {
    const value = awaited ? checker.getAwaitedType(type) ?? type : type;
    return value.isUnion() ? value.types : [value];
  };
  /** Each arm of `type` that is a door's answer, with its doors (each type's, once). */
  const seen = new Map<ts.Type, [ts.Type, Set<string>][]>();
  const answersIn = (type: ts.Type, awaited = false): [ts.Type, Set<string>][] =>
    armsOf(type, awaited).flatMap((arm) => {
      let found = seen.get(arm);
      if (found === undefined) {
        const doors = new Set(checker.getPropertiesOfType(arm).flatMap((field) =>
          [...doorsOf(field)]));
        found = doors.size === 0 ? [] : [[arm, doors]];
        seen.set(arm, found);
      }
      return found;
    });
  const where = (node: ts.Node): string => {
    const file = node.getSourceFile();
    return `${relative(PLATFORM, file.fileName)}:${
      file.getLineAndCharacterOfPosition(node.getStart()).line + 1}`;
  };

  /**
   * A read of `name` from a value of type `type`, at `node`: of each answer arm that has it,
   * or, where no arm of `type` has it, a read of a field no answer arm declares.
   */
  const read = (type: ts.Type, name: string, node: ts.Node): void => {
    const had = armsOf(type, false).some((arm) => checker.getPropertyOfType(arm, name));
    for (const [arm, doors] of answersIn(type)) {
      const field = checker.getPropertyOfType(arm, name);
      if (field !== undefined) {
        reads.push(...[...doorsOf(field)].map((door) => ({ door, field: name, at: where(node) })));
      } else if (!had) {
        undeclared.push(...[...doors].map((door) => ({ door, field: name, at: where(node) })));
      }
    }
  };
  /** A value at `node` handed to the type the code takes it as there. */
  const handed = (node: ts.Expression): void => {
    const found = answersIn(checker.getTypeAtLocation(node), true);
    if (found.length === 0) {
      return;
    }
    const target = checker.getContextualType(node);
    if (target === undefined || (target.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) !== 0
        || answersIn(target, true).length > 0) {
      return;
    }
    const taken = armsOf(target, true);
    for (const [arm] of found) {
      for (const field of checker.getPropertiesOfType(arm)) {
        if (taken.some((each) => checker.getPropertyOfType(each, field.name) !== undefined)) {
          reads.push(...[...doorsOf(field)].map((door) =>
            ({ door, field: field.name, at: where(node) })));
        }
      }
    }
  };
  /**
   * A cast of an answer to a type of its own, through any casts between (`as unknown as`): a
   * read no type holds.
   */
  const cast = (node: ts.Expression, operand: ts.Expression): void => {
    let inner = operand;
    while (ts.isAsExpression(inner) || ts.isTypeAssertionExpression(inner)
           || ts.isParenthesizedExpression(inner)) {
      inner = inner.expression;
    }
    const target = checker.getTypeAtLocation(node);
    if (answersIn(checker.getTypeAtLocation(inner)).length > 0
        && answersIn(target).length === 0
        && (target.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) === 0) {
      unheld.push(`${where(node)}: a cast of an answer to ${checker.typeToString(target)}`);
    }
  };

  const visit = (node: ts.Node): void => {
    if (ts.isPropertyAccessExpression(node)) {
      read(checker.getTypeAtLocation(node.expression), node.name.text, node);
    } else if (ts.isElementAccessExpression(node)
               && ts.isStringLiteralLike(node.argumentExpression)) {
      read(checker.getTypeAtLocation(node.expression), node.argumentExpression.text, node);
    } else if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.InKeyword
               && ts.isStringLiteralLike(node.left)) {
      const type = checker.getTypeAtLocation(node.right);
      if ((type.flags & ts.TypeFlags.NonPrimitive) !== 0) {
        unheld.push(`${where(node)}: an \`in\` check of "${node.left.text}" on a bare object`);
      }
      read(type, node.left.text, node);
    } else if (ts.isObjectBindingPattern(node)) {
      const type = checker.getTypeAtLocation(node);
      for (const element of node.elements) {
        const name = element.propertyName ?? element.name;
        if (ts.isIdentifier(name) || ts.isStringLiteralLike(name)) {
          read(type, name.text, element);
        }
      }
    } else if (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) {
      cast(node, node.expression);
    } else if (ts.isParenthesizedExpression(node) && ts.getJSDocTypeTag(node) !== undefined) {
      cast(node, node.expression);
    }
    if (ts.isSpreadAssignment(node) || ts.isSpreadElement(node)) {
      handed(node.expression);
    } else if (ts.isExpression(node) && !ts.isPropertyAccessExpression(node.parent)
               && !ts.isElementAccessExpression(node.parent)) {
      handed(node);
    }
    ts.forEachChild(node, visit);
  };
  for (const path of files) {
    visit(sourceOf(program, path));
  }
  return { reads, undeclared, unheld };
}

function sourceOf(program: ts.Program, path: string): ts.SourceFile {
  const file = program.getSourceFile(join(PLATFORM, path));
  if (file === undefined) {
    throw new Error(`${path} is not among the readers' modules`);
  }
  return file;
}

/** The declaration named `name` among `path`'s statements (in `program`). */
function declared(program: ts.Program, path: string, name: string): ts.Node {
  const found = sourceOf(program, path).statements.find((statement) =>
    (ts.isInterfaceDeclaration(statement) || ts.isClassDeclaration(statement))
    && statement.name?.text === name);
  if (found === undefined) {
    throw new Error(`${path} declares no ${name}`);
  }
  return found;
}

/**
 * Each door the interface `types` (`<path>#<name>`) types an answer of that the class `owner`
 * (`<path>#<name>`) does not answer as typed: its method's answer is not that type, or it has
 * no such method.
 */
export function unanswered(program: ts.Program, types: string, owner: string): string[] {
  const checker = program.getTypeChecker();
  const [typesPath, typesName] = types.split("#") as [string, string];
  const [ownerPath, ownerName] = owner.split("#") as [string, string];
  const typeOf = (path: string, name: string): ts.Type => checker.getDeclaredTypeOfSymbol(
    checker.getSymbolAtLocation(
      (declared(program, path, name) as ts.InterfaceDeclaration | ts.ClassDeclaration).name!)!);
  const typed = typeOf(typesPath, typesName);
  const answering = typeOf(ownerPath, ownerName);
  return checker.getPropertiesOfType(typed).flatMap((entry) => {
    const method = checker.getPropertyOfType(answering, entry.name);
    const signature = method === undefined ? undefined : checker.getSignaturesOfType(
      checker.getTypeOfSymbol(method), ts.SignatureKind.Call)[0];
    if (signature === undefined) {
      return [`${entry.name}: ${ownerName} has no such door`];
    }
    const answer = checker.getAwaitedType(signature.getReturnType()) ?? signature.getReturnType();
    return checker.isTypeAssignableTo(answer, checker.getTypeOfSymbol(entry)) ? []
      : [`${entry.name}: ${ownerName} answers ${checker.typeToString(answer)}`];
  });
}
