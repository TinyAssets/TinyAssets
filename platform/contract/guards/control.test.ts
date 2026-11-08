/**
 * The control record's module (`src/control.ts`), the one every Worker reads the record by:
 * its key, the live name the founder's record is stored under, and the grammar of its
 * sign-in switches. Its parse and gates are held in the doors' tests, against the doors.
 */
import { describe, expect, it } from "vitest";

import {
  CONTROL_KEY, inboundGate, parseControl, RUNS_CEILING, TIER_STORAGE_MAX,
} from "../src/control";

const GATES = Object.freeze({ outside: "on", ordinary: "all", canary: [], inbound: true });

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
    expect(inboundGate({ kind: "read", control })).toBe(control);
    expect(inboundGate({ kind: "read", control: { ...control, inbound: false } })).toBe("off");
    expect(inboundGate({ kind: "missing" })).toBe("off");
    expect(inboundGate({ kind: "malformed" })).toBe("unavailable");
    expect(inboundGate({ kind: "unreadable" })).toBe("unavailable");
  });
});
