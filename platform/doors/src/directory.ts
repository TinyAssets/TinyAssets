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
 * This slice writes `k:` (API keys) and reads `c:control`. Owed by later slices, each in
 * its task: `h:` hook tokens and `i:` shared-app installs (`/hooks/inbound`), `a:`
 * published app slugs (S6), `l:` sandbox lease tokens (6). Nothing here, or anywhere,
 * maps a session cookie or its hash to an owner (the binder guard).
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
