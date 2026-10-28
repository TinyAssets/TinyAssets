/**
 * The seal (research §1.3): a round trip, every AAD field, every changed byte, rotation,
 * and the keyring failing loudly.
 */
import { describe, expect, it } from "vitest";

import { credential } from "../src/credential";
import { Failure } from "../src/failure";
import { binding, open, reseal, seal } from "../src/keyring";
import { ALICE, ANTHROPIC, apiKey, ring, rotate, sealed } from "./support";

async function refused(run: () => Promise<unknown>, code = "credential_refused") {
  const error = await run().then(() => undefined, (caught: unknown) => caught);
  expect(error).toBeInstanceOf(Failure);
  expect((error as Failure).code).toBe(code);
  expect((error as Failure).message).toBe(code);
}

describe("seal and open", () => {
  it("round-trips a credential under its owner, connection, version and kid", async () => {
    const env = ring("k1");
    const plaintext = apiKey("sk-alice", ANTHROPIC);
    const { blob, kid } = await seal(env, ALICE, plaintext);
    expect(kid).toBe("k1");
    expect(blob).toMatch(/^v1\.k1\.[A-Za-z0-9_-]{16}\.[A-Za-z0-9_-]+$/);
    expect(blob).not.toContain("sk-alice");
    const opened = await open(env, ALICE, blob);
    expect(opened.credential).toEqual(credential(plaintext));
    expect(opened.kid).toBe("k1");
    expect(opened.resealed).toBeUndefined();
  });

  it("is exactly the specified construction: HKDF per owner, and the AAD", async () => {
    // Rebuilt here from research §1.3 alone: a blob that opens this way was sealed with
    // the owner's own key and bound to all four fields, in this order and framing.
    const root = new Uint8Array(32).map((_, at) => at + 1);
    const env = ring("k7", ["k7"], { k7: btoa(String.fromCharCode(...root)) });
    const bound = { owner: "Ålice", connection: "c-9", version: 12 };
    const { blob } = await seal(env, bound, apiKey("sk-kat", ANTHROPIC));
    const [, kid, nonce, body] = blob.split(".") as [string, string, string, string];
    const bytes = (text: string) => Uint8Array.from(
      atob(text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4)),
      (char) => char.charCodeAt(0));
    const base = await crypto.subtle.importKey("raw", root, "HKDF", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode("tinyassets/gateway/v1"),
        info: new TextEncoder().encode("cred\0Ålice") },
      base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const additionalData = new TextEncoder().encode("ta-cred/v1\0Ålice\0c-9\0" + "12\0k7");
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: bytes(nonce), additionalData }, key, bytes(body));
    expect(kid).toBe("k7");
    expect(bytes(nonce).byteLength).toBe(12);
    expect(JSON.parse(new TextDecoder().decode(plain))).toMatchObject(
      { kind: "api_key", secret: "sk-kat" });
  });

  it("uses a fresh nonce for every seal", async () => {
    const env = ring("k1");
    const one = await sealed(env, ALICE, "sk-alice", ANTHROPIC);
    const two = await sealed(env, ALICE, "sk-alice", ANTHROPIC);
    expect(one.split(".")[2]).not.toBe(two.split(".")[2]);
  });

  it.each([
    ["another owner", { ...ALICE, owner: "bob" }],
    ["an owner differing by case", { ...ALICE, owner: "Alice" }],
    ["another connection", { ...ALICE, connection: "c2" }],
    ["another version", { ...ALICE, version: 2 }],
  ])("refuses the blob under %s", async (_what, other) => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-alice", ANTHROPIC);
    await refused(() => open(env, other, blob));
  });

  it("refuses the blob relabelled with another kid the ring holds", async () => {
    const env = ring("k1", ["k1", "k2"]);
    const blob = await sealed(env, ALICE, "sk-alice", ANTHROPIC);
    await refused(() => open(env, ALICE, blob.replace(/^v1\.k1\./, "v1.k2.")));
  });

  it("refuses a blob sealed under the same kid name with another root", async () => {
    const blob = await sealed(ring("k1"), ALICE, "sk-alice", ANTHROPIC);
    await refused(() => open(ring("k1"), ALICE, blob));
  });

  it("binds each field so no two bindings share an AAD", async () => {
    // "a\0b" style ambiguity cannot arise: the owner grammar refuses control characters.
    expect(() => binding("alice\u0000c1", "c1", 1)).toThrow("invalid_binding");
    const env = ring("k1");
    const blob = await sealed(env, { owner: "alice", connection: "c10", version: 1 }, "sk", ANTHROPIC);
    await refused(() => open(env, { owner: "alice", connection: "c1", version: 10 }, blob));
  });

  it("refuses every changed byte of the ciphertext, the nonce and the header", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-alice", ANTHROPIC);
    const [, , nonce, body] = blob.split(".") as [string, string, string, string];
    const flip = (text: string, at: number) =>
      text.slice(0, at) + (text[at] === "A" ? "B" : "A") + text.slice(at + 1);
    for (let at = 0; at < body.length - 1; at += 7) {
      await refused(() => open(env, ALICE, `v1.k1.${nonce}.${flip(body, at)}`));
    }
    await refused(() => open(env, ALICE, `v1.k1.${flip(nonce, 3)}.${body}`));
    await refused(() => open(env, ALICE, `v2.k1.${nonce}.${body}`));
    await refused(() => open(env, ALICE, `v1.k1.${nonce}.${body.slice(0, -4)}`));
    await refused(() => open(env, ALICE, `v1.k1.${nonce}.${body}.x`));
    await refused(() => open(env, ALICE, `v1.k1.${nonce}`));
    await refused(() => open(env, ALICE, `v1.k1.${nonce}.${body}=`));
    await refused(() => open(env, ALICE, `v1.k9.${nonce}.${body}`));
    await refused(() => open(env, ALICE, ""));
    await refused(() => open(env, ALICE, 42));
  });

  it("refuses a blob whose plaintext no longer fits the credential shape", async () => {
    // Sealed by hand around the checks, as an older or buggy writer would.
    const env = ring("k1");
    const good = await sealed(env, ALICE, "sk-alice", ANTHROPIC);
    expect((await open(env, ALICE, good)).credential.kind).toBe("api_key");
    await refused(() => seal(env, ALICE, { ...apiKey("sk", ANTHROPIC), kind: "other" } as never));
  });

  it("opens a blob under an older kid and reseals it at the next version", async () => {
    const old = ring("k1", ["k1"]);
    const blob = await sealed(old, ALICE, "sk-alice", ANTHROPIC);
    const rotated = rotate(old, "k2", ["k2"]);
    const opened = await open(rotated, ALICE, blob);
    expect(opened.kid).toBe("k1");
    expect(opened.credential.kind === "api_key" && opened.credential.secret).toBe("sk-alice");
    const resealed = opened.resealed!;
    expect(resealed.kid).toBe("k2");
    expect(resealed.version).toBe(2);
    expect(resealed.blob.startsWith("v1.k2.")).toBe(true);
    const next = await open(rotated, { ...ALICE, version: 2 }, resealed.blob);
    expect(next.kid).toBe("k2");
    expect(next.resealed).toBeUndefined();
    expect(next.credential.kind === "api_key" && next.credential.secret).toBe("sk-alice");
    // The resealed blob is bound to version 2 only, and the old one to version 1 only.
    await refused(() => open(rotated, ALICE, resealed.blob));
    await refused(() => open(rotated, { ...ALICE, version: 2 }, blob));
  });

  it("refuses a blob under a kid the ring has retired", async () => {
    const old = ring("k1");
    const blob = await sealed(old, ALICE, "sk-alice", ANTHROPIC);
    await refused(() => open(ring("k2"), ALICE, blob));
  });

  it("reseals for the next version under the active kid", async () => {
    const env = ring("k2", ["k1", "k2"]);
    const plaintext = apiKey("sk-alice", ANTHROPIC);
    const next = await reseal(env, ALICE, plaintext);
    expect(next).toMatchObject({ kid: "k2", version: 2 });
    expect((await open(env, { ...ALICE, version: 2 }, next.blob)).credential.kind).toBe("api_key");
  });
});

describe("the keyring", () => {
  const key = btoa(String.fromCharCode(...new Uint8Array(32).fill(7)));
  const short = btoa(String.fromCharCode(...new Uint8Array(16).fill(7)));
  it.each([
    ["missing", undefined],
    ["not text", 42],
    ["empty", ""],
    ["not JSON", "{active"],
    ["a list", "[]"],
    ["without keys", JSON.stringify({ active: "k1" })],
    ["without an active kid", JSON.stringify({ keys: { k1: key } })],
    ["with an active kid it does not hold", JSON.stringify({ active: "k2", keys: { k1: key } })],
    ["with no keys", JSON.stringify({ active: "k1", keys: {} })],
    ["with a short key", JSON.stringify({ active: "k1", keys: { k1: short } })],
    ["with a key that is not base64", JSON.stringify({ active: "k1", keys: { k1: "*".repeat(44) } })],
    ["with a kid holding a dot", JSON.stringify({ active: "k.1", keys: { "k.1": key } })],
    ["with an extra field", JSON.stringify({ active: "k1", keys: { k1: key }, note: "x" })],
    ["with keys as a list", JSON.stringify({ active: "k1", keys: [key] })],
  ])("fails loudly when it is %s", async (_what, value) => {
    const env = { GATEWAY_KEYRING: value };
    await refused(() => seal(env, ALICE, apiKey("sk", ANTHROPIC)), "keyring_unavailable");
    await refused(() => open(env, ALICE, "v1.k1.AAAAAAAAAAAAAAAA.AAAAAAAAAAAAAAAAAAAAAA"),
                  "keyring_unavailable");
  });

  it("accepts a well-formed ring", async () => {
    const env = { GATEWAY_KEYRING: JSON.stringify({ active: "k1", keys: { k1: key } }) };
    const blob = (await seal(env, ALICE, apiKey("sk", ANTHROPIC))).blob;
    expect((await open(env, ALICE, blob)).kid).toBe("k1");
  });

  it("is readable only through the env it is given, never process.env", () => {
    expect(globalThis.process?.env?.["GATEWAY_KEYRING"]).toBeUndefined();
  });
});

describe("the binding", () => {
  it.each([
    ["an empty owner", "", "c1", 1],
    ["an owner with surrounding space", " alice", "c1", 1],
    ["an owner with a control character", "al\u0007ice", "c1", 1],
    ["an owner over 512 characters", "a".repeat(513), "c1", 1],
    ["a connection with a dot first", "alice", ".c1", 1],
    ["a connection with a NUL", "alice", "c\u00001", 1],
    ["a version of zero", "alice", "c1", 0],
    ["a fractional version", "alice", "c1", 1.5],
    ["a version that is text", "alice", "c1", "1"],
  ])("refuses %s", (_what, owner, connection, version) => {
    expect(() => binding(owner, connection, version)).toThrow("invalid_binding");
  });

  it("takes any printable owner, as the owner object's grammar does", () => {
    expect(binding("Ålice 🌊", "c1", 3)).toEqual({ owner: "Ålice 🌊", connection: "c1", version: 3 });
  });
});
