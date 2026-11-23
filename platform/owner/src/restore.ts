/**
 * Restore (d3 §G; `docs/decisions/untangle-2026-11/r5r.md`): the whole owner, from one finished
 * generation (`backup.ts`, `export.ts`), into the owner's own object, only while that object is
 * `pending` and empty. The platform's alone: its doors take the reserved caller
 * (`PLATFORM_CALLER`), which only the operator channel binds for it (`doors/src/operator.ts`, ops
 * `restore.*`).
 *
 * **What it reads.** The generation's root, its base parts and its log segments, through the
 * files Worker's manifest reads (`Bucket`, `FILES`), the same reads an export makes. The
 * files Worker opens each under the owner's data key; nothing here holds a key or puts to R2,
 * and it deletes only the chunks its `sweep` step finds no row names. A restore from the
 * off-account copy puts the copy's bytes back at the same R2 keys first
 * (the seal binds the key, `r4.md` item 6), so this reads them unchanged: only where
 * `{namespace, gen}` comes from differs.
 *
 * **The object takes the generation's namespace and wrapped key** (`file_space`), so every
 * chunk a content names is found where it is, under the same key: no chunk moves.
 *
 * **The steps**, each a unit or a bounded run of units, each resuming from the object's own
 * record (`owner_restore`), so a crash anywhere repeats nothing that committed and a second
 * run goes on or refuses, never loads a row twice:
 * 1. `restoreBegin {namespace, gen}`: refused unless the object is `pending` and empty
 *    (`emptiness`), and unless the root opens for the namespace and part 0's header names
 *    format `tinyassets-export` version 3, this object's owner, this generation and a schema
 *    this code brings forward (`unforwardable`: from 9 on). Then, in one unit, the namespace
 *    and key adopted and the record made, with this object's schema and the generation's.
 *    A migration that lands under a restore fails it at its next piece, named: the object's
 *    tables were migrated half loaded, so it is wiped and begun again, from the same
 *    generation.
 * 2. **index**: each base part's span of tables (the export's order), one unit a part.
 * 3. **log**: each log segment's lines staged in `restore_rows`, one unit a segment, the last
 *    line per key winning and a `gone` line's key held as gone: the stream read as the owner
 *    at E (`export.ts`, "Read last line per key wins"). The base is fuzzy, so a base row the
 *    log names never goes to its table; staging the log alone keeps the object's rows at one
 *    copy. Each staged line's bytes as its segment holds it are counted (`restore_rows.n`),
 *    and their sum's peak at a segment's end (`staged`, `peak`): once it passes `LOG_BYTES`
 *    the restore fails, named (a recorded ceiling, `r5r.md`), so the staged log is at most
 *    that and one segment. The capture counts the same peak as it writes the segments
 *    (`changelog.ts`), which is how the object judges a generation restorable before a pin.
 * 4. **apply**: table by table in `loadOrder`'s order, a base part's rows of the table but those
 *    the log names, a unit a part, then the log's rows of it, a slice a unit. The schema's
 *    triggers rebuild every counter the export leaves out, and each exported column a
 *    trigger keeps as a reference count (`refs`) starts at 0 for them to rebuild. A column the
 *    export leaves out is left to its default, or, where the schema requires one (an inbound
 *    route, an egress token: credentials the backup never held), made afresh and held by no
 *    one.
 * 5. **check**: the same rows read back from their tables, column for column, and every
 *    table's count, equal to the generation's; a difference fails the restore, recorded, and
 *    the owner is never admitted. Then **chunks**: every chunk a content names read back
 *    through `FILES` with its hash verified. A read that fails is thrown, so the next step
 *    tries it again.
 * 6. **names**, then **sweep**: the chunks in the namespace that no restored row names (those
 *    put after E, which nothing else would ever collect) deleted, while the restore holds the
 *    owner, so no upload makes a row for one meanwhile (`putChunks` of a key already there
 *    only verifies it). First the owner's admission at E is held to one a restore admits, or
 *    the restore fails before anything is deleted. **names** adds the bucket name of every
 *    `file_chunks` row, in any state (an upload's held one, a garbage one in its grace that
 *    may revive, one a kept generation names), to one Bloom filter (`names-filter.ts`),
 *    `NAMES_ROWS` rows a unit by hash, their names from `FILES.chunkNames`; **sweep** lists the
 *    namespace's chunks a page at a time from the last name listed (`FILES.listChunks`) and
 *    deletes those the filter says no row names (`FILES.deleteListed`). A filter has no false
 *    negative, so no chunk a row names is deleted; a false positive keeps an unnamed chunk
 *    until erasure, at most about 1% of them. The keep rule is the files plane's own
 *    (`files.ts`: every object in the bucket has a row first), so it never reads which
 *    generations are kept.
 * 7. **clear**: the staged rows deleted, a slice a unit, then the filter.
 * 8. **done**, one unit: the fence (every outside client and API key revoked, every MCP grant
 *    fenced, every inbound binding disabled at a new revision, every connection marked for a
 *    reconnect: their secrets and routes were never in the backup), the allocators the export
 *    leaves out moved past everything the rows name (each its module's `restored`), the
 *    generation recorded as the object's newest finished one, and the owner's admission as it
 *    stood at E (`held`).
 *
 * **Into a live owner** (`untangle-2026-11/r5w.md`) the object is first brought to `pending`
 * and empty, and its record begins two steps earlier:
 * - `restorePin {namespace, gen}`: one of the object's finished, restorable generations pinned
 *   (never pruned while a restore holds the owner, `Backups`' `pinned`), or one of another
 *   namespace that names this owner, which its own then holds; the owner held (the record's
 *   `pinned` step: every door but the restore's refused, every run and stream ended, no lane
 *   run), and then a fresh generation of its present state written (`fresh`), begun after
 *   the hold, so nothing the owner wrote is lost unwritten (Fact 13). Asked again until it is
 *   written, each call carrying the generation on for at most `RESTORE_PASS_MS`; its answer
 *   then names the words that confirm the wipe, `wipe-<owner>-<fresh>`.
 * - `restoreWipe {confirm}`: in one unit, every table the schema made dropped and the schema
 *   migrated afresh (`remigrate`), as a new object's start makes it, and the record written
 *   again at `wiped`, the pin with it; then the instance reset, so nothing it held in memory
 *   outlives the wipe. Nothing in R2 is touched: the files' chunks and every generation stay
 *   in the namespace, which the record names, and the pinned one is begun as a lost object's
 *   is. A failed restore is wiped the same way, to be restored again.
 * - `restoreRelease`: a pin not yet wiped given up, the owner as it was.
 * A wiped object takes a restore only of the generation it was pinned for, or of the one its
 * pin wrote (the undo). One no pin held (a lost object's failed restore) takes any generation
 * of this owner's, as the lost object did: a log that stages past `LOG_BYTES` fails only at
 * its `log` step, and the one kept before it is then restored.
 *
 * **A restore from another of the owner's namespaces** (a live owner's from one its own holds,
 * `r8-lost-object.md`): the pin has its own hold the pinned one, the wipe has the pinned one
 * hold its own first, a begin into a wiped object has the one it adopts hold the record's, and
 * the restore's last step deletes every hold of the adopted one by one it holds
 * (`rootNamespace`), so at every step the namespace the object or its record names reaches
 * every other, and once done no cycle of holds is left.
 *
 * **Another namespace of the owner** (`restoreHold {namespace, gen}`, `r8-erased-orphans.md`):
 * one whose generation names this owner but which this object will not adopt (it is live, so
 * it takes no restore) is held by its own namespace in the bucket (`HOLDS`), made if none, so
 * the owner's erasure marks and deletes it too and a restore of the whole bucket begins none it
 * holds. An owner never keeps a namespace its erasure would miss.
 *
 * From the pin until done, or once a restore failed, no lane runs (`restoring`, the schedule's
 * halt), every door but the restore's refuses (`refuseRestoring`), and so do the platform's
 * admission and the cutover's import: `restoring`, a refusal for the moment.
 *
 * **What the owner loses** (the kit's plan says so): its sessions, every agent's grant, its API
 * keys, its model connections' secrets, its inbound URLs, and anything written after E.
 */
import { EXPORT_FORMAT, EXPORT_VERSION, exportHeader } from "../../contract/src/export-header";
import { HEX16, HEX32, randomHex } from "../../contract/src/ids";
import { DELETE_BATCH, LOG_BYTES } from "../../contract/src/limits";
import type { ChunkStore, ManifestRoot } from "../../contract/src/files";
import { remigrate } from "../../contract/src/migrate";
import type {
  RestoreAnswer, RestoreBeginRequest, RestoreHeld, RestoreHold, RestoreNames,
  RestoreNamesRequest,
  RestorePinRequest, RestoreSource, RestoreWipeRequest,
} from "../../contract/src/owner";
import { Refusal } from "../../contract/src/refusal";

import { clock } from "./clock";
import { exported, NOT_EXPORTED } from "./export";
import type { OwnerStorage } from "./gate";
import type { Backups } from "./backup";
import type { Collections } from "./collections";
import type { Door, DoorUnits, ObjectReads } from "./door";
import type { Egress } from "./egress";
import type { OwnerEnv } from "./env";
import type { Files } from "./files";
import type { Inbound } from "./inbound";
import type { Keys } from "./keys";
import type { Launcher } from "./launch";
import type { Relay } from "./relay";
import { addNames, emptyFilter, filterOf, mayHold } from "./names-filter";
import { MIGRATIONS, unforwardable } from "./schema";

/**
 * What a restore reaches in the bucket (`FILES`): a generation's manifest reads, and its
 * sweep's list of the chunks; it deletes those no row names through `Files.sweep`.
 */
type Bucket = Pick<ChunkStore, "getManifestRoot" | "getManifestPart" | "getManifestSegment"
  | "listChunks">;

/**
 * One exported table as a restore loads it: its name, the columns a trigger keeps as a
 * reference count (`refs`: 0 at insert, rebuilt by the rows that name it, then checked), and
 * the columns held back until the check passes (`held`).
 */
interface Load {
  readonly table: string;
  readonly refs?: readonly string[];
  readonly held?: readonly string[];
}

/**
 * Every exported table in its load order (`restore.test.ts` holds it to the schema's
 * triggers). A table whose reference counts another's triggers keep loads before that table,
 * so its rows are there to count on (contents before what names them, chunks before their
 * lists, messages and readers before recipients, agents before messages, clients before agents
 * and cards). A table whose other trigger-kept columns are sums over rows that may since have
 * been pruned (`model_spend` over reservations, a run's requests, tokens and cost over its
 * egress) loads after the rows that add to it, its value as the stream holds it: their
 * triggers find no row to add to, or a row its own load then overwrites. The owner's admission
 * keeps `pending` until the check passes (`held`). A function, as `exported` is, so no module
 * holds the table.
 */
export function loadOrder(): readonly Load[] {
  return Object.freeze([
    { table: "object_owner" },
    { table: "owner_admission", held: ["state", "reasons"] },
    { table: "owner_policy" },
    { table: "policy_proposals" },
    { table: "policy_receipts" },
    { table: "outside_clients", refs: ["agents", "held"] },
    { table: "agent_cards" },
    { table: "connections" },
    { table: "engine_signins" },
    { table: "owner_notices" },
    { table: "file_chunks", refs: ["refs"] },
    { table: "file_contents", refs: ["refs"] },
    { table: "file_content_chunks" },
    { table: "file_entries" },
    { table: "file_history" },
    { table: "starter_receipts" },
    { table: "starter_state" },
    { table: "collection_rows" },
    { table: "collection_history" },
    { table: "collection_erasures" },
    { table: "collection_seqs" },
    { table: "collection_clock" },
    { table: "outside_agents", refs: ["unacked_to_owner"] },
    { table: "native_agents", refs: ["unacked_to_owner"] },
    { table: "rooms" },
    { table: "room_members" },
    { table: "ledger_messages", refs: ["unacked"] },
    { table: "message_bodies" },
    { table: "ledger_readers", refs: ["unacked"] },
    { table: "ledger_recipients" },
    { table: "archive_segments" },
    { table: "archive_purges" },
    { table: "ledger_ids" },
    { table: "egress_reservations" },
    { table: "model_spend" },
    { table: "runs" },
    { table: "run_inputs" },
    { table: "run_schedules" },
    { table: "conversations" },
    { table: "owner_keys" },
    { table: "inbound_bindings" },
  ].map((load) => Object.freeze(load)));
}

/**
 * The tables a fresh object's migrations seed with their one row: a restore's load updates
 * that row, and a restore takes an object whose other exported tables are empty.
 */
export const SEEDED: readonly string[] = Object.freeze(["owner_admission", "owner_policy",
                                                        "collection_clock"]);

/** A restore's record (`owner_restore`), as its row holds it. */
type Held = {
  readonly ns: string;
  readonly gen: string;
  readonly step: Step;
  readonly at: number;
  readonly after: string | null;
  readonly held: string | null;
  readonly spans: string | null;
  readonly failed: string | null;
  readonly fresh: string | null;
  /** When the hold began, in seconds: the pin, or a lost object's begin. */
  readonly since: number;
  /** The bytes of the log's lines staged (`restore_rows.n`), and their most at a segment's end. */
  readonly staged: number;
  readonly peak: number;
  /**
   * The object's schema when the restore was begun, and its generation's (null for a record
   * not yet begun, or one from before migration 16 that no step carries on).
   */
  readonly schema: number | null;
  readonly source: number | null;
};

type Step = "pinned" | "wiped" | "index" | "log" | "apply" | "check" | "chunks" | "names"
  | "sweep" | "clear" | "done" | "failed";

/** How long one `restoreStep` call takes new work: its answer is well inside the kit's wait. */
const RESTORE_PASS_MS = 30_000;
/** Logged rows one apply, check or clear unit takes (a base part's are one unit's). */
const SLICE_ROWS = 500;
/** Chunks one check unit reads back. */
const SLICE_CHUNKS = 64;
/** A live owner's admissions: the ones a pin holds, and a restore admits at E (`finish`). */
const ADMITS: readonly string[] = Object.freeze(["admitted", "suspended"]);

/** The restore's record, or null when none was begun. A read. */
function record(sql: Pick<SqlStorage, "exec">): Held | null {
  return sql.exec<Held>("SELECT ns, gen, step, at, after, held, spans, failed, fresh, since, " +
                          "staged, peak, schema, source FROM owner_restore").toArray()[0] ?? null;
}

/**
 * What every door but the restore's answers while a restore holds the owner, the platform's
 * admission and the cutover's import too (`refuseRestoring`): from the pin the object holds
 * an owner about to be wiped, then a half-loaded one, which only the restore's own doors take,
 * so nothing admits, reads or writes it before the check passes.
 */
export const RESTORING = "this account is held while it is restored, or while its restore " +
  "waits for an operator's check";

/**
 * Whether a restore holds the owner: pinned and not yet done, or failed. While one does, no
 * lane runs (the schedule's halt, `owner-do.ts`) and every door but the restore's refuses
 * (`refuseRestoring`). A read.
 */
export function restoring(storage: OwnerStorage): boolean {
  return holdingStep(storage) !== null;
}

/** The step of the restore that holds the owner (`restoring`), or null when none does. A read. */
export function holdingStep(storage: OwnerStorage): Step | null {
  const held = record(storage.sql);
  return held === null || held.step === "done" ? null : held.step;
}

/**
 * The generations a restore that holds the owner reads its roots from: its `gen`, and the
 * one its pin wrote (`fresh`), the undo; none when no restore holds it. A rotation's rewrap
 * counts them left (`files.ts` `dataKeyRewrap`): a wiped object names neither. A read.
 */
export function heldGenerations(storage: OwnerStorage): string[] {
  if (holdingStep(storage) === null) {
    return [];
  }
  const { gen, fresh } = record(storage.sql)!;
  return fresh === null || fresh === gen ? [gen] : [gen, fresh];
}

/**
 * The namespace the restore that holds the owner reads (its record's), or null when none holds
 * it. A wipe empties the object, so from then until the restore's begin this is the one place
 * the owner's namespace is named, and the owner's erasure takes it from here (`owner-do.ts`).
 * A read.
 */
export function restoreNamespace(storage: OwnerStorage): string | null {
  return holdingStep(storage) === null ? null : record(storage.sql)!.ns;
}

/**
 * Refused, for the moment (`restoring`, a transient code: a sign-in's redirect answers it
 * `temporarily_unavailable`), while a restore holds the owner. A read. When the hold began
 * is `restore.source`'s to answer (`since`): no door shows a refusal's facts.
 */
export function refuseRestoring(storage: OwnerStorage): void {
  if (restoring(storage)) {
    throw new Refusal("restoring", RESTORING);
  }
}

/**
 * The generation a restore holds while it holds the owner, which no prune deletes
 * (`Backups`' `pinned`); null otherwise. A read.
 */
export function pinned(storage: OwnerStorage): string | null {
  return restoring(storage) ? record(storage.sql)!.gen : null;
}

/**
 * Why this object can't be restored into, or null: an object not `pending`, or one holding
 * anything but what its migrations seeded (`SEEDED`), a namespace, or a restore already, but
 * the record of the wipe that emptied it. In a unit.
 */
function emptiness(o: RestoreDoorNeeds): string | null {
  const { state } = o.admission.row();
  if (state !== "pending") {
    return `the owner is ${state}: a restore takes only a pending owner`;
  }
  const begun = record(o.storage.sql);
  if (begun !== null && begun.step !== "wiped") {
    return "a restore of this owner was begun already";
  }
  const held = o.files.storedNamespace() !== null ? ["a namespace"]
    : Object.keys(exported()).filter((table) => o.storage.sql.exec<{ n: number }>(
      `SELECT count(*) AS n FROM ${table}`).one().n > (SEEDED.includes(table) ? 1 : 0));
  return held.length > 0
    ? `the owner's object is not empty (${held.join(", ")}): a restore takes only an empty one`
    : null;
}

/** A refusal of the restore, terminal: what is asked can't be done to this object. */
const refused = (why: string) => new Refusal("refused", why);

/**
 * Refused unless a wiped object a pin held (`held` at `wiped`, with `fresh`) is begun from the
 * generation it was pinned for, in its record's namespace, or from the one its pin wrote, in the
 * namespace the object named then; any other object passes, a wiped one no pin held (a lost
 * object's failed restore) for any generation of this owner's (`ownGeneration`).
 */
function wipedFor(held: Held | null, ns: string, gen: string): void {
  if (held?.step === "wiped" && held.fresh !== null && !(ns === held.ns && gen === held.gen)
      && gen !== held.fresh) {
    throw refused(`this object was wiped to restore generation ${held.gen} of ${held.ns}, or ` +
                  `${held.fresh}, the one its pin wrote`);
  }
}

/**
 * Whether generation `gen` of `ns` is one a pin takes on this object, and whether it is of the
 * object's own namespace: a live owner's (admitted or suspended), and, in its own namespace,
 * one of its finished generations a restore can load; another namespace's the pin holds to this
 * owner by its header (`ownGeneration`). In a unit.
 */
function pinnable(o: RestoreDoorNeeds, ns: string, gen: string): boolean {
  const { state } = o.admission.row();
  if (!ADMITS.includes(state)) {
    throw refused(`the owner is ${state}: a pin holds a live owner, and a pending one is ` +
                  "restored into with none");
  }
  const own = ns === o.files.storedNamespace();
  if (own && !o.backups.restorable(gen, clock())) {
    throw refused(`generation ${gen} is not one of the owner's finished generations a ` +
                  "restore can load: restore.source names them");
  }
  return own;
}

/**
 * The restore that holds the owner, for a pin of generation `gen` of `ns`: null when none holds
 * it; refused unless it is that pin's, its undo's, or a failed one no pin held (a lost object's,
 * wiped for any generation). In a unit.
 */
function pinHeld(o: RestoreDoorNeeds, ns: string, gen: string): Held | null {
  const now = record(o.storage.sql);
  if (now === null || now.step === "done") {
    return null;
  }
  if (!(ns === now.ns && gen === now.gen) && gen !== now.fresh
      && (now.step !== "failed" || now.fresh !== null)) {
    throw refused(`a restore of this owner from generation ${now.gen} holds it: release it, ` +
                  "or restore that one");
  }
  return now;
}

/**
 * The hold a restore's record makes, as it stands (`RestoreHold`): whether the wipe may go on
 * (no fresh generation is due, or it is finished), and the words that confirm it while one
 * is due: at `pinned` once the fresh generation is written, or at `failed`, naming the
 * generation the wipe leaves as the undo (the one a failed restore was from, should no pin
 * have written one). In a unit.
 */
function holdOf(o: RestoreDoorNeeds, held: Held): RestoreHold {
  const written = held.step !== "pinned"
    || (held.fresh !== null && o.backups.finished(held.fresh));
  const due = held.step === "failed" || (held.step === "pinned" && written);
  return { step: held.step, namespace: held.ns, gen: held.gen, fresh: held.fresh, written,
           confirm: due ? `wipe-${o.owner()}-${held.fresh ?? held.gen}` : null };
}

/**
 * Whether the wipe `door` asks is due on the record `held`: false for one wiped or begun, which
 * answers as it stands; refused with none pinned, before the present state is written, for
 * words not the pin's, and once its generation or its undo can no longer be restored. In a
 * unit.
 */
function wipeDue(o: RestoreDoorNeeds, door: Door<RestoreWipeRequest>, held: Held | null): boolean {
  const { r } = door.check();
  if (held === null || held.step === "done") {
    throw refused("no generation of this owner is pinned: restore.pin first");
  }
  if (held.step !== "pinned" && held.step !== "failed") {
    return false;
  }
  const { confirm } = holdOf(o, held);
  if (confirm === null) {
    throw refused("the owner's present state is not yet written: ask restore.pin again " +
                  "until it is");
  }
  if (r.confirm !== confirm) {
    throw new Refusal("invalid", "a wipe is confirmed by wipe-<owner>-<gen>, as " +
                                 "restore.pin's answer names it");
  }
  // A live owner is wiped only for a generation, and an undo, a restore can still load (a
  // migration since may refuse their schema): else the pin is released, the owner kept. Another
  // namespace's generation was held to its schema at the pin; its begin holds it again.
  const loads = held.ns === o.files.storedNamespace() ? [held.gen, held.fresh!] : [held.fresh!];
  const unloadable = held.step === "pinned"
    ? loads.find((gen) => !o.backups.restorable(gen, clock())) : undefined;
  if (unloadable !== undefined) {
    throw refused(`generation ${unloadable} can no longer be restored: release the pin, ` +
                  "and pin one restore.source names restorable");
  }
  return true;
}

/** Every table of the owner's schema: the export's two lists, which hold every one. */
const schemaTables = (): string[] => [...Object.keys(exported()), ...Object.keys(NOT_EXPORTED)];

/** A line of a part or a segment, as JSON. */
type Line = { readonly t: string } & Readonly<Record<string, unknown>>;

function lines(bytes: Uint8Array): Line[] {
  return sized(bytes).map(({ line }) => line);
}

/** An object's lines, each with its bytes as the object holds it, its newline included. */
function sized(bytes: Uint8Array): { line: Line; n: number }[] {
  const encoder = new TextEncoder();
  return new TextDecoder().decode(bytes).trimEnd().split("\n")
    .map((text) => ({ line: JSON.parse(text) as Line, n: encoder.encode(text).byteLength + 1 }));
}

/** The generation's root, read through the source: refused when there is none. */
async function rootOf(source: Bucket, ns: string, gen: string): Promise<ManifestRoot> {
  const root = await source.getManifestRoot(ns, gen);
  if (root === null) {
    throw refused(`the namespace holds no finished generation ${gen}`);
  }
  return root;
}

/** The `index`th object of the generation (its parts, then its segments), read whole. */
function objectAt(source: Bucket, ns: string, root: ManifestRoot,
                  index: number): Promise<Uint8Array> {
  const part = root.parts[index];
  if (part !== undefined) {
    return source.getManifestPart(ns, root.key, root.gen, index, part.sha256);
  }
  const segment = root.segments[index - root.parts.length]!;
  return source.getManifestSegment(ns, root.key, root.gen, segment.a, segment.b,
                                   segment.sha256);
}

/** The key a staged row is held under: its key's values, as JSON (`replay`'s). */
const keyOf = (table: string, row: Readonly<Record<string, unknown>>): string =>
  JSON.stringify(exported()[table]!.key.map((column) => row[column]));

/** A value as the stream carries it, as SQLite takes it: bytes from their base64. */
function cell(value: unknown): SqlStorageValue {
  if (typeof value === "object" && value !== null && "$base64" in value) {
    return new Uint8Array(Buffer.from((value as { $base64: string }).$base64, "base64"))
      .buffer as ArrayBuffer;
  }
  return value as SqlStorageValue;
}

/** A column's value as the stream carries it (`export.ts`'s `value`). */
function streamed(value: unknown): unknown {
  return value instanceof ArrayBuffer ? { $base64: Buffer.from(value).toString("base64") } : value;
}

/**
 * The columns the export leaves out that the schema requires (`NOT NULL` and no default): each
 * made afresh as 32 random bytes in hex, held by no one (an inbound route, an egress token). A
 * read of the schema, so a column a later migration adds is covered by the same rule.
 */
function required(sql: Pick<SqlStorage, "exec">, table: string): string[] {
  const omitted = exported()[table]!.omitted ?? {};
  return sql.exec<{ name: string; notnull: number; dflt_value: SqlStorageValue }>(
    `PRAGMA table_info(${table})`).toArray()
    .filter((column) => Object.hasOwn(omitted, column.name) && column.notnull === 1
            && column.dflt_value === null)
    .map((column) => column.name);
}

/**
 * A line's table and row (a `row` line's row, or null for a `gone` line), or null for none:
 * every line a restore reads passes here. A line of any schema a restore begins
 * (`unforwardable`) is the same line at this one, since no migration since changes an exported
 * table (`FORWARD`); the first that does brings each line forward here.
 */
function rowOf(line: Line): { table: string; k: string; row: Record<string, unknown> | null }
    | null {
  if (line.t === "header" || line.t === "chunk") {
    return null;  // part 0's header, held at begin; a chunk's bytes are in the bucket
  }
  const table = line["table"];
  if (typeof table !== "string" || !Object.hasOwn(exported(), table)
      || (line.t !== "row" && line.t !== "gone")) {
    throw refused(`the generation holds a line this schema has no table for (${line.t})`);
  }
  if (line.t === "gone") {
    return { table, k: JSON.stringify(line["key"]), row: null };
  }
  const row = line["row"] as Record<string, unknown>;
  return { table, k: keyOf(table, row), row };
}

/**
 * In a unit: a log segment's lines staged, the last line per key winning, a `gone` line's key
 * held as gone (a null row), so the base row it replaces is never loaded; each with its bytes
 * (`n`). What the staged lines' bytes grew by.
 */
function stageLog(sql: Pick<SqlStorage, "exec">, segment: readonly { line: Line; n: number }[]):
    number {
  let grown = 0;
  for (const { line, n } of segment) {
    const held = rowOf(line);
    if (held !== null) {
      grown += n - (sql.exec<{ n: number }>("SELECT n FROM restore_rows WHERE tbl = ? AND k = ?",
                                            held.table, held.k).toArray()[0]?.n ?? 0);
      sql.exec("INSERT INTO restore_rows (tbl, k, row, n) VALUES (?, ?, ?, ?) ON CONFLICT " +
                 "(tbl, k) DO UPDATE SET row = excluded.row, n = excluded.n", held.table, held.k,
               held.row === null ? null : JSON.stringify(held.row), n);
    }
  }
  return grown;
}

/**
 * A base part's span of tables, in the export's order: the index of the first and the last
 * table its rows are of, or null when it holds none (only chunks' lines).
 */
function spanOf(part: readonly Line[]): [number, number] | null {
  const names = Object.keys(exported());
  const at = part.map(rowOf).filter((held) => held !== null).map((held) => names.indexOf(held.table));
  return at.length === 0 ? null : [at[0]!, at.at(-1)!];
}

/** The base parts that hold rows of `table`, by index, from the record's spans. */
function partsOf(held: Held, table: string): number[] {
  const at = Object.keys(exported()).indexOf(table);
  return (JSON.parse(held.spans ?? "[]") as ([number, number] | null)[]).flatMap((span, part) =>
    span !== null && span[0] <= at && at <= span[1] ? [part] : []);
}

/** A row of `table` as the generation holds it at E: the base's, unless the log names its key. */
function baseRows(sql: Pick<SqlStorage, "exec">, table: string, part: readonly Line[]):
    Record<string, unknown>[] {
  return part.map(rowOf).filter((held) => held !== null && held.table === table
    && sql.exec("SELECT 1 FROM restore_rows WHERE tbl = ? AND k = ?", table, held.k).toArray()
      .length === 0).map((held) => held!.row!);
}

/** The log's rows of `table` after `after` (a key), at most `SLICE_ROWS`, and their keys. */
function loggedRows(sql: Pick<SqlStorage, "exec">, table: string, after: string):
    { k: string; row: Record<string, unknown> }[] {
  return sql.exec<{ k: string; row: string }>(
    "SELECT k, row FROM restore_rows WHERE tbl = ? AND k > ? AND row IS NOT NULL ORDER BY k " +
      "LIMIT ?", table, after, SLICE_ROWS).toArray()
    .map(({ k, row }) => ({ k, row: JSON.parse(row) as Record<string, unknown> }));
}

/**
 * In a unit: `rows` of `load` into its table, each reference count at 0 for the triggers to
 * rebuild, each column the schema requires and the export leaves out made afresh, each held
 * column at its seeded row's value, kept in the record until the check passes.
 */
function insert(sql: Pick<SqlStorage, "exec">, load: Load,
                rows: readonly Record<string, unknown>[]): void {
  const { columns, key } = exported()[load.table]!;
  const held = load.held ?? [];
  const refs = load.refs ?? [];
  const loaded = columns.filter((column) => !held.includes(column));
  const fresh = required(sql, load.table);
  const updated = loaded.filter((column) => !key.includes(column) && !refs.includes(column));
  // A seeded row, or one a trigger of a table loaded before made, takes the stream's values.
  const statement = `INSERT INTO ${load.table} (${[...loaded, ...fresh, ...held].join(", ")}) ` +
    `VALUES (${[...loaded, ...fresh].map(() => "?").concat(held.map((column) =>
      `(SELECT ${column} FROM ${load.table})`)).join(", ")}) ON CONFLICT DO ${
      updated.length === 0 ? "NOTHING"
        : `UPDATE SET ${updated.map((column) => `${column} = excluded.${column}`).join(", ")}`}`;
  for (const row of rows) {
    if (held.length > 0) {
      const kept = JSON.parse(record(sql)!.held ?? "{}") as Record<string, unknown>;
      kept[load.table] = Object.fromEntries(held.map((column) => [column, row[column]]));
      sql.exec("UPDATE owner_restore SET held = ?", JSON.stringify(kept));
    }
    sql.exec(statement, ...loaded.map((column) => refs.includes(column) ? 0 : cell(row[column])),
             ...fresh.map(() => randomHex(32)));
  }
}

/**
 * In a unit: whether each of `rows` of `load` reads back from its table as the generation
 * holds it, column for column but the held ones; the first that does not, by its key.
 */
function differs(sql: Pick<SqlStorage, "exec">, load: Load,
                 rows: readonly Record<string, unknown>[]): string | null {
  const { columns, key } = exported()[load.table]!;
  const compared = columns.filter((column) => !(load.held ?? []).includes(column));
  const as = (row: Record<string, unknown>) =>
    JSON.stringify(compared.map((column) => streamed(row[column])));
  for (const want of rows) {
    const got = sql.exec<Record<string, SqlStorageValue>>(
      `SELECT ${compared.join(", ")} FROM ${load.table} WHERE ${
        key.map((column) => `${column} IS ?`).join(" AND ")}`,
      ...key.map((column) => cell(want[column]))).toArray()[0];
    if (got === undefined || as(got) !== as(want)) {
      return `${load.table}'s row ${JSON.stringify(key.map((column) => want[column]))} is ` +
        "not the generation's";
    }
  }
  return null;
}

/**
 * Where an apply or a check is in its table: at a base part (`p`, its index among the parts
 * that hold the table's rows), or past the base at the log's rows after a key (`k`); and, for
 * a check, how many rows of the table it has held to the generation so far (`n`).
 */
type Within = { readonly p: number; readonly n: number }
  | { readonly k: string; readonly n: number };

const START = "{\"p\":0,\"n\":0}";

/**
 * In the last unit: the fence, each by its own module. Every outside client is revoked with its
 * API key and its agent, as the owner's own revoke does, and every MCP grant before now is
 * fenced (`Grants.revokeAll`, `Keys.revoked`, `Ledger.removeKeyAgents`); every inbound binding is disabled at a new revision, so
 * no route the directory still holds for it reaches it (`Inbound.disableAll`); every connection
 * is marked failed, for its owner to connect again (`Connections.secretsLost`). Runs live at E
 * have no lease here, so the runs lane settles them `unknown` once the owner is admitted
 * (`Runs.lost`). The doors delete the API keys from the directory (`restoreStep`'s answer).
 */
function fence(o: RestoreDoorNeeds, now: number): void {
  for (const client of o.grants.revokeAll(now)) {
    o.keys.revoked(client, now);
    o.ledger.removeKeyAgents(client, now);
  }
  o.inbound.disableAll(now);
  o.connections.secretsLost();
}

/** The restore's answer for its record as it stands. In a unit. */
function answer(o: RestoreDoorNeeds, held: Held): RestoreAnswer {
  if (held.step === "failed") {
    return { step: "failed", done: false, failed: held.failed! };
  }
  if (held.step !== "done") {
    return { step: held.step, done: false };
  }
  const { state, reasons } = o.admission.row();
  const rows = Object.keys(exported()).reduce((sum, table) => sum + o.storage.sql.exec<{
    n: number }>(`SELECT count(*) AS n FROM ${table}`).one().n, 0);
  return { step: "done", done: true, gen: held.gen, rows, chunks: held.at, keys: o.keys.ids(),
           state, reasons };
}

/** The next step of a restore after `step`, in order. */
const NEXT: Readonly<Partial<Record<Step, Step>>> = Object.freeze({
  index: "log", log: "apply", apply: "check", check: "chunks", chunks: "names", names: "sweep",
  sweep: "clear", clear: "done" });

/**
 * Chunk rows one `names` unit adds to the filter: `NAMES_CALLS` `FILES.chunkNames` calls of
 * `DELETE_BATCH` each, so the filter's row (`FILTER_BYTES()`, about 1.45 MB) is written again
 * once per 10,000 rows, at most 122 times for `CHUNK_ROWS_MAX`, and a unit holds the filter
 * and its rows' hashes and names, under 4 MB.
 */
const NAMES_CALLS = 10;
const NAMES_ROWS = NAMES_CALLS * DELETE_BATCH;

/** In a unit: the restore's names filter as its row holds it; loud when there is none. */
function heldFilter(sql: Pick<SqlStorage, "exec">): Uint8Array {
  const row = sql.exec<{ bits: ArrayBuffer }>("SELECT bits FROM restore_filter").toArray()[0];
  if (row === undefined) {
    throw new Error("a restore's names filter is missing");
  }
  return filterOf(row.bits);
}

/**
 * One bounded piece of the restore's current step, from its record, the record moved on in
 * the same unit, and only while no other call moved it first (`still`), so two calls at once
 * never do one piece twice; whether the restore goes on.
 */
async function piece(o: RestoreDoorNeeds, door: Door<undefined>, source: Bucket):
    Promise<boolean> {
  const sql = o.storage.sql;
  const held = await o.door(() => {
    door.check();
    return record(sql);
  });
  if (held === null || held.step === "pinned" || held.step === "wiped") {
    throw refused("no restore of this owner was begun");
  }
  /** In a unit: whether the record is still as `held` read it. */
  const still = () => {
    door.check();
    const now = record(sql);
    return now !== null && now.step === held.step && now.at === held.at
      && now.after === held.after;
  };
  /** In a unit: the record moved on from where `held` read it. */
  const move = (step: Step, at: number, after: string | null, failed: string | null = null) => {
    sql.exec("UPDATE owner_restore SET step = ?, at = ?, after = ?, failed = ?", step, at, after,
             failed);
  };
  // A migration landed under a restore under way: the wake migrated its half-loaded tables.
  if (Object.hasOwn(NEXT, held.step) && held.schema !== MIGRATIONS.length) {
    await o.door(() => {
      if (still()) {
        console.error("a migration landed under a restore; the owner is not admitted");
        move("failed", held.at, null, "a migration landed while it was under way: begun at " +
             `schema ${String(held.schema)}, this object's is ${MIGRATIONS.length}; wipe it and ` +
             "restore again, from the same generation");
      }
    });
    return true;
  }
  switch (held.step) {
    case "index":
    case "log": {
      // Each base part's span of tables, then each log segment staged: one unit an object.
      const root = await rootOf(source, held.ns, held.gen);
      const index = held.step === "index";
      const count = index ? root.parts.length : root.segments.length;
      if (held.at >= count) {
        await o.door(() => {
          if (still()) {
            move(NEXT[held.step]!, 0, held.step === "log" ? START : null);
          }
        });
        return true;
      }
      const read = sized(await objectAt(source, held.ns, root,
                                        index ? held.at : root.parts.length + held.at));
      await o.door(() => {
        if (!still()) {
          return;
        }
        if (index) {
          const spans = JSON.parse(held.spans ?? "[]") as unknown[];
          sql.exec("UPDATE owner_restore SET spans = ?", JSON.stringify(
            [...spans, spanOf(read.map(({ line }) => line))]));
        } else {
          const staged = held.staged + stageLog(sql, read);
          const peak = Math.max(held.peak, staged);
          sql.exec("UPDATE owner_restore SET staged = ?, peak = ?", staged, peak);
          if (peak > LOG_BYTES) {
            move("failed", held.at, null, `the generation's log stages past the ${LOG_BYTES} ` +
                 "bytes a restore takes: wipe it and restore the one kept before it, or the " +
                 "one its pin wrote");
            return;
          }
        }
        move(held.step, held.at + 1, null);
      });
      return true;
    }
    case "apply":
    case "check": {
      const load = loadOrder()[held.at];
      if (load === undefined) {
        await o.door(() => {
          if (still()) {
            move(NEXT[held.step]!, 0, held.step === "apply" ? START : null);
          }
        });
        return true;
      }
      const within = JSON.parse(held.after!) as Within;
      const parts = partsOf(held, load.table);
      // A base part's rows of the table, read before the unit that loads or checks them.
      const read = "p" in within && within.p < parts.length
        ? lines(await objectAt(source, held.ns, await rootOf(source, held.ns, held.gen),
                               parts[within.p]!)) : null;
      await o.door(() => {
        if (!still()) {
          return;
        }
        let rows: Record<string, unknown>[];
        let next: Within | null;
        if (read !== null) {
          rows = baseRows(sql, load.table, read);
          next = { p: (within as { p: number }).p + 1, n: within.n + rows.length };
        } else {
          const logged = loggedRows(sql, load.table, "k" in within ? within.k : "");
          rows = logged.map((each) => each.row);
          next = logged.length === SLICE_ROWS ? { k: logged.at(-1)!.k, n: within.n + rows.length }
            : null;
        }
        if (held.step === "apply") {
          insert(sql, load, rows);
          move("apply", next === null ? held.at + 1 : held.at, next === null ? START
            : JSON.stringify(next));
          return;
        }
        let difference = differs(sql, load, rows);
        const total = next?.n ?? within.n + rows.length;
        if (difference === null && next === null) {
          const stands = sql.exec<{ n: number }>(`SELECT count(*) AS n FROM ${load.table}`)
            .one().n;
          if (stands !== total) {
            difference = `${load.table} holds ${stands} rows where the generation has ${total}`;
          }
        }
        if (difference !== null) {
          console.error("a restore's check found a difference; the owner is not admitted");
          move("failed", held.at, held.after, difference);
          return;
        }
        move("check", next === null ? held.at + 1 : held.at, next === null ? START
          : JSON.stringify(next));
      });
      return true;
    }
    case "chunks": {
      const listed = await o.door(() => o.files.chunksPage(held.at, SLICE_CHUNKS));
      // Each read whole by the files Worker under the owner's key and held to its hash, no
      // byte of it coming back. A read gone wrong is thrown (the next step reads the slice
      // again); `missing` or `broken` is the bucket's answer, so the restore failed, named,
      // and its operator wipes it again for another generation or the pin's undo.
      const found = await o.files.check(listed.map((chunk) => chunk.sha256));
      const unread = found.filter((check) => check !== "stored");
      await o.door(() => {
        if (!still()) {
          return;
        }
        if (unread.length > 0) {
          console.error("a restore's chunks are not stored; the owner is not admitted");
          move("failed", held.at, null, `${unread.length} of the generation's chunks are not ` +
               `stored as their hash (${[...new Set(unread)].join(", ")})`);
          return;
        }
        move(listed.length < SLICE_CHUNKS ? NEXT.chunks! : "chunks", held.at + listed.length,
             null);
      });
      return true;
    }
    case "names": {
      // Read from the record before anything is deleted: a restore that cannot admit the
      // owner as it stood at E fails here, its chunks untouched, for its operator to wipe.
      const kept = JSON.parse(held.held ?? "{}") as Record<string, Record<string, unknown>>;
      const state = kept["owner_admission"]?.["state"];
      if (typeof state !== "string" || !ADMITS.includes(state)) {
        await o.door(() => {
          if (still()) {
            console.error("a restore's generation holds no live admission; nothing is swept");
            move("failed", held.at, held.after, `the generation holds the owner's admission ` +
                 `at E as ${String(state)}: a restore admits only an admitted or suspended one`);
          }
        });
        return true;
      }
      const page = await o.door(() => o.files.chunksAfter(held.after, NAMES_ROWS));
      const names = await o.files.names(page);
      await o.door(() => {
        if (!still()) {
          return;
        }
        // The first page begins the filter; each after it adds to the bits so far, so a page
        // asked again sets the bits it set.
        const filter = held.after === null ? emptyFilter() : heldFilter(sql);
        addNames(filter, names);
        sql.exec("INSERT INTO restore_filter (only, bits) VALUES (1, ?) ON CONFLICT (only) DO " +
                   "UPDATE SET bits = excluded.bits", filter.buffer as ArrayBuffer);
        const last = page.length < NAMES_ROWS;
        move(last ? NEXT.names! : "names", held.at, last ? null : page.at(-1)!);
      });
      return true;
    }
    case "sweep": {
      const listed = await source.listChunks(held.ns, held.after);
      const unnamed = await o.door(() => {
        if (!still()) {
          return null;
        }
        const filter = heldFilter(sql);
        return listed.names.filter((name) => !mayHold(filter, name));
      });
      if (unnamed === null) {
        return true;
      }
      if (unnamed.length > 0) {
        await o.files.sweep(held.ns, unnamed);
      }
      await o.door(() => {
        if (still()) {
          if (unnamed.length > 0) {
            console.log(`a restore swept ${unnamed.length} chunks no row names`);  // no names
          }
          move(listed.more ? "sweep" : NEXT.sweep!, held.at,
               listed.more ? listed.names.at(-1)! : null);
        }
      });
      return true;
    }
    case "clear": {
      // A sweep's delete another call of this instance still has in flight lands first, so
      // none lands after `done` (`Files.sweep`).
      await o.files.settled();
      const cleared = await o.door(() => {
        if (!still() || sql.exec("DELETE FROM restore_rows WHERE (tbl, k) IN (SELECT tbl, k " +
                                   "FROM restore_rows LIMIT ?)", SLICE_ROWS).rowsWritten
                          === SLICE_ROWS) {
          return false;
        }
        sql.exec("DELETE FROM restore_filter");
        return true;
      });
      if (cleared) {
        // No cycle of holds through the namespace adopted: one it holds may have held it.
        await o.files.rootNamespace(held.ns);
        const root = await rootOf(source, held.ns, held.gen);
        await o.door(() => {
          if (still()) {
            finish(o, held, root);
          }
        });
      }
      return true;
    }
    case "done":
    case "failed":
      return false;
  }
}

/**
 * In the last unit: the fence, the allocators past every value the rows name (each its
 * module's), the generation recorded as the object's newest, and the held columns as the
 * generation holds them: the owner's admission as it stood at E, which `names` held to one
 * a restore admits before anything was swept.
 */
function finish(o: RestoreDoorNeeds, held: Held, root: ManifestRoot): void {
  const sql = o.storage.sql;
  fence(o, clock());
  o.files.restored();
  o.collections.restored();
  o.ledger.restored();
  // Begun at this object's schema (`piece`'s check), so its generation's was recorded.
  o.backups.adopt(root, held.peak, held.source!);
  const kept = JSON.parse(held.held ?? "{}") as Record<string, Record<string, SqlStorageValue>>;
  for (const load of loadOrder().filter((each) => each.held !== undefined)) {
    const columns = load.held!;
    sql.exec(`UPDATE ${load.table} SET ${columns.map((column) => `${column} = ?`).join(", ")}`,
             ...columns.map((column) => kept[load.table]?.[column] ?? null));
  }
  sql.exec("UPDATE owner_restore SET step = 'done', after = NULL");
}

// -- THE DOORS: the `restore` rows' handlers (`owner/doors.json`) ----------------------------------

/**
 * What the `restore` doors reach: the generations (`Backups`) and the bucket they are read
 * from; every store a generation loads into (the files, collections and ledger's allocators);
 * what a pin ends (the runs, the sandbox's egress, the app sockets) and fences (the grants and
 * keys, the inbound bindings, the model connections' secrets); and the owner's admission.
 */
export interface RestoreDoorNeeds extends Pick<DoorUnits, "door" | "quiet" | "sync">,
                                          Pick<ObjectReads, "owner" | "storage" | "reset"> {
  readonly env: Pick<OwnerEnv, "FILES">;
  readonly admission: {
    row(): { readonly state: string; readonly reasons: readonly string[] };
  };
  readonly backups: Pick<Backups, "adopt" | "begin" | "finished" | "kept" | "manifests"
    | "restorable" | "run" | "writing">;
  readonly collections: Pick<Collections, "restored">;
  readonly connections: { secretsLost(): void };
  readonly egress: Pick<Egress, "end">;
  readonly files: Pick<Files, "adoptSpace" | "check" | "chunksAfter" | "chunksFrom" | "chunksPage"
    | "holdNamespace" | "holdUnder" | "names" | "restored" | "rootNamespace" | "settled"
    | "storedNamespace" | "sweep">;
  readonly grants: { revokeAll(now: number): string[] };
  readonly inbound: Pick<Inbound, "disableAll">;
  readonly keys: Pick<Keys, "ids" | "revoked">;
  readonly launcher: Pick<Launcher, "endAll">;
  readonly ledger: { restored(): void; removeKeyAgents(client: string, now: number): unknown };
  readonly relay: Pick<Relay, "closeAll">;
}

/** The namespace and generation a restore door names, or `invalid`. */
function generationAsked(door: Door<RestoreBeginRequest>): { ns: string; gen: string } {
  const { namespace: ns, gen } = door.check().r;
  if (typeof ns !== "string" || !HEX32.test(ns) || typeof gen !== "string" || !HEX16.test(gen)) {
    throw new Refusal("invalid", "a restore names a namespace (32 hex) and a generation " +
                                 "(16 hex)");
  }
  return { ns, gen };
}

/**
 * Generation `gen` of `ns`, held to this owner: its root opens for the namespace, and part 0's
 * header names format `tinyassets-export` version 3, this generation and this object's owner;
 * its root and schema, or refused.
 */
async function ownGeneration(o: RestoreDoorNeeds, ns: string, gen: string):
    Promise<{ root: ManifestRoot; schema: number }> {
  const source: Bucket = o.env.FILES;
  const root = await rootOf(source, ns, gen);
  let first: Line | undefined;
  try {
    first = lines(await objectAt(source, ns, root, 0))[0];
  } catch {
    throw refused("the generation's first part does not open under the namespace's key");
  }
  const header = exportHeader(first);
  if (header === null || header.gen !== gen) {
    throw refused(`the generation is not a ${EXPORT_FORMAT} version ${EXPORT_VERSION} ` +
                  `generation ${gen}`);
  }
  if (header.owner !== o.owner()) {
    throw refused("the generation is another owner's");
  }
  return { root, schema: header.schema };
}

export const restoreDoors = Object.freeze({
  /**
   * What a restore could read for this owner: its namespace and its finished generations, as
   * this object holds them, and its admission and any restore's step. A read.
   */
  async restoreSource(o: RestoreDoorNeeds, door: Door<undefined>): Promise<RestoreSource> {
    return o.door(() => {
      door.check();
      const held = record(o.storage.sql);
      const begun = held !== null && !["pinned", "wiped", "done", "failed"].includes(held.step);
      return { namespace: o.files.storedNamespace(),
               generations: o.backups.manifests().map(({ gen, finished_at }) =>
                 ({ gen, finished_at, restorable: o.backups.restorable(gen, clock()) })),
               state: o.admission.row().state, begins: begun || emptiness(o) === null,
               restore: held === null ? null
                 : { step: held.step, gen: held.gen, fresh: held.fresh, since: held.since } };
    });
  },
  /**
   * A restore begun from generation `gen` in `namespace`: refused unless this object is pending
   * and empty, and unless the generation opens under the namespace and is this owner's, of a
   * schema this code brings forward. Asked again with the same generation, it answers as it
   * stands.
   */
  async restoreBegin(o: RestoreDoorNeeds, door: Door<RestoreBeginRequest>): Promise<RestoreAnswer> {
    const asked = await o.door(() => {
      const { ns, gen } = generationAsked(door);
      const held = record(o.storage.sql);
      if (held !== null && held.step !== "pinned" && held.step !== "wiped" && held.ns === ns
          && held.gen === gen) {
        return { ns, gen, held };
      }
      const why = emptiness(o);
      if (why !== null) {
        throw refused(why);
      }
      wipedFor(held, ns, gen);
      return { ns, gen, held: null, from: held?.ns ?? null };
    });
    if (asked.held !== null) {
      return o.door(() => answer(o, asked.held!));
    }
    const { ns, gen, from } = asked;
    const { root, schema } = await ownGeneration(o, ns, gen);
    const unloadable = unforwardable(schema);
    if (unloadable !== null) {
      throw refused(unloadable);
    }
    // A wiped object's record names the namespace the owner had: the one it adopts holds it
    // first, so no erasure from here on misses it.
    if (from !== null && from !== ns) {
      await o.files.holdUnder(ns, from);
    }
    return o.door(() => {
      door.check();
      const why = emptiness(o);
      if (why !== null) {
        throw refused(why);  // another call began one, or wrote, while the root was read
      }
      wipedFor(record(o.storage.sql), ns, gen);
      o.files.adoptSpace(ns, root.key);
      // A wiped object's record goes on, its pin's fresh generation kept: the undo.
      o.storage.sql.exec("INSERT INTO owner_restore (only, ns, gen, step, at, since, schema, " +
                           "source) VALUES (1, ?, ?, 'index', 0, ?, ?, ?) ON CONFLICT (only) DO " +
                           "UPDATE SET ns = excluded.ns, gen = excluded.gen, step = 'index', " +
                           "at = 0, staged = 0, peak = 0, schema = excluded.schema, source = " +
                           "excluded.source", ns,
                         gen, clock(), MIGRATIONS.length, schema);
      return answer(o, record(o.storage.sql)!);
    });
  },
  /**
   * Another namespace of this owner, which this object does not adopt, held by its own
   * (`Files.holdNamespace`, `contract/src/files.ts` `HOLDS`), so its erasure takes that one
   * too and a restore of the whole bucket begins none it holds
   * (`docs/decisions/untangle-2026-11/r8-erased-orphans.md`): refused unless generation `gen`
   * of `namespace` is this owner's (as a begin holds it), while this object takes a restore
   * (it then adopts the namespace: `restoreBegin`), for a pending owner no restore holds (an
   * import's, whose discard would delete what it holds), and for its own namespace. Its own is
   * made if it has none, and none it holds holds it after (`r8-lost-object.md`). Asked again,
   * it answers as it stands.
   */
  async restoreHold(o: RestoreDoorNeeds, door: Door<RestoreBeginRequest>): Promise<RestoreHeld> {
    const { ns, gen } = await o.door(() => {
      const asked = generationAsked(door);
      if (emptiness(o) === null) {
        throw refused("this object takes a restore: begin it from the namespace, which it then " +
                      "adopts");
      }
      // Pending, holding rows and no restore: the import's, whose discard deletes its
      // namespace and every one it holds (`Erasure.discard`).
      if (o.admission.row().state === "pending" && record(o.storage.sql) === null) {
        throw refused("the owner is pending and no restore holds it: an import's discard " +
                      "would delete what it holds");
      }
      if (o.files.storedNamespace() === asked.ns) {
        throw refused("the namespace is this object's own");
      }
      return asked;
    });
    await ownGeneration(o, ns, gen);
    return { namespace: await o.files.holdNamespace(ns), held: ns };
  },
  /**
   * The restore carried on, a piece at a time, for at most `RESTORE_PASS_MS`; how far it is.
   * Refused while none was begun; a failed one answers its failure.
   */
  async restoreStep(o: RestoreDoorNeeds, door: Door<undefined>): Promise<RestoreAnswer> {
    await o.door(() => door.check());
    const until = clock() + RESTORE_PASS_MS / 1000;
    const source: Bucket = o.env.FILES;
    while (await piece(o, door, source) && clock() < until) {
      // the next piece
    }
    await o.sync();
    return o.door(() => answer(o, record(o.storage.sql)!));
  },
  /**
   * Generation `gen` of `namespace` pinned on this live owner, the owner held, and its present
   * state written as a fresh generation: the first call pins and holds, every call carries the
   * fresh generation on for at most `RESTORE_PASS_MS` until it is written; its answer then names
   * the words that confirm the wipe. The generation is one of its own namespace's finished,
   * restorable ones, or one of another namespace that names this owner and that its own then
   * holds, so its erasure takes it from the pin on (`r8-lost-object.md`). A record past the pin,
   * for that generation or the one its pin wrote, answers as it stands; a pending owner is
   * restored into directly, with no pin.
   */
  async restorePin(o: RestoreDoorNeeds, door: Door<RestorePinRequest>): Promise<RestoreHold> {
    const asked = await o.door(() => {
      const { ns, gen } = generationAsked(door);
      const held = pinHeld(o, ns, gen);
      return { ns, gen, own: held !== null || pinnable(o, ns, gen), held };
    });
    if (asked.held === null && !asked.own) {
      const unloadable = unforwardable((await ownGeneration(o, asked.ns, asked.gen)).schema);
      if (unloadable !== null) {
        throw refused(unloadable);
      }
      await o.files.holdNamespace(asked.ns);
    }
    const { held, began } = asked.held !== null ? { held: asked.held, began: false }
      : await o.door(() => {
        door.check();
        const now = pinHeld(o, asked.ns, asked.gen);  // another call pinned it meanwhile
        if (now !== null) {
          return { held: now, began: false };
        }
        pinnable(o, asked.ns, asked.gen);
        // The hold, in the unit that pins: no door but the restore's from here on, and every
        // run and stream ended, as a suspension's prompt stop ends them (`admission.ts`).
        o.storage.sql.exec("INSERT INTO owner_restore (only, ns, gen, step, at, since) VALUES " +
                             "(1, ?, ?, 'pinned', 0, ?) ON CONFLICT (only) DO UPDATE SET ns = " +
                             "excluded.ns, gen = excluded.gen, step = 'pinned', at = 0, after = " +
                             "NULL, held = NULL, spans = NULL, failed = NULL, fresh = NULL, " +
                             "since = excluded.since, staged = 0, peak = 0, schema = NULL, " +
                             "source = NULL", asked.ns, asked.gen, clock());
        o.egress.end("admission");
        o.launcher.endAll(clock());
        return { held: record(o.storage.sql)!, began: true };
      });
    if (began) {
      o.relay.closeAll("lost");  // a socket opened again is refused until the restore is done
    }
    if (held.step !== "pinned") {
      return o.door(() => holdOf(o, held));
    }
    // The fresh generation, begun after the hold: a generation under way began before it, so
    // it is carried to its end first, and a new one begun.
    await o.quiet(() => {
      door.check();
      const now = record(o.storage.sql);
      if (now?.step === "pinned" && (now.fresh === null || !o.backups.kept(now.fresh))
          && o.backups.writing() === null) {
        o.storage.sql.exec("UPDATE owner_restore SET fresh = ?", o.backups.begin(clock()));
      }
    });
    if (!await o.door(() => holdOf(o, record(o.storage.sql)!).written)) {
      await o.backups.run(clock(), clock() * 1000 + RESTORE_PASS_MS);
    }
    return o.door(() => {
      door.check();
      return holdOf(o, record(o.storage.sql)!);
    });
  },
  /**
   * The object wiped for its restore, confirmed by the words the pin's answer names: in one
   * unit, every table the schema made dropped and migrated afresh, and the record written
   * again at `wiped`; then the instance reset (the call ends with its reset, and the
   * operator channel's redelivery answers it). Nothing in R2 is touched. A wiped or begun
   * record answers as it stands; a failed restore is wiped again the same way. A pin is
   * refused its wipe once its generation or its undo can no longer be restored.
   */
  async restoreWipe(o: RestoreDoorNeeds, door: Door<RestoreWipeRequest>): Promise<RestoreHold> {
    // From the wipe the object names no namespace: a pin of another namespace's generation
    // first has that one hold the object's own, so the owner's erasure, which takes the
    // record's then, misses neither (`restoreNamespace`).
    const leaving = await o.door(() => {
      const held = record(o.storage.sql);
      const own = o.files.storedNamespace();
      return wipeDue(o, door, held) && held!.step === "pinned" && own !== null && own !== held!.ns
        ? { ns: held!.ns, own } : null;
    });
    if (leaving !== null) {
      await o.files.holdUnder(leaving.ns, leaving.own);
    }
    const wiped = await o.door(() => {
      const held = record(o.storage.sql);
      if (!wipeDue(o, door, held)) {
        return false;
      }
      // A transaction of its own: a unit that throws keeps what it wrote (`gate.ts`), so a
      // wipe that throws part way would leave the object half made and its pin gone.
      o.storage.transactionSync(() => {
        remigrate(o.storage, MIGRATIONS, schemaTables());
        o.storage.sql.exec("INSERT INTO owner_restore (only, ns, gen, step, at, fresh, since) " +
                             "VALUES (1, ?, ?, 'wiped', 0, ?, ?)", held!.ns, held!.gen,
                           held!.fresh, held!.since);
      });
      return true;
    });
    if (wiped) {
      await o.reset();
    }
    return o.door(() => {
      door.check();
      return holdOf(o, record(o.storage.sql)!);
    });
  },
  /**
   * A pin not yet wiped given up: the record gone, the owner as it was (the runs the hold
   * ended stay ended), its lanes running again. With none pinned, answered the same.
   */
  async restoreRelease(o: RestoreDoorNeeds, door: Door<undefined>): Promise<{ released: true }> {
    await o.door(() => {
      door.check();
      const held = record(o.storage.sql);
      if (held === null || held.step === "done") {
        return;
      }
      if (held.step !== "pinned") {
        throw refused("the owner's object was wiped for its restore: restore the generation " +
                      "it was pinned for, or the one its pin wrote");
      }
      o.storage.sql.exec("DELETE FROM owner_restore");
    });
    await o.sync();
    return { released: true as const };
  },
  /**
   * What a copy back from the off-account copy may write into this owner's namespace
   * (`deploy/offsite.ts` `copyBack`), as the files plane's rule has it, every object in the
   * bucket having a row first: the bucket name of each of `NAMES_ROWS` of its chunk rows, in
   * any state but `deleting`, from the `from`th in hash order, and every finished generation
   * it holds. So a chunk the owner's own collection deleted or is deleting (garbage past its
   * grace, a redaction's) is never written back. A read. Pages go by offset, so no hash leaves the
   * object (the operator log keeps every answer): a row gone between two pages moves one past the
   * next page's start, a name left out, never one added, and a run again finds it still lacking.
   */
  async restoreNames(o: RestoreDoorNeeds, door: Door<RestoreNamesRequest>): Promise<RestoreNames> {
    const read = await o.door(() => {
      const { r } = door.check();
      const from = r.from;
      if (typeof from !== "number" || !Number.isSafeInteger(from) || from < 0) {
        throw new Refusal("invalid", "a page of names begins at a whole row (from >= 0)");
      }
      if (o.files.storedNamespace() === null) {
        throw refused("the owner's object names no namespace");
      }
      return { hashes: o.files.chunksFrom(from, NAMES_ROWS + 1), from,
               generations: o.backups.manifests().map(({ gen }) => gen) };
    });
    const page = read.hashes.slice(0, NAMES_ROWS);
    return { chunks: await o.files.names(page), generations: read.generations,
             next: read.hashes.length > NAMES_ROWS ? read.from + NAMES_ROWS : null };
  },
});
