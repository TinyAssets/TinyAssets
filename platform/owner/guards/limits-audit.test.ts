/**
 * The limits that ship (`openspec/changes/clean-sheet-platform/limits-audit.md`), costed by
 * the owner-bound guard's own terms (`termsFor`): the owner object's worst case, its margin
 * under half the 10 GB, and each term's share, printed for the audit. The structural changes
 * the audit's raises needed first (ADR-035) are what let them fit: large bodies placed in R2,
 * the collection history in bytes, one recipient a message, the mailboxes' caps within the
 * ceilings, grant scopes in bytes. Each is shown here to be what lifts its raise: without
 * it, the same raise would break the guard or the ledger's own check.
 */
import { describe, expect, it } from "vitest";

import { LIMITS as COLLECTIONS } from "../src/collections";
import { checkLimits, LEDGER_LIMITS } from "../src/ledger";
import { PLACE_BYTES } from "../src/manifest";
import {
  type Bounds, DATABASE_BYTES, RUNTIME, SHARE, type Term, termBytes, TERMS, termsFor, worstCase,
} from "./owner-bound.test";

const HALF = SHARE * DATABASE_BYTES;
/** The margin the shipped limits leave under the guard's half, at least: no thin margin. */
const MARGIN = 500_000_000;
const ROW = 256;
const total = (terms: Readonly<Record<string, Term>>) => worstCase({ ...terms, ...RUNTIME });

/**
 * The shipped bounds' terms as they would be without one structural change: what each change
 * buys, counted the way the guard counted it before.
 */
const WITHOUT: Readonly<Record<string, () => Readonly<Record<string, Term>>>> = Object.freeze({
  "bodies placed in R2 past PLACE_BYTES": () => ({ ...TERMS,
    message_bodies: { rows: LEDGER_LIMITS.maxMessages,
                      bytes: ROW + LEDGER_LIMITS.maxBodyBytes / LEDGER_LIMITS.maxMessages,
                      by: "every body in its row" },
    collection_rows: { rows: COLLECTIONS.maxOwnerRows,
                       bytes: ROW + COLLECTIONS.maxOwnerBytes / COLLECTIONS.maxOwnerRows,
                       by: "every body in its row" } }),
  "collection history counted in bytes, not rows times revisions": () => ({ ...TERMS,
    collection_history: { rows: COLLECTIONS.maxOwnerRows * COLLECTIONS.maxRevisions,
                          bytes: ROW + PLACE_BYTES, by: "maxRevisions a row" } }),
  "the ledger's recipients bounded by their own count, not messages times agents": () => ({
    ...TERMS, ledger_recipients: { rows: LEDGER_LIMITS.maxMessages * LEDGER_LIMITS.maxAgents,
                                   bytes: ROW, by: "each message to each agent" } }),
  "grant scopes counted in bytes, not 512 code points of JSON each": () => ({ ...TERMS,
    outside_clients: { rows: 2_000, bytes: ROW + 8 + 1_000 * (256 * (3 * 512 * 6 + 64)) / 2_000,
                       by: "maxScopes of three 512-point fields" } }),
});

describe("the limits that ship", () => {
  it("hold the owner's worst case under half the 10 GB, with a margin past MARGIN", () => {
    expect(total(TERMS)).toBe(worstCase());
    expect(HALF - worstCase()).toBeGreaterThan(MARGIN);
  });

  it("pass the ledger's own load-time check, and nest the collections' bounds", () => {
    expect(() => checkLimits(LEDGER_LIMITS)).not.toThrow();
    const c = COLLECTIONS;
    expect(4 * c.maxRow).toBeLessThanOrEqual(c.maxWriterBytes);
    expect(c.maxWriterBytes).toBeLessThanOrEqual(c.maxCollectionBytes);
    expect(c.maxCollectionBytes).toBeLessThanOrEqual(c.maxOwnerBytes);
    expect(c.maxWriterRows).toBeLessThanOrEqual(c.maxRows);
    expect(c.maxRows).toBeLessThanOrEqual(c.maxOwnerRows);
  });

  it("fit only with their structural changes: without each, the guard breaks", () => {
    for (const [change, without] of Object.entries(WITHOUT)) {
      expect(total(without()), change).toBeGreaterThan(HALF);
    }
    // The mailboxes' caps, reserved out of the ceilings as before, would fail the check.
    const L = LEDGER_LIMITS;
    expect(L.maxAgents * L.maxAgentUnacked + L.maxOwnerUnacked).toBeGreaterThan(L.maxMessages);
  });

  it("move a term only where the guard counts them", () => {
    const raised: Bounds = { collections: { maxRow: 2 * COLLECTIONS.maxRow },
                             ledger: { maxBodyBytes: 2 * LEDGER_LIMITS.maxBodyBytes } };
    // A body's own size, or the bytes held in R2, moves nothing in the object.
    expect(total(termsFor(raised))).toBe(worstCase());
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
