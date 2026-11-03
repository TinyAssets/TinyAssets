/**
 * The sync agent against a stand-in sync door (the review's attacks, rvst, as the code must
 * answer them):
 * - a refusal of one file is that file's, with the object's whole message, and never the
 *   sync's: a full storage at an upload's begin, a `file_quota` refusal never read as a fence;
 * - the mirror is written as each batch commits, from each file's identity when it was
 *   offered, so a change made meanwhile is sent next time and a file gone meanwhile is noted;
 * - an upload given up (its file changed mid-sync) is aborted at once;
 * - the sync-ignore never hides itself, and its defaults never stop a path already kept;
 * - the ignore list's matcher is linear time;
 * - memory stays bounded both ways: sync-in of a file larger than the sandbox's memory, and
 *   sync-out over many paths.
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, utimesSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import {
  ignoredBy, memoryMirror, type Mirror, openMirror, type Place, SyncClient, SyncFenced,
  syncIn, syncOut, wildcard,
} from "./sync";

type Handler = (door: string, body: Record<string, unknown>) =>
  { status: number; body: unknown } | Promise<{ status: number; body: unknown }>;

const roots: string[] = [];
afterAll(() => {
  for (const root of roots) {
    rmSync(root, { recursive: true, force: true });
  }
});

function client(handler: Handler, log: string[] = []): SyncClient {
  const fetcher = (async (url: string, init: { body: string }) => {
    const door = url.split("/sync/")[1]!;
    log.push(door);
    const got = await handler(door, JSON.parse(init.body) as Record<string, unknown>);
    return new Response(JSON.stringify(got.body), { status: got.status });
  }) as unknown as typeof fetch;
  return new SyncClient("https://egress.test", "tok", 1, 2, fetcher);
}

let version = 100;
function commitAll(body: Record<string, unknown>, sent?: string[]) {
  const ops = body["ops"] as { path: string; op: string }[];
  sent?.push(...ops.map((op) => op.path));
  return { status: 200, body: { outcomes: ops.map((op) => ({
    path: op.path, outcome: "committed",
    entry: op.op === "remove" ? null : { version: version += 1 } })) } };
}

function place(): { place: Place; local: string; root: string } {
  const root = mkdtempSync(join(tmpdir(), "sync-"));
  roots.push(root);
  const local = join(root, "ws");
  mkdirSync(local, { recursive: true });
  mkdirSync(join(root, "out"), { recursive: true });
  return { local, root, place: { local, sessionsIn: join(root, "in"),
                                 sessionsOut: join(root, "out"), authority: "owner",
                                 records: new Map() } };
}

const LEASE = { lease_id: "l", epoch: 1 };
const FULL = "storage_full: your storage is full: 24.5 GB of 25.0 GB used, and this needs " +
  "2.0 GB more (500 MB free). Free some space (delete files you no longer need) or upgrade " +
  "to paid for 250 GB. {}";

describe("a refusal of one file", () => {
  it("at an upload's begin (the storage full) is that file's, the sync going on", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "small.txt"), "small");
    writeFileSync(join(local, "big.bin"), Buffer.alloc(10_000, 7));
    const sent: string[] = [];
    const mirror = memoryMirror();
    const report = await syncOut(client((door, body) => {
      if (door === "upload_begin") {
        return { status: 507, body: { error: { code: "storage_full", message: FULL } } };
      }
      return door === "commit" ? commitAll(body, sent) : { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    expect(report.refused).toEqual([{ path: "/workspace/big.bin", reason: FULL }]);
    expect(sent).toContain("/workspace/small.txt");
    // What landed is mirrored; what was refused is not, so the next sync offers it again.
    expect(mirror.get("/workspace/small.txt")).toBeDefined();
    expect(mirror.get("/workspace/big.bin")).toBeUndefined();
  });

  it("refused file_quota (403) is never read as a fence", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "big.bin"), Buffer.alloc(10_000, 7));
    const quota = "file_quota: too many uploads are open; commit or abort one first";
    const report = await syncOut(client((door, body) => door === "upload_begin"
      ? { status: 403, body: { error: { code: "file_quota", message: quota } } }
      : door === "commit" ? commitAll(body) : { status: 404, body: {} }),
                                 p, memoryMirror(), LEASE, []);
    expect(report.refused).toEqual([{ path: "/workspace/big.bin", reason: quota }]);
    // A lost lease still is a fence.
    await expect(syncOut(client(() => ({ status: 403, body: { error: {
      code: "sync_fenced", message: "sync_fenced: the sync lease was lost" } } })),
                         p, memoryMirror(), LEASE, [])).rejects.toBeInstanceOf(SyncFenced);
  });
});

describe("the mirror, from each file's identity when it was offered", () => {
  it("goes on when a file another run deletes meanwhile", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "a.txt"), "aaaa");
    writeFileSync(join(local, "b.txt"), "bbbb");
    const mirror = memoryMirror();
    const report = await syncOut(client((door, body) => {
      if (door === "commit") {
        rmSync(join(local, "a.txt"), { force: true });
        return commitAll(body);
      }
      return { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    expect(report.committed).toBeGreaterThanOrEqual(2);
    // Gone now: the next sync removes it from the owner's files.
    const sent: string[] = [];
    await syncOut(client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toContain("/workspace/a.txt");
    expect(mirror.get("/workspace/a.txt")).toBeUndefined();
  });

  it("sends again a same-size change made between offer and commit", async () => {
    const { place: p, local } = place();
    const file = join(local, "notes.md");
    writeFileSync(file, "version-1");
    let changed = false;
    const mirror = memoryMirror();
    await syncOut(client((door, body) => {
      if (door === "commit" && !changed) {
        changed = true;
        writeFileSync(file, "version-2");  // the same size, by a concurrent run
        const later = new Date(Date.now() + 5_000);
        utimesSync(file, later, later);
      }
      return door === "commit" ? commitAll(body) : { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    const sent: string[] = [];
    await syncOut(client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(statSync(file).size).toBe(9);
    expect(sent).toContain("/workspace/notes.md");
  });

  it("aborts an upload whose file changed mid-sync, and notes it for the next sync",
     async () => {
    const { place: p, local } = place();
    const file = join(local, "log.bin");
    writeFileSync(file, Buffer.alloc(10_000, 1));
    const aborted: string[] = [];
    const committed: string[] = [];
    const report = await syncOut(client((door, body) => {
      if (door === "upload_begin") {
        writeFileSync(file, Buffer.alloc(10_000, 2));  // the file changes mid-sync
        return { status: 200, body: { intent_id: "i1", missing: body["chunks"] } };
      }
      if (door === "upload_abort") {
        aborted.push(body["intent_id"] as string);
        return { status: 200, body: { aborted: true } };
      }
      if (door === "commit") {
        committed.push(...(body["ops"] as { intent_id?: string }[])
          .flatMap((op) => op.intent_id === undefined ? [] : [op.intent_id]));
        return commitAll(body);
      }
      return { status: 200, body: {} };
    }), p, memoryMirror(), LEASE, []);
    expect(aborted).toEqual(["i1"]);
    expect(committed).toEqual([]);
    expect(report.skipped.map((each) => each.path)).toEqual(["/workspace/log.bin"]);
  });

  it("is written as each batch commits, so a sync cut short keeps what it sent", async () => {
    const { place: p, local, root } = place();
    for (let i = 0; i < 1_500; i++) {
      writeFileSync(join(local, `f${i}.txt`), `file ${i}`);
    }
    const mirror = openMirror(join(root, "mirror.db"));
    let commits = 0;
    await expect(syncOut(client((door, body) => {
      if (door === "commit" && ++commits === 2) {
        return { status: 500, body: { error: { code: "boom", message: "the door failed" } } };
      }
      return door === "commit" ? commitAll(body) : { status: 404, body: {} };
    }), p, mirror, LEASE, [])).rejects.toThrow("the door failed");
    // The first batch (1,000 ops) stands in the mirror on disk.
    let known = 0;
    for (let after = "", page = mirror.page(after, 500); page.length > 0;
         page = mirror.page(after, 500)) {
      known += page.length;
      after = page.at(-1)!;
    }
    expect(known).toBe(1_000);
    mirror.close();
  });
});

describe("the sync-ignore", () => {
  it("never hides itself, so a run's edit of it reaches the owner", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, ".ta"), { recursive: true });
    writeFileSync(join(local, ".ta", "sync-ignore"), "*\n");
    writeFileSync(join(local, "report.md"), "the work the owner asked for");
    const sent: string[] = [];
    await syncOut(client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, memoryMirror(), LEASE, []);
    expect(sent).toEqual(["/workspace/.ta/sync-ignore"]);
  });

  it("never stops syncing a path already kept by a default line, only new ones", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "build"), { recursive: true });
    writeFileSync(join(local, "build", "kept.txt"), "v1");
    writeFileSync(join(local, "build", "new.txt"), "new");
    const mirror = memoryMirror({ files: { "/workspace/build/kept.txt": {
      version: 1, size: 1, mtime_ms: 0, sha256: "x" } } });
    const sent: string[] = [];
    await syncOut(client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toContain("/workspace/build/kept.txt");
    expect(sent).not.toContain("/workspace/build/new.txt");
  });

  it("matches as written: names anywhere, paths from the workspace, the file itself never",
     () => {
    const ignored = ignoredBy(["node_modules", "*.log", "docs/draft-*", "# a comment"]);
    expect(ignored("app/node_modules/x/index.js")).toBe("default");
    expect(ignored("server.log")).toBe("custom");
    expect(ignored("docs/draft-1.md")).toBe("custom");
    expect(ignored("src/docs/draft-1.md")).toBeNull();
    expect(ignoredBy(["*"])(".ta/sync-ignore")).toBeNull();
  });

  it("matches in linear time: the review's backtracking pattern is instant", () => {
    const started = Date.now();
    expect(wildcard("*a*a*a*a*a*a*b", "a".repeat(100))).toBe(false);
    expect(wildcard("*a*a*a*a*a*a*a*a*b", "a".repeat(10_000))).toBe(false);
    expect(Date.now() - started).toBeLessThan(1_000);
    expect([wildcard("a*b?d", "aXXbcd"), wildcard("a*b?d", "abd"), wildcard("*", ""),
            wildcard("?", "")]).toEqual([true, false, true, false]);
  });
});

describe("memory, whatever the tree holds", () => {
  it("sync-in streams a file larger than the sandbox's memory (1 GiB) to disk", async () => {
    const { place: p, local } = place();
    const CHUNK = 4 * 1024 * 1024;
    const CHUNKS = 320;  // 1.25 GiB
    const piece = Buffer.alloc(CHUNK, 3).toString("base64");
    let peak = 0;
    const base = process.memoryUsage().rss;
    const timer = setInterval(() => { peak = Math.max(peak, process.memoryUsage().rss); }, 5);
    const mirror: Mirror = memoryMirror();
    await syncIn(client((door, body) => {
      peak = Math.max(peak, process.memoryUsage().rss);
      if (door === "changes") {
        return { status: 200, body: { cursor: 5, changes: [
          { path: "/workspace/video.bin", kind: "file", version: 1 }] } };
      }
      if (door === "read") {
        return { status: 200, body: { entry: { version: 1, mode: 0, kind: "file" },
          chunks: Array.from({ length: CHUNKS }, (_, i) => ({ sha256: String(i), size: CHUNK })) } };
      }
      return { status: 200, body: { chunks: Array.from({ length: body["count"] as number },
                                                         () => piece) } };
    }), p, mirror);
    clearInterval(timer);
    expect(statSync(join(local, "video.bin")).size).toBe(CHUNK * CHUNKS);
    expect(mirror.get("/workspace/video.bin")!.size).toBe(CHUNK * CHUNKS);
    // Bounded by a few replies in flight (each 8 MiB of chunks, as base64 and decoded, the
    // stand-in door's own copies among them), never by the file: it is 1.25 GiB.
    expect(peak - base).toBeLessThan(384 * 1024 * 1024);
    rmSync(join(local, "video.bin"));
  }, 300_000);

  it("sync-out over many paths holds about what it holds over few", async () => {
    const heap = async (files: number) => {
      const { place: p, local, root } = place();
      for (let d = 0; d < files / 100; d++) {
        const dir = join(local, "a-fairly-ordinary-project-folder", `module-${d}`, "src");
        mkdirSync(dir, { recursive: true });
        for (let f = 0; f < 100; f++) {
          writeFileSync(join(dir, `component-file-number-${f}.ts`), "");
        }
      }
      const mirror = openMirror(join(root, "mirror.db"));
      const before = process.memoryUsage().heapUsed;
      let peak = before;
      const timer = setInterval(() => {
        peak = Math.max(peak, process.memoryUsage().heapUsed);
      }, 5);
      await syncOut(client((door, body) => door === "commit" ? commitAll(body)
        : { status: 404, body: {} }), p, mirror, LEASE, []);
      clearInterval(timer);
      mirror.close();
      rmSync(local, { recursive: true, force: true });
      return Math.max(0, peak - before);
    };
    const few = await heap(10_000);
    const many = await heap(100_000);
    // Ten times the paths: the same bounded batch, not ten times the memory (the walk, the
    // mirror and the removals are read a page at a time).
    expect(many).toBeLessThan(few * 3 + 32 * 1024 * 1024);
    expect(many).toBeLessThan(96 * 1024 * 1024);
  }, 600_000);
});

it("reads its legacy JSON mirror once, into the database", () => {
  const root = mkdtempSync(join(tmpdir(), "sync-"));
  roots.push(root);
  writeFileSync(join(root, "mirror.json"), JSON.stringify({ cursor: 7, files: {
    "/workspace/a": { version: 2, size: 1, mtime_ms: 3, sha256: "h" } } }));
  const mirror = openMirror(join(root, "mirror.db"), join(root, "mirror.json"));
  expect([mirror.cursor(), mirror.get("/workspace/a")?.version]).toEqual([7, 2]);
  mirror.close();
  expect(() => readFileSync(join(root, "mirror.json"))).toThrow();
});

describe("verification round 2", () => {
  it("says a kept file whose changes a run's own line holds back (M2)", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "src"), { recursive: true });
    writeFileSync(join(local, "src", "app.ts"), "v1");
    const mirror = memoryMirror();
    await syncOut(client((door, body) => door === "commit" ? commitAll(body)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(mirror.get("/workspace/src/app.ts")).toBeDefined();
    // A run adds a line of its own, then changes the kept file and leaves another as it was.
    writeFileSync(join(local, ".ta", "sync-ignore"), "src\n");
    writeFileSync(join(local, "src", "app.ts"), "v2, the run's work");
    const sent: string[] = [];
    const report = await syncOut(client((door, body) => door === "commit"
      ? commitAll(body, sent) : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toEqual(["/workspace/.ta/sync-ignore"]);
    expect(report.skipped).toEqual([{ path: "/workspace/src/app.ts", reason:
      "held back by a sync-ignore line: your files keep the version they have" }]);
    // Never removed, and the owner's copy stands.
    expect(mirror.get("/workspace/src/app.ts")).toBeDefined();
  });

  it("sends a same-size edit that kept its change time, by the next sync (L-c)", async () => {
    const { place: p, local } = place();
    const file = join(local, "state.json");
    writeFileSync(file, "{\"n\":1}");
    const mtime = Math.floor(Date.now() / 1000);  // whole seconds: restored exactly
    utimesSync(file, mtime, mtime);
    let changed = false;
    const mirror = memoryMirror();
    await syncOut(client((door, body) => {
      if (door === "commit" && !changed) {
        changed = true;
        writeFileSync(file, "{\"n\":2}");
        utimesSync(file, mtime, mtime);
      }
      return door === "commit" ? commitAll(body) : { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    const sent: string[] = [];
    await syncOut(client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toContain("/workspace/state.json");
    // Once its change time is settled, an unchanged file is never read again.
    const settled = Math.floor(Date.now() / 1000) - 60;
    utimesSync(file, settled, settled);
    await syncOut(client((door, body) => door === "commit" ? commitAll(body) : { status: 404,
      body: {} }), p, mirror, LEASE, []);
    expect(mirror.get("/workspace/state.json")!.mtime_ms).toBe(settled * 1000);
    const again: string[] = [];
    await syncOut(client((door, body) => door === "commit" ? commitAll(body, again)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(again).toEqual([]);
  });
});
