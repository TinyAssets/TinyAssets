/**
 * Self-routing sandbox tokens, MAC'd, and the sandbox's sealed route: what lets a door reach
 * an owner's object by the value alone, with no directory read, and lets no stranger's value
 * reach any object.
 *
 * Both are under the routing keyring (`ROUTING_KEYRING`), a Worker secret of both the owner
 * Worker, which mints and seals under its active kid (`mintToken`, `sealRoute`), and the doors
 * Worker, which verifies and opens under whichever kid the value names, if its ring holds it
 * (`verifyToken`, `openRoute`), before any stub call. It is a ring in the envelope's form
 * (`envelope.ts` `parseRing`), made by `keyring-new` and grown by the kit's keyring tools
 * (`deploy/deploy.ts`), and a Worker without a usable one fails loudly on its first value.
 * The owner deploys before the doors, so a new key reaches both rings before it is made active
 * (`keyring-stage`, then `keyring-activate`; `deploy/checklist.md`). A value lives as long as
 * the sandbox that carries it: a lease's tokens are in its sandbox, minted no earlier than
 * that sandbox's route was sealed, and the route is in the sandbox's E2B metadata, set once at
 * its create and kept while E2B keeps the sandbox, paused indefinitely. So mode `retire` takes
 * a kid out only once every route E2B holds opens under the ring left (`deploy.ts`
 * `routesHeld`).
 *
 * A token is `<owner>.<expires>.<secret>.<kid>.<mac>`:
 * - `owner`, the owner id as the binder names the owner's object (`getByName`), base64url:
 *   it tells nothing but which object, as every binding's owner does;
 * - `expires`, in Unix seconds (decimal), past which the door refuses the token without
 *   reaching any object: a socket ticket's 30 s. `0` for a sandbox lease's egress and sync
 *   tokens, which have no deadline of their own: they live while their lease is renewed,
 *   which the owner's object checks (its heartbeat; `files.ts`'s `LIVE_LEASE`);
 * - `secret`, 256 random bits, base64url, whose SHA-256 alone the owner's object keeps, on
 *   its lease;
 * - `kid`, the ring's kid its MAC is under;
 * - `mac`, HMAC-SHA-256 under that kid's key over the token's kind, kid, owner, expiry and
 *   secret, base64url.
 *
 * So a forged, altered or other-kind token is refused at the door and reaches no object; the
 * object then checks the secret's hash against its live lease. The kind is in the MAC, so an
 * egress token is never a sync token, nor the reverse. One module, so the two Workers cannot
 * disagree: it is the token's and the route's format, which both compute; the ring itself is
 * each Worker's own secret, read from its own env and handed in.
 */
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import { sha256Hex } from "./digest.ts";
import {
  blobMax, type Domain, openBlob, parseBlob, parseRing, type Ring, sealBlob,
} from "./envelope.ts";
import { AUTHORITY } from "./ids.ts";
import { principal, utf8Text } from "./text.ts";

/** A lease's egress or sync token's expiry: none of its own, its lease's (renewed). */
export const LEASE_LIVED = 0;

/** The Worker secret both Workers hold. */
export const ROUTING_KEYRING = "ROUTING_KEYRING";
/**
 * What a token is for: E2B's transform on the egress door, the in-VM sync agent, or the owner
 * app's socket ticket (`contract/src/socket-shape.ts`), which the doors verify before any object.
 */
export type TokenKind = "egress" | "sync" | "socket";

/** A token's parts; its kid is one only when the ring holds it (`verifyToken`). */
const TOKEN =
  /^([A-Za-z0-9_-]{1,2732})\.(0|[1-9][0-9]{9,10})\.([A-Za-z0-9_-]{43})\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{43})$/;
const DOMAIN = "ta-sandbox-token-v3";

/** The routing ring, or a loud failure: a Worker deployed without its secret. */
function routingRing(keyring: unknown): Ring {
  const ring = parseRing(keyring);
  if (ring === null) {
    throw new Error(`${ROUTING_KEYRING} is not a keyring: {active, keys}, each key 32 bytes`);
  }
  return ring;
}

function mac(key: Uint8Array, kind: TokenKind, kid: string, owner: string, expires: string,
             secret: string): Buffer {
  return createHmac("sha256", key)
    .update(`${DOMAIN}\n${kind}\n${kid}\n${owner}.${expires}.${secret}`, "utf8").digest();
}

/** The SHA-256 hex of a token's secret part: what the owner's object keeps. */
export function tokenHash(secret: string): string {
  return sha256Hex(secret);
}

/**
 * A fresh `kind` token for `owner` under the ring's active kid, good until `expires` (Unix
 * seconds; `LEASE_LIVED` for a lease's token, which its lease's life bounds), and the hash
 * of its secret, the only part of it the owner's object keeps.
 */
export function mintToken(keyring: unknown, kind: TokenKind, owner: string, expires: number):
    { readonly token: string; readonly hash: string } {
  const ring = routingRing(keyring);
  if (!Number.isSafeInteger(expires) || !/^(0|[1-9][0-9]{9,10})$/.test(String(expires))
      || (expires === LEASE_LIVED && kind === "socket")) {
    throw new Error("a sandbox token's expiry is whole Unix seconds, or its lease's");
  }
  const routing = Buffer.from(principal(owner), "utf8").toString("base64url");
  const secret = randomBytes(32).toString("base64url");
  const at = String(expires);
  const { active } = ring;
  return { token: `${routing}.${at}.${secret}.${active}.${
             mac(ring.keys.get(active)!, kind, active, routing, at, secret).toString("base64url")}`,
           hash: tokenHash(secret) };
}

/**
 * The owner a `kind` token names, its expiry (Unix seconds) and its secret's hash, once its
 * MAC is that of the kid it names, which the ring holds; null for anything else (malformed,
 * forged, altered, a kid the ring does not hold, or a token of another kind). The caller
 * refuses one past its expiry.
 */
export function verifyToken(keyring: unknown, kind: TokenKind, token: unknown):
    { readonly owner: string; readonly expires: number; readonly hash: string } | null {
  const ring = routingRing(keyring);
  const match = typeof token === "string" ? TOKEN.exec(token) : null;
  const key = match === null ? undefined : ring.keys.get(match[4]!);
  if (match === null || key === undefined) {
    return null;
  }
  const [, routing, at, secret, kid, presented] =
    match as unknown as [string, string, string, string, string, string];
  const expected = mac(key, kind, kid, routing, at, secret);
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

/** Whose sandbox an E2B event is: the owner and the authority (`owner`, or a client's id). */
export interface SandboxRoute {
  readonly owner: string;
  readonly authority: string;
}

/**
 * A sealed route's most characters: an envelope blob over the longest route, an owner
 * principal's 512 code points of up to 4 bytes each and a client's 64-hex authority, as JSON.
 */
function routeMax(): number {
  return blobMax(512 * 4 + 64 + JSON.stringify(["", ""]).length);
}

/** The route's use of the ring: its own HKDF info and context, apart from every other use. */
const ROUTE: Domain = Object.freeze({ salt: "tinyassets/route/v1", info: "route",
                                      context: "ta-e2b-route/v2" });

/**
 * The E2B metadata key a sandbox's sealed route is under: the sandbox Worker writes it at the
 * create, the doors read it from each lifecycle event, and mode retire from E2B's list.
 */
export const ROUTE_METADATA = "ta_route";

/** Whether `sealed` has a sealed route's form, unopened: what the sandbox Worker checks. */
export function routeShaped(sealed: unknown): boolean {
  return parseBlob(sealed, routeMax()) !== null;
}

/**
 * The sandbox's route, sealed in the envelope (`sealBlob`) under the ring's active kid, for
 * E2B's metadata: the owner object seals it on each start (`SandboxStart.route`), the sandbox
 * Worker sets it on the sandbox it creates (`ta_route`), and E2B echoes it on every lifecycle
 * event, which the doors open to reach that owner's object. E2B learns no owner id from it.
 */
export async function sealRoute(keyring: unknown, route: SandboxRoute): Promise<string> {
  const ring = routingRing(keyring);
  principal(route.owner);
  if (!AUTHORITY.test(route.authority)) {
    throw new Error("a sandbox's authority is owner or a client's id");
  }
  return sealBlob(ring, ROUTE, new TextEncoder().encode(JSON.stringify([route.owner,
                                                                         route.authority])));
}

/**
 * A sealed route opened, or null for anything another key, a kid the ring does not hold or a
 * changed byte made.
 */
export async function openRoute(keyring: unknown, sealed: unknown): Promise<SandboxRoute | null> {
  const opened = await openBlob(routingRing(keyring), ROUTE, sealed, routeMax());
  if (opened === null) {
    return null;
  }
  try {
    const [owner, authority] = JSON.parse(utf8Text(opened.plain, "strip")) as [unknown, unknown];
    if (typeof owner !== "string" || typeof authority !== "string" || !AUTHORITY.test(authority)) {
      return null;
    }
    return { owner: principal(owner), authority };
  } catch {
    return null;
  }
}
