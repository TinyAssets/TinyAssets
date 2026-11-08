/**
 * The key store (tasks 2; slice L): each of the owner's API keys, so the owner's key list
 * and its erasure can enumerate them. The directory's `k:<id>` entry holds the key's
 * owner, its secret's hash and its mint time (`doors/src/directory.ts`), and the grant row
 * holds only its client hash (`grants.ts`); this row holds what the owner is shown: the key
 * id, its name, its mint time and its last use, and when it was revoked.
 *
 * - **Mint** (`OwnerDO.keyMint`): the key's grant (`Grants.set`, source `api_key`, scoped,
 *   revision 0) and this row in one unit, the live keys counted in that unit, so two
 *   concurrent mints cannot pass the count by one. The count is the owner's set clients'
 *   own bound (`GRANT_LIMITS.maxOwnerClients`, the 10 GB's share: each key's grant is an
 *   owner-set client), held equal by a test. An agent's held key request (a policy card,
 *   `KEY_REQUEST`) is applied with the grant, once, its scopes the card's (the Python's
 *   `_apply_key_request`).
 * - **Use**: every call an API key makes marks its row's `last_used_at`, at most once a
 *   `USE_INTERVAL_S`, so a key's use is a read most calls (as a grant's `last_seen`).
 * - **Revoke** follows the grant: a revoke, or a set to `revoked`, marks the row in the
 *   same unit. **Forget** follows the grant too: the row goes with its forgotten grant.
 *
 * The rows are bounded by the grants': a row exists only while its key's owner-set grant
 * row does (the owner-bound guard counts `maxOwnerClients` of them).
 */
import { HEX16 } from "../../contract/src/ids";
import { KEY_NAME_MAX, MAX_KEYS } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import { codePoints } from "../../contract/src/text";
import type {
  Call, GrantRequest, OwnerState,
} from "./owner-do";
import { apiKeyOrigin } from "../../contract/src/binding";
import { clock } from "./clock";

/** How stale a key's `last_used_at` may be before a call marks it again. */
export const USE_INTERVAL_S = 60;
/** The policy card an agent files to ask the owner for a key (`policyPropose`). */
export const KEY_REQUEST = "api_key_request";

export const TOO_MANY_KEYS = "too many live API keys; revoke one first";

const KEY_ID = HEX16;

/** One key as the owner is shown it. */
export interface ApiKey {
  readonly key_id: string;
  readonly name: string;
  readonly created_at: number;
  readonly last_used_at: number | null;
  readonly revoked_at: number | null;
}

export function keyId(value: unknown): string {
  if (typeof value !== "string" || !KEY_ID.test(value)) {
    throw new Refusal("invalid", "key_id is 16 hex");
  }
  return value;
}

export function keyName(value: unknown): string {
  const name = typeof value === "string" ? value.trim() : "";
  if (!name || codePoints(name) > KEY_NAME_MAX) {
    throw new Refusal("invalid", `key name required (at most ${KEY_NAME_MAX} characters)`);
  }
  return name;
}

export class Keys {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  /** The owner's keys, newest first: live and revoked, every one the directory may name. */
  list(): ApiKey[] {
    return this.#storage.sql.exec<ApiKey & Record<string, SqlStorageValue>>(
      "SELECT key_id, name, created_at, last_used_at, revoked_at FROM owner_keys " +
        "ORDER BY created_at DESC, key_id").toArray().map((row) => ({ ...row }));
  }

  /** The key ids the directory may hold for this owner: every row's. */
  ids(): string[] {
    return this.#storage.sql.exec<{ key_id: string }>("SELECT key_id FROM owner_keys")
      .toArray().map((row) => row.key_id);
  }

  /**
   * Inside the mint's unit: refused past the count, then the row, `client` its grant's
   * (the key's own origin, `ledger.ts`'s `apiKeyOrigin`). Its grant is the caller's.
   */
  add(id: string, client: string, name: string, now: number): ApiKey {
    const sql = this.#storage.sql;
    const live = sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM owner_keys WHERE revoked_at IS NULL").one().n;
    if (live >= MAX_KEYS) {
      throw new Refusal("refused", TOO_MANY_KEYS);
    }
    if (sql.exec("SELECT 1 FROM owner_keys WHERE key_id = ?", id).toArray().length > 0) {
      throw new Refusal("refused", "this key id is minted already");
    }
    sql.exec("INSERT INTO owner_keys (key_id, client, name, created_at) VALUES (?, ?, ?, ?)",
             id, client, name, now);
    return { key_id: id, name, created_at: now, last_used_at: null, revoked_at: null };
  }

  /** A call by key `id`: its last use, at most once a `USE_INTERVAL_S`. */
  used(id: string, now: number): void {
    this.#storage.sql.exec(
      "UPDATE owner_keys SET last_used_at = ? WHERE key_id = ? AND revoked_at IS NULL " +
        "AND (last_used_at IS NULL OR last_used_at <= ?)", now, id, now - USE_INTERVAL_S);
  }

  /** In the revoke's unit: the key whose grant `client` is, revoked. */
  revoked(client: string, now: number): void {
    this.#storage.sql.exec(
      "UPDATE owner_keys SET revoked_at = ? WHERE client = ? AND revoked_at IS NULL", now, client);
  }

  /** In the forget's unit: the key whose grant `client` is, gone with it. */
  forgotten(client: string): void {
    this.#storage.sql.exec("DELETE FROM owner_keys WHERE client = ?", client);
  }

  /** The key id `client` is, when it is one of the owner's keys. */
  idOf(client: string): string | null {
    return this.#storage.sql.exec<{ key_id: string }>(
      "SELECT key_id FROM owner_keys WHERE client = ?", client).toArray()[0]?.key_id ?? null;
  }
}

/** A key's mint (`keyMint`): its id, name and scopes, and an agent's held request. */
export interface KeyMintRequest {
  readonly key_id: string;
  readonly name: string;
  readonly scopes: unknown;
  readonly proposal?: { readonly request_id: string; readonly digest: string };
}

// -- THE DOORS: the `keys` rows' handlers (`contract/doors.json`) ---------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `keys` doors' handlers, by door. */
export const keysDoors = Object.freeze({
  keyMint(o: OwnerState, { session: id, r }: Call<KeyMintRequest>): ApiKey & { scopes: GrantRequest["scopes"];
      revision: number } {
    const key = keyId(r["key_id"]);
    const name = keyName(r["name"]);
    const now = Math.floor(clock());  // whole seconds: the directory entry's mint time
    return o.storage.transactionSync(() => {
      const row = o.keys.add(key, apiKeyOrigin(key).client, name, now);
      const set = o.grants.set(id, {
        client: apiKeyOrigin(key).client, source: "api_key", mode: "scoped",
        scopes: r["scopes"] as GrantRequest["scopes"], expected_revision: 0, name,
      } as unknown as GrantRequest, now);
      if (r["proposal"] !== undefined) {
        o.policy.applyKeyRequest(r["proposal"], set.scopes, now);
      }
      return { ...row, scopes: set.scopes, revision: set.revision };
    });
  },
  keyList(o: OwnerState, _call: Call<undefined>): { keys: ApiKey[] } {
    return { keys: o.keys.list() };
  },
});
