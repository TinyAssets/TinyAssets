/**
 * Generations under test (slice P; `backup.ts`, `changelog.ts`): an owner with a session,
 * the stream replayed last line per key, the owner's rows as they stand, a digest of either
 * to compare, and a harness that runs a generation in the owner's own storage with a writer
 * at every outside call (and, if asked, after every unit), failing one outside call if asked,
 * each pass a fresh lane as a restarted object makes it.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { Backups } from "../src/backup";
import { ChangeLog } from "../src/changelog";
import { exported } from "../src/export";
import type { ChunkStore, Files } from "../src/files";
import { WriteGate } from "../src/gate";
import type { Binding, OwnerDO } from "../src/owner-do";
import { MIGRATIONS } from "../src/schema";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { drainDue, filesOn, TIERS } from "./support";
import type { FauxOwnerDO } from "./worker";

export type Line = Record<string, unknown> & { t: string };
/** Rows as a reader keeps them: each table's rows by key, the last line for a key winning. */
export type Rows = Record<string, Record<string, string>>;

/** An owner of its own on the class whose erasure is in reach, signed in, with a session. */
export async function owner() {
  const name = `owner-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<FauxOwnerDO> = env.AGENT.getByName(name);
  const at = signedIn();
  const bind = (origin: typeof MUSE | null = null, extra: Partial<Binding> = {}): Binding =>
    ({ owner: name, caller: name, origin, tiers: TIERS,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const { session } = await stub.sessionOpen(bind(null, { authenticated_at: at }));
  return { name, stub, bind, session: () => bind(null, { session }) };
}
export type Owner = Awaited<ReturnType<typeof owner>>;

/** A stream's lines replayed, last line per (table, key) winning, `gone` taking one away. */
export function replay(lines: Line[]): Rows {
  const tables = exported();
  const out: Rows = Object.fromEntries(Object.keys(tables).map((table) => [table, {}]));
  for (const line of lines) {
    const table = line["table"] as string;
    if (line.t === "row") {
      const row = line["row"] as Record<string, unknown>;
      out[table]![JSON.stringify(tables[table]!.key.map((column) => row[column]))] =
        JSON.stringify(row);
    } else if (line.t === "gone") {
      delete out[table]![JSON.stringify(line["key"])];
    }
  }
  return out;
}

/** The owner's exported rows as they stand, keyed as `replay` keys them. */
export function standing(sql: Pick<SqlStorage, "exec">): Rows {
  const tables = exported();
  return Object.fromEntries(Object.entries(tables).map(([table, { columns, key }]) => [
    table, Object.fromEntries(sql.exec<Record<string, SqlStorageValue>>(
      `SELECT ${[...new Set([...columns, ...key])].join(", ")} FROM ${table}`).toArray()
      .map((row) => [JSON.stringify(key.map((column) => row[column])), JSON.stringify(
        Object.fromEntries(columns.map((column) => {
          const value = row[column];
          return [column, value instanceof ArrayBuffer
            ? { $base64: Buffer.from(value).toString("base64") } : value];
        })))]))]));
}

/** A digest of rows, whatever order they were gathered in. */
export function digest(rows: Rows): string {
  const hash = createHash("sha256");
  for (const table of Object.keys(rows).sort()) {
    for (const key of Object.keys(rows[table]!).sort()) {
      // Each row as JSON reads it back, so a number's spelling does not matter.
      hash.update(`${table}\0${key}\0${JSON.stringify(JSON.parse(rows[table]![key]!))}\n`);
    }
  }
  return hash.digest("hex");
}

/** What a generation's run gave: its id, its lines, E, and the owner's digest at E. */
export interface Generated {
  readonly gen: string;
  readonly lines: Line[];
  readonly e: number;
  /** The owner's rows' digest at E, as the harness saw them when the log was at E. */
  readonly atE: string | undefined;
  readonly notices: string[];
  readonly calls: number;
}

export interface Harness {
  /** At the start of every outside call of the generation, before it is made. */
  readonly duringCall?: (at: { files: Files; gate: WriteGate; n: number }) => Promise<void>;
  /** After every unit of the generation (`n` counts units, from 100,000). */
  readonly afterUnit?: (at: { files: Files; gate: WriteGate; n: number }) => Promise<void>;
  /** The outside call to fail, by its number, before it is made or after it landed. */
  readonly failAt?: { readonly call: number; readonly when: "before" | "after" };
  /** Begin it and stop, a pass with no time to carry it on, for an export to join. */
  readonly onePass?: boolean;
  /** The schema version the lane runs under (the code's, unless a migration is played). */
  readonly schema?: number;
}

/**
 * Run a generation of `stub`'s owner (begun, or the one under way), pass after pass, each a
 * fresh lane over the owner's own storage, until it finishes.
 */
export function generate(stub: DurableObjectStub<OwnerDO>, harness: Harness = {}):
    Promise<Generated> {
  return runInDurableObject(stub, async (_instance: OwnerDO, state) => {
    const gate = new WriteGate(state.storage);
    const log = new ChangeLog(gate.storage);
    const files = filesOn(gate.storage, gate.write);
    const digests = new Map<number, string>();
    const note = () => {
      const { last } = log.state();
      if (!digests.has(last)) {
        digests.set(last, digest(standing(state.storage.sql)));
      }
    };
    let calls = 0;
    const call = <A extends unknown[], R>(made: (...args: A) => Promise<R>) =>
      async (...args: A): Promise<R> => {
        const n = ++calls;
        note();
        await harness.duringCall?.({ files, gate, n });
        if (harness.failAt?.call === n && harness.failAt.when === "before") {
          throw new Error("an outside call failed before it was made");
        }
        const done = await made(...args);
        if (harness.failAt?.call === n && harness.failAt.when === "after") {
          throw new Error("an outside call's answer was lost after it landed");
        }
        return done;
      };
    const real = env.FILES as ChunkStore;
    const store: ChunkStore = {
      ...real,
      mintKey: (ns) => real.mintKey(ns),
      putManifestPart: call((ns, key, gen, part, bytes) =>
        real.putManifestPart(ns, key, gen, part, bytes)),
      putManifestSegment: call((ns, key, gen, a, b, bytes) =>
        real.putManifestSegment(ns, key, gen, a, b, bytes)),
      putManifestRoot: call((ns, key, gen, parts, segments, span) =>
        real.putManifestRoot(ns, key, gen, parts, segments, span)),
      getManifestRoot: (ns, gen) => real.getManifestRoot(ns, gen),
      deleteManifest: (ns, gen, parts, segments) => real.deleteManifest(ns, gen, parts, segments),
    };
    let units = 100_000;
    const quiet = async <T>(unit: () => T): Promise<T> => {
      const value = await gate.quiet(unit);
      note();
      await harness.afterUnit?.({ files, gate, n: ++units });
      return value;
    };
    const notices: string[] = [];
    const lane = () => new Backups({
      storage: gate.storage, write: quiet, store, log, space: () => files.space(),
      owner: () => "owner", serves: () => true, schema: harness.schema ?? MIGRATIONS.length,
      halted: () => false, notice: (type) => notices.push(type),
    });
    // The one under way, or a new one, begun now (its owner changed since the last).
    if (state.storage.sql.exec("SELECT 1 FROM owner_backups WHERE state = 'writing'")
          .toArray().length === 0) {
      await gate.write(() => gate.storage.sql.exec("UPDATE owner_backup_state SET changed = 1"));
    }
    let gen = await gate.quiet(() => lane().forExport(Date.now() / 1000));
    for (let pass = 0; pass < 40; pass++) {
      await lane().run(Date.now() / 1000, harness.onePass === true ? Date.now()
        : Date.now() + 60_000);
      const row = state.storage.sql.exec<{ state: string }>(
        "SELECT state FROM owner_backups WHERE gen = ?", gen).toArray()[0];
      // One given up for a migration is followed by a fresh one: it is carried on instead.
      const fresh = row !== undefined ? undefined : state.storage.sql.exec<{ gen: string }>(
        "SELECT gen FROM owner_backups ORDER BY started_at DESC LIMIT 1").toArray()[0];
      if (fresh !== undefined && notices.length > 0) {
        gen = fresh.gen;
        continue;
      }
      if (harness.onePass === true || row === undefined || row.state === "done") {
        break;
      }
    }
    const { ns } = await files.space();
    const root = await real.getManifestRoot(ns, gen);
    const lines: Line[] = [];
    if (root !== null) {
      for (const [at, part] of root.parts.entries()) {
        lines.push(...new TextDecoder().decode(await real.getManifestPart(
          ns, root.key, gen, at, part.sha256)).trimEnd().split("\n")
          .map((line) => JSON.parse(line) as Line));
      }
      for (const segment of root.segments) {
        lines.push(...new TextDecoder().decode(await real.getManifestSegment(
          ns, root.key, gen, segment.a, segment.b, segment.sha256)).trimEnd().split("\n")
          .map((line) => JSON.parse(line) as Line));
      }
    }
    return { gen, lines, e: root?.e ?? -1,
             atE: root === null ? undefined : digests.get(root.e), notices, calls };
  });
}

/** Every page of an export from `cursor`, the lane driven while one is not yet written. */
export async function exportFrom(o: Owner, cursor: string | null, exportId?: string):
    Promise<{ lines: Line[]; cursors: string[]; exportId: string }> {
  const lines: Line[] = [];
  const cursors: string[] = [];
  let id = exportId;
  for (let next: string | null = cursor, waits = 0; ;) {
    const page = await o.stub.ownerExport(o.session(), next === null ? { cursor: null }
      : { cursor: next, export_id: id! });
    id = page.export_id;
    if (page.pending) {
      if (++waits > 400) {
        throw new Error("the export's generation did not go on");
      }
      await drainDue(o.stub);
      await new Promise((resolve) => setTimeout(resolve, 25));
      next = page.next;
      continue;
    }
    lines.push(...page.lines.map((line) => JSON.parse(line) as Line));
    if (page.next === null) {
      return { lines, cursors, exportId: id };
    }
    cursors.push(page.next);
    next = page.next;
  }
}
