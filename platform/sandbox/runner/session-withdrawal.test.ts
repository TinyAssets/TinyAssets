import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, it, vi } from "vitest";

it.each(["feed", "reset", "feed read", "reset read"])("withdraws deleted inbound transcripts after a %s", async mode => {
  const root = mkdtempSync(join(tmpdir(), "ta-session-withdrawal-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.resetModules();
  vi.doMock("./protocol", async original => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async original => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  const { openMirror, syncIn, SyncClient } = await import("./sync");
  const { importSessions, RECORDS } = await import("./session");
  const mirror = openMirror(join(root, "mirror.db"));
  try {
    const prefix = "/workspace/.sessions/owner/codex/.codex/sessions";
    const inDir = join(sync, "in/codex/.codex/sessions");
    const store = join(state, "store/codex/.codex/sessions");
    const names = ["deleted", "edited", "surviving"];
    const client = new SyncClient("https://example.invalid", "", 1, 2);
    const entries = names.map((name, i) => ({
      path: prefix + "/" + name, version: i + 1, kind: "file",
    }));
    const place = { local: join(root, "workspace"), sessionsIn: join(sync, "in"),
                    sessionsOut: join(sync, "out/run"), authority: "owner", records: RECORDS };
    mkdirSync(place.sessionsIn, { recursive: true });
    mkdirSync(place.local);
    const reset = mode.startsWith("reset");
    const lateDeletion = mode.endsWith("read");
    let changed = false;
    vi.spyOn(client, "post").mockImplementation(async (door, body) => {
      if (door === "changes") {
        if (!body || typeof body !== "object" || !("after" in body)) throw Error("missing cursor");
        if (body.after === 0) return { changes: entries, cursor: 3, reset: false };
        if (body.after === 3) {
          changed = true;
          const changes = reset ? [] : entries.slice(0, 2).map((e, i) => ({
            ...e, kind: lateDeletion ? "file" : null, version: 4 + i,
          }));
          return { changes, cursor: 5, reset };
        }
        return { changes: [], cursor: 5, reset: false };
      }
      if (door === "list" && body && typeof body === "object" && "dir" in body) {
        const dir = String(body.dir);
        if (dir === prefix) return { entries: lateDeletion ? entries : entries.slice(2), next: null };
        const child = prefix.slice(dir.length + 1).split("/")[0];
        return { entries: [{ path: dir + "/" + child, kind: "dir" }], next: null };
      }
      if (door === "read" && body && typeof body === "object" && "path" in body) {
        const entry = entries.find(e => e.path === body.path);
        if (changed && lateDeletion && entry !== entries[2]) return { missing: true };
        if (entry) return {
          entry: { ...entry, mode: 0 }, inline: Buffer.from(entry.path).toString("base64"),
        };
      }
      throw Error(door);
    });
    await syncIn(client, place, mirror);
    expect(readFileSync(join(inDir, "deleted"), "utf8")).toBe(prefix + "/deleted");
    mkdirSync(store, { recursive: true });
    writeFileSync(join(store, "edited"), "pending local edit");
    if (mode === "reset") mirror.drop(prefix + "/deleted"); // a download with no recorded row
    await syncIn(client, place, mirror);
    await importSessions();
    expect(mirror.cursor()).toBe(5);
    expect(readFileSync(join(store, "edited"), "utf8")).toBe("pending local edit");
    expect(readFileSync(join(store, "surviving"), "utf8")).toBe(prefix + "/surviving");
    expect(existsSync(join(store, "deleted"))).toBe(false);
    expect(existsSync(join(inDir, "edited"))).toBe(false);
    expect(mirror.get(prefix + "/edited")?.version).toBe(2);
  } finally {
    mirror.close();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
