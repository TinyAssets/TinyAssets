/**
 * The owner directory (research §3): one KV namespace, `DIRECTORY`, a routing hint only.
 *
 * It maps a credential's id to its owner, plus one `c:control` record of platform gates.
 * It holds no owner content: hashes, owner ids, opaque ids and times. Every security
 * decision is made again in the owner's object, which is strongly consistent and on the
 * path, so KV's lag of up to 60 s can only route a dead credential to a refusal:
 * - a key's revoke lands in the object (`grantRevoke`) before its entry is deleted;
 * - a deleted key's grant is forgotten (`grantForget`) after, and the object refuses an
 *   API key with no grant row, never records one.
 *
 * It holds `k:` (API keys), `l:` (sandbox lease tokens, G4) and reads `c:control`. Owed
 * by later slices, each in its task: `h:` hook tokens and `i:` shared-app installs
 * (`/hooks/inbound`), `a:` published app slugs (S6). Nothing here, or anywhere, maps a
 * session cookie or its hash to an owner (the binder guard).
 *
 * A lease token's entry, `l:<sha256 hex of the token>`, is `{o, l, e}`: the owner, the
 * lease id and the lease's expiry. The owner's object holds the truth (the lease row with
 * the token's hash, its id, epoch and expiry), so the entry is a hint, as a key's is: it is
 * written after the object took the lease and rewritten after each renewal, and a close
 * lands in the object before the entry is deleted. KV expires it a minute past its expiry,
 * so a lapsed lease leaves nothing behind.
 */
import { principal } from "../../owner/src/text";

import type { DoorsEnv } from "./config";

const HEX64 = /^[0-9a-f]{64}$/;
const KEY_ID = /^[0-9a-f]{16}$/;
const CONTROL = "c:control";
/** Canary owners the maintenance gate admits; far past a real canary. */
const MAX_CANARY = 100;

/** `k:<key_id>`: the key's owner, the SHA-256 of its secret, and its mint time. */
export interface KeyEntry {
  readonly o: string;
  readonly h: string;
  readonly c: number;
}

/**
 * The platform gates (research §7): `outside` is the outside kill switch (the Python's
 * `TINYASSETS_OUTSIDE_DENY` and durable switch), `ordinary` the maintenance gate (all
 * owners, the canary owners only, or none), `inbound` the hooks gate (owed with
 * `/hooks/inbound`).
 */
export interface Control {
  readonly outside: "on" | "off";
  readonly ordinary: "all" | "canary" | "none";
  readonly canary: readonly string[];
  readonly inbound: boolean;
}

/** `l:<sha256 hex of a lease token>`: the lease's owner, its id and its expiry (whole seconds). */
export interface LeaseEntry {
  readonly o: string;
  readonly l: string;
  readonly e: number;
}

/** How long past its expiry KV keeps a lease's entry (KV's least TTL is a minute). */
const LEASE_KEPT_S = 60;
const LEASE_ID = /^[0-9a-f]{32}$/;

function leaseName(tokenHash: string): string {
  if (!HEX64.test(tokenHash)) {
    throw new Error("a lease token's hash is 64 hex");
  }
  return `l:${tokenHash}`;
}

/** A lease token's entry, or null. A malformed entry is a bug: it throws. */
export async function readLease(env: DoorsEnv, tokenHash: string): Promise<LeaseEntry | null> {
  const value: unknown = await env.DIRECTORY.get(leaseName(tokenHash), { type: "json" });
  if (value === null) {
    return null;
  }
  if (!exactly(value, "e,l,o") || typeof value["l"] !== "string" || !LEASE_ID.test(value["l"])
      || typeof value["e"] !== "number" || !Number.isSafeInteger(value["e"]) || value["e"] < 0) {
    throw new Error("the directory's entry for a lease token is malformed");
  }
  return { o: principal(value["o"]), l: value["l"], e: value["e"] };
}

/** The entry written, kept by KV until a minute past the lease's expiry. */
export async function putLease(env: DoorsEnv, tokenHash: string, entry: LeaseEntry,
                               now: number): Promise<void> {
  await env.DIRECTORY.put(leaseName(tokenHash),
                          JSON.stringify({ o: entry.o, l: entry.l, e: entry.e }),
                          { expirationTtl: Math.max(entry.e - now, 0) + LEASE_KEPT_S });
}

export async function deleteLease(env: DoorsEnv, tokenHash: string): Promise<void> {
  await env.DIRECTORY.delete(leaseName(tokenHash));
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

/** The record parsed exactly, or null when it is anything else. */
export function parseControl(value: unknown): Control | null {
  if (!exactly(value, "canary,inbound,ordinary,outside")) {
    return null;
  }
  const { outside, ordinary, canary, inbound } = value;
  if ((outside !== "on" && outside !== "off")
      || (ordinary !== "all" && ordinary !== "canary" && ordinary !== "none")
      || typeof inbound !== "boolean" || !Array.isArray(canary) || canary.length > MAX_CANARY) {
    return null;
  }
  try {
    return { outside, ordinary, inbound, canary: Object.freeze(canary.map(principal)) };
  } catch {
    return null;
  }
}

/**
 * The `control` record as read: there and well formed, missing, malformed (an operator's
 * bad write), or unreadable (an outage, or a value that is not JSON). The gates tell them
 * apart (`control.ts`); the last two are reported.
 */
export type ControlRead =
  | { readonly kind: "read"; readonly control: Control }
  | { readonly kind: "missing" }
  | { readonly kind: "malformed" }
  | { readonly kind: "unreadable" };

export async function readControl(env: DoorsEnv): Promise<ControlRead> {
  let value: unknown;
  try {
    value = await env.DIRECTORY.get(CONTROL, { type: "json" });
  } catch (error) {
    console.error("the directory's control record is unreadable", error);
    return { kind: "unreadable" };
  }
  if (value === null) {
    return { kind: "missing" };
  }
  const control = parseControl(value);
  if (control === null) {
    console.error("the directory's control record is malformed");
    return { kind: "malformed" };
  }
  return { kind: "read", control };
}
