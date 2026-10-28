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
import { describe, expect, it } from "vitest";

import {
  BATCH_BYTES as WORKER_BATCH_BYTES, CHUNK_BYTES as WORKER_CHUNK_BYTES,
} from "../../files/src/keys";
import { BATCH_BYTES, CHUNK_BYTES, type ChunkStore, Files, sha256Hex } from "../src/files";
import { ignored, ignoreRules, OwnerFilesEnv } from "../src/files-env";
import type { OwnerDO } from "../src/owner-do";
import { FILE_DEFAULTS } from "../src/policy";
import { ownerObject } from "./support";

const context = BACKGROUND_CONTEXT;

/** A fresh owner's object, and its files, for the length of `use`. */
function inOwner(use: (files: Files) => Promise<void>): Promise<void> {
  return runInDurableObject(ownerObject().stub, (_instance: OwnerDO, state) =>
    use(new Files(state.storage, env.FILES, () => FILE_DEFAULTS)));
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
  withEnv: (use) => inOwner((files) => use(new OwnerFilesEnv({ files }))),
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
      const result = await new OwnerFilesEnv({ files }).exec("true", undefined, context);
      expect(result.ok ? null : result.error.code).toBe("shell_unavailable");
    });
  });

  it("refuses an edit whose file changed between its read and its write", async () => {
    await inOwner(async (files) => {
      const first = new OwnerFilesEnv({ files });
      const second = new OwnerFilesEnv({ files });
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
    await runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
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
      const files = new Files(state.storage, store, () => FILE_DEFAULTS);
      const fs = new OwnerFilesEnv({ files });
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
      const one = new OwnerFilesEnv({ files });
      const other = new OwnerFilesEnv({ files });
      getOrThrow(await other.writeFile("a.txt", "theirs", context));
      getOrThrow(await one.writeFile("a.txt", "mine", context));
      getOrThrow(await one.writeFile("a.txt", "mine again", context));
      getOrThrow(await one.appendFile("a.txt", "!", context));
      expect(getOrThrow(await other.readTextFile("a.txt", context))).toBe("mine again!");
    });
  });

  it("shares one id across environments of one owner, and none with another owner",
     async () => {
    let first = "";
    await inOwner(async (files) => {
      first = new OwnerFilesEnv({ files }).id;
      expect(new OwnerFilesEnv({ files }).id).toBe(first);
      expect(first).toMatch(/^ta-files:[0-9a-f]{32}$/);
      expect(first).not.toContain(files.ns());
    });
    await inOwner(async (files) => {
      expect(new OwnerFilesEnv({ files }).id).not.toBe(first);
    });
  });

  it("reads paths outside the owner's roots as missing and refuses writes to them",
     async () => {
    await inOwner(async (files) => {
      const fs = new OwnerFilesEnv({ files });
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
      const fs = new OwnerFilesEnv({ files });
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
    await runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
      const { store, gets, puts } = counting();
      const files = new Files(state.storage, store, () => FILE_DEFAULTS);
      const fs = new OwnerFilesEnv({ files });
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
    await runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
      const { store, gets } = counting();
      const files = new Files(state.storage, store, () => FILE_DEFAULTS);
      const fs = new OwnerFilesEnv({ files });
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
        expect(read.ok ? null : read.error.code).toBe("invalid");
      }
      const grown = await fs.truncateFile("huge.txt", 2 * size, context);
      expect(grown.ok ? null : grown.error.code).toBe("invalid");
      expect(gets).toEqual([]);
      const reader = getOrThrow(await fs.openBinaryReader("huge.txt", undefined, context));
      const tooMuch = await reader.read(0, size, context);
      expect(tooMuch.ok ? null : tooMuch.error.code).toBe("invalid");
      expect(getOrThrow(await reader.read(CHUNK_BYTES - 1, 2, context))).toEqual(
        new Uint8Array([65, 65]));
      expect(gets).toHaveLength(1);
      gets.length = 0;
      getOrThrow(await reader.scanLines({ startLine: 1 }, context));
      expect(gets.map((call) => call.length)).toEqual([2, 2, 1]);
    });
  }, 30_000);

  it("keeps a temporary file under /.ta/tmp until cleanup, and refuses writes outside one",
     async () => {
    await inOwner(async (files) => {
      const fs = new OwnerFilesEnv({ files });
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
