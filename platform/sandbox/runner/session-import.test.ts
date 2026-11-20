import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync }
  from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { expect, it, vi } from "vitest";
import { CHUNK_BYTES } from "../../contract/src/limits";

it("imports missing sessions under the engine lock, one chunk at a time", async () => {
  const root = mkdtempSync(join(tmpdir(), "ta-session-import-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.doMock("./protocol", async (original) => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async (original) => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  try {
    const { importSessions, record, withStore } = await import("./session");
    const rel = ".codex/sessions";
    const staged = join(sync, "in", "codex", rel);
    const store = join(state, "store", "codex", rel);
    mkdirSync(staged, { recursive: true });
    mkdirSync(store, { recursive: true });
    const bytes = Buffer.alloc(CHUNK_BYTES + 31, 97);
    bytes[CHUNK_BYTES] = 98;
    writeFileSync(join(staged, "new.jsonl"), bytes);
    writeFileSync(join(staged, "current.jsonl"), "earlier");
    let importing: Promise<void> | undefined;
    const allocations = vi.spyOn(Buffer, "alloc");
    await withStore(record("codex"), () => {
      importing = importSessions();
      expect(existsSync(join(store, "new.jsonl"))).toBe(false);
      writeFileSync(join(store, "current.jsonl"), "current");
    });
    await importing;
    const largest = Math.max(...allocations.mock.calls.map(([size]) => size));
    allocations.mockRestore();
    expect(largest).toBeLessThanOrEqual(CHUNK_BYTES);
    expect(readFileSync(join(store, "new.jsonl")).equals(bytes)).toBe(true);
    expect(statSync(join(store, "new.jsonl")).mode & 0o777).toBe(0o600);
    expect(readFileSync(join(store, "current.jsonl"), "utf8")).toBe("current");
  } finally {
    vi.restoreAllMocks();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
