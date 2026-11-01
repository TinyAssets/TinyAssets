/**
 * N10 and P0-3 policy-writers: owner policy, grants and sessions are written through
 * the object's doors only.
 *
 * Ported from `tests/test_policy_writers.py` (origin/main 1fd430857, and its P0-3 form on
 * `p0-3-outside-grants`). There, only the broker's policy store wrote policy and grants
 * and only the router's owner channel submitted a widening. Here, only the object
 * imports the session and grant modules (so only its doors, which check the owner's
 * session themselves, can call a widening), and each policy table is named only by the
 * module that keeps it and the schema. The held agent registrations and the owner's
 * admission are held to the same: only the object imports them, and the admission row is
 * its module's and the schema's alone. So are the model connections and their spend: only the
 * object builds them, the provider and `auto` read their types and calls, and their
 * tables (the connections, their blobs and flows, the reservations and the spend and turn
 * counters) are their modules' alone, and so are the agent's threads, live, usage and
 * receipt tables, and the owner's erasure mark. The lists only shrink.
 */
import { posix } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { workerSources } from "./sources";

const OWNER_DO = "owner/src/owner-do.ts";
const SCHEMA = "owner/src/schema.ts";
const MODULES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  policy: Object.freeze([OWNER_DO, "owner/src/grants.ts", SCHEMA]),
  grants: Object.freeze([OWNER_DO]),
  sessions: Object.freeze([OWNER_DO]),
  registrations: Object.freeze([OWNER_DO]),
  admission: Object.freeze([OWNER_DO]),
  connections: Object.freeze([OWNER_DO, "owner/src/auto.ts", "owner/src/gateway-provider.ts"]),
  usage: Object.freeze([OWNER_DO, "owner/src/gateway-provider.ts"]),
});
const TABLES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  owner_sessions: Object.freeze(["owner/src/sessions.ts", SCHEMA]),
  owner_policy: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  policy_receipts: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  policy_proposals: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  proposal_shares: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  outside_clients: Object.freeze(["owner/src/grants.ts", SCHEMA]),
  grant_fences: Object.freeze(["owner/src/grants.ts", SCHEMA]),
  policy_usage: Object.freeze(["owner/src/grants.ts", "owner/src/policy.ts",
                               "owner/src/registrations.ts", "owner/src/sessions.ts", SCHEMA]),
  // Its table is dropped (migration 13); only the migrations that made and dropped it name it.
  scheduled_work: Object.freeze([SCHEMA]),
  owner_admission: Object.freeze(["owner/src/admission.ts", SCHEMA]),
  agent_cards: Object.freeze(["owner/src/registrations.ts", SCHEMA]),
  agent_threads: Object.freeze(["owner/src/threads.ts", SCHEMA]),
  agent_live: Object.freeze(["owner/src/threads.ts", SCHEMA]),
  agent_usage: Object.freeze(["owner/src/threads.ts", SCHEMA]),
  agent_usage_space: Object.freeze(["owner/src/threads.ts", SCHEMA]),
  run_receipts: Object.freeze(["owner/src/threads.ts", SCHEMA]),
  owner_erasure: Object.freeze(["owner/src/erasure.ts", SCHEMA]),
  connection_secrets: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  connection_flows: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  model_reservations: Object.freeze(["owner/src/usage.ts", SCHEMA]),
  model_spend: Object.freeze(["owner/src/usage.ts", SCHEMA]),
  model_turns: Object.freeze(["owner/src/usage.ts", SCHEMA]),
  model_turn_owners: Object.freeze(["owner/src/usage.ts", SCHEMA]),
  egress_reservations: Object.freeze(["owner/src/egress.ts", SCHEMA]),
  egress_runs: Object.freeze(["owner/src/egress.ts", SCHEMA]),
});

/**
 * Tables whose name is a plain word the sources use, held by the SQL that names one: a
 * statement reading, writing or defining it.
 */
const SQL_TABLES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  connections: Object.freeze(["owner/src/connections.ts", SCHEMA]),
});

function sqlNaming(table: string): RegExp {
  return new RegExp(`\\b(?:FROM|INTO|UPDATE|TABLE|JOIN)\\s+${table}\\b`);
}

/**
 * Tables no source names at all: Lifecycle's (`cf_agents_*`, its job queue among them) and
 * pi's session store (`pi_*`). The SDK alone reads and writes them.
 */
const RESERVED: readonly RegExp[] = Object.freeze([/\bcf_agents_\w*/, /\bpi_[a-z]\w*/]);

function importer(module: string): RegExp {
  return new RegExp(`(?:\\bfrom\\s*|\\bimport\\s*\\(?\\s*)["'][^"']*/${module}(?:\\.[cm]?[jt]s)?["']`);
}

/** Who imports each policy module, and who names each policy table, but the module itself. */
export function reach(sources: Map<string, string>): Record<string, string[]> {
  const found: Record<string, string[]> = {};
  for (const [path, source] of [...sources].sort(([a], [b]) => (a < b ? -1 : 1))) {
    for (const module of Object.keys(MODULES)) {
      if (path !== `owner/src/${module}.ts` && importer(module).test(source)) {
        (found[module] ??= []).push(path);
      }
    }
    for (const table of Object.keys(TABLES)) {
      if (new RegExp(`\\b${table}\\b`).test(source)) {
        (found[table] ??= []).push(path);
      }
    }
    for (const table of Object.keys(SQL_TABLES)) {
      if (sqlNaming(table).test(source)) {
        (found[`sql:${table}`] ??= []).push(path);
      }
    }
    if (RESERVED.some((reserved) => reserved.test(source))) {
      (found["reserved"] ??= []).push(path);
    }
  }
  return found;
}

describe("policy-boundary guard", () => {
  it("lets only the object reach the session and grant modules, and each table its own",
     () => {
    const expected = Object.fromEntries([
      ...Object.entries(MODULES), ...Object.entries(TABLES),
      ...Object.entries(SQL_TABLES).map(([name, paths]) => [`sql:${name}`, paths] as const),
    ].map(([name, paths]) => [name, [...paths].sort()]));
    expect(reach(workerSources())).toEqual(expected);
  });

  it.each([
    ['import { Sessions } from "./sessions";', "sessions"],
    ["import type { Grant } from './grants.ts';", "grants"],
    ['const policy = await import("./policy");', "policy"],
    ['sql.exec("UPDATE owner_policy SET generation = 9");', "owner_policy"],
    ['sql.exec("DELETE FROM outside_clients");', "outside_clients"],
    ['sql.exec("DELETE FROM cf_agents_jobs");', "reserved"],
    ['sql.exec("UPDATE cf_agents_jobs SET time = 0");', "reserved"],
    ['sql.exec("SELECT * FROM pi_entries");', "reserved"],
    ['sql.exec("UPDATE connections SET status = \'error\'");', "sql:connections"],
    ['sql.exec("SELECT id FROM connections WHERE 1");', "sql:connections"],
    ['sql.exec("INSERT INTO connections (id) VALUES (?)", id);', "sql:connections"],
    ['"CREATE TABLE connections (id TEXT)"', "sql:connections"],
  ])("sees %s", (line, name) => {
    expect(reach(new Map([["owner/src/probe.ts", line]]))[name]).toEqual(["owner/src/probe.ts"]);
  });

  it("leaves the plain word alone", () => {
    expect(reach(new Map([["owner/src/probe.ts",
                           "const connections = new Connections(storage); // connections"]])))
      .toEqual({});
  });
});

/** Every module specifier `source` loads at runtime, read from its syntax tree. */
function runtimeSpecifiers(path: string, source: string): { specs: string[]; opaque: number } {
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const specs: string[] = [];
  let opaque = 0;
  const named = (node: ts.Expression | undefined) => {
    if (node !== undefined && ts.isStringLiteralLike(node)) {
      specs.push(node.text);
    } else {
      opaque++;  // a computed specifier: what it loads cannot be read
    }
  };
  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node)) {
      // Under `verbatimModuleSyntax` only `import type` is erased: `import { type T }` stays,
      // as a side-effect import of its module.
      if (node.importClause?.isTypeOnly !== true) {
        named(node.moduleSpecifier);
      }
    } else if (ts.isExportDeclaration(node)) {
      if (node.moduleSpecifier !== undefined && !node.isTypeOnly) {
        named(node.moduleSpecifier);
      }
    } else if (ts.isImportEqualsDeclaration(node)) {
      if (!node.isTypeOnly && ts.isExternalModuleReference(node.moduleReference)) {
        named(node.moduleReference.expression);
      }
    } else if (ts.isCallExpression(node)
               && (node.expression.kind === ts.SyntaxKind.ImportKeyword
                   || (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
      named(node.arguments[0]);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return { specs, opaque };
}

/** The source a relative specifier from `from` names, as the bundler resolves it, or null. */
function resolved(sources: ReadonlyMap<string, string>, from: string, spec: string):
    string | null {
  const base = posix.join(posix.dirname(from), spec);
  const stem = base.replace(/\.(?:js|mjs|cjs)$/, "");
  for (const candidate of [base, `${stem}.ts`, `${stem}.mts`, `${stem}/index.ts`]) {
    if (sources.has(candidate) && /\.m?ts$/.test(candidate)) {
      return candidate;
    }
  }
  return null;
}

/**
 * The modules a source runs, transitively, from `roots`, by the TypeScript compiler's own
 * reading of each one's imports: every import, export-from, `import()` and `require` that
 * is not erased as type-only, each relative one resolved as the bundler resolves it, and
 * each package reached. What cannot be resolved, or read (a computed specifier), is
 * `unresolved`, which the guard refuses.
 */
export function runtimeReach(sources: ReadonlyMap<string, string>, roots: readonly string[]):
    { modules: string[]; packages: string[]; unresolved: string[] } {
  const modules = new Set<string>();
  const packages = new Set<string>();
  const unresolved: string[] = [];
  const pending = [...roots];
  while (pending.length > 0) {
    const path = pending.pop()!;
    if (modules.has(path)) {
      continue;
    }
    modules.add(path);
    const source = sources.get(path);
    if (source === undefined) {
      unresolved.push(path);
      continue;
    }
    const { specs, opaque } = runtimeSpecifiers(path, source);
    if (opaque > 0) {
      unresolved.push(`${path}: a computed specifier`);
    }
    for (const spec of specs) {
      if (spec.startsWith(".")) {
        const target = resolved(sources, path, spec);
        if (target === null) {
          unresolved.push(`${path}: ${spec}`);
        } else {
          pending.push(target);
        }
      } else {
        packages.add(spec);
      }
    }
  }
  return { modules: [...modules].sort(), packages: [...packages].sort(),
           unresolved: unresolved.sort() };
}

/** What the sandbox's spend may not run: pi's engine, in our modules or its packages. */
function engine({ modules, packages }: { modules: string[]; packages: string[] }): string[] {
  return [...modules.filter((path) => /\/(usage|thread-host|thread-store|agent|turns)\.ts$/
    .test(path)),
          ...packages.filter((name) => name.startsWith("@earendil-works/")
                                       && !name.startsWith("@earendil-works/pi-ai"))];
}

describe("the sandbox's spend", () => {
  it("runs nothing of pi's engine: its rows and counters are its own and the schema's", () => {
    const reach = runtimeReach(workerSources(), ["owner/src/egress.ts", "owner/src/money.ts"]);
    expect(reach.modules).toContain("owner/src/money.ts");
    expect(reach.modules).toContain("doors/src/routing-mac.ts");
    expect(reach.unresolved).toEqual([]);
    expect(engine(reach)).toEqual([]);
  });

  it.each([
    ["a named import", 'import { x } from "./thread-host";'],
    ["a side-effect import", 'import "./thread-host";'],
    ["single quotes", "import { x } from './thread-host';"],
    ["a dynamic import", 'const m = await import("./thread-host");'],
    ["a .js specifier", 'import { x } from "./thread-host.js";'],
    ["export star", 'export * from "./thread-host";'],
    ["a require", 'const m = require("./thread-host");'],
    ["a package, side effect", 'import "@earendil-works/pi-durable";'],
    ["a package, by a mixed import", 'import { type T, x } from "@earendil-works/pi-durable";'],
    ["a package, by inline types alone (kept by verbatimModuleSyntax)",
     'import { type T } from "@earendil-works/pi-durable";'],
  ])("sees %s of pi's engine", (_what, line) => {
    const sources = new Map([["owner/src/egress.ts", line],
                             ["owner/src/thread-host.ts", "export const x = 1;"]]);
    expect(engine(runtimeReach(sources, ["owner/src/egress.ts"]))).not.toEqual([]);
  });

  it("skips an `import type`, and refuses an import it cannot resolve or read", () => {
    const sources = new Map([
      ["owner/src/a.ts", 'import type { T } from "./thread-host";\n' +
                         'import type { U } from "@earendil-works/pi-durable";\n' +
                         'export type { V } from "./thread-host";\n' +
                         'import { b } from "./missing";\nawait import(name);'],
      ["owner/src/thread-host.ts", "export const x = 1;"]]);
    const reach = runtimeReach(sources, ["owner/src/a.ts"]);
    expect(engine(reach)).toEqual([]);
    expect(reach.unresolved).toEqual(["owner/src/a.ts: ./missing",
                                      "owner/src/a.ts: a computed specifier"]);
  });
});
