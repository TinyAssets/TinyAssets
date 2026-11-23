/**
 * Values that carry a place in one of the object's orders, opaque to anyone without the
 * object's id key (`ledger_ids`, which the export carries, so a restored owner reads its own
 * back): a mail's id its seq (`orderedId`), and a collection reader's cursor its place in the
 * collections' clock or in one collection's seqs (`cursorAt`). Neither says how many changes
 * came before it, or between two of them, and the object reads the place back.
 */
import { Buffer } from "node:buffer";
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import { HEX32 } from "../../contract/src/ids";
import type { OwnerStorage } from "./gate";

/** A fresh key for `orderedId`: 32 random bytes, kept in the object. */
export function idKey(): Uint8Array {
  return new Uint8Array(randomBytes(32));
}

/** The object's id key, made in the caller's unit the first time one is needed. */
export function heldIdKey(sql: OwnerStorage["sql"]): Uint8Array {
  const held = readIdKey(sql);
  if (held !== null) {
    return held;
  }
  const made = idKey();
  sql.exec("INSERT INTO ledger_ids (only, id_key) VALUES (1, ?)",
           Buffer.from(made).toString("hex"));
  return made;
}

/** The object's id key, or null before one is made. A read. */
export function readIdKey(sql: OwnerStorage["sql"]): Uint8Array | null {
  const held = sql.exec<{ id_key: string }>("SELECT id_key FROM ledger_ids").toArray()[0];
  return held === undefined ? null : Uint8Array.from(Buffer.from(held.id_key, "hex"));
}

function round(key: Uint8Array, index: number, half: Buffer): Buffer {
  return createHmac("sha256", key).update(Buffer.from([index])).update(half).digest()
    .subarray(0, 8);
}

function xor(a: Buffer, b: Buffer): Buffer {
  const out = Buffer.alloc(a.length);
  for (let i = 0; i < a.length; i++) {
    out[i] = a[i]! ^ b[i]!;
  }
  return out;
}

/**
 * An id for `key` (a mail's seq): 32 hex, a keyed permutation (a four-round Feistel network
 * over HMAC-SHA256) of the key and eight random bytes. Opaque to anyone without the object's
 * key, so it says nothing of the seq or the volume, and the object reads the seq back.
 */
export function orderedId(idKeyBytes: Uint8Array, key: number): string {
  let left = Buffer.alloc(8);
  left.writeBigUInt64BE(BigInt(key));
  let right = Buffer.from(randomBytes(8));
  for (let i = 0; i < 4; i++) {
    [left, right] = [right, xor(left, round(idKeyBytes, i, right))];
  }
  return Buffer.concat([left, right]).toString("hex");
}

/** The key an `orderedId` carries, or null for text that is not one (any 32 hex decodes). */
export function idOrder(idKeyBytes: Uint8Array, id: string): number | null {
  if (!HEX32.test(id)) {
    return null;
  }
  const whole = Buffer.from(id, "hex");
  let left = whole.subarray(0, 8);
  let right = whole.subarray(8);
  for (let i = 3; i >= 0; i--) {
    [right, left] = [left, xor(right, round(idKeyBytes, i, left))];
  }
  const key = left.readBigUInt64BE();
  return key <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(key) : null;
}

const CURSOR = /^[0-9a-f]{48}$/;

/**
 * Eight bytes of HMAC-SHA256 under the key, over what they are for (`use`), the order the
 * cursor is in (`order`) and `data`. A Feistel round's input starts with a byte under 4, and
 * this one with a letter, so no two of them meet under the one key.
 */
function mac(key: Uint8Array, use: "mask" | "tag", order: string, data: Buffer): Buffer {
  return createHmac("sha256", key).update(`${use}\n${order}\n`).update(data).digest()
    .subarray(0, 8);
}

/**
 * A cursor at `place` in `order` (the collections' clock, or one collection's seqs): eight
 * random bytes, the place masked under them, and a tag over both and the order. So it says
 * nothing of its place, two cursors at one place differ, and one made up, altered or given
 * for another order is refused. 48 hex.
 */
export function cursorAt(key: Uint8Array, order: string, place: number): string {
  const nonce = randomBytes(8);
  const placed = Buffer.alloc(8);
  placed.writeBigUInt64BE(BigInt(place));
  const masked = xor(placed, mac(key, "mask", order, nonce));
  const body = Buffer.concat([nonce, masked]);
  return Buffer.concat([body, mac(key, "tag", order, body)]).toString("hex");
}

/** The place a `cursorAt` cursor in `order` names, or null for anything else. */
export function cursorPlace(key: Uint8Array, order: string, cursor: string): number | null {
  if (!CURSOR.test(cursor)) {
    return null;
  }
  const whole = Buffer.from(cursor, "hex");
  const body = whole.subarray(0, 16);
  if (!timingSafeEqual(whole.subarray(16), mac(key, "tag", order, body))) {
    return null;
  }
  const place = xor(body.subarray(8), mac(key, "mask", order, body.subarray(0, 8)))
    .readBigUInt64BE();
  return place <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(place) : null;
}
