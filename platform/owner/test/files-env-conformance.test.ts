/**
 * pi-durable 1.4.0's own `ExecutionEnv` conformance suite, run against the owner's
 * environment over the manifest and the real files Worker: every case but `exec`'s,
 * with no symbolic links (both come with the sandbox, tasks 6) and with `listFiles`.
 * Then the implicit compare-and-swap pi's `edit` relies on.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { getOrThrow } from "@earendil-works/pi-durable/env";
import { createEnvConformance, createExpectAssertions } from "@earendil-works/pi-durable/testing";
import { createEditTool } from "@earendil-works/pi-durable/tools";
import { describe, expect, it } from "vitest";

import {
  BATCH_BYTES as WORKER_BATCH_BYTES, CHUNK_BYTES as WORKER_CHUNK_BYTES,
} from "../../files/src/keys";
import {
  Calls, CallScope, CallsSpent, FILES_CALLS, GATEWAY_HOOKS_HEADROOM, INVOCATION_CALLS,
} from "../src/calls";
import { BATCH_BYTES, CHUNK_BYTES, type ChunkStore, Files, INLINE_BYTES, sha256Hex } from "../src/files";
import { ignored, ignoreRules, OwnerFilesEnv } from "../src/files-env";
import type { OwnerDO } from "../src/owner-do";
import { FILE_DEFAULTS } from "../src/policy";
import { filesOn, inScope, lostContent, ownerObject, sqlIn } from "./support";

const context = BACKGROUND_CONTEXT;
/** The schedule's sync, which the object wires in; these cases drive no schedule. */
const rearm = async (): Promise<void> => undefined;

/** A fresh owner's object, and its files, for the length of `use`. */
function inOwner(use: (files: Files) => Promise<void>): Promise<void> {
  return inScope(ownerObject().stub, (_instance: OwnerDO, state, scope) =>
    use(filesOn(state.storage, scope, env.FILES)));
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
    await inScope(ownerObject().stub, async (_instance: OwnerDO, state, scope) => {
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
      const files = filesOn(state.storage, scope, store);
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
    await inScope(ownerObject().stub, async (_instance: OwnerDO, state, scope) => {
      const { store, gets, puts } = counting();
      const files = filesOn(state.storage, scope, store);
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
    await inScope(ownerObject().stub, async (_instance: OwnerDO, state, scope) => {
      const { store, gets } = counting();
      const files = filesOn(state.storage, scope, store);
      const fs = new OwnerFilesEnv({ files, rearm });
      // Five chunks, 16 MiB and one byte, sent by upload as the HTTP door will.
      const piece = new Uint8Array(CHUNK_BYTES).fill(65);
      const last = new Uint8Array([10]);
      const size = 4 * CHUNK_BYTES + 1;
      const begun = files.begin(size, [...Array(4).fill(sha256Hex(piece)), sha256Hex(last)]);
      await files.upload(begun.intent_id, [{ sha256: sha256Hex(piece), bytes: piece },
                                           { sha256: sha256Hex(last), bytes: last }]);
      files.commit(begun.intent_id, "/workspace/huge.txt", { expected: null, writer: "owner" });
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
    await inScope(r.stub, async (_instance: OwnerDO, state, scope) => {
      const files = filesOn(state.storage, scope, env.FILES);
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
    await inScope(r.stub, async (_instance: OwnerDO, state, scope) => {
      const files = filesOn(state.storage, scope, env.FILES);
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

  it("draws every tool call of one invocation from its one budget, refused before a call " +
     "past it", async () => {
    await inScope(ownerObject().stub, async (_instance: OwnerDO, state, scope) => {
      const { store, gets, puts } = counting();
      const files = filesOn(state.storage, scope, store);
      await files.write("/workspace/two.bin", new Uint8Array(2 * CHUNK_BYTES).fill(1),
                        { expected: null, writer: "owner" });
      // One pi turn is one invocation: its tool calls, each its own environment built with
      // no budget of its own, draw on the invocation's one.
      await scope.run(async () => {
        const calls = scope.current();
        const first = new OwnerFilesEnv({ files, rearm });
        const second = new OwnerFilesEnv({ files, rearm });
        gets.length = 0;
        for (let i = 0; i < FILES_CALLS / 2; i++) {
          getOrThrow(await first.readBinaryFile("two.bin", context));
          getOrThrow(await second.readBinaryFile("two.bin", context));
        }
        expect([calls.made, gets.length]).toEqual([FILES_CALLS, FILES_CALLS]);
        const over = await second.readBinaryFile("two.bin", context);
        expect(over.ok ? null : [over.error.code, over.error.message]).toEqual([
          "not_supported",
          `the invocation's call budget is spent: files calls may bring it to ${FILES_CALLS} ` +
          `of the ${INVOCATION_CALLS} service calls one invocation makes, ${FILES_CALLS} are ` +
          "made and 1 more were asked; read a smaller range, or fewer files at once"]);
        expect([calls.made, gets.length]).toEqual([FILES_CALLS, FILES_CALLS]);
        // The gateway and hooks keep their room past what FILES may take.
        calls.draw("gateway", GATEWAY_HOOKS_HEADROOM);
        expect(calls.made).toBe(INVOCATION_CALLS);
        expect(() => calls.draw("hooks", 1)).toThrow(CallsSpent);
      });
      for (const bad of [-1, 0.5]) {
        expect(() => new Calls().draw("files", bad)).toThrow("a draw is a whole number of calls");
      }
      // Another invocation's budget, which its puts draw from; a small file moves no bytes
      // through FILES, so it costs nothing.
      await scope.run(async () => {
        const next = new OwnerFilesEnv({ files, rearm });
        puts.length = 0;
        for (let i = 0; i < FILES_CALLS; i++) {
          getOrThrow(await next.writeFile(`p${i}.bin`, new Uint8Array(INLINE_BYTES + 1).fill(i),
                                          context));
        }
        // Bytes no file holds yet, so the write needs a put of its own.
        const refused = await next.writeFile("one-more.bin",
                                             new Uint8Array(INLINE_BYTES + 1).fill(255), context);
        expect(refused.ok ? null : refused.error.code).toBe("not_supported");
        expect(puts).toHaveLength(FILES_CALLS);
        expect(getOrThrow(await next.exists("one-more.bin", context))).toBe(false);
        getOrThrow(await next.writeFile("small.txt", "small", context));
      });
    });
  });

  it("reads a range across more than two chunks as its calls, two chunks a call", async () => {
    await inScope(ownerObject().stub, async (_instance: OwnerDO, state, scope) => {
      const { store, gets } = counting();
      const files = filesOn(state.storage, scope, store);
      // Two whole chunks and a byte of a third: a range over all three is two calls.
      const bytes = new Uint8Array(2 * CHUNK_BYTES + 1);
      bytes.subarray(0, CHUNK_BYTES).fill(1);
      bytes.subarray(CHUNK_BYTES).fill(2);
      await files.write("/workspace/three.bin", bytes, { expected: null, writer: "owner" });
      await scope.run(async () => {
        const reader = getOrThrow(await new OwnerFilesEnv({ files, rearm })
          .openBinaryReader("three.bin", undefined, context));
        gets.length = 0;
        const span = getOrThrow(await reader.read(0, bytes.byteLength, context));
        expect(sha256Hex(span)).toBe(sha256Hex(bytes));
        expect([scope.current().made, gets.map((get) => get.length)]).toEqual([2, [2, 1]]);
      });
      // Near the budget's end, a range that needs two calls is refused before either.
      await scope.run(async () => {
        const edge = scope.current();
        edge.draw("files", FILES_CALLS - 1);
        const near = getOrThrow(await new OwnerFilesEnv({ files, rearm })
          .openBinaryReader("three.bin", undefined, context));
        gets.length = 0;
        const refused = await near.read(0, bytes.byteLength, context);
        expect(refused.ok ? null : refused.error.code).toBe("not_supported");
        expect([edge.made, gets.length]).toEqual([FILES_CALLS - 1, 0]);
        expect(getOrThrow(await near.read(0, 10, context))).toEqual(bytes.slice(0, 10));
        expect(edge.made).toBe(FILES_CALLS);
      });
    });
  });

  it("gives invocations running at once their own budgets", async () => {
    await inScope(ownerObject().stub, async (_instance: OwnerDO, state, scope) => {
      const { store, gets } = counting();
      const files = filesOn(state.storage, scope, store);
      await files.write("/workspace/one.bin", new Uint8Array(CHUNK_BYTES).fill(1),
                        { expected: null, writer: "owner" });
      // Two invocations interleave at every FILES call; neither draws on the other's count.
      gets.length = 0;
      const runs = await Promise.all([0, 1].map(() => scope.run(async () => {
        const fs = new OwnerFilesEnv({ files, rearm });
        for (let i = 0; i < FILES_CALLS; i++) {
          getOrThrow(await fs.readBinaryFile("one.bin", context));
        }
        return [(await fs.readBinaryFile("one.bin", context)).ok, scope.current().made];
      })));
      expect([runs, gets.length])
        .toEqual([[[false, FILES_CALLS], [false, FILES_CALLS]], 2 * FILES_CALLS]);
    });
  });

  it("refuses a file-store call outside any invocation, loudly, before making it", async () => {
    await runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
      const { store, gets, puts } = counting();
      const files = filesOn(state.storage, new CallScope(), store);
      const outside = "a service-binding call outside any invocation's scope: enter one at " +
        "the door or the alarm";
      await expect(files.write("/workspace/big.bin", new Uint8Array(INLINE_BYTES + 1),
                               { expected: null, writer: "owner" })).rejects.toThrow(outside);
      await expect(files.fetch([{ sha256: "a".repeat(64) }])).rejects.toThrow(outside);
      expect(() => new CallScope().current()).toThrow(outside);
      expect([gets, puts]).toEqual([[], []]);
      // A small write makes no call, so it needs no budget.
      await files.write("/workspace/small.txt", new Uint8Array(3), { expected: null,
                                                                     writer: "owner" });
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
