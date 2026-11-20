import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { expect, it, vi } from "vitest";

it.each(["before", "after"])("retains the original version of a local edit made %s the next download", async when => {
  const root = mkdtempSync(join(tmpdir(), "ta-session-base-"));
  const state = join(root, "state");
  const sync = join(root, "sync");
  vi.resetModules();
  vi.doMock("./protocol", async original => ({
    ...await original<typeof import("./protocol")>(), STATE_DIR: state,
  }));
  vi.doMock("./users", async original => ({
    ...await original<typeof import("./users")>(), SYNC_HOME: sync,
  }));
  const { openMirror, stageSessions, syncIn, syncOut, SyncClient } = await import("./sync");
  const { record } = await import("./session");
  const db = join(root, "mirror.db");
  let mirror = openMirror(db);
  try {
    const rel = ".codex/sessions/conversation.jsonl";
    const path = "/workspace/.sessions/owner/codex/" + rel;
    const file = join(state, "store/codex", rel);
    const place = { local: join(root, "workspace"), sessionsIn: join(sync, "in"),
                    sessionsOut: join(sync, "out/run"), authority: "owner",
                    records: new Map([["codex", record("codex")]]) };
    for (const dir of [place.local, place.sessionsIn, dirname(file)]) mkdirSync(dir, { recursive: true });
    let version = 1;
    const offers: unknown[] = [];
    const client = new SyncClient("https://example.invalid", "", 1, 2);
    vi.spyOn(client, "post").mockImplementation(async (door, body) => {
      if (door === "changes") return { changes: [{ path, version, kind: "file" }], cursor: version, reset: false };
      if (door === "read") return { entry: { kind: "file", version, mode: 0 },
                                    inline: Buffer.from("remote " + version).toString("base64") };
      if (door === "commit" && body && typeof body === "object" && "ops" in body && Array.isArray(body.ops)) {
        const outcomes = body.ops.map(op => {
          if (op.path === path) offers.push(op);
          return { outcome: "committed", path: op.path, entry: { version: 3 } };
        });
        return { outcomes };
      }
      throw Error(door);
    });
    await syncIn(client, place, mirror);
    const advance = async () => { version = 2; await syncIn(client, place, mirror); };
    if (when === "after") await advance();
    // Here the edit is created and staged together. An edit inherited from
    // an older home can have an even earlier original version.
    writeFileSync(file, "local edit");
    const base = mirror.get(path)!.version;
    await stageSessions("owner", "run", [], async paths =>
      Object.fromEntries(paths.map(p => [p, mirror.get(p)?.sha256 ?? null])));
    if (when === "before") await advance();
    mirror.close();
    mirror = openMirror(db);
    expect(mirror.get(path)?.version).toBe(2);
    expect(readFileSync(join(place.sessionsOut, "codex", rel), "utf8")).toBe("local edit");
    await syncOut(client, place, mirror, { lease_id: "synthetic", epoch: 1 }, []);
    expect(offers).toHaveLength(1);
    expect(offers[0]).toMatchObject({ path, base, inline: Buffer.from("local edit").toString("base64") });
  } finally {
    mirror.close();
    vi.doUnmock("./protocol");
    vi.doUnmock("./users");
    rmSync(root, { recursive: true, force: true });
  }
});
