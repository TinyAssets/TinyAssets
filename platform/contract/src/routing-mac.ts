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
 * is never a sync token, nor the reverse. One module, so the two Workers cannot disagree:
 * it is the token's and the route's format, which both compute; the key itself is each
 * Worker's own secret, read from its own env and handed in.
 */
import {
  createCipheriv, createDecipheriv, createHmac, randomBytes, timingSafeEqual,
} from "node:crypto";

import { sha256Hex } from "./digest";
import { principal, utf8Text } from "./text";

/** A lease's egress or sync token's expiry: none of its own, its lease's (renewed). */
export const LEASE_LIVED = 0;

/** The Worker secret both Workers hold. */
export const ROUTING_MAC_KEY = "ROUTING_MAC_KEY";
/** The shortest routing key, in bytes once decoded. */
export const KEY_MIN_BYTES = 32;
/**
 * What a token is for: E2B's transform on the egress door, the in-VM sync agent, or the owner
 * app's socket ticket (`contract/src/socket-shape.ts`), which the doors verify before any object.
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
  return sha256Hex(secret);
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
    const owner = principal(utf8Text(Buffer.from(routing, "base64url"), "strip"));
    return { owner, expires: Number(at), hash: tokenHash(secret) };
  } catch {
    return null;
  }
}

/** Whether `token` has a token's form, MAC unchecked: what a placeholder check reads. */
export function tokenShaped(token: unknown): boolean {
  return typeof token === "string" && TOKEN.test(token);
}

/** Whose sandbox an E2B event is: the owner and the authority (`owner`, or a client's id). */
export interface SandboxRoute {
  readonly owner: string;
  readonly authority: string;
}

const ROUTE_DOMAIN = "ta-e2b-route-v1";
const AUTHORITY = /^[A-Za-z0-9._:-]{1,64}$/;

/** The route key: the routing key's own, apart from every token's by its domain. */
function routeKey(key: unknown): Buffer {
  return createHmac("sha256", keyBytes(key)).update(ROUTE_DOMAIN).digest();
}

/**
 * The sandbox's route, sealed (AES-256-GCM, a fresh nonce) for E2B's metadata (slice P): the
 * owner object seals it on each start (`SandboxStart.route`), the sandbox Worker sets it on
 * the sandbox it creates (`ta_route`), and E2B echoes it on every lifecycle event, which the
 * doors open to reach that owner's object. E2B learns no owner id from it.
 */
export function sealRoute(key: unknown, route: SandboxRoute): string {
  principal(route.owner);
  if (!AUTHORITY.test(route.authority)) {
    throw new Error("a sandbox's authority is owner or a client's id");
  }
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", routeKey(key), nonce);
  const body = Buffer.concat([cipher.update(JSON.stringify([route.owner, route.authority]),
                                            "utf8"), cipher.final()]);
  return Buffer.concat([nonce, body, cipher.getAuthTag()]).toString("base64url");
}

/** A sealed route opened, or null for anything another key or a changed byte made. */
export function openRoute(key: unknown, sealed: unknown): SandboxRoute | null {
  const bytes = typeof sealed === "string" && /^[A-Za-z0-9_-]{40,2048}$/.test(sealed)
    ? Buffer.from(sealed, "base64url") : null;
  if (bytes === null || bytes.byteLength < 12 + 16 + 2) {
    return null;
  }
  try {
    const decipher = createDecipheriv("aes-256-gcm", routeKey(key), bytes.subarray(0, 12));
    decipher.setAuthTag(bytes.subarray(bytes.byteLength - 16));
    const plain = Buffer.concat([decipher.update(bytes.subarray(12, bytes.byteLength - 16)),
                                 decipher.final()]).toString("utf8");
    const [owner, authority] = JSON.parse(plain) as [unknown, unknown];
    if (typeof owner !== "string" || typeof authority !== "string" || !AUTHORITY.test(authority)) {
      return null;
    }
    return { owner: principal(owner), authority };
  } catch {
    return null;
  }
}
