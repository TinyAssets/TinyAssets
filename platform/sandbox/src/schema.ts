/**
 * The sandbox Worker's two objects' migrations, applied on wake (`migrate`, the contract's
 * mechanism, the owner's too) and held by their locks (`migrations/<class>.json`, `guards/migrations.test.ts`).
 * Migration 1 is each object's schema as it was frozen before the Worker's first deploy;
 * a change from here on is a new migration after it, never an edit.
 */
import type { Migration } from "../../contract/src/migrate";

/** `SandboxControl`'s, one object an owner. */
export const CONTROL_MIGRATIONS: readonly Migration[] = Object.freeze([
  Object.freeze([
    "CREATE TABLE IF NOT EXISTS sandboxes (authority TEXT PRIMARY KEY, " +
      "sandbox_id TEXT NOT NULL, lease_id TEXT, end_at REAL NOT NULL, " +
      "renewing INTEGER NOT NULL DEFAULT 0, releasing INTEGER NOT NULL " +
      "DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0, idle_since REAL, retry_at REAL, " +
      "tries INTEGER NOT NULL DEFAULT 0, runner TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS launched (run TEXT PRIMARY KEY, " +
      "authority TEXT NOT NULL, lease_id TEXT NOT NULL)",
    // The meter's log (slice P): each sandbox started, resumed, paused or killed, as this
    // object did it or saw it, for the owner's object to read after its cursor (`meter`).
    "CREATE TABLE IF NOT EXISTS meter_log (seq INTEGER PRIMARY KEY " +
      "AUTOINCREMENT, authority TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN " +
      "('start', 'resume', 'pause', 'kill')), at INTEGER NOT NULL)",
    // The control record's sandbox numbers as last read, and the looks failed in a row.
    "CREATE TABLE IF NOT EXISTS known (only INTEGER PRIMARY KEY CHECK " +
      "(only = 1), runtime_s INTEGER NOT NULL, concurrency INTEGER NOT NULL, " +
      "window_s INTEGER NOT NULL, failures INTEGER NOT NULL DEFAULT 0)",
    // The control record's scratch retention as last read (`days` null: the record names none).
    "CREATE TABLE IF NOT EXISTS known_retention (only INTEGER PRIMARY KEY " +
      "CHECK (only = 1), days REAL)",
  ]),
  // 2 (E2): each launched job's vendor hosts, so a network that replaces its sandbox's whole
  // (a start that opens a lease, a re-apply) keeps them; and a re-apply kept while its
  // sandbox is paused, applied as it resumes.
  Object.freeze([
    "ALTER TABLE launched ADD COLUMN hosts TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE sandboxes ADD COLUMN network_due TEXT",
  ]),
]);

/** `SandboxSlots`'s, the one line for the platform. */
export const SLOTS_MIGRATIONS: readonly Migration[] = Object.freeze([
  Object.freeze([
    "CREATE TABLE IF NOT EXISTS waiting (owner TEXT PRIMARY KEY, " +
      "seq INTEGER NOT NULL UNIQUE, misses INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS line (only INTEGER PRIMARY KEY CHECK " +
      "(only = 1), next_seq INTEGER NOT NULL, ceiling_s INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS held (sandbox_id TEXT PRIMARY KEY)",
  ]),
]);
