/**
 * The sync agent against a stand-in sync door (the review's attacks, rvst, as the code must
 * answer them):
 * - a refusal of one file is that file's, with the object's whole message, and never the
 *   sync's: a full storage at an upload's begin, a `file_quota` refusal never read as a fence;
 * - the mirror is written as each batch commits, from each file's identity when it was
 *   offered, so a change made meanwhile is sent next time and a file gone meanwhile is noted;
 * - an upload given up (its file changed mid-sync) is aborted at once;
 * - the sync-ignore never hides itself, and its defaults never stop a path already kept;
 * - lines past the matcher's ceilings keep their files syncing, and are said (the matcher
 *   itself, as git reads a .gitignore, is `sync-ignore.test.ts`);
 * - memory stays bounded both ways: sync-in of a file larger than the sandbox's memory, and
 *   sync-out over many paths.
 */
import { mkdirSync, mkdtempSync, rmSync, statSync, utimesSync,
         writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createServer, type Server } from "node:http";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { setFlagsFromString } from "node:v8";
import { runInNewContext } from "node:vm";

import { afterAll, afterEach, describe, expect, it } from "vitest";

import {
  AUTOMATON_STATES, ignoredBy, LIST_BYTES, REMEMBERED_NAME_BYTES, SANDBOX_HEAP, wildcard,
} from "../../contract/src/sync-ignore";
import { CHUNK_BYTES, INLINE_BYTES, MAX_SYNC_OPS, SYNC_BODY_MAX } from "../../contract/src/limits";
import {
  INTENT_ID_CHARS, openMirror, type Place, SyncClient, SyncFenced, syncIn, syncOut,
} from "./sync";

const HERE = dirname(fileURLToPath(import.meta.url));

type Handler = (door: string, body: Record<string, unknown>) =>
  { status: number; body: unknown } | Promise<{ status: number; body: unknown }>;

const roots: string[] = [];
afterAll(() => {
  for (const root of roots) {
    rmSync(root, { recursive: true, force: true });
  }
});

const servers: Server[] = [];
const keptServers: Server[] = [];
const close = (list: Server[]) => {
  for (const server of list.splice(0)) {
    server.closeAllConnections();
    server.close();
  }
};
afterEach(() => close(servers));
afterAll(() => close(keptServers));

/** What a stand-in door answers one call: its door's name and its body as sent. */
type Answer = (door: string, raw: string) =>
  { status: number; text: string } | Promise<{ status: number; text: string }>;

/**
 * A stand-in sync door on loopback, answering each call by `answer`; its client. It is closed
 * after the test, unless it is `kept` (closed after the file).
 */
async function serve(answer: Answer, kept = false): Promise<SyncClient> {
  const server = createServer((request, response) => {
    const parts: Buffer[] = [];
    request.on("data", (chunk: Buffer) => { parts.push(chunk); });
    request.on("end", () => {
      void (async () => {
        const got = await answer((request.url ?? "").split("/sync/")[1]!,
                                 Buffer.concat(parts).toString("utf8"));
        response.writeHead(got.status, { "content-type": "application/json" });
        response.end(got.text);
      })();
    });
  });
  (kept ? keptServers : servers).push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as { port: number };
  return new SyncClient(`http://127.0.0.1:${port}`, "tok", 1, 2);
}

function client(handler: Handler, log: string[] = []): Promise<SyncClient> {
  return serve(async (door, raw) => {
    log.push(door);
    const got = await handler(door, JSON.parse(raw) as Record<string, unknown>);
    return { status: got.status, text: JSON.stringify(got.body) };
  });
}

/** A mirror of the sync agent's own kind (`openMirror`), in memory. */
const mirrorOf = () => openMirror(":memory:");

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
    const mirror = mirrorOf();
    const report = await syncOut(await client((door, body) => {
      if (door === "upload_begin") {
        return { status: 507, body: { error: { code: "storage_full", message: FULL } } };
      }
      return door === "commit" ? commitAll(body, sent) : { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    expect(report.refused).toEqual([{ path: "/workspace/big.bin", reason: FULL,
                                      code: "storage_full" }]);
    expect(sent).toContain("/workspace/small.txt");
    // What landed is mirrored; what was refused is not, so the next sync offers it again.
    expect(mirror.get("/workspace/small.txt")).toBeDefined();
    expect(mirror.get("/workspace/big.bin")).toBeUndefined();
  });

  it("refused file_quota (403) is never read as a fence", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "big.bin"), Buffer.alloc(10_000, 7));
    const quota = "file_quota: too many uploads are open; commit or abort one first";
    const report = await syncOut(await client((door, body) => door === "upload_begin"
      ? { status: 403, body: { error: { code: "file_quota", message: quota } } }
      : door === "commit" ? commitAll(body) : { status: 404, body: {} }),
                                 p, mirrorOf(), LEASE, []);
    expect(report.refused).toEqual([{ path: "/workspace/big.bin", reason: quota,
                                      code: "file_quota" }]);
    // A lost lease still is a fence.
    await expect(syncOut(await client(() => ({ status: 403, body: { error: {
      code: "sync_fenced", message: "sync_fenced: the sync lease was lost" } } })),
                         p, mirrorOf(), LEASE, [])).rejects.toBeInstanceOf(SyncFenced);
  });
});

describe("the mirror, from each file's identity when it was offered", () => {
  it("goes on when a file another run deletes meanwhile", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "a.txt"), "aaaa");
    writeFileSync(join(local, "b.txt"), "bbbb");
    const mirror = mirrorOf();
    const report = await syncOut(await client((door, body) => {
      if (door === "commit") {
        rmSync(join(local, "a.txt"), { force: true });
        return commitAll(body);
      }
      return { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    expect(report.committed).toBeGreaterThanOrEqual(2);
    // Gone now: the next sync removes it from the owner's files.
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toContain("/workspace/a.txt");
    expect(mirror.get("/workspace/a.txt")).toBeUndefined();
  });

  it("sends again a same-size change made between offer and commit", async () => {
    const { place: p, local } = place();
    const file = join(local, "notes.md");
    writeFileSync(file, "version-1");
    let changed = false;
    const mirror = mirrorOf();
    await syncOut(await client((door, body) => {
      if (door === "commit" && !changed) {
        changed = true;
        writeFileSync(file, "version-2");  // the same size, by a concurrent run
        const later = new Date(Date.now() + 5_000);
        utimesSync(file, later, later);
      }
      return door === "commit" ? commitAll(body) : { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
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
    const report = await syncOut(await client((door, body) => {
      if (door === "upload_begin") {
        writeFileSync(file, Buffer.alloc(10_000, 2));  // the file changes mid-sync
        return { status: 200, body: { intent_id: "1".repeat(INTENT_ID_CHARS),
                                      missing: body["chunks"] } };
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
    }), p, mirrorOf(), LEASE, []);
    expect(aborted).toEqual(["1".repeat(INTENT_ID_CHARS)]);
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
    await expect(syncOut(await client((door, body) => {
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
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    expect(sent).toEqual(["/workspace/.ta/sync-ignore"]);
  });

  it("never stops syncing a path already kept by a default line, only new ones", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "build"), { recursive: true });
    writeFileSync(join(local, "build", "kept.txt"), "v1");
    writeFileSync(join(local, "build", "new.txt"), "new");
    const mirror = mirrorOf();
    mirror.put("/workspace/build/kept.txt", { version: 1, size: 1, mtime_ms: 0, sha256: "x" });
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
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
    // CPU time, not wall-clock: another process's load does not stretch it. A backtracking
    // matcher spends far more than a second here; a linear one, milliseconds.
    const started = process.cpuUsage();
    expect(wildcard("*a*a*a*a*a*a*b", "a".repeat(100))).toBe(false);
    expect(wildcard("*a*a*a*a*a*a*a*a*b", "a".repeat(10_000))).toBe(false);
    const spent = process.cpuUsage(started);
    expect((spent.user + spent.system) / 1000).toBeLessThan(1_000);
    expect([wildcard("a*b?d", "aXXbcd"), wildcard("a*b?d", "abd"), wildcard("*", ""),
            wildcard("?", "")]).toEqual([true, false, true, false]);
  });
});

describe("memory, whatever the tree holds", () => {
  // A sample is the live set: the heap and the byte arrays' backing stores, read right after
  // full collections, at a checkpoint where sync-in is parked on a door's call (the owner
  // memory leg's sampler, `owner/memory/leg.mjs`). Never RSS: it counts garbage not yet
  // collected and pages the allocator keeps, so it reads the collector's timing, not the code.
  setFlagsFromString("--expose-gc");
  const gc = runInNewContext("gc") as () => void;
  const live = () => {
    gc();
    gc();
    const usage = process.memoryUsage();
    return usage.heapUsed + usage.arrayBuffers;
  };
  // One door for every measurement, its answer each run's: the client's own state for its
  // door (its connection) is made once, before any measurement, never inside one.
  let answer: Answer = () => ({ status: 404, text: "{}" });
  let shared: SyncClient | null = null;
  const door = async (next: Answer): Promise<SyncClient> => {
    shared ??= await serve((name, raw) => answer(name, raw), true);
    answer = next;
    return shared;
  };

  /**
   * Sync-in of one file of `chunks` chunks from a stand-in door, sampled at every call: the
   * peak over the live set before it, the most calls open at once (each answered after a
   * turn of the loop, so calls made together overlap) and the largest reply. `keep` is the
   * control: a client that keeps every reply it is given.
   */
  async function syncInMemory(chunks: number, keep = false) {
    const { place: p, local } = place();
    const piece = Buffer.alloc(CHUNK_BYTES, 3).toString("base64");
    const kept: string[] = [];
    const mirror = mirrorOf();
    let open = 0;
    let inFlight = 0;
    let reply = 0;
    let peak = 0;
    const answered = await door(async (name, raw) => {
      open += 1;
      inFlight = Math.max(inFlight, open);
      await new Promise((resolve) => setImmediate(resolve));
      peak = Math.max(peak, live() - base);
      const body = JSON.parse(raw) as Record<string, unknown>;
      const text = JSON.stringify(name === "changes"
        ? { cursor: 5, changes: [{ path: "/workspace/video.bin", kind: "file", version: 1 }] }
        : name === "read"
        ? { entry: { version: 1, mode: 0, kind: "file" },
            chunks: Array.from({ length: chunks }, (_, i) => ({
              sha256: i.toString(16).padStart(64, "0"), size: CHUNK_BYTES })) }
        : { chunks: Array.from({ length: body["count"] as number }, () => piece) });
      reply = Math.max(reply, text.length);
      if (keep) {
        kept.push(text);
      }
      open -= 1;
      return { status: 200, text };
    });
    const base = live();
    await syncIn(answered, p, mirror);
    expect(statSync(join(local, "video.bin")).size).toBe(CHUNK_BYTES * chunks);
    expect(mirror.get("/workspace/video.bin")!.size).toBe(CHUNK_BYTES * chunks);
    rmSync(join(local, "video.bin"));
    return { peak, inFlight, reply };
  }

  it("sync-in streams a file larger than the sandbox's memory (1 GiB) to disk", { tags: ["large"] }, async () => {
    const LARGE = 320;  // 1.25 GiB
    const SMALL = LARGE / 4;
    // The steps' own objects (the file's list of chunks among them): a quarter chunk, as the
    // owner memory leg allows each step and the difference of two sizes (ADR-037).
    const QUARTER = CHUNK_BYTES / 4;
    // Each size the least of two runs, as the leg reads it: a run's peak is the path's own
    // plus whatever the process made for a moment beside it.
    const runs = [await syncInMemory(SMALL), await syncInMemory(LARGE),
                  await syncInMemory(SMALL), await syncInMemory(LARGE)];
    const small = Math.min(runs[0]!.peak, runs[2]!.peak);
    const large = Math.min(runs[1]!.peak, runs[3]!.peak);
    // What the code holds by its shape: the replies it has open at once (one: each call is
    // awaited before the next), each at most a call's chunks (the count it asks for, as
    // base64), and the steps' own objects.
    const bound = Math.max(...runs.map((run) => run.inFlight)) *
      Math.max(...runs.map((run) => run.reply)) + QUARTER;
    // The control: a client that keeps every reply reads as growing, past the bound, or the
    // samples prove nothing.
    const kept = [await syncInMemory(LARGE / 16, true), await syncInMemory(LARGE / 8, true)];
    expect(kept[1]!.peak - kept[0]!.peak).toBeGreaterThan(QUARTER);
    expect(kept[0]!.peak).toBeGreaterThan(bound);
    // Each step within the bound, and O(1) in the file's size: four times the file holds no
    // more than a quarter chunk more.
    expect(large).toBeLessThanOrEqual(bound);
    expect(large - small).toBeLessThanOrEqual(QUARTER);
  });

  /**
   * Sync-out of a tree of `modules` folders of 100 files each, sampled at every call as
   * sync-in is: the peak over the live set before it, the most calls open at once, the most
   * ops a commit carried and its largest body. `keep` is the control: a client that keeps
   * every op it is sent.
   */
  async function syncOutMemory(modules: number, keep = false) {
    const { place: p, local, root } = place();
    for (let d = 0; d < modules; d++) {
      const dir = join(local, "a-fairly-ordinary-project-folder", `module-${d}`, "src");
      mkdirSync(dir, { recursive: true });
      for (let f = 0; f < 100; f++) {
        writeFileSync(join(dir, `component-file-number-${f}.ts`), "");
      }
    }
    const mirror = openMirror(join(root, "mirror.db"));
    const kept: unknown[] = [];
    let open = 0;
    let inFlight = 0;
    let ops = 0;
    let body = 0;
    let peak = 0;
    const answered = await door(async (name, raw) => {
      open += 1;
      inFlight = Math.max(inFlight, open);
      await new Promise((resolve) => setImmediate(resolve));
      peak = Math.max(peak, live() - base);
      body = Math.max(body, raw.length);
      const sent = JSON.parse(raw) as Record<string, unknown>;
      open -= 1;
      if (name !== "commit") {
        return { status: 404, text: "{}" };
      }
      ops = Math.max(ops, (sent["ops"] as unknown[]).length);
      if (keep) {
        kept.push(...(sent["ops"] as unknown[]));
      }
      return { status: 200, text: JSON.stringify(commitAll(sent).body) };
    });
    const base = live();
    await syncOut(answered, p, mirror, LEASE, []);
    mirror.close();
    rmSync(local, { recursive: true, force: true });
    return { peak, inFlight, ops, body };
  }

  it("sync-out over many paths holds about what it holds over few", { tags: ["large"] }, async () => {
    // One batch: `MAX_SYNC_OPS` files, which sync-out sends as one commit. A batch's live set,
    // measured, is the unit; the steps' own objects are a quarter of it (the owner memory
    // leg's allowance, a quarter unit, ADR-037).
    const BATCH = MAX_SYNC_OPS / 100;
    const FEW = 10 * BATCH;
    const MANY = 100 * BATCH;
    // Each size the least of two runs (a run's peak is the path's own plus whatever the
    // process made for a moment beside it).
    const least = (runs: Awaited<ReturnType<typeof syncOutMemory>>[]) =>
      runs.reduce((best, run) => run.peak < best.peak ? run : best);
    const all: Awaited<ReturnType<typeof syncOutMemory>>[] = [];
    const sized = async (modules: number) => {
      const runs = [await syncOutMemory(modules), await syncOutMemory(modules)];
      all.push(...runs);
      return least(runs);
    };
    const batch = await sized(BATCH);
    const few = await sized(FEW);
    const many = await sized(MANY);
    const QUARTER = batch.peak / 4;
    // Its shape, from the code's constants: one commit open at once, each at most
    // `MAX_SYNC_OPS` ops in a body of at most `SYNC_BODY_MAX`.
    const inFlight = Math.max(...all.map((run) => run.inFlight));
    expect(inFlight).toBe(1);
    expect(Math.max(...all.map((run) => run.ops))).toBe(MAX_SYNC_OPS);
    expect(Math.max(...all.map((run) => run.body))).toBeLessThanOrEqual(SYNC_BODY_MAX);
    // What a tree may cost past one batch is the sync-ignore matcher's memory of names,
    // `REMEMBERED_NAME_BYTES` at most (of folders it keeps only the path's own,
    // `sync-ignore.test.ts`), never its paths: the walk holds one directory a level, and the
    // mirror and the removals are read a page at a time.
    const bound = inFlight * batch.peak + REMEMBERED_NAME_BYTES + QUARTER;
    // The control: a client that keeps every op it is sent reads as growing, past the
    // bound, or the samples prove nothing.
    const kept = [await syncOutMemory(FEW, true), await syncOutMemory(2 * FEW, true)];
    expect(kept[1]!.peak - kept[0]!.peak).toBeGreaterThan(QUARTER);
    expect(kept[1]!.peak).toBeGreaterThan(bound);
    // Each size within one batch beside the matcher's names, and ten times the paths no more
    // than those names more.
    expect(few.peak).toBeLessThanOrEqual(bound);
    expect(many.peak).toBeLessThanOrEqual(bound);
    expect(many.peak - few.peak).toBeLessThanOrEqual(REMEMBERED_NAME_BYTES + QUARTER);
  });
});

describe("verification round 2", () => {
  it("says a kept file whose changes a run's own line holds back (M2)", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "src"), { recursive: true });
    writeFileSync(join(local, "src", "app.ts"), "v1");
    const mirror = mirrorOf();
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(mirror.get("/workspace/src/app.ts")).toBeDefined();
    // A run adds a line of its own, then changes the kept file and leaves another as it was.
    writeFileSync(join(local, ".ta", "sync-ignore"), "src\n");
    writeFileSync(join(local, "src", "app.ts"), "v2, the run's work");
    const sent: string[] = [];
    const report = await syncOut(await client((door, body) => door === "commit"
      ? commitAll(body, sent) : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toEqual(["/workspace/.ta/sync-ignore"]);
    expect(report.skipped).toEqual([{ path: "/workspace/src/app.ts", code: "held_back" }]);
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
    const mirror = mirrorOf();
    await syncOut(await client((door, body) => {
      if (door === "commit" && !changed) {
        changed = true;
        writeFileSync(file, "{\"n\":2}");
        utimesSync(file, mtime, mtime);
      }
      return door === "commit" ? commitAll(body) : { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toContain("/workspace/state.json");
    // Once its change time is settled, an unchanged file is never read again.
    const settled = Math.floor(Date.now() / 1000) - 60;
    utimesSync(file, settled, settled);
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body) : { status: 404,
      body: {} }), p, mirror, LEASE, []);
    expect(mirror.get("/workspace/state.json")!.mtime_ms).toBe(settled * 1000);
    const again: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, again)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(again).toEqual([]);
  });
});


describe("the list's own write carries what it holds back", () => {
  it("offers the sync-ignore last, with the kept files its lines hold back counted", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "src"), { recursive: true });
    writeFileSync(join(local, "src", "a.ts"), "a");
    writeFileSync(join(local, "src", "b.ts"), "b");
    const mirror = mirrorOf();
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    writeFileSync(join(local, ".ta", "sync-ignore"), "src\n");
    writeFileSync(join(local, "src", "a.ts"), "a, changed");
    const ops: Record<string, unknown>[] = [];
    await syncOut(await client((door, body) => {
      if (door === "commit") {
        ops.push(...(body["ops"] as Record<string, unknown>[]));
        return commitAll(body);
      }
      return { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    expect(ops.map((op) => [op["path"], op["held_back"]]))
      .toEqual([["/workspace/.ta/sync-ignore", { kept: 2, changed: 1 }]]);
  });
});

describe("the sync-ignore's ceilings, at sync-out", () => {
  it("says how long matching took in the sync's report", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "a.txt"), "a");
    const report = await syncOut(await client((door, body) => door === "commit" ? commitAll(body)
      : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    expect(typeof report.match_ms).toBe("number");
  });

  it("keeps syncing the files lines past the automaton's states name, and says which, why",
     async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, ".ta"), { recursive: true });
    // Automaton lines filling its states, then one more naming `run.log`.
    const filler = Array.from({ length: Math.ceil(AUTOMATON_STATES / 10) },
                              (_, i) => `*x${i}?${"y".repeat(14)}`);
    writeFileSync(join(local, ".ta", "sync-ignore"), `${[...filler, "*?log"].join("\n")}\n`);
    writeFileSync(join(local, "run.log"), "kept syncing");
    const sent: string[] = [];
    const report = await syncOut(await client((door, body) => door === "commit"
      ? commitAll(body, sent) : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    expect(sent).toContain("/workspace/run.log");
    // Said in file order, each with its ceiling: the first listed, all counted.
    expect(report.unapplied[0]).toMatchObject({ reason: "states" });
    expect(report.unapplied_count).toBeGreaterThan(report.unapplied.length);
    expect(report.list_bytes_past).toBe(0);
  });

  it("reads the list to `LIST_BYTES` alone, the rest said, and the sync completes", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, ".ta"), { recursive: true });
    const line = "*.ext-0123456789-";
    const count = Math.ceil(LIST_BYTES / (line.length + 6)) + 1_000;
    writeFileSync(join(local, ".ta", "sync-ignore"),
                  `${Array.from({ length: count }, (_, i) => `${line}${i}`).join("\n")}\nlast.txt\n`);
    writeFileSync(join(local, "last.txt"), "named past the cut: kept syncing");
    const sent: string[] = [];
    const report = await syncOut(await client((door, body) => door === "commit"
      ? commitAll(body, sent) : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    expect(sent).toContain("/workspace/last.txt");
    expect(report.list_bytes_past).toBeGreaterThan(0);
  });
});

describe("names that are not UTF-8", () => {
  it("are said, their bytes escaped, never silently missed; the rest syncs", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "src"), { recursive: true });
    writeFileSync(join(local, "src", "ok.txt"), "ok");
    writeFileSync(Buffer.concat([Buffer.from(`${local}/src/`), Buffer.from([0x66, 0xff, 0x6f])]),
                  "a name a string cannot hold");
    mkdirSync(Buffer.concat([Buffer.from(`${local}/`), Buffer.from([0xc3, 0x28])]));
    const sent: string[] = [];
    const report = await syncOut(await client((door, body) => door === "commit"
      ? commitAll(body, sent) : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    expect(sent).toContain("/workspace/src/ok.txt");
    expect([...report.unnamed].sort()).toEqual(["/workspace/\\xc3(", "/workspace/src/f\\xffo"]);
    expect(report.unnamed_count).toBe(2);
  });
});

describe("a list larger than the sandbox's heap would hold whole", () => {
  it("is cut at `LIST_BYTES` in a sync agent given the sandbox's heap, which completes", { tags: ["large"] }, () => {
    // The sync, bundled as the template bundles the runner, run in a Node given the heap a
    // sync agent has in the sandbox (`SANDBOX_HEAP`), over a 20 MiB list of `*.x` lines (the
    // list that crashed the agent before `LIST_BYTES`).
    const { root, local } = place();
    mkdirSync(join(local, ".ta"), { recursive: true });
    const lines: string[] = [];
    for (let i = 0, bytes = 0; bytes < 20 * 1024 * 1024; i++) {
      lines.push(`*.x${i.toString(36)}`);
      bytes += lines.at(-1)!.length + 1;
    }
    writeFileSync(join(local, ".ta", "sync-ignore"), `${lines.join("\n")}\n`);
    lines.length = 0;
    writeFileSync(join(local, "kept.txt"), "kept");
    const entry = join(root, "entry.mjs");
    const require = createRequire(join(HERE, "..", "..", "package.json"));
    (require("esbuild") as { buildSync: (options: unknown) => void }).buildSync({
      stdin: { contents: `
        import { createServer } from "node:http";
        import { openMirror, SyncClient, syncOut } from ${JSON.stringify(join(HERE, "sync.ts"))};
        const server = createServer((request, response) => {
          const parts = [];
          request.on("data", (chunk) => parts.push(chunk));
          request.on("end", () => {
            const body = JSON.parse(Buffer.concat(parts).toString("utf8"));
            // Every door at once: an upload's begin (all its chunks held), and a commit's.
            response.writeHead(200, { "content-type": "application/json" });
            response.end(JSON.stringify({ intent_id: "0".repeat(32), missing: [],
              outcomes: (body.ops ?? []).map((op) => ({
                path: op.path, outcome: "committed", entry: { version: 1 } })) }));
          });
        });
        await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
        const origin = "http://127.0.0.1:" + server.address().port;
        const report = await syncOut(new SyncClient(origin, "t", 1, 2),
          { local: ${JSON.stringify(local)}, sessionsIn: ${JSON.stringify(join(root, "in"))},
            sessionsOut: ${JSON.stringify(join(root, "out"))}, authority: "owner",
            records: new Map() }, openMirror(":memory:"), { lease_id: "l", epoch: 1 }, []);
        server.closeAllConnections();
        server.close();
        process.stdout.write(JSON.stringify({ committed: report.committed,
                                              past: report.list_bytes_past }));
      `, resolveDir: HERE, loader: "ts" },
      outfile: entry, bundle: true, platform: "node", target: "node22", format: "esm",
      banner: { js: "import { createRequire as __r } from 'node:module'; " +
                    "const require = __r(import.meta.url);" },
      logLevel: "error" });
    // `--max-old-space-size` is the old space; the young space adds its own to the limit.
    const run = spawnSync(process.execPath, [
      `--max-old-space-size=${Math.floor(SANDBOX_HEAP / 1024 / 1024) - 48}`, entry],
                          { encoding: "utf8" });
    expect(run.status, run.stderr).toBe(0);
    const out = JSON.parse(run.stdout) as { committed: number; past: number };
    expect(out.committed).toBeGreaterThan(0);
    expect(out.past).toBeGreaterThan(10 * 1024 * 1024);
  });
});

describe("a commit's body", () => {
  it("stays within the door's limit whatever its paths' characters", async () => {
    // A path's characters as the body carries them: a non-ASCII one 2 to 4 bytes, a control
    // character escaped to 6. A folder chain near the path's most (4,096 bytes), each name of
    // `é` and U+0001 (3 bytes on disk, 8 in the body), and `MAX_SYNC_OPS` files of
    // `INLINE_BYTES` under it: about 15 MB of ops, past the door's 12 MiB, though their
    // paths count about 8 MB in UTF-16 units.
    const { place: p, local } = place();
    const name = "é\u0001".repeat(84);
    let dir = local;
    for (let level = 0; level < 14; level++) {
      dir = join(dir, name);
    }
    mkdirSync(dir, { recursive: true });
    for (let f = 0; f < MAX_SYNC_OPS; f++) {
      writeFileSync(join(dir, `file-${f}`), "x".repeat(INLINE_BYTES));
    }
    const bodies: number[] = [];
    const sent: string[] = [];
    const answered = await serve((door, raw) => {
      if (door !== "commit") {
        return { status: 404, text: "{}" };
      }
      bodies.push(Buffer.byteLength(raw, "utf8"));
      return { status: 200, text: JSON.stringify(commitAll(JSON.parse(raw) as
        Record<string, unknown>, sent).body) };
    });
    await syncOut(answered, p, mirrorOf(), LEASE, []);
    expect(sent.filter((path) => path.includes("/file-"))).toHaveLength(MAX_SYNC_OPS);
    expect(bodies.reduce((sum, each) => sum + each, 0)).toBeGreaterThan(SYNC_BODY_MAX);
    expect(Math.max(...bodies)).toBeLessThanOrEqual(SYNC_BODY_MAX);
  });

  it("fails loudly on an intent id of another length than it counted", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "big.bin"), Buffer.alloc(10_000, 7));
    for (const id of ["i1", "1".repeat(INTENT_ID_CHARS + 1), 7]) {
      await expect(syncOut(await client((door, body) => door === "upload_begin"
        ? { status: 200, body: { intent_id: id, missing: body["chunks"] } }
        : door === "commit" ? commitAll(body) : { status: 200, body: {} }),
                           p, mirrorOf(), LEASE, []))
        .rejects.toThrow(`intent id is not ${INTENT_ID_CHARS} characters`);
    }
  });
});
