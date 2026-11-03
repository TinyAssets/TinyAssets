/**
 * Self-routing sandbox tokens, MAC'd (G4): what lets a door reach an owner's object by the
 * token alone, with no directory read, and lets no stranger's token reach any object.
 *
 * A token is `<owner>.<expires>.<secret>.<mac>`:
 * - `owner`, the owner id as the binder names the owner's object (`getByName`), base64url:
 *   it tells nothing but which object, as every binding's owner does;
 * - `expires`, in Unix seconds (decimal), past which the door refuses the token without
 *   reaching any object: a socket ticket's 30 s. `0` for a sandbox lease's egress and sync
 *   tokens, which have no deadline of their own: they live while their lease is renewed,
 *   which the owner's object checks (its heartbeat; `files.ts`'s `LIVE_LEASE`);
 * - `secret`, 256 random bits, base64url, whose SHA-256 alone the owner's object keeps, on
 *   its lease;
 * - `mac`, HMAC-SHA-256 under the routing key over the token's kind, owner, expiry and
 *   secret, base64url.
 *
 * The routing key (`ROUTING_MAC_KEY`) is a Worker secret of both the owner Worker, which
 * mints (`mintToken`), and the doors Worker, which verifies (`verifyToken`) before any stub
 * call, so a forged, altered or other-kind token is refused at the door and reaches no
 * object; the object then checks the secret's hash against its live lease. It is generated
 * at deploy (32 random bytes, base64url, at least `KEY_MIN_BYTES`), and a Worker without a
 * usable one fails loudly on its first token. The kind is in the MAC, so an egress token
 * is never a sync token, nor the reverse. One module, so the two Workers cannot disagree.
 */
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import { principal } from "../../owner/src/text";

/** A lease's egress or sync token's expiry: none of its own, its lease's (renewed). */
export const LEASE_LIVED = 0;

/** The Worker secret both Workers hold. */
export const ROUTING_MAC_KEY = "ROUTING_MAC_KEY";
/** The shortest routing key, in bytes once decoded. */
export const KEY_MIN_BYTES = 32;
/**
 * What a token is for: E2B's transform on the egress door, the in-VM sync agent, or the owner
 * app's socket ticket (`owner/src/socket-shape.ts`), which the doors verify before any object.
 */
export type TokenKind = "egress" | "sync" | "socket";

const TOKEN =
  /^([A-Za-z0-9_-]{1,2732})\.(0|[1-9][0-9]{9,10})\.([A-Za-z0-9_-]{43})\.([A-Za-z0-9_-]{43})$/;
const KEY = /^[A-Za-z0-9_-]+$/;
const DOMAIN = "ta-sandbox-token-v2";

/** The routing key's bytes, or a loud failure: a Worker deployed without its secret. */
function keyBytes(key: unknown): Buffer {
  if (typeof key !== "string" || !KEY.test(key)
      || Buffer.from(key, "base64url").byteLength < KEY_MIN_BYTES) {
    throw new Error(`${ROUTING_MAC_KEY} is not set to at least ${KEY_MIN_BYTES} bytes, base64url`);
  }
  return Buffer.from(key, "base64url");
}

function mac(key: Buffer, kind: TokenKind, owner: string, expires: string, secret: string):
    Buffer {
  return createHmac("sha256", key)
    .update(`${DOMAIN}\n${kind}\n${owner}.${expires}.${secret}`, "utf8").digest();
}

/** The SHA-256 hex of a token's secret part: what the owner's object keeps. */
export function tokenHash(secret: string): string {
  return createHash("sha256").update(secret, "utf8").digest("hex");
}

/**
 * A fresh `kind` token for `owner` under the routing key, good until `expires` (Unix
 * seconds; `LEASE_LIVED` for a lease's token, which its lease's life bounds), and the hash
 * of its secret, the only part of it the owner's object keeps.
 */
export function mintToken(key: unknown, kind: TokenKind, owner: string, expires: number):
    { readonly token: string; readonly hash: string } {
  const bytes = keyBytes(key);
  if (!Number.isSafeInteger(expires) || !/^(0|[1-9][0-9]{9,10})$/.test(String(expires))
      || (expires === LEASE_LIVED && kind === "socket")) {
    throw new Error("a sandbox token's expiry is whole Unix seconds, or its lease's");
  }
  const routing = Buffer.from(principal(owner), "utf8").toString("base64url");
  const secret = randomBytes(32).toString("base64url");
  const at = String(expires);
  return { token: `${routing}.${at}.${secret}.${
             mac(bytes, kind, routing, at, secret).toString("base64url")}`,
           hash: tokenHash(secret) };
}

/**
 * The owner a `kind` token names, its expiry (Unix seconds) and its secret's hash, once its
 * MAC is the routing key's own; null for anything else (malformed, forged, altered, or a
 * token of the other kind). The caller refuses one past its expiry.
 */
export function verifyToken(key: unknown, kind: TokenKind, token: unknown):
    { readonly owner: string; readonly expires: number; readonly hash: string } | null {
  const bytes = keyBytes(key);
  const match = typeof token === "string" ? TOKEN.exec(token) : null;
  if (match === null) {
    return null;
  }
  const [, routing, at, secret, presented] =
    match as unknown as [string, string, string, string, string];
  const expected = mac(bytes, kind, routing, at, secret);
  const given = Buffer.from(presented, "base64url");
  if (given.byteLength !== expected.byteLength || !timingSafeEqual(given, expected)
      || given.toString("base64url") !== presented) {
    return null;
  }
  // The MAC covers the routing part exactly as the owner's object wrote it, so it decodes to
  // the owner that object minted it for.
  try {
    const owner = principal(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false })
      .decode(Buffer.from(routing, "base64url")));
    return { owner, expires: Number(at), hash: tokenHash(secret) };
  } catch {
    return null;
  }
}

/** Whether `token` has a token's form, MAC unchecked: what a placeholder check reads. */
export function tokenShaped(token: unknown): boolean {
  return typeof token === "string" && TOKEN.test(token);
}
