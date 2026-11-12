/**
 * `/api` keys (research §5; checklist 13, 14, 15), ported from `tinyassets/api_keys.py`.
 *
 * The key is the Python's, exactly, so keys imported at cutover keep working:
 * `ta_key_<16 hex id>_<43 urlsafe secret>`, the id `secrets.token_hex(8)` and the secret
 * `secrets.token_urlsafe(32)`, and only the SHA-256 hex of the secret's UTF-8 is stored,
 * compared in constant time (a 256-bit random secret needs no KDF). The directory's
 * `k:<id>` holds the owner, that hash and the mint time; the owner's object holds the key's
 * grant row (scopes, revoke) and its key-store row (`owner/src/keys.ts`: id, name, mint
 * time, last use), so the owner's key list and its erasure enumerate the keys.
 *
 * - **Mint**, from the owner's browser in session: the object's `keyMint`, one unit that
 *   counts the live keys strictly (`MAX_KEYS`, the owner-set clients' own bound), sets the
 *   grant (the key's own origin, source `api_key`, scoped, revision 0), applies an agent's
 *   held key request with it when the owner names one (`proposal`), and writes the key's
 *   row; then the directory entry, then the secret, once. A refused mint writes no entry;
 *   an entry that fails to write leaves a grant the owner can revoke.
 * - **Use**: parse, read the entry, compare hashes, then the kill switch. A wrong key is
 *   `401` before any object is reached. The binding's origin is `apiKeyOrigin(id)` and
 *   its sign-in the mint time, so a revoked key never passes the fence.
 * - **Revoke**: `grantRevoke` first, so the object refuses the key at once, then the
 *   directory entry goes, so dead keys stop at the edge.
 * - **Delete**: revoked first, then the directory entry, then `grantForget`, which the
 *   object allows for an API key only. An MCP client is never forgotten this way.
 *
 * A key the directory names for another owner is "not found" to this one; nothing of
 * it is touched.
 *
 * No per-key or per-owner request cap (the founder's rule: no invented limits): a key's
 * calls are bounded by the tier's simultaneous runs, the owner's own settings and vendors'
 * limits.
 */
import { HEX16 } from "../../contract/src/ids";

import { bindApiKey, type Bound } from "./binder";
import { apiKeyOrigin } from "../../contract/src/binding";
import type { DoorsEnv } from "./env";
import { deleteKey, type KeyEntry, putKey, readKey } from "./directory";

export const PREFIX = "ta_key_";
const KEY = /^ta_key_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$/;

export class KeyRefused extends Error {
  override readonly name = "KeyRefused";
  readonly status: 400 | 403 | 404;

  constructor(status: 400 | 403 | 404, message: string) {
    super(message);
    this.status = status;
  }
}

export function looksLikeKey(token: string): boolean {
  return token.startsWith(PREFIX);
}

/** The key's id and secret, or null for anything not exactly a key. */
export function parseKey(token: string): { keyId: string; secret: string } | null {
  const match = KEY.exec(token);
  return match === null ? null : { keyId: match[1]!, secret: match[2]! };
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The stored digest: SHA-256 hex of the secret's UTF-8 (`api_keys._digest`). */
export async function secretHash(secret: string): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(secret))));
}

/** `secrets.token_hex(8)` and `secrets.token_urlsafe(32)`. */
export function newKey(): { keyId: string; secret: string; key: string } {
  const keyId = hex(crypto.getRandomValues(new Uint8Array(8)));
  const secret = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return { keyId, secret, key: `${PREFIX}${keyId}_${secret}` };
}

function equal(a: string, b: string): boolean {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);
  return left.byteLength === right.byteLength && crypto.subtle.timingSafeEqual(left, right);
}

/** The owner's binding for a key whose secret matches its entry, or null. */
export async function verifyKey(env: DoorsEnv, token: string): Promise<Bound | null> {
  const parsed = parseKey(token);
  if (parsed === null) {
    return null;
  }
  const entry = await readKey(env, parsed.keyId);
  if (entry === null || !equal(entry.h, await secretHash(parsed.secret))) {
    return null;
  }
  return bindApiKey(env, parsed.keyId, entry);
}

function keyId(value: unknown): string {
  if (typeof value !== "string" || !HEX16.test(value)) {
    throw new KeyRefused(400, "key_id is 16 hex");
  }
  return value;
}

/** The directory's entry for the owner's key, null when absent; another owner's is not found. */
async function ownEntry(env: DoorsEnv, owner: string, id: string): Promise<KeyEntry | null> {
  const entry = await readKey(env, id);
  if (entry !== null && entry.o !== owner) {
    throw new KeyRefused(404, "API key not found");
  }
  return entry;
}

interface Minted {
  readonly key: string;
  readonly key_id: string;
  readonly name: string;
  readonly created_at: number;
  readonly scopes: unknown;
}

/**
 * Mint a key for the bound owner: the object's mint (its row, its grant, the count, a held
 * request applied), then the directory entry, then the secret once.
 */
export async function mintKey(env: DoorsEnv, { binding, stub }: Bound, request: unknown):
    Promise<Minted> {
  const { name, scopes, proposal } = (request ?? {}) as { name?: unknown; scopes?: unknown;
                                                          proposal?: unknown };
  if (typeof name !== "string" || name.trim() === "") {
    throw new KeyRefused(400, "key name required (at most 80 characters)");
  }
  const minted = newKey();
  const row = await stub.keyMint(binding, {
    key_id: minted.keyId, name, scopes,
    ...(proposal === undefined ? {} : { proposal }) });
  await putKey(env, minted.keyId,
               { o: binding.owner, h: await secretHash(minted.secret), c: row.created_at });
  return { key: minted.key, key_id: minted.keyId, name: row.name, created_at: row.created_at,
           scopes: row.scopes };
}

/** Revoke the owner's key: the object first, then the directory entry. */
export async function revokeKey(env: DoorsEnv, { binding, stub }: Bound, request: unknown):
    Promise<{ key_id: string; revoked: boolean }> {
  const id = keyId((request as { key_id?: unknown } | null)?.key_id);
  const entry = await ownEntry(env, binding.owner, id);
  const done = await stub.grantRevoke(binding, { client: apiKeyOrigin(id).client });
  if (!done.revoked && entry === null) {
    throw new KeyRefused(404, "API key not found");
  }
  if (entry !== null) {
    await deleteKey(env, id);
  }
  return { key_id: id, revoked: done.revoked };
}

/** Delete the owner's key: revoked, then its directory entry, then its grant forgotten. */
export async function deleteOwnKey(env: DoorsEnv, { binding, stub }: Bound, request: unknown):
    Promise<{ key_id: string; deleted: boolean }> {
  const id = keyId((request as { key_id?: unknown } | null)?.key_id);
  const entry = await ownEntry(env, binding.owner, id);
  const client = apiKeyOrigin(id).client;
  await stub.grantRevoke(binding, { client });
  if (entry !== null) {
    await deleteKey(env, id);
  }
  const { forgotten } = await stub.grantForget(binding, { client });
  return { key_id: id, deleted: forgotten || entry !== null };
}

/** The owner's own entry for a key it registers an agent for, or refused. */
export async function registeringEntry(env: DoorsEnv, owner: string, value: unknown):
    Promise<{ keyId: string; entry: KeyEntry }> {
  const id = keyId(value);
  const entry = await readKey(env, id);
  if (entry === null || entry.o !== owner) {
    throw new KeyRefused(403, "the API key is not this owner's live key");
  }
  return { keyId: id, entry };
}
