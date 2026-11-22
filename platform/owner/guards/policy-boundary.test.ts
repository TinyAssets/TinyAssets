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
 * its module's and the schema's alone; so are the action rules' held calls and receipts,
 * whose module the object and the policy's receipts door (a type) alone import. So are the model connections and their spend: only the
 * object builds them and alone imports their module (the shape the runs rank, `Usable`, is
 * `auto`'s), and their tables (the connections, their
 * blobs and flows, the reservations and the spend counters) are their modules' alone, and
 * so are the sandbox's egress rows and the owner's erasure mark, and the runs' rows (slice R:
 * runs, their grants, conversations and schedules, the tier numbers kept, the socket
 * tickets), each its module's and the schema's. The lists only shrink.
 *
 * One module names every table to read it, never to write it: the owner export
 * (`export.ts`, slice P), which reads everything the owner holds for the owner alone. Its
 * source is held to SQL that only reads (`READ_ONLY`): no statement but `SELECT`, and no
 * policy module imported. One names every table to load it: the restore (`restore.ts`, lane
 * R), which loads a generation into a pending, empty owner for the platform's reserved caller
 * alone. It names them as data, in its load table, and its SQL names no guarded table: a
 * table's own statements take the table from the load table (`${load.table}`), and every
 * other change (its fence, its allocators) is the owning module's (`LOADER`).
 *
 * The widenings themselves are a type fact (`door.ts`): each takes as its first argument the
 * proof its caller made, a row's call carries only its own caller's proof, and the cast rule
 * below (`forged`) keeps each proof to its maker.
 */
import { posix } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { ownerRows } from "../src/door-steps";
import { parse, reporter, workerSources } from "../../ci/kit/sources";

const OWNER_DO = "owner/src/owner-do.ts";
/** The owner export: it names tables only to read them (held below). */
const READ_ONLY = "owner/src/export.ts";
/** The restore: it names tables only in its load table, to load them (held below). */
const LOADER = "owner/src/restore.ts";
/** SQL that writes or changes a table, as a statement's first word. */
const WRITES = /\b(?:INSERT|UPDATE|DELETE|REPLACE|UPSERT|CREATE|DROP|ALTER|PRAGMA|ATTACH|VACUUM)\b/;
const SCHEMA = "owner/src/schema.ts";
const MODULES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  policy: Object.freeze([OWNER_DO, "owner/src/grants.ts"]),
  actions: Object.freeze([OWNER_DO, "owner/src/policy.ts"]),
  grants: Object.freeze([OWNER_DO]),
  sessions: Object.freeze([OWNER_DO]),
  registrations: Object.freeze([OWNER_DO]),
  admission: Object.freeze([OWNER_DO]),
  connections: Object.freeze([OWNER_DO]),
});
const TABLES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  owner_sessions: Object.freeze(["owner/src/sessions.ts", SCHEMA]),
  owner_policy: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  policy_receipts: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  policy_proposals: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  proposal_shares: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  outside_clients: Object.freeze(["owner/src/grants.ts", SCHEMA]),
  grant_fences: Object.freeze(["owner/src/grants.ts", SCHEMA]),
  policy_usage: Object.freeze(["owner/src/actions.ts", "owner/src/grants.ts",
                               "owner/src/policy.ts", "owner/src/registrations.ts",
                               "owner/src/sessions.ts", SCHEMA]),
  action_requests: Object.freeze(["owner/src/actions.ts", SCHEMA]),
  action_receipts: Object.freeze(["owner/src/actions.ts", SCHEMA]),
  owner_admission: Object.freeze(["owner/src/admission.ts", SCHEMA]),
  owner_keys: Object.freeze(["owner/src/keys.ts", SCHEMA]),
  inbound_bindings: Object.freeze(["owner/src/inbound.ts", SCHEMA]),
  agent_cards: Object.freeze(["owner/src/registrations.ts", SCHEMA]),
  owner_erasure: Object.freeze(["owner/src/erasure.ts", SCHEMA]),
  connection_secrets: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  connection_flows: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  model_spend: Object.freeze(["owner/src/egress.ts", SCHEMA]),
  egress_reservations: Object.freeze(["owner/src/egress.ts", SCHEMA]),
  run_grants: Object.freeze(["owner/src/runs.ts", SCHEMA]),
  run_schedules: Object.freeze(["owner/src/runs.ts", SCHEMA]),
  platform_tiers: Object.freeze(["owner/src/admission.ts", SCHEMA]),
  platform_signins: Object.freeze(["owner/src/signins.ts", SCHEMA]),
  socket_tickets: Object.freeze(["owner/src/tickets.ts", SCHEMA]),
  run_inputs: Object.freeze(["owner/src/runs.ts", SCHEMA]),
  sandbox_leases: Object.freeze(["owner/src/files.ts", SCHEMA]),
  engine_signins: Object.freeze(["owner/src/runs.ts", SCHEMA]),
});

/**
 * Each proof's maker (`door.ts`): the runner, once its row's caller held (the live session
 * of the row `Sessions.live` found), minting each (`proof-mint.ts`, which only the runner and
 * the proofs' check may import: the layer table's `SOLE_IMPORTERS`). A proof it did not mint
 * is refused at run time (`proven`), whatever it holds; and as `tsc` takes any cast, a type
 * assertion naming a proof's type, or the name of a proof's kind (`proved`) or of its mint
 * in any spelling (a key, a shorthand, a computed key, a string, so a JSON text too), is
 * refused anywhere else.
 */
const MAKERS: Readonly<Record<string, string>> = Object.freeze({
  LiveSession: OWNER_DO, OwnerSelf: OWNER_DO, PlatformLine: OWNER_DO, ImportLine: OWNER_DO,
});
/**
 * Where the proofs and their kind are declared and checked (`door.ts`), and where they are
 * minted (`proof-mint.ts`).
 */
const HOMES: ReadonlySet<string> = new Set(["owner/src/door.ts", "owner/src/proof-mint.ts"]);
/** The mint's name, the runner's and its own module's alone: `door.ts` checks, never mints. */
const MINTERS: ReadonlySet<string> = new Set([OWNER_DO, "owner/src/proof-mint.ts"]);

/** Each proof `sources` make outside its maker, as `file:line: what`. */
export function forged(sources: ReadonlyMap<string, string>): string[] {
  const makers = new Set(Object.values(MAKERS));
  return [...sources].flatMap(([path, source]) => {
    const file = parse(path, source);
    const problems: string[] = [];
    const report = reporter(file, problems);
    const visit = (node: ts.Node): void => {
      if (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) {
        for (const [proof, maker] of Object.entries(MAKERS)) {
          if (path !== maker && new RegExp(`\\b${proof}\\b`).test(node.type.getText(file))) {
            report(node, `casts to ${proof}, which only ${maker} makes`);
          }
        }
      }
      const word = ts.isIdentifier(node) ? node.text
        : ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node)
          || ts.isTemplateTail(node) ? node.text : null;
      if (word !== null && ((/\bproved\b/.test(word) && !makers.has(path) && !HOMES.has(path))
          || (/\bmintProof\b/.test(word) && !MINTERS.has(path)))) {
        report(node, "names a proof's kind, which only its maker does");
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    return problems;
  });
}

/**
 * Tables whose name is a plain word the sources use, held by the SQL that names one: a
 * statement reading, writing or defining it.
 */
const SQL_TABLES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  connections: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  runs: Object.freeze(["owner/src/runs.ts", SCHEMA]),
  conversations: Object.freeze(["owner/src/runs.ts", SCHEMA]),
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
      if (path !== READ_ONLY && path !== LOADER
          && new RegExp(`\\b${table}\\b`).test(source)) {
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
  it("holds the owner export to reading: no SQL that writes, no policy module", () => {
    const source = workerSources().get(READ_ONLY)!;
    const literals = [...source.matchAll(/"[^"\n]*"|`[^`]*`/g)].map((match) => match[0]);
    expect(literals.filter((literal) => WRITES.test(literal))).toEqual([]);
    expect(Object.keys(MODULES).filter((module) => importer(module).test(source))).toEqual([]);
    expect(WRITES.test('"UPDATE owner_policy SET x = 1"')).toBe(true);
  });

  it("holds the restore to loading: no SQL naming a guarded table, no policy module, its " +
     "doors the platform's alone", () => {
    const source = workerSources().get(LOADER)!;
    const literals = [...source.matchAll(/"[^"\n]*"|`[^`]*`/g)].map((match) => match[0]);
    const guarded = [...Object.keys(TABLES), ...Object.keys(SQL_TABLES)];
    expect(guarded.filter((table) => literals.some((literal) => sqlNaming(table).test(literal))))
      .toEqual([]);
    expect(Object.keys(MODULES).filter((module) => importer(module).test(source))).toEqual([]);
    expect(new Set(ownerRows().filter((row) => row.handler === "restore")
      .map((row) => row.caller))).toEqual(new Set(["reserved"]));
    expect(sqlNaming("owner_keys").test('"SELECT key_id FROM owner_keys"')).toBe(true);
  });

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

  it("makes each proof only in its maker", () => {
    expect(forged(workerSources())).toEqual([]);
  });

  it.each([
    ["a live session cast outside the runner",
     'const forged = { proved: "session", hash: "h" } as LiveSession;', "owner/src/sessions.ts",
     ["casts to LiveSession, which only owner/src/owner-do.ts makes",
      "names a proof's kind, which only its maker does"]],
    ["the platform's line cast by the sessions",
     "const line = {} as unknown as PlatformLine;", "owner/src/sessions.ts",
     ["casts to PlatformLine, which only owner/src/owner-do.ts makes"]],
    ["the owner itself in an angle-bracket assertion", "const self = <OwnerSelf>{};",
     "owner/src/keys.ts", ["casts to OwnerSelf, which only owner/src/owner-do.ts makes"]],
    ["a proof's kind handed on as anything", 'grants.set({ "proved": "session" } as never);',
     "owner/src/ledger.ts", ["names a proof's kind, which only its maker does"]],
    ["a proof's kind by a shorthand", 'grants.set({ proved, hash } as never);',
     "owner/src/ledger.ts", ["names a proof's kind, which only its maker does"]],
    ["a proof's kind by a computed key", 'grants.set({ ["proved"]: "session" } as never);',
     "owner/src/ledger.ts", ["names a proof's kind, which only its maker does"]],
    ["a proof's kind in a JSON text", 'grants.set(JSON.parse(\'{"proved":"session"}\'));',
     "owner/src/ledger.ts", ["names a proof's kind, which only its maker does"]],
    ["the proofs' mint outside the runner", "grants.set(mintProof({}) as never);",
     "owner/src/ledger.ts", ["names a proof's kind, which only its maker does"]],
    ["the proofs' mint handed on by their check", 'export { mintProof as m } from "./proof-mint";',
     "owner/src/door.ts", ["names a proof's kind, which only its maker does"]],
  ])("sees %s", (_what, source, path, problems) => {
    expect(forged(new Map([[path, source]])))
      .toEqual(problems.map((problem) => `${path}:1: ${problem}`));
  });

  it("lets each maker make its own proof", () => {
    expect(forged(new Map([
      ["owner/src/owner-do.ts", 'const live = mintProof({ proved: "session" }) as LiveSession;'],
      ["owner/src/proof-mint.ts", "export function mintProof() {}"],
    ]))).toEqual([]);
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
      if (spec.startsWith(".") && spec.endsWith(".json")) {
        continue;  // a data file: it runs nothing
      } else if (spec.startsWith(".")) {
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
  return [...modules.filter((path) => /\/(thread-host|thread-store|agent|turns)\.ts$/
    .test(path)),
          ...packages.filter((name) => name.startsWith("@earendil-works/")
                                       && !name.startsWith("@earendil-works/pi-ai"))];
}

describe("the sandbox's spend", () => {
  it("runs nothing of pi's engine: its rows and counters are its own and the schema's", () => {
    const reach = runtimeReach(workerSources(), ["owner/src/egress.ts", "owner/src/money.ts"]);
    expect(reach.modules).toContain("owner/src/money.ts");
    expect(reach.modules).toContain("contract/src/egress-scope.ts");
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
    ["a package, side effect", 'import "@earendil-works/pi-coding-agent";'],
    ["a package, by a mixed import", 'import { type T, x } from "@earendil-works/pi-coding-agent";'],
    ["a package, by inline types alone (kept by verbatimModuleSyntax)",
     'import { type T } from "@earendil-works/pi-coding-agent";'],
  ])("sees %s of pi's engine", (_what, line) => {
    const sources = new Map([["owner/src/egress.ts", line],
                             ["owner/src/thread-host.ts", "export const x = 1;"]]);
    expect(engine(runtimeReach(sources, ["owner/src/egress.ts"]))).not.toEqual([]);
  });

  it("skips an `import type`, and refuses an import it cannot resolve or read", () => {
    const sources = new Map([
      ["owner/src/a.ts", 'import type { T } from "./thread-host";\n' +
                         'import type { U } from "@earendil-works/pi-coding-agent";\n' +
                         'export type { V } from "./thread-host";\n' +
                         'import { b } from "./missing";\nawait import(name);'],
      ["owner/src/thread-host.ts", "export const x = 1;"]]);
    const reach = runtimeReach(sources, ["owner/src/a.ts"]);
    expect(engine(reach)).toEqual([]);
    expect(reach.unresolved).toEqual(["owner/src/a.ts: ./missing",
                                      "owner/src/a.ts: a computed specifier"]);
  });
});
