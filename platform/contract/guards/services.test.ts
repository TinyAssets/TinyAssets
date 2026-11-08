/**
 * The services' RPC contracts (`decisions.md` A1): the gateway's, the files Worker's, the
 * hooks Worker's, the sandbox Worker's and the owner object's doors as the doors call them
 * (`contract/src/{gateway,files,hooks,sandbox,owner}.ts`).
 *
 * - Each service implements its contract: every entrypoint class names its interface
 *   (`implements`), so `tsc` holds the two to each other.
 * - No Worker keeps a copy: no Worker source declares a name a service contract exports, so
 *   a type that crosses a service is spelled once, in the contract.
 * - The owner's doors are the door table's (`doors.json`): each one the contract names is a
 *   row, taking the binding first (but the two unbound doors) and a request where its row
 *   takes one.
 */
import { describe, expect, it } from "vitest";
import ts from "typescript";

import { parse, workerSources } from "../../ci/kit/sources";
import { doorRow, takesRequest, UNBOUND_DOORS } from "../src/doors";

/** The service contracts, by their module. */
const CONTRACTS = Object.freeze(["gateway", "files", "hooks", "sandbox", "owner"]
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

/** Each Worker source's declaration of a name a service contract exports, as `path: name`. */
export function copies(sources: ReadonlyMap<string, string>, names: ReadonlySet<string>):
    string[] {
  return [...sources].filter(([path]) => !path.startsWith("contract/") && !path.startsWith("sources/"))
    .flatMap(([path, source]) => declared(path, source)
      .filter((name) => names.has(name)).map((name) => `${path}: ${name}`));
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

/** The owner's doors as the contract names them: each method and its parameters' count. */
export function ownerDoors(source: string): [string, number][] {
  const found: [string, number][] = [];
  for (const statement of parse("contract/src/owner.ts", source).statements) {
    if (ts.isInterfaceDeclaration(statement) && statement.name.text === "OwnerDoors") {
      for (const member of statement.members) {
        if (ts.isMethodSignature(member) && ts.isIdentifier(member.name)) {
          found.push([member.name.text, member.parameters.length]);
        }
      }
    }
  }
  return found;
}

/** Why a door the contract names breaks its row, or null: a row, the binding, a request. */
export function doorProblem(door: string, parameters: number): string | null {
  let row;
  try {
    row = doorRow(door);
  } catch {
    return `${door}: no row of the door table`;
  }
  const expected = (UNBOUND_DOORS.includes(door) ? 0 : 1) + (takesRequest(row) ? 1 : 0);
  return parameters === expected ? null
    : `${door}: takes ${parameters} arguments, its row ${expected}`;
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
      "Started", "SandboxSignin", "MeterEvent", "METER_BATCH", "OwnerDoors", "BilledEvent"]));
    expect(copies(sources, names)).toEqual([]);
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
    expect(doors.map(([door, parameters]) => doorProblem(door, parameters))
      .filter((problem) => problem !== null)).toEqual([]);
    expect(doorProblem("fileProbe", 2)).toBe("fileProbe: no row of the door table");
    expect(doorProblem("sessionOpen", 2)).toBe("sessionOpen: takes 2 arguments, its row 1");
    expect(doorProblem("sandboxRung", 1)).toBe("sandboxRung: takes 1 arguments, its row 0");
  });
});
