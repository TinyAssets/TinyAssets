/**
 * The sealed envelope (`src/envelope.ts`): the ring, the codec, the construction rebuilt by
 * hand from its documented form, domain separation, and rotation's cases. The blobs main
 * sealed, and main's own code opening today's, are each binding's to prove
 * (`gateway/guards/keyring-main.test.ts`, `files/guards/datakey-main.test.ts`).
 */
import { describe, expect, it } from "vitest";

import {
  base64, base64url, type Domain, fromBase64, hex, KID, openBlob, parseBlob, parseRing,
  type Ring, ringText, ringWith, sealBlob,
} from "../src/envelope";

const key = (fill: number) => new Uint8Array(32).fill(fill);
const ring = (active: string, kids: readonly string[] = [active], fill = 1): Ring =>
  ({ active, keys: new Map(kids.map((kid, at) => [kid, key(fill + at)])) });
const DOMAIN: Domain = { salt: "tinyassets/test/v1", info: "use\0one", context: "ctx/v1\0one" };
const plain = new TextEncoder().encode("the plaintext");
const text = (opened: { plain: Uint8Array } | null) =>
  opened === null ? null : new TextDecoder().decode(opened.plain);

/** `blob` re-encoded from its own bytes with `edit` applied: a blob as a writer could make it. */
function reencoded(blob: string, edit: (parts: { nonce: Uint8Array; sealed: Uint8Array }) =>
  { nonce: Uint8Array; sealed: Uint8Array }, version = "v1"): string {
  const parts = parseBlob(blob, 1024)!;
  const { nonce, sealed } = edit({ nonce: parts.nonce, sealed: parts.sealed });
  return `${version}.${parts.kid}.${base64url(nonce)}.${base64url(sealed)}`;
}

describe("the ring", () => {
  const good = base64(key(7));
  const short = base64(new Uint8Array(16).fill(7));
  it.each([
    ["missing", undefined],
    ["not text", 42],
    ["empty", ""],
    ["not JSON", "{active"],
    ["a list", "[]"],
    ["null", "null"],
    ["without keys", JSON.stringify({ active: "k1" })],
    ["without an active kid", JSON.stringify({ keys: { k1: good } })],
    ["with an active kid that is not text", JSON.stringify({ active: 1, keys: { 1: good } })],
    ["with an active kid it does not hold", JSON.stringify({ active: "k2", keys: { k1: good } })],
    ["with no keys", JSON.stringify({ active: "k1", keys: {} })],
    ["with a short key", JSON.stringify({ active: "k1", keys: { k1: short } })],
    ["with a key that is not base64", JSON.stringify({ active: "k1", keys: { k1: "*".repeat(44) } })],
    ["with an unpadded key", JSON.stringify({ active: "k1", keys: { k1: good.slice(0, -1) } })],
    ["with a key that is not text", JSON.stringify({ active: "k1", keys: { k1: 7 } })],
    ["with a kid holding a dot", JSON.stringify({ active: "k.1", keys: { "k.1": good } })],
    ["with a kid over 32 characters", JSON.stringify({ active: "k".repeat(33),
                                                       keys: { ["k".repeat(33)]: good } })],
    ["with an extra field", JSON.stringify({ active: "k1", keys: { k1: good }, note: "x" })],
    ["with keys as a list", JSON.stringify({ active: "k1", keys: [good] })],
    ["with 65 kids", JSON.stringify({ active: "k1", keys: Object.fromEntries(
      Array.from({ length: 65 }, (_, n) => [`k${n + 1}`, good])) })],
    ["over 5 KiB", JSON.stringify({ active: "k1", keys: { k1: good } }, null, 2)
      .replace("{", `{${" ".repeat(5 * 1024)}`)],
  ])("is no ring when it is %s", (_what, raw) => {
    expect(parseRing(raw)).toBeNull();
  });

  it("reads each kid's key, in the ring's order, the active one among them", () => {
    const raw = JSON.stringify({ active: "k2", keys: { k1: base64(key(1)), k2: base64(key(2)) } });
    const read = parseRing(raw)!;
    expect(read.active).toBe("k2");
    expect([...read.keys.keys()]).toEqual(["k1", "k2"]);
    expect(read.keys.get("k1")).toEqual(key(1));
    expect(read.keys.get("k2")).toEqual(key(2));
    const most = JSON.stringify({ active: "k64", keys: Object.fromEntries(
      Array.from({ length: 64 }, (_, n) => [`k${n + 1}`, good])) });
    expect(parseRing(most)!.keys.size).toBe(64);
  });

  it("is written as its secret's text, which reads back the same; a ring out of shape is not",
     () => {
    const rotated = ring("k2", ["k1", "k2"]);
    expect(ringText(rotated)).toBe(JSON.stringify(
      { active: "k2", keys: { k1: base64(key(1)), k2: base64(key(2)) } }));
    expect(parseRing(ringText(rotated))).toEqual(rotated);
    expect(() => ringText({ active: "k9", keys: rotated.keys })).toThrow(/active kid/);
    expect(() => ringText({ active: "k1", keys: new Map([["k1", new Uint8Array(16)]]) }))
      .toThrow(/32-byte key/);
    expect(() => ringText({ active: "k.1", keys: new Map([["k.1", key(1)]]) })).toThrow();
  });

  it("takes a new key under the kid after its highest, made active, keeping every old key",
     () => {
    const first = ringWith(ring("k1"), key(9));
    expect(first.active).toBe("k2");
    expect([...first.keys]).toEqual([["k1", key(1)], ["k2", key(9)]]);
    // k1 retired: the ring holds k2 and k3, so the next kid is k4, never k3 again.
    const retired = ringWith(ring("k3", ["k2", "k3"]), key(9));
    expect(retired.active).toBe("k4");
    expect([...retired.keys]).toEqual([["k2", key(1)], ["k3", key(2)], ["k4", key(9)]]);
    // k1 and k2 retired: the next is k4, never a retired kid's name (k2) again.
    expect(ringWith(ring("k3"), key(9)).active).toBe("k4");
    expect(ringWith(ring("k10"), key(9)).active).toBe("k11");
    expect(ringWith(ring("k2", ["k10", "k2"]), key(9)).active).toBe("k11");
    // Kids it does not number are kept, and a ring of only those starts at k1.
    expect(ringWith(ring("a", ["a"]), key(9)).active).toBe("k1");
    expect(ringWith(ring("k2", ["kx", "k2", "k-3"]), key(9)).active).toBe("k3");
    // Past a safe integer, still one more than the highest, so never a held name.
    const big = `k${"9".repeat(20)}`;
    expect(ringWith(ring(big), key(9)).active).toBe(`k1${"0".repeat(20)}`);
  });
});

describe("the codec", () => {
  it("is v1, the kid, a 12-byte nonce and the ciphertext with its 16-byte tag, base64url",
     async () => {
    const blob = await sealBlob(ring("k1"), DOMAIN, plain);
    expect(blob).toMatch(/^v1\.k1\.[A-Za-z0-9_-]{16}\.[A-Za-z0-9_-]+$/);
    const parts = parseBlob(blob, 1024)!;
    expect(parts.kid).toBe("k1");
    expect(parts.nonce.byteLength).toBe(12);
    expect(parts.sealed.byteLength).toBe(plain.byteLength + 16);
  });

  it("refuses every other version, shape and size", async () => {
    const keys = ring("k1");
    const blob = await sealBlob(keys, DOMAIN, plain);
    const [, , nonce, body] = blob.split(".") as [string, string, string, string];
    for (const other of [
      `v2.k1.${nonce}.${body}`, `V1.k1.${nonce}.${body}`, `v0.k1.${nonce}.${body}`,
      `.k1.${nonce}.${body}`, `v1.k1.${nonce}`, `v1.k1.${nonce}.${body}.x`,
      `v1.k.1.${nonce}.${body}`, `v1..${nonce}.${body}`, `v1.${"k".repeat(33)}.${nonce}.${body}`,
      `v1.k1.${nonce}.${body}=`, `v1.k1.${nonce}=.${body}`, `v1.k1.${nonce}.${body}+`,
      `v1.k1.${nonce}.AAAAA`, `v1.k1.${nonce}.AAAA`, `v1.k1.AAAAA.${body}`,
      `v1.k1..${body}`, `v1.k1.${nonce}.`, "", "v1", 42, null, undefined,
    ]) {
      expect(parseBlob(other, 1024), String(other)).toBeNull();
      expect(await openBlob(keys, DOMAIN, other, 1024), String(other)).toBeNull();
    }
    expect(parseBlob(blob, blob.length)).not.toBeNull();
    expect(parseBlob(blob, blob.length - 1)).toBeNull();
    expect(await openBlob(keys, DOMAIN, blob, blob.length - 1)).toBeNull();
  });

  it("refuses a nonce of any other length, and a ciphertext shorter than its tag", async () => {
    const keys = ring("k1");
    const blob = await sealBlob(keys, DOMAIN, plain);
    for (const size of [8, 11, 13, 16]) {
      const other = reencoded(blob, ({ sealed }) => ({ nonce: new Uint8Array(size), sealed }));
      expect(parseBlob(other, 1024), `${size}`).toBeNull();
    }
    const short = reencoded(blob, ({ nonce }) => ({ nonce, sealed: new Uint8Array(15) }));
    expect(parseBlob(short, 1024)).toBeNull();
    expect(parseBlob(reencoded(blob, ({ nonce }) => ({ nonce, sealed: new Uint8Array(16) })),
                     1024)).not.toBeNull();
  });

  it("encodes base64, base64url and hex as RFC 4648 and the hex alphabet do", () => {
    const foobar = new TextEncoder().encode("foobar");
    expect(["", "f", "fo", "foo", "foob", "fooba", "foobar"]
      .map((prefix) => base64(foobar.slice(0, prefix.length))))
      .toEqual(["", "Zg==", "Zm8=", "Zm9v", "Zm9vYg==", "Zm9vYmE=", "Zm9vYmFy"]);
    expect(fromBase64("Zm9vYmE=")).toEqual(foobar.slice(0, 5));
    expect(() => fromBase64("*")).toThrow();
    expect(base64url(new Uint8Array([0xfb, 0xff, 0xbf]))).toBe("-_-_");
    expect(base64url(new Uint8Array([0xfb]))).toBe("-w");
    expect(hex(new Uint8Array([0, 1, 0xab, 0xff]))).toBe("0001abff");
    expect(KID.test("k_-1")).toBe(true);
    // KID's literal bound is KID_MAX, which blobMax's longest kid reads: 32, not 33.
    expect(KID.test("k".repeat(32))).toBe(true);
    expect(KID.test("k".repeat(33))).toBe(false);
  });
});

describe("the seal", () => {
  it("is exactly the documented construction: HKDF of the kid's key, and the AAD", async () => {
    // Rebuilt here from the module's documented form alone.
    const root = new Uint8Array(32).map((_, at) => at + 1);
    const keys: Ring = { active: "k7", keys: new Map([["k7", root]]) };
    const blob = await sealBlob(keys, DOMAIN, plain);
    const parts = parseBlob(blob, 1024)!;
    const base = await crypto.subtle.importKey("raw", root, "HKDF", false, ["deriveKey"]);
    const derived = await crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode("tinyassets/test/v1"),
        info: new TextEncoder().encode("use\0one") },
      base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const opened = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: parts.nonce,
        additionalData: new TextEncoder().encode("ctx/v1\0one\0k7") }, derived, parts.sealed);
    expect(new TextDecoder().decode(opened)).toBe("the plaintext");
  });

  it("round-trips under the active kid, with a fresh nonce every seal", async () => {
    const keys = ring("k2", ["k1", "k2"]);
    const one = await sealBlob(keys, DOMAIN, plain);
    const two = await sealBlob(keys, DOMAIN, plain);
    expect(one.startsWith("v1.k2.")).toBe(true);
    expect(one.split(".")[2]).not.toBe(two.split(".")[2]);
    expect(await openBlob(keys, DOMAIN, one, 1024)).toEqual({ plain, kid: "k2" });
  });

  it.each([
    ["the salt", { ...DOMAIN, salt: "tinyassets/other/v1" }],
    ["the info", { ...DOMAIN, info: "use\0two" }],
    ["the context", { ...DOMAIN, context: "ctx/v1\0two" }],
    ["a context differing in its last byte", { ...DOMAIN, context: "ctx/v1\0onf" }],
  ])("refuses a blob opened under another domain: %s", async (_what, other) => {
    const keys = ring("k1");
    const blob = await sealBlob(keys, DOMAIN, plain);
    expect(await openBlob(keys, other, blob, 1024)).toBeNull();
    expect(await openBlob(keys, DOMAIN, await sealBlob(keys, other, plain), 1024)).toBeNull();
  });

  it("refuses every changed byte of the ciphertext and the nonce", async () => {
    const keys = ring("k1");
    const blob = await sealBlob(keys, DOMAIN, plain);
    const parts = parseBlob(blob, 1024)!;
    for (const [field, size] of [["nonce", 12], ["sealed", parts.sealed.byteLength]] as const) {
      for (let at = 0; at < size; at++) {
        const flipped = reencoded(blob, (both) => {
          const copy = { nonce: both.nonce.slice(), sealed: both.sealed.slice() };
          copy[field][at]! ^= 1;
          return copy;
        });
        expect(await openBlob(keys, DOMAIN, flipped, 1024), `${field} ${at}`).toBeNull();
      }
    }
  });
});

describe("rotation", () => {
  it("opens a blob under every kid its ring holds, and says which", async () => {
    const old = ring("k1", ["k1"]);
    const rotated: Ring = { active: "k2", keys: new Map([...old.keys, ["k2", key(5)]]) };
    const under1 = await sealBlob(old, DOMAIN, plain);
    const under2 = await sealBlob(rotated, DOMAIN, plain);
    expect(await openBlob(rotated, DOMAIN, under1, 1024)).toEqual({ plain, kid: "k1" });
    expect(await openBlob(rotated, DOMAIN, under2, 1024)).toEqual({ plain, kid: "k2" });
    // The old ring never held k2.
    expect(await openBlob(old, DOMAIN, under2, 1024)).toBeNull();
  });

  it("refuses a blob under a kid the ring has retired, and one relabelled to another kid",
     async () => {
    const rotated = ring("k2", ["k1", "k2"]);
    const under1 = await sealBlob({ ...rotated, active: "k1" }, DOMAIN, plain);
    const retired: Ring = { active: "k2", keys: new Map([["k2", rotated.keys.get("k2")!]]) };
    expect(await openBlob(retired, DOMAIN, under1, 1024)).toBeNull();
    expect(await openBlob(rotated, DOMAIN, under1.replace(/^v1\.k1\./, "v1.k2."), 1024))
      .toBeNull();
    // A kid of the same name under another key opens nothing either.
    expect(await openBlob(ring("k1", ["k1"], 9), DOMAIN, under1, 1024)).toBeNull();
  });
});
