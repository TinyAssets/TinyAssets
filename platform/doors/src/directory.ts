/**
 * The owner directory (research §3): one KV namespace, `DIRECTORY`, a routing hint only.
 *
 * It maps a credential's id to its owner. It holds no owner content: hashes, owner ids,
 * opaque ids and times. The `c:control` record of platform gates lives in a namespace of its
 * own (`CONTROL`), which the owner Worker binds too, so the owner's object reads the gates
 * at code mode's calls (`owner/src/control-gate.ts`) and never this directory. Every security
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
 * (`/hooks/inbound`, slice L). `readControl` reads `c:control` from `CONTROL` (its gates,
 * and since slice R the tier numbers, `tiers`, which the founder edits there with no
 * deploy). Owed: `a:` published app slugs (S6). A sandbox's tokens have no entry: each names its owner itself
 * (G4, `routing-mac.ts`, its MAC verified at the door), and the owner's object checks it.
 * Nothing here, or anywhere, maps a session cookie or its hash to an owner (the binder
 * guard).
 */
import { principal } from "../../owner/src/text";
import { parseTiers, type Tiers } from "../../owner/src/tier";

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
 * `o:<owner>`: the platform's record of an owner: admitted at sign-up (`admitted`), or
 * erased (`erased`), with when. An owner with no record has never signed up.
 */
export interface OwnerEntry {
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

const HEX32 = /^[0-9a-f]{32}$/;
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

/**
 * The platform gates (research §7): `outside` is the outside kill switch (the Python's
 * `TINYASSETS_OUTSIDE_DENY` and durable switch), `ordinary` the maintenance gate (all
 * owners, the canary owners only, or none), `inbound` the hooks gate (`/hooks/inbound`
 * serves nothing unless it is on, `inbound.ts`).
 */
export interface Control {
  readonly outside: "on" | "off";
  readonly ordinary: "all" | "canary" | "none";
  readonly canary: readonly string[];
  readonly inbound: boolean;
  /**
   * The tier numbers (ADR-034 decision 8; `owner/src/tier.ts`): each tier's simultaneous
   * runs and storage, which the founder edits here with no deploy, and the binder hands
   * the run doors. Null when the record holds none: every run is then refused.
   */
  readonly tiers: Tiers | null;
  /**
   * The sandbox's platform numbers (slice S; the founder's rule of 2026-11-03), which the
   * founder edits here with no deploy and the sandbox Worker reads at each start: null
   * when the record holds none, and the sandbox Worker then starts nothing.
   */
  readonly sandbox: SandboxCeilings | null;
}

/**
 * The sandbox's numbers, as data (`sandbox/src/control.ts`): how long one sandbox may run
 * before it is paused and resumed (`sandbox_runtime_ceiling_s`: Hobby's 1 hour, Pro's 24),
 * and how many sandboxes the E2B team may run at once (`sandbox_concurrency_ceiling`: Hobby
 * 20, Pro 100 and more with add-ons), both the vendor plan's own ceilings, which raising
 * needs the plan raised first, or E2B refuses; and how long before the runtime ceiling a
 * renewal looks for a moment its CLIs are quiet (`sandbox_renewal_window_s`), the founder's
 * choice of how long a turn may run on before a renewal cuts into it. Each is a positive
 * whole number, the window shorter than the ceiling; the record bounds nothing else.
 */
export interface SandboxCeilings {
  readonly runtime_ceiling_s: number;
  readonly concurrency_ceiling: number;
  readonly renewal_window_s: number;
}

const SANDBOX_KEYS: readonly string[] = Object.freeze([
  "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling", "sandbox_renewal_window_s"]);

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

/** The three numbers checked, or a throw. */
export function sandboxCeilings(runtime: unknown, concurrency: unknown,
                                window: unknown): SandboxCeilings {
  if (!positive(runtime) || !positive(concurrency) || !positive(window) || window >= runtime) {
    throw new Error("the sandbox numbers are positive whole numbers, the renewal window " +
                    "shorter than the runtime ceiling");
  }
  return Object.freeze({ runtime_ceiling_s: runtime, concurrency_ceiling: concurrency,
                         renewal_window_s: window });
}

/** The record's keys: the gates always; the tiers, and the sandbox's numbers, optional. */
const CONTROL_KEYS: readonly string[] = Object.freeze(["canary", "inbound", "ordinary",
                                                       "outside"]);
const CONTROL_OPTIONAL: readonly string[] = Object.freeze([
  "tiers", "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling",
  "sandbox_renewal_window_s"]);

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
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const keys = Object.keys(value);
  if (!CONTROL_KEYS.every((key) => keys.includes(key))
      || !keys.every((key) => CONTROL_KEYS.includes(key) || CONTROL_OPTIONAL.includes(key))) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const { outside, ordinary, canary, inbound, tiers } = record;
  const sandbox = SANDBOX_KEYS.map((key) => record[key]);
  if ((outside !== "on" && outside !== "off")
      || (ordinary !== "all" && ordinary !== "canary" && ordinary !== "none")
      || typeof inbound !== "boolean" || !Array.isArray(canary) || canary.length > MAX_CANARY
      || !(sandbox.every((value) => value === undefined)
           || sandbox.every((value) => value !== undefined))) {
    return null;
  }
  try {
    // A record whose tiers or sandbox numbers are there but malformed is malformed whole:
    // never a guess.
    return { outside, ordinary, inbound, canary: Object.freeze(canary.map(principal)),
             tiers: tiers === undefined ? null : parseTiers(tiers),
             sandbox: sandbox[0] === undefined ? null
               : sandboxCeilings(sandbox[0], sandbox[1], sandbox[2]) };
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

/** The record in its own namespace (`CONTROL`: the doors' and the owner Worker's). */
export async function readControl(store: KVNamespace): Promise<ControlRead> {
  let value: unknown;
  try {
    value = await store.get(CONTROL, { type: "json" });
  } catch (error) {
    console.error("the control record is unreadable", error);
    return { kind: "unreadable" };
  }
  if (value === null) {
    return { kind: "missing" };
  }
  const control = parseControl(value);
  if (control === null) {
    console.error("the control record is malformed");
    return { kind: "malformed" };
  }
  return { kind: "read", control };
}
