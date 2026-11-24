/**
 * Every blob the gateway's keyring ever sealed stays readable, and every blob it seals now
 * opens on the code that sealed before: the keyring, a binding over the one
 * sealed envelope (`contract/src/envelope.ts`), against main's own copy of the scheme as it
 * stood at `ci/keyring-vectors.json`'s commit, the last before the envelope, read from git
 * and run as it was.
 *
 * - Main's golden credentials and doorbells, checked in, open under today's code.
 * - Blobs sealed today open on main's code, a credential's plaintext read by the base
 *   branch's grammar (`baseOf`), the version a rollback of this deploy reaches (main's
 *   predates upstreams naming their APIs, and no rollback reaches it); and main's fresh
 *   blobs on today's.
 * - Every malformed ring, every tampered or relabelled blob, every retired kid and every
 *   blob of its own domain past the 64 KiB bound is answered with main's own refusal.
 * - No data key the files Worker wrapped opens here, under the very same ring, and no
 *   doorbell opens as a credential, nor a credential as a doorbell.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { baseOf, type Tree, treeAt } from "../../ci/history";
import VECTORS from "../../ci/keyring-vectors.json" with { type: "json" };
import { parseRing, sealBlob } from "../../contract/src/envelope";
import { CONNECTION_BLOB_MAX } from "../../contract/src/limits";
import { credential } from "../src/credential";
import { doorbellSecret, sealDoorbell } from "../src/doorbell";
import * as keyring from "../src/keyring";

type Keyring = typeof keyring;
type Doorbell = { doorbellSecret: typeof doorbellSecret; sealDoorbell: typeof sealDoorbell };

const { k1, k2 } = VECTORS.keys;
const OLD = { GATEWAY_KEYRING: JSON.stringify({ active: "k1", keys: { k1 } }) };
const ROTATED = { GATEWAY_KEYRING: JSON.stringify({ active: "k2", keys: { k1, k2 } }) };
const RETIRED = { GATEWAY_KEYRING: JSON.stringify({ active: "k2", keys: { k2 } }) };
// Each golden bound as the gateway binds a stored record: by its names, through today's grammar.
const BOUND = keyring.binding(VECTORS.gateway.binding.owner, VECTORS.gateway.binding.connection,
                              VECTORS.gateway.binding.version);
const VALUE = VECTORS.gateway.value;
const VALUE_BOUND = keyring.binding(VALUE.binding.owner, VALUE.binding.connection,
                                    VALUE.binding.version);
const CREDENTIAL = VECTORS.gateway.credential as unknown as Parameters<Keyring["seal"]>[2];
const SECRET = { secret: VECTORS.gateway.credential.secret };
/** The golden credential as today's code reads it: its one API a row of `apis`. */
const HELD = credential(VECTORS.gateway.credential);
const BELL = VECTORS.gateway.doorbell;
const BELL_BOUND = keyring.wakeBinding(BELL.owner, BELL.subscription, BELL.version);
const BELL_SECRET = "sk-ant-oat01-" + "v".repeat(40);
const BELL_INPUT = { record: BELL.value.record, url: BELL.value.url, secret: BELL_SECRET };
const bell = (kid: "k1" | "k2") =>
  ({ ...BELL.value, secret: BELL_SECRET, sealed_at: BELL.sealed_at[kid] });
const same = (value: unknown) => value;

let tree: Tree;
let base: Tree;
let main: { keyring: Keyring; doorbell: Doorbell };
/** The base branch's credential grammar. */
let before: typeof credential;

beforeAll(async () => {
  tree = treeAt(VECTORS.commit, ["platform/contract", "platform/gateway/src"]);
  base = treeAt(baseOf(), ["platform/contract", "platform/gateway/src"]);
  main = { keyring: await tree.load("platform/gateway/src/keyring.ts"),
           doorbell: await tree.load("platform/gateway/src/doorbell.ts") };
  before = (await base.load<{ credential: typeof credential }>(
    "platform/gateway/src/credential.ts")).credential;
});

afterAll(() => {
  tree.remove();
  base.remove();
});

/** What a call ends as: "ok", or its refusal's code. */
async function outcome(run: () => Promise<unknown>): Promise<string> {
  return run().then(() => "ok", (error: unknown) =>
    (error as { code?: string }).code ?? String(error));
}

describe("main's golden blobs", () => {
  it("are main's: its own code opens them", async () => {
    expect((await main.keyring.open(ROTATED, BOUND, VECTORS.gateway.k2)).credential)
      .toEqual(VECTORS.gateway.credential);
    for (const kid of ["k1", "k2"] as const) {
      expect((await main.keyring.openValue(ROTATED, BELL_BOUND, BELL[kid],
                                           main.doorbell.doorbellSecret)).value).toEqual(bell(kid));
    }
  });

  it("open as credentials under every ring that holds their kid, an older kid's resealed",
     async () => {
    for (const [ring, kid, blob] of [[ROTATED, "k2", VECTORS.gateway.k2],
                                     [OLD, "k1", VECTORS.gateway.k1],
                                     [RETIRED, "k2", VECTORS.gateway.k2]] as const) {
      expect(await keyring.open(ring, BOUND, blob)).toEqual({ credential: HELD, kid });
    }
    const old = await keyring.open(ROTATED, BOUND, VECTORS.gateway.k1);
    expect([old.kid, old.credential]).toEqual(["k1", HELD]);
    expect(old.resealed).toMatchObject({ kid: "k2", version: BOUND.version + 1 });
    const next = await keyring.open(ROTATED, { ...BOUND, version: BOUND.version + 1 },
                                    old.resealed!.blob);
    expect(next.resealed).toBeUndefined();
    expect(next.credential).toMatchObject(SECRET);
  });

  it("are refused under a ring that has retired their kid or never held it, or another binding",
     async () => {
    expect(await outcome(() => keyring.open(RETIRED, BOUND, VECTORS.gateway.k1)))
      .toBe("credential_refused");
    expect(await outcome(() => keyring.open(OLD, BOUND, VECTORS.gateway.k2)))
      .toBe("credential_refused");
    expect(await outcome(() => keyring.open(ROTATED, { ...BOUND, version: 4 },
                                            VECTORS.gateway.k2))).toBe("credential_refused");
  });

  it("open as doorbells, an older kid's resealed", async () => {
    expect(await keyring.openValue(ROTATED, BELL_BOUND, BELL.k2, doorbellSecret))
      .toEqual({ value: bell("k2"), kid: "k2" });
    const old = await keyring.openValue(ROTATED, BELL_BOUND, BELL.k1, doorbellSecret);
    expect([old.value, old.kid, old.resealed?.kid]).toEqual([bell("k1"), "k1", "k2"]);
    expect(await outcome(() => keyring.openValue(RETIRED, BELL_BOUND, BELL.k1, doorbellSecret)))
      .toBe("credential_refused");
  });
});

describe("today's blobs", () => {
  it("open on main's code as credentials, read by the base branch's, resealed and swept ones " +
     "included", async () => {
    // Each plaintext as sealed, in the one spelling today's code writes (HELD's), and as the
    // base branch reads it.
    const read = (value: unknown) => [value, before(value)];
    const sealed = await keyring.seal(ROTATED, BOUND, CREDENTIAL);
    const opened = await main.keyring.openValue(ROTATED, BOUND, sealed.blob, same);
    expect([opened.kid, ...read(opened.value)]).toEqual(["k2", HELD, HELD]);
    const resealed = await keyring.reseal(ROTATED, BOUND, CREDENTIAL);
    const again = { ...HELD, sealed_at: expect.any(Number) };
    expect(read((await main.keyring.openValue(ROTATED, { ...BOUND, version: resealed.version },
                                              resealed.blob, same)).value)).toEqual([again, again]);
    const swept = (await keyring.open(ROTATED, BOUND, VECTORS.gateway.k1)).resealed!;
    expect(read((await main.keyring.openValue(RETIRED, { ...BOUND, version: swept.version },
                                              swept.blob, same)).value)).toEqual([HELD, HELD]);
  });

  it("open on main's code as doorbells", async () => {
    const sealed = await sealDoorbell(ROTATED, BELL.owner, BELL.subscription, BELL.version,
                                      BELL_INPUT);
    const opened = await main.keyring.openValue(ROTATED, BELL_BOUND, sealed.blob,
                                                main.doorbell.doorbellSecret);
    expect(opened.value).toMatchObject({ ...BELL_INPUT, kind: "doorbell", auth: "bearer" });
  });

  it("and main's fresh blobs open on today's", async () => {
    const sealed = await main.keyring.seal(ROTATED, BOUND, CREDENTIAL);
    expect((await keyring.open(ROTATED, BOUND, sealed.blob)).credential).toEqual(HELD);
    const rung = await main.doorbell.sealDoorbell(OLD, BELL.owner, BELL.subscription,
                                                  BELL.version, BELL_INPUT);
    expect((await keyring.openValue(ROTATED, BELL_BOUND, rung.blob, doorbellSecret))
      .resealed?.kid).toBe("k2");
  });
});

describe("every refusal is main's", () => {
  const key = btoa(String.fromCharCode(...new Uint8Array(32).fill(7)));
  const RINGS: unknown[] = [
    undefined, 42, "", "{active", "[]", "null", JSON.stringify({ active: "k1" }),
    JSON.stringify({ keys: { k1: key } }), JSON.stringify({ active: "k2", keys: { k1: key } }),
    JSON.stringify({ active: "k1", keys: {} }),
    JSON.stringify({ active: "k1", keys: { k1: key.slice(0, 24) } }),
    JSON.stringify({ active: "k1", keys: { k1: "*".repeat(44) } }),
    JSON.stringify({ active: "k.1", keys: { "k.1": key } }),
    JSON.stringify({ active: "k1", keys: { k1: key }, note: "x" }),
    JSON.stringify({ active: "k1", keys: Object.fromEntries(
      Array.from({ length: 65 }, (_, n) => [`k${n + 1}`, key])) }),
    `{${" ".repeat(5 * 1024)}"active":"k1","keys":{"k1":"${key}"}}`,
    OLD.GATEWAY_KEYRING, ROTATED.GATEWAY_KEYRING, RETIRED.GATEWAY_KEYRING,
  ];

  /** A blob and its tampered forms: each field changed, each part out of shape. */
  function tampered(blob: string): unknown[] {
    const [, kid, nonce, body] = blob.split(".") as [string, string, string, string];
    const flip = (text: string, at: number) =>
      text.slice(0, at) + (text[at] === "A" ? "B" : "A") + text.slice(at + 1);
    return [blob, `v2.${kid}.${nonce}.${body}`, `v1.${kid === "k1" ? "k2" : "k1"}.${nonce}.${body}`,
            `v1.k9.${nonce}.${body}`, `v1.${kid}.${flip(nonce, 2)}.${body}`,
            `v1.${kid}.${nonce}.${flip(body, 5)}`, `v1.${kid}.${nonce}.${body.slice(0, -4)}`,
            `v1.${kid}.${nonce}.${body}=`, `v1.${kid}.${nonce}.${body}.x`, `v1.${kid}.${nonce}`,
            `v1.${kid}.${nonce.slice(0, 12)}.${body}`, `v1.${kid}.${nonce}AAAA.${body}`,
            `v1.${kid}.${nonce}.AAAAA`, `v1.${kid}.${nonce}.AAAA`, "", "v1", 42, null];
  }

  /**
   * Blobs sealed correctly in the gateway's own domain (its documented salt, info and
   * context) for `BOUND` and for the plain value's binding, one at the 64 KiB bound and one
   * past it: no seal writes the second, and no open takes it.
   */
  let sized: { bound: typeof BOUND; at: string; over: string }[];
  beforeAll(async () => {
    const keys = parseRing(ROTATED.GATEWAY_KEYRING)!;
    const seal = (bound: typeof BOUND, chars: number) => sealBlob(keys, {
      salt: "tinyassets/gateway/v1", info: `cred\0${bound.owner}`,
      context: `ta-cred/v1\0${bound.owner}\0${bound.connection}\0${bound.version}`,
    }, new TextEncoder().encode(JSON.stringify("x".repeat(chars))));
    sized = [];
    for (const bound of [BOUND, VALUE_BOUND]) {
      // A JSON string of n characters is n + 2 bytes; with its tag, 49,134 bytes are 65,512
      // base64url characters, so the blob is 65,535 long.
      sized.push({ bound, at: await seal(bound, 49_116), over: await seal(bound, 49_300) });
    }
  });

  it("refuses a blob of its own domain past the 64 KiB bound, and opens one at it", async () => {
    for (const { bound, at, over } of sized) {
      expect(at.length).toBeLessThanOrEqual(CONNECTION_BLOB_MAX);
      expect(over.length).toBeGreaterThan(CONNECTION_BLOB_MAX);
      expect((await keyring.openValue(ROTATED, bound, at, same)).kid).toBe("k2");
      expect(await outcome(() => main.keyring.openValue(ROTATED, bound, at, same))).toBe("ok");
      for (const run of [() => keyring.openValue(ROTATED, bound, over, same),
                         () => keyring.open(ROTATED, bound, over),
                         () => main.keyring.openValue(ROTATED, bound, over, same),
                         () => main.keyring.open(ROTATED, bound, over)]) {
        expect(await outcome(run)).toBe("credential_refused");
      }
    }
  });

  it("for every ring and every blob, as credentials, doorbells and plain values", async () => {
    const over = sized.map(({ over: each }) => each);
    for (const raw of RINGS) {
      const ring = { GATEWAY_KEYRING: raw } as keyring.KeyringEnv;
      const what = (blob: unknown) => `${String(raw).slice(0, 40)} ${String(blob).slice(0, 12)}`;
      for (const blob of [...tampered(VECTORS.gateway.k1), ...tampered(VECTORS.gateway.k2),
                          ...over]) {
        expect(await outcome(() => keyring.open(ring, BOUND, blob)), what(blob))
          .toBe(await outcome(() => main.keyring.open(ring, BOUND, blob)));
      }
      for (const blob of tampered(BELL.k1)) {
        expect(await outcome(() => keyring.openValue(ring, BELL_BOUND, blob, doorbellSecret)),
               what(blob))
          .toBe(await outcome(() => main.keyring.openValue(ring, BELL_BOUND, blob,
                                                           main.doorbell.doorbellSecret)));
      }
      for (const blob of [...tampered(VALUE.k2), ...over]) {
        expect(await outcome(() => keyring.openValue(ring, VALUE_BOUND, blob, same)), what(blob))
          .toBe(await outcome(() => main.keyring.openValue(ring, VALUE_BOUND, blob, same)));
      }
      expect(await outcome(() => keyring.seal(ring, BOUND, CREDENTIAL)))
        .toBe(await outcome(() => main.keyring.seal(ring, BOUND, CREDENTIAL)));
    }
  });
});

describe("the gateway's domain", () => {
  it("opens no data key the files Worker wrapped, under the very same ring", async () => {
    const ns = VECTORS.files.namespace;
    for (const wrapped of [VECTORS.files.k1.wrapped, VECTORS.files.k2.wrapped,
                           VECTORS.files.rewrapped]) {
      for (const bound of [keyring.binding(ns, BOUND.connection, 1), BOUND, BELL_BOUND]) {
        expect(await outcome(() => keyring.openValue(ROTATED, bound, wrapped, same)))
          .toBe("credential_refused");
        expect(await outcome(() => keyring.open(ROTATED, bound, wrapped)))
          .toBe("credential_refused");
      }
    }
    // The same-sized value of its own, under that namespace as the owner, opens.
    expect((await keyring.openValue(ROTATED, VALUE_BOUND, VALUE.k2, same)).value)
      .toBe(VALUE.value);
  });

  it("opens no doorbell as a model credential, nor a credential as a doorbell", async () => {
    expect(await outcome(() => keyring.open(ROTATED, BELL_BOUND, BELL.k2)))
      .toBe("credential_refused");
    expect(await outcome(() => keyring.openValue(ROTATED, BOUND, VECTORS.gateway.k2,
                                                 doorbellSecret))).toBe("credential_refused");
  });
});
