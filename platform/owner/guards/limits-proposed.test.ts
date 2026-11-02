/**
 * The limits audit's proposed raises (`openspec/changes/clean-sheet-platform/limits-audit.md`)
 * fit the ceiling they move: the owner object's 10 GB database, which the owner-bound guard
 * holds the worst case under half of. Every raise is costed by the guard's own terms
 * (`termsFor`), all of them applied together, and each module's own load-time checks
 * pass with them (the ledger's `checkLimits`; the collections' nesting). No live value
 * changes here: the founder approves the table first, and each raise is its own change.
 *
 * `AFTER` holds the raises the audit's first draft proposed that do not fit today, each
 * shown to break the guard or a check, so it is proposed only with the structural change it
 * names (slice D review H3).
 */
import { describe, expect, it } from "vitest";

import { LIMITS as COLLECTIONS } from "../src/collections";
import { checkLimits, LEDGER_LIMITS } from "../src/ledger";
import {
  type Bounds, DATABASE_BYTES, RUNTIME, SHARE, termBytes, TERMS, termsFor, worstCase,
} from "./owner-bound.test";

const KIB = 1024;
const MIB = 1024 ** 2;

/** The proposed raises that move a guard term or a module's own check, all together. */
export const PROPOSED = Object.freeze({
  collections: { maxRow: MIB, maxWriterRows: 1_000, maxWriterBytes: 8 * MIB,
                 maxRows: 10_000, maxCollectionBytes: 64 * MIB, maxOwnerRows: 20_000,
                 maxOwnerBytes: 128 * MIB, maxRevisions: 20 },
  ledger: { maxAgents: 110, maxMessages: 11_000, maxBodyBytes: 72 * MIB },
  sessions: { maxSessions: 64 },
  notices: { kept: 1_000 },
  egress: { open: 160 },
} satisfies Bounds);

/**
 * The first draft's raises that fit only after a structural change, by that change: each
 * breaks the guard's half, or a module's own check, applied alone today.
 */
export const AFTER: Readonly<Record<string, Bounds>> = Object.freeze({
  "collection history counted in bytes, not rows times revisions": {
    collections: { maxOwnerRows: 100_000, maxOwnerBytes: 512 * MIB, maxRevisions: 100 } },
  "the ledger's recipients bounded by their own count, not messages times agents": {
    ledger: { maxAgents: 1_000 } },
  "the mailboxes' caps within maxMessages and maxBodyBytes": {
    ledger: { maxAgentUnacked: 1_000, maxAgentUnackedBytes: 8 * MIB, maxOwnerUnacked: 10_000,
              maxOwnerUnackedBytes: 64 * MIB, maxSenderUnacked: 5_000,
              maxSenderUnackedBytes: 32 * MIB } },
  "grant scopes counted in bytes, not 512 code points of JSON each": {
    grants: { maxScopes: 256 } },
});

const HALF = SHARE * DATABASE_BYTES;
const total = (bounds: Bounds) => worstCase({ ...termsFor(bounds), ...RUNTIME });
const ledgerHolds = (bounds: Bounds) => {
  try {
    checkLimits({ ...LEDGER_LIMITS, ...bounds.ledger });
    return true;
  } catch {
    return false;
  }
};

describe("the limits audit's proposed raises", () => {
  it("hold the owner's worst case under half the 10 GB, every raise applied together", () => {
    expect(total({})).toBe(worstCase());
    expect(total(PROPOSED)).toBeLessThan(HALF);
    expect(total(PROPOSED)).toBeGreaterThan(total({}));
  });

  it("pass the ledger's own load-time check", () => {
    expect(ledgerHolds(PROPOSED)).toBe(true);
    expect(() => checkLimits({ ...LEDGER_LIMITS, ...PROPOSED.ledger })).not.toThrow();
  });

  it("nest each collection bound inside the next, as the collections hold them", () => {
    const c = { ...COLLECTIONS, ...PROPOSED.collections };
    expect(c.maxRow).toBeLessThanOrEqual(c.maxWriterBytes);
    expect(c.maxWriterBytes).toBeLessThanOrEqual(c.maxCollectionBytes);
    expect(c.maxCollectionBytes).toBeLessThanOrEqual(c.maxOwnerBytes);
    expect(c.maxWriterRows).toBeLessThanOrEqual(c.maxRows);
    expect(c.maxRows).toBeLessThanOrEqual(c.maxOwnerRows);
  });

  it("name only bounds the modules declare, so a renamed one fails here", () => {
    for (const key of Object.keys(PROPOSED.collections)) {
      expect(Object.hasOwn(COLLECTIONS, key), key).toBe(true);
    }
    for (const key of Object.keys(PROPOSED.ledger)) {
      expect(Object.hasOwn(LEDGER_LIMITS, key), key).toBe(true);
    }
  });

  it("each of the first draft's other raises breaks the guard or a check without its " +
     "structural change", () => {
    for (const [change, bounds] of Object.entries(AFTER)) {
      expect(total(bounds) >= HALF || !ledgerHolds(bounds), change).toBe(true);
    }
    // The review's figures (H3): the draft's collections raise alone passes the whole limit.
    expect(total(AFTER["collection history counted in bytes, not rows times revisions"]!))
      .toBeGreaterThan(DATABASE_BYTES);
  });

  it("prints the audit's figures", () => {
    const now = { ...TERMS, ...RUNTIME };
    const proposed = { ...termsFor(PROPOSED), ...RUNTIME };
    const figures = {
      budget: HALF, now: total({}), proposed: total(PROPOSED), bounds: PROPOSED,
      terms: Object.fromEntries(Object.keys(now).map((name) => [name, {
        now: termBytes(name, now[name]!), proposed: termBytes(name, proposed[name]!) }])),
      after: Object.fromEntries(Object.entries(AFTER).map(([change, bounds]) =>
        [change, { bytes: total(bounds), ledgerHolds: ledgerHolds(bounds) }])),
    };
    console.log(`LIMITS-AUDIT ${JSON.stringify(figures)}`);
    expect(figures.terms["file_manifest"]!.now).toBeGreaterThan(KIB);
  });
});
