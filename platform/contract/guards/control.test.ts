/**
 * The control record's module (`src/control.ts`), the one every Worker reads the record by:
 * its key, the live name the founder's record is stored under, the grammar of its sign-in
 * switches, and how a read tells an outage from a bad write, serving the reader's last good
 * copy only for the first. Its parse and gates are held in the doors' tests, against the doors.
 */
import { describe, expect, it } from "vitest";

import {
  CONTROL_KEY, CONTROL_TEXT_MAX, type ControlKeeper, type ControlRead, type ControlStore,
  FRESH_ONLY, type FreshAct, freshRecord, inboundGate, type Kept, outsideOpen, ownerOpen,
  parseControl, readControl, requireFresh, RUNS_CEILING, TIER_STORAGE_MAX,
} from "../src/control";

const GATES = Object.freeze({ outside: "on", ordinary: "all", canary: [], inbound: true });
const TIERS = Object.freeze({ free: { runs: 5, storage_bytes: 1 },
                              paid: { runs: 10, storage_bytes: 2 } });

describe("the control record", () => {
  it("is read under its live key, which renaming would orphan", () => {
    expect(CONTROL_KEY).toBe("c:control");
  });

  it("switches sign-ins by source id, the records' own grammar", () => {
    expect(parseControl({ ...GATES, signins: { google_ai_studio: true, "claude-code": false } })
      ?.signins).toEqual({ google_ai_studio: true, "claude-code": false });
    for (const id of ["", "Claude", "-x", "a b", "x".repeat(65)]) {
      expect(parseControl({ ...GATES, signins: { [id]: true } }), id).toBeNull();
    }
  });

  it("bounds each tier by the owner database's own ceilings", () => {
    const tier = (runs: number, bytes: number) => ({ ...GATES, tiers: {
      free: { runs, storage_bytes: bytes }, paid: { runs: 1, storage_bytes: 1 } } });
    expect(parseControl(tier(RUNS_CEILING, TIER_STORAGE_MAX))?.tiers?.free)
      .toEqual({ runs: RUNS_CEILING, storage_bytes: TIER_STORAGE_MAX });
    expect(parseControl(tier(RUNS_CEILING + 1, 1))).toBeNull();
    expect(parseControl(tier(1, TIER_STORAGE_MAX + 1))).toBeNull();
  });

  it("opens the inbound door only on a record read well formed with it on", () => {
    const control = parseControl(GATES)!;
    const text = JSON.stringify(GATES);
    expect(inboundGate({ kind: "read", control, text })).toBe(control);
    expect(inboundGate({ kind: "read", control: { ...control, inbound: false }, text }))
      .toBe("off");
    expect(inboundGate({ kind: "missing" })).toBe("off");
    expect(inboundGate({ kind: "malformed" })).toBe("unavailable");
    expect(inboundGate({ kind: "unreadable" })).toBe("unavailable");
  });
});

/** A keeper in memory, counting its writes. */
function memory(copy: Kept | null = null): ControlKeeper & { copy: Kept | null; writes: number } {
  return {
    copy, writes: 0,
    kept() {
      return this.copy;
    },
    keep(text, at) {
      if (this.copy?.text !== (text ?? undefined)) {
        this.copy = text === null ? null : { text, read_at: at };
        this.writes += 1;
      }
    },
  };
}

/** A namespace holding `text` (null: no record), or one whose reads throw (an outage). */
function store(text: string | null | "down"): ControlStore {
  return { get: (async (key: string, options: unknown) => {
    expect([key, options]).toEqual([CONTROL_KEY, { type: "text" }]);
    if (text === "down") {
      throw new Error("KV down");
    }
    return text;
  }) as unknown as ControlStore["get"] };
}

const OPEN = JSON.stringify({ ...GATES, tiers: TIERS });
const COPY: Kept = Object.freeze({ text: OPEN, read_at: 1_700_000_000_000 });

describe("a read of the control record", () => {
  it("keeps each well-formed read's text, written only when it changes", async () => {
    const keeper = memory();
    for (let read = 0; read < 3; read++) {
      expect(await readControl(store(OPEN), keeper))
        .toEqual({ kind: "read", control: parseControl(JSON.parse(OPEN)), text: OPEN });
    }
    expect([keeper.copy?.text, keeper.writes]).toEqual([OPEN, 1]);
    const off = JSON.stringify({ ...GATES, outside: "off" });
    await readControl(store(off), keeper);
    expect([keeper.copy?.text, keeper.writes]).toEqual([off, 2]);
  });

  it("serves the copy kept when the namespace throws, of any age, and none when none is kept",
     async () => {
    expect(await readControl(store("down"), memory(COPY))).toEqual({
      kind: "kept", control: parseControl(JSON.parse(OPEN)), read_at: COPY.read_at });
    expect(await readControl(store("down"), memory())).toEqual({ kind: "unreadable" });
  });

  it("reads a value that is not JSON, too long or out of shape as a bad write, the copy dropped",
     async () => {
    const long = JSON.stringify(GATES).padEnd(CONTROL_TEXT_MAX + 1);
    for (const bad of ["{outside", "not json", long, JSON.stringify({ ...GATES, outside: true })]) {
      const keeper = memory(COPY);
      expect(await readControl(store(bad), keeper), bad.slice(0, 20))
        .toEqual({ kind: "malformed" });
      // The gates it closed stay closed through an outage after it (a kill flip's typo).
      expect(await readControl(store("down"), keeper)).toEqual({ kind: "unreadable" });
    }
    expect(await readControl(store(JSON.stringify(GATES).padEnd(CONTROL_TEXT_MAX)), memory()))
      .toMatchObject({ kind: "read" });
  });

  it("reads no record as missing, the copy dropped", async () => {
    const keeper = memory(COPY);
    expect(await readControl(store(null), keeper)).toEqual({ kind: "missing" });
    expect(await readControl(store("down"), keeper)).toEqual({ kind: "unreadable" });
  });

  it("keeps a good read good when its keeper fails, and an outage then unreadable", async () => {
    const failing: ControlKeeper = {
      kept: () => Promise.reject(new Error("cache down")),
      keep: () => Promise.reject(new Error("cache down")),
    };
    expect((await readControl(store(OPEN), failing)).kind).toBe("read");
    expect(await readControl(store("down"), failing)).toEqual({ kind: "unreadable" });
    // A copy no longer in shape (kept by a version whose parser took more) is no copy.
    expect(await readControl(store("down"), memory({ text: "{}", read_at: 1 })))
      .toEqual({ kind: "unreadable" });
  });
});

describe("a kept copy", () => {
  const kept = { kind: "kept", control: parseControl(JSON.parse(OPEN))!, read_at: 1 } as const;
  const read: ControlRead = { kind: "read", control: kept.control, text: OPEN };

  it("opens every gate the copy opens", () => {
    expect([outsideOpen(kept, "a"), ownerOpen(kept, "a"), inboundGate(kept)])
      .toEqual([true, true, kept.control]);
    const off = { ...kept, control: { ...kept.control, outside: "off", ordinary: "none",
                                      inbound: false } } as const;
    expect([outsideOpen(off, "a"), ownerOpen(off, "a"), inboundGate(off)])
      .toEqual([false, false, "off"]);
  });

  it("refuses each act that needs a fresh read, for a while, and none on any other read", () => {
    const acts = Object.keys(FRESH_ONLY) as FreshAct[];
    expect(acts).toEqual(["authorization", "admit", "key", "signin"]);
    for (const act of acts) {
      expect(() => requireFresh(kept, act)).toThrow(expect.objectContaining({
        code: "unavailable", message: expect.stringMatching(/can't be read just now$/) }));
      for (const other of [read, { kind: "missing" }, { kind: "malformed" },
                           { kind: "unreadable" }] as const) {
        expect(() => requireFresh(other, act)).not.toThrow();
      }
    }
  });

  it("hands the object no record to keep, as an outage with no copy doesn't", () => {
    expect([freshRecord(kept), freshRecord({ kind: "unreadable" })])
      .toEqual([undefined, undefined]);
    expect([freshRecord(read), freshRecord({ kind: "malformed" }),
            freshRecord({ kind: "missing" })]).toEqual([OPEN, null, null]);
  });
});
