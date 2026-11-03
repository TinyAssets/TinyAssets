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
 * This slice writes `k:` (API keys) and reads `c:control` (its gates, and since slice R the
 * tier numbers, `tiers`, which the founder edits here with no deploy). Owed by later slices, each in
 * its task: `h:` hook tokens and `i:` shared-app installs (`/hooks/inbound`), `a:`
 * published app slugs (S6). A sandbox's tokens have no entry: each names its owner itself
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
 * The E2B plan's two numbers, as data (`sandbox/src/control.ts`): how long one sandbox may
 * run before it is paused and resumed (`sandbox_runtime_ceiling_s`: Hobby's 1 hour, Pro's
 * 24), and how many sandboxes the project may run at once (`sandbox_concurrency_ceiling`:
 * Hobby 20, Pro 100 and more with add-ons). Both technical ceilings of the vendor's plan:
 * raising either needs the plan raised first, or E2B refuses.
 */
export interface SandboxCeilings {
  readonly runtime_ceiling_s: number;
  readonly concurrency_ceiling: number;
}

/** The longest runtime ceiling a record may name: a week, past any E2B plan's day. */
const RUNTIME_MAX_S = 7 * 24 * 3600;
/** The shortest: a minute, so a renewal's window and its edge are whole seconds. */
const RUNTIME_MIN_S = 60;
/** The most concurrent sandboxes a record may name, far past E2B's add-ons. */
const CONCURRENCY_MAX = 100_000;

/** The two numbers checked, or a throw. */
export function sandboxCeilings(runtime: unknown, concurrency: unknown): SandboxCeilings {
  if (typeof runtime !== "number" || !Number.isSafeInteger(runtime) || runtime < RUNTIME_MIN_S
      || runtime > RUNTIME_MAX_S || typeof concurrency !== "number"
      || !Number.isSafeInteger(concurrency) || concurrency < 1
      || concurrency > CONCURRENCY_MAX) {
    throw new Error("the sandbox ceilings are whole numbers in their bounds");
  }
  return Object.freeze({ runtime_ceiling_s: runtime, concurrency_ceiling: concurrency });
}

/** The record's keys: the gates always; the tiers, and the sandbox's two numbers, optional. */
const CONTROL_KEYS: readonly string[] = Object.freeze(["canary", "inbound", "ordinary",
                                                       "outside"]);
const CONTROL_OPTIONAL: readonly string[] = Object.freeze([
  "tiers", "sandbox_runtime_ceiling_s", "sandbox_concurrency_ceiling"]);

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
  const runtime = record["sandbox_runtime_ceiling_s"];
  const concurrency = record["sandbox_concurrency_ceiling"];
  if ((outside !== "on" && outside !== "off")
      || (ordinary !== "all" && ordinary !== "canary" && ordinary !== "none")
      || typeof inbound !== "boolean" || !Array.isArray(canary) || canary.length > MAX_CANARY
      || (runtime === undefined) !== (concurrency === undefined)) {
    return null;
  }
  try {
    // A record whose tiers or sandbox numbers are there but malformed is malformed whole:
    // never a guess.
    return { outside, ordinary, inbound, canary: Object.freeze(canary.map(principal)),
             tiers: tiers === undefined ? null : parseTiers(tiers),
             sandbox: runtime === undefined ? null : sandboxCeilings(runtime, concurrency) };
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
