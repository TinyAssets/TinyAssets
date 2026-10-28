/** The files Worker: verified, create-once chunks under one owner namespace's keys. */
import { createExecutionContext } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import FilesService from "../src/index";
import { BATCH_BYTES, CHUNK_BYTES, chunkKey, DELETE_BATCH } from "../src/keys";

const files = () => new FilesService(createExecutionContext(), env);

function ns(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

async function sha(bytes: Uint8Array): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function failure(call: () => Promise<unknown>): Promise<Error> {
  try {
    await call();
  } catch (error) {
    return error as Error;
  }
  throw new Error("expected the call to fail");
}

const bytes = (text: string) => new TextEncoder().encode(text);

async function put(space: string, ...bodies: Uint8Array[]): Promise<{ created: number }> {
  return files().putChunks(space, await Promise.all(bodies.map(async (body) => ({
    sha256: await sha(body), bytes: body,
  }))));
}

async function keys(space: string): Promise<string[]> {
  return (await env.BLOBS.list({ prefix: `o/${space}/` })).objects.map((o) => o.key).sort();
}

describe("the Worker's main module", () => {
  it("exports only its entrypoint, as a deployed Worker must", async () => {
    expect(Object.keys(await import("../src/index"))).toEqual(["default"]);
  });
});

describe("putChunks", () => {
  it("stores chunks once under o/<ns>/c/<sha256>, and a re-put is a no-op", async () => {
    const space = ns();
    const [one, two] = [bytes("hello chunk"), bytes("second")];
    expect(await put(space, one, two)).toEqual({ created: 2 });
    expect(await put(space, one, two)).toEqual({ created: 0 });
    expect(await put(space, bytes("third"), one)).toEqual({ created: 1 });
    expect(await keys(space)).toEqual([
      chunkKey(space, await sha(one)), chunkKey(space, await sha(two)),
      chunkKey(space, await sha(bytes("third"))),
    ].sort());
  });

  it("refuses bytes that miss their hash, and stores nothing", async () => {
    const space = ns();
    const hash = await sha(bytes("one"));
    expect((await failure(() => files().putChunks(space, [{ sha256: hash,
                                                             bytes: bytes("two") }]))).message)
      .toBe("the chunk's bytes do not match its sha256");
    expect(await keys(space)).toEqual([]);
  });

  it("refuses a re-put whose bytes miss the hash of the chunk already there", async () => {
    const space = ns();
    const body = bytes("kept");
    await put(space, body);
    const hash = await sha(body);
    expect((await failure(() => files().putChunks(space, [{ sha256: hash,
                                                             bytes: bytes("other") }]))).message)
      .toBe("the chunk's bytes do not match its sha256");
    expect(await files().getChunks(space, [{ sha256: hash }])).toEqual([body]);
  });

  it("fails loudly on a hash key holding bytes it did not verify", async () => {
    const space = ns();
    const body = bytes("claimed");
    const hash = await sha(body);
    await env.BLOBS.put(chunkKey(space, hash), bytes("planted!"));
    expect((await failure(() => put(space, body))).message)
      .toBe("a chunk key holds bytes this Worker did not verify");
    // Same bytes and size, but no checksum this Worker had R2 verify: still refused.
    await env.BLOBS.put(chunkKey(space, hash), bytes("claimed"));
    expect((await failure(() => put(space, body))).message)
      .toBe("a chunk key holds bytes this Worker did not verify");
  });

  // Whether R2 checks a put's digest before its precondition is unprobed (tasks 6): a
  // bucket that reports "exists" first must still never vouch for the wrong bytes.
  const existing = (size: number, sha256: string): R2Bucket => ({
    put: async () => null,
    head: async () => ({ size, checksums: { sha256: Uint8Array.from(
      sha256.match(/../g)!.map((pair) => parseInt(pair, 16))).buffer } }),
  }) as unknown as R2Bucket;

  it("refuses bytes that miss their hash even when the bucket answers before checking", async () => {
    const body = bytes("true bytes");
    const hash = await sha(body);
    const lying = new FilesService(createExecutionContext(), { BLOBS: existing(body.length, hash) });
    expect((await failure(() => lying.putChunks(ns(), [{ sha256: hash,
                                                         bytes: bytes("wrong byte") }]))).message)
      .toBe("the chunk's bytes do not match its sha256");
    expect(await lying.putChunks(ns(), [{ sha256: hash, bytes: body }])).toEqual({ created: 0 });
  });

  it("fails loudly when the key's object is another size than the chunk", async () => {
    const body = bytes("sized");
    const hash = await sha(body);
    const odd = new FilesService(createExecutionContext(), { BLOBS: existing(body.length + 1, hash) });
    expect((await failure(() => odd.putChunks(ns(), [{ sha256: hash, bytes: body }]))).message)
      .toBe("a chunk key holds bytes this Worker did not verify");
  });

  it.each([
    ["an empty chunk", new Uint8Array(0)],
    ["a chunk past 4 MiB", new Uint8Array(CHUNK_BYTES + 1)],
    ["a string", "text"],
    ["an array of numbers", [1, 2, 3]],
  ])("refuses %s", async (_what, body) => {
    expect((await failure(() => files().putChunks(ns(), [{ sha256: "0".repeat(64),
                                                            bytes: body as Uint8Array }])))
      .message).toBe(`a chunk is 1 to ${CHUNK_BYTES} bytes`);
  });

  it.each([
    ["no chunks", []],
    ["not a list", { sha256: "0".repeat(64), bytes: bytes("x") }],
  ])("refuses a put of %s", async (_what, chunks) => {
    expect((await failure(() => files().putChunks(ns(), chunks as never))).message)
      .toBe("a put is a list of at least one chunk");
  });

  it("refuses a put repeating a chunk, or past the batch's bytes, before storing any", async () => {
    const space = ns();
    const body = bytes("twice");
    const chunk = { sha256: await sha(body), bytes: body };
    expect((await failure(() => files().putChunks(space, [chunk, chunk]))).message)
      .toMatch(/^a put names distinct chunks/);
    const big = Array.from({ length: BATCH_BYTES / CHUNK_BYTES + 1 },
                           (_, i) => new Uint8Array(CHUNK_BYTES).fill(i));
    expect((await failure(() => put(space, ...big))).message).toMatch(/^a put names distinct/);
    expect(await keys(space)).toEqual([]);
  });

  it("takes a full batch of 4 MiB chunks", async () => {
    const full = Array.from({ length: BATCH_BYTES / CHUNK_BYTES },
                            (_, i) => new Uint8Array(CHUNK_BYTES).fill(i + 1));
    expect(await put(ns(), ...full)).toEqual({ created: full.length });
  });
});

describe("keys", () => {
  it.each([
    ["an upper-case namespace", "A".repeat(32), "a".repeat(64)],
    ["a short namespace", "a".repeat(31), "a".repeat(64)],
    ["a long namespace", "a".repeat(33), "a".repeat(64)],
    ["a namespace with a path after it", `${"a".repeat(32)}/../b`, "a".repeat(64)],
    ["a hash with a path after it", "a".repeat(32), `${"a".repeat(64)}/x`],
    ["a namespace with a slash", `${"a".repeat(15)}/${"a".repeat(16)}`, "a".repeat(64)],
    ["a path as the namespace", "../../etc/passwd", "a".repeat(64)],
    ["an upper-case hash", "a".repeat(32), "A".repeat(64)],
    ["a long hash", "a".repeat(32), "a".repeat(65)],
    ["a path as the hash", "a".repeat(32), "workspace/AGENTS.md"],
    ["a number", 7, "a".repeat(64)],
  ])("refuses %s", async (_what, space, hash) => {
    const error = await failure(() => files().putChunks(space as string,
                                                        [{ sha256: hash, bytes: bytes("x") }]));
    expect(error.message).toMatch(/^a (namespace|chunk hash) is \d+ lowercase hex characters$/);
    expect(() => chunkKey(space, hash)).toThrow(error.message);
    for (const call of [() => files().getChunks(space as string, [{ sha256: hash }]),
                        () => files().deleteChunks(space as string, [hash])]) {
      expect((await failure(call)).message).toBe(error.message);
    }
  });

  it("builds o/<ns>/c/<sha256> and nothing else", () => {
    expect(chunkKey("0123456789abcdef0123456789abcdef", "f".repeat(64)))
      .toBe(`o/0123456789abcdef0123456789abcdef/c/${"f".repeat(64)}`);
  });
});

describe("getChunks", () => {
  it("reads chunks whole and by range, in order, and only under their own namespace", async () => {
    const space = ns();
    const [digits, letters] = [bytes("0123456789"), bytes("abcdef")];
    await put(space, digits, letters);
    const [d, l] = [await sha(digits), await sha(letters)];
    expect(await files().getChunks(space, [{ sha256: l }, { sha256: d }]))
      .toEqual([letters, digits]);
    expect(await files().getChunks(space, [{ sha256: d, offset: 2, length: 3 },
                                           { sha256: d, offset: 8, length: 100 },
                                           { sha256: l, offset: 0, length: 1 }]))
      .toEqual([bytes("234"), bytes("89"), bytes("a")]);
    expect((await failure(() => files().getChunks(ns(), [{ sha256: d }]))).message)
      .toBe("a chunk the manifest holds is missing from the bucket");
  });

  it.each([
    [{ offset: -1, length: 1 }], [{ offset: 0, length: 0 }], [{ offset: 0.5, length: 1 }],
    [{ offset: 0, length: CHUNK_BYTES + 1 }], [{ offset: CHUNK_BYTES, length: 1 }],
    [{ offset: 0 }], [{ length: 4 }], [{ offset: "0", length: 1 }],
  ])("refuses the range %j", async (at) => {
    expect((await failure(() => files().getChunks(ns(), [{ sha256: "a".repeat(64), ...at }] as
                                                  never))).message)
      .toBe("a chunk range is an offset and a length inside one chunk");
  });

  it("refuses a get of no chunks, or of more bytes than a batch", async () => {
    expect((await failure(() => files().getChunks(ns(), []))).message)
      .toBe("a get is a list of at least one chunk");
    const whole = Array.from({ length: BATCH_BYTES / CHUNK_BYTES + 1 },
                             () => ({ sha256: "a".repeat(64) }));
    expect((await failure(() => files().getChunks(ns(), whole))).message)
      .toBe(`a get moves at most ${BATCH_BYTES} bytes`);
  });

  it("fails loudly when a chunk's bytes no longer match its hash", async () => {
    const space = ns();
    const body = bytes("original");
    await put(space, body);
    const hash = await sha(body);
    await env.BLOBS.put(chunkKey(space, hash), bytes("corrupted"));
    expect((await failure(() => files().getChunks(space, [{ sha256: hash }]))).message)
      .toBe("a chunk's bytes no longer match its hash");
  });
});

describe("deleteChunks", () => {
  it("deletes only the named chunks of the named namespace", async () => {
    const [mine, theirs] = [ns(), ns()];
    const kept = bytes("kept");
    const gone = bytes("gone");
    for (const space of [mine, theirs]) {
      await put(space, kept, gone);
    }
    expect(await files().deleteChunks(mine, [await sha(gone), "e".repeat(64)]))
      .toEqual({ deleted: 2 });
    expect(await keys(mine)).toEqual([chunkKey(mine, await sha(kept))]);
    expect(await keys(theirs)).toHaveLength(2);
  });

  it.each([
    ["no chunks", []],
    ["too many chunks", Array.from({ length: DELETE_BATCH + 1 },
                                    (_, i) => i.toString(16).padStart(64, "0"))],
    ["a repeated chunk", ["a".repeat(64), "a".repeat(64)]],
    ["not a list", "a".repeat(64)],
  ])("refuses %s", async (_what, names) => {
    expect((await failure(() => files().deleteChunks(ns(), names as string[]))).message)
      .toBe(`a delete names 1 to ${DELETE_BATCH} distinct chunks`);
  });

  it("refuses a bad hash in the list before deleting any", async () => {
    const space = ns();
    const body = bytes("stay");
    const hash = await sha(body);
    await put(space, body);
    expect((await failure(() => files().deleteChunks(space, [hash, "nope"]))).message)
      .toBe("a chunk hash is 64 lowercase hex characters");
    expect(await keys(space)).toHaveLength(1);
  });

  it("takes 1,000 chunks in one call", async () => {
    const names = Array.from({ length: DELETE_BATCH }, (_, i) => i.toString(16).padStart(64, "0"));
    expect(await files().deleteChunks(ns(), names)).toEqual({ deleted: DELETE_BATCH });
  });
});
