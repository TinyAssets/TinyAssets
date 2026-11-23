/**
 * The services' RPC contracts (`docs/decisions/untangle-2026-11-wave-1.md` A1): the gateway's,
 * the files Worker's, the codemode Worker's, the sandbox Worker's and the owner object's doors as
 * the doors call them (`contract/src/{gateway,files,codemode,sandbox,owner}.ts`).
 *
 * - Each service implements its contract: every class `contract/services.json` lists names
 *   its interface (`implements`), so `tsc` holds the two to each other, and the class a
 *   binding with no entrypoint reaches is its Worker's default export. A Worker binding
 *   another is typed by the same table (`ci/kit/types.ts`).
 * - No Worker keeps a copy: no Worker source declares a name a service contract exports, nor
 *   an object type of exactly the members one does under another name, so a type that
 *   crosses a service is spelled once, in the contract.
 * The owner's doors are the door table's by construction: `npm run generate` writes them
 * (`contract/src/owner-doors.ts`), each door's name, binding and request its row's, and `tsc`
 * refuses a door `DoorTypes` types that is no row (`contract/src/owner.ts`).
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import ts from "typescript";

import { parse, workerSources } from "../../ci/kit/sources";
import { services } from "../../ci/kit/types";
import { PLATFORM } from "../../ci/paths";
import { readWrangler } from "../../ci/wrangler";

/** The service contracts, by their module. */
const CONTRACTS = Object.freeze(["gateway", "files", "codemode", "sandbox", "owner", "owner-doors",
                                 "requests", "queues"].map((name) => `contract/src/${name}.ts`));

const table = services();

/** Each service's classes, by source, and the contract interface each implements. */
const IMPLEMENTS = Object.entries(table.workers).flatMap(([worker, entry]) =>
  Object.entries(entry.classes)
    .map(([name, { source, contract }]) => ({ path: `${worker}/${source}`, name,
                                               contract: contract.split("#")[1]! })));

/** Each Worker's default entrypoint, as the table names it, by the Worker's main module. */
const DEFAULTS = Object.entries(table.workers).flatMap(([worker, entry]) =>
  entry.default === undefined ? []
    : [{ path: `${worker}/${String(readWrangler(join(PLATFORM, worker))["main"])}`,
         name: entry.default }]);

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

describe("the services' contracts", () => {
  const sources = workerSources();

  it("are each implemented by its service's entrypoints", () => {
    expect(IMPLEMENTS.length).toBeGreaterThan(0);
    for (const { path, name, contract } of IMPLEMENTS) {
      expect(implemented(path, sources.get(path)!).get(name), `${path} ${name}`)
        .toContain(contract);
    }
  });

  it("name each Worker's default export as the class an unnamed entrypoint reaches", () => {
    expect(DEFAULTS.length).toBeGreaterThan(0);
    for (const { path, name } of DEFAULTS) {
      expect(sources.get(path), path).toMatch(
        new RegExp(`^export default (?:class ${name}\\b|${name};)`, "m"));
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
      "  readonly egress: EgressScope | OneApiScope;\n}";
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

  it("let only the doors' body parser make a JsonBody: no source spells its mark or casts to it",
     () => {
    const MARK = ["parsed by", "jsonObject"].join(" ");
    expect([...sources].filter(([path, source]) =>
      (path !== "contract/src/owner.ts" && source.includes(MARK))
      || (path !== "doors/src/kit.ts" && /\bas\s+JsonBody\b|<\s*JsonBody\s*>/.test(source)))
      .map(([path]) => path)).toEqual([]);
  });
});
