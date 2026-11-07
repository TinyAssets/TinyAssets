/**
 * A small backup copy in the old stack's shape (owner-box layout, `import_research.md` §1),
 * made fresh in a temporary folder: the manifest and its archive, the shared layer's
 * router and broker stores (their tables as the old stack's DDL makes them), three owners'
 * images, and each image's tree where the census reads it mounted.
 *
 * - alice (`user_alice`, uid 300001): admitted, paid, with broker stores (policy, a
 *   collection, mail, a credential vault), two centers (`novel`, and `old`, retired), root
 *   stores, harness sessions, a root wiki, and in `novel`: user files, a two-chunk file, an
 *   executable, a link, a FIFO, an ignored `node_modules`, a name that is not UTF-8 and one
 *   past 255 bytes, a center store with archived and dropped tables, platform entries.
 * - bob (`user_bob`, uid 300002): suspended for billing, free, one center, no broker stores.
 * - the founder (`user_founder`, uid 300003): admitted, paid; the exclusion list's.
 * - tombstones: `user_gone` by principal, and two by digest alone.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, truncateSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { gzipSync } from "node:zlib";

import { CHUNK_BYTES } from "../../contract/src/limits";

import type { Copy } from "../survey";
import { manifestOf } from "../backup";

export const ARCHIVE = "tinyassets-data-2026-11-07T03-00-00Z.tar.gz";
export const ALICE = "user_alice";
export const BOB = "user_bob";
export const FOUNDER = "user_founder";
export const UIDS = Object.freeze({ [ALICE]: 300_001, [BOB]: 300_002, [FOUNDER]: 300_003 });
/** The founder's tier numbers (ADR-034 decision 8), as the control record holds them. */
export const TIERS = Object.freeze({ free: { storage_bytes: 25_000_000_000 },
                                     paid: { storage_bytes: 250_000_000_000 } });

export const sha = (text: string | Uint8Array) => createHash("sha256").update(text).digest("hex");
export const keyOf = (principal: string) => sha(principal).slice(0, 16);

/** A store made at `path` from DDL and rows. */
export function makeStore(path: string, sql: string,
                          rows: Readonly<Record<string, readonly unknown[][]>> = {}): void {
  mkdirSync(dirname(path), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec(sql);
  for (const [table, values] of Object.entries(rows)) {
    for (const row of values) {
      db.prepare(`INSERT INTO ${table} VALUES (${row.map(() => "?").join(", ")})`)
        .run(...(row as (string | number | null)[]));
    }
  }
  db.close();
}

function file(path: string, text: string | Buffer, mode = 0o644): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, text);
  chmodSync(path, mode);
}

export interface Fixture {
  readonly work: string;
  readonly copy: Copy;
  /** An owner's mounted tree. */
  readonly tree: (principal: string) => string;
  readonly account: (principal: string) => string;
}

const POLICY = { entries: [], egress: { mode: "open", destinations: [] },
                 spend: { daily: null, monthly: null, transaction: null, ask_above: null,
                          source_ceilings: {} } };

/** The two-chunk file's bytes: a chunk and ten bytes more. */
export function bigBytes(): Buffer {
  const bytes = Buffer.alloc(CHUNK_BYTES + 10);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = (i * 7) % 251;
  }
  return bytes;
}

/**
 * The names in alice's workspace that are not UTF-8: a file's, and a folder's (a surrogate
 * as CESU-8 writes it) holding a file of its own.
 */
export const BAD_NAME = Buffer.from([0x62, 0xff, 0xfe, 0x2e, 0x74, 0x78, 0x74]);
export const BAD_DIR = Buffer.from([0x64, 0xed, 0xa0, 0x80]);

export function fixture(): Fixture {
  const work = mkdtempSync(join(tmpdir(), "ta-import-"));
  const shared = join(work, "data", "_data");
  const tree = (principal: string) => join(work, "mnt", String(UIDS[principal as keyof typeof UIDS]));
  const account = (principal: string) => join(shared, ".broker", "accounts", sha(principal));

  // The shared layer.
  const images = Object.fromEntries(Object.entries(UIDS).map(([principal, uid]) =>
    [principal, Buffer.from(`an XFS image of ${uid}, ${principal}`)]));
  const snapshot = (principal: string) => sha(`snapshot ${principal}`).slice(0, 32);
  makeStore(join(shared, ".broker/state/owner-identities.db"),
            "CREATE TABLE owner_identities (principal TEXT PRIMARY KEY NOT NULL, " +
            "machine_id INTEGER NOT NULL UNIQUE CHECK(machine_id BETWEEN 300001 AND 399999));" +
            "CREATE TABLE owner_admission (digest TEXT PRIMARY KEY NOT NULL, " +
            "state TEXT NOT NULL CHECK(state IN ('admitted','suspended','erased')), " +
            "reasons TEXT NOT NULL);" +
            "CREATE TABLE owner_volumes (principal TEXT PRIMARY KEY NOT NULL, " +
            "uid INTEGER NOT NULL UNIQUE, volume_generation INTEGER NOT NULL, snapshot_id TEXT, " +
            "snapshot_sha256 TEXT, work TEXT, local INTEGER NOT NULL DEFAULT 1, asleep_at REAL, " +
            "rewind_generation INTEGER);" +
            "CREATE TABLE owner_box_secrets (principal TEXT PRIMARY KEY NOT NULL, " +
            "secret TEXT NOT NULL);" +
            "CREATE TABLE routing_sequence (revision INTEGER NOT NULL);", {
              owner_identities: Object.entries(UIDS).map(([p, uid]) => [p, uid]),
              owner_admission: [[sha(ALICE), "admitted", "[]"], [sha(BOB), "suspended", '["billing"]'],
                                [sha(FOUNDER), "admitted", "[]"], [sha("user_gone2"), "erased", "[]"]],
              owner_volumes: Object.entries(UIDS).map(([p, uid]) =>
                [p, uid, 3, snapshot(p), sha(images[p]!), null, 0, 1.5, null]),
              owner_box_secrets: Object.keys(UIDS).map((p) => [p, `secret-${p}`]),
              routing_sequence: [[7]],
            });
  makeStore(join(shared, ".broker/plans.db"),
            "CREATE TABLE plans (owner TEXT PRIMARY KEY, tier TEXT NOT NULL, " +
            "event_created REAL NOT NULL, ends_at REAL, customer_id TEXT UNIQUE, " +
            "subscription_id TEXT UNIQUE, attempt TEXT);" +
            "CREATE TABLE checkout_settlements (session_id TEXT PRIMARY KEY, " +
            "attempt_id TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, attempt TEXT NOT NULL);", {
              plans: [[ALICE, "paid", 1, null, "cus_alice", "sub_alice", null],
                      [BOB, "free", 1, null, null, null, null],
                      [FOUNDER, "paid", 1, null, "cus_founder", "sub_founder", null]],
            });
  makeStore(join(shared, ".broker/credential-custody.db"),
            "CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY);",
            { deleted_principals: [[sha("user_gone3")], [sha("user_gone")]] });
  makeStore(join(shared, ".router-deleted-owners.sqlite3"),
            "CREATE TABLE deleted_owners (owner TEXT PRIMARY KEY, receipt TEXT);",
            { deleted_owners: [["user_gone", "{}"]] });
  makeStore(join(shared, ".api-keys.sqlite3"),
            "CREATE TABLE api_keys (key_id TEXT PRIMARY KEY, owner TEXT NOT NULL, " +
            "name TEXT NOT NULL, secret_sha256 TEXT NOT NULL, created_at REAL NOT NULL, " +
            "last_used_at REAL, revoked_at REAL);", {
              api_keys: [["k1", ALICE, "laptop", sha("s1"), 1, null, null],
                         ["k2", BOB, "old", sha("s2"), 1, null, 2]],
            });
  makeStore(join(shared, ".hook-owners.sqlite3"),
            "CREATE TABLE hook_owners (token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL);",
            { hook_owners: [[sha("hook"), ALICE]] });
  makeStore(join(shared, ".tinyassets.db"),
            "CREATE TABLE user_accounts (id TEXT PRIMARY KEY);" +
            "CREATE TABLE user_sessions (id TEXT PRIMARY KEY);",
            { user_accounts: [[ALICE], [BOB]] });
  file(join(shared, ".layout.json"), '{"layout": 2, "state": "stable", "box_layout": "owner-box"}');
  file(join(shared, ".connect", "client_id"), "client");
  file(join(shared, ".broker", "browser-vault", "keys"), "secret");

  // Alice's broker stores.
  makeStore(join(account(ALICE), "policy.db"),
            "CREATE TABLE policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1), " +
            "generation INTEGER NOT NULL, digest TEXT NOT NULL, document TEXT NOT NULL);" +
            "CREATE TABLE receipts (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, " +
            "kind TEXT NOT NULL, owner TEXT NOT NULL, session_id TEXT, old_digest TEXT, " +
            "new_digest TEXT, request_digest TEXT, generation INTEGER, rule TEXT, " +
            "decision TEXT, outcome TEXT);" +
            "CREATE TABLE spend_reservations (request_id TEXT PRIMARY KEY, " +
            "request_digest TEXT NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, " +
            "amount INTEGER NOT NULL);" +
            "CREATE TABLE once_approvals (token_hash TEXT PRIMARY KEY, " +
            "request_digest TEXT NOT NULL, session_id TEXT NOT NULL, generation INTEGER NOT NULL, " +
            "expires_at REAL NOT NULL, consumed_at REAL);" +
            "CREATE TABLE proposals (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, " +
            "document TEXT NOT NULL, at REAL NOT NULL, applied_at REAL);", {
              policy: [[1, 2, sha("p"), JSON.stringify({ ...POLICY, egress: { mode: "ask",
                                                                         destinations: [] } })]],
              receipts: [[1, 1.0, "set", ALICE, null, null, sha("p"), null, 2, null, "ok", "ok"]],
              once_approvals: [[sha("t"), sha("r"), "s", 2, 9.0, null]],
            });
  makeStore(join(account(ALICE), "conversations.db"),
            "CREATE TABLE message_bodies (message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, " +
            "body TEXT NOT NULL CHECK(length(body) <= 16384), created_at REAL NOT NULL);" +
            "CREATE TABLE collection_rows (collection TEXT NOT NULL, row_id TEXT NOT NULL, " +
            "revision INTEGER NOT NULL, seq INTEGER NOT NULL, etag TEXT NOT NULL, " +
            "writer TEXT NOT NULL, private INTEGER NOT NULL CHECK(private IN (0,1)), " +
            "body TEXT NOT NULL CHECK(length(body) <= 65536), updated_at REAL NOT NULL, " +
            "creator TEXT, PRIMARY KEY(collection, row_id), UNIQUE(collection, seq));" +
            "CREATE TABLE collection_seqs (collection TEXT PRIMARY KEY, seq INTEGER NOT NULL);", {
              message_bodies: [["m1", "room", "hello", 1.0]],
              collection_rows: [["board", "b2", 1, 2, "e2", "agent", 0, '{"t":"second"}', 2.0, "agent"],
                                ["board", "b1", 3, 1, "e1", ALICE, 1, '{"t":"first"}', 1.0, ALICE]],
              collection_seqs: [["board", 2]],
            });
  makeStore(join(account(ALICE), "message_ledger.db"),
            "CREATE TABLE ledger_messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
            "message_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, body_sha256 TEXT NOT NULL);",
            { ledger_messages: [[1, "m1", ALICE, sha("hello")]] });
  file(join(account(ALICE), "credentials", ".credential-vault.json"), '{"token": "sk-secret"}');
  makeStore(join(account(FOUNDER), "policy.db"),
            "CREATE TABLE policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1), " +
            "generation INTEGER NOT NULL, digest TEXT NOT NULL, document TEXT NOT NULL);",
            { policy: [[1, 1, sha("f"), JSON.stringify(POLICY)]] });

  // The images, their pins, and the trees they hold.
  for (const [principal, uid] of Object.entries(UIDS)) {
    writeFileSync(join(work, `${uid}.img`), images[principal]!);
    mkdirSync(join(work, "mnt", String(uid)), { recursive: true });
  }
  const archive = gzipSync(Buffer.from("the shared layer's archive"));
  writeFileSync(join(work, ARCHIVE), archive);
  writeFileSync(join(work, "manifest.json"), JSON.stringify({
    version: 1, archive: ARCHIVE, sha256: sha(archive),
    snapshots: Object.entries(UIDS).map(([p, uid]) => ({ uid, snapshot_id: snapshot(p),
                                                         sha256: sha(images[p]!) })),
  }));

  // Alice's image.
  const a = tree(ALICE);
  makeStore(join(a, ".centers.db"),
            "CREATE TABLE centers (center TEXT PRIMARY KEY, retired INTEGER NOT NULL DEFAULT 0);",
            { centers: [["novel", 0], ["old", 1]] });
  makeStore(join(a, ".tinyassets.db"),
            "CREATE TABLE branch_definitions (branch_def_id TEXT PRIMARY KEY, author TEXT, " +
            "body TEXT);" +
            "CREATE TABLE universes (id TEXT PRIMARY KEY);", {
              branch_definitions: [["bd1", ALICE, "my workflow"]],
              universes: [["novel"]],
            });
  makeStore(join(a, ".runs.db"),
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, owner_user_id TEXT);" +
            "CREATE TABLE branch_schedules (schedule_id TEXT PRIMARY KEY, cron TEXT);", {
              runs: [["r1", ALICE], ["r2", ALICE]],
              branch_schedules: [["s1", "0 9 * * *"]],
            });
  makeStore(join(a, ".agent-sessions", "novel", "history.db"),
            "CREATE TABLE changes (id INTEGER PRIMARY KEY, what TEXT);",
            { changes: [[1, "wrote chapter one"]] });
  makeStore(join(a, ".agent-sessions", "novel", "steering.db"),
            "CREATE TABLE steer (id INTEGER PRIMARY KEY);");
  file(join(a, ".agent-home", ".claude", "credentials.json"), '{"token": "secret"}');
  file(join(a, ".owner-boxd", "boxhost.db"), "not read");
  file(join(a, "wiki", "home.md"), "# Alice's wiki\n");
  const novel = join(a, "novel");
  file(join(novel, "AGENTS.md"), "# Novelist\n");
  file(join(novel, "notes", "ch1.md"), "It was a dark night.\n");
  file(join(novel, "workspace", "big.bin"), bigBytes());
  file(join(novel, "workspace", "run.sh"), "#!/bin/sh\necho hi\n", 0o755);
  symlinkSync("../notes/ch1.md", join(novel, "workspace", "chapter"));
  file(join(novel, "workspace", "node_modules", "dep", "index.js"), "module.exports = 1;\n");
  file(join(novel, "workspace", "node_modules", "dep", "package.json"), "{}\n");
  execFileSync("mkfifo", [join(novel, "workspace", "pipe")]);
  writeFileSync(Buffer.concat([Buffer.from(join(novel, "workspace") + "/"), BAD_NAME]), "odd");
  const badDir = Buffer.concat([Buffer.from(join(novel, "workspace") + "/"), BAD_DIR]);
  mkdirSync(badDir);
  writeFileSync(Buffer.concat([badDir, Buffer.from("/inside.md")]), "kept in the tar");
  makeStore(join(novel, ".conversation_memory.db"),
            "CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, role TEXT, text TEXT);" +
            "CREATE TABLE conversation_backfill (id INTEGER PRIMARY KEY);", {
              conversation_turns: [[1, "user", "write chapter one"], [2, "agent", "done"]],
              conversation_backfill: [[1]],
            });
  file(join(novel, ".conversation_memory.db-shm"), "");
  file(join(novel, "status.json"), "{}");
  file(join(novel, ".credential-vault.json"), '{"token": "secret"}');
  file(join(a, "old", "soul.md"), "retired soul\n");

  // Bob's image.
  const b = tree(BOB);
  makeStore(join(b, ".centers.db"),
            "CREATE TABLE centers (center TEXT PRIMARY KEY, retired INTEGER NOT NULL DEFAULT 0);",
            { centers: [["main", 0]] });
  file(join(b, "main", "MEMORY.md"), "bob remembers\n");
  file(join(b, "main", "workspace", "a.txt"), "a\n");

  // The founder's image.
  const f = tree(FOUNDER);
  makeStore(join(f, ".centers.db"),
            "CREATE TABLE centers (center TEXT PRIMARY KEY, retired INTEGER NOT NULL DEFAULT 0);",
            { centers: [["home", 0]] });
  file(join(f, "home", "AGENTS.md"), "# The founder's agent\n");
  file(join(f, "home", "notes", "n.md"), "private\n");

  const copy: Copy = {
    work,
    manifest: manifestOf(readFileSync(join(work, "manifest.json"), "utf8")),
    volume: (uid) => ({ root: join(work, "mnt", String(uid)) }),
  };
  return { work, copy, tree, account };
}

/** A sparse file of `size` bytes: past the files plane's largest file, with no disk taken. */
export function sparse(path: string, size: number): void {
  writeFileSync(path, "");
  truncateSync(path, size);
}
