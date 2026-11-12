/**
 * The long-pause probe's judgement (`pause-probe.ts`, run by `e2b-pause-probe.yml`): what a
 * resumed sandbox's inspection must show for it to have held its known file, its process and
 * its scratch, and how much scratch a disk takes. No E2B call is made here.
 */
import { describe, expect, it } from "vitest";

import { facts, HEADROOM, SCRATCH_FLOOR, SCRATCH_TARGET, scratchBytes, TICKER, verdict }
  from "../pause-probe";

const KNOWN = "a".repeat(64);
const SCRATCH = "b".repeat(64);

/** An inspection's output, as the sandbox prints it, with `change` applied. */
function inspected(change: Record<string, string | null> = {},
                   scratch: { bytes: number; sha256: string } | null = { bytes: 5, sha256: SCRATCH }):
    string {
  const lines: Record<string, string | null> = {
    record: JSON.stringify({ known_sha256: KNOWN, pid: 42, scratch }),
    paused_at: "2026-11-12T10:00:00.000Z",
    known_sha256: KNOWN,
    pid: "42",
    pid_state: "S",
    pid_cmd: `/bin/sh ${TICKER} `,
    tick: "7 2026-11-12T10:03:30Z",
    ...(scratch === null ? {} : { scratch_bytes: String(scratch.bytes), scratch_sha256: SCRATCH }),
    ...change,
  };
  return Object.entries(lines).filter(([, value]) => value !== null)
    .map(([key, value]) => `${key}=${value}`).join("\n");
}

describe("a resumed sandbox's verdict", () => {
  it("holds when the file, the ticker and the scratch are all as recorded", () => {
    expect(verdict(facts(inspected()))).toEqual([]);
    expect(verdict(facts(inspected({}, null)))).toEqual([]);
  });

  it.each([
    ["the record is gone", { record: "" }, /record .* is missing or unreadable/],
    ["the known file changed", { known_sha256: "c".repeat(64) }, /known file's SHA-256 is c+/],
    ["the known file is gone", { known_sha256: "" }, /known file's SHA-256 is missing/],
    ["the ticker died", { pid_state: "" }, /process 42 is gone/],
    ["the ticker is a zombie", { pid_state: "Z" }, /process 42 is in state Z/],
    ["the pid is another process", { pid_cmd: "/usr/bin/envd " }, /another process/],
    ["the pid file moved", { pid: "43" }, /pid file says 43, not 42/],
    ["the scratch is short", { scratch_bytes: "4" }, /scratch is 4 B, not 5 B/],
    ["the scratch is gone", { scratch_bytes: null, scratch_sha256: null }, /scratch is missing/],
    ["the scratch changed", { scratch_sha256: "d".repeat(64) }, /scratch's SHA-256 is d+/],
  ])("fails when %s", (_what, change, why) => {
    const failures = verdict(facts(inspected(change)));
    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatch(why);
  });

  it("keeps a value's own = signs", () => {
    expect(facts("pid_cmd=/bin/sh -c a=b\nnoise")).toEqual({ pid_cmd: "/bin/sh -c a=b" });
  });
});

describe("the scratch a disk takes", () => {
  it("is the target when the disk keeps its headroom beside it", () => {
    expect(scratchBytes(SCRATCH_TARGET + HEADROOM)).toEqual({ bytes: SCRATCH_TARGET, note: null });
  });

  it("is less, in whole MiB and said, when the disk is tighter", () => {
    const { bytes, note } = scratchBytes(SCRATCH_TARGET + HEADROOM - 1);
    expect(bytes).toBe(SCRATCH_TARGET - 2 ** 20);
    expect(note).toMatch(/less than the .* asked/);
  });

  it("refuses a disk too small to measure a scratch resume", () => {
    expect(() => scratchBytes(SCRATCH_FLOOR + HEADROOM - 1)).toThrow(/too little/);
    expect(() => scratchBytes(Number.NaN)).toThrow(/too little/);
  });
});
