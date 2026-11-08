/**
 * The cutover import's queue message (`import_research.md` §3, slices 3 and 4): what the
 * importer's job puts on the import queue for each owner (`platform/import/stage.ts`), and
 * what the doors' consumer takes (`doors/src/import.ts`). One module, so the two cannot
 * disagree; deleted at the cutover with both.
 *
 * A message is `{owner, bundle, prefix, mac}`, and a consumer's own continuation adds
 * `cursor`:
 * - `owner`, the owner's name (the old principal), whose object the bundle goes into;
 * - `bundle`, the SHA-256 of the bundle's `bundle.json` (`import/bundle.ts`), which names
 *   every other part by its own SHA-256;
 * - `prefix`, where the bundle sits in the staging bucket: `<run>/<key>`;
 * - `mac`, HMAC-SHA-256 under the window's `IMPORT_KEY` over the owner and the bundle's
 *   digest, base64url: the key is the doors' Worker secret, set only for the window, and the
 *   job's input, so a stray or forged message names no bundle the doors take;
 * - `cursor`, the step and entry a continuation goes on from: a place to skip to, never an
 *   authority (every step is idempotent from the owner's own state).
 */
import { createHmac, timingSafeEqual } from "node:crypto";

import { HEX64 } from "./ids";
import { principal } from "./text";

/**
 * The import doors' bounds (`owner/src/import.ts`), which the consumer batches by: a call's
 * most rows, and its most bytes encoded (the export's page); a call's most folders and
 * inline files, their bytes held to the same; and the most import uploads open at once,
 * under the files plane's `MAX_OPEN_INTENTS` (64).
 */
export const IMPORT_ROWS = 256;
export const IMPORT_BATCH_BYTES = 1024 * 1024;
export const IMPORT_FILES = 256;
export const IMPORT_INTENTS = 8;

/** The import queue, the doors' producer's and consumer's. */
export const IMPORT_QUEUE = "tinyassets-import";
/** The window's secret, by name: the doors' Worker secret, and the job's input. */
export const IMPORT_KEY = "IMPORT_KEY";
/** The shortest key, in bytes once decoded. */
export const IMPORT_KEY_MIN_BYTES = 32;
const DOMAIN = "tinyassets-import-v1";
const KEY = /^[A-Za-z0-9_-]+$/;
/** A run's id (the staging bucket's top folder), and an owner's key (`sha256(principal)[:16]`). */
export const RUN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const PREFIX = /^[a-z0-9][a-z0-9-]{0,63}\/[0-9a-f]{16}$/;
const MAC = /^[A-Za-z0-9_-]{43}$/;

/** Where a continuation goes on from: the bundle's step, and the entry within it. */
export interface Cursor {
  readonly step: number;
  readonly entry: number;
}

export interface ImportMessage {
  readonly owner: string;
  readonly bundle: string;
  readonly prefix: string;
  readonly mac: string;
  readonly cursor?: Cursor;
}

/** The key's bytes, or a loud failure: a key shorter than the window's, or not base64url. */
export function importKeyBytes(key: string): Buffer {
  if (!KEY.test(key) || Buffer.from(key, "base64url").byteLength < IMPORT_KEY_MIN_BYTES) {
    throw new Error(`${IMPORT_KEY} is at least ${IMPORT_KEY_MIN_BYTES} bytes, base64url`);
  }
  return Buffer.from(key, "base64url");
}

/** The message's MAC: HMAC-SHA-256 under `key` over the owner and the bundle's digest. */
export function importMac(key: string, owner: string, bundle: string): string {
  return createHmac("sha256", importKeyBytes(key))
    .update(`${DOMAIN}\0${principal(owner)}\0${bundle}`).digest("base64url");
}

/** Whether `message`'s MAC is its owner's and bundle's under `key`, compared in constant time. */
export function macHolds(key: string, message: ImportMessage): boolean {
  const want = Buffer.from(importMac(key, message.owner, message.bundle), "base64url");
  const got = Buffer.from(message.mac, "base64url");
  return got.byteLength === want.byteLength && timingSafeEqual(got, want);
}

const whole = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

/** A message in its exact shape, or a throw: no field more, none missing, each its grammar. */
export function importMessage(value: unknown): ImportMessage {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("an import message is an object");
  }
  const body = value as Record<string, unknown>;
  const keys = Object.keys(body).sort().join();
  if (keys !== "bundle,mac,owner,prefix" && keys !== "bundle,cursor,mac,owner,prefix") {
    throw new Error("an import message is its owner, bundle, prefix and mac, and a cursor");
  }
  const owner = principal(body["owner"]);
  const { bundle, prefix, mac, cursor } = body;
  if (typeof bundle !== "string" || !HEX64.test(bundle) || typeof prefix !== "string"
      || !PREFIX.test(prefix) || typeof mac !== "string" || !MAC.test(mac)) {
    throw new Error("an import message's bundle, prefix or mac is out of shape");
  }
  if (cursor === undefined) {
    return { owner, bundle, prefix, mac };
  }
  const at = cursor as Record<string, unknown> | null;
  if (typeof at !== "object" || at === null || Object.keys(at).sort().join() !== "entry,step"
      || !whole(at["step"]) || !whole(at["entry"])) {
    throw new Error("an import message's cursor is a step and an entry");
  }
  return { owner, bundle, prefix, mac, cursor: { step: at["step"], entry: at["entry"] } };
}
