import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, it, vi } from "vitest";
import { sha256Hex } from "../../contract/src/digest";
import { CHUNK_BYTES } from "../../contract/src/limits";

it("recognizes an already-held transcript without allocating its whole contents", async () => {
  const root = mkdtempSync(join(tmpdir(), "ta-session-fingerprint-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.doMock("./protocol", async original => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async original => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  try {
    const { stageSessions } = await import("./sync");
    const dir = join(state, "store/codex/.codex/sessions");
    mkdirSync(dir, { recursive: true });
    const bytes = Buffer.alloc(2 * CHUNK_BYTES + 31, 97);
    bytes[CHUNK_BYTES] = 98;
    bytes[bytes.length - 1] = 99;
    const files = new Map([["large.jsonl", bytes], ["empty.jsonl", Buffer.alloc(0)]]);
    const known: Record<string, string> = {};
    for (const [name, contents] of files) {
      writeFileSync(join(dir, name), contents);
      known[`/workspace/.sessions/owner/codex/.codex/sessions/${name}`] = sha256Hex(contents);
    }
    const allocations = vi.spyOn(Buffer, "alloc");
    const refused = await stageSessions("owner", "cold", [], async paths => {
      expect([...paths].sort()).toEqual(Object.keys(known).sort());
      return known;
    });
    const largest = Math.max(...allocations.mock.calls.map(([size]) => Number(size)));
    expect(refused).toEqual([]);
    expect(existsSync(join(sync, "out/cold"))).toBe(false);
    expect(largest).toBeLessThanOrEqual(CHUNK_BYTES);
  } finally {
    vi.restoreAllMocks();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
