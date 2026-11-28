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
    // The meter's log: each sandbox started, resumed, paused or killed, as this
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
  // 2: each launched job's vendor hosts, so a network that replaces its sandbox's whole
  // (a start that opens a lease, a re-apply) keeps them; and a re-apply kept while its
  // sandbox is paused, applied as it resumes.
  Object.freeze([
    "ALTER TABLE launched ADD COLUMN hosts TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE sandboxes ADD COLUMN network_due TEXT",
  ]),
  // 3: the slots this object owes the platform's line, each kept until the line takes
  // it back (a renewal's held slot, its sandbox running again or gone).
  Object.freeze([
    "CREATE TABLE IF NOT EXISTS owed (sandbox_id TEXT PRIMARY KEY)",
  ]),
  // 4 (`untangle-2026-11/control-last-good.md`): the control record's last good copy
  // (`contract/src/control.ts` `SqlKeeper`), which serves a start's numbers and scratch
  // retention when KV can't be read; the retention kept apart for that goes.
  Object.freeze([
    "CREATE TABLE control_kept (only INTEGER PRIMARY KEY CHECK (only = 1), text TEXT NOT NULL, " +
      "read_at INTEGER NOT NULL)",
    "DROP TABLE known_retention",
  ]),
  // 5 (`untangle-2026-11/foreign-runner-reap.md`): when the look kills a sandbox a job lives
  // in on a runner this Worker does not speak, which no renewal can carry on: the end of the
  // window it was found in.
  Object.freeze([
    "ALTER TABLE sandboxes ADD COLUMN kill_at REAL",
  ]),
  // 6 (`untangle-2026-11/look-resume-loop.md`): whether its runner answered since the sandbox
  // last came running, so the look resumes a paused one for its lease only on that word.
  Object.freeze([
    "ALTER TABLE sandboxes ADD COLUMN heard INTEGER NOT NULL DEFAULT 0",
  ]),
  // 7 (`untangle-2026-11/start-faults.md`): whether its runner's `prepare` ended since the
  // sandbox last came running or took another runner, so a start that finds it running and
  // unprepared (a fault on the way) prepares it before it launches anything. Unknown for the
  // sandboxes already kept, so each is prepared again once.
  Object.freeze([
    "ALTER TABLE sandboxes ADD COLUMN prepared INTEGER NOT NULL DEFAULT 0",
  ]),
  // 8 (`untangle-2026-11/template-move.md`): a template move's state, kept before each step it
  // takes, so one cut short by a fault or the object's end leaves nothing unnamed: the sandbox it
  // made while it is not yet the authority's (`move_to`, killed if the move goes no further), its
  // failures and when it is tried again; and each sandbox a move left that is to be killed
  // (`retiring`), until E2B says it is gone.
  Object.freeze([
    "ALTER TABLE sandboxes ADD COLUMN move_to TEXT",
    "ALTER TABLE sandboxes ADD COLUMN move_tries INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sandboxes ADD COLUMN move_after REAL",
    "CREATE TABLE retiring (sandbox_id TEXT PRIMARY KEY)",
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
