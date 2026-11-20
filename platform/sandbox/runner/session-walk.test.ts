import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { expect, it, vi } from "vitest";

it("stages the remaining sessions when a listed transcript or its directory disappears", async () => {
  const root = mkdtempSync(join(tmpdir(), "session-walk-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.doMock("./protocol", async (original) => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async (original) => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  try {
    const { stageSessions } = await import("./sync");
    const files = await import("./fsafe");
    const walk = files.walkFiles;
    const store = join(state, "store", "codex");
    const sessions = ".codex/sessions";
    const deleted = join(sessions, "removed.jsonl");
    const replaced = join(sessions, "old", "child.jsonl");
    const kept = join(sessions, "kept.jsonl");
    mkdirSync(join(store, dirname(replaced)), { recursive: true });
    mkdirSync(sync);
    for (const path of [deleted, replaced, kept]) writeFileSync(join(store, path), path);
    const changed: string[] = [];
    vi.spyOn(files, "walkFiles").mockImplementation(function* (dir, options, base) {
      for (const rel of walk(dir, options, base)) {
        if (dir === store && (rel === deleted || rel === replaced)) {
          const path = join(store, rel);
          if (rel === deleted) {
            rmSync(path);
          } else {
            rmSync(dirname(path), { recursive: true });
            writeFileSync(dirname(path), "new regular file");
          }
          changed.push(rel);
        }
        yield rel;
      }
    });
    const known = vi.fn(async (_paths: readonly string[]) => ({}));
    expect(await stageSessions("owner", "run", [], known)).toEqual([]);
    expect(changed.sort()).toEqual([deleted, replaced].sort());
    expect(known).toHaveBeenCalledExactlyOnceWith([`/workspace/.sessions/owner/codex/${kept}`]);
    expect(readFileSync(join(sync, "out/run/codex", kept), "utf8")).toBe(kept);
  } finally {
    vi.restoreAllMocks();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
