/**
 * Every data key the files Worker ever wrapped stays readable, and every key it wraps now
 * opens on the code that wrapped before (untangle A5): the data keys' ring, a binding over
 * the one sealed envelope (`contract/src/envelope.ts`), against main's own copy of the
 * scheme as it stood at `ci/keyring-vectors.json`'s commit, the last before the envelope,
 * read from git and run as it was.
 *
 * - Main's golden wrapped keys, checked in, open under today's code, and rewrap.
 * - Keys wrapped today open on main's code, and main's fresh keys on today's.
 * - Every malformed ring, every tampered or relabelled key, every retired kid and every
 *   blob of this domain over a plaintext that is not 32 bytes is answered with main's own
 *   refusal, by `unwrap`, `rewrap` and `mint` alike.
 * - No blob the gateway sealed opens here as a data key, under the very same ring, even
 *   one of a data key's size sealed for this namespace as its owner.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { type Tree, treeAt } from "../../ci/history";
import VECTORS from "../../ci/keyring-vectors.json" with { type: "json" };
import { hex, parseRing, sealBlob } from "../../contract/src/envelope";
import * as datakey from "../src/datakey";

type Datakey = typeof datakey;

const { k1, k2 } = VECTORS.keys;
const OLD = JSON.stringify({ active: "k1", keys: { k1 } });
const ROTATED = JSON.stringify({ active: "k2", keys: { k1, k2 } });
const RETIRED = JSON.stringify({ active: "k2", keys: { k2 } });
const NS = VECTORS.files.namespace;
const { k1: OLDER, k2: NEWER, rewrapped: REWRAPPED } = VECTORS.files;

let tree: Tree;
let main: Datakey;

beforeAll(async () => {
  tree = treeAt(VECTORS.commit, ["platform/contract", "platform/files/src"]);
  main = await tree.load("platform/files/src/datakey.ts");
});

afterAll(() => {
  tree.remove();
});

/** What a call ends as: "ok", or its refusal's message. */
async function outcome(run: () => Promise<unknown>): Promise<string> {
  return run().then(() => "ok", (error: unknown) => (error as Error).message);
}

const keyOf = async (code: Datakey, ring: string, wrapped: unknown, ns = NS) =>
  hex(new Uint8Array(await code.unwrap(ring, ns, wrapped)));

describe("main's golden keys", () => {
  it("are main's: its own code opens them", async () => {
    expect(await keyOf(main, ROTATED, OLDER.wrapped)).toBe(OLDER.key);
    expect(await keyOf(main, ROTATED, NEWER.wrapped)).toBe(NEWER.key);
    expect(await keyOf(main, RETIRED, REWRAPPED)).toBe(OLDER.key);
  });

  it("open under every ring that holds their kid, and rewrap to the active one", async () => {
    expect(await keyOf(datakey, ROTATED, OLDER.wrapped)).toBe(OLDER.key);
    expect(await keyOf(datakey, ROTATED, NEWER.wrapped)).toBe(NEWER.key);
    expect(await keyOf(datakey, OLD, OLDER.wrapped)).toBe(OLDER.key);
    expect(await keyOf(datakey, RETIRED, NEWER.wrapped)).toBe(NEWER.key);
    expect(await keyOf(datakey, RETIRED, REWRAPPED)).toBe(OLDER.key);
    const rewrapped = await datakey.rewrap(ROTATED, NS, OLDER.wrapped);
    expect(rewrapped.startsWith("v1.k2.")).toBe(true);
    expect(await keyOf(datakey, RETIRED, rewrapped)).toBe(OLDER.key);
    expect(await datakey.rewrap(ROTATED, NS, NEWER.wrapped)).toBe(NEWER.wrapped);
  });

  it("are refused under a ring that has retired their kid or never held it, or another namespace",
     async () => {
    for (const [ring, wrapped, ns] of [[RETIRED, OLDER.wrapped, NS], [OLD, NEWER.wrapped, NS],
                                       [ROTATED, OLDER.wrapped, "f".repeat(32)]] as const) {
      expect(await outcome(() => datakey.unwrap(ring, ns, wrapped))).toBe(datakey.KEY_REFUSED);
    }
  });
});

describe("today's keys", () => {
  it("open on main's code, rewrapped ones included", async () => {
    const minted = await datakey.mint(ROTATED, NS);
    expect(await keyOf(main, ROTATED, minted)).toBe(await keyOf(datakey, ROTATED, minted));
    const rewrapped = await datakey.rewrap(ROTATED, NS, OLDER.wrapped);
    expect(await keyOf(main, RETIRED, rewrapped)).toBe(OLDER.key);
  });

  it("and main's fresh keys open on today's", async () => {
    const minted = await main.mint(OLD, NS);
    expect(await keyOf(datakey, ROTATED, minted)).toBe(await keyOf(main, OLD, minted));
    const rewrapped = await main.rewrap(ROTATED, NS, minted);
    expect(await keyOf(datakey, RETIRED, rewrapped)).toBe(await keyOf(main, OLD, minted));
  });
});

describe("every refusal is main's", () => {
  const key = btoa(String.fromCharCode(...new Uint8Array(32).fill(7)));
  const RINGS: unknown[] = [
    undefined, null, 42, "", "{active", "[]", "null", JSON.stringify({ active: "k1" }),
    JSON.stringify({ keys: { k1: key } }), JSON.stringify({ active: "k2", keys: { k1: key } }),
    JSON.stringify({ active: "k1", keys: {} }),
    JSON.stringify({ active: "k1", keys: { k1: key.slice(0, 24) } }),
    JSON.stringify({ active: "k1", keys: { k1: "*".repeat(44) } }),
    JSON.stringify({ active: "k.1", keys: { "k.1": key } }),
    JSON.stringify({ active: "k1", keys: { k1: key }, note: "x" }),
    JSON.stringify({ active: "k1", keys: Object.fromEntries(
      Array.from({ length: 65 }, (_, n) => [`k${n + 1}`, key])) }),
    `{${" ".repeat(5 * 1024)}"active":"k1","keys":{"k1":"${key}"}}`,
    OLD, ROTATED, RETIRED,
  ];

  /** A wrapped key and its tampered forms: each field changed, each part out of shape. */
  function tampered(wrapped: string): unknown[] {
    const [, kid, nonce, body] = wrapped.split(".") as [string, string, string, string];
    const flip = (text: string, at: number) =>
      text.slice(0, at) + (text[at] === "A" ? "B" : "A") + text.slice(at + 1);
    return [wrapped, `v2.${kid}.${nonce}.${body}`,
            `v1.${kid === "k1" ? "k2" : "k1"}.${nonce}.${body}`, `v1.k9.${nonce}.${body}`,
            `v1.${kid}.${flip(nonce, 2)}.${body}`, `v1.${kid}.${nonce}.${flip(body, 5)}`,
            `v1.${kid}.${nonce}.${body.slice(0, -4)}`, `v1.${kid}.${nonce}.${body}AAAA`,
            `v1.${kid}.${nonce}.${body}=`, `v1.${kid}.${nonce}.${body}.x`, `v1.${kid}.${nonce}`,
            `v1.${kid}.${nonce.slice(0, 12)}.${body}`, `v1.${kid}.${nonce}AAAA.${body}`,
            `v1.${kid}.${nonce}.AAAAA`, `v1.${kid}.${nonce}.${body}`.padEnd(118, "A"),
            "", "v1", 42, null];
  }

  /**
   * Blobs sealed correctly in this Worker's own domain (its documented salt, info and
   * context) under k1 and k2, over plaintexts of 31, 32 and 33 bytes: only a data key's 32
   * bytes is a data key.
   */
  let sized: { bytes: number; wrapped: string }[];
  beforeAll(async () => {
    const domain = { salt: "tinyassets/files/v1", info: `dek\0${NS}`,
                     context: `ta-files-key/v1\0${NS}` };
    sized = [];
    for (const ring of [OLD, ROTATED]) {
      for (const bytes of [31, 32, 33]) {
        const plain = new Uint8Array(bytes).fill(bytes);
        sized.push({ bytes, wrapped: await sealBlob(parseRing(ring)!, domain, plain) });
      }
    }
  });

  it("refuses a blob of this domain over any plaintext but a data key's 32 bytes", async () => {
    for (const { bytes, wrapped } of sized) {
      for (const run of ["unwrap", "rewrap"] as const) {
        const ours = await outcome(() => datakey[run](ROTATED, NS, wrapped));
        expect(ours, `${run} ${bytes}`).toBe(bytes === 32 ? "ok" : datakey.KEY_REFUSED);
        expect(ours).toBe(await outcome(() => main[run](ROTATED, NS, wrapped)));
      }
    }
    expect(await keyOf(datakey, ROTATED, sized[1]!.wrapped)).toBe("20".repeat(32));
  });

  it("for every ring and every key, by unwrap, rewrap and mint", async () => {
    for (const ring of RINGS) {
      for (const wrapped of [...tampered(OLDER.wrapped), ...tampered(NEWER.wrapped),
                             ...sized.map(({ wrapped: each }) => each)]) {
        for (const run of ["unwrap", "rewrap"] as const) {
          const what = `${run} ${String(ring).slice(0, 40)} ${String(wrapped).slice(0, 12)}`;
          expect(await outcome(() => datakey[run](ring, NS, wrapped)), what)
            .toBe(await outcome(() => main[run](ring, NS, wrapped)));
        }
      }
      expect(await outcome(() => datakey.mint(ring, NS)))
        .toBe(await outcome(() => main.mint(ring, NS)));
    }
  });
});

describe("the files Worker's domain", () => {
  it("opens no blob the gateway sealed, under the very same ring", async () => {
    // A 32-byte value sealed by the gateway for this namespace as its owner: the size of a
    // data key, so it passes every check but the domain's.
    const value = VECTORS.gateway.value;
    expect(value.binding.owner).toBe(NS);
    for (const blob of [value.k2, VECTORS.gateway.k1, VECTORS.gateway.k2,
                        VECTORS.gateway.doorbell.k2]) {
      for (const run of ["unwrap", "rewrap"] as const) {
        expect(await outcome(() => datakey[run](ROTATED, NS, blob))).toBe(datakey.KEY_REFUSED);
      }
    }
  });
});
