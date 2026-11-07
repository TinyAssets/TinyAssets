/**
 * The census (slice 1) on an old-stack-shaped backup copy (`fixture.ts`): its exact counts,
 * that it holds no user content and writes nothing to the copy, rule 0's loud refusals,
 * the backup's own gate, and each owner's fit against the platform's own limits.
 */
import {
  appendFileSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync,
  symlinkSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { FILE_DEFAULTS } from "../../contract/src/limits";
import { DEFAULT_IGNORES, ignoredBy } from "../../contract/src/sync-ignore";
import { buildBundles } from "../bundle";
import { census } from "../census";
import { ERASURE_SOURCES } from "../erasure";
import { TAR_MEMBER_MAX } from "../tar";
import { IMPORTED, newTree, walkUser } from "../survey";
import {
  ALICE, BAD_NAME, BOB, deletionJob, DORA, ERASED, ERASING, type Fixture, fixture, FOUNDER, keyOf,
  makeStore, sha, sparse, TIERS, unmergedStore,
} from "./fixture";

const made: Fixture[] = [];
function fresh(): Fixture {
  const f = fixture();
  made.push(f);
  return f;
}
afterEach(() => {
  for (const f of made.splice(0)) {
    rmSync(f.work, { recursive: true, force: true });
  }
});

/** Every node under `root` as lstat sees it, with each file's bytes' hash: the copy's state. */
function state(root: string): string[] {
  const found: string[] = [];
  const walk = (dir: Buffer) => {
    for (const name of readdirSync(dir, { encoding: "buffer" }).sort(Buffer.compare)) {
      const path = Buffer.concat([dir, Buffer.from("/"), name]);
      const stat = lstatSync(path);
      const bytes = stat.isFile() ? sha(readFileSync(path)) : "";
      found.push(`${path.toString("hex")} ${stat.mode} ${stat.size} ${stat.mtimeMs} ${bytes}`);
      if (stat.isDirectory()) {
        walk(path);
      }
    }
  };
  walk(Buffer.from(root));
  return found;
}

describe("the census", () => {
  it("counts the backup copy exactly, ready to import", () => {
    const f = fresh();
    const { report, ready } = census(f.copy, TIERS);
    expect(ready).toBe(true);
    expect(report["problems"]).toEqual({});
    expect(report["backup"]).toEqual({ pins: 4, verified: true });
    expect(report["owners"]).toEqual({
      identities: 15, imported: 3, erased: 1, erasure_pending: 11,
      admission: { admitted: 13, suspended: 1, erased: 1 },
      tier: { free: 1, paid: 2 }, with_customer: 2, with_subscription: 2, not_authkit: 0,
      with_volume: 4, no_volume: 0, centers: 4, retired_centers: 1, orphan_accounts: 0,
      orphan_rows: { admission: 0, plans: 0, volumes: 0 },
    });
    // user_gone by principal; user_gone2 (erased admission) and user_gone3 by digest alone.
    expect(report["tombstones"]).toEqual({ by_principal: 1, digest_only: 2 });
    expect(report["files"]).toEqual({
      files: 10, dirs: 5, bytes: (report["files"] as { bytes: number }).bytes, inline_files: 9,
      largest: (report["files"] as { largest: number }).largest, links: 1, refused_names: 2,
      ignored: 2, ignored_trees: 1, specials: 1, hardlinks: 0, sqlite_in_user_files: 0,
      archived_stores: 8,
    });
    expect((report["files"] as { largest: number }).largest).toBe(4 * 1024 * 1024 + 10);
    expect(report["rows"]).toEqual({ policies: 2, collection_rows: 2, body_bytes: 27 });
    expect(report["stores"]).toMatchObject({
      "account/policy.db/policy carry": 2, "account/policy.db/once_approvals drop": 1,
      "account/policy.db/receipts archive": 1, "account/conversations.db/collection_rows carry": 2,
      "account/message_ledger.db/* archive": 1, "sessions/steering.db drop, not read": 1,
      "center/.conversation_memory.db/conversation_turns archive": 2,
      "center/.conversation_memory.db/conversation_backfill drop": 1,
      "root/.runs.db/runs drop": 2, "root/.runs.db/branch_schedules archive": 1,
      "root/.tinyassets.db/branch_definitions archive": 1, "sessions/history.db/* archive": 1,
      "shared/.broker/state/owner-identities.db/owner_identities carry": 15,
      "shared/.broker/state/owner-identities.db/deletion_jobs drop": 1,
      "shared/.api-keys.sqlite3/api_keys drop": 2, "shared/.hook-owners.sqlite3/hook_owners drop": 1,
      "shared/.router-deleted-owners.sqlite3/deleted_owners carry": 2,
      "shared/.broker/state/owner-identities.db/owner_box_revocations drop": 2,
      "shared/.boxhost/boxhost.db/owner_destroys drop": 1,
      "shared/.erasure-ledger.sqlite3/events drop": 1,
      "shared/.workos-revocations.sqlite3/revocations drop": 2,
    });
  });

  it("reports counts only: no principal, no name an owner chose, no secret", () => {
    const f = fresh();
    const { report, table } = census(f.copy, TIERS);
    const text = JSON.stringify(report) + table;
    for (const secret of [ALICE, BOB, FOUNDER, "user_gone", "novel", "ch1", "big.bin", "chapter",
                          "sk-secret", "secret-user", "cus_alice", "sub_alice", "inside.md",
                          BAD_NAME.toString("latin1"), "dark night"]) {
      expect(text).not.toContain(secret);
    }
    expect(table).toContain(`${keyOf(ALICE)}\tfiles\t6`);
    expect(table).toContain(`${keyOf(BOB)}\tadmission\tsuspended,billing`);
  });

  it("never writes to the backup copy, its stores in WAL mode: not a census, not a bundle", () => {
    const f = fresh();
    // The fleet's, an account's and an image root's stores are in WAL mode, as the old
    // stack's were: opened by `mode=ro` alone, SQLite would make each one's -wal and -shm.
    for (const store of [join(f.work, "data", "_data", ".broker", "state", "owner-identities.db"),
                         join(f.account(ALICE), "policy.db"), join(f.tree(ALICE), ".tinyassets.db")]) {
      expect(readFileSync(store).subarray(18, 20)).toEqual(Buffer.from([2, 2]));
    }
    const before = state(f.work);
    census(f.copy, TIERS);
    expect(state(f.work)).toEqual(before);
    const out = mkdtempSync(join(tmpdir(), "ta-out-"));
    const priv = mkdtempSync(join(tmpdir(), "ta-priv-"));
    try {
      expect(buildBundles(f.copy, { out, private: priv, exclude: new Set([FOUNDER]),
                                    tiers: TIERS }).bundled).toBe(2);
      expect(state(f.work)).toEqual(before);
    } finally {
      rmSync(out, { recursive: true, force: true });
      rmSync(priv, { recursive: true, force: true });
    }
  });

  it("counts storage as the files plane charges it, the same number the bundle seals", () => {
    const f = fresh();
    const { table } = census(f.copy, TIERS);
    const stored = Number(new RegExp(`${keyOf(ALICE)}\\tstored\\t(\\d+)`).exec(table)![1]);
    const out = mkdtempSync(join(tmpdir(), "ta-out-"));
    const priv = mkdtempSync(join(tmpdir(), "ta-priv-"));
    try {
      buildBundles(f.copy, { out, private: priv, exclude: new Set([FOUNDER]), tiers: TIERS });
      const counts = (JSON.parse(readFileSync(join(out, keyOf(ALICE), "bundle.json"), "utf8")) as
        { counts: { chunk_bytes: number; inline_bytes: number } }).counts;
      // Unique chunks, unique inline content, and the collections' bodies (27 bytes).
      expect(stored).toBe(counts.chunk_bytes + counts.inline_bytes + 27);
    } finally {
      rmSync(out, { recursive: true, force: true });
      rmSync(priv, { recursive: true, force: true });
    }
  });
});

describe("rule 0", () => {
  it("refuses a table no rule names, by its schema name, and only that owner", () => {
    const f = fresh();
    const db = new DatabaseSync(join(f.tree(BOB), ".centers.db"));
    db.exec("CREATE TABLE mystery (x)");
    db.close();
    const { report, ready, table } = census(f.copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toEqual({ unknown_table: 1 });
    expect(table).toContain(`${keyOf(BOB)}\tproblem unknown_table\troot/.centers.db/mystery`);
  });

  it("refuses an entry no rule names, naming it by its digest alone", () => {
    const f = fresh();
    writeFileSync(join(f.tree(BOB), "main", "mystery-novel.txt"), "who wrote this");
    const { report, table } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({ unknown_entry: 1 });
    expect(table).toContain(`problem unknown_entry\tcenter/#${sha("mystery-novel.txt").slice(0, 12)}`);
    expect(table).not.toContain("mystery");
  });

  it("drops the locks and older session records the old code leaves in a sessions folder, " +
     "and refuses one outside their grammar", () => {
    const f = fresh();
    const sessions = join(f.tree(ALICE), ".agent-sessions", "novel");
    // The fixture holds owner-control.lock, request-continuation.lock, a request lock and a
    // 32-hex session record there, as every used center does: ready.
    expect(readdirSync(sessions)).toEqual(expect.arrayContaining([
      "owner-control.lock", `${sha("thread 1").slice(0, 32)}.json`]));
    expect(census(f.copy, TIERS).ready).toBe(true);
    writeFileSync(join(sessions, `${sha("thread 2").slice(0, 31)}.json`), "{}");
    const { report, table } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({ unknown_entry: 1 });
    expect(table).toContain("problem unknown_entry\tsessions/#");
  });

  it("refuses a store stopped uncleanly (its log not merged), and one that fails its check", () => {
    const f = fresh();
    unmergedStore(join(f.tree(ALICE), "novel", ".authoring.db"));
    writeFileSync(join(f.tree(BOB), "main", "rules.db"),
                  Buffer.concat([Buffer.from("SQLite format 3\0", "latin1"), Buffer.alloc(200, 7)]));
    const { report } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({ integrity: 1, unclean_store: 1 });
  });

  it("reports a store kept whole by one total, never the names its tables were given", () => {
    const f = fresh();
    makeStore(join(f.tree(ALICE), "knowledge.db"), "CREATE TABLE my_secret_plot (x);",
              { my_secret_plot: [[1], [2]] });
    const { report, table } = census(f.copy, TIERS);
    expect(report["stores"]).toMatchObject({ "root/knowledge.db/* archive": 2 });
    expect(JSON.stringify(report) + table).not.toContain("my_secret_plot");
  });

  it("refuses an account folder that is a link, never following it", () => {
    const f = fresh();
    const real = f.account(ALICE);
    renameSync(real, join(f.work, "elsewhere"));
    symlinkSync(join(f.work, "elsewhere"), real);
    const { report, ready } = census(f.copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toEqual({ unknown_entry: 1 });
  });

  it("refuses a shared store's table no rule names: the whole backup is not ready", () => {
    const f = fresh();
    makeStore(join(f.work, "data", "_data", ".broker", "plans.db"), "CREATE TABLE coupons (x)");
    const { report, ready, table } = census(f.copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toEqual({ unknown_table: 1 });
    expect(table).toContain("fleet\tproblem unknown_table\tshared/.broker/plans.db/coupons");
  });
});

describe("the backup's own gate", () => {
  it("refuses an image whose hash is not its pin, an unpublished snapshot and an unmounted image", () => {
    const f = fresh();
    appendFileSync(join(f.work, "300002.img"), "changed");
    const db = new DatabaseSync(join(f.work, "data", "_data", ".broker", "state",
                                     "owner-identities.db"));
    db.prepare("UPDATE owner_volumes SET work = 'snapshot' WHERE principal = ?").run(ALICE);
    db.close();
    const copy = { ...f.copy, volume: (uid: number) => uid === 300_003
      ? { problem: "is mounted writable" } : f.copy.volume(uid) };
    const { report, ready } = census(copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toEqual({ image_mismatch: 1, not_mounted: 1, pending_work: 1 });
  });
});

describe("the fleet's rows", () => {
  const identities = (f: Fixture) =>
    new DatabaseSync(join(f.work, "data", "_data", ".broker", "state", "owner-identities.db"));

  it("refuses a pinned image no owner_volumes row binds: never left unread", () => {
    const f = fresh();
    const db = identities(f);
    db.prepare("DELETE FROM owner_volumes WHERE principal = ?").run(BOB);
    db.close();
    const { report, ready, table } = census(f.copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toEqual({ pin_mismatch: 1 });
    expect(table).toContain(`${keyOf(BOB)}\tproblem pin_mismatch\tvolume/300002`);
  });

  it("counts an identity with no volume row and no pin, and refuses none for it", () => {
    const f = fresh();
    const db = identities(f);
    db.prepare("INSERT INTO owner_identities VALUES (?, ?)").run("user_nobox", 300_009);
    db.prepare("INSERT INTO owner_admission VALUES (?, 'admitted', '[]')").run(sha("user_nobox"));
    db.close();
    const { report } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({});
    expect((report["owners"] as { no_volume: number }).no_volume).toBe(1);
  });

  it("counts and refuses an admission, plan or volume row that is no identity's", () => {
    for (const [what, sql] of [
      ["admission", "INSERT INTO owner_admission VALUES ('" + sha("user_x") + "', 'admitted', '[]')"],
      ["volumes", "INSERT INTO owner_volumes VALUES ('user_x', 300010, 1, NULL, NULL, NULL, 0, " +
                  "NULL, NULL)"],
    ] as const) {
      const f = fresh();
      const db = identities(f);
      db.exec(sql);
      db.close();
      const { report, ready } = census(f.copy, TIERS);
      expect(ready, what).toBe(false);
      expect(report["problems"], what).toEqual({ orphan_row: 1 });
      expect((report["owners"] as { orphan_rows: Record<string, number> }).orphan_rows[what])
        .toBe(1);
    }
    const f = fresh();
    const plans = new DatabaseSync(join(f.work, "data", "_data", ".broker", "plans.db"));
    plans.exec("INSERT INTO plans VALUES ('user_x', 'paid', 1, NULL, 'cus_x', NULL, NULL)");
    plans.close();
    const { report } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({ orphan_row: 1 });
    expect((report["owners"] as { orphan_rows: { plans: number } }).orphan_rows.plans).toBe(1);
  });

  it("refuses an owner the old stack never admitted (no admission row), counted", () => {
    const f = fresh();
    const db = identities(f);
    db.prepare("DELETE FROM owner_admission WHERE digest = ?").run(sha(BOB));
    db.close();
    const { report, table } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({ never_admitted: 1 });
    expect((report["owners"] as { admission: Record<string, number> }).admission)
      .toEqual({ absent: 1, admitted: 13, erased: 1 });
    expect(table).toContain(`${keyOf(BOB)}\tadmission\tabsent`);
  });
});

describe("an erasure the owner asked for", () => {
  const jobs = (f: Fixture, sql: string, ...values: string[]) => {
    const db = new DatabaseSync(join(f.work, "data", "_data", ".broker", "state",
                                     "owner-identities.db"));
    db.prepare(sql).run(...values);
    db.close();
  };

  it("withholds an owner whose deletion job has not finished: counted, the run not stopped", () => {
    const f = fresh();
    const { report, ready, table } = census(f.copy, TIERS);
    expect(ready).toBe(true);
    expect(report["owners"]).toMatchObject({ identities: 15, imported: 3, erasure_pending: 11 });
    expect(table).toContain(`${keyOf(DORA)}\terasure_pending\ttrue`);
    // Nothing of hers is read: her files are in no count.
    expect(table).toContain(`${keyOf(DORA)}\tfiles\t0`);
    expect(table).not.toContain(`${keyOf(DORA)}\tentries`);
  });

  it("withholds every owner any erasure source names, each source alone", () => {
    const f = fresh();
    const { report, ready, table } = census(f.copy, TIERS);
    expect(ready).toBe(true);
    for (const [source, principal] of Object.entries(ERASING)) {
      expect(table, source).toContain(`${keyOf(principal)}\terasure_pending\ttrue`);
      expect(table, source).toContain(`${keyOf(principal)}\tfiles\t0`);
    }
    expect(table).toContain(`${keyOf(ERASED)}\terased\ttrue`);
    expect(table).toContain(`${keyOf(ALICE)}\terasure_pending\tfalse`);
    expect(table).toContain(`${keyOf(BOB)}\terasure_pending\tfalse`);
    // An owner is no tombstone: only those no identity holds are counted as one.
    expect(report["tombstones"]).toEqual({ by_principal: 1, digest_only: 2 });
  });

  it("holds an owner in the fixture for every erasure source the importer reads", () => {
    const held = new Set([...Object.keys(ERASING).filter((k) => k !== "center_deleted_principals"),
                          "deletion_jobs", "owner_admission"]);
    expect([...held].sort()).toEqual([...ERASURE_SOURCES].sort());
  });

  it("stops the run on an image whose tombstones cannot be read whole", () => {
    const f = fresh();
    writeFileSync(join(f.tree(BOB), "main", ".tinyassets.db"),
                  Buffer.concat([Buffer.from("SQLite format 3\0", "latin1"), Buffer.alloc(200, 7)]));
    const { report, ready, table } = census(f.copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toMatchObject({ unreadable: 1 });
    expect(table).toContain("fleet\tproblem unreadable\terasure/volume/300002");
  });

  it("stops the run on a disaster report's unfinished erasures, and on no other", () => {
    for (const [doc, ready] of [[{ pending_erasures: ["a/b"], erasure_failures: [] }, false],
                                [{ pending_erasures: [], erasure_failures: ["c"] }, false],
                                [{ pending_erasures: [], erasure_failures: [] }, true]] as const) {
      const f = fresh();
      writeFileSync(join(f.work, "data", "_data", ".disaster-restore.json"), JSON.stringify(doc));
      const { report } = census(f.copy, TIERS);
      expect(report["problems"], JSON.stringify(doc))
        .toEqual(ready ? {} : { unfinished_erasure: 1 });
    }
  });

  it("withholds one whose job finished but whose admission does not yet read erased", () => {
    const f = fresh();
    jobs(f, "DELETE FROM deletion_jobs");
    const [id, record] = deletionJob(BOB, ["withdrawal", "volume", "snapshots", "custody",
                                           "billing", "credentials", "identity", "receipt"]);
    jobs(f, "INSERT INTO deletion_jobs VALUES (?, ?)", id, record);
    expect(census(f.copy, TIERS).report["owners"])
      .toMatchObject({ imported: 3, erasure_pending: 11 });
  });

  it("stops the run on a job whose owner is no identity", () => {
    const f = fresh();
    const [id, record] = deletionJob("user_unknown", []);
    jobs(f, "INSERT INTO deletion_jobs VALUES (?, ?)", id, record);
    const { report, ready } = census(f.copy, TIERS);
    expect(ready).toBe(false);
    expect(report["problems"]).toEqual({ orphan_row: 1 });
  });

  it("refuses anything an unfinished account deletion left in .deleting or .account-deletions",
     () => {
    // Absent is fine (the fixture), and so is empty.
    const quiet = fresh();
    for (const folder of [".deleting", ".account-deletions"]) {
      mkdirSync(join(quiet.work, "data", "_data", folder));
      mkdirSync(join(quiet.tree(ALICE), folder));
    }
    expect(census(quiet.copy, TIERS).ready).toBe(true);
    for (const folder of [".deleting", ".account-deletions"]) {
      // In the shared layer: the whole backup is not ready.
      const shared = fresh();
      mkdirSync(join(shared.work, "data", "_data", folder, "x"), { recursive: true });
      const fleet = census(shared.copy, TIERS);
      expect([fleet.ready, fleet.report["problems"]], folder)
        .toEqual([false, { unfinished_erasure: 1 }]);
      expect(fleet.table).toContain(`fleet\tproblem unfinished_erasure\tshared/${folder}`);
      // In an owner's image: that owner is not ready.
      const owner = fresh();
      mkdirSync(join(owner.tree(BOB), folder));
      writeFileSync(join(owner.tree(BOB), folder, "receipt.json"), "{}");
      const one = census(owner.copy, TIERS);
      expect(one.report["problems"], folder).toEqual({ unfinished_erasure: 1 });
      expect(one.table).toContain(`${keyOf(BOB)}\tproblem unfinished_erasure\troot/${folder}`);
    }
  });
});

describe("each owner's fit", () => {
  it("refuses a file past the platform's largest (FILE_DEFAULTS.max_file_bytes), read by size", () => {
    const f = fresh();
    sparse(join(f.tree(BOB), "main", "workspace", "huge.bin"), FILE_DEFAULTS.max_file_bytes + 1);
    const { report, table } = census(f.copy, TIERS);
    expect(report["problems"]).toEqual({ over_file_bytes: 1, over_storage: 1 });
    expect(table).toContain(`${keyOf(BOB)}\tproblem over_file_bytes`);
  });

  it("counts entries as the files plane does, folders and the starter's seed included", () => {
    const f = fresh();
    const { table } = census(f.copy, TIERS);
    const entries = Number(new RegExp(`${keyOf(BOB)}\\tentries\\t(\\d+)`).exec(table)![1]);
    // The starter's files and their folders, read from its package here independently.
    const starter = new Set<string>();
    for (const file of (JSON.parse(readFileSync(join(PLATFORM, "starter", "starter.json"),
                                                "utf8")) as { files: { path: string }[] }).files) {
      const parts = file.path.split("/");
      for (let i = 3; i <= parts.length; i += 1) {
        starter.add(parts.slice(0, i).join("/"));
      }
    }
    // Bob's: imported/, main/, MEMORY.md, workspace/, workspace/a.txt and README.md.
    expect(entries).toBe(6 + starter.size);
  });

  it("refuses a file past what a tar member holds, ignored or refused, read by size", () => {
    const f = fresh();
    sparse(join(f.tree(BOB), "main", "workspace", "node_modules"), TAR_MEMBER_MAX + 1);
    expect(census(f.copy, TIERS).report["problems"]).toEqual({ over_tar_member: 1 });
  });

  it("refuses an owner whose entries pass max_entries, folders counted", () => {
    const f = fresh();
    const many = join(f.tree(BOB), "main", "workspace", "many");
    mkdirSync(many);
    for (let i = 0; i < FILE_DEFAULTS.max_entries; i += 1) {
      writeFileSync(join(many, String(i)), "");
    }
    expect(census(f.copy, TIERS).report["problems"]).toEqual({ over_entries: 1 });
  });
});

describe("the walk of an owner's files", () => {
  it("keeps for refused.tar a name whose stored path would pass 4,096 bytes, and all below it", () => {
    const f = fresh();
    const source = join(f.work, "deep");
    mkdirSync(join(source, "x".repeat(15)), { recursive: true });
    writeFileSync(join(source, "x".repeat(15), "inner.txt"), "kept");
    writeFileSync(join(source, "ok.txt"), "carried");
    // A stored path of 4,085 bytes: its child "ok.txt" fits, a name of 15 does not.
    const path = `${IMPORTED}${`/${"a".repeat(200)}`.repeat(20)}/${"b".repeat(45)}`;
    expect(Buffer.byteLength(path)).toBe(4085);
    const tree = newTree();
    walkUser(Buffer.from(source), path, Buffer.from("m"), tree, ignoredBy(DEFAULT_IGNORES));
    expect(tree.carried.map((item) => [item.kind, item.path.slice(path.length)]))
      .toEqual([["dir", ""], ["file", "/ok.txt"]]);
    expect(tree.refused.map((refusal) => refusal.member.toString())).toEqual([`m/${"x".repeat(15)}`]);
  });
});
