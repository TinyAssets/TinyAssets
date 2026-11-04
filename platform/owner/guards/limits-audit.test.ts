/**
 * The limits that ship (`openspec/changes/clean-sheet-platform/limits-audit.md`), costed by
 * the owner-bound guard's own terms (`termsFor`): the owner object's worst case at the
 * validators' widest names, at or under half the 10 GB (the one ceiling), and each term's share, printed
 * for the audit. The structural changes the raises needed first (ADR-035) are what let them
 * fit: large bodies placed in R2, the collections' rows and history counted in the bytes
 * they take, one recipient a message, the mailboxes' caps within the ceilings, grant scopes
 * in bytes. Each is shown here to be what lifts its raise: without it, the guard breaks.
 */
import { describe, expect, it } from "vitest";

import { BODY_BYTES_MAX } from "../src/bodies";
import { LIMITS as COLLECTIONS } from "../src/collections";
import { checkLimits, LEDGER_LIMITS } from "../src/ledger";
import { CHUNK_BYTES, PLACE_BYTES } from "../src/manifest";
import { WORST_FILL } from "../src/manifest";
import {
  DATABASE_BYTES, IMAGE_MAX, LOG_BYTES, LOG_ROW_BYTES, RUNTIME, SHARE, type Term, termBytes,
  TERMS, worstCase,
} from "./owner-bound.test";
import {
  ARCHIVE_SEGMENTS_MAX, MIN_RECORD_BYTES, MOVE_BYTES, pack, recordBytes, SEGMENT_LEAST_BYTES,
  SEGMENT_MIN_BYTES, SEGMENT_MIN_RECORDS, type SegmentRecord, TIER_STORAGE_MAX,
} from "../src/segments";

/** The isolate's memory (Workers limits), as the memory guard counts it. */
const ISOLATE_BYTES = 128 * 1000 * 1000;

const HALF = SHARE * DATABASE_BYTES;
/**
 * The room under half when `LOG_BYTES` was derived (2026-11-03: main at `8161540c1`, slices
 * L, APP and SIGNIN, with slice P's tables, a connection counted in bytes since review L5-1),
 * every term but the change log's at its worst.
 */
const DERIVED_ROOM = 665_127_185.5;
/**
 * The change log's share of that room: a quarter, an equal share with each of the three
 * slices still to land (STORE, STARTER, BRAIN), whose terms take the rest. The room exists
 * so a generation's held changes survive an R2 outage, so the log takes a full share.
 */
const LOG_SHARE = 1 / 4;
const ROW = 256;
/** History revisions a row kept, as rows times revisions were counted before. */
const REVISIONS = 100;
/** The rows the collections' charge holds at the narrowest names, as rows were counted. */
const ROWS = Math.floor(COLLECTIONS.maxRowsCharge / 1024);
const total = (terms: Readonly<Record<string, Term>>) => worstCase({ ...terms, ...RUNTIME });

/**
 * The shipped bounds' terms as they would be without one structural change: what each change
 * buys, counted the way the guard counted it before.
 */
const WITHOUT: Readonly<Record<string, () => Readonly<Record<string, Term>>>> = Object.freeze({
  "bodies placed in R2 past PLACE_BYTES": () => ({ ...TERMS,
    message_bodies: { rows: LEDGER_LIMITS.maxMessages, bytes: ROW + BODY_BYTES_MAX,
                      by: "every body in its row" } }),
  "collection history counted in bytes, not rows times revisions": () => ({ ...TERMS,
    collection_history: { rows: ROWS * REVISIONS, bytes: ROW + PLACE_BYTES,
                          by: "a hundred revisions a row" } }),
  "the ledger's recipients bounded by their own count, not messages times agents": () => ({
    ...TERMS, ledger_recipients: { rows: LEDGER_LIMITS.maxMessages * LEDGER_LIMITS.maxAgents,
                                   bytes: ROW, by: "each message to each agent" } }),
  "grant scopes counted in bytes, not 512 code points of JSON each": () => ({ ...TERMS,
    outside_clients: { rows: 2_000, bytes: ROW + 8 + 1_000 * (256 * (3 * 512 * 6 + 64)) / 2_000,
                       by: "256 scopes of three 512-point fields" } }),
});

describe("the change log's bound", () => {
  it("is derived from the room under half: the largest whole MiB within its share", () => {
    const share = DERIVED_ROOM * LOG_SHARE;
    const largest = Math.floor((share / WORST_FILL - IMAGE_MAX - LOG_ROW_BYTES) / 2 ** 20)
      * 2 ** 20;
    expect(LOG_BYTES).toBe(largest);
    expect(LOG_BYTES).toBeGreaterThanOrEqual(2 ** 20);
    // The share fits the room as it stands: a later slice whose terms take more fails the
    // half below, loudly, and the log's share is decided again then.
    const { change_log: _log, ...others } = TERMS;
    expect(termBytes("change_log", TERMS["change_log"]!))
      .toBeLessThanOrEqual(HALF - total(others));
  });
});

describe("slice STORE's archive (item 2)", () => {
  it("takes at most STORE's quarter of the room LOG_BYTES was derived from", () => {
    const { change_log: _log, ...others } = TERMS;
    expect(HALF - total(others)).toBeGreaterThanOrEqual(DERIVED_ROOM * (1 - LOG_SHARE));
  });

  it("sets the unread ceiling at the hot ceiling less one segment's records", () => {
    // The guard costs the three hot terms (a message, its body, its recipient) at
    // `maxMessages` rows, so any unread count up to it fits under half. The one binding
    // constraint: at the hot ceiling a segment's records stay acked, so the lane always has
    // one to move. So 50,000 - 25,000. (`maxMessages` is a chosen hot size: the room left
    // under half is STARTER's and BRAIN's quarters, so it cannot grow.)
    const L = LEDGER_LIMITS;
    for (const table of ["ledger_messages", "message_bodies", "ledger_recipients"]) {
      expect(TERMS[table]!.rows, table).toBe(L.maxMessages);
    }
    expect(L.maxUnacked).toBe(L.maxMessages - L.archiveRecords);
    expect(L.archiveRecords).toBe(SEGMENT_MIN_RECORDS);
    // The hot share fits under half beside every other term (the guard's own check), and the
    // room left is no more than STARTER's and BRAIN's quarters and a little.
    expect(HALF - total(TERMS)).toBeLessThan(DERIVED_ROOM * 2 * LOG_SHARE + 50e6);
  });

  it("cuts segments only where both sides hold the least, so ARCHIVE_SEGMENTS_MAX holds " +
     "(review N2, the reviewer's rvsh2_pack case)", () => {
    const message = (key: number, body: string): SegmentRecord => ({
      key, chain: `msg_${String(key).padStart(32, "0")}`,
      rows: [["ledger_messages", { seq: key, body }]] });
    const size = recordBytes;
    // One small message, a straggler with a body at the cap, then small mail just short of
    // the two: past twice the least, where the old cut left a 698-byte first half.
    const first = message(0, "x".repeat(400));
    const straggler = message(1, "y".repeat(BODY_BYTES_MAX));
    const head = size(first) + size(straggler);
    const small = size(message(2, "x".repeat(400)));
    const rest: SegmentRecord[] = [];
    for (let key = 2, bytes = 0; bytes + small < head; key++, bytes += small) {
      rest.push(message(key, "x".repeat(400)));
    }
    const records = [first, straggler, ...rest];
    expect(records.reduce((sum, record) => sum + size(record), 0))
      .toBeGreaterThan(2 * SEGMENT_LEAST_BYTES);
    const bytesOfPiece = (piece: SegmentRecord[]) =>
      piece.reduce((sum, record) => sum + size(record), 0);
    const pieces = pack(records, SEGMENT_LEAST_BYTES);
    expect(pieces.every((piece) => bytesOfPiece(piece) >= SEGMENT_LEAST_BYTES)).toBe(true);
    expect(pieces.flat().map((record) => record.key)).toEqual(records.map((r) => r.key));
    // Many small records past twice the least: several pieces, each the least or more.
    const many = Array.from({ length: 30_000 }, (_, key) => message(key, "z".repeat(700)));
    const cut = pack(many, SEGMENT_LEAST_BYTES);
    expect(cut.length).toBeGreaterThan(1);
    expect(cut.every((piece) => bytesOfPiece(piece) >= SEGMENT_LEAST_BYTES)).toBe(true);
    expect(cut.flat().length).toBe(many.length);
    // At most twice the least, one piece, whatever is in it (a lone one may be small).
    expect(pack([first], SEGMENT_LEAST_BYTES)).toEqual([[first]]);
  });

  it("holds a move within the isolate: MOVE_BYTES of records and its chunks (review N3)", () => {
    // The worst merge, measured in workerd (round 3, `limits-audit.md`): its live heap grew
    // 39.8 MiB at 31.8 MiB of records, two chunks being sent.
    const MEASURED = 39.8 * 1024 * 1024;
    // `pack`'s largest piece (twice the least and a record) and the one straggler always
    // taken: the forced merge is within the budget but for two records' lines.
    expect(MOVE_BYTES).toBeGreaterThanOrEqual(2 * SEGMENT_LEAST_BYTES + 2 * BODY_BYTES_MAX);
    // Its records, the two chunks being sent and the service binding's copy of them, and
    // the measured peak with that copy, each within half the isolate.
    expect(MOVE_BYTES + 4 * CHUNK_BYTES).toBeLessThanOrEqual(ISOLATE_BYTES / 2);
    expect(MEASURED + 2 * CHUNK_BYTES).toBeLessThanOrEqual(ISOLATE_BYTES / 2);
  });

  it("lets a tier offer about 0.5 TB: the fence rows, each a sealed segment's least bytes",
     () => {
    expect(TIER_STORAGE_MAX).toBe((ARCHIVE_SEGMENTS_MAX - 1) * SEGMENT_LEAST_BYTES);
    expect(TIER_STORAGE_MAX).toBeGreaterThanOrEqual(0.5e12);
    expect(TIER_STORAGE_MAX).toBeLessThan(0.51e12);
    expect(SEGMENT_LEAST_BYTES).toBe(Math.min(SEGMENT_MIN_BYTES,
                                              SEGMENT_MIN_RECORDS * MIN_RECORD_BYTES));
    expect(TERMS["archive_segments"]!.rows).toBe(ARCHIVE_SEGMENTS_MAX);
  });
});

describe("the limits that ship", () => {
  it("hold the owner's worst case at or under half the 10 GB, the real ceiling", () => {
    expect(total(TERMS)).toBe(worstCase());
    expect(worstCase()).toBeLessThanOrEqual(HALF);
  });

  it("pass the ledger's own load-time check", () => {
    expect(() => checkLimits(LEDGER_LIMITS)).not.toThrow();
  });

  it("fit only with their structural changes: without each, the guard breaks", () => {
    for (const [change, without] of Object.entries(WITHOUT)) {
      expect(total(without()), change).toBeGreaterThan(HALF);
    }
    // The unread ceiling leaves the hot ceiling a segment to move (slice STORE item 2).
    const L = LEDGER_LIMITS;
    expect(L.maxUnacked + L.archiveRecords).toBeLessThanOrEqual(L.maxMessages);
  });

  it("move no term for a body's own size, or for the bytes R2 holds", () => {
    // The collections are costed by their charge, the messages' bodies at PLACE_BYTES.
    expect(TERMS["collection_rows"]).toMatchObject({ charged: true,
                                                     bytes: COLLECTIONS.maxRowsCharge });
    expect(Object.keys(COLLECTIONS)).not.toContain("maxOwnerBytes");
    expect(Object.keys(LEDGER_LIMITS)).not.toContain("maxBodyBytes");
  });

  it("prints the audit's figures", () => {
    const now = { ...TERMS, ...RUNTIME };
    const figures = {
      budget: HALF, worst: worstCase(), margin: HALF - worstCase(),
      terms: Object.fromEntries(Object.keys(now).map((name) =>
        [name, termBytes(name, now[name]!)])),
      without: Object.fromEntries(Object.entries(WITHOUT).map(([change, without]) =>
        [change, total(without())])),
    };
    console.log(`LIMITS-AUDIT ${JSON.stringify(figures)}`);
    expect(figures.terms["file_manifest"]).toBeGreaterThan(0);
  });
});
