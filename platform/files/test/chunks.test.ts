/**
 * The files Worker: verified, create-once chunks under one owner namespace's keys, each put
 * and read under that owner's data key (SSE-C, held to R2's documented answers by
 * `ssec.ts`, since miniflare ignores the key).
 */
import { createExecutionContext } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { KEY_REFUSED, KEYRING_UNAVAILABLE } from "../src/datakey";
import FilesService from "../src/index";
import { BATCH_BYTES, CHUNK_BYTES, chunkKey, DELETE_BATCH, manifestKey } from "../src/keys";

import { type Presented, ssec } from "./ssec";

function ringKey(): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
}

/** The keyring, rotated: `k1` an older kid, `k2` the active one. */
const OLD = JSON.stringify({ active: "k1", keys: { k1: ringKey() } });
const RING = JSON.stringify({ active: "k2", keys: { ...JSON.parse(OLD).keys, k2: ringKey() } });
const seen: Presented[] = [];

const service = (bucket: R2Bucket = ssec(env.BLOBS, seen), ring: unknown = RING) =>
  new FilesService(createExecutionContext(), { BLOBS: bucket, FILES_KEYRING: ring });

const KEYS = new Map<string, Promise<string>>();
/** A namespace's one data key, minted on first use; a malformed namespace gets none. */
function keyOf(space: unknown): Promise<string> {
  if (typeof space !== "string" || !/^[0-9a-f]{32}$/.test(space)) {
    return Promise.resolve("none");
  }
  if (!KEYS.has(space)) {
    KEYS.set(space, service().mintKey(space).then(({ key }) => key));
  }
  return KEYS.get(space)!;
}

/** The Worker, each call under its namespace's own key, as the owner's object calls it. */
const files = () => ({
  putChunks: async (space: string, chunks: Parameters<FilesService["putChunks"]>[2]) =>
    service().putChunks(space, await keyOf(space), chunks),
  getChunks: async (space: string, reads: Parameters<FilesService["getChunks"]>[2]) =>
    service().getChunks(space, await keyOf(space), reads),
  deleteChunks: (space: string, names: string[]) => service().deleteChunks(space, names),
});

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
  const existing = (stored: Uint8Array): R2Bucket => ({
    put: async () => null,
    get: async () => ({ arrayBuffer: async () => stored.slice().buffer }),
  }) as unknown as R2Bucket;

  it("refuses bytes that miss their hash even when the bucket answers before checking", async () => {
    const body = bytes("true bytes");
    const hash = await sha(body);
    const space = ns();
    const lying = service(existing(body));
    expect((await failure(async () => lying.putChunks(space, await keyOf(space), [{
      sha256: hash, bytes: bytes("wrong byte") }]))).message)
      .toBe("the chunk's bytes do not match its sha256");
    expect(await lying.putChunks(space, await keyOf(space), [{ sha256: hash, bytes: body }]))
      .toEqual({ created: 0 });
  });

  it("fails loudly when the key's object is another size than the chunk", async () => {
    const body = bytes("sized");
    const hash = await sha(body);
    const space = ns();
    const odd = service(existing(bytes("sized!")));
    expect((await failure(async () => odd.putChunks(space, await keyOf(space), [{
      sha256: hash, bytes: body }]))).message)
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

  it("takes a full batch of two 4 MiB chunks", async () => {
    expect([CHUNK_BYTES, BATCH_BYTES / CHUNK_BYTES]).toEqual([4 * 1024 * 1024, 2]);
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
    const head = await env.BLOBS.head(chunkKey(space, hash));
    // The same key's mark kept: the bytes, not the key, are what changed.
    await env.BLOBS.put(chunkKey(space, hash), bytes("corrupted"),
                        { customMetadata: head!.customMetadata! });
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

describe("data keys (SSE-C)", () => {
  it("puts and reads every chunk under its owner's key, and refuses a call with none", async () => {
    const space = ns();
    const body = bytes("keyed");
    const hash = await sha(body);
    const from = seen.length;
    await put(space, body);
    expect(await files().getChunks(space, [{ sha256: hash }, { sha256: hash, offset: 1,
                                                                length: 2 }]))
      .toEqual([body, bytes("ey")]);
    const calls = seen.slice(from);
    expect(calls.map((call) => call.op)).toEqual(["put", "get", "get"]);
    expect(new Set(calls.map((call) => call.digest)).size).toBe(1);
    expect(calls[0]!.digest).toMatch(/^[0-9a-f]{64}$/);
    for (const key of [undefined, "", "v1.k2.AAAA.AAAA", 7]) {
      expect((await failure(() => service().getChunks(space, key as string,
                                                      [{ sha256: hash }]))).message)
        .toBe(KEY_REFUSED);
      expect((await failure(() => service().putChunks(space, key as string,
                                                      [{ sha256: hash, bytes: body }]))).message)
        .toBe(KEY_REFUSED);
    }
  });

  it("gives every namespace its own key, which no other namespace's key opens", async () => {
    const [mine, theirs] = [ns(), ns()];
    const body = bytes("mine alone");
    const hash = await sha(body);
    await put(mine, body);
    expect(await keyOf(mine)).not.toBe(await keyOf(theirs));
    // Their wrapped key, presented for my namespace, does not open: its tag binds theirs.
    expect((await failure(async () => service().getChunks(mine, await keyOf(theirs),
                                                          [{ sha256: hash }]))).message)
      .toBe(KEY_REFUSED);
    // A second key minted for my very namespace opens, but R2 refuses it the chunk.
    const second = (await service().mintKey(mine)).key;
    expect((await failure(() => service().getChunks(mine, second, [{ sha256: hash }]))).message)
      .toMatch(/^SSE-C/);
    expect(await files().getChunks(mine, [{ sha256: hash }])).toEqual([body]);
  });

  it("keeps no plaintext key: a wrapped key is AES-GCM under the ring, and a changed byte fails", async () => {
    const space = ns();
    const key = await keyOf(space);
    expect(key).toMatch(/^v1\.k2\.[A-Za-z0-9_-]{16}\.[A-Za-z0-9_-]{64}$/);
    const flipped = key.slice(0, -2) + (key.at(-2) === "A" ? "B" : "A") + key.at(-1)!;
    expect((await failure(() => service().getChunks(space, flipped, [{ sha256: "a".repeat(64) }])))
      .message).toBe(KEY_REFUSED);
  });

  it("opens a key wrapped under an older kid, and none once its kid leaves the ring", async () => {
    const space = ns();
    const old = (await service(ssec(env.BLOBS), OLD).mintKey(space)).key;
    expect(old.startsWith("v1.k1.")).toBe(true);
    const body = bytes("older");
    await service().putChunks(space, old, [{ sha256: await sha(body), bytes: body }]);
    expect(await service().getChunks(space, old, [{ sha256: await sha(body) }])).toEqual([body]);
    const without = JSON.stringify({ active: "k2", keys: { k2: JSON.parse(RING).keys.k2 } });
    expect((await failure(() => service(ssec(env.BLOBS), without)
      .getChunks(space, old, [{ sha256: "a".repeat(64) }]))).message).toBe(KEY_REFUSED);
  });

  it.each([
    ["no ring", null],
    ["text", "not json"],
    ["an active kid the ring lacks", JSON.stringify({ active: "k9", keys: { k1: ringKey() } })],
    ["a short key", JSON.stringify({ active: "k1", keys: { k1: btoa("short") } })],
    ["an extra field", JSON.stringify({ active: "k1", keys: { k1: ringKey() }, x: 1 })],
  ])("fails every keyed call loudly with %s", async (_what, ring) => {
    const space = ns();
    expect((await failure(() => service(ssec(env.BLOBS), ring).mintKey(space))).message)
      .toBe(KEYRING_UNAVAILABLE);
    expect((await failure(async () => service(ssec(env.BLOBS), ring).getChunks(
      space, await keyOf(space), [{ sha256: "a".repeat(64) }]))).message)
      .toBe(KEYRING_UNAVAILABLE);
  });
});

describe("manifests", () => {
  it("writes parts under the owner's key and a root naming them, read back and deleted", async () => {
    const space = ns();
    const key = await keyOf(space);
    const parts = [bytes("first part"), bytes("second")];
    const written: { sha256: string; size: number }[] = [];
    for (const [part, body] of parts.entries()) {
      written.push(await service().putManifestPart(space, key, "20261103", part, body));
    }
    expect(written).toEqual([{ sha256: await sha(parts[0]!), size: 10 },
                             { sha256: await sha(parts[1]!), size: 6 }]);
    expect(await service().getManifestRoot(space, "20261103")).toBeNull();
    await service().putManifestRoot(space, key, "20261103", written);
    const root = await service().getManifestRoot(space, "20261103");
    expect(root).toMatchObject({ format: "tinyassets-manifest", version: 1, day: "20261103",
                                 key, parts: written });
    // A recovery needs only the ring: the root's own key opens its parts.
    expect(await service().getManifestPart(space, root!.key, "20261103", 1,
                                           root!.parts[1]!.sha256)).toEqual(parts[1]);
    const second = (await service().mintKey(space)).key;
    expect((await failure(() => service().getManifestPart(space, second, "20261103", 0,
                                                          written[0]!.sha256))).message)
      .toMatch(/^SSE-C/);
    await service().deleteManifest(space, "20261103", 2);
    expect(await keys(space)).toEqual([]);
    expect(manifestKey(space, "20261103", 3)).toBe(`o/${space}/m/20261103/3`);
  });

  it.each([["a day", "2026-11-03", 0], ["a part", "20261103", -1],
           ["a part", "20261103", 4096], ["a day", "20261303", 0]])(
    "refuses %s out of shape", async (_what, day, part) => {
      const space = ns();
      expect((await failure(async () => service().putManifestPart(
        space, await keyOf(space), day as string, part as number, bytes("x")))).message)
        .toMatch(/^a manifest/);
    });
});
