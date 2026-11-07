/**
 * The bundle builder (slice 2) on an old-stack-shaped backup copy (`fixture.ts`): each
 * bundle's manifest holds the platform's own content ids for the backup's bytes, its rows
 * and archive are what rule 0 says, links and refused names are kept as the research
 * specifies, the excluded owner's bundle goes to the private folder alone, an owner rule 0
 * refuses is left out loudly while the others go on, and a dry run writes nothing.
 */
import { execFileSync } from "node:child_process";
import {
  appendFileSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, describe, expect, it } from "vitest";

import { contentIdOf } from "../../contract/src/content";
import { CHUNK_BYTES, FILE_DEFAULTS, INLINE_BYTES } from "../../contract/src/limits";
import { buildBundles, type Entry, exclusionList, NOTICE, type Options, pages, PAGE_ROWS,
         type Row } from "../bundle";
import {
  ALICE, BAD_DIR, BAD_NAME, bigBytes, BOB, type Fixture, fixture, FOUNDER, keyOf, sha, sparse,
  TIERS,
} from "./fixture";

const temps: string[] = [];
afterEach(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function setup(): { f: Fixture; out: string; priv: string; options: Options } {
  const f = fixture();
  const out = mkdtempSync(join(tmpdir(), "ta-bundles-"));
  const priv = mkdtempSync(join(tmpdir(), "ta-private-"));
  temps.push(f.work, out, priv);
  return { f, out, priv,
           options: { out, private: priv, exclude: new Set([FOUNDER]), tiers: TIERS } };
}

const json = (path: string) => JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;

function manifest(bundle: string): Entry[] {
  return readdirSync(join(bundle, "manifest")).sort((a, b) => parseInt(a) - parseInt(b))
    .flatMap((page) => json(join(bundle, "manifest", page))["entries"] as Entry[]);
}

function rows(bundle: string): Row[] {
  return readdirSync(join(bundle, "rows")).sort((a, b) => parseInt(a) - parseInt(b))
    .flatMap((page) => json(join(bundle, "rows", page))["rows"] as Row[]);
}

/** A file's bytes as the bundle holds them: inline, or its chunks in order. */
function bytesOf(bundle: string, entry: Entry): Buffer {
  return entry.chunks === undefined
    ? readFileSync(join(bundle, "inline", entry.content_id!))
    : Buffer.concat(entry.chunks.map((chunk) => readFileSync(join(bundle, "chunks", chunk))));
}

const I = "/workspace/imported";

describe("the bundle builder", () => {
  it("writes each owner's tree with the platform's own content ids for the backup's bytes", () => {
    const { f, out, options } = setup();
    const summary = buildBundles(f.copy, options);
    expect(summary).toMatchObject({ dry_run: false, owners: 3, bundled: 2, excluded: 1, erased: 0,
                                    refused: {} });
    const bundle = join(out, keyOf(ALICE));
    const entries = manifest(bundle);
    expect(entries.map((entry) => `${entry.kind} ${entry.path}`)).toEqual([
      `dir ${I}`,
      `dir ${I}/.records`,
      `file ${I}/.records/.runs.db`,
      `file ${I}/.records/.tinyassets.db`,
      `dir ${I}/.records/broker`,
      `file ${I}/.records/broker/conversations.db`,
      `file ${I}/.records/broker/message_ledger.db`,
      `file ${I}/.records/broker/policy.db`,
      `file ${I}/README.md`,
      `file ${I}/links.txt`,
      `dir ${I}/novel`,
      `dir ${I}/novel/.records`,
      `file ${I}/novel/.records/.conversation_memory.db`,
      `dir ${I}/novel/.records/sessions`,
      `file ${I}/novel/.records/sessions/history.db`,
      `file ${I}/novel/AGENTS.md`,
      `dir ${I}/novel/notes`,
      `file ${I}/novel/notes/ch1.md`,
      `dir ${I}/novel/workspace`,
      `file ${I}/novel/workspace/big.bin`,
      `file ${I}/novel/workspace/run.sh`,
      `dir ${I}/old`,
      `file ${I}/old/soul.md`,
      `file ${I}/refused.tar`,
      `dir ${I}/wiki`,
      `file ${I}/wiki/home.md`,
    ]);
    const tree = f.tree(ALICE);
    for (const entry of entries.filter((e) => e.kind === "file")) {
      const bytes = bytesOf(bundle, entry);
      expect(entry.size, entry.path).toBe(bytes.length);
      expect(entry.content_id, entry.path).toBe(contentIdOf(bytes));
    }
    const big = entries.find((entry) => entry.path.endsWith("/big.bin"))!;
    expect(big.chunks).toHaveLength(2);
    expect(big.chunks).toEqual([sha(bigBytes().subarray(0, CHUNK_BYTES)),
                                sha(bigBytes().subarray(CHUNK_BYTES))]);
    expect(bytesOf(bundle, big).equals(bigBytes())).toBe(true);
    const run = entries.find((entry) => entry.path.endsWith("/run.sh"))!;
    expect([run.mode, run.mtime_ms, run.chunks])
      .toEqual([1, Math.floor(lstatSync(join(tree, "novel", "workspace", "run.sh")).mtimeMs),
                undefined]);
    expect(entries.find((entry) => entry.path.endsWith("/ch1.md"))!.mode).toBe(0);
    // Every small file's content is inline, every large one's in chunks.
    for (const entry of entries.filter((e) => e.kind === "file")) {
      expect(entry.chunks === undefined, entry.path).toBe(entry.size! <= INLINE_BYTES);
    }
  });

  it("carries the policy and the collections as rows, and the owner's admission and tier", () => {
    const { f, out, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    expect(rows(bundle)).toEqual([
      { kind: "policy", document: { entries: [], egress: { mode: "ask", destinations: [] },
                                    spend: { daily: null, monthly: null, transaction: null,
                                             ask_above: null, source_ceilings: {} } } },
      { kind: "collection", collection: "board", row_id: "b1", private: 1, body: '{"t":"first"}' },
      { kind: "collection", collection: "board", row_id: "b2", private: 0, body: '{"t":"second"}' },
    ]);
    expect(json(join(bundle, "owner.json"))).toEqual({
      format: 1, owner: ALICE, key: keyOf(ALICE), uid: 300_001,
      admission: { state: "admitted", reasons: [] }, tier: "paid", notice: NOTICE,
      centers: [{ name: "novel", retired: false }, { name: "old", retired: true }],
    });
    expect(json(join(out, keyOf(BOB), "owner.json"))).toMatchObject({
      admission: { state: "suspended", reasons: ["billing"] }, tier: "free" });
    expect(rows(join(out, keyOf(BOB)))).toEqual([]);
  });

  it("archives each store by rule 0: kept tables only, a whole store byte for byte", () => {
    const { f, out, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    const entries = manifest(bundle);
    const work = mkdtempSync(join(tmpdir(), "ta-archive-"));
    temps.push(work);
    const tables = (path: string) => {
      const at = join(work, sha(path));
      writeFileSync(at, bytesOf(bundle, entries.find((e) => e.path === path)!));
      const db = new DatabaseSync(at, { readOnly: true });
      const found = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND " +
                                 "name NOT LIKE 'sqlite%' ORDER BY name")
        .all().map((row) => `${row["name"]} ${Number(db.prepare(
          `SELECT COUNT(*) AS n FROM "${row["name"]}"`).get()!["n"])}`);
      db.close();
      return found;
    };
    expect(tables(`${I}/.records/broker/policy.db`)).toEqual(["policy 1", "proposals 0", "receipts 1"]);
    expect(tables(`${I}/.records/broker/conversations.db`))
      .toEqual(["collection_rows 2", "message_bodies 1"]);
    expect(tables(`${I}/novel/.records/.conversation_memory.db`)).toEqual(["conversation_turns 2"]);
    expect(tables(`${I}/.records/.runs.db`)).toEqual(["branch_schedules 1"]);
    expect(tables(`${I}/.records/.tinyassets.db`)).toEqual(["branch_definitions 1"]);
    const ledger = entries.find((e) => e.path === `${I}/.records/broker/message_ledger.db`)!;
    expect(bytesOf(bundle, ledger)
      .equals(readFileSync(join(f.account(ALICE), "message_ledger.db")))).toBe(true);
    const readme = bytesOf(bundle, entries.find((e) => e.path === `${I}/README.md`)!).toString();
    expect(readme).toContain("| `.records/broker/policy.db` | receipts | 1 |");
    expect(readme).toContain("- `old/` (retired)");
    // Nothing secret travels: no credential vault, no engine HOME, no box secret.
    const all = readdirSync(join(bundle, "inline")).map((name) =>
      readFileSync(join(bundle, "inline", name)).toString("latin1")).join("\n");
    expect(all).not.toContain("sk-secret");
    expect(all).not.toContain("secret-user");
    expect(all).not.toMatch(/"token"/);
  });

  it("lists links in links.txt, never followed, and keeps refused names verbatim in refused.tar", () => {
    const { f, out, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    const entries = manifest(bundle);
    expect(bytesOf(bundle, entries.find((e) => e.path === `${I}/links.txt`)!).toString()).toBe(
      "# Each link your old files held: its path -> where it pointed. Not followed.\n" +
      `"${I}/novel/workspace/chapter" -> "../notes/ch1.md"\n`);
    const tar = mkdtempSync(join(tmpdir(), "ta-tar-"));
    temps.push(tar);
    const archive = join(tar, "refused.tar");
    writeFileSync(archive,
      bytesOf(bundle, entries.find((e) => e.path === `${I}/refused.tar`)!));
    const into = join(tar, "x");
    mkdirSync(into);
    execFileSync("tar", ["-xf", archive, "-C", into]);
    const at = Buffer.from(join(into, "novel", "workspace"));
    const names = readdirSync(at, { encoding: "buffer" }).sort(Buffer.compare);
    expect(names).toEqual([BAD_DIR, BAD_NAME].sort(Buffer.compare));
    const slash = Buffer.from("/");
    expect(readFileSync(Buffer.concat([at, slash, BAD_NAME])).toString()).toBe("odd");
    expect(readFileSync(Buffer.concat([at, slash, BAD_DIR, Buffer.from("/inside.md")])).toString())
      .toBe("kept in the tar");
  });

  it("writes the excluded owner's bundle to the private folder alone, with the private record", () => {
    const { f, out, priv, options } = setup();
    const summary = buildBundles(f.copy, options);
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(BOB)].sort());
    expect(readdirSync(priv).sort()).toEqual([keyOf(FOUNDER), "cutover.private.json"].sort());
    expect(json(join(priv, keyOf(FOUNDER), "owner.json"))["owner"]).toBe(FOUNDER);
    expect(manifest(join(priv, keyOf(FOUNDER))).map((entry) => entry.path))
      .toContain(`${I}/home/notes/n.md`);
    expect(lstatSync(join(priv, "cutover.private.json")).mode & 0o777).toBe(0o600);
    const record = json(join(priv, "cutover.private.json"))["owners"] as Record<string, unknown>[];
    expect(record.map((owner) => [owner["owner"], owner["excluded"], owner["subscription_id"]]))
      .toEqual([[ALICE, false, "sub_alice"], [BOB, false, null], [FOUNDER, true, "sub_founder"]]);
    expect(Object.keys(summary.digests).sort())
      .toEqual([keyOf(ALICE), keyOf(BOB), keyOf(FOUNDER)].sort());
    expect(JSON.stringify(summary)).not.toContain("user_");
  });

  it("refuses an exclusion list naming no owner of the backup, before anything is written", () => {
    const { f, out, priv, options } = setup();
    expect(() => buildBundles(f.copy, { ...options, exclude: new Set(["user_fonder"]) }))
      .toThrow(/exclusion list/);
    expect([readdirSync(out), readdirSync(priv)]).toEqual([[], []]);
    expect(exclusionList(`# the founder\n${FOUNDER}\n\n  user_canary  \n`))
      .toEqual(new Set([FOUNDER, "user_canary"]));
  });

  it("seals each bundle: its index holds every part's hash, and its own hash is the digest", () => {
    const { f, out, options } = setup();
    const summary = buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    const index = readFileSync(join(bundle, "bundle.json"));
    expect(summary.digests[keyOf(ALICE)]).toBe(sha(index));
    const parts = (JSON.parse(index.toString()) as { parts: Record<string, string> }).parts;
    expect(Object.keys(parts).sort()).toEqual(["manifest/0.json", "owner.json", "rows/0.json"]);
    for (const [name, digest] of Object.entries(parts)) {
      expect(sha(readFileSync(join(bundle, name))), name).toBe(digest);
    }
    for (const chunk of readdirSync(join(bundle, "chunks"))) {
      expect(sha(readFileSync(join(bundle, "chunks", chunk)))).toBe(chunk);
    }
    expect(readdirSync(bundle).sort())
      .toEqual(["bundle.json", "chunks", "inline", "manifest", "owner.json", "rows"]);
  });

  it("leaves out an owner rule 0 refuses, counted, while the others go on", () => {
    const { f, out, options } = setup();
    const db = new DatabaseSync(join(f.tree(BOB), ".centers.db"));
    db.exec("CREATE TABLE mystery (x)");
    db.close();
    const summary = buildBundles(f.copy, options);
    expect(summary).toMatchObject({ bundled: 1, excluded: 1, refused: { unknown_table: 1 } });
    expect(readdirSync(out)).toEqual([keyOf(ALICE)]);
  });

  it("leaves out an owner past the platform's limits, counted, while the others go on", () => {
    const { f, out, options } = setup();
    sparse(join(f.tree(BOB), "main", "workspace", "huge.bin"), FILE_DEFAULTS.max_file_bytes + 1);
    const summary = buildBundles(f.copy, options);
    expect(summary.refused).toEqual({ over_file_bytes: 1, over_storage: 1 });
    expect(readdirSync(out)).toEqual([keyOf(ALICE)]);
  });

  it("refuses the whole run when the backup copy fails its own check, writing nothing", () => {
    const { f, out, priv, options } = setup();
    appendFileSync(join(f.work, "300001.img"), "x");
    expect(() => buildBundles(f.copy, options)).toThrow(/image_mismatch/);
    expect([readdirSync(out), readdirSync(priv)]).toEqual([[], []]);
  });

  it("reads and hashes everything in a dry run, and writes nothing", () => {
    const { f } = setup();
    const before = execFileSync("find", [f.work, "-printf", "%p %s %T@\n"], { encoding: "utf8" });
    const summary = buildBundles(f.copy, { out: null, private: null, exclude: new Set([FOUNDER]),
                                           tiers: TIERS });
    expect(summary).toMatchObject({ dry_run: true, bundled: 2, excluded: 1, digests: {},
                                    refused: {} });
    // big.bin's two chunks, and one each for the two stores copied whole (8 KiB each); the
    // files the import makes (README, links, the tar, the tables' copies) are not made.
    expect(summary.chunks).toBe(4);
    expect(execFileSync("find", [f.work, "-printf", "%p %s %T@\n"], { encoding: "utf8" }))
      .toBe(before);
  });

  it("pages at most PAGE_ROWS items and a MiB encoded", () => {
    const small = Array.from({ length: 600 }, (_, i) => ({ i }));
    expect(pages(small).map((page) => page.length)).toEqual([PAGE_ROWS, PAGE_ROWS, 88]);
    const large = Array.from({ length: 5 }, () => ({ body: "x".repeat(300_000) }));
    expect(pages(large).map((page) => page.length)).toEqual([3, 2]);
  });
});
