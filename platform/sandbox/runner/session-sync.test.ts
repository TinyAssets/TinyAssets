import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { expect, it, vi } from "vitest";

import { sha256Hex } from "../../contract/src/digest";

it("stages changed session bytes when a copy preserves the timestamp and length", async () => {
  const root = mkdtempSync(join(tmpdir(), "session-sync-"));
  const state = join(root, "state");
  const home = join(root, "sync");
  vi.doMock(import("./protocol"), async (original) => ({ ...await original(), STATE_DIR: state }));
  vi.doMock(import("./users"), async (original) => ({ ...await original(), SYNC_HOME: home }));
  try {
    // Let the real file's timestamps settle from the scanner's perspective.
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 60_000);
    const reads = vi.spyOn(await import("./fsafe"), "safeRead");
    const { stageSessions } = await import("./sync");
    const rel = ".codex/sessions/example.jsonl";
    const file = join(state, "store", "codex", rel);
    mkdirSync(dirname(file), { recursive: true });
    mkdirSync(home);
    const remote = new Map<string, string>();
    const known = async (paths: readonly string[]) =>
      Object.fromEntries(paths.map((path) => [path, remote.get(path) ?? null]));
    for (const [run, value] of [["first", "alpha"], ["second", "bravo"]] as const) {
      writeFileSync(file, value);
      utimesSync(file, 1_700_000_000, 1_700_000_000);
      await stageSessions("owner", run, [], known);
      const sent = readFileSync(join(home, "out", run, "codex", rel));
      expect(sent.toString()).toBe(value);
      remote.set(`/workspace/.sessions/owner/codex/${rel}`, sha256Hex(sent));
    }
    reads.mockClear();
    await stageSessions("owner", "again", [], known);
    expect(reads.mock.calls.filter(([, path]) => path === file)).toHaveLength(0);
    expect(existsSync(join(home, "out", "again", "codex", rel))).toBe(false);
  } finally {
    vi.restoreAllMocks();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
