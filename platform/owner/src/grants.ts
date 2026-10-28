/**
 * Outside-client grants (P0-3, decision A1), in the owner's own object (ADR-032).
 *
 * Ported from the outside-client half of `PolicyStore` on `p0-3-outside-grants` (PR
 * #5343, `tinyassets/broker/policy.py` at be88c9116): `outside_admit`, `outside_check`,
 * `outside_standing`, `outside_live`, `outside_list`, `outside_set`, `outside_revoke`
 * and `_grant`, whose rules it keeps. An outside client has the owner's normal
 * authority by default (`full`), or exact `{universe, agent, capability}` triples
 * (`scoped`), or none (`revoked`).
 *
 * Enforced here now:
 * - Every call from an outside client is admitted, in the object that holds its grant,
 *   before any door acts (`admit`): an MCP client's first verified call records it
 *   `full`; an API key's grant is written with the key, so a key with no grant is
 *   refused, never widened. A client whose grant was revoked needs a sign-in made after
 *   the revoke, even once it is granted again.
 * - Only the owner, in its protected session, changes a grant (`set`); revoking one is
 *   a narrowing the owner's account makes without a session (`revoke`), as a key's
 *   revoke does. Both are receipted, and the object ends the client's agents' open
 *   wakes and revokes their doorbell subscriptions in the same transaction (the
 *   Python's M3), so the agent subscribes again under the new grant.
 * - Work saved under a grant is rechecked with `standing` against the generation it
 *   was stamped with: a changed, revoked or fenced grant refuses it.
 *
 * Owed by later slices, each written into its task: durable work (scheduled turns
 * 3.6, agent runs and launches 5) stamps its outside authority and calls `standing`
 * before it runs; a grant change ends that client's in-flight turns and streams (the
 * Python's `_end_outside_streams`; there are none in the object yet); the doorbell
 * rechecks the grant just before it sends (7).
 *
 * The rows stay bounded without ever forgetting a fence. A row the owner set or an
 * agent holds is kept: a revoked row is what keeps a pre-revoke credential dead, and
 * an agent's row is what its wakes ring under. Any other row is exactly what its
 * client's next verified call records again, so it is free: past `maxFreeClients` the
 * oldest free row goes, and a new client is never refused. Rows the owner sets are
 * bounded by `maxOwnerClients`, refused at the owner's own `set`; a revoke, a narrowing
 * that must always land, is never refused. Rows agents hold are bounded by the agent
 * registry.
 *
 * Declared differences from the Python:
 * - Admission is the object's own, per call, where the grant lives: the Python router's
 *   identity stamp and the box's recheck of it have no counterpart.
 * - The platform's outside kill switch (`TINYASSETS_OUTSIDE_DENY`) is the binder's, from
 *   the owner directory (tasks 2), and the `wakes` lane's (tasks 7): the object has no env.
 * - A held API-key request applied with its grant (`_apply_key_request`) needs the
 *   directory's key ids, so it comes with the key store (tasks 2).
 * - Rows are bounded as above; the Python kept every row.
 */
import { Invalid, Refused } from "./errors";
import { canonical, plain, type Policy, policyDigest } from "./policy";
import { codePoints } from "./text";

export const OUTSIDE_MODES: readonly string[] = Object.freeze(["full", "scoped", "revoked"]);
export const OUTSIDE_SOURCES: readonly string[] = Object.freeze(["mcp", "api_key"]);
const HEX64 = /^[0-9a-f]{64}$/;

/**
 * `seenIntervalS`: a client's `last_seen` is coarse, so admission is a read most calls.
 * `revokeClockMarginS`: a sign-in this close to a revoke counts as before it, since the
 * identity provider's clock and the object's need not agree to the second.
 * `maxFreeClients`: free rows kept, the least recently seen going first; a busy owner's
 * clients re-register often (dynamic client registration), and a free row costs nothing
 * to record again. `maxOwnerClients`: rows the owner has set, far past a real owner's.
 */
export interface GrantLimits {
  readonly maxFreeClients: number;
  readonly maxOwnerClients: number;
  readonly maxScopes: number;
  readonly seenIntervalS: number;
  readonly revokeClockMarginS: number;
}

export const GRANT_LIMITS: GrantLimits = Object.freeze({
  maxFreeClients: 1_000,
  maxOwnerClients: 1_000,
  maxScopes: 64,
  seenIntervalS: 60,
  revokeClockMarginS: 5,
});

export interface Scope {
  readonly universe: string;
  readonly agent: string;
  readonly capability: string;
}

/** The grant a call is admitted under. */
export interface Grant {
  readonly mode: "full" | "scoped" | "revoked";
  readonly scopes: Scope[];
  readonly generation: number;
}

export type Client = {
  readonly client: string;
  readonly source: string;
  readonly mode: string;
  readonly scopes: Scope[];
  readonly generation: number;
  readonly revoked_at: number | null;
  readonly name: string;
  readonly first_seen: number;
  readonly last_seen: number;
  /** Whether the owner has set or revoked this grant; such a row is never forgotten. */
  readonly owner_set: number;
  /** The agents registered from this client, which hold its row. */
  readonly agents: number;
};

type Stored = Omit<Client, "scopes"> & { scopes: string };

export interface GrantChange {
  readonly client: unknown;
  readonly source: unknown;
  readonly mode: unknown;
  readonly scopes: unknown;
  readonly expected_generation: unknown;
  readonly name: unknown;
}

/** `outside_scopes`: exact triples; a wildcard is never a scope. */
export function scopes(value: unknown, limits: GrantLimits = GRANT_LIMITS): Scope[] {
  if (!Array.isArray(value) || value.length > limits.maxScopes || value.some((scope) =>
    !plain(scope) || Object.keys(scope).sort().join() !== "agent,capability,universe"
    || Object.values(scope).some((v) => typeof v !== "string" || !v || v === "*"
                                        || codePoints(v) > 512))) {
    throw new Invalid("outside scopes require exact universe, agent and capability");
  }
  return value as Scope[];
}

/** `_outside_client`: a verified client is 64 hex, from an MCP client or an API key. */
function outsideClient(client: unknown, source: unknown = "mcp"): string {
  if (typeof client !== "string" || !HEX64.test(client)) {
    throw new Invalid("invalid outside client");
  }
  if (typeof source !== "string" || !OUTSIDE_SOURCES.includes(source)) {
    throw new Invalid("invalid outside client source");
  }
  return client;
}

/** Whether a scoped grant holds exactly this triple; `full` holds every one. */
export function holds(grant: Grant, scope: Scope): boolean {
  return grant.mode === "full" || (grant.mode === "scoped" && grant.scopes.some((held) =>
    held.universe === scope.universe && held.agent === scope.agent
    && held.capability === scope.capability));
}

type Usage = { owner_clients: number; free_clients: number };

export class Grants {
  readonly #storage: DurableObjectStorage;
  readonly #policy: Policy;
  readonly #limits: GrantLimits;

  constructor(storage: DurableObjectStorage, policy: Policy, limits: GrantLimits = GRANT_LIMITS) {
    this.#storage = storage;
    this.#policy = policy;
    this.#limits = limits;
  }

  get #sql(): SqlStorage {
    return this.#storage.sql;
  }

  #usage(): Usage {
    return this.#sql.exec<Usage>("SELECT owner_clients, free_clients FROM policy_usage").one();
  }

  #row(client: string): Stored | undefined {
    return this.#sql.exec<Stored>("SELECT * FROM outside_clients WHERE client = ?", client)
      .toArray()[0];
  }

  /** `_grant`: the grant a call is admitted under, or a refusal naming why. */
  #grant(row: Stored | undefined, authenticatedAt: number | null): Grant {
    if (row === undefined) {
      throw new Refused("outside client has no grant");
    }
    if (row.mode === "revoked") {
      throw new Refused("outside client revoked");
    }
    if (row.revoked_at !== null && (authenticatedAt === null
        || authenticatedAt <= row.revoked_at + this.#limits.revokeClockMarginS)) {
      throw new Refused("a fresh sign-in is required after a revoke");
    }
    return { mode: row.mode as Grant["mode"], scopes: JSON.parse(row.scopes) as Scope[],
             generation: row.generation };
  }

  /**
   * The grant of the verified outside client making this call. An MCP client's first
   * verified call records it `full` (A1), making room by forgetting the least recently
   * seen free row; an API key's row is written with the key. `authenticatedAt` is when
   * the client's credential was signed in, if the binder knows.
   */
  admit(client: string, source: string, authenticatedAt: number | null, now: number): Grant {
    outsideClient(client, source);
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      let row = this.#row(client);
      if (row === undefined && source === "mcp") {
        const over = this.#usage().free_clients - this.#limits.maxFreeClients + 1;
        if (over > 0) {
          sql.exec("DELETE FROM outside_clients WHERE client IN (SELECT client FROM " +
                     "outside_clients WHERE owner_set = 0 AND agents = 0 " +
                     "ORDER BY last_seen LIMIT ?)", over);
        }
        sql.exec("INSERT INTO outside_clients (client, source, mode, scopes, generation, " +
                   "revoked_at, name, first_seen, last_seen) " +
                   "VALUES (?, ?, 'full', '[]', 1, NULL, '', ?, ?)", client, source, now, now);
        row = this.#row(client);
      } else if (row !== undefined && row.source !== source) {
        throw new Refused("outside client source changed");
      } else if (row !== undefined && now - row.last_seen >= this.#limits.seenIntervalS) {
        sql.exec("UPDATE outside_clients SET last_seen = ? WHERE client = ?", now, client);
      }
      return this.#grant(row, authenticatedAt);
    });
  }

  /**
   * `outside_standing`: the grant saved work was admitted under, unchanged since (the
   * same `generation`, not revoked, fenced by no revoke after its sign-in); else a
   * refusal. A read: it never records a client.
   */
  standing(clientId: unknown, generation: unknown, authenticatedAt: number | null): Grant {
    const grant = this.#grant(this.#row(outsideClient(clientId)), authenticatedAt);
    if (grant.generation !== generation) {
      throw new Refused("outside client authority changed");
    }
    return grant;
  }

  /** `outside_live`: whether a client's grant stands at all (a row, not revoked). */
  live(client: string): boolean {
    const row = this.#sql.exec<{ mode: string }>(
      "SELECT mode FROM outside_clients WHERE client = ?", client).toArray()[0];
    return row !== undefined && row.mode !== "revoked";
  }

  list(): Client[] {
    return this.#sql.exec<Stored>(
      "SELECT * FROM outside_clients ORDER BY first_seen, client",
    ).toArray().map((row) => ({ ...row, scopes: JSON.parse(row.scopes) as Scope[] }));
  }

  /**
   * `outside_set`, an owner widening from its protected session: one client's mode.
   * `expected_generation` 0 creates the row, as a key's mint does.
   */
  set(sessionId: string, change: GrantChange, now: number):
      { client: string; mode: string; scopes: Scope[]; generation: number } {
    const client = outsideClient(change.client, change.source);
    const { mode, expected_generation: expected, name } = change;
    const source = change.source as string;
    if (typeof mode !== "string" || !OUTSIDE_MODES.includes(mode)) {
      throw new Invalid("invalid outside grant mode");
    }
    const held = scopes(change.scopes, this.#limits);
    if ((mode === "scoped") !== (held.length > 0)) {
      throw new Invalid("a scoped grant, and only a scoped one, names scopes");
    }
    if (typeof expected !== "number" || !Number.isSafeInteger(expected) || expected < 0) {
      throw new Invalid("expected outside grant generation required");
    }
    if (typeof name !== "string" || codePoints(name) > 80) {
      throw new Invalid("invalid outside client name");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const row = this.#row(client);
      if ((row?.generation ?? 0) !== expected) {
        throw new Refused("outside grant changed");
      }
      if (row !== undefined && row.source !== source) {
        throw new Refused("outside client source changed");
      }
      if (row?.owner_set !== 1 && this.#usage().owner_clients >= this.#limits.maxOwnerClients) {
        throw new Refused("the owner's managed outside clients are at their limit");
      }
      const generation = expected + 1;
      const revokedAt = mode === "revoked" ? now : row?.revoked_at ?? null;
      sql.exec(
        "INSERT INTO outside_clients (client, source, mode, scopes, generation, revoked_at, " +
          "name, first_seen, last_seen, owner_set) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1) " +
          "ON CONFLICT (client) DO UPDATE SET mode = excluded.mode, scopes = excluded.scopes, " +
          "generation = excluded.generation, revoked_at = excluded.revoked_at, owner_set = 1, " +
          "name = CASE WHEN excluded.name = '' THEN name ELSE excluded.name END",
        client, source, mode, canonical(held), generation, revokedAt, name, now, now,
      );
      this.#policy.receipt("outside_grant", {
        session_id: sessionId, request_digest: policyDigest({ client, mode, scopes: held }),
        generation, rule: client, decision: mode, outcome: "committed",
      }, now);
      return { client, mode, scopes: held, generation };
    });
  }

  /**
   * `outside_revoke`, a narrowing: revoke one client's grant now, never refused for a
   * bound. A client with no grant has nothing to revoke; a revoked one stays as it is.
   */
  revoke(clientId: unknown, now: number): { client: string; revoked: boolean; generation: number } {
    const client = outsideClient(clientId);
    return this.#storage.transactionSync(() => {
      const row = this.#row(client);
      if (row === undefined || row.mode === "revoked") {
        return { client, revoked: row !== undefined, generation: row?.generation ?? 0 };
      }
      const generation = row.generation + 1;
      this.#sql.exec("UPDATE outside_clients SET mode = 'revoked', scopes = '[]', " +
                       "generation = ?, revoked_at = ?, owner_set = 1 WHERE client = ?",
                     generation, now, client);
      this.#policy.receipt("outside_grant", {
        request_digest: policyDigest({ client, mode: "revoked" }), generation, rule: client,
        decision: "revoked", outcome: "committed",
      }, now);
      return { client, revoked: true, generation };
    });
  }
}
