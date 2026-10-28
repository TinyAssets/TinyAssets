/**
 * `/api` keys (research §5; checklist 13, 14, 15), ported from `tinyassets/api_keys.py`.
 *
 * The key is the Python's, exactly, so keys imported at cutover keep working:
 * `ta_key_<16 hex id>_<43 urlsafe secret>`, the id `secrets.token_hex(8)` and the secret
 * `secrets.token_urlsafe(32)`, and only the SHA-256 hex of the secret's UTF-8 is stored,
 * compared in constant time (a 256-bit random secret needs no KDF). The directory's
 * `k:<id>` holds the owner, that hash and the mint time; the key's grant row in the
 * owner's object is its metadata (name, scopes, revoke).
 *
 * - **Mint**, from the owner's browser in session: at most 25 live keys, then the grant
 *   (`grantSet`: the key's own origin, source `api_key`, scoped, generation 0), then the
 *   directory entry, then the secret, once. A refused grant writes no entry; an entry
 *   that fails to write leaves a grant the owner can revoke.
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
 * Owed, each in tasks 2: the key store in the owner's object (key ids, names and last
 * use for the owner's list, and the Python's held `_apply_key_request`), which also makes
 * the 25-key count strict (two concurrent mints can pass it by one); the per-key and
 * per-owner rate limits (120 and 600 a minute); and `run_graph` and `converse`, which join
 * the scopes with the agent's doors (5).
 */
import { codePoints } from "../../owner/src/text";

import { apiKeyOrigin, bindApiKey, type Bound } from "./binder";
import { type DoorsEnv, now } from "./config";
import { deleteKey, type KeyEntry, putKey, readKey } from "./directory";

export const PREFIX = "ta_key_";
const KEY = /^ta_key_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$/;
export const MAX_KEYS_PER_OWNER = 25;
const MAX_NAME = 80;

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
  if (typeof value !== "string" || !/^[0-9a-f]{16}$/.test(value)) {
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

export interface Minted {
  readonly key: string;
  readonly key_id: string;
  readonly name: string;
  readonly created_at: number;
  readonly scopes: unknown;
}

/** Mint a key for the bound owner: grant, then directory entry, then the secret once. */
export async function mintKey(env: DoorsEnv, { binding, stub }: Bound, request: unknown):
    Promise<Minted> {
  const { name, scopes } = (request ?? {}) as { name?: unknown; scopes?: unknown };
  const label = typeof name === "string" ? name.trim() : "";
  if (!label || codePoints(label) > MAX_NAME) {
    throw new KeyRefused(400, "key name required (at most 80 characters)");
  }
  const { clients } = await stub.grantList(binding);
  if (clients.filter((c) => c.source === "api_key" && c.mode !== "revoked").length
      >= MAX_KEYS_PER_OWNER) {
    throw new KeyRefused(403, "too many live API keys; revoke one first");
  }
  const minted = newKey();
  const createdAt = now();
  await stub.grantSet(binding, {
    client: apiKeyOrigin(minted.keyId).client, source: "api_key", mode: "scoped",
    scopes: scopes as never, expected_generation: 0, name: label,
  });
  await putKey(env, minted.keyId,
               { o: binding.owner, h: await secretHash(minted.secret), c: createdAt });
  return { key: minted.key, key_id: minted.keyId, name: label, created_at: createdAt, scopes };
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
