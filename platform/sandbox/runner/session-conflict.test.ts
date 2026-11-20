import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { expect, it, vi } from "vitest";

it.each(["cold", "warm"])("preserves the owner's transcript after a conflict (%s store)", async (temperature) => {
  const root = mkdtempSync(join(tmpdir(), "ta-session-conflict-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.resetModules();
  vi.doMock("./protocol", async (original) => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async (original) => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  const { openMirror, stageSessions, syncIn, SyncClient } = await import("./sync");
  const { importSessions, RECORDS } = await import("./session");
  const mirror = openMirror(join(root, "mirror.db"));
  try {
    const rel = ".codex/sessions/example.jsonl";
    const path = `/workspace/.sessions/owner/codex/${rel}`;
    const copy = path + ".ta-conflict-1";
    const store = join(state, "store/codex", rel);
    if (temperature === "warm") {
      mkdirSync(dirname(store), { recursive: true });
      writeFileSync(store, "earlier");
    }
    const files = new Map([[path, { version: 34, text: "newer" }],
                           [copy, { version: 35, text: "earlier" }]]);
    const client = new SyncClient("https://example.invalid", "", 1, 2);
    vi.spyOn(client, "post").mockImplementation(async (door, body) => {
      if (door === "changes") return {
        changes: [...files].map(([path, file]) => ({ path, version: file.version, kind: "file" })),
        cursor: 35, reset: false,
      };
      if (door !== "read" || body === null || typeof body !== "object" || !("path" in body)) {
        throw Error(door);
      }
      const file = files.get(String(body.path))!;
      return { entry: { version: file.version, kind: "file", mode: 0 },
               inline: Buffer.from(file.text).toString("base64") };
    });
    const place = { local: join(root, "workspace"), sessionsIn: join(sync, "in"),
                    sessionsOut: join(sync, "out/run"), authority: "owner", records: RECORDS };
    mkdirSync(place.sessionsIn, { recursive: true });
    await syncIn(client, place, mirror);
    await importSessions();
    expect(readFileSync(store + ".ta-conflict-1", "utf8")).toBe("earlier");
    expect(mirror.get(path)?.version).toBe(34);
    await stageSessions("owner", "run", [], async paths =>
      Object.fromEntries(paths.map(p => [p, mirror.get(p)?.sha256 ?? null])));
    // The earlier bytes already have their own copy. Reoffering them here
    // would use the current canonical file's version as the base.
    expect(existsSync(join(place.sessionsOut, "codex", rel))).toBe(false);
  } finally {
    mirror.close();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
