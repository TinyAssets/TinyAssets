/**
 * The archive (slice STORE item 2, `segments.ts`; ADR-035): the `archive` lane's pass, which
 * moves acked mail out of the hot rows into immutable segments in R2 and rewrites segments a
 * deleted agent's mail is gone from (`MoveReader`, `MoveHold`: a move's reads and its held
 * units), and the reads of archived mail: a post's archived parent, a record by its seq, and
 * the owner's view of a mail chain, archived and hot together. The object hands it its gate,
 * its stores and two test seams (`Internals.archiveBetweenPasses`, `Internals.chainStep`).
 */
import { utf8Decoder } from "../../contract/src/text";
import { sha256Hex } from "../../contract/src/digest";
import { letGo } from "./edit";
import { MOVE_PASS_UNITS, moveTableUnits, ownerSlots } from "./holds";
import {
  isSlotBusy, type Lease as PoolLease, slotBusy, type SlotPool,
} from "../../contract/src/slots";
import { SLOT_WAIT_MS } from "../../contract/src/limits";
import { createHash } from "node:crypto";

import { type Bodies, bodyOffset, type Window, WINDOWS_BYTES } from "./bodies";
import { PAGE_BYTES, type Wants } from "./pages";
import { Refusal } from "../../contract/src/refusal";
import type { Files } from "./files";
import type { OwnerStorage } from "./gate";
import {
  type ArchivedMail, archivedHead, archivedMail, archivedRecipients, keptRecord, type MailParts,
  recordHead, type ChainMail, clock, type Fence, Ledger, STORAGE_BUSY,
} from "./ledger";
import type { Notices } from "./notices";
import {
  chunkReads, type Content as Stored, joined, lineAt, makeChunk, MOVE_BYTES, MOVE_RECORDS,
  MoveTable, pack, parseTrailer, planSegment, roomMembers, SEGMENT_LEAST_BYTES,
  type Source as Run,
} from "./segments";

/**
 * How often a mailbox read reads its placed bodies again when mail that came between its
 * read of them and its unit put an unread placed body first (ADR-035).
 */
export const READ_TRIES = 3;

/**
 * A read of the archive that the lane's moves and rewrites changed under each of its
 * `READ_TRIES`: retried soon, never an internal error (reviews N9 and N10).
 */
export function archiveMoving(): Refusal {
  const retry = 1;
  return new Refusal(STORAGE_BUSY, `${STORAGE_BUSY}: the archive moved this mail under each ` +
    `of ${READ_TRIES} reads; try again in ${retry} second. ` +
    JSON.stringify({ retry_after_s: retry }), { retryAfterS: retry });
}

/** A post's parent, hot at its gate, moved to the archive before its unit (`roomPost`). */
export class ParentMoved extends Error {
  override readonly name = "ParentMoved";
}

/** An archived record read whole in one read when no larger (a chain page's own share). */
const ARCHIVED_WHOLE_BYTES = 64 * 1024;

/** The bytes first read of a larger record for its first two lines (more when they pass it). */
const ARCHIVED_HEAD_BYTES = 16 * 1024;

/** The `archive` lane's failed passes in a row at which the owner is told, once. */
const ARCHIVE_FAILS_TOLD = 3;

/** How long a failed `archive` pass holds its lane. */
const ARCHIVE_RETRY_S = 60;

/**
 * How long a pass refused `busy` holds its lane (review round 11, Lows A and B): the pool's
 * own wait (`SLOT_WAIT_MS`), the term a full queue of larger holds turns over within (the
 * memory leg checks it), so the units held when it was refused have been given back.
 */
const ARCHIVE_BUSY_RETRY_S = SLOT_WAIT_MS / 1000;

/**
 * What the owner is told when a busy pool keeps the archive from moving while the hot set
 * stands at its ceiling: once a blocked spell (since the last segment the lane placed).
 */
const ARCHIVE_BUSY_TOLD = "New mail is held at its ceiling because this server is too busy " +
  `to move older mail out just now; nothing is lost, and the archive tries again every ${
    ARCHIVE_BUSY_RETRY_S} seconds.`;

/**
 * An archive move's reads of stored contents (review N5): each chunk read whole through
 * `FILES` (which rechecks its hash), the last one kept for the next range in it; and each
 * content the move's table gives a hash for (a placed body, its message's) checked as its
 * chunks are first read, in order, in the move's first pass (`bodiesChecked`), one hash at a
 * time, so a move of many placed bodies holds none but the one being read.
 */
class MoveReader {
  readonly #files: Files;
  readonly #table: MoveTable;
  /** Each content's next chunk to hash; its count once checked. */
  readonly #next: Uint32Array;
  #hashing: { ref: number; hash: ReturnType<typeof createHash> } | null = null;
  #checking = true;
  #last: { sha256: string; bytes: Uint8Array } | null = null;

  constructor(files: Files, table: MoveTable) {
    this.#files = files;
    this.#table = table;
    this.#next = new Uint32Array(Math.max(1, table.contents));
  }

  readonly read = async (content: Stored, index: number): Promise<Uint8Array> => {
    const sha256 = content.chunks[index];
    if (sha256 === undefined) {
      throw new Error("a move's range runs past its content");
    }
    let bytes = this.#last?.sha256 === sha256 ? this.#last.bytes : undefined;
    if (bytes === undefined) {
      // The chunk kept is let go before the next arrives: its pieces are taken (`makeChunk`
      // and `lineAt` copy what they keep), so it never outlives its use (`letGo`).
      this.letGo();
      bytes = (await this.#files.fetch([{ sha256 }]))[0];
      if (bytes === undefined) {
        throw new Error("a move's chunk was not read");
      }
      this.#last = { sha256, bytes };
    }
    const ref = Number(content.id);
    if (this.#checking && this.#table.expected(ref) !== null && this.#next[ref] === index) {
      if (this.#hashing?.ref !== ref) {
        this.#hashing = { ref, hash: createHash("sha256") };
      }
      this.#hashing.hash.update(bytes);
      this.#next[ref]!++;
      if (index + 1 === content.chunks.length) {
        if (this.#hashing.hash.digest("hex") !== this.#table.expected(ref)) {
          throw new Error("an archived body is not the body its message holds");
        }
        this.#hashing = null;
      }
    }
    return bytes;
  };

  /** The chunk kept for the next range let go, its buffer detached (`edit.ts`'s `letGo`). */
  letGo(): void {
    if (this.#last !== null) {
      letGo(this.#last.bytes);
      this.#last = null;
    }
  }

  /** After the first pass: every content with a hash read whole and checked, or loudly not. */
  bodiesChecked(): void {
    for (let ref = 0; ref < this.#table.contents; ref++) {
      if (this.#table.expected(ref) !== null
          && this.#next[ref] !== this.#table.contentOf(ref).chunks.length) {
        throw new Error("an archived body is not the body its message holds");
      }
    }
    this.#checking = false;
  }
}

/**
 * `lease` grown to `total` units now, or refused `busy` (everything it holds given back):
 * inside the alarm a pass never waits on the pool, since the object's other lanes would wait
 * with it (round 11, Low B). A call that does not fit at once leaves its queue at the next
 * turn of the event loop; the lane holds and tries again (`ARCHIVE_BUSY_RETRY_S`).
 */
async function now(lease: PoolLease, total: number): Promise<void> {
  const turn = new AbortController();
  const timer = setTimeout(() => turn.abort(slotBusy()), 0);
  try {
    await lease.grow(total, turn.signal);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * An archive move's or rewrite's holds on the isolate's pool (ADR-037), one lease in the
 * larger class (`SlotPool.lease`, the pool's one admission rule), each growth taken now or
 * refused `busy` (`now`: inside the alarm it never waits). Its table's units
 * (`moveTableUnits`), taken before its records arrive and held to its end, since the table's
 * bytes are in hand throughout; grown as a rewrite takes in a neighbour; and each pass's
 * `MOVE_PASS_UNITS` beside them, grown before the pass's first chunk arrives and given back
 * (`Lease.shrink`) once its last is gone: its waits each bring or send its bytes (a chunk
 * read, a chunk put, a unit reading hot records' lines), never a begin or a commit. Refused
 * `busy`, the pass ends with nothing changed: the lane holds and tries again
 * (`ARCHIVE_BUSY_RETRY_S`), not counted as a failure (`#archivePass`).
 */
class MoveHold {
  readonly #pool: SlotPool;
  readonly #lease: PoolLease;
  /** The units its records hold; a pass's are grown past them and given back. */
  #table: number;

  private constructor(pool: SlotPool, lease: PoolLease, table: number) {
    this.#pool = pool;
    this.#lease = lease;
    this.#table = table;
  }

  /** The units for a table of `records` records: a lease in the larger class. */
  static async take(pool: SlotPool, records: number): Promise<MoveHold> {
    const lease = pool.lease("", true);
    const table = moveTableUnits(records);
    await now(lease, table);
    return new MoveHold(pool, lease, table);
  }

  /** The units held for the table. */
  get units(): number {
    return this.#table;
  }

  /** The table grown to hold `records` records: its units grown first. */
  async table(records: number): Promise<void> {
    const table = moveTableUnits(records);
    if (table > this.#table) {
      await now(this.#lease, table);
      this.#table = table;
    }
  }

  /** One pass, holding its own units beside the table's; `letGo` drops what it kept. */
  async pass<T>(act: () => Promise<T>, letGo: () => void): Promise<T> {
    await now(this.#lease, this.#table + MOVE_PASS_UNITS);
    try {
      const done = await act();
      letGo();
      if (this.#pool.observe !== null) {
        await this.#pool.observe(this.#lease.units);
      }
      return done;
    } finally {
      letGo();
      this.#lease.shrink(this.#table);
    }
  }

  release(): void {
    this.#lease.release();
  }
}

/** The owner's notice that the archive keeps failing: its last error as it said it, if any. */
function archiveFailing(said: string | null): string {
  return `Older mail could not be moved out (${said ?? "the last tries stopped before they " +
    "could say why"}), so new mail may wait at the hot ceiling; the archive tries again ` +
    "every minute.";
}

/** An archive move as a test sees it between its passes: its kind, what it replaces, holds. */
export interface ArchiveMoveSeen {
  readonly stream: "mail" | "rewrite";
  readonly replaced: number;
  readonly records: number;
}

/** Where a test may hold a chain read (`Internals.chainStep`). */
export type ChainStep = "fenced" | "hot";

/** What the archive works through: the object's gate and stores, and a test's seams. */
export interface ArchiveParts {
  readonly storage: OwnerStorage;
  /** One unit of the object's write gate (`gate.ts`). */
  readonly door: <T>(act: () => T) => Promise<T>;
  readonly ledger: Ledger;
  readonly files: Files;
  readonly bodies: Bodies;
  readonly notices: Notices;
  /** A page read as the object reads one (`OwnerDO`'s, its placed bodies' windows). */
  readonly rereadingPage: <T>(locate: () => Wants,
                              read: (windows: ReadonlyMap<string, Window>) => T,
                              again: (value: T) => boolean) => Promise<T>;
  /** A test's step between an archive move's passes. */
  readonly betweenPasses: ((storage: OwnerStorage, move: ArchiveMoveSeen) => void) | null;
  /** A test's steps in a chain read. */
  readonly chainStep: ((step: ChainStep) => Promise<void>) | null;
}

/** A chain read's call (`mailChain`), checked again in each of its units. */
export interface ChainDoor {
  check(): { readonly r: Record<string, unknown> };
}

export class Archive {
  readonly #storage: OwnerStorage;
  readonly #door: <T>(act: () => T) => Promise<T>;
  readonly #ledger: Ledger;
  readonly #files: Files;
  readonly #bodies: Bodies;
  readonly #notices: Notices;
  readonly #rereadingPage: ArchiveParts["rereadingPage"];
  readonly #betweenPasses: ArchiveParts["betweenPasses"];
  readonly #chainStep: ArchiveParts["chainStep"];

  constructor(parts: ArchiveParts) {
    this.#storage = parts.storage;
    this.#door = parts.door;
    this.#ledger = parts.ledger;
    this.#files = parts.files;
    this.#bodies = parts.bodies;
    this.#notices = parts.notices;
    this.#rereadingPage = parts.rereadingPage;
    this.#betweenPasses = parts.betweenPasses;
    this.#chainStep = parts.chainStep;
  }

  /** The owner's read of a mail chain, its call checked in each unit (`mailChain`). */
  async chain(door: ChainDoor, r: Record<string, unknown>):
      Promise<{ root: string; messages: ChainMail[]; more: boolean }> {
    const after = r["after"] ?? 0;
    if (typeof after !== "number" || !Number.isSafeInteger(after) || after < 0) {
      throw new Refusal("invalid", "a chain read's after is a seq");
    }
    const offset = bodyOffset(r["body_offset"]);
    // Its root: the hot message's, or the archived one's, read from its segment.
    const start = await this.#door(() => this.#ledger.chainStart(r["message_id"]));
    let root: string;
    let rootSeq: number;
    let room: string;
    if ("archived" in start) {
      const found = await this.archived(start.archived, r["message_id"] as string);
      if (found === null) {
        throw new Refusal("refused", "no such message held");
      }
      root = found.message.chain_root;
      room = found.message.room_id;
      rootSeq = await this.#door(() => this.#ledger.seqOf(root)) ?? 0;
    } else {
      ({ root, root_seq: rootSeq, room } = start);
    }
    const most = typeof r["limit"] === "number" ? r["limit"] : 1;
    for (let tries = 0; tries < READ_TRIES; tries++) {
      const read = await this.#chainPage(door, r, { root, rootSeq, room }, after, offset,
                                         most);
      if (read !== null) {
        return { root, messages: read.messages, more: read.more };
      }
    }
    throw archiveMoving();
  }

  /**
   * One read of a chain's page (`mailChain`), or null when the archive changed under it.
   * Its archived messages past `after`, as a page reads placed bodies (`pages.ts`, PR 1):
   * the next segments' chain maps, then each record by range, its body only as the window
   * a page may show (from `body_offset` when it is the page's first message), the windows
   * stopping at `WINDOWS_BYTES` past the first; the first left unread is the frontier, so
   * the page stops below it and the next goes on from there. Each fence's content and chunks
   * are located in the fences' own unit, with the archive's mark (review N10); a segment
   * replaced after it (a merge, a compaction, a purge's rewrite) is read from its chunks in
   * their grace, and a read that fails all the same (a chunk collected early, past the figure)
   * is a changed archive, read again, when the mark moved (`#segmentsRead`). Its hot rows are
   * read in a later unit, which first checks that no segment made since may hold the chain's
   * room past `after`: a move between them took mail out of the hot rows into a segment the
   * read never saw (review N9), so the read is null, and `mailChain` reads again from the
   * fences. Another room's moves never count. The page stays the one bounded page
   * `ledger.chain` fits; only the reads are repeated, at most `READ_TRIES`.
   */
  async #chainPage(door: ChainDoor, r: Record<string, unknown>,
                   chain: { root: string; rootSeq: number; room: string }, after: number,
                   offset: number, most: number):
      Promise<{ messages: ChainMail[]; more: boolean } | null> {
    const { root, rootSeq, room } = chain;
    const { fenced, firstHot, seen, located } = await this.#door(() => {
      const fences = this.#ledger.chainFences(rootSeq, after);
      return { fenced: fences, firstHot: this.#ledger.chainNext(root, after),
               seen: this.#ledger.archiveMark(),
               located: this.#bodies.locate(fences.fences.map((fence) => fence.content_id)) };
    });
    await this.#chainStep?.("fenced");
    let frontier = fenced.frontier;
    const mail: ArchivedMail[] = [];
    const read = await this.#segmentsRead(seen.mark, async () => {
      let held = 0;
      read: for (const fence of fenced.fences) {
        const chunks = located.get(fence.content_id)!.chunks;
        const trailer = await this.#trailer(fence, chunks);
        for (const [key, at, length] of trailer.chains[root] ?? []) {
          if (key <= after) {
            continue;
          }
          if (mail.length >= most || (mail.length > 0 && held >= WINDOWS_BYTES)) {
            frontier = key;
            break read;
          }
          const first = mail.length === 0 && (firstHot === null || key < firstHot);
          const found = await this.#archivedRecord(chunks, at, length, first ? offset : 0);
          held += found.body === null ? 0 : found.body.end - found.body.offset;
          mail.push(found);
        }
      }
      return true;
    });
    if (read === null) {
      return null;
    }
    await this.#chainStep?.("hot");
    // Its hot messages' placed bodies (ADR-035) are read from R2 between its units, and the
    // page, hot and archived together, fits one answer.
    const archived = { root, mail, frontier };
    const page = (windows: ReadonlyMap<string, Window>) => this.#ledger.chain(
      r["message_id"], after, r["limit"], windows, offset, r["body_message_id"] ?? null,
      archived);
    return this.#rereadingPage(() => {
      door.check();
      const read = page(new Map());
      return { contents: read.placed,
               offsetOf: (contentId) => contentId === read.first ? offset : 0 };
    }, (windows) => {
      door.check();
      return this.#ledger.movedSince(seen.newest, room, after) ? null : page(windows);
    }, (read) => read !== null && read.messages.length === 0 && read.placed.length > 0);
  }

  // -- THE ARCHIVE (slice STORE item 2, `segments.ts`) --------------------------------------

  /**
   * Segments' reads from R2, outside any unit, after the unit that took their fences, their
   * located chunks and the archive's `mark`: the reads' value, or null when they failed
   * because the archive changed since (the mark moved), for the caller to read again. A
   * segment replaced since (a merge, a compaction, a purge's rewrite) is let go as garbage
   * in its grace (`GC_GRACE_S`, 30 days), so its chunks still read for far longer than one
   * read, and their hashes still check; but past the owner's figure the oldest garbage is
   * collected early (`files.ts`), and a just-replaced segment's chunks, the newest, could
   * still go, so a failed read is retried when the mark moved. With the mark unchanged, the
   * failure is no change of the archive's, and stands, loudly.
   */
  async #segmentsRead<T>(mark: string, read: () => Promise<T>): Promise<T | null> {
    try {
      return await read();
    } catch (error) {
      if (await this.#door(() => this.#ledger.archiveMark().mark !== mark)) {
        return null;
      }
      throw error;
    }
  }

  /**
   * A byte range of a segment's content, read from R2 outside any unit by the verified
   * ranges it spans (`FILES` reads each whole chunk, rechecks its hash and answers the range).
   */
  async #segmentBytes(chunks: readonly string[], offset: number, length: number):
      Promise<Uint8Array> {
    return joined(await this.#files.fetch(chunkReads(chunks, offset, length)));
  }

  /** A segment's trailer, read alone by its fence's range. */
  async #trailer(fence: Fence, chunks: readonly string[]):
      Promise<ReturnType<typeof parseTrailer>> {
    return parseTrailer(utf8Decoder("keep").decode(
      await this.#segmentBytes(chunks, fence.trailer_offset, fence.trailer_bytes)));
  }

  /**
   * An archived record read by range, never its body whole (PR 1): a small one in one read;
   * a larger one its head (its message's row and its body's), its recipients' lines, and
   * its body's window from `offset` (at most `PAGE_BYTES`, cut back to a character), or no
   * body at all (`offset` null: a reply's parent needs only its row).
   */
  async #archivedRecord(chunks: readonly string[], at: number, length: number,
                        offset: number | null): Promise<ArchivedMail> {
    if (length <= ARCHIVED_WHOLE_BYTES) {
      return archivedMail(await this.#segmentBytes(chunks, at, length), offset);
    }
    let head = null;
    for (let read = ARCHIVED_HEAD_BYTES; head === null; read *= 4) {
      head = archivedHead(await this.#segmentBytes(chunks, at, Math.min(length, read)));
      if (head === null && read >= length) {
        throw new Error("an archived mail record lacks its message or its body");
      }
    }
    const bodyAt = at + head.bodyAt;
    const tail = bodyAt + head.bodyBytes + 1;
    const recipients = archivedRecipients(await this.#segmentBytes(chunks, tail,
                                                                   at + length - tail));
    return { message: head.message, recipients, body: offset === null ? null
      : await this.#bodies.windowAt(chunks, bodyAt, head.bodyBytes, offset, PAGE_BYTES) };
  }

  /**
   * An archived message by the seq its id carries, from its segment; null when none. Its
   * fences' contents are located with them and the archive's mark, and a read that failed
   * because the archive changed is read again (`#segmentsRead`, review N10), at most
   * `READ_TRIES`, then refused `storage_busy`.
   */
  async archived(seq: number, messageId: string): Promise<ArchivedMail | null> {
    for (let tries = 0; tries < READ_TRIES; tries++) {
      const { fences, seen, located } = await this.#door(() => {
        const fences = this.#ledger.archiveFences(seq);
        return { fences, seen: this.#ledger.archiveMark(),
                 located: this.#bodies.locate(fences.map((fence) => fence.content_id)) };
      });
      const found = await this.#segmentsRead(seen.mark, async () => {
        for (const fence of fences) {
          const chunks = located.get(fence.content_id)!.chunks;
          const placement = (await this.#trailer(fence, chunks)).keys
            .find(([key]) => key === seq);
          if (placement !== undefined) {
            return { mail: await this.#archivedRecord(chunks, placement[1], placement[2], null) };
          }
        }
        return { mail: null };
      });
      if (found === null) {
        continue;
      }
      if (found.mail === null) {
        return null;
      }
      const mail = found.mail;
      const purged = await this.#door(() => this.#ledger.purgedAgents());
      return mail.message.message_id === messageId
        && !roomMembers(mail.message.room_id).some((member) => purged.includes(member))
        ? mail : null;
    }
    throw archiveMoving();
  }

  /** In a unit: a post's parent's seq, when it is archived (not hot, and the object's). */
  archivedParent(request: unknown): { id: string; seq: number } | null {
    const parentId = (request as { parent_id?: unknown } | null)?.parent_id;
    if (typeof parentId !== "string") {
      return null;
    }
    try {
      const start = this.#ledger.chainStart(parentId);
      return "archived" in start ? { id: parentId, seq: start.archived } : null;
    } catch {
      return null;  // not one the object gave: the post refuses it as no parent
    }
  }

  /**
   * One pass of the `archive` lane: a deleted agent's mail rewritten out of a segment, or
   * else one move of acked mail (a straggler's merge, or the tail's new segment). A move
   * that would leave the storage figure higher than it found it is refused for the figure
   * (`archive_full`): the archive is then blocked, and a post at the hot ceiling is the one
   * wall, `storage_full`, until the owner frees space (review M1). A failure outside (the
   * storage service down) holds the lane a while. Each pass is counted in storage as it
   * begins and cleared as it ends (review N3), so a pass killed with its isolate (for memory,
   * say) still counts: at `ARCHIVE_FAILS_TOLD` in a row the owner is told once, with the last
   * error as it said it, or that the passes stopped before they could say.
   */
  async pass(): Promise<unknown> {
    let begun: { fails: number; error: string | null } | null = null;
    try {
      // Blocked and still full: nothing to try until the owner frees space.
      begun = await this.#door(() => {
        const { stored, figure } = this.#files.storedNow();
        return this.#ledger.archiveBlocked() && figure !== null && stored >= figure ? null
          : this.#ledger.archiveBegin();
      });
      if (begun === null) {
        return { hold: clock() + ARCHIVE_RETRY_S };
      }
      if (begun.fails === ARCHIVE_FAILS_TOLD && begun.error === null) {
        // The third failed pass never reached its catch: it went with its isolate.
        await this.#door(() => this.#notices.add("storage_busy_long", archiveFailing(null),
                                                 clock()));
      }
      // A purge's rewrite first, so a deleted agent's mail leaves R2 promptly; the order is
      // not needed for correctness (a merge into a segment that still holds it keeps it,
      // and its bloom names the agent, so the purge rewrites that segment in turn). A
      // rewrite that fails never holds the move behind it (review N7): the move runs, and
      // the rewrite's failure is the pass's, counted and told at three.
      // Each holds its own units of the isolate's pool (`MoveHold`), released as it ends.
      let rewrote = false;
      let failed: unknown = null;
      try {
        rewrote = await this.#archiveRewrite();
      } catch (error) {
        if (error instanceof Refusal && error.code === "archive_changed") {
          rewrote = true;
        } else {
          failed = error;
        }
      }
      // The move runs whenever mail is due (the hot set past half), a rewrite or not, so a
      // long purge never holds new mail at `storage_busy` (review N8); the rewrite's table
      // and units are let go before it, so the pass holds one move's at a time.
      if (!rewrote || await this.#door(() => this.#ledger.archiveDue())) {
        await this.#archiveMove();
      }
      if (failed !== null) {
        throw failed;
      }
      await this.#door(() => this.#ledger.archiveEnded());
      return undefined;
    } catch (error) {
      if (error instanceof Refusal && error.code === "archive_changed") {
        await this.#door(() => this.#ledger.archiveEnded());
        return undefined;  // the batch moved under it: the next pass picks again
      }
      if (error instanceof Refusal && (error.code === "archive_full"
                                       || error.code === "storage_full")) {
        const first = await this.#door(() => {
          const was = this.#ledger.archiveBlocked();
          this.#ledger.blockArchive();
          this.#ledger.archiveEnded();  // its own cause, told in its own words
          return !was;
        });
        if (first) {
          await this.#door(() => this.#notices.add("storage_busy_long",
            "Older mail could not be moved out because your storage is full, so new mail is " +
              "refused until you free some space or upgrade.", clock()));
        }
        return { hold: clock() + ARCHIVE_RETRY_S };
      }
      if (isSlotBusy(error)) {
        // The isolate's pool full (`busy`, `MoveHold`): contention, not storage (round 11,
        // Low A). Not a failed pass, never logged: the lane tries again once the pool's own
        // wait has turned its queue over. The owner is told only when it puts storage at risk,
        // the hot set at its ceiling (the next post refused `storage_busy`) with the moves
        // blocked: once a blocked spell, since the last segment the lane placed.
        await this.#door(() => {
          if (begun !== null) {
            this.#ledger.archiveBusy(begun);
          }
          if (this.#ledger.atHotCeiling()) {
            const since = this.#ledger.lastPlaced();
            if (!this.#notices.list().some((notice) => notice.type === "storage_busy_long"
                && notice.detail === ARCHIVE_BUSY_TOLD && notice.at >= since)) {
              this.#notices.add("storage_busy_long", ARCHIVE_BUSY_TOLD, clock());
            }
          }
        });
        return { hold: clock() + ARCHIVE_BUSY_RETRY_S };
      }
      console.error("the archive lane's pass failed", error);
      if (begun !== null) {
        // What failed, as it said it, never a cause it did not give (review N1).
        const said = (error instanceof Error ? error.message.split("\n")[0]! : String(error))
          .slice(0, 200);
        const fails = begun.fails + 1;
        await this.#door(() => {
          this.#ledger.archiveFailed(said);
          if (fails === ARCHIVE_FAILS_TOLD) {
            this.#notices.add("storage_busy_long", archiveFailing(said), clock());
          }
        });
      }
      return { hold: clock() + ARCHIVE_RETRY_S };
    }
  }

  /**
   * One move: a straggler's merge (acked mail whose keys fall in a segment, rewritten into
   * it, so no two segments overlap) or the tail's new segment, in the move's two units.
   * Planned from lengths alone, then streamed (`#placePieces`): nothing is read whole.
   */
  async #archiveMove(): Promise<void> {
    const pick = await this.#door(() => this.#ledger.archivePick());
    if (pick === null) {
      return;
    }
    // Its table's units before its records arrive: the batch's, and its segment's.
    const hold = await MoveHold.take(ownerSlots(), pick.seqs.length + (pick.into?.records ?? 0));
    try {
      const table = new MoveTable();
      const batch = await this.#door(() => this.#ledger.archiveBatch(pick.seqs, table,
        (contentId, bytes, sha256) => table.addContent(
          bytes, this.#bodies.locate([contentId]).get(contentId)!.chunks, sha256)));
      if (batch === null) {
        return;
      }
      const reader = new MoveReader(this.#files, table);
      if (pick.into !== null) {
        const into = pick.into;
        await hold.pass(() => this.#scanSegment(into, table, reader), () => reader.letGo());
      }
      const all = Array.from({ length: table.count }, (_, n) => n);
      await this.#placePieces(table, all, pick.into === null ? [] : [pick.into], pick.seqs,
                              batch.digest, "mail", reader, hold);
    } finally {
      hold.release();
    }
  }

  /**
   * One rewrite pass: the next segment a deleted agent's mail may be in, without that mail;
   * one left under `SEGMENT_LEAST_BYTES` takes in its neighbours by key (never by id), so
   * ranges stay disjoint. Each segment is scanned with the purge's filter: only what is kept
   * enters the move's table, the rest streams past (review N7).
   *
   * Bounded, compacting across passes: a pass takes in a neighbour only while what it keeps
   * and the neighbour's records stay within `MOVE_RECORDS` and the neighbours it scans
   * within `MOVE_BYTES` (a merge's work), and stops there. A pass stopped so, still under the
   * least, places what it kept as one small segment: the next pass, finding a segment under
   * the least that is not the only one (`purgeNext`, derived from the fences alone, so a
   * restore keeps it), takes it back as its target and goes on taking in its neighbours,
   * and so on until it holds the least or stands alone. Each pass leaves the archive whole and disjoint, only less compacted
   * until the last; the first neighbour always fits (a small segment's records are under
   * the least's worth and a piece's at most 49,933), so every pass makes progress. False
   * when no rewrite waits.
   */
  async #archiveRewrite(): Promise<boolean> {
    const target = await this.#door(() => this.#ledger.purgeNext());
    if (target === null) {
      return false;
    }
    const purged = new Set(await this.#door(() => this.#ledger.purgedAgents()));
    const keep = (room: string) => !roomMembers(room).some((member) => purged.has(member));
    // Its table's units before its records arrive, grown as it takes in each neighbour.
    const hold = await MoveHold.take(ownerSlots(), target.fence.records);
    try {
      return await this.#rewriteHeld(target, keep, hold);
    } finally {
      hold.release();
    }
  }

  /** `#archiveRewrite` holding its table's units (`hold`, for the target's records). */
  async #rewriteHeld(target: { fence: Fence; compact: boolean },
                     keep: (room: string) => boolean, hold: MoveHold): Promise<boolean> {
    const table = new MoveTable();
    const scan = (fence: Fence) => {
      const reader = new MoveReader(this.#files, table);
      return hold.pass(() => this.#scanSegment(fence, table, reader, keep),
                       () => reader.letGo());
    };
    const first = await scan(target.fence);
    if (first.kept === first.all && !target.compact) {
      await this.#door(() => this.#ledger.purgeSkip(target.fence.segment));
      return true;
    }
    const replaced = [target.fence];
    let size = 0;
    for (let n = 0; n < table.count; n++) {
      size += table.length(n);
    }
    let scanned = 0;
    // Left small, it takes in its neighbours by key (a neighbour a purge also thinned may
    // not be enough alone), within the pass's bound, until it holds the least or none is
    // left: never by id.
    while (size < SEGMENT_LEAST_BYTES) {
      const span = { ...target.fence,
                     first_key: Math.min(...replaced.map((fence) => fence.first_key)),
                     last_key: Math.max(...replaced.map((fence) => fence.last_key)) };
      const beside = await this.#door(() => this.#ledger.fenceBeside(span));
      if (beside === null) {
        break;
      }
      if (table.count + beside.records > MOVE_RECORDS || scanned + beside.bytes > MOVE_BYTES) {
        break;  // the pass's bound: the next pass goes on from what this one places
      }
      replaced.push(beside);
      scanned += beside.bytes;
      // What it keeps so far and all the neighbour's: within `MOVE_RECORDS` (checked above).
      await hold.table(table.count + beside.records);
      const from = table.count;
      await scan(beside);
      for (let n = from; n < table.count; n++) {
        size += table.length(n);
      }
    }
    if (target.compact && replaced.length === 1) {
      return false;  // a small segment with no neighbour to take in stands alone, as it may
    }
    const all = Array.from({ length: table.count }, (_, n) => n);
    await this.#placePieces(table, all, replaced, [], "0".repeat(64), "rewrite",
                            new MoveReader(this.#files, table), hold);
    return true;
  }

  /**
   * A segment's records appended to a move's table as ranges of its content, never held
   * (review N5): its trailer read, then each record's first line alone (its room, its chain,
   * its time), the chunks read whole one at a time, in order. Only the records `keep` takes
   * (by room) enter the table (review N7: a purge's dropped mail streams past). Its records
   * read, and those kept.
   */
  async #scanSegment(fence: Fence, table: MoveTable, reader: MoveReader | null,
                     keep: (room: string) => boolean = () => true):
      Promise<{ all: number; kept: number }> {
    const located = await this.#door(() => this.#bodies.locate([fence.content_id]));
    const ref = table.addContent(fence.bytes, located.get(fence.content_id)!.chunks, null);
    const content = table.contentOf(ref);
    const read = (reader ?? new MoveReader(this.#files, table)).read;
    const trailer = parseTrailer(utf8Decoder("keep")
      .decode(await lineAt(content, fence.trailer_offset, fence.trailer_bytes, read)));
    let kept = 0;
    for (const [key, offset, length] of trailer.keys) {
      const head = recordHead(await lineAt(content, offset, length, read));
      if (keep(head.room)) {
        keptRecord(table, key, offset, length, ref, head);
        kept++;
      }
    }
    return { all: trailer.keys.length, kept };
  }

  /**
   * A move's records (`indices` into its table) placed as one or two segments (`pack`),
   * replacing `replaced` and the hot batch `seqs` in its second unit (ADR-035's order: the
   * bytes to R2 first, held by uploads that may pass the storage figure, then named in one
   * unit, which may not leave the stored figure above both the figure and where the move
   * found it: `archive_full`). Streamed (review N5, the memory research's §2 A): each piece
   * planned from its records' lengths; pass 1 makes each chunk from the source ranges it
   * covers, hashes it and drops it (and checks each placed body against its message's hash),
   * for `Files.begin`; pass 2 makes each again and sends it, one a call. The sources do not
   * change between the passes (contents are named by their hashes; the batch's rows are
   * checked at the commit by their digest), and a chunk that did differ is refused by R2's
   * check of its hash: loudly.
   */
  async #placePieces(table: MoveTable, indices: readonly number[], replaced: readonly Fence[],
                     seqs: readonly number[], digest: string, stream: "mail" | "rewrite",
                     reader: MoveReader, hold: MoveHold): Promise<void> {
    const ordered = [...indices].sort((a, b) => table.key(a) - table.key(b));
    const plans = ordered.length === 0 ? []
      : pack(ordered, SEGMENT_LEAST_BYTES, (n) => table.length(n)).map((part) => {
        const piece = table.piece(Uint32Array.from(part));
        return { plan: planSegment("mail", piece), indices: piece.indices };
      });
    const hashes: string[][] = [];
    await hold.pass(async () => {
      for (const { plan, indices: at } of plans) {
        const sources = (from: number, to: number) =>
          this.#moveSources(table, at.subarray(from, to));
        const each: string[] = [];
        for (let n = 0; n < plan.chunks; n++) {
          const made = await makeChunk(plan, n, sources, reader.read);
          each.push(sha256Hex(made));
          letGo(made);
        }
        hashes.push(each);
      }
    }, () => reader.letGo());
    reader.bodiesChecked();
    if (this.#betweenPasses !== null) {
      const between = this.#betweenPasses;
      await this.#door(() => between(this.#storage, { stream, replaced: replaced.length,
                                                     records: table.count }));
    }
    const pieces = plans.map(({ plan, indices: at }) => {
      const members = new Set<string>();
      let firstAt = Number.MAX_SAFE_INTEGER;
      let lastAt = 0;
      for (const n of at) {
        for (const member of roomMembers(table.room(n))) {
          members.add(member);
        }
        firstAt = Math.min(firstAt, table.at(n));
        lastAt = Math.max(lastAt, table.at(n));
      }
      return { plan, at, fence: {
        first_key: plan.first, last_key: plan.last, first_at: firstAt, last_at: lastAt,
        records: plan.records.count, bytes: plan.size,
        trailer_offset: plan.trailer_offset, trailer_bytes: plan.trailer_bytes,
        members: Ledger.members(members) } };
    });
    const begun = await this.#door(() => {
      const before = this.#files.storedNow().stored;
      const started = pieces.map((piece, n) => this.#files.begin(
        piece.plan.size, hashes[n]!, "owner", [], true, null, true));
      this.#ledger.moveBegun(stream, started[0]?.intent_id ?? "0".repeat(32), digest, clock(),
                             before);
      return { started, before };
    });
    try {
      await hold.pass(async () => {
        for (const [n, piece] of pieces.entries()) {
          const sources = (from: number, to: number) =>
            this.#moveSources(table, piece.at.subarray(from, to));
          const wanted = new Set(begun.started[n]!.missing);
          for (const [k, sha256] of hashes[n]!.entries()) {
            if (wanted.delete(sha256)) {
              const made = await makeChunk(piece.plan, k, sources, reader.read);
              await this.#files.upload(begun.started[n]!.intent_id, [{ sha256, bytes: made }]);
              letGo(made);
            }
          }
        }
      }, () => reader.letGo());
      await this.#door(() => {
        const named: { content_id: string; fence: (typeof pieces)[number]["fence"] }[] = [];
        const adopt = (n: number): void => {
          if (n === pieces.length) {
            this.#ledger.archiveCommit(seqs, digest, replaced, named, clock());
            const { stored, figure } = this.#files.storedNow();
            if (figure !== null && stored > Math.max(figure, begun.before)) {
              throw new Refusal("archive_full",
                                "archive_full: the move would leave the storage figure higher " +
                                "than it found it");
            }
            this.#ledger.moveEnded(stream);
            return;
          }
          this.#files.adopt(begun.started[n]!.intent_id, "owner", (contentId) => {
            named.push({ content_id: contentId, fence: pieces[n]!.fence });
            adopt(n + 1);
          });
        };
        adopt(0);
      });
    } finally {
      await this.#door(() => {
        for (const started of begun.started) {
          this.#files.abort(started.intent_id);
        }
        this.#ledger.moveEnded(stream, begun.started[0]?.intent_id ?? "0".repeat(32));
      });
    }
  }

  /**
   * The sources of a group of a move's records (`at`, their indices in its table), in their
   * order: a kept one's range of its segment; a hot one's lines, read again in a unit,
   * around its body (inline, or its placed content's whole range).
   */
  async #moveSources(table: MoveTable, at: Uint32Array): Promise<(readonly Run[])[]> {
    const hot: number[] = [];
    for (const n of at) {
      if (table.kind(n) !== MoveTable.KEPT) {
        hot.push(table.key(n));
      }
    }
    const parts = hot.length === 0 ? new Map<number, MailParts>()
      : await this.#door(() => this.#ledger.archiveParts(hot));
    return Array.from(at, (n): Run[] => {
      if (table.kind(n) === MoveTable.KEPT) {
        return [{ content: table.content(n), offset: table.offset(n), length: table.length(n) }];
      }
      const own = parts.get(table.key(n))!;
      if (table.kind(n) === MoveTable.INLINE) {
        return [own.head, own.inline!, own.tail];
      }
      const content = table.content(n);
      return [own.head, { content, offset: 0, length: content.size }, own.tail];
    });
  }
}
