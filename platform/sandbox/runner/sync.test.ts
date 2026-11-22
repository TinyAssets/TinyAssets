/**
 * The sync agent against a stand-in sync door, and the attacks it must answer:
 * - a refusal of one file is that file's, with the object's whole message, and never the
 *   sync's: a full storage at an upload's begin, a `file_quota` refusal never read as a fence;
 * - the mirror is written as each batch commits, from each file's identity when it was
 *   offered, so a change made meanwhile is sent next time and a file gone meanwhile is noted;
 * - an upload given up (its file changed mid-sync) is aborted at once;
 * - the sync-ignore never hides itself, and its defaults never stop a path already kept;
 * - lines past the matcher's ceilings keep their files syncing, and are said (the matcher
 *   itself, as git reads a .gitignore, is `contract/guards/sync-ignore.test.ts`);
 * - memory stays bounded both ways: sync-in of a file larger than the sandbox's memory, and
 *   sync-out over many paths; the matcher's, at `LIST_BYTES` of any class, within what the
 *   sandbox's heap leaves beside the agent (`sizing.ts`).
 */
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, utimesSync,
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
  AUTOMATON_STATES, ignoredBy, LIST_BYTES, REMEMBERED_NAME_BYTES, SYNC_IGNORE,
} from "../../contract/src/sync-ignore";
import { CHUNK_BYTES, INLINE_BYTES, INTENT_ID_BYTES, MAX_SYNC_OPS, SYNC_BODY_MAX }
  from "../../contract/src/limits";
import { sha256Hex } from "../../contract/src/digest";
import { served } from "../../sources/src/served";
import { isKept, resumePaths } from "./engine";
import { AGENT_PEAK, HEAP_PER_LIST_BYTE, SANDBOX_HEAP } from "./sizing";
import { fetchSessions, openMirror, type Place, SyncClient, SyncFenced, syncIn, syncOut,
         tookSessions } from "./sync";

/** An upload's intent id, as the object makes it: `INTENT_ID_BYTES` as hex. */
const INTENT_ID_CHARS = 2 * INTENT_ID_BYTES;

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
/** A text's hash, as the mirror keeps a file's. */
const sha = (text: string) => sha256Hex(text);

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
    const sent: string[] = [];
    const report = await syncOut(await client((door, body) => {
      if (door === "commit") {
        rmSync(join(local, "a.txt"), { force: true });
        return commitAll(body, sent);
      }
      return { status: 404, body: {} };
    }), p, mirror, LEASE, []);
    expect(report.committed).toBeGreaterThanOrEqual(2);
    // Gone once its write landed: its removal follows, by this sync's scan or the next's.
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent.filter((path) => path === "/workspace/a.txt")).toHaveLength(2);
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
    mirror.put("/workspace/build/kept.txt", { version: 1, size: 1, mtime_ms: 0, sha256: "x",
                                              unread: false });
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirror, LEASE, []);
    expect(sent).toContain("/workspace/build/kept.txt");
    expect(sent).not.toContain("/workspace/build/new.txt");
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

describe("kept files and same-size edits at sync-out", () => {
  it("says a kept file whose changes a run's own line holds back", async () => {
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

  it("sends a same-size edit that kept its change time, by the next sync", async () => {
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

describe("a name with a backslash", () => {
  it("is offered as it is, a backslash being a name's own character on Linux", async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, "a"));
    writeFileSync(join(local, "a\\b.txt"), "the backslash's");
    writeFileSync(join(local, "a", "b.txt"), "the directory's");
    mkdirSync(join(local, ".ta"));
    writeFileSync(join(local, ".ta", "sync-ignore"), "a/\n");
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    // The directory's file is kept out by its line; the other is no path under it.
    expect(sent.filter((path) => path !== SYNC_IGNORE))
      .toEqual(["/workspace/a\\b.txt"]);
  });
});

describe("the engines' sessions in the workspace", () => {
  it("are never offered as the owner's files: the sessions tree is the store's to send",
     async () => {
    const { place: p, local } = place();
    mkdirSync(join(local, ".sessions", "owner", "claude-code"), { recursive: true });
    writeFileSync(join(local, ".sessions", "owner", "claude-code", "s.jsonl"), "a session");
    mkdirSync(join(local, "notes", ".sessions"), { recursive: true });
    writeFileSync(join(local, "notes", ".sessions", "kept.md"), "a file of the owner's");
    const sent: string[] = [];
    await syncOut(await client((door, body) => door === "commit" ? commitAll(body, sent)
      : { status: 404, body: {} }), p, mirrorOf(), LEASE, []);
    expect(sent.filter((path) => path !== SYNC_IGNORE))
      .toEqual(["/workspace/notes/.sessions/kept.md"]);
  });
});

describe("the matcher's memory in the sync agent's heap", () => {
  // `gc` for this test alone, so a list's heap is read after the garbage of its making.
  setFlagsFromString("--expose-gc");
  const gc = runInNewContext("gc") as () => void;
  // Heads the lines share, and heads that diverge (a trie's and Aho–Corasick's worst).
  const head = (i: number) => (Math.imul(i + 1, 2654435761) >>> 0).toString(36);
  const shapes: Record<string, (i: number) => string> = {
    literalName: (i) => `name-${i.toString(36)}-xxxxxxxx`,
    literalPath: (i) => `dir/${i.toString(36)}/name-xxxx`,
    suffix: (i) => `*.ext-${i.toString(36)}-xxxx`,
    prefix: (i) => `pre-${i.toString(36)}-xxxx*`,
    pathPrefix: (i) => `dir/${i.toString(36)}/x*`,
    anywhere: (i) => `**/dir/${i.toString(36)}/x`,
    automaton: (i) => `*${i.toString(36)}?x*y`,
    negated: (i) => `!name-${i.toString(36)}-xxxx`,
    literalNameDiverging: (i) => `${head(i)}${i.toString(36)}`,
    literalPathDiverging: (i) => `${head(i)}/${i.toString(36)}`,
    suffixDiverging: (i) => `*${head(i)}${i.toString(36)}`,
    prefixDiverging: (i) => `${head(i)}${i.toString(36)}*`,
    pathPrefixDiverging: (i) => `${head(i)}/${i.toString(36)}*`,
    anywhereDiverging: (i) => `**/${head(i)}/${i.toString(36)}`,
  };

  it("holds, at `LIST_BYTES` of any class (each measured at a quarter of it, a list byte's " +
     "heap), what the sandbox's heap leaves beside the agent",
     { tags: ["large"] }, () => {
    expect(LIST_BYTES * HEAP_PER_LIST_BYTE).toBeLessThanOrEqual(SANDBOX_HEAP - AGENT_PEAK);
    const used = () => process.memoryUsage().heapUsed + process.memoryUsage().arrayBuffers;
    // Two full collections settle what a collection leaves for the next (weak maps, the
    // code a first call compiles), so a sample is the matcher's own heap.
    const settled = () => {
      gc();
      gc();
      return used();
    };
    for (const [label, shape] of Object.entries(shapes)) {
      // The least of three samples: a sample is the matcher's heap plus whatever else the
      // process held for a moment (its own lazily made structures, measured once in a
      // whole-suite run at 53 against 45 alone); the least is the matcher's own, the bound
      // unchanged.
      let perByte = Number.POSITIVE_INFINITY;
      for (let sample = 0; sample < 3; sample++) {
        const base = settled();
        const lines: string[] = [];
        let bytes = 0;
        for (let i = 0; bytes < LIST_BYTES / 4; i++) {
          lines.push(shape(i));
          bytes += shape(i).length + 1;
        }
        const matcher = ignoredBy(lines);
        matcher("a/b.c");
        perByte = Math.min(perByte, (settled() - base) / bytes);
        expect(lines.length + (typeof matcher === "function" ? 0 : 1)).toBeGreaterThan(0);
      }
      expect(perByte, label).toBeLessThanOrEqual(HEAP_PER_LIST_BYTE);
    }
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

/**
 * The owner's files as a stand-in holds them: each path's version and text, null if gone or
 * a folder (`kind`).
 */
type Files = Map<string, { version: number; text: string | null; kind?: "dir" }>;

/**
 * A stand-in feed, read and commit door over `files`, a commit's write against a version that
 * moved on, onto a folder, or under a file (the copy beside that file, the rest of the path
 * below it) kept beside the owner's under a conflict name and its outcome naming the owner's
 * entry, as the owner's object does (`files.ts` `#syncOp`, `#copyPath`); `refuse` names what a
 * commit refuses (the owner's storage full).
 */
function feedOf(files: Files, reads: string[], refuse: Set<string> = new Set()): Handler {
  const kindOf = (file: { text: string | null; kind?: "dir" } | undefined) =>
    file?.kind ?? (file?.text == null ? null : "file");
  return (door, body) => {
    if (door === "changes") {
      const after = body["after"] as number;
      const changes = [...files.entries()].filter(([, file]) => file.version > after)
        .map(([path, file]) => ({ path, kind: kindOf(file), version: file.version }));
      return { status: 200, body: { changes, reset: false, cursor: Math.max(after,
        ...changes.map((change) => change.version)) } };
    }
    if (door === "read") {
      const path = body["path"] as string;
      reads.push(path);
      const file = files.get(path);
      return file?.kind === "dir"
        ? { status: 200, body: { entry: { version: file.version, mode: 0, kind: "dir" } } }
        : file === undefined || file.text === null ? { status: 404, body: {} }
        : { status: 200, body: { entry: { version: file.version, mode: 0, kind: "file" },
                                 inline: Buffer.from(file.text).toString("base64") } };
    }
    if (door !== "commit") {
      return { status: 404, body: {} };
    }
    const ops = body["ops"] as { op: string; path: string; base: number | null;
                                 inline?: string }[];
    return { status: 200, body: { outcomes: ops.map((op) => {
      if (refuse.has(op.path)) {
        return { path: op.path, outcome: "refused", reason: FULL, code: "storage_full" };
      }
      const now = files.get(op.path);
      const current = kindOf(now) === null ? undefined : now;
      const text = op.op === "remove" ? null : Buffer.from(op.inline!, "base64").toString();
      const parts = op.path.split("/");
      const blocked = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"))
        .find((dir) => kindOf(files.get(dir)) === "file");
      if ((op.base === null ? current !== undefined : current?.version !== op.base)
          || (text !== null && (current?.kind === "dir" || blocked !== undefined))) {
        if (text !== null) {
          const at = blocked ?? op.path;
          const name = (n: number) => `${at}.ta-conflict-${LEASE.epoch}${n > 1 ? `-${n}` : ""}${
            op.path.slice(at.length)}`;
          let n = 1;
          while (files.get(name(n))?.text != null) {
            n += 1;
          }
          files.set(name(n), { version: version += 1, text });
        }
        return { path: op.path, outcome: "conflict", current: current === undefined ? null
          : { kind: kindOf(current), version: current.version } };
      }
      files.set(op.path, { version: version += 1, text });
      return { path: op.path, outcome: "committed",
               entry: text === null ? null : { version } };
    }) } };
  };
}

describe("a change the mirror already holds", () => {
  it("is never fetched back: this sandbox's own write stays as it sent it", async () => {
    const { place: p, local } = place();
    writeFileSync(join(local, "a.md"), "mine");
    const mirror = mirrorOf();
    await syncOut(await client(feedOf(new Map(), [])), p, mirror, LEASE, []);
    const files: Files = new Map([["/workspace/a.md", {
      version: mirror.get("/workspace/a.md")!.version!, text: "mine" }]]);
    const reads: string[] = [];
    await syncIn(await client(feedOf(files, reads)), p, mirror);
    expect(reads).toEqual([]);
    // Another writer's version is fetched.
    files.set("/workspace/a.md", { version: files.get("/workspace/a.md")!.version + 1,
                                   text: "theirs" });
    await syncIn(await client(feedOf(files, reads)), p, mirror);
    expect([reads, readFileSync(join(local, "a.md"), "utf8")])
      .toEqual([["/workspace/a.md"], "theirs"]);
  });
});

describe("the engines' sessions from the owner's files", () => {
  const claude = served().get("claude-code")!;
  const at = "/workspace/.sessions/owner/claude-code/.claude/projects/main";
  const records = new Map([["claude-code", claude]]);

  it("are marked changed by the feed, never fetched; a resume fetches its own files alone",
     async () => {
    const { place: made } = place();
    const p = { ...made, records };
    mkdirSync(p.sessionsIn);
    const files = new Map<string, { version: number; text: string | null }>([
      [`${at}/s1.jsonl`, { version: 5, text: "one" }],
      [`${at}/s1/subagents/a.jsonl`, { version: 6, text: "its subagent" }],
      [`${at}/s2.jsonl`, { version: 7, text: "another conversation" }]]);
    const reads: string[] = [];
    const mirror = mirrorOf();
    const door = await client(feedOf(files, reads));
    await syncIn(door, p, mirror);
    expect(reads).toEqual([]);
    // Marked, with no base nor bytes: this sandbox has read none of it.
    expect(mirror.get(`${at}/s2.jsonl`)).toEqual({ version: null, size: 0, mtime_ms: 0,
                                                   sha256: "", unread: true });
    // A resume's fetch, the store taking all it fetched (`storeResume`, the root's).
    const fetch = async (have: Record<string, string>) => {
      const { fetched, cursor } = await fetchSessions(
        door, p, mirror, "claude-code", resumePaths(claude, "s1"), new Map(Object.entries(have)));
      tookSessions(mirror, fetched, cursor);
      return { fetched: fetched.filter((each) => each.got !== null).map((each) => each.path),
               gone: fetched.filter((each) => each.got === null).map((each) => each.path) };
    };
    expect(await fetch({})).toEqual({ fetched: [`${at}/s1.jsonl`, `${at}/s1/subagents/a.jsonl`],
                                      gone: [] });
    expect(reads).not.toContain(`${at}/s2.jsonl`);
    expect(readFileSync(join(p.sessionsIn, "claude-code", ".claude/projects/main/s1.jsonl"),
                        "utf8")).toBe("one");
    // The store holding both as the owner's files do, none is fetched again.
    reads.length = 0;
    const both = { [`${at}/s1.jsonl`]: sha("one"),
                   [`${at}/s1/subagents/a.jsonl`]: sha("its subagent") };
    expect([await fetch(both), reads]).toEqual([{ fetched: [], gone: [] }, []]);
    // One changed in the owner's files, one gone from them: fetched again, and named.
    files.set(`${at}/s1.jsonl`, { version: 10, text: "one, edited" });
    files.set(`${at}/s1/subagents/a.jsonl`, { version: 11, text: null });
    await syncIn(door, p, mirror);
    expect(await fetch(both)).toEqual({ fetched: [`${at}/s1.jsonl`],
                                        gone: [`${at}/s1/subagents/a.jsonl`] });
    expect(mirror.get(`${at}/s1/subagents/a.jsonl`)).toBeUndefined();
    // A store copy holding a turn of its own is never fetched over, nor named gone: the
    // resume reads it, and the run's staging sends it, a conflict copy at the owner's files.
    files.set(`${at}/s1/subagents/b.jsonl`, { version: 12, text: "another subagent" });
    await syncIn(door, p, mirror);
    await fetch({ [`${at}/s1.jsonl`]: sha("one, edited") });
    files.set(`${at}/s1.jsonl`, { version: 13, text: "one, edited again" });
    files.set(`${at}/s1/subagents/b.jsonl`, { version: 14, text: null });
    await syncIn(door, p, mirror);
    reads.length = 0;
    expect([await fetch({ [`${at}/s1.jsonl`]: sha("one, edited, and a turn"),
                          [`${at}/s1/subagents/b.jsonl`]: sha("its own turn") }), reads])
      .toEqual([{ fetched: [], gone: [] }, []]);
    expect(mirror.get(`${at}/s1/subagents/b.jsonl`)).toMatchObject({ unread: true });
  });

  it("staged before a sync-in marks it changed, lands beside the owner's edit, never over it",
     async () => {
    const { place: made } = place();
    const p = { ...made, records };
    const files = new Map<string, { version: number; text: string | null }>();
    const mirror = mirrorOf();
    const door = await client(feedOf(files, []));
    const path = `${at}/s1.jsonl`;
    const staged = join(p.sessionsOut, "claude-code", ".claude/projects/main/s1.jsonl");
    mkdirSync(dirname(staged), { recursive: true });
    writeFileSync(staged, "one");
    await syncOut(door, p, mirror, LEASE, []);
    // The owner edits it; this sandbox's next run stages its own turn; another run's sync-in
    // marks it changed before this run's sync-out.
    files.set(path, { version: version += 1, text: "one, the owner's edit" });
    writeFileSync(staged, "one, and this run's turn");
    await syncIn(door, p, mirror);
    const report = await syncOut(door, p, mirror, LEASE, []);
    // Still marked, its hash the copy's sent, so the next staging drops the store's copy
    // while it is that one and a resume fetches the owner's: one conflict copy, never one a
    // sync.
    expect([report.conflicts, files.get(path)!.text,
            files.get(`${path}.ta-conflict-${LEASE.epoch}`)?.text, mirror.get(path)])
      .toEqual([[path], "one, the owner's edit", "one, and this run's turn",
                expect.objectContaining({ sha256: sha("one, and this run's turn"),
                                          unread: true })]);
  });
});

describe("a workspace file holding changes not sent, when the owner's files change it", () => {
  const at = "/workspace/notes.md";
  const copy = `${at}.ta-conflict-${LEASE.epoch}`;

  /** A sandbox whose `notes.md` the owner's files hold as it sent it; its door, and theirs. */
  async function synced(text = "v1") {
    const { place: p, local } = place();
    const files: Files = new Map();
    const reads: string[] = [];
    const refuse = new Set<string>();
    const mirror = mirrorOf();
    const door = await client(feedOf(files, reads, refuse));
    writeFileSync(join(local, "notes.md"), text);
    await syncOut(door, p, mirror, LEASE, []);
    return { p, local, files, reads, refuse, mirror, door,
             file: () => readFileSync(join(local, "notes.md"), "utf8") };
  }

  it("refused at a full storage, ends in one conflict copy: never replaced, never a loop",
     async () => {
    const s = await synced();
    writeFileSync(join(s.local, "notes.md"), "the run's edit");
    s.refuse.add(at);
    const refused = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect(refused.refused.map((each) => each.code)).toEqual(["storage_full"]);
    s.refuse.clear();
    // The owner edits it meanwhile; the next run's sync-in leaves the sandbox's edit.
    s.files.set(at, { version: version += 1, text: "the owner's edit" });
    await syncIn(s.door, s.p, s.mirror);
    expect([s.reads, s.file()]).toEqual([[], "the run's edit"]);
    // Its sync-out: the owner's edit stays, the run's beside it, said, and the sandbox takes
    // the owner's.
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([report.conflicts, s.files.get(at)!.text, s.files.get(copy)?.text, s.file()])
      .toEqual([[at], "the owner's edit", "the run's edit", "the owner's edit"]);
    // No loop: the next sync-out sends nothing, and sync-in brings the copy.
    const sent = s.files.size;
    const again = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    await syncIn(s.door, s.p, s.mirror);
    expect([again.conflicts, s.files.size,
            readFileSync(join(s.local, "notes.md.ta-conflict-1"), "utf8")])
      .toEqual([[], sent, "the run's edit"]);
    expect((await syncOut(s.door, s.p, s.mirror, LEASE, [])).committed).toBe(0);
  });

  it("written by a run while sync-in fetches the owner's, is kept, and sent as a copy",
     async () => {
    const s = await synced();
    s.files.set(at, { version: version += 1, text: "the owner's edit" });
    const racing = await client((door, body) => {
      if (door === "read") {
        writeFileSync(join(s.local, "notes.md"), "a second run's edit, mid-fetch");
      }
      return feedOf(s.files, s.reads)(door, body);
    });
    await syncIn(racing, s.p, s.mirror);
    expect(s.file()).toBe("a second run's edit, mid-fetch");
    await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([s.files.get(at)!.text, s.files.get(copy)?.text, s.file()])
      .toEqual(["the owner's edit", "a second run's edit, mid-fetch", "the owner's edit"]);
  });

  it("written again while its conflict commits, is kept: a change of its own, sent again",
     async () => {
    const s = await synced();
    writeFileSync(join(s.local, "notes.md"), "the first edit");
    s.files.set(at, { version: version += 1, text: "the owner's edit" });
    const racing = await client((door, body) => {
      if (door === "commit") {
        writeFileSync(join(s.local, "notes.md"), "the second edit, mid-commit");
      }
      return feedOf(s.files, s.reads)(door, body);
    });
    await syncOut(racing, s.p, s.mirror, LEASE, []);
    expect([s.files.get(copy)?.text, s.file()])
      .toEqual(["the first edit", "the second edit, mid-commit"]);
    await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([s.files.get(`${copy}-2`)?.text, s.file()])
      .toEqual(["the second edit, mid-commit", "the owner's edit"]);
  });

  it("made new where the owner's files make the same path, is sent beside theirs",
     async () => {
    const s = await synced();
    writeFileSync(join(s.local, "draft.md"), "the run's draft");
    s.files.set("/workspace/draft.md", { version: version += 1, text: "the owner's draft" });
    await syncIn(s.door, s.p, s.mirror);
    expect(readFileSync(join(s.local, "draft.md"), "utf8")).toBe("the run's draft");
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([report.conflicts, s.files.get(`/workspace/draft.md.ta-conflict-${LEASE.epoch}`)
      ?.text, readFileSync(join(s.local, "draft.md"), "utf8")])
      .toEqual([["/workspace/draft.md"], "the run's draft", "the owner's draft"]);
  });

  it("removed from the owner's files, alone or with its folder, is sent beside, then goes",
     async () => {
    const s = await synced();
    mkdirSync(join(s.local, "docs"));
    writeFileSync(join(s.local, "docs", "kept.md"), "kept");
    writeFileSync(join(s.local, "docs", "plain.md"), "plain");
    await syncOut(s.door, s.p, s.mirror, LEASE, []);
    writeFileSync(join(s.local, "notes.md"), "the run's edit");
    writeFileSync(join(s.local, "docs", "kept.md"), "the run's edit of it");
    for (const path of [at, "/workspace/docs/kept.md", "/workspace/docs/plain.md",
                        "/workspace/docs"]) {
      s.files.set(path, { version: version += 1, text: null });
    }
    await syncIn(s.door, s.p, s.mirror);
    expect([s.file(), readFileSync(join(s.local, "docs", "kept.md"), "utf8"),
            existsSync(join(s.local, "docs", "plain.md"))])
      .toEqual(["the run's edit", "the run's edit of it", false]);
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([[...report.conflicts].sort(), s.files.get(copy)?.text,
            existsSync(join(s.local, "notes.md")), existsSync(join(s.local, "docs", "kept.md"))])
      .toEqual([["/workspace/docs/kept.md", at], "the run's edit", false, false]);
  });

  it("is the owner's to replace where sync-out never sends it (a default line's)",
     async () => {
    const s = await synced();
    mkdirSync(join(s.local, "build"));
    writeFileSync(join(s.local, "build", "out.js"), "rebuilt here");
    s.files.set("/workspace/build/out.js", { version: version += 1, text: "the owner's" });
    await syncIn(s.door, s.p, s.mirror);
    expect(readFileSync(join(s.local, "build", "out.js"), "utf8")).toBe("the owner's");
  });
});

describe("what a resume reads of an engine's store", () => {
  const rec = (id: string) => served().get(id)!;
  it("is the resumed session's own files, by each record's names, the id matched as it is",
     () => {
    const reads = (id: string, sid: string, rel: string) => isKept(resumePaths(rec(id), sid), rel);
    const rollout = ".codex/sessions/2026/11/22/rollout-2026-11-22T10-00-00-";
    const pi = ".pi/agent/sessions/--workspace--/2026-11-22_";
    expect([reads("codex", "t-1", `${rollout}t-1.jsonl`),
            reads("codex", "t-1", `${rollout}t-1.jsonl.zst`),
            reads("codex", "t-1", `${rollout}t-2.jsonl`),
            reads("pi", "p-1", `${pi}p-1.jsonl`), reads("pi", "p-1", `${pi}p-10.jsonl`),
            reads("codex", "t.1", `${rollout}tx1.jsonl`),
            reads("gemini", "g-1", ".gemini/tmp/main/chats/session-2026-11-22T10-00-ab.jsonl")])
      .toEqual([true, true, false, true, false, false, true]);
    expect(resumePaths(rec("pi"), null)).toEqual([]);
    for (const sid of ["a/b", "*", ""]) {
      expect(() => resumePaths(rec("pi"), sid)).toThrow("is not one name");
    }
  });
});

describe("a resume's session, when a run keeps a newer copy in the store during its fetch", () => {
  const claude = served().get("claude-code")!;
  const path = "/workspace/.sessions/owner/claude-code/.claude/projects/main/s1.jsonl";
  const copy = `${path}.ta-conflict-${LEASE.epoch}`;

  /**
   * A sandbox whose `s1` the owner's files hold as it sent it, then edited by the owner and
   * marked by a sync-in; `stage` puts a store copy in the sync agent's `out` (the staging).
   */
  async function edited() {
    const { place: made } = place();
    const p = { ...made, records: new Map([["claude-code", claude]]) };
    mkdirSync(p.sessionsIn);
    const files: Files = new Map();
    const mirror = mirrorOf();
    const door = await client(feedOf(files, []));
    const staged = join(p.sessionsOut, "claude-code", ".claude/projects/main/s1.jsonl");
    const stage = (text: string | null) => {
      rmSync(p.sessionsOut, { recursive: true, force: true });
      if (text !== null) {
        mkdirSync(dirname(staged), { recursive: true });
        writeFileSync(staged, text);
      }
    };
    stage("one");
    await syncOut(door, p, mirror, LEASE, []);
    stage(null);
    files.set(path, { version: version += 1, text: "the owner's edit" });
    await syncIn(door, p, mirror);
    const fetch = () => fetchSessions(door, p, mirror, "claude-code", resumePaths(claude, "s1"),
                                      new Map([[path, sha("one")]]));
    return { p, files, mirror, door, stage, fetch };
  }

  it("not taken, is sent against the base that copy was made on: a conflict copy, never " +
     "over the owner's", async () => {
    const s = await edited();
    const { fetched } = await s.fetch();
    expect(fetched.map((each) => each.got?.sha256)).toEqual([sha("the owner's edit")]);
    // Another run kept its turn in the store meanwhile, so the root takes nothing; its
    // staging sends that copy.
    s.stage("one, and a turn kept meanwhile");
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([report.conflicts, s.files.get(path)!.text, s.files.get(copy)?.text])
      .toEqual([[path], "the owner's edit", "one, and a turn kept meanwhile"]);
  });

  it("taken, moves its row to the owner's version, only while the row is the one fetched " +
     "against, and marked where a sync-in since may have named a newer one", async () => {
    const s = await edited();
    const first = await s.fetch();
    tookSessions(s.mirror, first.fetched, first.cursor);
    // Taken: the store's copy, the owner's, is in sync, never sent and never fetched again.
    s.stage("the owner's edit");
    expect((await syncOut(s.door, s.p, s.mirror, LEASE, [])).committed).toBe(0);
    s.stage(null);
    expect((await fetchSessions(s.door, s.p, s.mirror, "claude-code", resumePaths(claude, "s1"),
                                new Map([[path, sha("the owner's edit")]]))).fetched).toEqual([]);
    // A fetch whose row a sync-out moved meanwhile (another run's turn landed) leaves it.
    s.files.set(path, { version: version += 1, text: "the owner's second edit" });
    await syncIn(s.door, s.p, s.mirror);
    const stale = await fetchSessions(s.door, s.p, s.mirror, "claude-code",
                                      resumePaths(claude, "s1"),
                                      new Map([[path, sha("the owner's edit")]]));
    expect(stale.fetched).toHaveLength(1);
    s.stage("a turn landed meanwhile");
    await syncOut(s.door, s.p, s.mirror, LEASE, []);
    const landed = s.mirror.get(path);
    tookSessions(s.mirror, stale.fetched, stale.cursor);
    expect(s.mirror.get(path)).toEqual(landed);
    // A fetch the owner's files moved on from before its take stays marked: fetched again.
    s.stage(null);
    s.files.set(path, { version: version += 1, text: "the owner's third edit" });
    await syncIn(s.door, s.p, s.mirror);
    const third = await fetchSessions(s.door, s.p, s.mirror, "claude-code",
                                      resumePaths(claude, "s1"),
                                      new Map([[path, sha("a turn landed meanwhile")]]));
    s.files.set(path, { version: version += 1, text: "the owner's fourth edit" });
    await syncIn(s.door, s.p, s.mirror);
    tookSessions(s.mirror, third.fetched, third.cursor);
    const fourth = await fetchSessions(s.door, s.p, s.mirror, "claude-code",
                                       resumePaths(claude, "s1"),
                                       new Map([[path, sha("the owner's third edit")]]));
    expect(fourth.fetched.map((each) => each.got?.sha256))
      .toEqual([sha("the owner's fourth edit")]);
  });

  it("gone from the owner's files, keeps its row while the store keeps a copy made since",
     async () => {
    const s = await edited();
    s.files.set(path, { version: version += 1, text: null });
    await syncIn(s.door, s.p, s.mirror);
    const gone = await s.fetch();
    expect(gone.fetched.map((each) => each.got)).toEqual([null]);
    // Not taken (a run kept a newer copy): sent against its base, beside the owner's removal.
    s.stage("one, and a turn kept meanwhile");
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([report.conflicts, s.files.get(path)!.text, s.files.get(copy)?.text])
      .toEqual([[path], null, "one, and a turn kept meanwhile"]);
  });
});

describe("a path that changes kind in the owner's files", () => {
  /** A sandbox holding the owner's files as it sent them: its door, and theirs. */
  async function sent(write: Record<string, string>) {
    const { place: p, local } = place();
    const files: Files = new Map();
    const mirror = mirrorOf();
    const door = await client(feedOf(files, []));
    for (const [rel, text] of Object.entries(write)) {
      mkdirSync(dirname(join(local, rel)), { recursive: true });
      writeFileSync(join(local, rel), text);
    }
    await syncOut(door, p, mirror, LEASE, []);
    const read = (rel: string) => existsSync(join(local, rel))
      ? statSync(join(local, rel)).isDirectory() ? "a folder" : readFileSync(join(local, rel), "utf8")
      : null;
    return { p, local, files, mirror, door, read };
  }

  it("a file where the sandbox holds a folder: its unsent files sent beside, then the owner's " +
     "file", async () => {
    const s = await sent({ "docs/kept.md": "kept", "docs/plain.md": "plain" });
    writeFileSync(join(s.local, "docs", "kept.md"), "the run's edit");
    writeFileSync(join(s.local, "docs", "new.md"), "the run's new file");
    // The owner removes the folder, as the files door does (its entries first), and makes a file.
    for (const rel of ["docs/kept.md", "docs/plain.md"]) {
      s.files.set(`/workspace/${rel}`, { version: version += 1, text: null });
    }
    s.files.set("/workspace/docs", { version: version += 1, text: "the owner's docs" });
    await syncIn(s.door, s.p, s.mirror);
    expect([s.read("docs/kept.md"), s.read("docs/new.md"), s.read("docs/plain.md")])
      .toEqual(["the run's edit", "the run's new file", null]);
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    const beside = `/workspace/docs.ta-conflict-${LEASE.epoch}`;
    expect([[...report.conflicts].sort(), s.files.get(`${beside}/kept.md`)?.text,
            s.files.get(`${beside}/new.md`)?.text, s.files.get("/workspace/docs")!.text,
            s.read("docs")])
      .toEqual([["/workspace/docs/kept.md", "/workspace/docs/new.md"], "the run's edit",
                "the run's new file", "the owner's docs", "the owner's docs"]);
    // No loop: sync-in brings the copies, and nothing is sent again.
    await syncIn(s.door, s.p, s.mirror);
    expect([s.read("docs.ta-conflict-1/new.md"),
            (await syncOut(s.door, s.p, s.mirror, LEASE, [])).committed])
      .toEqual(["the run's new file", 0]);
  });

  it("a folder where the sandbox holds a file: its unsent edit sent beside, then the owner's " +
     "folder and what it holds", async () => {
    const s = await sent({ "notes.md": "v1" });
    writeFileSync(join(s.local, "notes.md"), "the run's edit");
    s.files.set("/workspace/notes.md", { version: version += 1, text: null, kind: "dir" });
    s.files.set("/workspace/notes.md/a.md", { version: version += 1, text: "theirs" });
    s.files.set("/workspace/notes.md/sub/c.md", { version: version += 1, text: "deeper" });
    s.files.set("/workspace/notes.md/b.md", { version: version += 1, text: "gone again" });
    s.files.set("/workspace/notes.md/b.md", { version: version += 1, text: null });
    await syncIn(s.door, s.p, s.mirror);
    expect(s.read("notes.md")).toBe("the run's edit");
    const report = await syncOut(s.door, s.p, s.mirror, LEASE, []);
    expect([report.conflicts, s.files.get(`/workspace/notes.md.ta-conflict-${LEASE.epoch}`)?.text,
            s.read("notes.md/a.md"), s.read("notes.md/sub/c.md"), s.read("notes.md/b.md")])
      .toEqual([["/workspace/notes.md"], "the run's edit", "theirs", "deeper", null]);
    expect((await syncOut(s.door, s.p, s.mirror, LEASE, [])).committed).toBe(0);
  });

  it("either way with nothing unsent: the owner's kind, at the sync-in", async () => {
    const s = await sent({ "docs/plain.md": "plain", "notes.md": "v1" });
    s.files.set("/workspace/docs/plain.md", { version: version += 1, text: null });
    s.files.set("/workspace/docs", { version: version += 1, text: "the owner's docs" });
    s.files.set("/workspace/notes.md", { version: version += 1, text: null, kind: "dir" });
    s.files.set("/workspace/notes.md/a.md", { version: version += 1, text: "theirs" });
    await syncIn(s.door, s.p, s.mirror);
    expect([s.read("docs"), s.read("notes.md/a.md")]).toEqual(["the owner's docs", "theirs"]);
    expect((await syncOut(s.door, s.p, s.mirror, LEASE, [])).committed).toBe(0);
  });
});

describe("a gone folder of many names", () => {
  setFlagsFromString("--expose-gc");
  const gc = runInNewContext("gc") as () => void;

  it("is walked an entry at a time: what sync-in holds does not grow with its listing",
     { tags: ["large"] }, async () => {
    // The most sync-in holds while it takes a gone folder of `names` long names, each kept
    // (none ever sent), sampled at its look at each one's row.
    const held = async (names: number) => {
      const { place: p, local } = place();
      mkdirSync(join(local, "big"));
      for (let i = 0; i < names; i++) {
        writeFileSync(join(local, "big", `${i}${"x".repeat(240)}`), "");
      }
      const files: Files = new Map([["/workspace/big", { version: version += 1, text: null }]]);
      const mirror = mirrorOf();
      let looks = 0;
      let most = 0;
      gc();
      gc();
      const base = process.memoryUsage().heapUsed;
      const sampled = { ...mirror, get: (at: string) => {
        if ((looks += 1) % 500 === 0) {
          gc();
          most = Math.max(most, process.memoryUsage().heapUsed - base);
        }
        return mirror.get(at);
      } };
      await syncIn(await client(feedOf(files, [])), p, sampled);
      expect(looks).toBeGreaterThanOrEqual(names);
      rmSync(join(local, "big"), { recursive: true, force: true });
      return most;
    };
    // A listing held whole would be about 300 bytes a name: 6 MB more over 20,000 names.
    expect(await held(22_000) - await held(2_000)).toBeLessThan(1_000_000);
  });
});
