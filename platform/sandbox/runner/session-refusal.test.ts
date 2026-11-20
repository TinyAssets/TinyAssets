import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { expect, it, vi } from "vitest";

it("retries clean transcript bytes after a different revision was refused", async () => {
  const root = mkdtempSync(join(tmpdir(), "session-refusal-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.doMock("./protocol", async (original) => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async (original) => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  try {
    const { stageSessions, SESSIONS_SEEN } = await import("./sync");
    const rel = ".codex/sessions/example.jsonl";
    const file = join(state, "store", "codex", rel);
    mkdirSync(dirname(file), { recursive: true });
    mkdirSync(sync);
    writeFileSync(file, "first revision");
    const marker = "synthetic-marker";
    const first = await stageSessions("owner", "one", [marker], async () => {
      writeFileSync(file, marker);
      return {};
    });
    expect(first).toEqual([`{home}/${rel}`]);
    expect(existsSync(join(sync, "out/one/codex", rel))).toBe(false);
    expect(await stageSessions("owner", "repeat", [marker], async () => ({}))).toEqual([]);
    expect(existsSync(join(sync, "out/repeat/codex", rel))).toBe(false);
    writeFileSync(file, "first revision");
    expect(await stageSessions("owner", "two", [marker], async () => ({}))).toEqual([]);
    expect(readFileSync(join(sync, "out/two/codex", rel), "utf8")).toBe("first revision");
    // An earlier runner could attach its refusal to these clean bytes.
    const legacy = JSON.parse(readFileSync(SESSIONS_SEEN, "utf8"));
    const path = `/workspace/.sessions/owner/codex/${rel}`;
    const { sha256Hex } = await import("../../contract/src/digest");
    legacy[path].refused = sha256Hex(new TextEncoder().encode(JSON.stringify([marker])));
    writeFileSync(SESSIONS_SEEN, JSON.stringify(legacy));
    expect(await stageSessions("owner", "upgrade", [marker], async () => ({}))).toEqual([]);
    expect(readFileSync(join(sync, "out/upgrade/codex", rel), "utf8")).toBe("first revision");
  } finally {
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
