/**
 * pi-durable 1.4.0's own `ExecutionEnv` conformance suite, run against the owner's
 * environment over the manifest and the real files Worker: every case but `exec`'s,
 * with no symbolic links (both come with the sandbox, tasks 6) and with `listFiles`.
 * Then the implicit compare-and-swap pi's `edit` relies on. The files are over a write
 * gate's view, as the object builds them, so every change an environment makes is a unit
 * of the gate, or the view refuses it.
 */
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { getOrThrow } from "@earendil-works/pi-durable/env";
import { createEnvConformance, createExpectAssertions } from "@earendil-works/pi-durable/testing";
import { createEditTool } from "@earendil-works/pi-durable/tools";
import { describe, expect, it } from "vitest";

import {
  BATCH_BYTES as WORKER_BATCH_BYTES, CHUNK_BYTES as WORKER_CHUNK_BYTES,
} from "../../files/src/keys";
import { BATCH_BYTES, CHUNK_BYTES, type ChunkStore, Files, INLINE_BYTES, sha256Hex } from "../src/files";
import { ignored, ignoreRules, OwnerFilesEnv } from "../src/files-env";
import { OUTSIDE_GATE, type WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { Refused } from "../src/errors";
import { FILE_DEFAULTS } from "../src/policy";
import {
  failure, faulty, filesOn, inGate, lostContent, ownerObject, sqlIn, STORAGE_FAULT,
} from "./support";

const context = BACKGROUND_CONTEXT;
/** The schedule's sync, which the object wires in; these cases drive no schedule. */
const rearm = async (): Promise<void> => undefined;

/**
 * The owner's files over `gate`'s view, as the object builds them, with the namespace
 * made, as the object's environment factory makes it before it builds an environment.
 */
async function gated(gate: WriteGate, store: ChunkStore = env.FILES): Promise<Files> {
  const files = filesOn(gate.storage, gate.write, store);
  await files.namespace();
  return files;
}

/** A fresh owner's object, and its files, for the length of `use`. */
function inOwner(use: (files: Files) => Promise<void>): Promise<void> {
  return inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) =>
    use(await gated(gate)));
}

type Get = { sha256: string; offset?: number; length?: number };
type Put = { sha256: string; bytes: Uint8Array };

/** The real files Worker, each get and put call it answers recorded. */
function counting(): { store: ChunkStore; gets: Get[][]; puts: Put[][] } {
  const gets: Get[][] = [];
  const puts: Put[][] = [];
  const store: ChunkStore = {
    putChunks: (ns, chunks) => {
      puts.push(chunks);
      return env.FILES.putChunks(ns, chunks);
    },
    getChunks: (ns, reads) => {
      gets.push(reads);
      return env.FILES.getChunks(ns, reads);
    },
    deleteChunks: (ns, shas) => env.FILES.deleteChunks(ns, shas),
  };
  return { store, gets, puts };
}

const cases = createEnvConformance({
  assertions: createExpectAssertions(expect),
  withEnv: (use) => inOwner((files) => use(new OwnerFilesEnv({ files, rearm }))),
  symlinks: false,
  listFiles: true,
});
const EXEC = cases.filter((c) => c.name.includes("exec"));

describe("pi-durable 1.4.0 env conformance", () => {
  it("is every case but the six exec cases, none skipped by accident", () => {
    expect(cases.length - EXEC.length).toBe(20);
    expect(EXEC.map((c) => c.name)).toHaveLength(6);
  });

  for (const c of cases.filter((each) => !EXEC.includes(each))) {
    it(c.name, () => c.run(), c.timeoutMs);
  }
});

describe("gitignore rules", () => {
  const rules = (text: string) => ignoreRules(text, "/workspace");
  it.each([
    ["*.log", "/workspace/a/b.log", false, true],
    ["*.log", "/workspace/a/b.logs", false, false],
    ["/top", "/workspace/a/top", false, false],
    ["/top", "/workspace/top", false, true],
    ["a/*.c", "/workspace/a/x.c", false, true],
    ["a/*.c", "/workspace/a/b/x.c", false, false],
    ["**/build", "/workspace/a/b/build", true, true],
    ["a/**", "/workspace/a/b/c", false, true],
    ["a/**/z", "/workspace/a/z", false, true],
    ["a/**/z", "/workspace/a/b/c/z", false, true],
    ["out/", "/workspace/out", false, false],
    ["out/", "/workspace/out", true, true],
    ["[!a]b", "/workspace/cb", false, true],
    ["[!a]b", "/workspace/ab", false, false],
    ["?.md", "/workspace/a.md", false, true],
    ["\\#x", "/workspace/#x", false, true],
    ["# comment", "/workspace/# comment", false, false],
    ["trailing   ", "/workspace/trailing", false, true],
    ["x.(y)+", "/workspace/x.(y)+", false, true],
  ])("%s on %s (a directory: %s) ignores it: %s", (pattern, path, directory, verdict) => {
    expect(ignored(rules(pattern), path, directory)).toBe(verdict);
  });

  it("lets the last matching rule decide, a negation included", () => {
    expect(ignored(rules("*.txt\n!keep.txt"), "/workspace/keep.txt", false)).toBe(false);
    expect(ignored(rules("!keep.txt\n*.txt"), "/workspace/keep.txt", false)).toBe(true);
    expect(ignored(ignoreRules("*", "/workspace/a"), "/workspace/b", false)).toBe(false);
  });
});

describe("the owner's environment", () => {
  it("cuts chunks and batches exactly as the files Worker takes them", () => {
    expect(CHUNK_BYTES).toBe(WORKER_CHUNK_BYTES);
    expect(BATCH_BYTES).toBe(WORKER_BATCH_BYTES);
  });

  it("answers exec as shell_unavailable until the sandbox slice", async () => {
    await inOwner(async (files) => {
      const result = await new OwnerFilesEnv({ files, rearm })
        .exec("true", undefined, context);
      expect(result.ok ? null : result.error.code).toBe("shell_unavailable");
    });
  });

  it("refuses an edit whose file changed between its read and its write", async () => {
    await inOwner(async (files) => {
      const first = new OwnerFilesEnv({ files, rearm });
      const second = new OwnerFilesEnv({ files, rearm });
      getOrThrow(await first.writeFile("notes.md", "one", context));
      // pi's edit: read, then write what it computed from that read.
      expect(getOrThrow(await second.readTextFile("notes.md", context))).toBe("one");
      getOrThrow(await first.writeFile("notes.md", "two", context));
      const lost = await second.writeFile("notes.md", "one, edited", context);
      expect(lost.ok ? null : [lost.error.code, lost.error.message])
        .toEqual(["unknown", "changed since read"]);
      expect(getOrThrow(await second.readTextFile("notes.md", context))).toBe("two");
      // Read again, the edit lands.
      getOrThrow(await second.writeFile("notes.md", "two, edited", context));
      expect(getOrThrow(await first.readTextFile("notes.md", context))).toBe("two, edited");
    });
  });

  it("refuses an append when the file changed while it fetched the tail", async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      let race: (() => Promise<unknown>) | null = null;
      const store = {
        putChunks: (ns: string, chunks: { sha256: string; bytes: Uint8Array }[]) =>
          env.FILES.putChunks(ns, chunks),
        getChunks: async (ns: string, reads: { sha256: string }[]) => {
          const answer = await env.FILES.getChunks(ns, reads);
          const run = race;
          race = null;
          await run?.();
          return answer;
        },
        deleteChunks: (ns: string, shas: string[]) => env.FILES.deleteChunks(ns, shas),
      };
      const files = await gated(gate, store);
      const fs = new OwnerFilesEnv({ files, rearm });
      getOrThrow(await fs.writeFile("log.txt", new Uint8Array(5000).fill(65), context));
      race = () => files.write("/workspace/log.txt", new Uint8Array([66]),
                               { expected: null, writer: "owner" });
      const lost = await fs.appendFile("log.txt", "more", context);
      expect(lost.ok ? null : lost.error.message).toBe("changed since read");
      expect(getOrThrow(await fs.readTextFile("log.txt", context))).toBe("B");
    });
  });

  it("writes unconditionally a path it never read, and keeps its own writes in order",
     async () => {
    await inOwner(async (files) => {
      const one = new OwnerFilesEnv({ files, rearm });
      const other = new OwnerFilesEnv({ files, rearm });
      getOrThrow(await other.writeFile("a.txt", "theirs", context));
      getOrThrow(await one.writeFile("a.txt", "mine", context));
      getOrThrow(await one.writeFile("a.txt", "mine again", context));
      getOrThrow(await one.appendFile("a.txt", "!", context));
      expect(getOrThrow(await other.readTextFile("a.txt", context))).toBe("mine again!");
      // Having read it, its own writes follow one another: each is the version it saw.
      getOrThrow(await other.writeFile("a.txt", "theirs, edited", context));
      getOrThrow(await other.writeFile("a.txt", "theirs, edited twice", context));
      expect(getOrThrow(await one.readTextFile("a.txt", context))).toBe("theirs, edited twice");
    });
  });

  it("re-checks the authority it runs under first in each change's own unit", async () => {
    await inOwner(async (files) => {
      const owner = new OwnerFilesEnv({ files, rearm });
      getOrThrow(await owner.writeFile("a.txt", "one", context));
      getOrThrow(await owner.createDir("dir", undefined, context));
      let stands = true;
      const theirs = new OwnerFilesEnv({ files, rearm, authorize: () => {
        if (!stands) {
          throw new Refused("outside client authority no longer stands");
        }
      } });
      getOrThrow(await theirs.writeFile("b.txt", "two", context));
      stands = false;
      const why = (result: { ok: boolean; error?: { code: string } }) =>
        result.ok ? "changed" : result.error!.code;
      expect([why(await theirs.writeFile("a.txt", "x", context)),
              why(await theirs.appendFile("a.txt", "x", context)),
              why(await theirs.createDir("new", undefined, context)),
              why(await theirs.renameFile("a.txt", "c.txt", context)),
              why(await theirs.remove("dir", { recursive: true }, context)),
              why(await theirs.createTempDir(undefined, context))])
        .toEqual(Array.from({ length: 6 }, () => "permission_denied"));
      expect(getOrThrow(await owner.readTextFile("a.txt", context))).toBe("one");
      expect([getOrThrow(await owner.exists("dir", context)),
              getOrThrow(await owner.exists("new", context)),
              getOrThrow(await owner.exists("c.txt", context))]).toEqual([true, false, false]);
    });
  });

  it("shares one id across environments of one owner, and none with another owner",
     async () => {
    let first = "";
    await inOwner(async (files) => {
      first = new OwnerFilesEnv({ files, rearm }).id;
      expect(new OwnerFilesEnv({ files, rearm }).id).toBe(first);
      expect(first).toMatch(/^ta-files:[0-9a-f]{32}$/);
      expect(first).not.toContain(files.ns());
    });
    await inOwner(async (files) => {
      expect(new OwnerFilesEnv({ files, rearm }).id).not.toBe(first);
    });
  });

  it("reads paths outside the owner's roots as missing and refuses writes to them",
     async () => {
    await inOwner(async (files) => {
      const fs = new OwnerFilesEnv({ files, rearm });
      const read = await fs.readTextFile("/etc/passwd", context);
      expect(read.ok ? null : read.error.code).toBe("not_found");
      expect(getOrThrow(await fs.exists("/tmp/x", context))).toBe(false);
      const wrote = await fs.writeFile("../escape.txt", "x", context);
      expect(wrote.ok ? null : wrote.error.code).toBe("invalid");
      expect(getOrThrow(await fs.absolutePath("~/a/../b", context))).toBe("/workspace/b");
      expect(getOrThrow(await fs.joinPath(["a", "..", "b", "./c"], context))).toBe("b/c");
    });
  });

  it("appends and truncates large files sending only the chunks after those kept",
     async () => {
    await inOwner(async (files) => {
      const fs = new OwnerFilesEnv({ files, rearm });
      const big = new Uint8Array(4 * 1024 * 1024 + 10).map((_, i) => i % 251);
      getOrThrow(await fs.writeFile("big.bin", big, context));
      const before = files.open("/workspace/big.bin").chunks!;
      getOrThrow(await fs.appendFile("big.bin", new Uint8Array([1, 2, 3]), context));
      const after = files.open("/workspace/big.bin").chunks!;
      expect(after[0]).toEqual(before[0]);
      expect(after[1]!.size).toBe(13);
      const read = getOrThrow(await fs.readBinaryFile("big.bin", context));
      expect(sha256Hex(read.subarray(0, big.length))).toBe(sha256Hex(big));
      expect([...read.subarray(big.length)]).toEqual([1, 2, 3]);
      getOrThrow(await fs.truncateFile("big.bin", 5, context));
      expect(getOrThrow(await fs.readBinaryFile("big.bin", context))).toEqual(big.subarray(0, 5));
      getOrThrow(await fs.truncateFile("big.bin", 8, context));
      expect([...getOrThrow(await fs.readBinaryFile("big.bin", context))])
        .toEqual([...big.subarray(0, 5), 0, 0, 0]);
      const invalid = await fs.truncateFile("big.bin", -1, context);
      expect(invalid.ok ? null : invalid.error.code).toBe("invalid");
    });
  });

  it("fetches only the kept tail and puts only the new last chunk on an append or truncate",
     async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      const { store, gets, puts } = counting();
      const files = await gated(gate, store);
      const fs = new OwnerFilesEnv({ files, rearm });
      const big = new Uint8Array(CHUNK_BYTES + 10).map((_, i) => i % 251);
      getOrThrow(await fs.writeFile("big.bin", big, context));
      const first = files.open("/workspace/big.bin").chunks![0]!;
      gets.length = 0;
      puts.length = 0;
      getOrThrow(await fs.appendFile("big.bin", new Uint8Array([1, 2, 3]), context));
      expect(gets).toEqual([[{ sha256: expect.any(String), offset: 0, length: 10 }]]);
      expect(puts.map((call) => call.map((put) => put.bytes.byteLength))).toEqual([[13]]);
      expect(files.open("/workspace/big.bin").chunks![0]).toEqual(first);
      gets.length = 0;
      puts.length = 0;
      getOrThrow(await fs.truncateFile("big.bin", CHUNK_BYTES + 5, context));
      expect(gets.flat().map((read) => read.length)).toEqual([5]);
      expect(puts.map((call) => call.map((put) => put.bytes.byteLength))).toEqual([[5]]);
      expect(files.open("/workspace/big.bin").chunks![0]).toEqual(first);
    });
  });

  it("reads a large file two chunks a call, by range or line, never whole", async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      const { store, gets } = counting();
      const files = await gated(gate, store);
      const fs = new OwnerFilesEnv({ files, rearm });
      // Five chunks, 16 MiB and one byte, sent by upload as the HTTP door will.
      const piece = new Uint8Array(CHUNK_BYTES).fill(65);
      const last = new Uint8Array([10]);
      const size = 4 * CHUNK_BYTES + 1;
      const begun = await files.unit(() => files.begin(
        size, [...Array(4).fill(sha256Hex(piece)), sha256Hex(last)]));
      await files.upload(begun.intent_id, [{ sha256: sha256Hex(piece), bytes: piece },
                                           { sha256: sha256Hex(last), bytes: last }]);
      await files.unit(() => files.commit(begun.intent_id, "/workspace/huge.txt",
                                          { expected: null, writer: "owner" }));
      gets.length = 0;
      for (const read of [await fs.readBinaryFile("huge.txt", context),
                          await fs.readTextFile("huge.txt", context)]) {
        expect(read.ok ? null : read.error.code).toBe("not_supported");
      }
      const grown = await fs.truncateFile("huge.txt", 2 * size, context);
      expect(grown.ok ? null : grown.error.code).toBe("not_supported");
      expect(gets).toEqual([]);
      const reader = getOrThrow(await fs.openBinaryReader("huge.txt", undefined, context));
      const tooMuch = await reader.read(0, size, context);
      expect(tooMuch.ok ? null : tooMuch.error.code).toBe("not_supported");
      expect(getOrThrow(await reader.read(CHUNK_BYTES - 1, 2, context))).toEqual(
        new Uint8Array([65, 65]));
      expect(gets).toHaveLength(1);
      // A read past the end is cut at it, and one wholly past it is empty.
      expect(getOrThrow(await reader.read(size - 1, 100, context))).toEqual(new Uint8Array([10]));
      expect(getOrThrow(await reader.read(size + 5, 1, context))).toEqual(new Uint8Array(0));
      expect(gets).toHaveLength(2);
      gets.length = 0;
      getOrThrow(await reader.scanLines({ startLine: 1 }, context));
      expect(gets.map((call) => call.length)).toEqual([2, 2, 1]);
    });
  }, 30_000);

  it("keeps a file written by the atomic-write idiom: a temporary file renamed into place",
     async () => {
    const r = ownerObject();
    const big = new Uint8Array(INLINE_BYTES + 100).fill(66);
    await inGate(r.stub, async (_instance: OwnerDO, _state, gate) => {
      const files = await gated(gate);
      const fs = new OwnerFilesEnv({ files, rearm });
      for (const [name, body] of [["small.txt", "small"], ["big.bin", big]] as const) {
        const spill = getOrThrow(await fs.createTempFile({ prefix: "spill-" }, context));
        getOrThrow(await fs.writeFile(spill, body, context));
        getOrThrow(await fs.renameFile(spill, name, context));
      }
      await fs.cleanup(context);
      expect(getOrThrow(await fs.readTextFile("small.txt", context))).toBe("small");
      expect(getOrThrow(await fs.readBinaryFile("big.bin", context))).toEqual(big);
    });
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1 WHERE gc_after IS NOT NULL");
    await inGate(r.stub, async (_instance: OwnerDO, _state, gate) => {
      const files = await gated(gate);
      await files.collect(Date.now() / 1000);
      const fs = new OwnerFilesEnv({ files, rearm });
      expect(getOrThrow(await fs.readBinaryFile("big.bin", context))).toEqual(big);
    });
    expect(await lostContent(r.stub)).toEqual([]);
  });

  it("keeps a BOM through pi's own edit, byte for byte", async () => {
    await inOwner(async (files) => {
      const fs = new OwnerFilesEnv({ files, rearm });
      const bytes = new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode("one\r\ntwo\r\n")]);
      getOrThrow(await fs.writeFile("bom.txt", bytes, context));
      const edit = createEditTool();
      await edit.execute({ path: "bom.txt", edits: [{ oldText: "two", newText: "three" }] },
                         { env: fs } as never, context);
      expect(getOrThrow(await fs.readBinaryFile("bom.txt", context))).toEqual(
        new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode("one\r\nthree\r\n")]));
    });
  });

  it("makes every change of a tool call a unit of the write gate, the view refusing others",
     async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      const files = await gated(gate);
      const fs = new OwnerFilesEnv({ files, rearm });
      // Each kind of change an environment makes, over the gate's view: a write, a chunked
      // write, an append, a directory, a rename, a remove, a temporary file and its cleanup.
      getOrThrow(await fs.writeFile("a.txt", "a", context));
      getOrThrow(await fs.writeFile("big.bin", new Uint8Array(INLINE_BYTES + 1).fill(3),
                                    context));
      getOrThrow(await fs.appendFile("a.txt", "b", context));
      getOrThrow(await fs.createDir("d/e", { recursive: true }, context));
      getOrThrow(await fs.renameFile("a.txt", "d/a.txt", context));
      getOrThrow(await fs.remove("big.bin", undefined, context));
      const temp = getOrThrow(await fs.createTempFile({ prefix: "t-" }, context));
      await fs.cleanup(context);
      expect(getOrThrow(await fs.readTextFile("d/a.txt", context))).toBe("ab");
      expect([getOrThrow(await fs.exists("big.bin", context)),
              getOrThrow(await fs.exists(temp, context))]).toEqual([false, false]);
      // The same change made directly, outside a unit, is refused before it writes.
      expect(() => files.mkdir("/workspace/direct", { expected: null, writer: "agent" }))
        .toThrow(OUTSIDE_GATE);
      expect(files.stat("/workspace/direct")).toBeNull();
    });
  });

  it("refuses a directory that is made while a plain createDir waits on the queue",
     async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, state, gate) => {
      const files = await gated(gate);
      const fs = new OwnerFilesEnv({ files, rearm });
      // A pi commit holds the queue, another change to the same path is queued behind it,
      // and the tool call's check and its mkdir are one unit after that: it sees the
      // directory and refuses, as it would had the change landed before the call.
      let release!: () => void;
      const held = new Promise<void>((resolve) => { release = resolve; });
      const commit = new DurableObjectSqliteDatabase(state.storage).transaction(() => held);
      const other = gate.write(() => files.mkdir("/workspace/d", { expected: null,
                                                                   writer: "owner" }));
      const made = fs.createDir("d", { recursive: false }, context);
      for (let i = 0; i < 20; i++) {
        await Promise.resolve();
      }
      release();
      await Promise.all([commit, other]);
      const answer = await made;
      expect(answer.ok ? null : [answer.error.code, answer.error.message])
        .toEqual(["invalid", "exists: already exists"]);
    });
  });

  it("fails a tool call, and cleanup, whose change cannot be written", async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      let down = false;
      const files = filesOn(gate.storage, faulty(gate.write, () => down));
      await files.namespace();
      const fs = new OwnerFilesEnv({ files, rearm });
      getOrThrow(await fs.writeFile("a.txt", "a", context));
      getOrThrow(await fs.createTempDir("t-", context));
      down = true;
      const calls = {
        createDir: fs.createDir("d", { recursive: true }, context),
        renameFile: fs.renameFile("a.txt", "b.txt", context),
        remove: fs.remove("a.txt", undefined, context),
      };
      for (const [name, call] of Object.entries(calls)) {
        const answer = await call;
        expect([name, answer.ok ? "ok" : answer.error.message]).toEqual([name, STORAGE_FAULT]);
      }
      expect((await failure(() => fs.cleanup(context))).message).toBe(STORAGE_FAULT);
      down = false;
      expect(getOrThrow(await fs.readTextFile("a.txt", context))).toBe("a");
    });
  });

  it("reads a range across more than two chunks as its calls, two chunks a call", async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      const { store, gets } = counting();
      const files = await gated(gate, store);
      // Two whole chunks and a byte of a third: a range over all three is two calls.
      const bytes = new Uint8Array(2 * CHUNK_BYTES + 1);
      bytes.subarray(0, CHUNK_BYTES).fill(1);
      bytes.subarray(CHUNK_BYTES).fill(2);
      await files.write("/workspace/three.bin", bytes, { expected: null, writer: "owner" });
      const reader = getOrThrow(await new OwnerFilesEnv({ files, rearm })
        .openBinaryReader("three.bin", undefined, context));
      gets.length = 0;
      const span = getOrThrow(await reader.read(0, bytes.byteLength, context));
      expect(sha256Hex(span)).toBe(sha256Hex(bytes));
      expect(gets.map((get) => get.length)).toEqual([2, 1]);
    });
  });

  it("reports the platform's refusal of a call as its own error, loudly", async () => {
    await inGate(ownerObject().stub, async (_instance: OwnerDO, _state, gate) => {
      // What workerd throws once an invocation's subrequests are spent: the error is the
      // platform's, never a budget of the object's.
      const spent = "Too many subrequests by single Worker invocation.";
      const store: ChunkStore = {
        putChunks: async () => { throw new Error(spent); },
        getChunks: async () => { throw new Error(spent); },
        deleteChunks: async () => { throw new Error(spent); },
      };
      const files = await gated(gate, store);
      const fs = new OwnerFilesEnv({ files, rearm });
      const wrote = await fs.writeFile("big.bin", new Uint8Array(INLINE_BYTES + 1), context);
      expect(wrote.ok ? null : [wrote.error.code, wrote.error.message]).toEqual(["unknown", spent]);
      expect(getOrThrow(await fs.exists("big.bin", context))).toBe(false);
    });
  });

  it("keeps a temporary file under /.ta/tmp until cleanup, and refuses writes outside one",
     async () => {
    await inOwner(async (files) => {
      const fs = new OwnerFilesEnv({ files, rearm });
      const file = getOrThrow(await fs.createTempFile({ prefix: "spill-", suffix: ".log" },
                                                      context));
      expect(file).toMatch(/^\/\.ta\/tmp\/tmp-[0-9a-f]{16}\/spill-.*\.log$/);
      getOrThrow(await fs.writeFile(file, "output", context));
      expect(getOrThrow(await fs.readTextFile(file, context))).toBe("output");
      const stray = await fs.writeFile("/.ta/tmp/mine/x.txt", "x", context);
      expect(stray.ok ? null : stray.error.code).toBe("invalid");
      await fs.cleanup(context);
      expect(getOrThrow(await fs.exists(file, context))).toBe(false);
    });
  });
});
