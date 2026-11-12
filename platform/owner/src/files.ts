/**
 * Owner files: the manifest in the owner's own object (ADR-032, plan §6).
 *
 * The bytes are immutable chunks of at most 4 MiB in one R2 bucket shared by every
 * owner, each named by its SHA-256 under this owner's random 128-bit namespace `ns`,
 * which is made on first use and held only here, and each put and read under this owner's
 * own data key (SSE-C), which this object holds only wrapped (`data_key`, made with the
 * namespace by `FILES.mintKey`): opened by the files Worker's keyring for one call at a
 * time, never here, and gone with the object at erasure (crypto-erasure, `datakey.ts`).
 * The files Worker (`FILES`) moves them and never sees a path. Everything that says what a file is lives in this object's
 * SQLite: paths, versions and history, which chunks make which content, the chunks'
 * reference counts, upload intents, quotas, the change feed and the sync lease. Files of
 * 4 KiB or less are kept inline, so reading `AGENTS.md` or a skill touches no bucket.
 *
 * - Versions. Every commit is stamped with the next owner-wide `seq`, and a version is
 *   named by that seq: it never repeats across a delete, a re-create or a history prune,
 *   with no tombstone kept. Each write is a compare-and-swap on the entry's etag, as on
 *   the board (S34): `expected` null writes unconditionally, `""` only creates, and
 *   anything else must be the current etag; a lost swap is answered with the entry as it
 *   stands ({@link FileConflict}).
 * - Content. A content id names its kind and size with its bytes (inline, or a chunk
 *   list), so no content of one kind can name another's; content already stored under
 *   an id is reused only when it is the same content, else the write fails loudly. A
 *   content is held by every entry and every kept history row naming it, in any root,
 *   so content anything still names is never collected.
 * - History. Every workspace change appends a history row (`file_history`), which is
 *   also the change feed; the temporary area keeps none. Retention keeps each live
 *   path's newest `keep_versions`, and its versions younger than `retention_days` up to
 *   `keep_recent` in all; a version past those goes in the change that pushed it out. A
 *   row kept only for its age is due for pruning (`prune_after`) at that age, and
 *   housekeeping prunes due rows; a retention change is applied
 *   to the rows already kept, by housekeeping too. A gone path's rows are `released`:
 *   a change that would take history past its quota first prunes that many of them,
 *   earliest due first, so churn of deleted paths never fills it. `restore` writes an
 *   old version as a new one; `redact` drops one old version's content, whose bytes go
 *   once no other row holds the same content; `purge` drops a path's old versions.
 * - Uploads (restic's order: bytes, then index, then snapshot). An upload intent
 *   declares a file's size and its chunks, cut at 4 MiB, and records them as held
 *   before any byte is sent. A chunk counts as stored only once `FILES` accepted a put
 *   R2 verified, recorded after the put resolves; a commit refuses any chunk not stored.
 *   So every object in the bucket has a row first, garbage collection never lists the
 *   bucket, and a deduplicated chunk is always one the bucket holds. A chunk whose
 *   references and holds both reach zero becomes garbage, deleted after a 24 h grace; a
 *   stored garbage chunk revives when wanted again, one never stored is sent again, and
 *   one being deleted is never held.
 * - Quotas (owner policy, `FILE_DEFAULTS`): chunk bytes live and in history, inline
 *   bytes, the manifest's own bytes (every row as SQLite stores it, each path byte and
 *   overflow page included: `manifest.ts`), entries, history rows and a file's size, each
 *   refused loudly at its bound in the change's own unit, with a fixed code
 *   (`FILE_QUOTA`) and nothing left behind. A change that only removes (a delete, a
 *   redaction, a purge, housekeeping) is never refused, so an owner at a bound can always
 *   make room. They read counters the schema's triggers keep, as do the bounds on open
 *   uploads. An outside client's change is held to its share of each as well, and all
 *   clients' to what the owner's reserve leaves (`FILE_SHARE`), so no client fills the
 *   owner's object. Each scope (an outside client's own, all clients', the owner's
 *   writers') is charged its own rows, and once for each content and chunk its rows and
 *   open uploads reference, deduplicated within the scope and never across, so no scope
 *   keeps bytes alive at another's cost; an upload is told a chunk need not be sent only
 *   when its writer's own scope holds it, and commits only the chunks it sent or holds.
 * - The sandbox leases, one an authority (the owner's own work's sandbox, and each outside
 *   client's own; slice R's review H-1), each for its sandbox's runs and sync agent: a
 *   random `lease_id` is the claim token and a rising `epoch` (one counter for all) the
 *   fencing number, as the ledger's claims are. A sync commit is refused unless both are a
 *   live lease's; a client's sandbox writes as that client (its share) and never into the
 *   owner's agent definitions (`OWNER_TREES`). Within a commit, each op has its
 *   own outcome and nothing one op does undoes another. API writes never take the
 *   lease. A sync write made against a version that has moved on, or blocked by what the
 *   API made of a parent, leaves the API's state in place and saves the sandbox's content
 *   beside it under a new conflict name, reported in the answer: nothing is dropped and
 *   nothing is silent.
 *
 * Every change runs in one `transactionSync`, so a refusal or a thrown error leaves
 * nothing behind; only chunk bytes and their deletes cross to `FILES`, between
 * transactions, at most two chunks a call. A change a caller makes is one unit of the
 * object's write gate (`gate.ts`), which the caller opens (a door); the methods that
 * cross to `FILES` open their own, one for each step around a call, so no write waits on
 * the files Worker.
 */
import { HEX32, HEX64, randomHex } from "../../contract/src/ids";
import type { SyncOp } from "../../contract/src/owner";

import { chunkedId, chunkPlan, cut, inlineId } from "../../contract/src/content";
import { sha256Hex } from "../../contract/src/digest";
import { KEEP_CEILING } from "../../contract/src/file-defaults";
import {
  BATCH_BYTES, CHUNK_BYTES, DELETE_BATCH, GC_GRACE_S, INLINE_BYTES, INTENT_ID_BYTES, LEASE_TTL_S,
  MAX_INTENT_CHUNKS, MAX_LIST, MAX_PATH_BYTES, MAX_SYNC_OPS,
} from "../../contract/src/limits";

import { letGo, planEdit } from "./edit";
import { FileFault, Refusal, type RefusalCode } from "../../contract/src/refusal";
import type { OwnerStorage, Write } from "./gate";
import { CLIENT_WRITER, CLIENTS, HISTORY_BYTES, OWNER_SCOPE } from "./manifest";
import { inTime } from "./schedule";
import { refuseErasing } from "./erasure";
import { TIER_UNREAD } from "./tier";
import { AGENTS_OWNER_ONLY, ownersOnly } from "./paths";
import {
  ancestors, isRoot, MAX_NAME_BYTES, nameOf, parentOf, storedPath, TEMP, within,
  WORKSPACE,
} from "../../contract/src/paths";
import { codePoints, isPrintable, utf8Bytes, utf8Decoder } from "../../contract/src/text";
import { APPEND_UNITS, EDIT_MAKE_UNITS, EDIT_READ_UNITS, ownerSlots } from "./holds";
import {
  AUTOMATON_STATES, DEFAULT_IGNORES, LIST_BYTES, ruleLines, SYNC_IGNORE, unappliedLines,
} from "../../contract/src/sync-ignore";
import type {
  Account, Call, Door, OwnerState,
} from "./owner-do";
import type { Binding } from "../../contract/src/binding";
import type { Edited, EditRefusal } from "./edit";
import type { ChunkStore } from "../../contract/src/files";
import { fitList, PAGE_BYTES, RESULT_TOO_LARGE } from "./pages";
import {
  frontmatter, READ_DEFAULT_BYTES, SEARCH_QUERY_BYTES, SEARCH_SCAN, searchPage, textOf, titleOf,
  windowEnd, words,
} from "./file-search";
import { clock } from "./clock";
import { fitted, jsonBytes } from "../../contract/src/encoded";
import { slotted } from "../../contract/src/slots";
import { APP_RESOURCE } from "../../contract/src/identity";


/**
 * The fixed codes a quota's refusal starts with: the owner's whole storage, the tier's
 * figure or the owner's own lower one (`storage_full`, `storage-full.ts`: the one wall a
 * user meets); another owner-wide bound (`file_quota`); and an outside client's share of
 * one, or all clients' together (`file_share`).
 */
export const FILE_QUOTA = "file_quota" satisfies RefusalCode;
export const FILE_SHARE = "file_share" satisfies RefusalCode;
const FILE_TOO_LARGE = `${FILE_QUOTA}: the file is larger than the owner's file size quota`;
/**
 * The most bytes one whole-file write moves through the object (four chunks, two `FILES`
 * calls); larger files go by upload, a chunk at a time.
 */
export const WHOLE_BYTES = 4 * CHUNK_BYTES;
/** An upload intent's life: past it, its uncommitted chunks become garbage. */
export const INTENT_TTL_S = 86_400;
/**
 * A placed body's upload's life (ADR-035): its bytes go to R2 and its row commits in one
 * door call, so a crashed placement holds its quota and its upload slot for minutes only.
 */
export const PLACE_TTL_S = 600;
/** When a failed chunk delete is tried again. */
export const GC_RETRY_S = 300;
/** The owner's own work's sandbox: the authority every run the owner started holds. */
export const OWNER_AUTHORITY = "owner";
/** A temporary directory's life. */
const TEMP_TTL_S = 7 * 86_400;
/**
 * How late housekeeping may run after it is due, so one alarm takes everything due in
 * an hour rather than each prune or delete arming its own.
 */
export const HOUSEKEEPING_SLACK_S = 3600;
/**
 * The least of a pass's budget a chunk delete is begun with: one that could not finish
 * would only mark its chunks `deleting` to be tried again `GC_RETRY_S` later.
 */
export const DELETE_FLOOR_MS = 1000;
/** Rows one alarm run prunes or expires per kind, and paths it re-marks for retention. */
const HOUSEKEEPING_BATCH = 500;
/** Open upload intents, and the chunks they hold in all. */
export const MAX_OPEN_INTENTS = 64;
export const MAX_HELD_CHUNKS = 20_000;


/** The owner's files settings the object enforces (owner policy's `files` table). */
export interface FileLimits {
  /**
   * File bytes (chunks, live and in history, and placed bodies): the tier's storage, or the
   * owner's own lower figure; null while the tier's is not known here (`TIER_UNREAD`).
   */
  readonly max_bytes: number | null;
  readonly max_inline_bytes: number;
  readonly max_manifest_bytes: number;
  readonly max_entries: number;
  readonly max_file_bytes: number;
  readonly max_history: number;
  readonly retention_days: number;
  readonly keep_versions: number;
  readonly keep_recent: number;
  /** The percent of every quota one outside client may hold. */
  readonly client_share: number;
  /** The percent of every quota kept for the owner: all clients together hold the rest. */
  readonly owner_reserve: number;
}

/** `bound`'s part at `percent`. */
function shareOf(bound: number, percent: number): number {
  return Math.floor(bound * percent / 100);
}

/** Whether a writer is an outside client, whose changes count against its share. */
function isClient(writer: string | null): writer is string {
  return writer !== null && writer.startsWith("client:");
}

/**
 * The scopes a writer's rows count in, its own last: an outside client's all clients'
 * and its own, anyone else's the owner's.
 */
function scopesOf(writer: string): readonly string[] {
  return isClient(writer) ? [CLIENTS, writer] : [OWNER_SCOPE];
}

/** The owner's place in the bucket: its namespace and its wrapped data key. */
export interface ChunkSpace {
  readonly ns: string;
  readonly key: string;
}

/**
 * What retention frees when the files byte quota would refuse a change (ADR-035): the oldest
 * placed collection history entry, or the oldest acked message with a placed body, of the
 * writers `filer` names (any, `*`; every outside writer, `client:*`; or one), one at a time;
 * false when none
 * is left. Run in the change's own unit, before its quota is checked.
 */
type Reclaim = (filer: string) => boolean;

/**
 * The refusal at the owner's storage figure: its message from what is stored (open uploads
 * apart), what the change needs, what open uploads hold, and the figure (`storage-full.ts`).
 */
/**
 * A workspace path a change made or took away, in that change's own unit: whether it is
 * there now, and the change itself (its writer, and a file's content and size).
 */
export type Changed = (path: string, present: boolean, now: number,
                       change: { writer: string; content_id: string | null; size: number })
  => void;

export type Full = (stored: number, needed: number, held: number, limit: number) => Refusal;

type FileKind = "file" | "dir";

/** A path as it stands. */
export interface Entry {
  readonly path: string;
  readonly kind: FileKind;
  readonly version: number;
  readonly etag: string;
  readonly size: number;
  /** The executable bit: 1 or 0. */
  readonly mode: number;
  readonly mtime_ms: number;
  readonly writer: string;
}

/** One chunk of a file: its hash and size. */
export interface ChunkRef {
  readonly sha256: string;
  readonly size: number;
}

/** A file's content, pinned: its bytes when inline, else its chunks in order. */
export interface Content {
  readonly entry: Entry;
  readonly content_id: string;
  readonly inline?: Uint8Array;
  readonly chunks?: ChunkRef[];
}

/** One history row, which is also one change in the feed. */
export type Change = {
  readonly version: number;
  readonly path: string;
  readonly op: string;
  /** What the path is after the change; null when it is gone. */
  readonly kind: FileKind | null;
  readonly content_id: string | null;
  readonly size: number;
  readonly mode: number;
  readonly writer: string;
  readonly at: number;
};

export interface Usage {
  readonly entries: number;
  readonly history: number;
  readonly chunk_bytes: number;
  readonly inline_bytes: number;
  readonly manifest_bytes: number;
  /** Inline bodies of collection rows, their history and mail. */
  readonly body_bytes: number;
  /** All the user content the tier's storage counts: chunks, small files, inline bodies. */
  readonly stored: number;
  readonly intents: number;
  readonly intent_chunks: number;
  readonly limits: FileLimits;
  /**
   * The caller's own scope's counters: an outside client's, held to `client_share` of
   * each quota, or the owner's own writers'.
   */
  readonly share: Pick<Usage, "entries" | "history" | "chunk_bytes" | "inline_bytes" |
                              "manifest_bytes" | "body_bytes" | "stored">;
}

export interface Lease {
  readonly lease_id: string;
  readonly epoch: number;
  readonly expires_at: number;
  /** The feed's seq when the lease was taken. */
  readonly base: number;
}

/** A live sandbox lease as the runs, egress and the sync doors check it. */
export interface SandboxLease {
  /** Whose sandbox: the owner's own work's (`OWNER_AUTHORITY`), or an outside client's id. */
  readonly authority: string;
  readonly lease_id: string;
  readonly epoch: number;
  /** The SHA-256 hex of the egress token's secret, which only E2B's transform carries. */
  readonly egress_token: string;
  /** The SHA-256 hex of the sync token's secret, which the in-VM sync agent holds. */
  readonly sync_token: string;
  /** The SHA-256 hex of the runner's own secret, which no CLI holds. */
  readonly runner_token: string;
  readonly expires_at: number;
  /** The egress generation the sandbox's network was last applied under, or null. */
  readonly network_generation: number | null;
  /**
   * The egress generation a re-apply of its network is out with (`Launcher.reapply`), or
   * null: its calls are admitted under it too, since only that network sets it.
   */
  readonly network_pending: number | null;
  /** When the last re-apply was sent or failed (s), and under which generation: the lane's
   * retry clock. */
  readonly network_tried_at: number | null;
  readonly network_tried_generation: number | null;
  /** The engines its own runner last reported signed in inside it (a client's sandbox's). */
  readonly signins: readonly string[];
}

/**
 * The three tokens' hashes a lease is bound to. They live while the lease does: no deadline
 * of their own, the lease's renewal (its heartbeat) is their life.
 */
interface LeaseTokens {
  readonly egress: string;
  readonly sync: string;
  readonly runner: string;
}

type LeaseRow = {
  authority: string; lease_id: string; epoch: number; holder: string; lease_expires: number;
  egress_token: string; sync_token: string; runner_token: string;
  network_generation: number | null; network_pending: number | null;
  network_tried_at: number | null; network_tried_generation: number | null; signins: string;
};

function leaseOf(row: LeaseRow): SandboxLease {
  return { authority: row.authority, lease_id: row.lease_id, epoch: row.epoch,
           egress_token: row.egress_token, sync_token: row.sync_token,
           runner_token: row.runner_token, expires_at: row.lease_expires,
           network_generation: row.network_generation, network_pending: row.network_pending,
           network_tried_at: row.network_tried_at,
           network_tried_generation: row.network_tried_generation,
           signins: row.signins === "" ? [] : row.signins.split(",") };
}

/** A lease's row, and its tokens, are live while its renewal has not lapsed. */
const LIVE_LEASE = "lease_expires > ?";

export type SyncOutcome =
  | { readonly path: string; readonly outcome: "committed"; readonly entry: Entry | null }
  | { readonly path: string; readonly outcome: "conflict"; readonly current: Entry | null;
      readonly copy: Entry | null }
  | {
    readonly path: string; readonly outcome: "refused"; readonly reason: string;
    /** Its code, which the sync agent reads; `reason` is for people. */
    readonly code: string;
  };

/** The expected etag is not the entry's; `current` is the entry as stored, or null. */
export class FileConflict extends Error {
  override readonly name = "FileConflict";
  constructor(readonly current: Entry | null) {
    super("conflict");
  }
}

type StoredEntry = {
  parent: string;
  name: string;
  kind: FileKind;
  version: number;
  content_id: string | null;
  size: number;
  mode: number;
  mtime_ms: number;
  writer: string;
};

type Space = {
  ns: string | null;
  data_key: string | null;
  seq: number;
  feed_floor: number;
  entries: number;
  history: number;
  chunk_bytes: number;
  inline_bytes: number;
  manifest_bytes: number;
  /** Inline bodies of collection rows, their history and mail (the schema's triggers). */
  body_bytes: number;
  /**
   * Everything the owner's writers store, wherever it lives: chunk bytes in R2 (files and
   * placed bodies), small files and inline bodies in the object. The tier's one storage
   * number (`max_bytes`) bounds it. Computed by the reads, never stored.
   */
  stored: number;
  intents: number;
  intent_chunks: number;
  epoch: number;
  retained_keep: number;
  retained_recent: number;
  retained_days: number;
  retain_cursor: string | null;
};

type Counters = Pick<Space, "entries" | "history" | "chunk_bytes" | "inline_bytes" |
                             "manifest_bytes" | "body_bytes" | "stored">;

/** What a change is checked against: the owner's counters, its writer's share and all clients'. */
interface Before {
  readonly space: Counters;
  readonly share: Counters;
  readonly clients: Counters;
}

const NO_SHARE: Counters = Object.freeze({ entries: 0, history: 0, chunk_bytes: 0,
                                           inline_bytes: 0, manifest_bytes: 0, body_bytes: 0,
                                           stored: 0 });
/** What the tier's storage counts, as a column expression over a counters row. */
const STORED = "chunk_bytes + inline_bytes + body_bytes";

/** Each counter a quota bounds: its setting, and what it is called in a refusal. */
const QUOTAS: readonly (readonly [keyof Counters, keyof FileLimits, string])[] = Object.freeze([
  Object.freeze(["inline_bytes", "max_inline_bytes", "small-file bytes"] as const),
  Object.freeze(["stored", "max_bytes", "stored bytes"] as const),
  Object.freeze(["manifest_bytes", "max_manifest_bytes", "file manifest bytes"] as const),
  Object.freeze(["entries", "max_entries", "file entries"] as const),
  Object.freeze(["history", "max_history", "file history rows"] as const),
]);
/** Released history rows one fitting step reads. */
const FIT_BATCH = 64;

type Intent = { size: number; chunks: string[]; pending: string[]; writer: string };

function etag(e: { version: number; kind: string; content_id: string | null; mode: number }):
    string {
  return sha256Hex(`${e.version}:${e.kind}:${e.content_id ?? ""}:${e.mode}`);
}

function pathOf(e: StoredEntry): string {
  return `${e.parent}/${e.name}`;
}

function shown(e: StoredEntry): Entry {
  return { path: pathOf(e), kind: e.kind, version: e.version, etag: etag(e), size: e.size,
           mode: e.mode, mtime_ms: e.mtime_ms, writer: e.writer };
}

function rootEntry(path: string): Entry {
  return { path, kind: "dir", version: 0, etag: etag({ version: 0, kind: "dir",
           content_id: null, mode: 0 }), size: 0, mode: 0, mtime_ms: 0, writer: "system" };
}

function buffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as
    ArrayBuffer;
}

function expectedEtag(value: unknown): string | null {
  if (value !== null && typeof value !== "string") {
    throw new Refusal("invalid", "expected_etag is null, empty or an etag");
  }
  return value;
}

/** The executable bit, or null to keep the path's (0 for a new file). */
function modeOf(value: unknown): number | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (value !== 0 && value !== 1) {
    throw new Refusal("invalid", "mode is 0 or 1");
  }
  return value;
}

function count(value: unknown, what: string, low: number, high: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < low || value > high) {
    throw new Refusal("invalid", `${what} is a whole number from ${low} to ${high}`);
  }
  return value;
}

function hashes(packed: string): string[] {
  return packed.match(/.{64}/g) ?? [];
}

/** Groups of items whose `size` sums to at most `BATCH_BYTES` each. */
export function batches<T extends { size: number }>(items: readonly T[]): T[][] {
  const groups: T[][] = [];
  let bytes = BATCH_BYTES;
  for (const item of items) {
    if (bytes + item.size > BATCH_BYTES) {
      groups.push([]);
      bytes = 0;
    }
    groups.at(-1)!.push(item);
    bytes += item.size;
  }
  return groups;
}

/**
 * A conflict copy's name: `name` cut at a code point to leave room, then
 * `.ta-conflict-<epoch>` and, past the first, `-<n>`, at most 255 bytes in all.
 */
/**
 * An upload names a chunk the sweep is deleting this moment: refused, nothing held, so its
 * writer begins again once the delete lands (a placed body's door does so itself).
 */
const CHUNK_DELETING = "a chunk of this upload is being deleted; begin again";
/** A chunk an export names that the owner no longer stores (or never did). */
export const CHUNK_GONE = "no such chunk: it is gone from the bucket; begin the export again";

function conflictName(name: string, epoch: number, n: number): string {
  const suffix = `.ta-conflict-${epoch}${n > 1 ? `-${n}` : ""}`;
  const points = [...name];
  while (utf8Bytes(points.join("")) + suffix.length > MAX_NAME_BYTES) {
    points.pop();
  }
  return points.join("") + suffix;
}

/**
 * A write's mtime as its caller gives it (the cutover's import keeps each file's, `import.ts`),
 * a whole number of milliseconds, or null for the write's own time.
 */
function mtimeOf(value: unknown): number | null {
  if (value === undefined) {
    return null;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Refusal("invalid", "a file's mtime is a whole number of milliseconds");
  }
  return value;
}

export class Files {
  readonly #storage: OwnerStorage;
  readonly #store: ChunkStore;
  readonly #limits: () => FileLimits;
  /** When (s) the newest finished nightly manifest began, or null: what it names is kept. */
  readonly #manifested: () => number | null;
  /** The object's write gate: the units the methods that cross to `FILES` write through. */
  readonly #write: Write;
  /** What retention frees at the byte quota (the collections' history, acked mail). */
  readonly #reclaim: Reclaim;
  /** The `FILES` puts in flight on this instance, which the erasure waits for. */
  readonly #puts = new Set<Promise<unknown>>();
  /**
   * Told of each workspace path a change makes or takes away, in that change's own unit:
   * what follows a path (an agent's ledger identity its `agent.json`). A refusal refuses
   * the change.
   */
  readonly #changed: Changed;
  /**
   * The refusal at the storage figure (`storage-full.ts`): the one wall, its message naming
   * what is stored, what the change needs, what open uploads hold apart, the whole, how to
   * free space and the upgrade path. The object names the owner's tier: its caller's to give,
   * never a default.
   */
  readonly #full: Full;
  /** The chunk bytes the change in hand moved to `uploading` itself (`begin`), for `#bounds`. */
  #moved = 0;
  /**
   * Whether the change in hand may pass the storage figure: an archive move's placement
   * (`begin`'s `pastFigure`), which frees at least the bytes it places in its second unit.
   */
  #pastFigure = false;

  constructor(storage: OwnerStorage, store: ChunkStore, limits: () => FileLimits,
              write: Write, full: Full, reclaim: Reclaim = () => false,
              changed: Changed = () => {}, manifested: () => number | null = () => null) {
    this.#storage = storage;
    this.#manifested = manifested;
    this.#store = store;
    this.#limits = limits;
    this.#write = write;
    this.#reclaim = reclaim;
    this.#changed = changed;
    this.#full = full;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #space(): Space {
    return this.#sql.exec<Space>(`SELECT *, ${STORED} AS stored FROM file_space`).one();
  }

  /**
   * The owner's namespace, made on first use; it never leaves the object but to `FILES`.
   * A read once made; making it is a write, so its first use is inside a unit.
   */
  ns(): string {
    const stored = this.#space().ns;
    if (stored !== null) {
      return stored;
    }
    const made = randomHex(16);
    this.#sql.exec("UPDATE file_space SET ns = ?", made);
    return made;
  }

  /**
   * In a unit: `act`, a change made outside the files plane that may store user content (a
   * collection write, a post, a mail's reply, their inline bodies counted by the schema's
   * triggers), held to the tier's storage as a file change is, in `filer`'s scopes: retention
   * makes room first, then a change past the bound is refused (`file_quota`, `file_share`).
   */
  held<T>(filer: string, act: () => T): T {
    return this.#change(filer, () => act());
  }

  /**
   * The namespace and its wrapped data key, from outside a unit: the namespace made through
   * the gate on first use, then its key minted by `FILES` and kept in a unit of its own
   * unless one was kept meanwhile (a lost race's key opens nothing anyone stored under).
   */
  async space(): Promise<ChunkSpace> {
    const held = this.#space();
    if (held.ns !== null && held.data_key !== null) {
      return { ns: held.ns, key: held.data_key };
    }
    const ns = held.ns ?? await this.#write(() => this.ns());
    const { key } = await this.#store.mintKey(ns);
    return this.#write(() => {
      this.#sql.exec("UPDATE file_space SET data_key = ? WHERE data_key IS NULL", key);
      return { ns, key: this.#space().data_key! };
    });
  }

  /**
   * Run `act` in one transaction. At the end, history is first fitted
   * to its quotas from gone paths' rows (`#fit`); then the quotas are checked (`#bounds`),
   * which never refuses a change that leaves every counter no higher, so a redaction, a
   * purge, an abort or housekeeping always passes. `writer` is whose change it is: an
   * outside client's is held to its share too (`client_share`, `owner_reserve`); null is
   * the owner's or the system's, held to the owner's quotas alone. `freeing` exempts the
   * changes that free while writing a record: a remove (its delete row adds to history) and
   * a sync commit (whose ops are bounded one by one, a remove's among them exempt).
   */
  #change<T>(writer: string | null, act: (now: number) => T,
             freeing = false): T {
    const outer = this.#moved;
    this.#moved = 0;
    try {
      return this.#storage.transactionSync(() => {
        const before = this.#before(writer);
        const value = act(Date.now() / 1000);
        this.#fit(before, writer);
        if (!freeing) {
          this.#bounds(before, writer);
        }
        return value;
      });
    } finally {
      this.#moved = outer;
    }
  }

  /** The owner's stored bytes and the storage figure (null when the tier is unread). A read. */
  storedNow(): { stored: number; figure: number | null } {
    return { stored: this.#space().stored, figure: this.#limits().max_bytes };
  }

  /**
   * The one wall's refusal for a change that needs `needed` bytes, with what is stored and
   * held as they stand (`storage_full`): what an archive that cannot move answers. A read.
   */
  fullRefusal(needed: number): Refusal {
    const space = this.#space();
    const held = this.#sql.exec<{ bytes: number }>(
      "SELECT coalesce(SUM(size), 0) AS bytes FROM file_chunks WHERE state = 'uploading'")
      .one().bytes;
    const limit = this.#limits().max_bytes ?? space.stored;
    return this.#full(Math.max(0, space.stored - held), needed, held, limit);
  }

  /** One share row's counters (`file_shares`, kept by the schema's triggers), or zeros. */
  #share(who: string): Counters {
    return this.#sql.exec<Counters>(
      "SELECT entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes, " +
        `${STORED} AS stored FROM file_shares WHERE who = ?`, who).toArray()[0] ?? NO_SHARE;
  }

  #before(writer: string | null): Before {
    const client = isClient(writer);
    return { space: this.#space(), share: client ? this.#share(writer) : NO_SHARE,
             clients: client ? this.#share(CLIENTS) : NO_SHARE };
  }

  /**
   * The scopes a change by `writer` is held to, narrowest first, each with the part of
   * every quota it may hold, its counters now and the released history rows it may prune:
   * an outside client's own share, all clients' together, then the owner's whole quota.
   */
  #scopes(before: Before, writer: string | null): {
    percent: number; before: Counters; now: () => Counters; refusal: (what: string) => Refusal;
    rows: string; args: SqlStorageValue[]; filer: string;
  }[] {
    const limits = this.#limits();
    const owner = { percent: 100, before: before.space, now: () => this.#space(),
                    refusal: (what: string) => new Refusal(FILE_QUOTA,
                      `${FILE_QUOTA}: the owner's ${what} are at their quota`),
                    rows: "1 = 1", args: [], filer: "*" };
    if (!isClient(writer)) {
      return [owner];
    }
    return [
      { percent: limits.client_share, before: before.share, now: () => this.#share(writer),
        refusal: (what) => new Refusal(FILE_SHARE, `${FILE_SHARE}: this client's ${what} are ` +
          "at its share of the owner's quota"), rows: "writer = ?", args: [writer], filer: writer },
      { percent: 100 - limits.owner_reserve, before: before.clients,
        now: () => this.#share(CLIENTS),
        refusal: (what) => new Refusal(FILE_SHARE, `${FILE_SHARE}: outside clients' ${what} are ` +
          "at their share; the rest is kept for the owner"), rows: "writer GLOB ?",
        args: [CLIENT_WRITER],
        filer: CLIENT_WRITER },
      owner,
    ];
  }

  /**
   * Refuse a change that took a counter past its bound, in any scope it is held to. A
   * change that leaves a counter no higher than it found it passes, so an owner (or a
   * client) over a lowered quota can still delete.
   */
  #bounds(before: Before, writer: string | null): void {
    const limits = this.#limits();
    for (const scope of this.#scopes(before, writer)) {
      const after = scope.now();
      for (const [counter, setting, what] of QUOTAS) {
        if (after[counter] <= scope.before[counter]) {
          continue;
        }
        const bound = limits[setting];
        if (bound === null) {
          throw new Refusal("tier_unread",
                            TIER_UNREAD);  // the tier's storage is not known: no guess
        }
        if (setting === "max_bytes" && this.#pastFigure) {
          continue;  // an archive move's placement: its second unit frees what it places
        }
        if (after[counter] > shareOf(bound, scope.percent)) {
          // The owner's whole storage is the one wall a user meets: `storage_full`, or
          // `uploads_open` when open uploads alone leave no room. A client's share of it, and
          // the index's own ceilings, keep their codes.
          if (setting === "max_bytes" && scope.filer === "*") {
            const needed = after[counter] - scope.before[counter];
            // What open uploads held before this change: what is uploading now, less the
            // chunks this change itself moved to uploading (an upload's begin), summed as it
            // moved them, so retention reclaimed in the same change never counts as held.
            const held = Math.max(0, this.#sql.exec<{ bytes: number }>(
              "SELECT coalesce(SUM(size), 0) AS bytes FROM file_chunks WHERE state = " +
                "'uploading'").one().bytes - this.#moved);
            throw this.#full(Math.max(0, scope.before[counter] - held), needed, held, bound);
          }
          throw scope.refusal(what);
        }
      }
    }
  }

  /**
   * Retention run early, only on rows it has already let go: when a change took history
   * rows or manifest bytes past their quota in a scope it is held to (or past where it
   * found them, if a lowered policy left them higher), prune the earliest-due rows of gone
   * paths (`released`) that scope holds until it fits, raising the feed's floor as
   * housekeeping does: a client's own rows for its share, any client's for all clients'
   * share, any row for the owner's. A live path's rows are never taken early, and the
   * policy keeps every live path's rows within `max_history`, so once housekeeping has
   * applied the retention in force, and with the entries quota met, there are always enough
   * gone rows and the history quota never refuses a change. The work is bounded by the
   * change's own: it prunes no more rows, nor bytes but its last row's, than the change
   * took past the bound.
   */
  #fit(before: Before, writer: string | null): void {
    const limits = this.#limits();
    for (const scope of this.#scopes(before, writer)) {
      // Retention kept in R2 makes room first (ADR-035): past the byte quota, the scope's
      // oldest placed history and acked mail go before the change is held to it.
      const bytes = limits.max_bytes;
      while (bytes !== null && !this.#pastFigure && scope.now().stored > Math.max(
               shareOf(bytes, scope.percent), scope.before.stored)
             && this.#reclaim(scope.filer)) {
        // one entry or message a step, until the scope fits or nothing is left
      }
      for (;;) {
        const now = scope.now();
        const rows = now.history - Math.max(shareOf(limits.max_history, scope.percent),
                                            scope.before.history);
        const bytes = now.manifest_bytes - Math.max(
          shareOf(limits.max_manifest_bytes, scope.percent), scope.before.manifest_bytes);
        if (rows <= 0 && bytes <= 0) {
          break;
        }
        const found = this.#sql.exec<{ version: number; path: string; bytes: number }>(
          `SELECT version, path, ${HISTORY_BYTES("file_history")} AS bytes FROM file_history ` +
            `WHERE released = 1 AND ${scope.rows} ORDER BY prune_after, version LIMIT ?`,
          ...scope.args, FIT_BATCH).toArray();
        if (found.length === 0) {
          break;
        }
        let take = 0;
        let freed = 0;
        while (take < found.length && (take < rows || freed < bytes)) {
          freed += found[take]!.bytes;
          take++;
        }
        this.#expire(found.slice(0, take));
      }
    }
  }

  // -- READING ------------------------------------------------------------------

  #entry(path: string): StoredEntry | undefined {
    return this.#sql.exec<StoredEntry>(
      "SELECT * FROM file_entries WHERE parent = ? AND name = ?", parentOf(path), nameOf(path),
    ).toArray()[0];
  }

  /** A path as it stands, a root included, or null. */
  stat(path: unknown): Entry | null {
    const at = storedPath(path);
    if (isRoot(at)) {
      return rootEntry(at);
    }
    const found = this.#entry(at);
    return found === undefined ? null : shown(found);
  }

  #dir(path: string): Entry {
    const found = this.stat(path);
    if (found === null) {
      throw new FileFault("not_found", "no such directory");
    }
    if (found.kind !== "dir") {
      throw new FileFault("not_directory", "not a directory");
    }
    return found;
  }

  /** A directory's entries in name order, after the name `after`, and the next cursor. */
  list(dir: unknown, after: unknown, limit: unknown): { entries: Entry[]; next: string | null } {
    const at = storedPath(dir);
    this.#dir(at);
    if (after !== null && typeof after !== "string") {
      throw new Refusal("invalid", "after is a name or null");
    }
    const most = count(limit, "limit", 1, MAX_LIST);
    const rows = this.#sql.exec<StoredEntry>(
      "SELECT * FROM file_entries WHERE parent = ? AND name > ? ORDER BY name LIMIT ?",
      at, after ?? "", most + 1,
    ).toArray();
    const page = rows.slice(0, most);
    return { entries: page.map(shown),
             next: rows.length > most ? page.at(-1)!.name : null };
  }

  /**
   * The workspace's entries in (directory, name) order after the stored path `after` (null
   * from the start), at most `limit` (a key range of the entries' own key), each file with
   * its bytes when they are inline; and the next cursor, null at the end.
   */
  scan(after: string | null, limit: number):
      { entries: (Entry & { inline: Uint8Array | null })[]; next: string | null } {
    const [parent, name] = after === null ? [WORKSPACE, ""]
      : [parentOf(storedPath(after)), nameOf(after)];
    const rows = this.#sql.exec<StoredEntry & { inline: ArrayBuffer | null }>(
      "SELECT e.*, c.inline FROM file_entries e LEFT JOIN file_contents c " +
        "ON c.content_id = e.content_id WHERE (e.parent = ? OR (e.parent >= ? AND " +
        "e.parent < ?)) AND (e.parent, e.name) > (?, ?) ORDER BY e.parent, e.name LIMIT ?",
      WORKSPACE, `${WORKSPACE}/`, `${WORKSPACE}0`, parent, name, limit + 1).toArray();
    const page = rows.slice(0, limit);
    return {
      entries: page.map((row) => ({
        ...shown(row), inline: row.inline === null ? null : new Uint8Array(row.inline) })),
      next: rows.length > limit ? pathOf(page.at(-1)!) : null,
    };
  }

  /** Every entry below `dir`, any depth (the subtree's parents are a key range). */
  #below(dir: string): StoredEntry[] {
    return [
      ...this.#sql.exec<StoredEntry>("SELECT * FROM file_entries WHERE parent = ?", dir),
      ...this.#sql.exec<StoredEntry>(
        "SELECT * FROM file_entries WHERE parent >= ? AND parent < ?", `${dir}/`, `${dir}0`),
    ];
  }

  /**
   * A small file's bytes, kept in the object, by its content id; null for content in
   * chunks. A read, for what follows a change in its own unit.
   */
  inlineOf(contentId: string): Uint8Array | null {
    const row = this.#sql.exec<{ inline: ArrayBuffer | null }>(
      "SELECT inline FROM file_contents WHERE content_id = ?", contentId).toArray()[0];
    return row?.inline === null || row === undefined ? null : new Uint8Array(row.inline);
  }

  /** The content a path's last change before `version` left, or null. A read. */
  contentBefore(path: string, version: number): { content_id: string; size: number } | null {
    const row = this.#sql.exec<{ content_id: string | null; size: number }>(
      "SELECT content_id, size FROM file_history WHERE path = ? AND version < ? " +
        "ORDER BY version DESC LIMIT 1", path, version).toArray()[0];
    return row === undefined || row.content_id === null ? null
      : { content_id: row.content_id, size: row.size };
  }

  /** The newest version of a path's history. A read. */
  lastVersion(path: string): number {
    return this.#sql.exec<{ version: number | null }>(
      "SELECT MAX(version) AS version FROM file_history WHERE path = ?", path).one().version ?? 0;
  }

  #content(contentId: string, size: number): { inline?: Uint8Array; chunks?: ChunkRef[] } {
    if (size <= INLINE_BYTES) {
      const row = this.#sql.exec<{ inline: ArrayBuffer | null }>(
        "SELECT inline FROM file_contents WHERE content_id = ?", contentId).one();
      if (row.inline !== null) {
        return { inline: new Uint8Array(row.inline) };
      }
    }
    const sizes = chunkPlan(size);
    const shas = this.#sql.exec<{ sha256: string }>(
      "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx", contentId,
    ).toArray();
    if (shas.length !== sizes.length) {
      throw new Error("a file's chunk list does not make up its size");
    }
    return { chunks: shas.map((row, index) => ({ sha256: row.sha256, size: sizes[index]! })) };
  }

  /** A file's current content, pinned: its bytes or its chunk list. */
  open(path: unknown): Content {
    const at = storedPath(path);
    const found = isRoot(at) ? undefined : this.#entry(at);
    if (isRoot(at) || found?.kind === "dir") {
      throw new FileFault("is_directory", "is a directory");
    }
    if (found === undefined) {
      throw new FileFault("not_found", "no such file");
    }
    return { entry: shown(found), content_id: found.content_id!,
             ...this.#content(found.content_id!, found.size) };
  }

  /** One kept version's content, pinned, as `open` gives the current one. */
  openVersion(path: unknown, version: unknown): Content & { change: Change } {
    const row = this.#version(storedPath(path), version);
    if (row.kind !== "file" || row.content_id === null) {
      throw new Refusal("invalid", "that version holds no file content");
    }
    const entry: Entry = { path: row.path, kind: "file", version: row.version,
                           etag: etag({ ...row, kind: "file" }), size: row.size, mode: row.mode,
                           mtime_ms: Math.floor(row.at * 1000), writer: row.writer };
    return { entry, change: row, content_id: row.content_id,
             ...this.#content(row.content_id, row.size) };
  }

  #version(path: string, version: unknown): Change {
    const v = count(version, "version", 1, Number.MAX_SAFE_INTEGER);
    const row = this.#sql.exec<Change>(
      "SELECT version, path, op, kind, content_id, size, mode, writer, at FROM file_history " +
        "WHERE version = ? AND path = ?", v, path,
    ).toArray()[0];
    if (row === undefined) {
      throw new FileFault("not_found", "no such version");
    }
    return row;
  }

  /**
   * Of `sha256s`, each a chunk this owner stores (put, and not being deleted: live, or
   * garbage still in its grace), in order: what an export fetches by the hashes its pages
   * named. Any other, never stored or gone since, is refused (`CHUNK_GONE`). A read.
   */
  storedChunks(sha256s: readonly unknown[]): string[] {
    return sha256s.map((sha256) => {
      if (typeof sha256 !== "string" || !HEX64.test(sha256)) {
        throw new Refusal("invalid", "a chunk is named by its sha256");
      }
      const found = this.#sql.exec("SELECT 1 FROM file_chunks WHERE sha256 = ? AND stored = 1 " +
                                     "AND state != 'deleting'", sha256).toArray().length > 0;
      if (!found) {
        throw new FileFault("not_found", CHUNK_GONE);
      }
      return sha256;
    });
  }

  /**
   * A key rotation's sweep for this owner (`files/src/datakey.ts`): its wrapped data key
   * wrapped again under the ring's active kid and kept, then each manifest root of `days`
   * wrapped again in place, so no copy of the key under a retiring kid outlives the sweep,
   * an idle owner's included. Whether the object's copy changed, and the roots found.
   */
  async rewrap(days: readonly string[]): Promise<{ rewrapped: boolean; roots: number }> {
    const { ns, data_key: held } = this.#space();
    if (ns === null || held === null) {
      return { rewrapped: false, roots: 0 };  // nothing was ever stored under a key
    }
    const { key } = await this.#store.rewrapKey(ns, held);
    if (key !== held) {
      await this.#write(() => this.#sql.exec(
        "UPDATE file_space SET data_key = ? WHERE data_key = ?", key, held));
    }
    let roots = 0;
    for (const day of days) {
      roots += await this.#store.rewrapManifestRoot(ns, day) === null ? 0 : 1;
    }
    return { rewrapped: key !== held, roots };
  }

  /** Chunk bytes in order, fetched from `FILES` at most two chunks a call. */
  async fetch(reads: readonly { sha256: string; offset?: number; length?: number;
                                 verify?: boolean }[]): Promise<Uint8Array[]> {
    if (reads.length === 0) {
      return [];
    }
    const { ns, key } = await this.space();
    const groups = batches(reads.map((read) => ({ read, size: read.length ?? CHUNK_BYTES })));
    const out: Uint8Array[] = [];
    for (const group of groups) {
      out.push(...await this.#store.getChunks(ns, key, group.map((item) => item.read)));
    }
    return out;
  }

  /** A path's kept history, newest first, before version `before`, and the next cursor. */
  history(path: unknown, before: unknown, limit: unknown):
      { changes: Change[]; next: number | null } {
    const at = storedPath(path);
    const most = count(limit, "limit", 1, MAX_LIST);
    const until = before === null ? Number.MAX_SAFE_INTEGER
      : count(before, "before", 1, Number.MAX_SAFE_INTEGER);
    const rows = this.#sql.exec<Change>(
      "SELECT version, path, op, kind, content_id, size, mode, writer, at FROM file_history " +
        "WHERE path = ? AND version < ? ORDER BY version DESC LIMIT ?", at, until, most + 1,
    ).toArray();
    const page = rows.slice(0, most);
    return { changes: page, next: rows.length > most ? page.at(-1)!.version : null };
  }

  /**
   * The workspace's changes after `after`, in order, and the next cursor. `reset` is true
   * when history the cursor still needed has been pruned: the reader rescans instead.
   */
  changes(after: unknown, limit: unknown): { changes: Change[]; cursor: number; reset: boolean } {
    const from = count(after, "after", 0, Number.MAX_SAFE_INTEGER);
    const most = count(limit, "limit", 1, MAX_LIST);
    const space = this.#space();
    const rows = this.#sql.exec<Change>(
      "SELECT version, path, op, kind, content_id, size, mode, writer, at FROM file_history " +
        "WHERE version > ? AND version <= ? ORDER BY version LIMIT ?", from, space.seq, most,
    ).toArray();
    return {
      changes: rows,
      cursor: rows.length === most ? rows.at(-1)!.version : space.seq,
      reset: from < space.feed_floor,
    };
  }

  /**
   * The owner's counters (the object's real storage) and limits, and the caller's own
   * scope's: an outside client's share, or the owner's own writers'.
   */
  usage(writer = "owner"): Usage {
    const { entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes, stored,
            intents, intent_chunks } = this.#space();
    return { entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes, stored,
             intents, intent_chunks, limits: this.#limits(),
             share: this.#share(scopesOf(writer).at(-1)!) };
  }

  // -- WRITING ------------------------------------------------------------------

  /**
   * Stamp the next version and, for a workspace path, append its history row; return
   * the version. The temporary area keeps no history.
   */
  #record(path: string, op: string, kind: FileKind | null, contentId: string | null,
          size: number, mode: number, writer: string, now: number): number {
    const version = this.#sql.exec<{ seq: number }>(
      "UPDATE file_space SET seq = seq + 1 RETURNING seq").one().seq;
    if (within(path, TEMP)) {
      return version;
    }
    this.#sql.exec(
      "INSERT INTO file_history (version, path, op, kind, content_id, size, mode, writer, at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      version, path, op, kind, contentId, size, mode, writer, now,
    );
    this.#retain(path, kind !== null);
    this.#changed(path, kind === "file", now, { writer, content_id: contentId, size });
    return version;
  }

  /**
   * Apply retention to `path` after a change. Its newest `keep` versions (none once it is
   * gone) are kept; the next ones, up to `keep_recent` in all, are due `retention_days`
   * after they were written; any older one goes now. So a path holds at most
   * `max(keep_versions, keep_recent)` rows, and every live path's history fits in
   * `max_history` (policy.ts). On a change only the newest rows move between those
   * bands, so this looks no further than the most rows policy can keep; a policy change
   * re-marks every row through housekeeping (`#remark`). A gone path's rows are all
   * `released`, which a live path's never are: retention has let them go, so a change at
   * the history quota may prune them early (`#fit`); a path made again takes them back.
   */
  #retain(path: string, live: boolean): void {
    const limits = this.#limits();
    const keep = live ? limits.keep_versions : 0;
    const cap = Math.max(keep, limits.keep_recent);
    const released = live ? 0 : 1;
    const newest = "SELECT version FROM file_history WHERE path = ? ORDER BY version DESC " +
      "LIMIT ? OFFSET ?";
    if (keep > 0) {
      this.#sql.exec("UPDATE file_history SET prune_after = NULL, released = 0 WHERE " +
                       `(prune_after IS NOT NULL OR released = 1) AND version IN (${newest})`,
                     path, keep, 0);
    }
    if (cap > keep) {
      this.#sql.exec("UPDATE file_history SET prune_after = coalesce(prune_after, at + ?), " +
                       "released = ? WHERE (prune_after IS NULL OR released != ?) AND " +
                       `version IN (${newest})`, limits.retention_days * 86_400, released,
                     released, path, cap - keep, keep);
    }
    this.#sql.exec(`DELETE FROM file_history WHERE version IN (${newest})`, path, KEEP_CEILING,
                   cap);
  }

  /** Every row of `path` marked, or dropped, as the policy in force keeps it. */
  #remark(path: string, limits: FileLimits): void {
    const live = this.#entry(path) !== undefined;
    const keep = live ? limits.keep_versions : 0;
    const cap = Math.max(keep, limits.keep_recent);
    const days = limits.retention_days * 86_400;
    const newest = "SELECT version FROM file_history WHERE path = ? ORDER BY version DESC LIMIT ?";
    this.#sql.exec(`DELETE FROM file_history WHERE path = ? AND version NOT IN (${newest})`,
                   path, path, cap);
    this.#sql.exec(`UPDATE file_history SET prune_after = NULL WHERE path = ? ` +
                   `AND prune_after IS NOT NULL AND version IN (${newest})`, path, path, keep);
    this.#sql.exec(`UPDATE file_history SET prune_after = at + ? WHERE path = ? ` +
                   `AND version NOT IN (${newest}) AND (prune_after IS NULL ` +
                   `OR prune_after != at + ?)`, days, path, path, keep, days);
  }

  #upsert(path: string, kind: FileKind, version: number, contentId: string | null, size: number,
          mode: number, writer: string, now: number, mtimeMs: number | null = null): StoredEntry {
    return this.#sql.exec<StoredEntry>(
      "INSERT INTO file_entries (parent, name, kind, version, content_id, size, mode, " +
        "mtime_ms, writer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (parent, name) DO " +
        "UPDATE SET kind = excluded.kind, version = excluded.version, " +
        "content_id = excluded.content_id, size = excluded.size, mode = excluded.mode, " +
        "mtime_ms = excluded.mtime_ms, writer = excluded.writer RETURNING *",
      parentOf(path), nameOf(path), kind, version, contentId, size, mode,
      mtimeMs ?? Math.floor(now * 1000), writer,
    ).one();
  }

  /** The compare-and-swap: `expected` null passes, `""` needs no entry, else its etag. */
  #swap(current: StoredEntry | undefined, expected: string | null): void {
    if (expected !== null && (current === undefined ? "" : etag(current)) !== expected) {
      throw new FileConflict(current === undefined ? null : shown(current));
    }
  }

  /**
   * The temporary area holds only what `tempDir` made, so every temporary entry sits in
   * a directory that expires.
   */
  #temporary(path: string): void {
    if (within(path, TEMP) && this.#sql.exec("SELECT 1 FROM file_temps WHERE name = ?",
                                             path.split("/")[3]).toArray().length === 0) {
      throw new Refusal("invalid", "the temporary area holds only temporary directories");
    }
  }

  /** Each missing ancestor made a directory (`parents`), or refused as missing. */
  #parents(path: string, parents: boolean, writer: string, now: number): void {
    for (const dir of ancestors(path)) {
      const found = this.#entry(dir);
      if (found?.kind === "file") {
        throw new FileFault("not_directory", "a parent is not a directory");
      }
      if (found === undefined) {
        if (!parents) {
          throw new FileFault("not_found", "no such parent directory");
        }
        this.#upsert(dir, "dir", this.#record(dir, "mkdir", "dir", null, 0, 0, writer, now),
                     null, 0, 0, writer, now);
      }
    }
  }

  /** Commit content as `path`'s next version, inside the caller's transaction. */
  #commit(path: string, expected: string | null, contentId: string, size: number,
          wanted: number | null, writer: string, op: string, parents: boolean, now: number,
          mtimeMs: number | null = null): Entry {
    if (isRoot(path)) {
      throw new FileFault("is_directory", "a root is a directory");
    }
    const current = this.#entry(path);
    this.#swap(current, expected);
    if (current?.kind === "dir") {
      throw new FileFault("is_directory", "is a directory");
    }
    this.#temporary(path);
    if (size > this.#limits().max_file_bytes) {
      throw new Refusal("file_quota", FILE_TOO_LARGE);
    }
    this.#parents(path, parents, writer, now);
    const mode = wanted ?? current?.mode ?? 0;
    const version = this.#record(path, op, "file", contentId, size, mode, writer, now);
    return shown(this.#upsert(path, "file", version, contentId, size, mode, writer, now,
                              mtimeMs));
  }

  /**
   * Content already stored under `contentId` must be this content: the same size and
   * kind, and the same bytes or chunk list. Anything else fails loudly, never reused.
   * Returns whether it was there.
   */
  #known(contentId: string, size: number, inline: Uint8Array | null,
         shas: readonly string[] | null): boolean {
    const row = this.#sql.exec<{ size: number; inline: ArrayBuffer | null }>(
      "SELECT size, inline FROM file_contents WHERE content_id = ?", contentId).toArray()[0];
    if (row === undefined) {
      return false;
    }
    const same = row.size === size && (inline === null ? row.inline === null
      : row.inline !== null && sha256Hex(new Uint8Array(row.inline)) === sha256Hex(inline))
      && (shas === null || this.#sql.exec<{ sha256: string }>(
        "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx", contentId)
        .toArray().map((r) => r.sha256).join() === shas.join());
    if (!same) {
      throw new Error("a content id names other stored content");
    }
    return true;
  }

  /** Small content kept inline: its id, the row made when new. */
  #inline(bytes: Uint8Array): string {
    const contentId = inlineId(bytes);
    if (!this.#known(contentId, bytes.byteLength, bytes, null)) {
      this.#sql.exec("INSERT INTO file_contents (content_id, size, inline) VALUES (?, ?, ?)",
                     contentId, bytes.byteLength, buffer(bytes));
    }
    return contentId;
  }

  /** Write small content (at most 4 KiB) in one transaction. */
  #writeInline(path: string, bytes: Uint8Array, expected: string | null, mode: number | null,
               writer: string, parents: boolean, op: string, mtimeMs: number | null = null):
      Entry {
    return this.#change(writer, (now) => this.#commit(
      path, expected, this.#inline(bytes), bytes.byteLength, mode, writer, op, parents, now,
      mtimeMs));
  }

  /**
   * Write small content (at most `INLINE_BYTES`) as `path` in the caller's own unit: one
   * transaction, no `FILES` call and no await, so a door that read the file in the same unit
   * writes exactly over what it read (`fileAppend`'s small case, which can never lose).
   */
  writeSmall(path: unknown, bytes: Uint8Array,
             options: { expected: unknown; mode?: unknown; writer: string;
                        mtime_ms?: number }): Entry {
    if (bytes.byteLength > INLINE_BYTES) {
      throw new Refusal("invalid", `a small write is at most ${INLINE_BYTES} bytes`);
    }
    return this.#writeInline(storedPath(path), bytes, expectedEtag(options.expected),
                             modeOf(options.mode), options.writer, true, "write",
                             mtimeOf(options.mtime_ms));
  }

  /**
   * Write bytes as `path`: inline, or chunked (intent, bytes to `FILES`, commit), the
   * object's own writes and the doors' small ones, at most `WHOLE_BYTES` at once.
   * `prefix` is whole chunks of content already stored that come first (an append or a
   * truncate keeps them), so only the chunks after them are cut from `bytes` and sent: the
   * caller read them (the environment, under the conversation's read access), so they
   * count as the writer's to hold without being sent again.
   * Called outside a unit: each step that writes (the inline write, or the intent, each
   * put's record and the commit or abort) is a unit of its own, between the calls.
   * `authorize`, a door's check of the caller's authority, runs first in the inline
   * write's, the intent's and the commit's unit, so the authority holds where it is used,
   * not only where the door first read it. `op` names the change in the path's history
   * (`write`, or `starter` for the starter package's own writes, `starter.ts`).
   */
  async write(path: unknown, bytes: unknown, options: { expected: unknown; mode?: unknown;
              writer: string; parents?: boolean; prefix?: readonly ChunkRef[];
              authorize?: () => void; op?: "write" | "starter" }):
      Promise<Entry> {
    const at = storedPath(path);
    if (!(bytes instanceof Uint8Array)) {
      throw new Refusal("invalid", "content is bytes");
    }
    if (bytes.byteLength > WHOLE_BYTES) {
      throw new Refusal("invalid",
                        `a whole write moves at most ${WHOLE_BYTES} bytes; upload larger files`);
    }
    const expected = expectedEtag(options.expected);
    const mode = modeOf(options.mode);
    const parents = options.parents ?? true;
    const prefix = options.prefix ?? [];
    if (prefix.some((chunk) => chunk.size !== CHUNK_BYTES)) {
      throw new Refusal("invalid", "a kept prefix is whole chunks");
    }
    const size = prefix.length * CHUNK_BYTES + bytes.byteLength;
    const authorize = options.authorize ?? (() => undefined);
    const op = options.op ?? "write";
    if (size <= INLINE_BYTES) {
      return this.#write(() => {
        authorize();
        return this.#writeInline(at, bytes, expected, mode, options.writer, parents, op);
      });
    }
    const tail = cut(bytes);
    const { intent_id, missing } = await this.#write(() => {
      authorize();
      return this.begin(size, [...prefix, ...tail].map((chunk) => chunk.sha256), options.writer,
                        prefix.map((chunk) => chunk.sha256));
    });
    try {
      const wanted = new Set(missing);
      const puts = tail.filter((chunk) => wanted.delete(chunk.sha256));
      if (wanted.size > 0) {
        throw new Refusal("refused", "a kept chunk of this file is gone; write it whole");
      }
      for (const group of batches(puts)) {
        await this.#put(group, intent_id);
      }
      return await this.#write(() => {
        authorize();
        return this.commit(intent_id, at, { expected, mode, writer: options.writer, parents,
                                            op });
      });
    } catch (error) {
      await this.#write(() => this.abort(intent_id));
      throw error;
    }
  }

  /**
   * Write a file of `size` bytes whose chunks are `hashes` (cut at 4 MiB), made one at a time
   * by `make` (a chunk-wise edit, `edit.ts`): only the chunks the files plane lacks are made
   * and sent, each checked against its hash; the chunks `readable` names were read by the
   * caller (its old file's), so they are kept, never sent again. Each chunk is made and put
   * in a step of its own (`hold`, the caller's making units, ADR-037), its bytes gone as the
   * step ends; the begin and the commit hold none.
   */
  async writeChunks(path: unknown, size: number, hashes: readonly string[],
                    make: (index: number) => Promise<Uint8Array>,
                    options: { expected: unknown; mode?: unknown; writer: string;
                               readable: readonly string[]; authorize?: () => void;
                               hold?: <T>(work: () => Promise<T>) => Promise<T> }):
      Promise<Entry> {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    const mode = modeOf(options.mode);
    const authorize = options.authorize ?? (() => undefined);
    const { intent_id, missing } = await this.#write(() => {
      authorize();
      return this.begin(size, hashes, options.writer, options.readable);
    });
    try {
      const hold = options.hold ?? ((work) => work());
      for (const sha256 of new Set(missing)) {
        await hold(async () => {
          const bytes = await make(hashes.indexOf(sha256));
          await this.#put([{ sha256, bytes }], intent_id);
          letGo(bytes);  // sent: its step ends holding nothing of it
        });
      }
      return await this.#write(() => {
        authorize();
        return this.commit(intent_id, at, { expected, mode, writer: options.writer,
                                            parents: true, op: "write" });
      });
    } catch (error) {
      await this.#write(() => this.abort(intent_id));
      throw error;
    }
  }

  /**
   * Put chunks through `FILES`, then, in a unit once it accepted them, record them stored
   * and sent for `intentId`: its writer has shown it holds their bytes.
   */
  async #put(chunks: readonly { sha256: string; bytes: Uint8Array }[], intentId: string):
      Promise<void> {
    // No byte goes to R2 once the erasure's mark stands; one already sent is waited for.
    const ns = await this.#write(() => {
      refuseErasing(this.#storage);
      return this.ns();
    });
    const { key } = await this.space();
    const put = this.#store.putChunks(ns, key,
                                      chunks.map(({ sha256, bytes }) => ({ sha256, bytes })));
    this.#puts.add(put);
    try {
      await put;
    } finally {
      this.#puts.delete(put);
    }
    await this.#write(() => this.#recordStored(new Set(chunks.map(({ sha256 }) => sha256)),
                                               intentId));
  }

  /** In a unit: chunks `FILES` accepted for an intent, recorded stored and sent. */
  #recordStored(sent: ReadonlySet<string>, intentId: string): void {
    this.#storage.transactionSync(() => {
      for (const sha256 of sent) {
        this.#sql.exec("UPDATE file_chunks SET stored = 1 WHERE sha256 = ? AND stored = 0",
                       sha256);
      }
      const row = this.#sql.exec<{ pending: string }>(
        "SELECT pending FROM file_intents WHERE intent_id = ?", intentId).toArray()[0];
      if (row !== undefined) {
        this.#sql.exec("UPDATE file_intents SET pending = ? WHERE intent_id = ?",
                       hashes(row.pending).filter((sha256) => !sent.has(sha256)).join(""),
                       intentId);
      }
    });
  }

  /**
   * An open upload's chunk plan, each chunk's hash and size in order, and the chunks it
   * still has to send: what the binder streams a file's bytes to `FILES` by (tasks 6.1).
   */
  uploadPlan(intentId: unknown): { chunks: { sha256: string; size: number }[];
                                   pending: string[] } {
    const intent = this.#intent(intentId, Date.now() / 1000);
    const plan = chunkPlan(intent.size);
    return { chunks: intent.chunks.map((sha256, index) => ({ sha256, size: plan[index]! })),
             pending: intent.pending };
  }

  /**
   * In a unit: the binder's word that `FILES` accepted these of an open upload's chunks,
   * each put verified by its hash (the binder streams them itself), recorded as `upload`
   * records its own puts. Only the intent's own chunks are taken.
   */
  stored(intentId: unknown, chunks: unknown): { stored: number } {
    const intent = this.#intent(intentId, Date.now() / 1000);
    const held = new Set(intent.chunks);
    if (!Array.isArray(chunks) || chunks.length === 0 || chunks.length > MAX_INTENT_CHUNKS
        || chunks.some((sha256) => typeof sha256 !== "string" || !held.has(sha256))) {
      throw new Refusal("invalid", "a stored report names only its intent's chunks");
    }
    const sent = new Set(chunks as string[]);
    this.#recordStored(sent, intentId as string);
    return { stored: sent.size };
  }

  /**
   * A read for the cutover's check (`import.ts`, `importCheck`): `limit` entries from the
   * `offset`th, in key order, each as the files plane lists it (its mtime too).
   */
  entriesPage(offset: number, limit: number): { path: string; kind: FileKind; size: number;
                                               mode: 0 | 1; content_id: string | null;
                                               mtime_ms: number }[] {
    return this.#sql.exec<StoredEntry>(
      "SELECT * FROM file_entries ORDER BY parent, name LIMIT ? OFFSET ?", limit, offset)
      .toArray().map((row) => ({
        path: row.parent === "/" ? `/${row.name}` : `${row.parent}/${row.name}`, kind: row.kind,
        size: row.size, mode: row.mode === 1 ? 1 : 0, content_id: row.content_id,
        mtime_ms: row.mtime_ms }));
  }

  /**
   * A read for the cutover's check (`importCheck`): `limit` of the chunks the owner's contents
   * name (its files' and its placed bodies'), from the `offset`th by hash, with their sizes.
   */
  chunksPage(offset: number, limit: number): { sha256: string; size: number }[] {
    return this.#sql.exec<{ sha256: string; size: number }>(
      "SELECT DISTINCT c.sha256, k.size FROM file_content_chunks c JOIN file_chunks k " +
        "ON k.sha256 = c.sha256 ORDER BY c.sha256 LIMIT ? OFFSET ?", limit, offset)
      .toArray();
  }

  /**
   * A read: the open uploads, and the live one of `writer` that is a file of `size` bytes
   * made of exactly `chunks`, if any. The cutover's import resumes that upload rather than
   * begin another (`import.ts`), so a delivery killed mid-file leaves no second intent.
   */
  openUploads(size: number, chunks: readonly string[], writer: string):
      { open: number; same: string | null } {
    const now = Date.now() / 1000;
    const open = this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM file_intents WHERE expires_at > ?", now).one().n;
    const same = this.#sql.exec<{ intent_id: string }>(
      "SELECT intent_id FROM file_intents WHERE writer = ? AND size = ? AND chunks = ? " +
        "AND expires_at > ? AND lease IS NULL ORDER BY expires_at DESC LIMIT 1",
      writer, size, chunks.join(""), now).toArray()[0];
    return { open, same: same?.intent_id ?? null };
  }

  /**
   * Whether `scope` holds a chunk's bytes for its own: through its contents (its
   * references but its open uploads'), or as the garbage it let go last. An open upload
   * holds a chunk without showing its bytes, so it is no proof.
   */
  #possessed(scope: string, sha256: string, uploading: ReadonlyMap<string, number>): boolean {
    const sql = this.#sql;
    const refs = sql.exec<{ refs: number }>(
      "SELECT refs FROM file_scope_chunks WHERE scope = ? AND sha256 = ?", scope, sha256)
      .toArray()[0]?.refs ?? 0;
    // An open upload holds a chunk without showing its bytes: its references are no proof.
    return refs > (uploading.get(sha256) ?? 0)
      || sql.exec("SELECT 1 FROM file_chunks WHERE sha256 = ? AND state = 'garbage' " +
                    "AND holder = ?", sha256, scope).toArray().length > 0;
  }

  /** How many of `scope`'s open uploads hold each chunk, each chunk once an upload. */
  #uploading(scope: string): Map<string, number> {
    const counts = new Map<string, number>();
    for (const { chunks } of this.#sql.exec<{ chunks: string }>(
           "SELECT chunks FROM file_intents WHERE writer = ? OR (? = ? AND writer NOT GLOB ?)",
           scope, scope, OWNER_SCOPE, CLIENT_WRITER)) {
      for (const sha256 of new Set(hashes(chunks))) {
        counts.set(sha256, (counts.get(sha256) ?? 0) + 1);
      }
    }
    return counts;
  }

  /** In a unit: count (`1`) or take out (`-1`) a scope reference of `writer`'s to a chunk. */
  #chunkRef(writer: string, sha256: string, by: 1 | -1): void {
    for (const scope of scopesOf(writer)) {
      if (by === 1) {
        this.#sql.exec("INSERT INTO file_scope_chunks (scope, sha256, refs) VALUES (?, ?, 1) " +
                         "ON CONFLICT (scope, sha256) DO UPDATE SET refs = refs + 1",
                       scope, sha256);
      } else {
        this.#sql.exec("UPDATE file_scope_chunks SET refs = refs - 1 WHERE scope = ? " +
                         "AND sha256 = ?", scope, sha256);
      }
    }
  }

  /**
   * Record an upload of a file of `size` bytes made of `chunks`, cut at 4 MiB: each chunk
   * held, and the ones whose bytes must be sent. `writer` is whose upload it is: its
   * scope holds each chunk while the upload is open, charged for it once whatever else
   * holds it. A chunk is not sent again only when it is stored and the writer's own scope
   * already holds it, or let it go last and it waits as garbage, or the caller read it
   * (`readable`): content stored by another scope
   * is never the writer's without its bytes, and an upload never shows what another
   * stored. The chunks to send are the upload's to send before it commits. One being
   * deleted is refused, to begin again. An outside client holds at most its share of the
   * open uploads and the chunks they hold, all clients together at most what the owner's
   * reserve leaves. Every bound is checked here, before any byte is sent.
   */
  begin(size: unknown, chunks: unknown, writer: string, readable: readonly string[] = [],
        placing = false, lease: string | null = null, pastFigure = false):
      { intent_id: string; missing: string[]; expires_at: number } {
    refuseErasing(this.#storage);
    // A placed body (ADR-035) is no file: any size past its threshold, its own short life.
    const bytes = count(size, "size", placing ? 1 : INLINE_BYTES + 1, Number.MAX_SAFE_INTEGER);
    if (!placing && bytes > this.#limits().max_file_bytes) {
      throw new Refusal("file_quota", FILE_TOO_LARGE);
    }
    const plan = chunkPlan(bytes);
    if (plan.length > MAX_INTENT_CHUNKS) {
      throw new Refusal("invalid", `an upload holds at most ${MAX_INTENT_CHUNKS} chunks`);
    }
    if (!Array.isArray(chunks) || chunks.length !== plan.length
        || chunks.some((sha) => typeof sha !== "string" || !HEX64.test(sha))) {
      throw new Refusal("invalid", "an upload names a 64-hex hash for each 4 MiB of the file");
    }
    const wanted = new Map<string, number>();
    (chunks as string[]).forEach((sha256, index) => {
      if ((wanted.get(sha256) ?? plan[index]) !== plan[index]) {
        throw new Refusal("invalid", "a chunk hash names one size");
      }
      wanted.set(sha256, plan[index]!);
    });
    const intentId = randomHex(INTENT_ID_BYTES);
    const outerPast = this.#pastFigure;
    this.#pastFigure = pastFigure;
    try {
    return this.#change(writer, (now) => {
      const sql = this.#sql;
      const space = this.#space();
      if (space.intents >= MAX_OPEN_INTENTS || space.intent_chunks + plan.length > MAX_HELD_CHUNKS) {
        throw new Refusal("file_quota",
                          `${FILE_QUOTA}: too many uploads are open; commit or abort one first`);
      }
      this.#uploadShare(writer, plan.length);
      const own = scopesOf(writer).at(-1)!;
      const known = new Set(readable);
      const uploading = this.#uploading(own);
      const missing: string[] = [];
      for (const [sha256, chunkSize] of wanted) {
        const row = sql.exec<{ state: string; stored: number; size: number }>(
          "SELECT state, stored, size FROM file_chunks WHERE sha256 = ?", sha256).toArray()[0];
        if (row !== undefined && row.size !== chunkSize) {
          throw new Refusal("invalid", "a chunk hash names one size");
        }
        const held = this.#possessed(own, sha256, uploading);
        if (row === undefined) {
          sql.exec("INSERT INTO file_chunks (sha256, size, state, stored, holds, holder) " +
                     "VALUES (?, ?, 'uploading', 0, 1, ?)", sha256, chunkSize, own);
          this.#moved += chunkSize;
        } else if (row.state === "deleting") {
          throw new Refusal("chunk_deleting", CHUNK_DELETING);
        } else if (row.state === "garbage") {
          sql.exec("UPDATE file_chunks SET state = 'uploading', holds = 1, gc_after = NULL " +
                     "WHERE sha256 = ?", sha256);
          this.#moved += chunkSize;
        } else {
          sql.exec("UPDATE file_chunks SET holds = holds + 1 WHERE sha256 = ?", sha256);
        }
        this.#chunkRef(writer, sha256, 1);
        if (row === undefined || !row.stored || !(held || known.has(sha256))) {
          missing.push(sha256);
        }
      }
      const expiresAt = now + (placing ? PLACE_TTL_S : INTENT_TTL_S);
      sql.exec("INSERT INTO file_intents (intent_id, size, chunks, expires_at, writer, " +
                 "pending, lease) VALUES (?, ?, ?, ?, ?, ?, ?)", intentId, bytes,
               (chunks as string[]).join(""), expiresAt, writer, missing.join(""), lease);
      return { intent_id: intentId, missing, expires_at: expiresAt };
    });
    } finally {
      this.#pastFigure = outerPast;
    }
  }

  /**
   * Refuse an outside client's upload past its share of the open uploads and the chunks
   * they hold, or all clients' past what the owner's reserve leaves; the owner's own are
   * held to the whole ceilings alone.
   */
  #uploadShare(writer: string, chunks: number): void {
    if (!isClient(writer)) {
      return;
    }
    const limits = this.#limits();
    const open = (where: string, who: string) => this.#sql.exec<{ n: number; held: number }>(
      "SELECT COUNT(*) AS n, coalesce(SUM(length(chunks) / 64), 0) AS held FROM file_intents " +
        `WHERE ${where}`, who).one();
    const mine = open("writer = ?", writer);
    if (mine.n + 1 > shareOf(MAX_OPEN_INTENTS, limits.client_share)
        || mine.held + chunks > shareOf(MAX_HELD_CHUNKS, limits.client_share)) {
      throw new Refusal("file_share",
                        `${FILE_SHARE}: this client's open uploads are at its share; ` +
                        "commit or abort one first");
    }
    const theirs = open("writer GLOB ?", CLIENT_WRITER);
    const percent = 100 - limits.owner_reserve;
    if (theirs.n + 1 > shareOf(MAX_OPEN_INTENTS, percent)
        || theirs.held + chunks > shareOf(MAX_HELD_CHUNKS, percent)) {
      throw new Refusal("file_share",
                        `${FILE_SHARE}: outside clients' open uploads are at their share; ` +
                        "the rest is kept for the owner");
    }
  }

  /** A live intent, or refused. */
  #intent(intentId: unknown, now: number): Intent {
    if (typeof intentId !== "string" || !HEX32.test(intentId)) {
      throw new Refusal("invalid", "an intent id is 32 lowercase hex characters");
    }
    const row = this.#sql.exec<{ size: number; chunks: string; expires_at: number;
                                 pending: string; writer: string }>(
      "SELECT size, chunks, expires_at, pending, writer FROM file_intents WHERE intent_id = ?",
      intentId).toArray()[0];
    if (row === undefined || row.expires_at <= now) {
      throw new Refusal("refused", "no such upload: it was committed, abandoned or has expired");
    }
    return { size: row.size, chunks: hashes(row.chunks), pending: hashes(row.pending),
             writer: row.writer };
  }

  /**
   * End an intent: its writer's scope references go, then its holds, and chunks nothing
   * else holds become garbage, held by the scope that let them go last.
   */
  #release(intentId: string): void {
    const sql = this.#sql;
    const row = sql.exec<{ chunks: string; writer: string }>(
      "DELETE FROM file_intents WHERE intent_id = ? RETURNING chunks, writer", intentId)
      .toArray()[0];
    for (const sha256 of new Set(hashes(row?.chunks ?? ""))) {
      this.#chunkRef(row!.writer, sha256, -1);
      sql.exec("UPDATE file_chunks SET holds = holds - 1 WHERE sha256 = ?", sha256);
    }
  }

  /** Abandon an upload now rather than at its expiry; a gone one is no error. */
  abort(intentId: unknown): { aborted: boolean } {
    if (typeof intentId !== "string" || !HEX32.test(intentId)) {
      throw new Refusal("invalid", "an intent id is 32 lowercase hex characters");
    }
    return this.#change(null, () => {
      const held = this.#sql.exec("SELECT 1 FROM file_intents WHERE intent_id = ?", intentId)
        .toArray().length > 0;
      this.#release(intentId);
      return { aborted: held };
    });
  }

  /**
   * Send bytes for chunks an intent holds, at most two chunks a call, each the size the
   * intent declared; each is recorded stored, in a unit, once `FILES` accepted it.
   */
  async upload(intentId: unknown, chunks: unknown): Promise<{ created: number }> {
    const held = new Set(this.#intent(intentId, Date.now() / 1000).chunks);
    if (!Array.isArray(chunks) || chunks.length === 0) {
      throw new Refusal("invalid", "an upload sends a list of chunks");
    }
    const puts = (chunks as unknown[]).map((chunk) => {
      const { sha256, bytes } = (chunk ?? {}) as Record<string, unknown>;
      if (typeof sha256 !== "string" || !held.has(sha256)) {
        throw new Refusal("invalid", "an upload sends only its intent's chunks");
      }
      const size = this.#sql.exec<{ size: number }>(
        "SELECT size FROM file_chunks WHERE sha256 = ?", sha256).one().size;
      if (!(bytes instanceof Uint8Array) || bytes.byteLength !== size) {
        throw new Refusal("invalid", "a chunk's bytes are the size its upload declared");
      }
      return { sha256, bytes, size };
    });
    if (puts.reduce((sum, put) => sum + put.size, 0) > BATCH_BYTES) {
      throw new Refusal("invalid", `an upload sends at most ${BATCH_BYTES} bytes at once`);
    }
    await this.#put(puts, intentId as string);
    return { created: puts.length };
  }

  /**
   * The content an intent declared, inside the caller's transaction: every chunk stored,
   * made live, and the content row made or checked. A chunk `FILES` never accepted is
   * refused: no commit can claim bytes the bucket does not hold, nor a writer bytes it
   * was asked to send and did not.
   */
  #fromIntent(intent: Intent): { contentId: string; size: number } {
    const sql = this.#sql;
    for (const sha256 of new Set(intent.chunks)) {
      const row = sql.exec<{ stored: number }>(
        "SELECT stored FROM file_chunks WHERE sha256 = ?", sha256).toArray()[0];
      if (row?.stored !== 1) {
        throw new Refusal("refused",
                          "a chunk of this commit was never stored; send its bytes first");
      }
      sql.exec("UPDATE file_chunks SET state = 'live' WHERE sha256 = ? AND state = 'uploading'",
               sha256);
    }
    // Each chunk this upload was to send it sent, or its writer's scope holds it now.
    const own = scopesOf(intent.writer).at(-1)!;
    const uploading = this.#uploading(own);
    if (intent.pending.some((sha256) => !this.#possessed(own, sha256, uploading))) {
      throw new Refusal("refused",
                        "a chunk of this commit was not sent by its upload; send its bytes " +
                        "first");
    }
    const contentId = chunkedId(intent.size, intent.chunks);
    if (!this.#known(contentId, intent.size, null, intent.chunks)) {
      sql.exec("INSERT INTO file_contents (content_id, size) VALUES (?, ?)", contentId,
               intent.size);
      intent.chunks.forEach((sha256, index) => sql.exec(
        "INSERT INTO file_content_chunks (content_id, idx, sha256) VALUES (?, ?, ?)",
        contentId, index, sha256));
    }
    return { contentId, size: intent.size };
  }

  /** Commit an upload as `path`'s next version; the intent ends with it. */
  commit(intentId: unknown, path: unknown, request: { expected: unknown; mode?: unknown;
         writer: string; parents?: boolean; op?: "write" | "starter"; mtime_ms?: number }):
      Entry {
    const at = storedPath(path);
    const expected = expectedEtag(request.expected);
    const mode = modeOf(request.mode);
    return this.#change(request.writer, (now) => {
      const { contentId, size } = this.#fromIntent(this.#intent(intentId, now));
      const entry = this.#commit(at, expected, contentId, size, mode, request.writer,
                                 request.op ?? "write", request.parents ?? true, now,
                                 mtimeOf(request.mtime_ms));
      this.#release(intentId as string);
      return entry;
    });
  }

  /**
   * In the caller's unit, a placed body's adoption (ADR-035, `bodies.ts`): the upload's
   * content made (its chunks stored, made live), the intent ended, and `name` run with the
   * content's id, the caller's row naming it, whose own triggers count the reference, in
   * its writer's scope too. Held to every quota as any change is, after the row, in the
   * same transaction, so a refused or conflicting row leaves the content unmade and the
   * intent open for its release.
   */
  adopt<T>(intentId: string, writer: string, name: (contentId: string) => T): T {
    return this.#change(writer, (now) => {
      const { contentId } = this.#fromIntent(this.#intent(intentId, now));
      this.#release(intentId);
      return name(contentId);
    });
  }

  /** Make a directory; an existing one is no error unless `expected` says otherwise. */
  mkdir(path: unknown, options: { expected: unknown; parents?: boolean; writer: string }):
      Entry {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    return this.#change(options.writer, (now) => this.#mkdir(
      at, expected, options.parents ?? true, options.writer, now));
  }

  #mkdir(path: string, expected: string | null, parents: boolean, writer: string,
         now: number): Entry {
    if (isRoot(path)) {
      if (expected !== null) {
        throw new Refusal("invalid", "a root is never written");
      }
      return rootEntry(path);
    }
    const current = this.#entry(path);
    this.#swap(current, expected);
    if (current?.kind === "file") {
      throw new FileFault("exists", "a file is in the way");
    }
    if (current !== undefined) {
      return shown(current);
    }
    this.#temporary(path);
    this.#parents(path, parents, writer, now);
    return shown(this.#upsert(path, "dir", this.#record(path, "mkdir", "dir", null, 0, 0,
                                                        writer, now),
                              null, 0, 0, writer, now));
  }

  /**
   * Remove a file, or a directory and everything below it (`recursive`). Never refused
   * for a quota: it only frees.
   */
  remove(path: unknown, options: { expected: unknown; recursive?: boolean; writer: string }):
      { removed: number } {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    return this.#change(options.writer, (now) => this.#remove(
      at, expected, options.recursive ?? false, options.writer, now), true);
  }

  #remove(path: string, expected: string | null, recursive: boolean, writer: string,
          now: number): { removed: number } {
    if (isRoot(path)) {
      throw new Refusal("invalid", "a root is never removed");
    }
    const current = this.#entry(path);
    if (current === undefined && expected === null) {
      throw new FileFault("not_found", "no such file or directory");
    }
    this.#swap(current, expected);
    if (current === undefined) {
      throw new FileFault("not_found", "no such file or directory");
    }
    const below = current.kind === "dir" ? this.#below(path) : [];
    if (current.kind === "dir" && !recursive) {
      throw new FileFault(below.length ? "not_empty" : "is_directory",
                          "a directory is removed with recursive");
    }
    for (const entry of [...below, current]) {
      this.#drop(pathOf(entry), "delete", writer, now);
    }
    return { removed: below.length + 1 };
  }

  #drop(path: string, op: string, writer: string, now: number): void {
    this.#sql.exec("DELETE FROM file_entries WHERE parent = ? AND name = ?", parentOf(path),
                   nameOf(path));
    this.#record(path, op, null, null, 0, 0, writer, now);
  }

  /**
   * Move a file or a directory with everything below it, in one transaction; no byte
   * moves. `replace` lets a file replace a file, or a directory an empty directory, as
   * POSIX `rename` does; without it an existing destination is a conflict. A path renamed
   * onto itself changes nothing.
   */
  rename(from: unknown, to: unknown, options: { expected: unknown; replace: boolean;
         writer: string }): Entry {
    const source = storedPath(from);
    const target = storedPath(to);
    const expected = expectedEtag(options.expected);
    if (isRoot(source) || isRoot(target)) {
      throw new Refusal("invalid", "a root is never renamed");
    }
    if (source !== target && within(target, source)) {
      throw new Refusal("invalid", "a directory is never moved into itself");
    }
    return this.#change(options.writer, (now) => {
      const moving = this.#entry(source);
      if (moving === undefined && expected === null) {
        throw new FileFault("not_found", "no such file or directory");
      }
      this.#swap(moving, expected);
      if (moving === undefined) {
        throw new FileFault("not_found", "no such file or directory");
      }
      if (source === target) {
        return shown(moving);
      }
      const parent = this.stat(parentOf(target));
      if (parent === null) {
        throw new FileFault("not_found", "no such destination directory");
      }
      if (parent.kind !== "dir") {
        throw new FileFault("not_directory", "the destination's parent is not a directory");
      }
      this.#temporary(target);
      const there = this.#entry(target);
      if (there !== undefined) {
        if (!options.replace) {
          throw new FileConflict(shown(there));
        }
        if (moving.kind === "file" && there.kind === "dir") {
          throw new FileFault("is_directory", "a file never replaces a directory");
        }
        if (moving.kind === "dir" && there.kind === "file") {
          throw new FileFault("not_directory", "a directory never replaces a file");
        }
        if (there.kind === "dir" && this.#below(target).length > 0) {
          throw new FileFault("not_empty", "the destination directory is not empty");
        }
      }
      // The feed reads each source's leaving before its arrival, but the source entries
      // go last: each content is held by its new entry before its old one lets go, so a
      // move never leaves content unreferenced, in any root.
      const tree = [moving, ...(moving.kind === "dir" ? this.#below(source) : [])];
      for (const entry of tree) {
        this.#record(pathOf(entry), "rename_from", null, null, 0, 0, options.writer, now);
      }
      let moved: StoredEntry | undefined;
      for (const entry of tree) {
        const path = target + pathOf(entry).slice(source.length);
        const version = this.#record(path, "rename_to", entry.kind, entry.content_id,
                                     entry.size, entry.mode, options.writer, now);
        const made = this.#upsert(path, entry.kind, version, entry.content_id, entry.size,
                                  entry.mode, options.writer, now);
        moved ??= made;
      }
      for (const entry of tree) {
        this.#sql.exec("DELETE FROM file_entries WHERE parent = ? AND name = ?", entry.parent,
                       entry.name);
      }
      return shown(moved!);
    });
  }

  /** Write a kept version's content as the path's next version; nothing rewinds. */
  restore(path: unknown, version: unknown, options: { expected: unknown; writer: string }):
      Entry {
    const at = storedPath(path);
    const expected = expectedEtag(options.expected);
    return this.#change(options.writer, (now) => {
      const row = this.#version(at, version);
      if (row.kind !== "file" || row.content_id === null) {
        throw new Refusal("invalid", "that version holds no file content");
      }
      return this.#commit(at, expected, row.content_id, row.size, row.mode, options.writer,
                          "restore", true, now);
    });
  }

  /**
   * Drop one kept version's content (a secret written by mistake). The current version
   * is never redacted: write a new one first. Content is shared by every row that holds
   * the same bytes, so its bytes go only once no other kept row, the current file's
   * included, holds them; redact each such version to drop them all.
   */
  redact(path: unknown, version: unknown): { redacted: number } {
    const at = storedPath(path);
    return this.#change(null, () => {
      const row = this.#version(at, version);
      if (row.content_id === null) {
        throw new Refusal("invalid", "that version holds no content");
      }
      if (this.#entry(at)?.version === row.version) {
        throw new Refusal("refused",
                          "the current version is never redacted; write a new one first");
      }
      this.#sql.exec("UPDATE file_history SET content_id = NULL WHERE version = ?", row.version);
      return { redacted: row.version };
    });
  }

  /**
   * Drop every kept version of `path` but its current one: the owner's way out of a full
   * history, never refused for a quota. A gone path's history goes whole, and the feed's
   * floor rises past it.
   */
  purge(path: unknown): { purged: number } {
    const at = storedPath(path);
    return this.#change(null, () => {
      const current = this.#entry(at)?.version ?? 0;
      const gone = this.#sql.exec<{ version: number }>(
        "DELETE FROM file_history WHERE path = ? AND version != ? RETURNING version", at, current,
      ).toArray().map((row) => row.version);
      if (current === 0 && gone.length > 0) {
        this.#sql.exec("UPDATE file_space SET feed_floor = MAX(feed_floor, ?)", Math.max(...gone));
      }
      return { purged: gone.length };
    });
  }

  /** A new temporary directory under `/.ta/tmp`, expired after a day. */
  tempDir(prefix: unknown, writer: string): string {
    // A prefix and the 16 hex after it make one path component (`MAX_NAME_BYTES`).
    if (typeof prefix !== "string" || prefix.includes("/")
        || utf8Bytes(prefix) > MAX_NAME_BYTES - 16 || !isPrintable(prefix)) {
      throw new Refusal("invalid",
                        `a temporary prefix is at most ${MAX_NAME_BYTES - 16} bytes of ` +
                        "printable characters, no slash");
    }
    const name = `${prefix}${randomHex(8)}`;
    const path = `${TEMP}/${name}`;
    this.#change(writer, (now) => {
      this.#sql.exec("INSERT INTO file_temps (name, expires_at, writer) VALUES (?, ?, ?)", name,
                     now + TEMP_TTL_S, writer);
      this.#mkdir(path, "", false, writer, now);
    });
    return path;
  }

  /** Remove temporary directories now (an environment's `cleanup`). */
  dropTemps(paths: readonly string[], writer: string): void {
    this.#change(writer, (now) => {
      for (const path of paths) {
        this.#dropTemp(nameOf(path), writer, now);
      }
    });
  }

  #dropTemp(name: string, writer: string, now: number): void {
    this.#sql.exec("DELETE FROM file_temps WHERE name = ?", name);
    if (this.#entry(`${TEMP}/${name}`) !== undefined) {
      this.#remove(`${TEMP}/${name}`, null, true, writer, now);
    }
  }

  // -- THE SANDBOX LEASES ----------------------------------------------------------

  /**
   * Take `authority`'s sandbox lease, when it holds no live one: a new lease id and the
   * next epoch, bound to the SHA-256 hex of its three tokens' secrets (`egress`, `sync` and
   * the runner's; the secrets never stay in the object) and their deadline. Lapsed leases go
   * first (a lease is taken only after the runs under a lapsed one were settled), so the
   * leases are never more than the live runs. Its network is marked at `generation`, the
   * egress generation of the network the start that opens it carries (`Launcher.openLease`):
   * egress opens only while that, or a re-apply's, is the current one (`egress.ts`).
   */
  leaseAcquire(authority: unknown, holder: unknown, tokens: LeaseTokens,
               generation: number): Lease {
    if (typeof authority !== "string"
        || (authority !== OWNER_AUTHORITY && !/^[!-~]{64}$/.test(authority))) {
      throw new Refusal("invalid", "a lease's authority is the owner's or one outside client's");
    }
    if (typeof holder !== "string" || !holder || codePoints(holder) > 128
        || !isPrintable(holder)) {
      throw new Refusal("invalid", "a lease holder is 1 to 128 printable characters");
    }
    const hashes = [tokens.egress, tokens.sync, tokens.runner];
    if (hashes.some((hash) => typeof hash !== "string" || !HEX64.test(hash))
        || new Set(hashes).size !== 3) {
      throw new Refusal("invalid", "a lease's three token hashes are 64 hex, and differ");
    }
    return this.#change(null, (now) => {
      if (this.sandboxLease(now, authority) !== null) {
        throw new Refusal("refused", "the sync lease is held");
      }
      // A lapsed lease, or this authority's own last one, goes here on every path that
      // opens a lease (a run's or a sign-in's), and its sync's open uploads with it.
      for (const gone of this.#sql.exec<{ lease_id: string }>(
        "DELETE FROM sandbox_leases WHERE authority = ? OR NOT " +
          `(${LIVE_LEASE}) RETURNING lease_id`, authority, now).toArray()) {
        this.#endUploads(gone.lease_id);
      }
      const row = this.#sql.exec<{ epoch: number; seq: number }>(
        "UPDATE file_space SET epoch = epoch + 1 RETURNING epoch, seq").one();
      const leaseId = randomHex(16);
      this.#sql.exec(
        "INSERT INTO sandbox_leases (authority, lease_id, epoch, holder, lease_expires, " +
          "egress_token, sync_token, runner_token, network_generation) VALUES " +
          "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        authority, leaseId, row.epoch, holder, now + LEASE_TTL_S,
        tokens.egress, tokens.sync, tokens.runner, generation);
      return { lease_id: leaseId, epoch: row.epoch, expires_at: now + LEASE_TTL_S,
               base: row.seq };
    });
  }

  /** `authority`'s live lease, or null. A read. */
  sandboxLease(now: number, authority: string): SandboxLease | null {
    const row = this.#sql.exec<LeaseRow>(
      `SELECT * FROM sandbox_leases WHERE authority = ? AND ${LIVE_LEASE}`, authority, now)
      .toArray()[0];
    return row === undefined ? null : leaseOf(row);
  }

  /** The live lease one of whose tokens' hash is `hash`, or null. A read. */
  leaseByToken(kind: "egress" | "sync" | "runner", hash: string, now: number):
      SandboxLease | null {
    const row = this.#sql.exec<LeaseRow>(
      `SELECT * FROM sandbox_leases WHERE ${kind}_token = ? AND ${LIVE_LEASE}`, hash, now)
      .toArray()[0];
    return row === undefined ? null : leaseOf(row);
  }

  /** Every live lease. A read. */
  liveLeases(now: number): SandboxLease[] {
    return this.#sql.exec<LeaseRow>(
      `SELECT * FROM sandbox_leases WHERE ${LIVE_LEASE} ORDER BY epoch`, now).toArray()
      .map(leaseOf);
  }

  /**
   * A re-apply of the live lease's network sent under `generation` at `now`: its calls are
   * admitted under it from this unit on. Whether that lease, at that epoch, is still live.
   */
  networkSent(leaseId: string, epoch: number, generation: number, now: number): boolean {
    return this.#change(null, () => this.#sql.exec(
      "UPDATE sandbox_leases SET network_pending = ?, network_tried_at = ?, " +
        `network_tried_generation = ? WHERE lease_id = ? AND epoch = ? AND ${LIVE_LEASE}`,
      generation, now, generation, leaseId, epoch, now).rowsWritten > 0);
  }

  /**
   * A re-apply's answer, fenced by the lease: `applied` confirms its generation (never an
   * older one over a newer) and clears it from out; `at_resume` keeps it out (its sandbox
   * paused, the Worker applies it as it resumes), its retry clock from `now`; a failure
   * (`lost`, no answer) clears it, so its calls are refused again, and the lane tries again
   * once its retry clock passes.
   */
  networkAnswered(leaseId: string, epoch: number, generation: number,
                  answer: "applied" | "at_resume" | "failed", now: number): void {
    this.#change(null, () => {
      const fence = `WHERE lease_id = ? AND epoch = ? AND ${LIVE_LEASE}`;
      if (answer === "applied") {
        this.#sql.exec(
          "UPDATE sandbox_leases SET network_generation = MAX(COALESCE(network_generation, " +
            "0), ?), network_pending = CASE WHEN network_pending <= ? THEN NULL ELSE " +
            "network_pending END, network_tried_at = CASE WHEN network_pending <= ? THEN " +
            `NULL ELSE network_tried_at END ${fence}`,
          generation, generation, generation, leaseId, epoch, now);
      } else if (answer === "at_resume") {
        this.#sql.exec(`UPDATE sandbox_leases SET network_tried_at = ? ${fence} AND ` +
                         "network_pending = ?", now, leaseId, epoch, now, generation);
      } else {
        this.#sql.exec("UPDATE sandbox_leases SET network_pending = NULL, network_tried_at = ? " +
                         `${fence} AND network_pending = ?`, now, leaseId, epoch, now,
                       generation);
      }
    });
  }

  /** The live lease these name, or refused: a stale epoch or lease id is fenced. */
  #fenced(leaseId: unknown, epoch: unknown, now: number): LeaseRow {
    const row = typeof leaseId !== "string" ? undefined : this.#sql.exec<LeaseRow>(
      `SELECT * FROM sandbox_leases WHERE lease_id = ? AND ${LIVE_LEASE}`, leaseId, now)
      .toArray()[0];
    if (row === undefined || row.epoch !== epoch) {
      throw new Refusal("sync_fenced", "sync_fenced: the sync lease was lost; this sync is fenced");
    }
    return row;
  }

  leaseRenew(leaseId: unknown, epoch: unknown): Lease {
    return this.#change(null, (now) => {
      const lease = this.#fenced(leaseId, epoch, now);
      this.#sql.exec("UPDATE sandbox_leases SET lease_expires = ? WHERE lease_id = ?",
                     now + LEASE_TTL_S, lease.lease_id);
      return { lease_id: lease.lease_id, epoch: lease.epoch, expires_at: now + LEASE_TTL_S,
               base: this.#space().seq };
    });
  }

  /** Give the lease back; one already lost is no error, and changes nothing. */
  leaseRelease(leaseId: unknown, epoch: unknown): { released: boolean } {
    return this.#change(null, (now) => {
      try {
        this.#fenced(leaseId, epoch, now);
      } catch {
        return { released: false };
      }
      this.#sql.exec("DELETE FROM sandbox_leases WHERE lease_id = ?", leaseId);
      this.#endUploads(leaseId as string);
      return { released: true };
    });
  }

  /**
   * In a unit: the engines signed in inside the live lease `leaseId`'s sandbox, as its own
   * runner reports them; the ones signed in before. Dies with the lease: a login lives in its
   * sandbox and is never copied to another.
   */
  leaseSignins(leaseId: string, engines: readonly string[]): string[] {
    const before = this.#sql.exec<{ signins: string }>(
      "SELECT signins FROM sandbox_leases WHERE lease_id = ?", leaseId).one().signins;
    this.#sql.exec("UPDATE sandbox_leases SET signins = ? WHERE lease_id = ?",
                   [...engines].sort().join(","), leaseId);
    return before === "" ? [] : before.split(",");
  }

  /**
   * In a unit: the digest of what the live lease `leaseId`'s sandbox may read, as its sync
   * agent reads the feed now; whether it changed since its last read (it reads again from
   * the feed's start).
   */
  leaseReadable(leaseId: string, digest: string): boolean {
    const before = this.#sql.exec<{ readable: string | null }>(
      "SELECT readable FROM sandbox_leases WHERE lease_id = ?", leaseId).one().readable;
    if (before === digest) {
      return false;
    }
    this.#sql.exec("UPDATE sandbox_leases SET readable = ? WHERE lease_id = ?", digest, leaseId);
    return before !== null;
  }

  /** In a unit: every lapsed lease's row gone (its runs settled first, `Runs.lost`). */
  /** Each lapsed lease's row gone: the ids of those that went. */
  leasesLapsed(now: number): string[] {
    const gone = this.#sql.exec<{ lease_id: string }>(
      `DELETE FROM sandbox_leases WHERE NOT (${LIVE_LEASE}) RETURNING lease_id`, now).toArray();
    for (const lease of gone) {
      this.#endUploads(lease.lease_id);
    }
    return gone.map((row) => row.lease_id);
  }

  /**
   * In a unit: every upload a lease's sync agent left open, let go as its lease ends, so no
   * sync strands bytes against the owner's storage for a day. Only the uploads begun under
   * that lease (`syncBegin`) go: a client's own writes and placed bodies, begun as the same
   * writer outside any sync, are never cancelled by its sandbox's lease ending.
   */
  #endUploads(leaseId: string): void {
    for (const { intent_id: intentId } of this.#sql.exec<{ intent_id: string }>(
      "SELECT intent_id FROM file_intents WHERE lease = ?", leaseId).toArray()) {
      this.#release(intentId);
    }
  }

  /**
   * The sync agent's upload, begun as its live lease's writer and named by that lease, so
   * the lease's end lets it go (`#endUploads`).
   */
  syncBegin(leaseId: unknown, epoch: unknown, size: unknown, chunks: unknown):
      { intent_id: string; missing: string[]; expires_at: number } {
    const writer = this.syncWriter(leaseId, epoch);
    return this.begin(size, chunks, writer, [], false, leaseId as string);
  }

  /**
   * The writer a live lease's sync commits as (its client's, or the owner's own lease's
   * epoch), the lease fenced as a commit fences it: what a sync upload begins as, so its
   * commit may take it. A read.
   */
  syncWriter(leaseId: unknown, epoch: unknown): string {
    const lease = this.#fenced(leaseId, epoch, Date.now() / 1000);
    return lease.authority !== OWNER_AUTHORITY ? `client:${lease.authority}`
      : `lease:${lease.epoch}`;
  }

  /** Who began an open upload (`begin`'s writer). A read. */
  intentWriter(intentId: unknown): string {
    return this.#intent(intentId, Date.now() / 1000).writer;
  }

  /**
   * The sandbox's sync-out, fenced by the lease, in one transaction with one outcome per
   * op, in order. Each op runs in its own savepoint with its own quota check, so one op
   * that cannot land is reported `refused` with its reason and undoes nothing else. A
   * write against a version that moved on, or under a parent the API made a file, is a
   * conflict: the API's state stays, and the sandbox's content is saved under a new
   * conflict name (`conflictName`). A directory is removed only once empty (the scan
   * removes its contents first), so a sync never deletes what the API wrote below it.
   */
  syncCommit(request: { lease_id: unknown; epoch: unknown; ops: unknown },
             fullGrant: (client: string) => boolean = () => false):
      { outcomes: SyncOutcome[] } {
    const ops = request.ops;
    if (!Array.isArray(ops) || ops.length === 0 || ops.length > MAX_SYNC_OPS) {
      throw new Refusal("invalid", `a sync commit holds 1 to ${MAX_SYNC_OPS} ops`);
    }
    return this.#change(null, (now) => {
      const { epoch, authority } = this.#fenced(request.lease_id, request.epoch, now);
      // A client's sandbox writes as that client, into the owner's agent trees only while
      // its grant is full, the default; a narrowed one's never (the founder's rule).
      const client = authority !== OWNER_AUTHORITY;
      const trees = !client || fullGrant(authority);
      const writer = client ? `client:${authority}` : `lease:${epoch}`;
      const seen = new Set<string>();
      const outcomes = (ops as unknown[]).map((raw) => {
        const op = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
        const named = typeof op["path"] === "string" ? op["path"] : "";
        try {
          return this.#storage.transactionSync(() => {
            const before = this.#before(writer);
            const path = storedPath(op["path"]);
            if (!within(path, WORKSPACE) || isRoot(path) || seen.has(path)) {
              throw new Refusal("invalid", "a sync op names one workspace path, once");
            }
            seen.add(path);
            if (!trees && ownersOnly(path)) {
              throw new Refusal("refused", AGENTS_OWNER_ONLY);
            }
            const outcome = this.#syncOp(op, path, epoch, writer, now);
            this.#fit(before, writer);
            if (op["op"] !== "remove") {
              this.#bounds(before, writer);
            }
            return outcome;
          });
        } catch (error) {
          if (error instanceof Refusal || error instanceof FileFault
              || error instanceof FileConflict) {
            return { path: named, outcome: "refused" as const, reason: error.message,
                     code: error instanceof FileConflict ? "conflict" : error.code };
          }
          throw error;
        }
      });
      return { outcomes };
    }, true);
  }

  /** Whether the API made a file of one of `path`'s ancestors. */
  #blocked(path: string): string | null {
    return ancestors(path).find((dir) => this.#entry(dir)?.kind === "file") ?? null;
  }

  /**
   * Where a conflicting write's content goes: the blocked ancestor, or else the path,
   * under the first conflict name nothing holds, so a copy never replaces anything. The
   * copy's name is a valid path and the rest is the end of one, so only the whole path's
   * length can fail: one that would pass 4096 bytes is refused, as that op's outcome. The sandbox keeps such a file and the sync agent
   * reports it on every sync until it is renamed or the conflict resolved (tasks 6).
   */
  #copyPath(path: string, epoch: number): string {
    const at = this.#blocked(path) ?? path;
    const rest = path.slice(at.length);
    // Each copy is an entry, so the free name is within the entries quota's count. Every
    // candidate starts with the name as cut for the longest suffix that count allows, so the
    // names taken are found in one range read of the parent's entries (review rvlim N6).
    const most = this.#limits().max_entries + 1;
    const longest = conflictName(nameOf(at), epoch, most);
    const cut = longest.slice(0, longest.length - `.ta-conflict-${epoch}-${most}`.length);
    const from = cut === "" ? "" : nameOf(storedPath(`${parentOf(at)}/${cut}`));
    const taken = new Set(this.#sql.exec<{ name: string }>(
      "SELECT name FROM file_entries WHERE parent = ? AND name >= ? AND name < ?",
      parentOf(storedPath(at)), from, `${from}\u{10FFFF}`).toArray().map((row) => row.name));
    for (let n = 1; n <= most; n++) {
      const copy = storedPath(`${parentOf(at)}/${conflictName(nameOf(at), epoch, n)}`);
      if (!taken.has(nameOf(copy))) {
        if (utf8Bytes(copy + rest) > MAX_PATH_BYTES) {
          throw new Refusal("refused", "the conflict copy's path would pass 4096 bytes; " +
                            "the sandbox keeps this file and reports it each sync");
        }
        return copy + rest;
      }
    }
    throw new Refusal("refused", "too many conflict copies of this path in this epoch");
  }

  #syncOp(op: Record<string, unknown>, path: string, epoch: number, writer: string,
          now: number): SyncOutcome {
    const base = op["base"] === null ? null : count(op["base"], "base", 1, Number.MAX_SAFE_INTEGER);
    const current = this.#entry(path);
    const moved = base === null ? current !== undefined : current?.version !== base;
    const conflict = (copy: Entry | null): SyncOutcome =>
      ({ path, outcome: "conflict", current: current === undefined ? null : shown(current),
         copy });
    switch (op["op"]) {
      case "write": {
        const mode = modeOf(op["mode"]);
        let contentId: string;
        let size: number;
        if (op["inline"] !== undefined) {
          const bytes = op["inline"];
          if (!(bytes instanceof Uint8Array) || bytes.byteLength > INLINE_BYTES
              || op["intent_id"] !== undefined) {
            throw new Refusal("invalid", "an inline sync write is at most 4096 bytes");
          }
          contentId = this.#inline(bytes);
          size = bytes.byteLength;
        } else {
          ({ contentId, size } = this.#fromIntent(this.#intent(op["intent_id"], now)));
          this.#release(op["intent_id"] as string);
        }
        if (moved || current?.kind === "dir" || this.#blocked(path) !== null) {
          return conflict(this.#commit(this.#copyPath(path, epoch), "", contentId, size, mode,
                                       writer, "conflict", true, now));
        }
        return { path, outcome: "committed",
                 entry: this.#commit(path, null, contentId, size, mode, writer, "write", true,
                                     now) };
      }
      case "mkdir":
        if ((moved && current?.kind !== "dir") || this.#blocked(path) !== null) {
          return conflict(null);
        }
        return { path, outcome: "committed",
                 entry: this.#mkdir(path, null, true, writer, now) };
      case "remove":
        if (moved || (current?.kind === "dir" && this.#below(path).length > 0)) {
          return conflict(null);
        }
        if (current !== undefined) {
          this.#remove(path, null, true, writer, now);
        }
        return { path, outcome: "committed", entry: null };
      default:
        throw new Refusal("invalid", "a sync op is a write, mkdir or remove");
    }
  }

  // -- HOUSEKEEPING, ON THE OWNER'S ONE SCHEDULE ---------------------------------------

  /** Whether the rows were marked under a retention other than the one in force. */
  #retentionChanged(space: Space, limits: FileLimits): boolean {
    return space.retained_keep !== limits.keep_versions
      || space.retained_recent !== limits.keep_recent
      || space.retained_days !== limits.retention_days;
  }

  /** Whether a retention change is still to be applied to the rows already kept. */
  #reretaining(space: Space, limits: FileLimits): boolean {
    return space.retain_cursor !== null || this.#retentionChanged(space, limits);
  }

  /**
   * The garbage bytes in their grace past the owner's storage figure (`max_bytes`, the
   * lower of its own setting and its tier's), to collect early; 0 for none. A chunk being
   * deleted is the delete's already and counts no more (`garbage_bytes`).
   */
  #garbageOver(): number {
    const max = this.#limits().max_bytes;
    const garbage = this.#sql.exec<{ garbage_bytes: number }>(
      "SELECT garbage_bytes FROM file_space").one().garbage_bytes;
    return max === null ? 0 : Math.max(0, garbage - max);
  }

  /**
   * The latest `gc_after` (exclusive) a garbage chunk may have to be collected early: let go
   * before the newest finished manifest began, so that manifest never named it (a chunk
   * live when it began is named by it). Infinity with no manifest kept.
   */
  #earlyBefore(): number {
    const began = this.#manifested();
    return began === null ? Infinity : began + GC_GRACE_S;
  }

  /** The bytes of deleted files kept for recovery, in no owner's number. A read. */
  garbageBytes(): number {
    return this.#sql.exec<{ garbage_bytes: number }>("SELECT garbage_bytes FROM file_space")
      .one().garbage_bytes;
  }

  /** Whether some garbage, not yet due, may be collected early. A read. */
  #earlyCollectable(now: number): boolean {
    const before = this.#earlyBefore();
    return this.#sql.exec("SELECT 1 FROM file_chunks WHERE state = 'garbage' AND gc_after > ? " +
                            "AND gc_after < ? LIMIT 1", now,
                          Number.isFinite(before) ? before : Number.MAX_VALUE)
      .toArray().length > 0;
  }

  /**
   * When housekeeping is next due, never before `now`, or null: expired intents and
   * temporary directories, due history prunes, and due chunk deletes, each with
   * `HOUSEKEEPING_SLACK_S`, so one run takes an hour's work; and at once, a retention
   * change still to apply, or garbage past the owner's storage figure that it may collect
   * early. Garbage waiting on a failed delete's retry is due at that retry, never at once,
   * and garbage the newest manifest names is never collected early. The `files` lane's.
   */
  next(now: number): number | null {
    if (this.#reretaining(this.#space(), this.#limits())
        || (this.#garbageOver() > 0 && this.#earlyCollectable(now))) {
      return now;
    }
    const times = [
      "SELECT MIN(expires_at) AS due FROM file_intents",
      "SELECT MIN(expires_at) AS due FROM file_temps",
      "SELECT MIN(prune_after) AS due FROM file_history WHERE prune_after IS NOT NULL",
      "SELECT MIN(gc_after) AS due FROM file_chunks WHERE gc_after IS NOT NULL",
    ].map((query) => this.#sql.exec<{ due: number | null }>(query).one().due)
      .filter((due): due is number => due !== null);
    return times.length ? Math.max(Math.min(...times) + HOUSEKEEPING_SLACK_S, now) : null;
  }

  /**
   * One housekeeping pass, the system's own and never refused for a quota: apply a
   * retention change to kept rows, expire intents and temporary directories, prune due
   * history, then delete due garbage chunks, and, while the garbage in its grace is past the
   * tier's storage figure, the oldest of it too (a technical ceiling: recovery against
   * cost, `limits-audit.md`). A delete marks its chunks `deleting` (never
   * held again) before it asks `FILES`, and removes their rows only after; one that fails
   * stays in those rows, retried after `GC_RETRY_S`, and never fails the pass (the `files`
   * lane's contract: an outside call's failure is its rows', a throw is a bug). No delete
   * is begun with less than `DELETE_FLOOR_MS` left before `until`. Each step that writes
   * is a unit of the gate of its own, between the calls, never the whole pass. The `files`
   * lane's pass.
   */
  async run(now: number, until: number = Infinity): Promise<void> {
    const sql = this.#sql;
    await this.#write(() => this.#change(null, () => {
      this.#reretain();
      for (const { intent_id: intentId } of sql.exec<{ intent_id: string }>(
             "SELECT intent_id FROM file_intents WHERE expires_at <= ? ORDER BY expires_at " +
               "LIMIT ?", now, HOUSEKEEPING_BATCH).toArray()) {
        this.#release(intentId);
      }
      for (const { name } of sql.exec<{ name: string }>(
             "SELECT name FROM file_temps WHERE expires_at <= ? ORDER BY expires_at LIMIT ?",
             now, HOUSEKEEPING_BATCH).toArray()) {
        this.#dropTemp(name, "system", now);
      }
      this.#prune(now);
    }));
    if (until - Date.now() < DELETE_FLOOR_MS) {
      return;  // too little of this pass left: the chunks stay due for a later one
    }
    // Read and marked in one unit: a chunk read due outside it could be held again (an
    // upload's begin) before the mark, and its bytes then deleted under a live file.
    const due = await this.#write(() => this.#storage.transactionSync(() => {
      // Oldest first, a tie by hash: the order `file_chunks_due` holds (its key, then the
      // table's), so the same chunks are taken whatever the plan.
      const rows = sql.exec<{ sha256: string; size: number; state: string }>(
        "SELECT sha256, size, state FROM file_chunks WHERE gc_after IS NOT NULL AND " +
          "gc_after <= ? ORDER BY gc_after, sha256 LIMIT ?", now, DELETE_BATCH).toArray();
      const found = rows.map((row) => row.sha256);
      // Garbage past the owner's figure, oldest first, as far as covers it: due now; none the
      // newest manifest names.
      let over = this.#garbageOver() - rows.filter((row) => row.state === "garbage")
        .reduce((sum, row) => sum + row.size, 0);
      const before = this.#earlyBefore();
      if (over > 0 && found.length < DELETE_BATCH) {
        for (const row of sql.exec<{ sha256: string; size: number }>(
               "SELECT sha256, size FROM file_chunks WHERE state = 'garbage' AND gc_after > ? " +
                 "AND gc_after < ? ORDER BY gc_after, sha256 LIMIT ?", now,
               Number.isFinite(before) ? before : Number.MAX_VALUE,
               DELETE_BATCH - found.length).toArray()) {
          if (over <= 0) {
            break;
          }
          found.push(row.sha256);
          over -= row.size;
        }
      }
      for (const sha256 of found) {
        sql.exec("UPDATE file_chunks SET state = 'deleting', gc_after = ? WHERE sha256 = ?",
                 now + GC_RETRY_S, sha256);
      }
      return found;
    }));
    if (due.length === 0) {
      return;
    }
    try {
      // Bounded by the pass's budget: a late delete is tried again, as a failed one is.
      const { ns, key } = await this.space();
      if (await inTime(this.#store.deleteChunks(ns, key, due),
                       until - Date.now()) === "late") {
        throw new Error("the chunk delete ran past its pass");
      }
    } catch {
      console.error("chunk delete failed; it is tried again later");  // no hashes in the log
      return;
    }
    await this.#write(() => this.#storage.transactionSync(() => {
      for (const sha256 of due) {
        sql.exec("DELETE FROM file_chunks WHERE sha256 = ? AND state = 'deleting'", sha256);
      }
    }));
  }

  /** The owner's namespace, or null when nothing was ever stored. A read. */
  storedNamespace(): string | null {
    return this.#space().ns;
  }

  /** Once every `FILES` put this instance has in flight has landed or failed. */
  async settled(): Promise<void> {
    while (this.#puts.size > 0) {
      await Promise.allSettled([...this.#puts]);
    }
  }

  /**
   * The erasure's R2 step (`erase.ts`): every object under the owner's namespace deleted,
   * by its prefix, not by its rows, so a chunk whose row was lost or whose put landed late
   * goes too. Repeatable; it changes no row.
   */
  async eraseNamespace(ns: string): Promise<void> {
    await this.#store.deleteNamespace(ns);
  }

  /**
   * Apply the retention policy in force to rows already kept, a batch of paths a pass,
   * from where the last pass stopped. A policy change during a pass starts it over.
   */
  #reretain(): void {
    const limits = this.#limits();
    const space = this.#space();
    if (!this.#reretaining(space, limits)) {
      return;
    }
    let cursor = space.retain_cursor ?? "";
    if (this.#retentionChanged(space, limits)) {
      cursor = "";
    }
    const paths = this.#sql.exec<{ path: string }>(
      "SELECT DISTINCT path FROM file_history WHERE path > ? ORDER BY path LIMIT ?", cursor,
      HOUSEKEEPING_BATCH).toArray().map((row) => row.path);
    for (const path of paths) {
      this.#remark(path, limits);
    }
    this.#sql.exec("UPDATE file_space SET retained_keep = ?, retained_recent = ?, " +
                     "retained_days = ?, retain_cursor = ?",
                   limits.keep_versions, limits.keep_recent, limits.retention_days,
                   paths.length === HOUSEKEEPING_BATCH ? paths.at(-1)! : null);
  }

  /**
   * Delete due history rows. A pruned row that was its path's newest (a deleted path's
   * last record) raises the feed's floor, so a reader behind it rescans instead of
   * missing the delete.
   */
  #prune(now: number): void {
    this.#expire(this.#sql.exec<{ version: number; path: string }>(
      "SELECT version, path FROM file_history WHERE prune_after IS NOT NULL AND " +
        "prune_after <= ? ORDER BY prune_after LIMIT ?", now, HOUSEKEEPING_BATCH).toArray());
  }

  /** Delete these history rows, the feed's floor raised past any that was its path's newest. */
  #expire(rows: readonly { version: number; path: string }[]): void {
    const sql = this.#sql;
    let floor = 0;
    for (const { version, path } of rows) {
      sql.exec("DELETE FROM file_history WHERE version = ?", version);
      const newer = sql.exec("SELECT 1 FROM file_history WHERE path = ? AND version > ? LIMIT 1",
                             path, version).toArray().length > 0;
      if (!newer) {
        floor = Math.max(floor, version);
      }
    }
    if (floor > 0) {
      sql.exec("UPDATE file_space SET feed_floor = MAX(feed_floor, ?)", floor);
    }
  }
}


/** Who a file change is by, in its history: the owner, or the outside client it came through. */
function fileWriter(account: Account): string {
  return account.origin === null ? "owner" : `client:${account.origin.client}`;
}

/** The owner app's page of the folder holding `path`: where a citation of it points. */
export function fileUrl(path: string): string {
  return `${APP_RESOURCE}#data/${encodeURIComponent(parentOf(path))}`;
}

/** A non-negative whole number of a request, or its default. */
export function whole(value: unknown, what: string, fallback: number, high: number): number {
  if (value === undefined) {
    return fallback;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > high) {
    throw new Refusal("invalid", `${what} is a whole number from 0 to ${high}`);
  }
  return value;
}

/**
 * A search's `next_cursor`: the stored path it resumes after, as base64url (review L5), an
 * opaque token at most 5,462 characters with nothing to escape, so a path of escaped
 * characters never makes an answer too wide to go on.
 */
function pathCursor(path: string): string {
  return Buffer.from(path, "utf8").toString("base64url");
}

function cursorPath(cursor: unknown): string {
  if (typeof cursor !== "string" || !/^[A-Za-z0-9_-]{1,5462}$/.test(cursor)) {
    throw new Refusal("invalid", "a cursor is a next_cursor a search answered");
  }
  return Buffer.from(cursor, "base64url").toString("utf8");
}

/** Files an append never changes: text put after their end breaks what they mean. */
export const STRUCTURED = /\.(json|jsonc|json5|toml|ya?ml)$/i;

/**
 * The tries a large append makes before it is refused `file_busy` (a small one is one unit
 * and never retries). A try is lost only when another write to the same file commits while
 * this one's chunks are out at `FILES`. Another chunked write holds one of the owner's open
 * uploads while its chunks are out, and the owner has at most `MAX_OPEN_INTENTS` open, this
 * one's among them, so those in flight when it starts can beat it at most
 * `MAX_OPEN_INTENTS - 1` times before every one has landed: `MAX_OPEN_INTENTS` tries outlast
 * them. A small whole write holds no upload; one landing in each of that many windows in a
 * row is a flood, refused loudly rather than retried without end. Measured (review rvbr):
 * 80 and then 300 concurrent appends to one small file all landed, none refused.
 */
export const APPEND_TRIES = MAX_OPEN_INTENTS;

/**
 * An outside agent's or a run's remove, rename or restore with no etag: it never removes,
 * moves or overwrites an entry it did not read (review R2-L3, as `UNREAD_WRITE`).
 */
export const UNREAD_CHANGE = "expected_etag is the entry's current etag, or \"\" where there " +
  "is none: read it first (file_stat, fetch or file_read)";

/** A change by an outside client or a run names the etag it read; the owner's app need not. */
function heldToRead(account: Account, asked: unknown): void {
  if ((account.origin !== null || account.run !== null) && typeof asked !== "string") {
    throw new Refusal("invalid", UNREAD_CHANGE);
  }
}

/** An outside agent's or a run's whole write with no etag: it never replaces a file unread. */
export const UNREAD_WRITE = "expected_etag is the file's current etag, or \"\" to create it " +
  "only when absent: read the file first (fetch, file_read or file_stat)";

function fileConflicted<T>(act: () => T): T | FileConflicted {
  try {
    return act();
  } catch (error) {
    if (error instanceof FileConflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

async function fileConflictedAfter<T>(act: () => Promise<T>): Promise<T | FileConflicted> {
  try {
    return await act();
  } catch (error) {
    if (error instanceof FileConflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

/**
 * The most bytes one `file_edit` edits (ADR-037's ruling 2): no longer `WHOLE_BYTES`, since a
 * chunk-wise edit's memory does not depend on the file, but the time one call takes, its
 * tightest bound. The workerd memory leg times the worst edit (the first chunk's, longer, so
 * every chunk is read, remade and put) on the real path, with the files Worker's own hashing
 * left out: about 17 ms a chunk of wall time, at least the CPU it spent (2026-11-04, its log
 * in `limits-audit.md`); a request has 30 s of CPU, and two thirds are left for a slower
 * host, so 500 chunks (about 9 s). The leg fails if the measured time passes 30 s at this
 * figure. Its subrequests, about five a chunk, are 2,500 at most. Production's wall time a
 * chunk (R2's round trips) is owed live; an edit commits whole or not at all.
 */
export const EDIT_BYTES_MAX = 500 * CHUNK_BYTES;

/**
 * An edit's output small enough to keep inline (at most `INLINE_BYTES`, under one chunk):
 * its one chunk made, in a making step (`hold`).
 */
function inlineOf(plan: Edited, hold: <T>(work: () => Promise<T>) => Promise<T>):
    Promise<Uint8Array> {
  if (plan.sizes.length !== 1) {
    throw new Error("an inline edit's output is one chunk");
  }
  return hold(() => plan.make(0));
}

export function flag(value: unknown, what: string): boolean {
  if (typeof value !== "boolean") {
    throw new Refusal("invalid", `${what} is true or false`);
  }
  return value;
}

/** An agent's file, whose agent is a ledger identity while it exists (slice ID). */
const AGENT_FILE = /^\/workspace\/agents\/([^/]+)\/agent\.json$/;

/**
 * What a sync commit's write of the sync-ignore list says of the files it holds back, as its
 * sync agent counted them (`sync.ts`): the kept files its lines name, and those with changes.
 */
function heldBackOf(ops: unknown): { kept: number; changed: number } | null {
  const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
    ? value as number : null;
  for (const op of Array.isArray(ops) ? ops as unknown[] : []) {
    const fields = (typeof op === "object" && op !== null ? op : {}) as Record<string, unknown>;
    const held = fields["held_back"] as Record<string, unknown> | undefined;
    if (fields["path"] === SYNC_IGNORE && fields["op"] === "write" && typeof held === "object"
        && held !== null) {
      const kept = count(held["kept"]);
      const changed = count(held["changed"]);
      if (kept !== null && changed !== null && changed <= kept) {
        return { kept, changed };
      }
    }
  }
  return null;
}

/** A sync token that is not the live lease's, or names another lease or epoch. */
export const SYNC_FENCED = "sync_fenced: the sync lease was lost; this sync is fenced";

export interface UploadStoredRequest {
  readonly intent_id: string;
  readonly chunks: readonly string[];
}
export interface FileRequest {
  readonly path: string;
}
export interface FileListRequest {
  readonly dir: string;
  readonly after?: string;
  readonly limit: number;
  /** At most this many characters of each small file's text (`file-search.ts`). */
  readonly head_chars?: number;
}
/** A window of a file's text (`file_read`): from a byte offset, at most `max_bytes`. */
export interface FileTextRequest {
  readonly path: string;
  readonly offset?: number;
  readonly max_bytes?: number;
}
/** A search of the owner's files (`search`), from a cursor a search answered. */
export interface FileSearchRequest {
  readonly query: string;
  readonly cursor?: string;
}
/** A file by the id a search answered (`fetch`): its stored path. */
export interface FileFetchRequest {
  readonly id: string;
}
/** A search's hit, in the shape ChatGPT's deep research and company knowledge read. */
export interface SearchResult {
  readonly id: string;
  readonly title: string;
  readonly url: string;
}
/** What `fetch` says of a file beside its text. */
export interface FetchMetadata {
  /** Its path, its id's; left out when its fields were cut to fit one answer (`fields_cut`). */
  readonly path?: string;
  /** Whether its frontmatter's fields were cut to fit one answer (review L5). */
  readonly fields_cut?: true;
  readonly size: number;
  readonly version: number;
  /** When it last changed, ISO 8601. */
  readonly modified: string;
  readonly writer: string;
  /** Its etag, which `file_edit` and `file_write` take. */
  readonly etag: string;
  readonly type?: string;
  readonly description?: string;
  readonly tags?: readonly string[];
  /** Whether the file is not UTF-8 text: its text is then empty. */
  readonly binary?: true;
  /** Whether the text stops short of the file's end: `file_read` reads on from `next_offset`. */
  readonly truncated: boolean;
  readonly next_offset?: number;
}
export interface FileReadRequest {
  readonly path: string;
  readonly etag?: string;
}
export interface FileChunksRequest {
  readonly path: string;
  readonly version: number;
  readonly first: number;
  readonly count: number;
}
export interface FileWriteRequest {
  readonly path: string;
  readonly expected_etag: string | null;
  /** The file's bytes, or its text, written as UTF-8 (`file_write`, slice BRAIN). */
  readonly content: Uint8Array | string;
  readonly mode?: number;
}
/** Text added at the end of a file, which is made when absent (`file_append`). */
export interface FileAppendRequest {
  readonly path: string;
  readonly content: string;
}
/** One exact occurrence of `old_text` in a file's text replaced, at its etag (`file_edit`). */
export interface FileEditRequest {
  readonly path: string;
  readonly old_text: string;
  readonly new_text: string;
  readonly expected_etag: string;
}
export interface UploadBeginRequest {
  /** The file's size; `chunks` are its 64-hex hashes, one for each 4 MiB. */
  readonly size: number;
  readonly chunks: string[];
}
export interface UploadChunksRequest {
  readonly intent_id: string;
  readonly chunks: { sha256: string; bytes: Uint8Array }[];
}
export interface UploadCommitRequest {
  readonly intent_id: string;
  readonly path: string;
  readonly expected_etag: string | null;
  readonly mode?: number;
}
export interface IntentRequest {
  readonly intent_id: string;
}
export interface MkdirRequest {
  readonly path: string;
  readonly expected_etag: string | null;
}
export interface RemoveRequest {
  readonly path: string;
  readonly expected_etag: string | null;
  readonly recursive: boolean;
}
export interface RenameRequest {
  readonly from: string;
  readonly to: string;
  readonly expected_etag: string | null;
  readonly replace: boolean;
}
export interface FileHistoryRequest {
  readonly path: string;
  readonly before?: number;
  readonly limit: number;
}
export interface VersionRequest {
  readonly path: string;
  readonly version: number;
}
export interface RestoreRequest extends VersionRequest {
  readonly expected_etag: string | null;
}
export interface ChangesRequest {
  readonly after: number;
  readonly limit: number;
}
interface LeaseRequest {
  readonly lease_id: string;
  readonly epoch: number;
}
export interface SyncCommitRequest extends LeaseRequest {
  readonly ops: SyncOp[];
}
/** The sync agent's upload, begun as its lease's writer (`fileSyncUploadBegin`). */
export interface SyncUploadBeginRequest extends LeaseRequest {
  readonly size: number;
  readonly chunks: string[];
}
/** The sync agent's chunk bytes for its own upload (`fileSyncUploadChunks`). */
export interface SyncUploadChunksRequest extends LeaseRequest {
  readonly intent_id: string;
  readonly chunks: { readonly sha256: string; readonly bytes: Uint8Array }[];
}
/** The sync agent's own upload given up (`fileSyncUploadAbort`). */
export interface SyncUploadAbortRequest extends LeaseRequest {
  readonly intent_id: string;
}
/** A lost file compare-and-swap: answered with the entry as it stands. */
export interface FileConflicted {
  readonly conflict: Entry | null;
}

// -- THE DOORS: the `files` rows' handlers (`contract/doors.json`) --------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** A directory's page (`fileList`). */
function listed(o: OwnerState, { r, readable }: Call):
    { entries: (Entry & { head?: string })[]; next: string | null } {
  const listed = o.files.list(r["dir"], r["after"] ?? null, r["limit"]);
  const entries = listed.entries.filter((entry) => readable?.test(entry.path) ?? true);
  const chars = r["head_chars"] === undefined ? null
    : whole(r["head_chars"], "head_chars", 0, Number.MAX_SAFE_INTEGER);
  // One answer holds the page (`pages.ts`): when entries are left, `next` is the last
  // one's name, so the next page starts after it.
  const shown = chars === null ? entries : entries.map((entry) => {
    if (entry.kind !== "file" || entry.size > INLINE_BYTES) {
      return entry;
    }
    const inline = o.files.open(entry.path).inline;
    const text = inline === undefined ? null : textOf(inline);
    return text === null ? entry : { ...entry, head: [...text].slice(0, chars).join("") };
  });
  return fitList({ ...listed, entries: shown }, shown,
                 { entries: [], next: String.fromCharCode(1).repeat(MAX_NAME_BYTES) },
                 (kept) => ({ entries: kept, next: nameOf(kept.at(-1)!.path) }));
}

/**
 * A change to the owner's agent definitions (`OWNER_TREES`: its agents' files, skills, goals and
 * `agent.json`). The founder's rule (2026-11-03): every agent of the owner has full power by
 * default to do what any other of its agents can, so an agent (an outside client's call, or
 * a run) under a full grant, the default, changes them; one under a grant the owner narrowed
 * (`scoped`) is refused. The owner's own app is a human's path, so its bearer token alone
 * changes nothing there: it is a widening, taken only in the owner's live session and
 * receipted when refused, against a leaked app token.
 */
function ownersTree(o: OwnerState, binding: Binding, account: Account, action: string,
                    request: unknown, ...paths: unknown[]): void {
  if (!paths.some((path) => ownersOnly(storedPath(path)))) {
    return;
  }
  if (account.run !== null) {
    const stamp = account.run.stamp;
    if (stamp !== null && !o.grants.full(stamp.client)) {
      throw new Refusal("refused", AGENTS_OWNER_ONLY);
    }
    return;
  }
  if (account.origin !== null) {
    if (account.grant?.mode !== "full") {
      throw new Refusal("refused", AGENTS_OWNER_ONLY);
    }
    return;
  }
  try {
    o.sessions.live(binding.session, clock());
  } catch (error) {
    try {
      o.policy.refusal(action, { request, origin: null }, clock());
    } catch {
      console.error("owner-session refusal receipt unavailable");  // refuse regardless
    }
    throw error;
  }
}

/** A search's page (`fileSearch`). */
function searched(o: OwnerState, { r }: Call):
    { results: SearchResult[]; next_cursor: string | null; note?: string } {
  const query = r["query"];
  if (typeof query !== "string" || utf8Bytes(query) > SEARCH_QUERY_BYTES) {
    throw new Refusal("invalid", `a query is text of at most ${SEARCH_QUERY_BYTES} bytes`);
  }
  const terms = new Set(words(query));
  if (terms.size === 0) {
    throw new Refusal("invalid", "a query has at least one word");
  }
  const cursor = r["cursor"] === undefined ? null : storedPath(cursorPath(r["cursor"]));
  const scanned = o.files.scan(cursor, SEARCH_SCAN);
  const page = searchPage(scanned.entries, scanned.next !== null, terms, fileUrl);
  const answer = page.resume === null ? { results: [...page.results], next_cursor: null }
    : { results: [...page.results], next_cursor: pathCursor(page.resume),
        note: "more of the files are still to search: search again with next_cursor" };
  // Only paths of thousands of escaped characters pass one answer: told, never cut.
  if (jsonBytes(answer) > PAGE_BYTES) {
    throw new Refusal("result_too_large", RESULT_TOO_LARGE);
  }
  return answer;
}

/**
 * A path a reader may not see is refused as one that does not exist: a file door's caller
 * sees every path, the sync agent its lease's readable set (`#syncer`), so no content door
 * shows a path its change feed would not.
 */
function visible(o: OwnerState, call: Call, path: unknown): void {
  if (typeof path === "string" && call.readable !== null && !call.readable.test(path)) {
    throw new FileFault("not_found", "no such file");
  }
}

/**
 * The text of `content` from `offset`, at most `max` bytes cut back to a character: its
 * inline bytes, or the chunks the window spans (at most two reads).
 */
async function textWindow(o: OwnerState, content: Content, offset: number, max: number):
    Promise<{ text: string | null; next: number | null }> {
  const size = content.entry.size;
  const want = Math.min(size, offset + max + 4);  // a character's tail past the window
  let bytes: Uint8Array;
  if (content.inline !== undefined) {
    bytes = content.inline.subarray(offset, want);
  } else {
    const reads: { sha256: string; offset: number; length: number; verify: true }[] = [];
    let at = 0;
    for (const chunk of content.chunks ?? []) {
      const from = Math.max(offset, at);
      const to = Math.min(want, at + chunk.size);
      if (from < to) {
        // Verified (review L7): `FILES` rechecks the whole chunk and answers the range.
        reads.push({ sha256: chunk.sha256, offset: from - at, length: to - from,
                     verify: true });
      }
      at += chunk.size;
    }
    const parts = await o.files.fetch(reads);
    bytes = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
    let put = 0;
    for (const part of parts) {
      bytes.set(part, put);
      put += part.byteLength;
    }
  }
  const end = windowEnd(bytes, 0, max);
  if (end === null) {
    throw new Refusal("invalid", "offset is inside a character: start at a window's next_offset");
  }
  return { text: textOf(bytes.subarray(0, end)),
           next: offset + end < size ? offset + end : null };
}

/**
 * A change to the sync's ignore list, in its own unit: a receipt and one notice naming
 * who changed it (the owner, an agent's run, or an outside client), and, when a line it
 * adds stops files the owner already keeps from syncing their changes, how many and which.
 */
/**
 * A file change, in its own unit, as the owner's other modules follow it: each agent's ledger
 * identity follows its `agent.json`; and every agent may edit the sync's ignore list (the
 * founder's every-agent-full-power rule), but never unseen: each change is a receipt and one
 * notice for the owner.
 */
export function fileChanged(o: OwnerState, path: string, present: boolean, now: number,
                            change: { writer: string; content_id: string | null; size: number }):
    void {
  const agent = AGENT_FILE.exec(path)?.[1];
  if (agent !== undefined) {
    if (present) {
      o.ledger.appear(agent, now);
    } else {
      o.ledger.retire(agent, now);
    }
  }
  if (path === SYNC_IGNORE) {
    syncIgnoreChanged(o, present, now, change);
  }
}

function syncIgnoreChanged(o: OwnerState, present: boolean, now: number,
                   change: { writer: string; content_id: string | null; size: number }): void {
  const outcome = present ? "changed" : "removed";
  const who = writerNamed(o, change.writer);
  o.policy.receipt("write", { rule: "sync_ignore", outcome, decision: who }, now);
  let held = "";
  if (present && change.content_id !== null) {
    // Whoever wrote it, its new lines are read (a set's work, the list at most an inline
    // file), never matched against the owner's files here.
    const lines = (bytes: Uint8Array | null) => bytes === null ? null
      : ruleLines(new TextDecoder().decode(bytes).split("\n"));
    const after = lines(o.files.inlineOf(change.content_id));
    const before = o.files.contentBefore(SYNC_IGNORE, o.files.lastVersion(SYNC_IGNORE));
    const was = new Set(before === null ? [] : lines(o.files.inlineOf(before.content_id))
      ?? []);
    const defaults = new Set(DEFAULT_IGNORES);
    if (after === null || after.some((line) => !was.has(line) && !defaults.has(line))) {
      held = " Its new lines may hold back files you keep from syncing their changes; " +
        "each sandbox sync reports the files they hold back.";
    }
    // Lines past a ceiling are not applied: said here, never in silence.
    const raw = o.files.inlineOf(change.content_id);
    const past = raw === null ? [] : unappliedLines(new TextDecoder().decode(raw).split("\n"));
    const states = past.filter((each) => each.reason === "states");
    if (states.length > 0) {
      held += ` ${states.length} line${states.length === 1 ? " is" : "s are"} past the ${
        AUTOMATON_STATES.toLocaleString("en")} automaton states the lines with *, ? or [ ] ` +
        `may take together, so not applied: the files they name go on syncing (${
          states.slice(0, 3).map((each) => each.line).join(", ")}${states.length > 3
          ? ", …" : ""}).`;
    }
    if (change.size > LIST_BYTES) {
      held += ` The list past its ${LIST_BYTES.toLocaleString("en")} bytes is not applied: ` +
        "the files those lines name go on syncing.";
    }
    // The owner's own sandbox, whose mirror is the owner's whole tree, adds what its sync
    // agent counted (`sync.ts`); an outside client's sees only its part, so its count is
    // never given, and no count ever takes the sentence away.
    const synced = o.heldBack;
    if (synced !== null && /^lease:[0-9]+$/.test(change.writer) && synced.kept > 0) {
      held += ` In the sync that wrote it, its lines hold back ${synced.kept} file${
        synced.kept === 1 ? "" : "s"} you keep (${synced.changed} with changes in the ` +
        "sandbox): your files keep the version they have, and each sync reports them.";
    }
  }
  o.notices.add("sync_ignore_changed", `${SYNC_IGNORE} was ${outcome} by ${who}: what ` +
                      `the sandbox keeps out of your files follows it.${held}`, now);
}

/**
 * Who a file change's writer is, in words the owner reads: the owner; the agents whose
 * runs hold the owner's sandbox lease it was synced under; or an outside client by name.
 */
function writerNamed(o: OwnerState, writer: string): string {
  const epoch = /^lease:([0-9]+)$/.exec(writer)?.[1];
  if (epoch !== undefined) {
    // The agents named at most three, so the receipt stays its size.
    const agents = o.runs.agentsAt(Number(epoch), 4);
    return agents.length === 0 ? "a run in your sandbox"
      : `a run of your agent${agents.length === 1 ? "" : "s"} ${agents.slice(0, 3).join(", ")}${
        agents.length > 3 ? " and others" : ""}`;
  }
  const client = /^client:(.+)$/.exec(writer)?.[1];
  if (client !== undefined) {
    const name = o.grants.name(client);
    return `the outside client ${name !== "" ? `"${name}"` : client.slice(0, 12)}`;
  }
  return writer === "owner" ? "you" : writer;
}

/** The `files` doors' handlers, by door. */
export const filesDoors = Object.freeze({
  fileStat(o: OwnerState, { r }: Call<FileRequest>): { entry: Entry | null } {
    return { entry: o.files.stat(r["path"]) };
  },
  async fileList(o: OwnerState, door: Door<FileListRequest>):
      Promise<{ entries: (Entry & { head?: string })[]; next: string | null }> {
    await o.seededFor(door);
    return o.door(() => listed(o, door.check()));
  },
  /** A window of a file's text, its call checked in its units (`fileText`). */
  async fileText(o: OwnerState, door: Door<FileTextRequest>):
      Promise<{ entry: Entry; offset: number; text: string; next_offset: number | null }> {
    await o.seededFor(door);
    const asked = await o.door(() => {
      const { r } = door.check();
      const content = o.files.open(r["path"]);
      const offset = whole(r["offset"], "offset", 0, content.entry.size);
      const max = whole(r["max_bytes"], "max_bytes", READ_DEFAULT_BYTES, CHUNK_BYTES);
      if (max < 1) {
        throw new Refusal("invalid", "max_bytes is at least 1");
      }
      return { content, offset, max };
    });
    const { content, offset, max } = asked;
    // One answer holds the window (`pages.ts`): its text cut to what the entry leaves.
    const room = PAGE_BYTES - jsonBytes({ entry: content.entry, offset, text: "",
                                          next_offset: content.entry.size }) + 2;
    if (room < 8) {
      throw new Refusal("result_too_large", RESULT_TOO_LARGE);
    }
    const read = await textWindow(o, content, offset, Math.min(max, room));
    if (read.text === null) {
      throw new Refusal("invalid", `${content.entry.path} is not UTF-8 text`);
    }
    const part = fitted(read.text, room);
    const end = offset + part.bytes;
    return { entry: content.entry, offset, text: part.text,
             next_offset: end < content.entry.size ? end : null };
  },
  async fileSearch(o: OwnerState, door: Door<FileSearchRequest>):
      Promise<{ results: SearchResult[]; next_cursor: string | null; note?: string }> {
    await o.seededFor(door);
    return o.door(() => searched(o, door.check()));
  },
  /** A file by a search's id, its call checked in its units (`fileFetch`). */
  async fileFetch(o: OwnerState, door: Door<FileFetchRequest>):
      Promise<{ id: string; title: string; text: string; url: string;
                                        metadata: FetchMetadata }> {
    await o.seededFor(door);
    const content = await o.door(() => o.files.open(door.check().r["id"]));
    // One answer holds it (`pages.ts`): its text cut to what its other fields leave, and
    // `truncated` with `next_offset` for file_read to go on from.
    const read = await textWindow(o, content, 0, PAGE_BYTES);
    // A file search found that is not text (a scan, an image) is answered with its metadata
    // and no text, never refused: every id a search answers is fetchable (review L4).
    const found = frontmatter(read.text ?? "");
    const path = content.entry.path;
    // A frontmatter too wide for one answer beside its path is cut, said so (review L5):
    // every id a search answers is fetchable, whatever its fields hold.
    let front = found;
    let cut = false;
    const answer = (text: string, next: number | null) => ({
      id: path, title: titleOf(path, front), text, url: fileUrl(path),
      metadata: { ...(cut ? { fields_cut: true as const } : { path }),
                  size: content.entry.size, version: content.entry.version,
                  modified: new Date(content.entry.mtime_ms).toISOString(),
                  writer: content.entry.writer, etag: content.entry.etag,
                  ...(front.type === undefined ? {} : { type: front.type }),
                  ...(front.description === undefined ? {} : { description: front.description }),
                  ...(front.tags.length === 0 ? {} : { tags: front.tags }),
                  ...(read.text === null ? { binary: true as const } : {}),
                  truncated: read.text !== null && next !== null,
                  ...(read.text === null || next === null ? {} : { next_offset: next }) },
    });
    let room = PAGE_BYTES - jsonBytes(answer("", content.entry.size)) + 2;
    if (room < 8) {
      // Its metadata's path is its id's; its frontmatter's fields, a few KiB each at most.
      cut = true;
      const { title, description, type } = found;
      front = { ...(title === undefined ? {} : { title: fitted(title, 1024).text }),
                ...(description === undefined ? {}
                  : { description: fitted(description, 4096).text }),
                ...(type === undefined ? {} : { type: fitted(type, 256).text }),
                tags: found.tags.filter((_, index) => index < 16)
                  .map((tag) => fitted(tag, 256).text) };
      room = PAGE_BYTES - jsonBytes(answer("", content.entry.size)) + 2;
      if (room < 8) {
        throw new Refusal("result_too_large", RESULT_TOO_LARGE);
      }
    }
    const part = fitted(read.text ?? "", room);
    return answer(part.text, read.text === null || part.bytes >= content.entry.size ? null
      : part.bytes);
  },
  /** Text added at the end of a file, its call checked in its units (`fileAppend`). */
  async fileAppend(o: OwnerState, door: Door<FileAppendRequest>): Promise<{ entry: Entry }> {
    await o.seededFor(door);
    const { account, path, added } = await o.door(() => {
      const { account, r } = door.check();
      ownersTree(o, door.binding, account, "FILES.append", door.request, r["path"]);
      if (STRUCTURED.test(storedPath(r["path"]))) {
        throw new Refusal("invalid",
                          "a structured file (JSON, TOML or YAML) is never appended to: read " +
                          "it and change it with file_edit");
      }
      const content = r["content"];
      if (typeof content !== "string" || content === "") {
        throw new Refusal("invalid", "appended content is text of at least one character");
      }
      const added = new TextEncoder().encode(content);
      if (added.byteLength > CHUNK_BYTES) {
        throw new Refusal("invalid", "appended text is at most 4 MiB at once");
      }
      return { account, path: storedPath(r["path"]), added };
    });
    const authorize = () => {
      ownersTree(o, door.binding, door.check().account, "FILES.append", door.request, path);
    };
    try {
      for (let tries = 0; ; tries += 1) {
        if (tries === APPEND_TRIES) {
          throw new Refusal("file_busy",
                            `file_busy: ${path} changed under each of ${APPEND_TRIES} tries: ` +
                            "append again");
        }
        const read = await o.door((): { done: Entry } | { now: Content | null } => {
          authorize();
          const entry = o.files.stat(path);
          if (entry !== null && entry.kind !== "file") {
            throw new Refusal("invalid", `${path} is a directory`);
          }
          const content = entry === null ? null : o.files.open(path);
          const before = content === null ? new Uint8Array(0) : content.inline;
          if (before !== undefined && before.byteLength + added.byteLength <= INLINE_BYTES) {
            // Small: read and written in this one unit, so nothing lands between.
            const whole = new Uint8Array(before.byteLength + added.byteLength);
            whole.set(before, 0);
            whole.set(added, before.byteLength);
            return { done: o.files.writeSmall(path, whole, {
              expected: entry?.etag ?? "", mode: entry?.mode, writer: fileWriter(account) }) };
          }
          return { now: content };
        });
        if ("done" in read) {
          return { entry: read.done };
        }
        const now = read.now;
        // The file's last chunk and the text, joined and written, in one step of
        // `APPEND_UNITS` (`holds.ts`, ADR-037): what it holds is the same whatever the
        // file's size.
        const wrote = await slotted(ownerSlots(), APPEND_UNITS, async () => {
          let bytes = added;
          let prefix: ChunkRef[] = [];
          if (now !== null) {
            let tail: Uint8Array;
            if (now.inline !== undefined) {
              tail = now.inline;
            } else {
              const chunks = now.chunks ?? [];
              const whole = chunks.length > 0 && chunks.at(-1)!.size === CHUNK_BYTES
                ? chunks.length : chunks.length - 1;
              prefix = chunks.slice(0, whole);
              const rest = chunks.slice(whole);
              tail = rest.length === 0 ? new Uint8Array(0)
                : (await o.files.fetch(rest.map(({ sha256 }) => ({ sha256 }))))[0]!;
            }
            bytes = new Uint8Array(tail.byteLength + added.byteLength);
            bytes.set(tail, 0);
            bytes.set(added, tail.byteLength);
          }
          return fileConflictedAfter(async () => ({
            entry: await o.files.write(path, bytes, {
              expected: now?.entry.etag ?? "",
              ...(now === null ? {} : { mode: now.entry.mode }), prefix,
              writer: fileWriter(account), authorize }),
          }));
        });
        if (!("conflict" in wrote)) {
          return wrote;
        }
        // Another write landed between the read and this one: read it again.
      }
    } finally {
      await o.sync();
    }
  },
  /** A whole file written, its call checked in its units (`fileWrite`). */
  async fileWrite(o: OwnerState, door: Door<FileWriteRequest>):
      Promise<{ entry: Entry } | FileConflicted> {
    await o.seededFor(door);
    const { account, r, content } = await o.door(() => {
      const { account, r } = door.check();
      ownersTree(o, door.binding, account, "FILES.write", door.request, r["path"]);
      const asked = r["expected_etag"];
      if ((account.origin !== null || account.run !== null) && typeof asked !== "string") {
        throw new Refusal("invalid", UNREAD_WRITE);
      }
      const given = r["content"];
      const content = typeof given === "string" ? new TextEncoder().encode(given) : given;
      if (!(content instanceof Uint8Array) || content.byteLength > CHUNK_BYTES) {
        throw new Refusal("invalid",
                          "a written file is bytes or text, at most 4 MiB; larger ones are " +
                          "uploaded");
      }
      return { account, r, content };
    });
    try {
      // `Files.write` writes each step around its `FILES` call as a unit of its own.
      return await fileConflictedAfter(async () => ({
        entry: await o.files.write(r["path"], content, {
          expected: r["expected_etag"], mode: r["mode"], writer: fileWriter(account),
          authorize: () => door.check() }),
      }));
    } finally {
      await o.sync();
    }
  },
  /** One exact occurrence replaced, its call checked in its units (`fileEdit`). */
  async fileEdit(o: OwnerState, door: Door<FileEditRequest>):
      Promise<{ entry: Entry } | FileConflicted> {
    await o.seededFor(door);
    const asked = await o.door(() => {
      const { account, r } = door.check();
      ownersTree(o, door.binding, account, "FILES.edit", door.request, r["path"]);
      const [old, replacement, etag] = [r["old_text"], r["new_text"], r["expected_etag"]];
      if (typeof old !== "string" || old === "" || typeof replacement !== "string") {
        throw new Refusal("invalid",
                          "old_text is text of at least one character, and new_text is text");
      }
      if (typeof etag !== "string" || etag === "") {
        throw new Refusal("invalid",
                          "expected_etag is the file's etag, as file_stat or fetch answers it");
      }
      const path = storedPath(r["path"]);
      const entry = o.files.stat(path);
      if (entry === null || entry.kind !== "file") {
        throw new FileFault("not_found", "no such file");
      }
      if (entry.etag !== etag) {
        return { conflict: entry };
      }
      if (entry.size > EDIT_BYTES_MAX) {
        throw new Refusal("invalid",
                          `file_edit edits a file of at most ${EDIT_BYTES_MAX} bytes, the ` +
                          "reads and time one call holds; write a larger one by upload");
      }
      return { account, path, old, replacement, content: o.files.open(path) };
    });
    if (asked.conflict !== undefined) {
      return { conflict: asked.conflict };
    }
    const { account, path, old, replacement, content } = asked;
    const authorize = () => door.check();
    const refused = (refusal: EditRefusal): never => {
      throw new Refusal("invalid", refusal === "ill_formed"
        ? "old_text and new_text are well-formed text"
        : refusal === "not_utf8" ? `${path} is not UTF-8 text`
        : refusal === "absent" ? "old_text is not in the file: read it again and give its " +
          "exact text"
        : `old_text occurs ${refusal.times} times: give more of the text around the one to ` +
          "change");
    };
    try {
      if (content.inline !== undefined) {
        // A small file, kept inline: edited whole, its bytes at most `INLINE_BYTES`.
        let text: string;
        try {
          text = utf8Decoder("keep").decode(content.inline);
        } catch {
          return refused("not_utf8");
        }
        const first = text.indexOf(old);
        if (first < 0) {
          return refused("absent");
        }
        let times = 0;
        for (let at = first; at >= 0; at = text.indexOf(old, at + 1)) {
          times += 1;
        }
        if (times > 1) {
          return refused({ times });
        }
        const edited = new TextEncoder().encode(
          text.slice(0, first) + replacement + text.slice(first + old.length));
        return await fileConflictedAfter(async () => ({
          entry: await o.files.write(path, edited, {
            expected: content.entry.etag, mode: content.entry.mode, writer: fileWriter(account),
            authorize }),
        }));
      }
      // A chunked file, edited chunk by chunk (`edit.ts`, ADR-037): each chunk read in a
      // reading step, each chunk made and put in a making step, the units taken as its
      // bytes come and released as they go, never across the begin, the commit or the
      // other chunks. Its memory does not depend on its size.
      const chunks = content.chunks ?? [];
      const pool = ownerSlots();
      const make = <T>(work: () => Promise<T>) => slotted(pool, EDIT_MAKE_UNITS, work);
      const plan = await planEdit({
        size: content.entry.size, chunks,
        read: async (index) => (await o.files.fetch([{ sha256: chunks[index]!.sha256 }]))[0]!,
      }, old, replacement, CHUNK_BYTES, (work) => slotted(pool, EDIT_READ_UNITS, work));
      if ("refused" in plan) {
        return refused(plan.refused);
      }
      return await fileConflictedAfter(async () => ({
        entry: plan.size <= INLINE_BYTES
          ? await o.files.write(path, await inlineOf(plan, make), {
            expected: content.entry.etag, mode: content.entry.mode,
            writer: fileWriter(account), authorize })
          : await o.files.writeChunks(path, plan.size, plan.hashes, plan.make, {
            expected: content.entry.etag, mode: content.entry.mode,
            writer: fileWriter(account), readable: chunks.map(({ sha256 }) => sha256),
            authorize, hold: make }),
      }));
    } finally {
      await o.sync();
    }
  },
  fileHistory(o: OwnerState, { r }: Call<FileHistoryRequest>):
      { changes: Change[]; next: number | null } {
    // One answer holds the page (`pages.ts`): when versions are left, `next` the last's.
    const kept = o.files.history(r["path"], r["before"] ?? null, r["limit"]);
    return fitList(kept, kept.changes, { changes: [], next: Number.MAX_SAFE_INTEGER },
                   (changes) => ({ changes, next: changes.at(-1)!.version }));
  },
  fileUsage(o: OwnerState, { account }: Call<undefined>): Usage {
    return o.files.usage(fileWriter(account));
  },
  fileMkdir(o: OwnerState, { account, r, binding, request }: Call<MkdirRequest>):
      { entry: Entry } | FileConflicted {
    ownersTree(o, binding, account, "FILES.mkdir", request, r["path"]);
    return fileConflicted(() => ({
      entry: o.files.mkdir(r["path"], { expected: r["expected_etag"],
                                            writer: fileWriter(account) }),
    }));
  },
  fileRemove(o: OwnerState, { account, r, binding, request }: Call<RemoveRequest>):
      { removed: number } | FileConflicted {
    ownersTree(o, binding, account, "FILES.remove", request, r["path"]);
    heldToRead(account, r["expected_etag"]);
    return fileConflicted(() => o.files.remove(r["path"], {
      expected: r["expected_etag"], recursive: flag(r["recursive"], "recursive"),
      writer: fileWriter(account) }));
  },
  fileRename(o: OwnerState, { account, r, binding, request }: Call<RenameRequest>):
      { entry: Entry } | FileConflicted {
    ownersTree(o, binding, account, "FILES.rename", request, r["from"], r["to"]);
    heldToRead(account, r["expected_etag"]);
    return fileConflicted(() => ({
      entry: o.files.rename(r["from"], r["to"], {
        expected: r["expected_etag"], replace: flag(r["replace"], "replace"),
        writer: fileWriter(account) }),
    }));
  },
  fileRestore(o: OwnerState, { account, r, binding, request }: Call<RestoreRequest>):
      { entry: Entry } | FileConflicted {
    ownersTree(o, binding, account, "FILES.restore", request, r["path"]);
    heldToRead(account, r["expected_etag"]);
    return fileConflicted(() => ({
      entry: o.files.restore(r["path"], r["version"], {
        expected: r["expected_etag"], writer: fileWriter(account) }),
    }));
  },
  storageUsage(o: OwnerState, _call: Call<undefined>): {
    used_bytes: number; limit_bytes: number | null;
    breakdown: { files: number; messages: number; collections: number };
    deleted_content: { kept_bytes: number; kept_up_to_days: number } } {
    const usage = o.files.usage();
    const used = usage.stored;
    // Each kind's bodies, inline or placed (a placed content counted once); files are the
    // rest, so the parts always sum to what is used.
    const messages = Math.min(used, o.ledger.storedBytes());
    const collections = Math.min(used - messages, o.collections.storedBytes());
    // Deleted files kept for recovery: up to 30 days, within the same figure (the oldest go
    // first past it), so a lower setting shortens recovery; counted in nothing used.
    return { used_bytes: used, limit_bytes: usage.limits.max_bytes,
             breakdown: { files: used - messages - collections, messages, collections },
             deleted_content: { kept_bytes: o.files.garbageBytes(),
                                kept_up_to_days: GC_GRACE_S / 86_400 } };
  },
  async dataKeyRewrap(o: OwnerState, door: Door<undefined>):
      Promise<{ rewrapped: boolean; roots: number }> {
    const days = await o.door(() => {
      door.check();
      return o.backups.manifests().map((manifest) => manifest.gen);
    });
    return o.files.rewrap(days);
  },
  async fileUploadTarget(o: OwnerState, door: Door<IntentRequest>):
      Promise<{ namespace: string; key: string; chunks: { sha256: string; size: number }[];
                pending: string[] }> {
    const plan = await o.door(() => o.files.uploadPlan(door.check().r["intent_id"]));
    // The namespace and its wrapped data key, which only `FILES` opens (slice P, M4).
    const { ns, key } = await o.files.space();
    return { namespace: ns, key, ...plan };
  },
  fileUploadStored(o: OwnerState, { r }: Call<UploadStoredRequest>): { stored: number } {
    return o.files.stored(r["intent_id"], r["chunks"]);
  },
  fileRead(o: OwnerState, call: Call<FileReadRequest>):
      { entry: Entry; not_modified?: true; inline?: Uint8Array; chunks?: ChunkRef[] } {
    const { r } = call;
    visible(o, call, r["path"]);
    const content: Content = o.files.open(r["path"]);
    if (r["etag"] !== undefined && r["etag"] === content.entry.etag) {
      return { entry: content.entry, not_modified: true };
    }
    const { content_id: _id, ...shown } = content;
    return shown;
  },
  async fileReadChunks(o: OwnerState, door: Door<FileChunksRequest>):
      Promise<{ chunks: Uint8Array[] }> {
    const reads = await o.door(() => {
      const call = door.check();
      const { r } = call;
      visible(o, call, r["path"]);
      const { chunks = [] } = o.files.openVersion(r["path"], r["version"]);
      const first = r["first"];
      const many = r["count"];
      if (typeof first !== "number" || typeof many !== "number" || !Number.isSafeInteger(first)
          || !Number.isSafeInteger(many) || first < 0 || many < 1 || many > 2
          || first + many > chunks.length) {
        throw new Refusal("invalid", "first and count name 1 or 2 of the version's chunks");
      }
      return chunks.slice(first, first + many).map(({ sha256 }) => ({ sha256 }));
    });
    return { chunks: await o.files.fetch(reads) };
  },
  fileUploadBegin(o: OwnerState, { account, r }: Call<UploadBeginRequest>):
      { intent_id: string; missing: string[]; expires_at: number } {
    return o.files.begin(r["size"], r["chunks"], fileWriter(account));
  },
  async fileUploadChunks(o: OwnerState, door: Door<UploadChunksRequest>):
      Promise<{ created: number }> {
    const { r } = await o.door(() => door.check());
    // `Files.upload` records each chunk stored, once `FILES` took it, in a unit of its own.
    return o.files.upload(r["intent_id"], r["chunks"]);
  },
  fileUploadCommit(o: OwnerState, { account, r, binding }: Call<UploadCommitRequest>):
      { entry: Entry } | FileConflicted {
    ownersTree(o, binding, account, "FILES.write", { path: r["path"] }, r["path"]);
    return fileConflicted(() => ({
      entry: o.files.commit(r["intent_id"], r["path"], {
        expected: r["expected_etag"], mode: r["mode"], writer: fileWriter(account) }),
    }));
  },
  fileUploadAbort(o: OwnerState, { r }: Call<IntentRequest>): { aborted: boolean } {
    return o.files.abort(r["intent_id"]);
  },
  fileRedact(o: OwnerState, { r }: Call<VersionRequest>): { redacted: number } {
    return o.files.redact(r["path"], r["version"]);
  },
  fileHistoryPurge(o: OwnerState, { r }: Call<FileRequest>): { purged: number } {
    return o.files.purge(r["path"]);
  },
  fileChanges(o: OwnerState, { r, readable }: Call<ChangesRequest>):
      { changes: Change[]; cursor: number; reset: boolean } {
    const changes = o.files.changes(r["after"], r["limit"]);
    if (readable === null) {
      return changes;
    }
    // A client's sandbox whose readable set changed since its last read (a new agent's run,
    // a widened file scope) reads the feed again from its start: `reset`.
    const moved = readable.scope !== null && o.files.leaseReadable(
      readable.lease, sha256Hex(readable.scope)
        .slice(0, 16));
    const test = readable.test;
    return { ...changes, reset: changes.reset || moved,
             changes: changes.changes.filter((change) => test(change.path)) };
  },
  fileSyncCommit(o: OwnerState, { r }: Call<SyncCommitRequest>): { outcomes: SyncOutcome[] } {
    o.heldBack = heldBackOf(r["ops"]);
    try {
      return o.files.syncCommit({ lease_id: r["lease_id"], epoch: r["epoch"],
                                      ops: r["ops"] },
                                    (authority) => o.grants.full(authority));
    } finally {
      o.heldBack = null;
    }
  },
  fileSyncUploadBegin(o: OwnerState, { r }: Call<SyncUploadBeginRequest>):
      { intent_id: string; missing: string[]; expires_at: number } {
    return o.files.syncBegin(r["lease_id"], r["epoch"], r["size"], r["chunks"]);
  },
  async fileSyncUploadChunks(o: OwnerState, door: Door<SyncUploadChunksRequest>):
      Promise<{ created: number }> {
    const r = await o.door(() => {
      const { r: fields } = door.check();
      if (o.files.intentWriter(fields["intent_id"])
          !== o.files.syncWriter(fields["lease_id"], fields["epoch"])) {
        throw new Refusal("sync_fenced", SYNC_FENCED);
      }
      return fields;
    });
    return o.files.upload(r["intent_id"], r["chunks"]);
  },
  fileSyncUploadAbort(o: OwnerState, { r }: Call<SyncUploadAbortRequest>): { aborted: boolean } {
    const writer = o.files.syncWriter(r["lease_id"], r["epoch"]);
    let owner: string | null;
    try {
      owner = o.files.intentWriter(r["intent_id"]);
    } catch (error) {
      if (!(error instanceof Refusal && !error.invalid)) {
        throw error;  // a malformed id: invalid
      }
      owner = null;  // gone already: expired, committed or let go
    }
    if (owner !== null && owner !== writer) {
      throw new Refusal("sync_fenced", SYNC_FENCED);
    }
    return owner === null ? { aborted: false } : o.files.abort(r["intent_id"]);
  },
});
