/**
 * The bundle builder (slice 2) on an old-stack-shaped backup copy (`fixture.ts`): each
 * bundle's manifest holds the platform's own content ids for the backup's bytes, its rows
 * and archive are what rule 0 says, links and refused names are kept as the research
 * specifies, the excluded owner's bundle goes to the private folder alone, an owner rule 0
 * refuses is left out loudly while the others go on, and a dry run writes nothing.
 */
import { execFileSync } from "node:child_process";
import {
  appendFileSync, lstatSync, mkdirSync, mkdtempSync, openSync, readdirSync, readFileSync, rmSync,
  symlinkSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, describe, expect, it } from "vitest";

import { contentIdOf } from "../../contract/src/content";
import { CHUNK_BYTES, INLINE_BYTES } from "../../contract/src/limits";
import { FILE_DEFAULTS } from "../../contract/src/file-defaults";
import { type Entry, type Row } from "../build";
import {
  buildBundles, DIGESTS, erasureGuard, exclusionList, NOTICE, type Options, pages, PAGE_ROWS,
  PRIVATE_RECORD,
} from "../bundle";
import { census } from "../census";
import { survey } from "../survey";
import { TarWriter, TAR_MEMBER_MAX } from "../tar";
import {
  ALICE, BAD_DIR, BAD_NAME, bigBytes, BOB, DORA, ERASED, ERASING, type Fixture, fixture, FOUNDER,
  keyOf, NEWBIE, sha, sparse, TIERS,
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

/** Words that would let an agent act on an owner's old files without their word. */
const PERMISSIONS = /\b(?:you|agents?|it) (?:may|can|should) (?:delete|move|remove|merge|overwrite)\b|\bautomatic(?:ally)?\b|\b(?:merge|delete|move|remove) (?:everything|all|them all|the whole)\b|\b(?:delete|move|remove) the imported\b|\bif they do not answer\b/i;

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
    expect(summary).toMatchObject({ dry_run: false, owners: 16, bundled: 3, excluded: 1, erased: 1,
                                    erasure_pending: 11,
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
      `file ${I}/ignored.tar`,
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
      `file ${I}/policy.refused.json`,
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

  it("carries the collections as rows, and the owner's admission and tier", () => {
    const { f, out, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    // The old stack's policy has its rule tables and spend caps, which the platform's validator
    // no longer takes (d3 §N6), so it is archived, never a row.
    expect(rows(bundle)).toEqual([
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

  it("archives a policy the platform's validator refuses, verbatim, and keeps the default, " +
     "found by the census first", () => {
    const { f, out, options } = setup();
    const at = join(f.account(ALICE), "policy.db");
    const refused = JSON.stringify({ entries: [], egress: { mode: "everywhere",
                                                            destinations: [] } });
    const db = new DatabaseSync(at);
    db.prepare("UPDATE policy SET document = ?").run(refused);
    db.close();
    // Alice's, and the founder's old-shape one beside it.
    expect((census(f.copy, TIERS).report["rows"] as Record<string, number>)["policies_archived"])
      .toBe(2);
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    expect(rows(bundle).filter((row) => row.kind === "policy")).toEqual([]);
    const archived = manifest(bundle).find((entry) => entry.path === `${I}/policy.refused.json`)!;
    expect(bytesOf(bundle, archived).toString("utf8")).toBe(refused);
    expect(bytesOf(bundle, manifest(bundle).find((entry) => entry.path === `${I}/README.md`)!)
      .toString("utf8")).toContain("`policy.refused.json` is your old policy, unchanged");
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
    // The records kept as they came, never "read-only" (the files plane has no such bit), and
    // what refused.tar holds beside names, the links links.txt does not list.
    expect(readme).toContain("(copies, kept as they came: don't write to them)");
    expect(readme).not.toContain("read-only");
    expect(readme).toContain("and links whose target is not UTF-8, which `links.txt` does not " +
                             "list.");
    // Nothing secret travels: no credential vault, no engine HOME, no box secret.
    const all = readdirSync(join(bundle, "inline")).map((name) =>
      readFileSync(join(bundle, "inline", name)).toString("latin1")).join("\n");
    expect(all).not.toContain("sk-secret");
    expect(all).not.toContain("secret-user");
    expect(all).not.toMatch(/"token"/);
  });

  it("tells the owner's agent first what to say of the move, promising only what the app " +
     "connects, and never to act on the old files alone", () => {
    const { f, out, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    const readme = bytesOf(bundle, manifest(bundle).find((e) => e.path === `${I}/README.md`)!)
      .toString();
    const lines = readme.split("\n");
    expect([lines[0], lines[2]]).toEqual(["# Your files from the old TinyAssets",
                                          "## For your agent"]);
    const section = readme.slice(readme.indexOf("## For your agent"),
                                 readme.indexOf("\n## ", readme.indexOf("## For your agent")))
      .replace(/\s+/g, " ");
    expect(section).toContain("none of them runs now");
    // What the owner's app connects (model sources, connected apps, API keys), and no
    // connector, which nothing on the platform connects.
    for (const again of ["model sources", "the AI assistants they use", "new API keys"]) {
      expect(section).toContain(again);
    }
    expect(section).not.toMatch(/connector/i);
    expect(section).toContain("never ask them to paste a key, token or password");
    expect(section).toContain("copy only what they pick");
    expect(section).toContain("never delete or move anything in this folder until they say so");
    expect(section).toContain(`\`- Old TinyAssets files are in ${I}/: merge only what they ` +
                              "pick, delete nothing there until they say` as a directive on " +
                              "their profile");
    // No word that lets an agent act alone; and the pattern sees each kind an edit could add.
    expect(section).not.toMatch(PERMISSIONS);
    for (const added of ["You may delete this folder once it is merged.",
                         "Merge them automatically.", "Agents can move the old files.",
                         "If they do not answer, merge everything and delete the folder."]) {
      expect(`${section} ${added}`, added).toMatch(PERMISSIONS);
    }
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
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(BOB), keyOf(NEWBIE)].sort());
    expect(readdirSync(priv).sort()).toEqual([keyOf(FOUNDER), DIGESTS, PRIVATE_RECORD].sort());
    expect(json(join(priv, keyOf(FOUNDER), "owner.json"))["owner"]).toBe(FOUNDER);
    expect(manifest(join(priv, keyOf(FOUNDER))).map((entry) => entry.path))
      .toContain(`${I}/home/notes/n.md`);
    expect(lstatSync(join(priv, "cutover.private.json")).mode & 0o777).toBe(0o600);
    const record = json(join(priv, "cutover.private.json"))["owners"] as Record<string, unknown>[];
    expect(record.map((owner) => [owner["owner"], owner["excluded"], owner["subscription_id"]]))
      .toEqual([[ALICE, false, "sub_alice"], [BOB, false, null], [FOUNDER, true, "sub_founder"],
                [NEWBIE, false, null]]);
    // The digests are keyed by owner: a 0600 file of the private folder's, never stdout.
    expect(lstatSync(join(priv, DIGESTS)).mode & 0o777).toBe(0o600);
    expect(Object.keys(json(join(priv, DIGESTS))["digests"] as object).sort())
      .toEqual([keyOf(ALICE), keyOf(BOB), keyOf(FOUNDER), keyOf(NEWBIE)].sort());
    const text = JSON.stringify(summary);
    for (const name of [ALICE, BOB, FOUNDER, keyOf(ALICE), keyOf(BOB), keyOf(FOUNDER)]) {
      expect(text).not.toContain(name);
    }
  });

  it("refuses the private folder inside the out folder, or the out folder inside it, by real path",
     () => {
    const { f, out, priv, options } = setup();
    const nested = join(out, "deep", "private");
    mkdirSync(nested, { recursive: true });
    const linked = join(priv, "to-out");
    symlinkSync(out, linked);
    const inner = join(priv, "out");
    mkdirSync(inner);
    for (const [o, p] of [[out, nested], [inner, priv], [out, linked], [out, `${out}/`],
                          [out, out]] as const) {
      expect(() => buildBundles(f.copy, { ...options, out: o, private: p }), `${o} ${p}`)
        .toThrow(/one inside the other/);
    }
    expect(readdirSync(out)).toEqual(["deep"]);
    expect(readdirSync(priv).sort()).toEqual(["out", "to-out"]);
  });

  it("refuses an empty exclusion list: the founder's owner is never bundled as an ordinary one",
     () => {
    const { f, out, priv, options } = setup();
    for (const list of ["", "# nobody\n\n"]) {
      expect(() => buildBundles(f.copy, { ...options, exclude: exclusionList(list) }))
        .toThrow(/names no owner/);
    }
    expect(() => buildBundles(f.copy, { ...options, out: null, private: null,
                                        exclude: new Set() })).toThrow(/names no owner/);
    expect([readdirSync(out), readdirSync(priv)]).toEqual([[], []]);
  });

  it("keeps what the sync's defaults ignore verbatim in ignored.tar, counted", () => {
    const { f, out, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    const tar = mkdtempSync(join(tmpdir(), "ta-tar-"));
    temps.push(tar);
    writeFileSync(join(tar, "ignored.tar"),
                  bytesOf(bundle, manifest(bundle).find((e) => e.path === `${I}/ignored.tar`)!));
    execFileSync("tar", ["-xf", join(tar, "ignored.tar"), "-C", tar]);
    const dep = join(tar, "novel", "workspace", "node_modules", "dep");
    expect(readdirSync(dep).sort()).toEqual(["index.js", "package.json"]);
    expect(readFileSync(join(dep, "index.js"))
      .equals(readFileSync(join(f.tree(ALICE), "novel", "workspace", "node_modules", "dep",
                                "index.js")))).toBe(true);
    expect(manifest(bundle).some((e) => e.path.includes("/node_modules"))).toBe(false);
  });

  it("refuses a tar member past the header's size field, before writing it", () => {
    const dir = mkdtempSync(join(tmpdir(), "ta-tar-"));
    temps.push(dir);
    sparse(join(dir, "huge"), TAR_MEMBER_MAX + 1);
    const tar = new TarWriter(openSync(join(dir, "out.tar"), "w"));
    expect(() => tar.add(Buffer.from("huge"), Buffer.from(join(dir, "huge"))))
      .toThrow(/past the format/);
    tar.end();
    expect(lstatSync(join(dir, "out.tar")).size).toBe(1024);
  });

  it("refuses an owner the old stack never admitted, and backs one up as absent", () => {
    const { f, out, priv, options } = setup();
    const db = new DatabaseSync(join(f.work, "data", "_data", ".broker", "state",
                                     "owner-identities.db"));
    db.prepare("DELETE FROM owner_admission WHERE digest IN (?, ?)").run(sha(BOB), sha(FOUNDER));
    db.close();
    const summary = buildBundles(f.copy, options);
    expect(summary).toMatchObject({ bundled: 2, excluded: 1, refused: { never_admitted: 1 } });
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(NEWBIE)].sort());
    expect(json(join(priv, keyOf(FOUNDER), "owner.json"))["admission"])
      .toEqual({ state: "absent", reasons: [] });
  });

  it("bundles no owner any erasure source names: not in either folder, nor either record", () => {
    const { f, out, priv, options } = setup();
    const erasing = [...Object.values(ERASING), DORA, ERASED];
    const summary = buildBundles(f.copy, { ...options, exclude: new Set([FOUNDER, ...erasing]) });
    expect(summary).toMatchObject({ bundled: 3, excluded: 1, erased: 1, erasure_pending: 11,
                                    refused: {} });
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(BOB), keyOf(NEWBIE)].sort());
    expect(readdirSync(priv).sort())
      .toEqual([keyOf(FOUNDER), DIGESTS, PRIVATE_RECORD].sort());
    const record = json(join(priv, PRIVATE_RECORD))["owners"] as Record<string, unknown>[];
    expect(record.map((owner) => owner["owner"]).sort())
      .toEqual([ALICE, BOB, FOUNDER, NEWBIE].sort());
    expect(Object.keys(json(join(priv, DIGESTS))["digests"] as object).sort())
      .toEqual([keyOf(ALICE), keyOf(BOB), keyOf(FOUNDER), keyOf(NEWBIE)].sort());
    for (const dir of [out, priv]) {
      for (const principal of erasing) {
        expect(() => execFileSync("grep", ["-rlF", principal, dir]), principal).toThrow();
      }
    }
  });

  it("refuses the run, the guard inside the bundle step, for any erased or erasing name", () => {
    const { f } = setup();
    const fleet = survey(f.copy);
    expect(() => erasureGuard(fleet, [ALICE, BOB, FOUNDER])).not.toThrow();
    for (const principal of [...Object.values(ERASING), DORA, ERASED, "user_gone", "user_gone2"]) {
      expect(() => erasureGuard(fleet, [ALICE, principal]), principal)
        .toThrow(/erased or erasing owner/);
    }
  });

  it("bundles an owner whose erasure is pending into neither folder, excluded or not", () => {
    const { f, out, priv, options } = setup();
    const summary = buildBundles(f.copy, { ...options, exclude: new Set([FOUNDER, DORA]) });
    expect(summary).toMatchObject({ bundled: 3, excluded: 1, erasure_pending: 11, refused: {} });
    expect(readdirSync(out)).not.toContain(keyOf(DORA));
    expect(readdirSync(priv)).not.toContain(keyOf(DORA));
    const record = json(join(priv, PRIVATE_RECORD))["owners"] as Record<string, unknown>[];
    expect(record.map((owner) => owner["owner"])).not.toContain(DORA);
    // Not a byte of hers anywhere: grep finds nothing (its exit 1).
    for (const dir of [out, priv]) {
      expect(() => execFileSync("grep", ["-rlF", "dora asked", dir]), dir).toThrow();
    }
  });

  it("stops the run on what an unfinished account deletion left in the shared layer, and " +
     "refuses the owner whose image holds one", () => {
    const { f, out, options } = setup();
    mkdirSync(join(f.tree(BOB), ".account-deletions"));
    writeFileSync(join(f.tree(BOB), ".account-deletions", "receipt.json"), "{}");
    expect(buildBundles(f.copy, options).refused).toEqual({ unfinished_erasure: 1 });
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(NEWBIE)].sort());
    const again = setup();
    mkdirSync(join(again.f.work, "data", "_data", ".deleting", "home"), { recursive: true });
    expect(() => buildBundles(again.f.copy, again.options)).toThrow(/unfinished_erasure/);
    expect(readdirSync(again.out)).toEqual([]);
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
    const { f, out, priv, options } = setup();
    buildBundles(f.copy, options);
    const bundle = join(out, keyOf(ALICE));
    const index = readFileSync(join(bundle, "bundle.json"));
    expect((json(join(priv, DIGESTS))["digests"] as Record<string, string>)[keyOf(ALICE)])
      .toBe(sha(index));
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
    expect(summary).toMatchObject({ bundled: 2, excluded: 1, refused: { unknown_table: 1 } });
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(NEWBIE)].sort());
  });

  it("leaves out an owner past the platform's limits, counted, while the others go on", () => {
    const { f, out, options } = setup();
    sparse(join(f.tree(BOB), "main", "workspace", "huge.bin"), FILE_DEFAULTS.max_file_bytes + 1);
    const summary = buildBundles(f.copy, options);
    expect(summary.refused).toEqual({ over_file_bytes: 1, over_storage: 1 });
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(NEWBIE)].sort());
  });

  it("refuses the whole run when the backup copy fails its own check, writing nothing", () => {
    const { f, out, priv, options } = setup();
    appendFileSync(join(f.work, "300001.img"), "x");
    expect(() => buildBundles(f.copy, options)).toThrow(/image_mismatch/);
    expect([readdirSync(out), readdirSync(priv)]).toEqual([[], []]);
  });

  it("reads and hashes everything in a dry run, counting as the real run does, and writes nothing",
     () => {
    const { f, options } = setup();
    const find = () => execFileSync("find", [f.work, "-printf", "%p %s %T@ %m\n"],
                                    { encoding: "utf8" });
    const before = find();
    const dry = buildBundles(f.copy, { out: null, private: null, exclude: new Set([FOUNDER]),
                                       tiers: TIERS });
    expect(dry).toMatchObject({ dry_run: true, bundled: 3, excluded: 1, refused: {} });
    expect(find()).toBe(before);
    const real = buildBundles(f.copy, options);
    expect({ ...dry, dry_run: false }).toEqual(real);
  });

  it("pages at most PAGE_ROWS items and a MiB encoded", () => {
    const small = Array.from({ length: 600 }, (_, i) => ({ i }));
    expect(pages(small).map((page) => page.length)).toEqual([PAGE_ROWS, PAGE_ROWS, 88]);
    const large = Array.from({ length: 5 }, () => ({ body: "x".repeat(300_000) }));
    expect(pages(large).map((page) => page.length)).toEqual([3, 2]);
  });
});
