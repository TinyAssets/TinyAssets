/**
 * The runner's on-disk format, its protocol, and their rollback rule (`state-format.ts`,
 * `protocol.ts`): each recorded format and protocol is frozen (its digest pinned here, so a
 * past entry is never edited, only a new one added); what this runner writes is exactly
 * its format's record (the mirror here, the run's and sign-in's `state.json` and the kept
 * snapshot in `runner.test.ts`, written by the real runner), and it reads every format it
 * says it reads, as recorded. The runner one deploy away passed the same reading test
 * against the same frozen record, so it reads what this one writes. The rule itself: a
 * runner reads its format and the ones either side at most, the marker is never lowered,
 * and a rollback across a format change starts. The protocol: the commands the runner
 * takes and the Worker sends, the exit codes, the paths and the answers the Worker reads
 * are the recorded ones (the documents the Worker writes and the answers the real runner
 * gives are held to the same record by the Worker's tests and `runner.test.ts`).
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync }
  from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, describe, expect, it } from "vitest";

import { canonical } from "../../contract/src/canonical";
import { ANSWERS, CARRY_FILE, COMMAND, EXIT, inboxCode, inboxDoc, incoming, INSTALL,
         INTERNAL_COMMANDS, JOBS, LEASE_FILE, LOADER, PROTOCOL, RUNNERS, SPEAKS, STATE_DIR,
         WORKER_COMMANDS } from "./protocol";
import { checkFormat, dropRunners, type FormatRule, formatStep, READS,
         STATE_FORMAT } from "./state-format";
import { openMirror } from "./sync";
import { FORMATS, PROTOCOLS, sample, shapeProblems, type Format } from "./test/state-shape";

/**
 * Every recorded format's and protocol's digest, frozen: an entry once recorded never
 * changes. A new format or protocol is a new entry, and its pin is added here with it. (Protocol
 * 5's moved once, to record what its runners always read of the owner's answers, `relayed`:
 * `docs/decisions/untangle-2026-11/runner-answers.md`.)
 */
const FROZEN = Object.freeze({
  formats: { "1": "a19128179e56fbfc00a07a0aec3a84aa274a692db344bf2b042e56cab0f07263",
             "2": "5f02a16dd4a6685627097767309b32800cfaa89fa94daeec41752c5c14b1fff7" },
  protocols: { "1": "aa417ab2b758c766a2278a57f2437bb570fa6ece2cd3ced66b20f684eb42c8b2",
               "2": "c38091d43b77abc01243f22e52285771355d6680a9d0ae3cce9ac438c5440fc0",
               "3": "f46e144d6b9eaa1cd38a394722a8135501755a85a5e79020246a06957a79d5ff",
               "4": "1d3c4ee19355036e58b6ea8e2a9cc35adc6bfce46bd29eabe9e0e17769b5932b",
               "5": "84ef8b5bc0a4bfbe04a6e4e01b3104e2d0ad3ea48bf14bc0912a1ec0a221d8fb",
               "6": "74bab57cac1f2b3490b61fd85db890491099da46689ca9bdeff7971043a2f295" },
});

const digest = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");

const dirs: string[] = [];
function temp(): string {
  const dir = mkdtempSync(join(tmpdir(), "ta-format-"));
  dirs.push(dir);
  return dir;
}
afterEach(() => {
  for (const dir of dirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function schema(path: string): Format["mirror"] {
  const db = new DatabaseSync(path);
  try {
    return db.prepare("SELECT type, name, sql FROM sqlite_master ORDER BY name").all()
      .map((row) => ({ type: String(row["type"]), name: String(row["name"]),
                       sql: row["sql"] === null ? null : String(row["sql"]) }));
  } finally {
    db.close();
  }
}

describe("the recorded formats and protocols", () => {
  it("are frozen: each entry is its pinned digest, and every entry is pinned", () => {
    expect(Object.fromEntries(Object.entries(FORMATS).map(([key, entry]) =>
      [key, digest(entry)]))).toEqual(FROZEN.formats);
    expect(Object.fromEntries(Object.entries(PROTOCOLS).map(([key, entry]) =>
      [key, digest(entry)]))).toEqual(FROZEN.protocols);
  });

  it("are read and spoken one deploy either side at most, and each is recorded", () => {
    expect(READS).toContain(STATE_FORMAT);
    expect(READS.every((format) => Math.abs(format - STATE_FORMAT) <= 1)).toBe(true);
    expect(SPEAKS).toContain(PROTOCOL);
    expect(SPEAKS.every((each) => Math.abs(each - PROTOCOL) <= 1)).toBe(true);
    for (const format of READS) {
      expect(FORMATS[String(format)], `format ${format} has no record`).toBeDefined();
    }
    for (const protocol of SPEAKS) {
      expect(PROTOCOLS[String(protocol)], `protocol ${protocol} has no record`).toBeDefined();
    }
  });
});

describe("the runner's on-disk format", () => {
  it("is the mirror this runner writes, exactly as its format's record says", () => {
    const path = join(temp(), "mirror.db");
    const mirror = openMirror(path);
    mirror.put("/workspace/a.md", { version: 3, size: 5, mtime_ms: 1, sha256: "0".repeat(64),
                                    unread: false });
    mirror.setCursor(7);
    mirror.close();
    expect(schema(path)).toEqual(FORMATS[String(STATE_FORMAT)]!.mirror);
  });

  it("reads every format's mirror it reads, as each was recorded", () => {
    // A synced row and a marked one, as each format wrote them: format 1's mark was its hash
    // `""`, format 2's is a column of its own, the hash kept.
    const hash = "0".repeat(64);
    const rows: Record<string, string> = {
      "1": `('/workspace/a.md', 3, 5, 1, '${hash}'), ('/workspace/b.md', 4, 0, 0, '')`,
      "2": `('/workspace/a.md', 3, 5, 1, '${hash}', 0), ('/workspace/b.md', 4, 0, 0, '', 1)`,
    };
    for (const format of READS) {
      const path = join(temp(), "mirror.db");
      const db = new DatabaseSync(path);
      for (const entry of FORMATS[String(format)]!.mirror) {
        if (entry.sql !== null) {
          db.exec(entry.sql);
        }
      }
      db.exec(`INSERT INTO meta VALUES (1, 7); INSERT INTO files VALUES ${rows[String(format)]!}`);
      db.close();
      // Opened twice: a mirror rebuilt once is this format's, read alike after.
      for (let open = 0; open < 2; open += 1) {
        const mirror = openMirror(path);
        expect(mirror.cursor()).toBe(7);
        expect(mirror.get("/workspace/a.md")).toEqual({ version: 3, size: 5, mtime_ms: 1,
                                                        sha256: hash, unread: false });
        expect(mirror.get("/workspace/b.md")).toEqual({ version: 4, size: 0, mtime_ms: 0,
                                                        sha256: "", unread: true });
        mirror.close();
      }
      expect(schema(path)).toEqual(FORMATS[String(STATE_FORMAT)]!.mirror);
    }
  });

  it("checks a shape field by field", () => {
    const shape = { pid: "number", cli_pid: "number|null" };
    expect(shapeProblems({ pid: 1, cli_pid: null }, shape)).toEqual([]);
    expect(shapeProblems({ pid: "1", extra: 2 }, shape)).toEqual([
      "cli_pid: missing", "extra: not in the record", "pid: string, not number"]);
    expect(sample(shape, { pid: 9 })).toEqual({ pid: 9, cli_pid: 0 });
  });
});

describe("the marker and the caches", () => {
  const cacheOf = (state: string) => join(state, "sessions-seen.json");
  const marker = (state: string) => readFileSync(join(state, "format"), "utf8");

  it("is marked on a new sandbox, and kept with its caches while it stands", () => {
    const state = temp();
    checkFormat(state, [cacheOf(state)]);
    expect(marker(state)).toBe(`${STATE_FORMAT}\n`);
    writeFileSync(cacheOf(state), "{}");
    checkFormat(state, [cacheOf(state)]);
    expect(existsSync(cacheOf(state))).toBe(true);
  });

  it("refuses loudly a format it does not read, and changes nothing", () => {
    const state = temp();
    writeFileSync(join(state, "format"), `${STATE_FORMAT + 2}\n`);
    writeFileSync(cacheOf(state), "{}");
    expect(() => checkFormat(state, [cacheOf(state)])).toThrow(/this runner reads/);
    expect(existsSync(cacheOf(state))).toBe(true);
    expect(marker(state)).toBe(`${STATE_FORMAT + 2}\n`);
  });

  it("survives a format change both ways: a rollback of one deploy starts", () => {
    // Three runners of a change from F to F+1: before it, the expand deploy (reads both,
    // writes F) and the contract deploy (writes F+1, still reads F), each meeting the disk
    // the last one left (`formatStep`, the rule `checkFormat` applies).
    const F = 7;
    const before: FormatRule = { writes: F, reads: [F - 1, F] };
    const expand: FormatRule = { writes: F, reads: [F, F + 1] };
    const contract: FormatRule = { writes: F + 1, reads: [F, F + 1] };
    let found: number | null = null;
    const meet = (rule: FormatRule) => {
      const step = formatStep(found, rule);
      if (step.refuse === null && step.write !== null) {
        found = step.write;
      }
      return step;
    };
    expect(meet(before)).toEqual({ refuse: null, dropCaches: true, write: F });
    expect(meet(expand)).toEqual({ refuse: null, dropCaches: false, write: null });
    // The format moved: the caches go with it.
    expect(meet(contract)).toEqual({ refuse: null, dropCaches: true, write: F + 1 });
    // Rolled back to the expand deploy: it starts, and the marker is never lowered.
    expect(meet(expand)).toEqual({ refuse: null, dropCaches: true, write: null });
    expect(found).toBe(F + 1);
    // Forward again, and back again: each starts.
    expect(meet(contract).refuse).toBeNull();
    expect(meet(expand).refuse).toBeNull();
    // Two deploys back is past the rule, refused loudly, changing nothing.
    expect(meet(before)).toEqual({ refuse: expect.stringMatching(/this runner reads/),
                                   dropCaches: false, write: null });
    expect(found).toBe(F + 1);
  });

  it("drops every other runner's bytes", () => {
    const runners = join(temp(), "opt/ta/runner");
    for (const name of ["a".repeat(64), "b".repeat(64)]) {
      mkdirSync(join(runners, name), { recursive: true });
    }
    dropRunners(runners, "a".repeat(64));
    expect(readdirSync(runners)).toEqual(["a".repeat(64)]);
  });
});

describe("the Worker's protocol with the runner", () => {
  it("names every command the runner takes, as its protocol's record says", () => {
    const named = Object.values(COMMAND);
    expect(new Set(named).size).toBe(named.length);
    // Each the runner takes is the Worker's or the runner's own: one of them alone.
    expect([...WORKER_COMMANDS, ...INTERNAL_COMMANDS].sort()).toEqual([...named].sort());
    expect([...named].sort()).toEqual([...PROTOCOLS[String(PROTOCOL)]!.commands].sort());
  });

  it("has every protocol answer `state` alike, so the Worker reads any runner's", () => {
    // A sandbox may hold a runner of any deploy before (paused across several): the Worker
    // asks it `state` for its protocol and whether anything lives on it, and adopts its own
    // over one nothing lives on, whatever that one speaks.
    for (const [protocol, record] of Object.entries(PROTOCOLS)) {
      expect(record.commands, `protocol ${protocol}`).toContain(COMMAND.state);
      expect(record.answers[COMMAND.state], `protocol ${protocol}`)
        .toEqual(ANSWERS[COMMAND.state]);
      // Asked through the template's loader, by its path and its exit codes, and answered.
      expect({ loader: record.paths.loader, ok: record.exit.ok, absent: record.exit.absent,
               mismatch: record.exit.mismatch }, `protocol ${protocol}`)
        .toEqual({ loader: LOADER, ok: EXIT.ok, absent: EXIT.absent, mismatch: EXIT.mismatch });
    }
  });

  it("has the Worker send only commands every protocol it speaks records", () => {
    for (const protocol of SPEAKS) {
      expect(WORKER_COMMANDS.filter((command) =>
        !PROTOCOLS[String(protocol)]!.commands.includes(command))).toEqual([]);
    }
  });

  it("names the exit codes, the paths and the answers its protocol's record names", () => {
    const record = PROTOCOLS[String(PROTOCOL)]!;
    expect(EXIT).toEqual(record.exit);
    expect({ loader: LOADER, install: INSTALL, runners: RUNNERS, state: STATE_DIR,
             incoming: incoming("<digest>"), inbox_doc: inboxDoc("<id>"),
             inbox_code: inboxCode("<id>"), lease: LEASE_FILE }).toEqual(record.paths);
    expect(ANSWERS).toEqual(record.answers);
  });

  it("names the carry where every record that names one does", () => {
    // A runner's `prepare` drops a move's archive whichever runner left it (`CARRY_FILE`).
    const named = Object.values(PROTOCOLS).flatMap((record) =>
      record.paths["carry"] === undefined ? [] : [record.paths["carry"]]);
    expect(named.length).toBeGreaterThan(0);
    expect(new Set(named)).toEqual(new Set([CARRY_FILE]));
  });

  it("launches and cancels each kind of job by a command the Worker sends", () => {
    for (const job of Object.values(JOBS)) {
      expect(WORKER_COMMANDS).toEqual(expect.arrayContaining([job.launch, job.cancel]));
      expect(INTERNAL_COMMANDS).toContain(job.own);
    }
  });
});
