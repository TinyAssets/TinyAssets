/**
 * The services' RPC contracts (`decisions.md` A1): the gateway's, the files Worker's, the
 * hooks Worker's, the sandbox Worker's and the owner object's doors as the doors call them
 * (`contract/src/{gateway,files,hooks,sandbox,owner}.ts`).
 *
 * - Each service implements its contract: every entrypoint class names its interface
 *   (`implements`), so `tsc` holds the two to each other.
 * - No Worker keeps a copy: no Worker source declares a name a service contract exports, nor
 *   an object type of exactly the members one does under another name, so a type that
 *   crosses a service is spelled once, in the contract.
 * - The owner's doors are the door table's (`doors.json`): each one the contract names is a
 *   row, taking the binding first (but the two unbound doors) and a request where its row
 *   takes one, by the row's own fields where it lists them (`Asked` or `Handed` of the door,
 *   from `requests.ts`, which `node contract/requests.ts` writes from the table and this
 *   holds current).
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";
import ts from "typescript";

import { parse, workerSources } from "../../ci/kit/sources";
import { REQUESTS_FILE, requestsSource } from "../requests";
import { doorRow, doorTable, takesRequest, UNBOUND_DOORS } from "../src/doors";

/** The service contracts, by their module. */
const CONTRACTS = Object.freeze(["gateway", "files", "hooks", "sandbox", "owner", "requests"]
  .map((name) => `contract/src/${name}.ts`));

/** Each service's entrypoint classes, by source, and the contract interface each implements. */
const IMPLEMENTS: Readonly<Record<string, Readonly<Record<string, string>>>> = Object.freeze({
  "gateway/src/index.ts": { ConnectGateway: "ConnectService", EgressGateway: "EgressService",
                            Gateway: "GatewayService" },
  "files/src/index.ts": { FilesService: "ChunkStore", FilesUpload: "ChunkUpload" },
  "hooks/src/index.ts": { Hooks: "HooksService" },
  "sandbox/src/index.ts": { Sandbox: "SandboxService" },
  "owner/src/owner-do.ts": { OwnerDO: "OwnerDoors" },
});

function exported(node: ts.Node): boolean {
  return ts.canHaveModifiers(node)
    && (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
}

/** The names `source`'s top level declares: its types, interfaces, classes, functions and consts. */
export function declared(path: string, source: string, onlyExported = false): string[] {
  const names: string[] = [];
  for (const statement of parse(path, source).statements) {
    if (onlyExported && !exported(statement)) {
      continue;
    }
    if ((ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)
         || ts.isClassDeclaration(statement) || ts.isFunctionDeclaration(statement)
         || ts.isEnumDeclaration(statement)) && statement.name !== undefined) {
      names.push(statement.name.text);
    } else if (ts.isVariableStatement(statement)) {
      for (const each of statement.declarationList.declarations) {
        if (ts.isIdentifier(each.name)) {
          names.push(each.name.text);
        }
      }
    }
  }
  return names;
}

/**
 * The fewest members a shape is compared at: two ({sha256, size}, a chunk's and a manifest
 * object's alike) share a shape by coincidence.
 */
const SHAPE_MEMBERS = 3;

/**
 * Each object type `source`'s top level declares (an interface that extends nothing, or a
 * type of one object literal) of at least `SHAPE_MEMBERS` members, by name, as its shape:
 * its members' text, docs and spacing dropped, sorted.
 */
export function shapes(path: string, source: string, onlyExported = false): Map<string, string> {
  const file = parse(path, source);
  const found = new Map<string, string>();
  for (const statement of file.statements) {
    const members = ts.isInterfaceDeclaration(statement) && statement.heritageClauses === undefined
      ? statement.members
      : ts.isTypeAliasDeclaration(statement) && ts.isTypeLiteralNode(statement.type)
        ? statement.type.members : undefined;
    if (members !== undefined && members.length >= SHAPE_MEMBERS
        && (!onlyExported || exported(statement))) {
      found.set((statement as ts.InterfaceDeclaration | ts.TypeAliasDeclaration).name.text,
                members.map((member) => member.getText(file).replace(/\/\*[\s\S]*?\*\//g, "")
                  .replace(/\s+/g, " ").replace(/[;,]$/, "").trim()).sort().join("; "));
    }
  }
  return found;
}

/**
 * Each Worker source's declaration of a name a service contract exports, as `path: name`, and
 * of an object type of exactly a contract type's shape (`shapes`) under another name, as
 * `path: name (the contract's Name)`.
 */
export function copies(sources: ReadonlyMap<string, string>, names: ReadonlySet<string>,
                       shaped: ReadonlyMap<string, string> = new Map()): string[] {
  return [...sources].filter(([path]) => !path.startsWith("contract/") && !path.startsWith("sources/"))
    .flatMap(([path, source]) => [
      ...declared(path, source).filter((name) => names.has(name))
        .map((name) => `${path}: ${name}`),
      ...[...shapes(path, source)].filter(([name, shape]) => shaped.has(shape) && !names.has(name))
        .map(([name, shape]) => `${path}: ${name} (the contract's ${shaped.get(shape)})`)]);
}

/** The contracts' object types, by shape, each its name. */
export function contractShapes(sources: ReadonlyMap<string, string>): Map<string, string> {
  return new Map(CONTRACTS.flatMap((path) => [...shapes(path, sources.get(path)!, true)]
    .map(([name, shape]) => [shape, name] as const)));
}

/** Each class `source` declares, and the interfaces it says it implements. */
export function implemented(path: string, source: string): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const statement of parse(path, source).statements) {
    if (ts.isClassDeclaration(statement)) {
      const name = statement.name?.text ?? "default";
      found.set(name, (statement.heritageClauses ?? [])
        .filter((clause) => clause.token === ts.SyntaxKind.ImplementsKeyword)
        .flatMap((clause) => clause.types.map((type) => type.expression.getText())));
    }
  }
  return found;
}

/**
 * The owner's doors as the contract names them: each method, its parameters' count, and its
 * request's type as written (the last parameter's), or null when it takes none.
 */
export function ownerDoors(source: string): [string, number, string | null][] {
  const found: [string, number, string | null][] = [];
  const file = parse("contract/src/owner.ts", source);
  for (const statement of file.statements) {
    if (ts.isInterfaceDeclaration(statement) && statement.name.text === "OwnerDoors") {
      for (const member of statement.members) {
        if (ts.isMethodSignature(member) && ts.isIdentifier(member.name)) {
          found.push([member.name.text, member.parameters.length,
                      member.parameters.at(-1)?.type?.getText(file) ?? null]);
        }
      }
    }
  }
  return found;
}

/**
 * Why a door the contract names breaks its row, or null: a row, the binding, a request, and
 * a row's field list as the request's type (`Asked` or `Handed` of the door itself).
 */
export function doorProblem(door: string, parameters: number, request: string | null = null):
    string | null {
  let row;
  try {
    row = doorRow(door);
  } catch {
    return `${door}: no row of the door table`;
  }
  const expected = (UNBOUND_DOORS.includes(door) ? 0 : 1) + (takesRequest(row) ? 1 : 0);
  if (parameters !== expected) {
    return `${door}: takes ${parameters} arguments, its row ${expected}`;
  }
  const listed = typeof row.args === "object";
  return !listed || request === null || [`Asked<"${door}">`, `Handed<"${door}">`].includes(request)
    ? null : `${door}: takes ${request}, not its row's fields (Asked<"${door}"> or Handed)`;
}

describe("the services' contracts", () => {
  const sources = workerSources();

  it("are each implemented by its service's entrypoints", () => {
    for (const [path, classes] of Object.entries(IMPLEMENTS)) {
      const found = implemented(path, sources.get(path)!);
      for (const [name, contract] of Object.entries(classes)) {
        expect(found.get(name), `${path} ${name}`).toContain(contract);
      }
    }
  });

  it("are each spelled once: no Worker source declares a name one exports", () => {
    const names = new Set(CONTRACTS.flatMap((path) => declared(path, sources.get(path)!, true)));
    expect([...names]).toEqual(expect.arrayContaining(["Verified", "Reservation", "Sealed",
      "Resealed", "Exchanged", "ChunkPut", "RingAnswer", "MeterOutcome", "SandboxStart",
      "Started", "SandboxSignin", "MeterEvent", "METER_BATCH", "OwnerDoors", "BilledEvent",
      "DoorRequests", "ConnectionCommitRequest", "EgressRequest"]));
    expect(copies(sources, names, contractShapes(sources))).toEqual([]);
  });

  it("sees a Worker's copy of a contract's type under another name, by its shape", () => {
    const sources = workerSources();
    const shaped = contractShapes(sources);
    const verified = "export interface VerifiedCopy {\n  /** Its models. */\n" +
      "  readonly models: readonly ModelRecord[];\n  readonly resealed?: Resealed;\n" +
      "  readonly egress: EgressScope;\n}";
    expect(copies(new Map([["gateway/src/forward.ts", verified]]), new Set(), shaped))
      .toEqual(["gateway/src/forward.ts: VerifiedCopy (the contract's Verified)"]);
    expect(copies(new Map([["doors/src/probe.ts",
      "type Setting = { readonly agent?: string; readonly binding_id: string;\n" +
      "  readonly enabled?: boolean, readonly expected_revision: number };"]]), new Set(), shaped))
      .toEqual(["doors/src/probe.ts: Setting (the contract's InboundSetRequest)"]);
    // Below three members a shape is a coincidence, and a narrower type is no copy.
    expect(copies(new Map([["doors/src/probe.ts",
      "type Revoking = { readonly connection: string };"]]), new Set(), shaped)).toEqual([]);
    expect(copies(new Map([["gateway/src/forward.ts",
      "export interface Narrower { readonly models: readonly ModelRecord[] }"]]), new Set(),
                  shaped)).toEqual([]);
  });

  it("sees a Worker's own copy of a contract's type", () => {
    const names = new Set(["Verified"]);
    expect(copies(new Map([["gateway/src/forward.ts",
      "export interface Verified { readonly models: readonly string[] }"]]), names))
      .toEqual(["gateway/src/forward.ts: Verified"]);
    expect(copies(new Map([["doors/src/probe.ts", "type Verified = { ok: true };"]]), names))
      .toEqual(["doors/src/probe.ts: Verified"]);
    expect(copies(new Map([["gateway/src/forward.ts",
      'import type { Verified } from "../../contract/src/gateway";']]), names)).toEqual([]);
  });

  it("name for the doors only the owner's doors, each as its row takes it", () => {
    const doors = ownerDoors(sources.get("contract/src/owner.ts")!);
    expect(doors.length).toBeGreaterThan(50);
    expect(doors.map(([door, parameters, request]) => doorProblem(door, parameters, request))
      .filter((problem) => problem !== null)).toEqual([]);
    expect(doors.filter(([, , request]) => /^(?:Asked|Handed)</.test(request ?? "")).length)
      .toBe(46);
    expect(doorProblem("grantRevoke", 2, "object"))
      .toBe('grantRevoke: takes object, not its row\'s fields (Asked<"grantRevoke"> or Handed)');
    expect(doorProblem("grantRevoke", 2, 'Asked<"grantForget">')).not.toBeNull();
    expect(doorProblem("fileProbe", 2)).toBe("fileProbe: no row of the door table");
    expect(doorProblem("sessionOpen", 2)).toBe("sessionOpen: takes 2 arguments, its row 1");
    expect(doorProblem("sandboxRung", 1)).toBe("sandboxRung: takes 1 arguments, its row 0");
  });

  it("take each request the table lists the fields of as requests.ts writes it from the table",
     () => {
    expect(readFileSync(REQUESTS_FILE, "utf8"), "run node contract/requests.ts")
      .toBe(requestsSource(doorTable()));
    expect(requestsSource(doorTable())).toContain(
      '  readonly grantRevoke: DoorFields<"client">;\n');
  });
});
