/**
 * The owner directory (research §3): one KV namespace, `DIRECTORY`, a routing hint only.
 *
 * It maps a credential's id to its owner. It holds no owner content: hashes, owner ids,
 * opaque ids and times. The platform's control record lives in a namespace of its own
 * (`CONTROL`, `contract/src/control.ts`), never this directory. Every security
 * decision is made again in the owner's object, which is strongly consistent and on the
 * path, so KV's lag of up to 60 s can only route a dead credential to a refusal:
 * - a key's revoke lands in the object (`grantRevoke`) before its entry is deleted;
 * - a deleted key's grant is forgotten (`grantForget`) after, and the object refuses an
 *   API key with no grant row, never records one.
 *
 * Its entries: `k:` (API keys); `o:` (owners: the platform's record of each owner it
 * admitted at sign-up, and of each it erased, which the erasure writes first, so no sign-in
 * resolves an erased owner's name again and the platform never admits it again: slice L);
 * `h:` (an inbound capability URL's token, by its SHA-256) and `i:` (a shared app's
 * installation), each routing to its owner's inbound binding at a revision
 * (`/hooks/inbound`, slice L). Owed: `a:` published app slugs (S6). A sandbox's tokens have
 * no entry: each names its owner itself
 * (G4, `routing-mac.ts`, its MAC verified at the door), and the owner's object checks it.
 * Nothing here, or anywhere, maps a session cookie or its hash to an owner (the binder
 * guard).
 */
import { HEX16, HEX32, HEX64 } from "../../contract/src/ids";
import { principal } from "../../contract/src/text";

import type { DoorsEnv } from "./config";

const KEY_ID = HEX16;

/** `k:<key_id>`: the key's owner, the SHA-256 of its secret, and its mint time. */
export interface KeyEntry {
  readonly o: string;
  readonly h: string;
  readonly c: number;
}

/**
 * `o:<owner>`: the platform's record of an owner: admitted at sign-up (`admitted`), or
 * erased (`erased`), with when. An owner with no record has never signed up.
 */
interface OwnerEntry {
  readonly s: "admitted" | "erased";
  readonly t: number;
}

/**
 * `h:<sha256 of the token>` or `i:<app>:<kind>:<sha256 of the installation id>`: the owner
 * an inbound route names, its binding there (`owner/src/inbound.ts`) and the binding's
 * revision when the entry was written.
 */
export interface RouteEntry {
  readonly o: string;
  readonly b: string;
  readonly r: number;
}

const ROUTE = /^(?:h:[0-9a-f]{64}|i:[a-z][a-z0-9_]{0,31}:(?:workspace|enterprise):[0-9a-f]{64})$/;

function ownerName(owner: string): string {
  return `o:${principal(owner)}`;
}

/** The platform's record of `owner`, or null when it never signed up. Malformed throws. */
export async function readOwner(env: DoorsEnv, owner: string): Promise<OwnerEntry | null> {
  const value: unknown = await env.DIRECTORY.get(ownerName(owner), { type: "json" });
  if (value === null) {
    return null;
  }
  if (!exactly(value, "s,t") || (value["s"] !== "admitted" && value["s"] !== "erased")
      || typeof value["t"] !== "number" || !Number.isSafeInteger(value["t"])) {
    throw new Error("the directory's record of an owner is malformed");
  }
  return { s: value["s"], t: value["t"] };
}

export async function putOwner(env: DoorsEnv, owner: string, entry: OwnerEntry): Promise<void> {
  await env.DIRECTORY.put(ownerName(owner), JSON.stringify({ s: entry.s, t: entry.t }));
}

function routeName(route: string): string {
  if (!ROUTE.test(route)) {
    throw new Error("an inbound route is h:<64 hex> or i:<app>:<kind>:<64 hex>");
  }
  return route;
}

/** The entry an inbound route names, or null. A malformed entry is a bug: it throws. */
export async function readRoute(env: DoorsEnv, route: string): Promise<RouteEntry | null> {
  const value: unknown = await env.DIRECTORY.get(routeName(route), { type: "json" });
  if (value === null) {
    return null;
  }
  if (!exactly(value, "b,o,r") || typeof value["b"] !== "string" || !HEX32.test(value["b"])
      || typeof value["r"] !== "number" || !Number.isSafeInteger(value["r"]) || value["r"] < 1) {
    throw new Error("the directory's entry for an inbound route is malformed");
  }
  return { o: principal(value["o"]), b: value["b"], r: value["r"] };
}

export async function putRoute(env: DoorsEnv, route: string, entry: RouteEntry): Promise<void> {
  await env.DIRECTORY.put(routeName(route), JSON.stringify({ o: entry.o, b: entry.b,
                                                            r: entry.r }));
}

export async function deleteRoute(env: DoorsEnv, route: string): Promise<void> {
  await env.DIRECTORY.delete(routeName(route));
}

function keyName(keyId: string): string {
  if (!KEY_ID.test(keyId)) {
    throw new Error("a key id is 16 hex");
  }
  return `k:${keyId}`;
}

function exactly(value: unknown, keys: string): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && Object.keys(value).sort().join() === keys;
}

/** A key's entry, or null when the directory has none. A malformed entry is a bug: it throws. */
export async function readKey(env: DoorsEnv, keyId: string): Promise<KeyEntry | null> {
  const value: unknown = await env.DIRECTORY.get(keyName(keyId), { type: "json" });
  if (value === null) {
    return null;
  }
  if (!exactly(value, "c,h,o") || typeof value["h"] !== "string" || !HEX64.test(value["h"])
      || typeof value["c"] !== "number" || !Number.isSafeInteger(value["c"]) || value["c"] < 0) {
    throw new Error(`the directory's entry for key ${keyId} is malformed`);
  }
  return { o: principal(value["o"]), h: value["h"], c: value["c"] };
}

export async function putKey(env: DoorsEnv, keyId: string, entry: KeyEntry): Promise<void> {
  await env.DIRECTORY.put(keyName(keyId), JSON.stringify({ o: entry.o, h: entry.h, c: entry.c }));
}

export async function deleteKey(env: DoorsEnv, keyId: string): Promise<void> {
  await env.DIRECTORY.delete(keyName(keyId));
}
