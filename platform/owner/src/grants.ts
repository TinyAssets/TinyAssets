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
 * A scoped grant holds exact `{universe, agent, capability}` triples. The capabilities
 * the object's doors check (`CAPABILITIES`): `read_graph` and `write_graph` (the mailbox,
 * its doorbell subscription and the collections), `propose_policy` (an agent's policy
 * proposal), `read_files` and `write_files` (the file doors), `converse` and `run_graph`
 * (the Python's REST scopes for a message to an agent and a run of one, whose doors the
 * runs slice (R) brings back).
 *
 * A grant also names its **tool profile** (`profile`, slice M; `doors/profiles.json`):
 * which of the MCP door's tools the client is served. `custom` (the owner's own choice of
 * tools, its policy's `tools.custom`), since every grant here is a custom connector's; the
 * owner may name `directory` (no `code`). Code mode is the owner's choice that way, and its
 * policy's switch, so no capability stands for it. A profile is authority: changing it
 * moves the generation.
 *
 * Enforced here now:
 * - Every call from an outside client is admitted, in the object that holds its grant,
 *   before any door acts (`admit`): an MCP client's first verified call records it
 *   `full`; an API key's grant is written with the key, so a key with no grant is
 *   refused, never widened. A client whose grant was revoked needs a sign-in made after
 *   the revoke, even once it is granted again.
 * - Only the owner, in its protected session, changes a grant (`set`); revoking one is
 *   a narrowing the owner's account makes without a session (`revoke`), as a key's
 *   revoke does. Both are receipted, and a grant change ends the client's agents' open
 *   wakes and revokes their doorbell subscriptions in the same transaction (the
 *   Python's M3), so the agent subscribes again under the new grant.
 * - A grant has two numbers. Its `revision` is the owner's concurrency token: every set
 *   and every revoke moves it, and a set names the one it was made against
 *   (`expected_revision`), so a set from a stale view is refused, a rename included. Its
 *   `generation` is its authority: it moves only when what the grant holds does, its mode,
 *   scopes or profile (a revoke is a mode), so a set that changes none, a rename, moves
 *   only the revision. "A grant change" below is one that moves the generation.
 * - Work saved under a grant is rechecked with `standing` against the generation it
 *   was stamped with: a changed, revoked or fenced grant refuses it. Stamps carry no
 *   scopes: a scoped grant's are read live from the row, so a rename needs no new stamp.
 *   An MCP client's held registration (`registrations.ts`) carries that stamp and holds
 *   its client's row (`held`), as an agent does, so the free-row bound can never forget
 *   it and turn a standing grant into "no grant".
 *
 * The runs slice (R) stamps an outside client's runs, each call checked with `standing`
 * and the scope's capability (`authority.ts`), a grant change ending them. Owed by a later
 * slice, written into its task: the doorbell rechecks the grant just before it sends (7).
 *
 * The rows stay bounded without ever forgetting a fence (slice 3's review L1, and slice
 * 5's review M1). A row an agent or stamped work holds is kept: it is what that work is
 * checked against. A row the owner set is kept, and only the owner's own act lifts a
 * revoke:
 * - An API key's revoked row is forgotten when the binder reports the directory deleted
 *   the key (`forgetKey`): a narrowing, since an API key with no row is refused, never
 *   recorded, so it needs no session.
 * - An MCP client's revoked row is forgotten only by the owner in its session
 *   (`forgetRevoked`, a widening the object receipts), never by time and never as a side
 *   effect of another change: no refresh-token lifetime bounds how long a pre-revoke
 *   credential lives. Even then the fence stays: every forgotten MCP revoke moves the
 *   owner-wide `grant_fences.mcp_through` (a schema trigger, so no path skips it), and an
 *   MCP client with no row is recorded only from a sign-in known to be after it.
 * Any other row is exactly what its client's next verified call records again, so it is
 * free: past `maxFreeClients` the oldest free row goes, and a new client is never
 * refused. The owner's live set rows are bounded by `maxOwnerClients`, refused at the
 * owner's own `set` with the way out named (revoke one); a revoked row never counts there
 * (slice L: a revoked key never holds a live key's place). Revoked set rows are bounded
 * apart, by `maxOwnerClients` again: past it the oldest revoked row no agent or held
 * registration holds is forgotten, as `forgetRevoked` forgets it (an MCP client's moves the
 * owner-wide fence, so its pre-revoke credentials stay dead). A revoke, a narrowing that
 * must always land, is never refused. Rows agents and stamped
 * work hold are bounded by their own bounds.
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
import { HEX64 } from "../../contract/src/ids";
import { Refusal } from "../../contract/src/refusal";
import { SCOPES_JSON_MAX } from "../../contract/src/limits";
import type { OwnerStorage } from "./gate";
import { appList, connectUrl, type Vendor } from "../../doors/src/apps";
import { CLIENT_PROFILES, DEFAULT_PROFILE, type ProfileName } from "../../doors/src/tools";
import { canonical, plain, type Policy, policyDigest } from "./policy";
import { codePoints, utf8Bytes } from "../../contract/src/text";

export const OUTSIDE_MODES: readonly string[] = Object.freeze(["full", "scoped", "revoked"]);
/** The capabilities a scoped grant's triples name at the object's doors. */
export const CAPABILITIES = Object.freeze(
  ["read_graph", "write_graph", "propose_policy", "read_files", "write_files", "converse",
   "run_graph"] as const);
export type Capability = (typeof CAPABILITIES)[number];
export const OUTSIDE_SOURCES: readonly string[] = Object.freeze(["mcp", "api_key"]);
/** Clients one owner forget names at most. */
const MAX_FORGET = 100;

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
  /** The scopes' JSON, in UTF-8 bytes, as the row keeps it: their only bound. */
  readonly maxScopesBytes: number;
  readonly seenIntervalS: number;
  readonly revokeClockMarginS: number;
}

export const GRANT_LIMITS: GrantLimits = Object.freeze({
  maxFreeClients: 1_000,
  maxOwnerClients: 1_000,
  // The scopes' JSON as the row keeps it: the schema's own ceiling on the column, a frozen
  // bound (the owner-bound guard counts a grant's scopes by it).
  maxScopesBytes: SCOPES_JSON_MAX,
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
  /** The tool profile the client is served (`directory` or `custom`). */
  readonly profile: ProfileName;
}

export type Client = {
  readonly client: string;
  readonly source: string;
  readonly mode: string;
  readonly scopes: Scope[];
  readonly profile: string;
  /** Its authority: moves only with its mode, scopes or profile, a revoke among them. */
  readonly generation: number;
  /** Its concurrency token: moves on every owner set and every revoke. */
  readonly revision: number;
  readonly revoked_at: number | null;
  readonly name: string;
  readonly first_seen: number;
  readonly last_seen: number;
  /**
   * Which of `apps.json`'s vendors the client is, as its sign-in knew it (migration 4,
   * slice BRAIN), or null; `vendor_verified` 1 when its CIMD host verified it.
   */
  readonly vendor: string | null;
  readonly vendor_verified: number;
  /** Whether the owner has set or revoked this grant; kept while it fences. */
  readonly owner_set: number;
  /** The agents registered from this client, which hold its row. */
  readonly agents: number;
  /** Its open stamped work and pending registrations, which hold its row too. */
  readonly held: number;
};

type Stored = Omit<Client, "scopes"> & { scopes: string };

/** Whole seconds as ISO 8601. */
const iso = (seconds: number): string => new Date(seconds * 1000).toISOString();

/**
 * One of the owner's AI apps as any of its agents sees it (`agents_list`): an `apps.json`
 * app, whether a live MCP grant names it (`connections` of them, the latest used at
 * `last_used`), whether a CIMD host verified one, and where its connect step is; or
 * (`id` null) the connected clients no app matched.
 */
export interface ConnectedApp {
  readonly id: string | null;
  readonly name: string;
  readonly connected: boolean;
  readonly connections: number;
  readonly verified: boolean;
  /**
   * When one of its clients last called, ISO 8601 (to `GRANT_LIMITS.seenIntervalS`), or null:
   * an app long unused may have been disconnected in its own app, which TinyAssets is not
   * told, so an agent asks before taking it as connected.
   */
  readonly last_used: string | null;
  readonly connect_url: string | null;
}

export interface GrantChange {
  readonly client: unknown;
  readonly source: unknown;
  readonly mode: unknown;
  readonly scopes: unknown;
  readonly expected_revision: unknown;
  readonly name: unknown;
  /** The tool profile; omitted, the grant keeps its own (a new one, `custom`). */
  readonly profile?: unknown;
}

/** What a grant holds, as one value: equal exactly when `holds` and its profile answer alike. */
function authority(mode: string, held: readonly Scope[], profile: string): string {
  return canonical([mode, [...new Set(held.map((scope) => canonical(scope)))].sort(), profile]);
}

/**
 * `outside_scopes`: exact triples; a wildcard is never a scope, and a capability is one a
 * door checks (`CAPABILITIES`), so a grant never names one that nothing would hold.
 */
export function scopes(value: unknown, limits: GrantLimits = GRANT_LIMITS): Scope[] {
  if (!Array.isArray(value) || value.some((scope) =>
    !plain(scope) || Object.keys(scope).sort().join() !== "agent,capability,universe"
    || Object.values(scope).some((v) => typeof v !== "string" || !v || v === "*"
                                        || codePoints(v) > 512))) {
    throw new Refusal("invalid", "outside scopes require exact universe, agent and capability");
  }
  if (value.some((scope) => !(CAPABILITIES as readonly string[]).includes(
    (scope as Record<string, string>)["capability"]!))) {
    throw new Refusal("invalid", `a scope's capability is one of ${CAPABILITIES.join(", ")}`);
  }
  if (utf8Bytes(JSON.stringify(value)) > limits.maxScopesBytes) {
    throw new Refusal("invalid", `a grant's scopes are at most ${limits.maxScopesBytes} bytes of JSON`);
  }
  return value as Scope[];
}

/** `_outside_client`: a verified client is 64 hex, from an MCP client or an API key. */
function outsideClient(client: unknown, source: unknown = "mcp"): string {
  if (typeof client !== "string" || !HEX64.test(client)) {
    throw new Refusal("invalid", "invalid outside client");
  }
  if (typeof source !== "string" || !OUTSIDE_SOURCES.includes(source)) {
    throw new Refusal("invalid", "invalid outside client source");
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
  readonly #storage: OwnerStorage;
  readonly #policy: Policy;
  readonly #limits: GrantLimits;

  constructor(storage: OwnerStorage, policy: Policy, limits: GrantLimits = GRANT_LIMITS) {
    this.#storage = storage;
    this.#policy = policy;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
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
      throw new Refusal("refused", "outside client has no grant");
    }
    if (row.mode === "revoked") {
      throw new Refusal("refused", "outside client revoked");
    }
    if (row.revoked_at !== null && (authenticatedAt === null
        || authenticatedAt <= row.revoked_at + this.#limits.revokeClockMarginS)) {
      throw new Refusal("refused", "a fresh sign-in is required after a revoke");
    }
    return { mode: row.mode as Grant["mode"], scopes: JSON.parse(row.scopes) as Scope[],
             generation: row.generation, profile: row.profile as ProfileName };
  }

  /**
   * The grant of the verified outside client making this call. An MCP client's first
   * verified call records it `full` (A1), making room by forgetting the least recently
   * seen free row; an API key's row is written with the key. `authenticatedAt` is when
   * the client's credential was signed in, if the binder knows.
   */
  admit(client: string, source: string, authenticatedAt: number | null, now: number,
        vendor: Vendor | null = null): Grant {
    outsideClient(client, source);
    if (vendor !== null && !appList().some((app) => app.id === vendor.id)) {
      throw new Refusal("invalid", "a client's vendor is one of apps.json's");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      let row = this.#row(client);
      if (row === undefined && source === "mcp") {
        // A forgotten revoke still fences: a client with no row is recorded only from a
        // sign-in known to be after the latest one.
        const fence = sql.exec<{ mcp_through: number }>(
          "SELECT mcp_through FROM grant_fences").one().mcp_through;
        if (fence > 0 && (authenticatedAt === null
                          || authenticatedAt <= fence + this.#limits.revokeClockMarginS)) {
          throw new Refusal("refused", "a fresh sign-in is required after a revoke");
        }
        const over = this.#usage().free_clients - this.#limits.maxFreeClients + 1;
        if (over > 0) {
          sql.exec("DELETE FROM outside_clients WHERE client IN (SELECT client FROM " +
                     "outside_clients WHERE owner_set = 0 AND agents = 0 AND held = 0 " +
                     "ORDER BY last_seen LIMIT ?)", over);
        }
        sql.exec("INSERT INTO outside_clients (client, source, mode, scopes, generation, " +
                   "revoked_at, name, first_seen, last_seen, vendor, vendor_verified) " +
                   "VALUES (?, ?, 'full', '[]', 1, NULL, '', ?, ?, ?, ?)", client, source, now,
                 now, vendor?.id ?? null, vendor?.verified === true ? 1 : 0);
        row = this.#row(client);
      } else if (row !== undefined && row.source !== source) {
        throw new Refusal("refused", "outside client source changed");
      } else if (row !== undefined && now - row.last_seen >= this.#limits.seenIntervalS) {
        sql.exec("UPDATE outside_clients SET last_seen = ? WHERE client = ?", now, client);
      }
      if (row !== undefined && row.vendor === null && vendor !== null) {
        // A row recorded before its sign-in named its vendor (an API key's never does).
        sql.exec("UPDATE outside_clients SET vendor = ?, vendor_verified = ? WHERE client = ?",
                 vendor.id, vendor.verified ? 1 : 0, client);
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
      throw new Refusal("refused", "outside client authority changed");
    }
    return grant;
  }

  /**
   * Whether a client's grant is full, the default (the founder's rule: every agent of the
   * owner may do what any other may), not one the owner narrowed or revoked. A read.
   */
  full(client: string): boolean {
    return this.#sql.exec<{ mode: string }>(
      "SELECT mode FROM outside_clients WHERE client = ?", client).toArray()[0]?.mode === "full";
  }

  /** `outside_live`: whether a client's grant stands at all (a row, not revoked). */
  live(client: string): boolean {
    const row = this.#sql.exec<{ mode: string }>(
      "SELECT mode FROM outside_clients WHERE client = ?", client).toArray()[0];
    return row !== undefined && row.mode !== "revoked";
  }

  /**
   * Whether a client's grant, as it stands, holds `scope` (a full grant holds every one):
   * what a wake is held to before it rings (slice W). A read: it never records a client.
   */
  reads(client: string, scope: Scope): boolean {
    const row = this.#row(client);
    return row !== undefined && row.mode !== "revoked"
      && holds({ mode: row.mode as Grant["mode"], scopes: JSON.parse(row.scopes) as Scope[],
                 generation: row.generation, profile: row.profile as ProfileName }, scope);
  }

  /**
   * The owner's connected AI apps, by vendor (`agents_list`'s `apps`): each of `apps.json`'s
   * apps, connected when a live MCP grant names it, with how many and when last seen; and,
   * when there are any, the live MCP grants no app matched, as one line. Bounded by the
   * apps, not the grants. A read.
   */
  apps(): ConnectedApp[] {
    const rows = this.#sql.exec<{ vendor: string | null; n: number; verified: number;
                                  seen: number }>(
      "SELECT vendor, count(*) AS n, max(vendor_verified) AS verified, max(last_seen) AS seen " +
      "FROM outside_clients WHERE source = 'mcp' AND mode != 'revoked' GROUP BY vendor")
      .toArray();
    const by = new Map(rows.map((row) => [row.vendor, row]));
    const listed: ConnectedApp[] = appList().map((app) => {
      const row = by.get(app.id);
      return { id: app.id, name: app.name, connected: row !== undefined,
               connections: row?.n ?? 0, verified: row !== undefined && row.verified === 1,
               last_used: row === undefined ? null : iso(row.seen),
               connect_url: connectUrl(app.id) };
    });
    const other = rows.filter((row) => row.vendor === null
                                       || !appList().some((app) => app.id === row.vendor));
    const others = other.reduce((sum, row) => sum + row.n, 0);
    if (others > 0) {
      listed.push({ id: null, name: "Other apps", connected: true, connections: others,
                    verified: false, last_used: iso(Math.max(...other.map((row) => row.seen))),
                    connect_url: null });
    }
    return listed;
  }

  /** The name the owner gave a client's grant ("" when none, or no row). A read. */
  name(client: string): string {
    return this.#sql.exec<{ name: string }>("SELECT name FROM outside_clients WHERE client = ?",
                                            client).toArray()[0]?.name ?? "";
  }

  list(): Client[] {
    return this.#sql.exec<Stored>(
      "SELECT * FROM outside_clients ORDER BY first_seen, client",
    ).toArray().map((row) => ({ ...row, scopes: JSON.parse(row.scopes) as Scope[] }));
  }

  /**
   * `outside_set`, an owner widening from its protected session: one client's mode, scopes
   * and name, against the revision the owner was shown; `expected_revision` 0 creates the
   * row, as a key's mint does. The revision always moves; the generation only when the
   * authority does (`moved`), which is when the caller ends the client's work.
   */
  set(sessionId: string, change: GrantChange, now: number):
      { client: string; mode: string; scopes: Scope[]; profile: string; generation: number;
        revision: number; moved: boolean } {
    const client = outsideClient(change.client, change.source);
    const { mode, expected_revision: expected, name } = change;
    const source = change.source as string;
    if (typeof mode !== "string" || !OUTSIDE_MODES.includes(mode)) {
      throw new Refusal("invalid", "invalid outside grant mode");
    }
    const held = scopes(change.scopes, this.#limits);
    if ((mode === "scoped") !== (held.length > 0)) {
      throw new Refusal("invalid", "a scoped grant, and only a scoped one, names scopes");
    }
    if (typeof expected !== "number" || !Number.isSafeInteger(expected) || expected < 0) {
      throw new Refusal("invalid", "expected outside grant revision required");
    }
    if (typeof name !== "string" || codePoints(name) > 80) {
      throw new Refusal("invalid", "invalid outside client name");
    }
    if (change.profile !== undefined && !(CLIENT_PROFILES as readonly unknown[])
      .includes(change.profile)) {
      throw new Refusal("invalid", `a grant's profile is one of ${CLIENT_PROFILES.join(", ")}`);
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const row = this.#row(client);
      if ((row?.revision ?? 0) !== expected) {
        throw new Refusal("refused", "outside grant changed");
      }
      if (row !== undefined && row.source !== source) {
        throw new Refusal("refused", "outside client source changed");
      }
      const live = row?.owner_set === 1 && row.mode !== "revoked";
      if (mode !== "revoked" && !live && this.#liveSet() >= this.#limits.maxOwnerClients) {
        throw new Refusal("refused", "the owner's managed outside clients are at their limit; revoke " +
                          "one to make room");
      }
      const profile = (change.profile ?? row?.profile ?? DEFAULT_PROFILE) as string;
      const moved = row === undefined
        || authority(row.mode, JSON.parse(row.scopes) as Scope[], row.profile)
          !== authority(mode, held, profile);
      const generation = (row?.generation ?? 0) + (moved ? 1 : 0);
      const revision = expected + 1;
      // A revoke is timed when it lands; a revoked grant set again keeps its fence.
      const revokedAt = mode === "revoked" && moved ? now : row?.revoked_at ?? null;
      const named = name === "" ? row?.name ?? "" : name;
      sql.exec(
        "INSERT INTO outside_clients (client, source, mode, scopes, generation, revision, " +
          "revoked_at, name, first_seen, last_seen, owner_set, profile) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) " +
          "ON CONFLICT (client) DO UPDATE SET mode = excluded.mode, scopes = excluded.scopes, " +
          "profile = excluded.profile, " +
          "generation = excluded.generation, revision = excluded.revision, " +
          "revoked_at = excluded.revoked_at, owner_set = 1, " +
          "name = CASE WHEN excluded.name = '' THEN name ELSE excluded.name END",
        client, source, mode, canonical(held), generation, revision, revokedAt, name, now, now,
        profile,
      );
      // The receipt's digest names the set whole, so a rename's differs from a set that
      // changes nothing; its generation is the authority's.
      this.#policy.receipt("outside_grant", {
        session_id: sessionId,
        request_digest: policyDigest({ client, mode, scopes: held, name: named, revision,
                                       profile }),
        generation, rule: client, decision: mode, outcome: "committed",
      }, now);
      return { client, mode, scopes: held, profile, generation, revision, moved };
    });
  }

  /** Drop one revoked row nothing holds, receipted; the schema moves the MCP fence. */
  #forget(row: Stored, sessionId: string | null, now: number): void {
    if (row.mode !== "revoked") {
      throw new Refusal("refused", "only a revoked grant is forgotten; revoke it first");
    }
    if (row.agents > 0 || row.held > 0) {
      throw new Refusal("refused", "an agent or a held registration still holds this " +
                        "client's grant");
    }
    this.#sql.exec("DELETE FROM outside_clients WHERE client = ?", row.client);
    this.#policy.receipt("outside_grant", {
      session_id: sessionId, request_digest: policyDigest({ client: row.client,
                                                           mode: "forgotten" }),
      generation: 0, rule: row.client, decision: "forgotten", outcome: "committed",
    }, now);
  }

  /**
   * Forget a revoked API key's row once the binder has deleted the key from the owner
   * directory (so call it only then). A narrowing: a key with no row is refused, never
   * recorded, so the owner's account makes it without a session. An MCP client's revoke
   * is lifted only by the owner in its session (`forgetRevoked`).
   */
  forgetKey(clientId: unknown, now: number): { client: string; forgotten: boolean } {
    const client = outsideClient(clientId, "api_key");
    return this.#storage.transactionSync(() => {
      const row = this.#row(client);
      if (row === undefined) {
        return { client, forgotten: false };
      }
      if (row.source !== "api_key") {
        throw new Refusal("refused", "only the owner, in its session, forgets an MCP client's revoke");
      }
      this.#forget(row, null, now);
      return { client, forgotten: true };
    });
  }

  /**
   * The owner, in its session, forgets revoked clients, one or many, to make room under
   * its bound: a widening, receipted per client. An MCP client forgotten may come back
   * only from a sign-in after its revoke (`grant_fences`); an API key's, not at all.
   */
  forgetRevoked(sessionId: string, clients: unknown, now: number):
      { forgotten: string[]; mcp_fence: number } {
    if (!Array.isArray(clients) || clients.length < 1 || clients.length > MAX_FORGET
        || new Set(clients).size !== clients.length) {
      throw new Refusal("invalid", `forget names 1 to ${MAX_FORGET} distinct outside clients`);
    }
    const ids = clients.map((client) => outsideClient(client));
    return this.#storage.transactionSync(() => {
      const forgotten: string[] = [];
      for (const client of ids) {
        const row = this.#row(client);
        if (row !== undefined) {
          this.#forget(row, sessionId, now);
          forgotten.push(client);
        }
      }
      return { forgotten, mcp_fence: this.#sql.exec<{ mcp_through: number }>(
        "SELECT mcp_through FROM grant_fences").one().mcp_through };
    });
  }

  /**
   * `outside_revoke`, a narrowing: revoke one client's grant now, never refused for a
   * bound. A client with no grant has nothing to revoke; a revoked one stays as it is.
   */
  /** The owner's live set rows: what `maxOwnerClients` bounds at a set. */
  #liveSet(): number {
    return this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM outside_clients WHERE owner_set = 1 AND mode != 'revoked'")
      .one().n;
  }

  /**
   * In the caller's transaction: the revoked set rows past `maxOwnerClients` forgotten,
   * oldest revoke first, any an agent or a held registration holds kept; the clients gone.
   */
  evictRevoked(now: number): string[] {
    const over = this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM outside_clients WHERE owner_set = 1 AND mode = 'revoked'")
      .one().n - this.#limits.maxOwnerClients;
    if (over <= 0) {
      return [];
    }
    const rows = this.#sql.exec<Stored & Record<string, SqlStorageValue>>(
      "SELECT * FROM outside_clients WHERE owner_set = 1 AND mode = 'revoked' AND agents = 0 " +
        "AND held = 0 ORDER BY revoked_at, client LIMIT ?", over).toArray();
    for (const row of rows) {
      this.#forget(row, null, now);
    }
    return rows.map((row) => row.client);
  }

  revoke(clientId: unknown, now: number):
      { client: string; revoked: boolean; generation: number; revision: number } {
    const client = outsideClient(clientId);
    return this.#storage.transactionSync(() => {
      const row = this.#row(client);
      if (row === undefined || row.mode === "revoked") {
        return { client, revoked: row !== undefined, generation: row?.generation ?? 0,
                 revision: row?.revision ?? 0 };
      }
      const generation = row.generation + 1;
      this.#sql.exec("UPDATE outside_clients SET mode = 'revoked', scopes = '[]', " +
                       "generation = ?, revision = revision + 1, revoked_at = ?, " +
                       "owner_set = 1 WHERE client = ?",
                     generation, now, client);
      this.#policy.receipt("outside_grant", {
        request_digest: policyDigest({ client, mode: "revoked" }), generation, rule: client,
        decision: "revoked", outcome: "committed",
      }, now);
      return { client, revoked: true, generation, revision: row.revision + 1 };
    });
  }
}
