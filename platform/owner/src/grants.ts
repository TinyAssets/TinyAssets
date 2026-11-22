/**
 * Outside-client grants, in the owner's own object (ADR-032). An outside client has the
 * owner's normal authority by default (`full`), or exact `{universe, agent, capability}`
 * triples (`scoped`), or none (`revoked`).
 *
 * A scoped grant holds exact `{universe, agent, capability}` triples. The capabilities
 * the object's doors check (`RUN_CAPABILITIES`): `read_graph` and `write_graph` (the mailbox,
 * its doorbell subscription and the collections), `propose_policy` (an agent's policy
 * proposal), `read_files` and `write_files` (the file doors), `converse` and `run_graph`
 * (a message to an agent and a run of one).
 *
 * A grant also names its **tool profile** (`profile`, `contract/profiles.json`):
 * which of the MCP door's tools the client is served. `custom` (the owner's own choice of
 * tools, its policy's `tools.custom`), since every grant here is a custom connector's; the
 * owner may name `directory` (no `code`). Code mode is the owner's choice that way, and its
 * policy's switch, so no capability stands for it. A profile is authority: changing it
 * moves the generation.
 *
 * And a grant names **which of the owner's agents** the client is (`agent`, `MAIN_AGENT` until
 * the owner names another in `set`): the agent whose definition it reads (`definitionRead`)
 * and where a scoped grant's triples are held when a call names no agent
 * (`authority.ts` `actingAgent`). An agent is authority too: changing it moves the generation.
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
 *   wakes and revokes their doorbell subscriptions in the same transaction, so the agent
 *   subscribes again under the new grant.
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
 * An outside client's runs are stamped, each call checked with `standing` and the scope's
 * capability (`authority.ts`), a grant change ending them; the doorbell rechecks the grant
 * just before it sends (`GrantReader.live`).
 *
 * The rows stay bounded without ever forgetting a fence. A row an agent or stamped work holds is
 * kept: it is what that work is checked against. A row the owner set is kept, and only the owner's
 * own act lifts a revoke:
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
 * (a revoked key never holds a live key's place). Revoked set rows are bounded
 * apart, by `maxOwnerClients` again: past it the oldest revoked row no agent or held
 * registration holds is forgotten, as `forgetRevoked` forgets it (an MCP client's moves the
 * owner-wide fence, so its pre-revoke credentials stay dead). A revoke, a narrowing that
 * must always land, is never refused. Rows agents and stamped work hold are bounded by
 * their own bounds.
 */
import { HEX64 } from "../../contract/src/ids";
import { Refusal } from "../../contract/src/refusal";
import { SCOPES_JSON_MAX } from "../../contract/src/limits";
import type { OwnerStorage } from "./gate";
import { appList, connectUrl, type Vendor } from "../../contract/src/apps";
import { CLIENT_PROFILES, DEFAULT_PROFILE, type ProfileName } from "../../contract/src/doors";
import { type ConnectedApp, type Grant, MAIN_AGENT, may, type Scope } from "./authority";
import { RUN_CAPABILITIES } from "../../contract/src/capabilities";
import { keyed } from "../../contract/src/checks";
import { canonical } from "../../contract/src/canonical";
import { policyDigest } from "../../contract/src/policy-document";
import type { Policy } from "./policy";
import { agentId, codePoints, utf8Bytes } from "../../contract/src/text";
import { type Call, type LiveSession, type ObjectReads, proven } from "./door";
import type { Keys } from "./keys";
import type { Launcher } from "./launch";
import { clock } from "./clock";

const OUTSIDE_MODES: readonly string[] = Object.freeze(["full", "scoped", "revoked"]);
const OUTSIDE_SOURCES: readonly string[] = Object.freeze(["mcp", "api_key"]);
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
interface GrantLimits {
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

export type Client = {
  readonly client: string;
  readonly source: string;
  readonly mode: string;
  readonly scopes: Scope[];
  readonly profile: string;
  /** The owner's agent the client is. */
  readonly agent: string;
  /** Its authority: moves only with its mode, scopes, profile or agent, a revoke among them. */
  readonly generation: number;
  /** Its concurrency token: moves on every owner set and every revoke. */
  readonly revision: number;
  readonly revoked_at: number | null;
  readonly name: string;
  readonly first_seen: number;
  readonly last_seen: number;
  /**
   * Which of `apps.json`'s vendors the client is, as its sign-in knew it (migration 4),
   * or null; `vendor_verified` 1 when its CIMD host verified it.
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

/** A grant's set, as the owner asks it (`grantSet`), or a key's mint makes it (`keyMint`). */
export interface GrantChange {
  readonly client: unknown;
  readonly source: unknown;
  readonly mode: unknown;
  readonly scopes: unknown;
  readonly expected_revision: unknown;
  readonly name: unknown;
  /** The tool profile; omitted, the grant keeps its own (a new one, `custom`). */
  readonly profile?: unknown;
  /** The owner's agent the client is; omitted, the grant keeps its own (a new one, `main`). */
  readonly agent?: unknown;
}

/**
 * What a grant holds, as one value: equal exactly when `holds`, its profile and its agent
 * answer alike.
 */
function authority(mode: string, held: readonly Scope[], profile: string, agent: string):
    string {
  return canonical([mode, [...new Set(held.map((scope) => canonical(scope)))].sort(), profile,
                    agent]);
}

/**
 * `outside_scopes`: exact triples; a wildcard is never a scope, and a capability is one a
 * door checks (`RUN_CAPABILITIES`), so a grant never names one that nothing would hold.
 */
export function scopes(value: unknown): Scope[] {
  if (!Array.isArray(value) || value.some((scope) =>
    !keyed(scope, ["agent", "capability", "universe"])
    || Object.values(scope).some((v) => typeof v !== "string" || !v || v === "*"
                                        || codePoints(v) > 512))) {
    throw new Refusal("invalid", "outside scopes require exact universe, agent and capability");
  }
  if (value.some((scope) => !(RUN_CAPABILITIES as readonly string[]).includes(
    (scope as Record<string, string>)["capability"]!))) {
    throw new Refusal("invalid", `a scope's capability is one of ${RUN_CAPABILITIES.join(", ")}`);
  }
  if (utf8Bytes(JSON.stringify(value)) > GRANT_LIMITS.maxScopesBytes) {
    throw new Refusal("invalid",
                      `a grant's scopes are at most ${GRANT_LIMITS.maxScopesBytes} bytes of JSON`);
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

type Usage = { owner_clients: number; free_clients: number };

export class Grants {
  readonly #storage: OwnerStorage;
  readonly #policy: Policy;

  constructor(storage: OwnerStorage, policy: Policy) {
    this.#storage = storage;
    this.#policy = policy;
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
        || authenticatedAt <= row.revoked_at + GRANT_LIMITS.revokeClockMarginS)) {
      throw new Refusal("refused", "a fresh sign-in is required after a revoke");
    }
    return { mode: row.mode as Grant["mode"], scopes: JSON.parse(row.scopes) as Scope[],
             generation: row.generation, profile: row.profile as ProfileName,
             agent: row.agent };
  }

  /**
   * The grant of the verified outside client making this call. An MCP client's first
   * verified call records it `full`, making room by forgetting the least recently
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
                          || authenticatedAt <= fence + GRANT_LIMITS.revokeClockMarginS)) {
          throw new Refusal("refused", "a fresh sign-in is required after a revoke");
        }
        const over = this.#usage().free_clients - GRANT_LIMITS.maxFreeClients + 1;
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
      } else if (row !== undefined && now - row.last_seen >= GRANT_LIMITS.seenIntervalS) {
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
   * Whether a client's grant, as it stands, holds `capability` in `universe` as the agent it
   * names (a full grant holds every one): what a wake is held to before it rings. A read: it
   * never records a client.
   */
  reads(client: string, universe: string, capability: string): boolean {
    const row = this.#row(client);
    return row !== undefined && row.mode !== "revoked"
      && may({ mode: row.mode, scopes: JSON.parse(row.scopes) as Scope[] }, capability,
             { universe, agent: row.agent });
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
   * `outside_set`, an owner widening from its protected session: one client's mode, scopes,
   * profile, agent and name, against the revision the owner was shown; `expected_revision` 0
   * creates the row, as a key's mint does. The revision always moves; the generation only when
   * the authority does (`moved`), which is when the caller ends the client's work.
   */
  set(session: LiveSession, change: GrantChange, now: number):
      { client: string; mode: string; scopes: Scope[]; profile: string; agent: string;
        generation: number; revision: number; moved: boolean } {
    proven(session, "session");
    const client = outsideClient(change.client, change.source);
    const { mode, expected_revision: expected, name } = change;
    const source = change.source as string;
    if (typeof mode !== "string" || !OUTSIDE_MODES.includes(mode)) {
      throw new Refusal("invalid", "invalid outside grant mode");
    }
    const held = scopes(change.scopes);
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
    const asked = change.agent === undefined ? undefined : agentId(change.agent);
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
      if (mode !== "revoked" && !live && this.#liveSet() >= GRANT_LIMITS.maxOwnerClients) {
        throw new Refusal("refused",
                          "the owner's managed outside clients are at their limit; revoke " +
                          "one to make room");
      }
      const profile = (change.profile ?? row?.profile ?? DEFAULT_PROFILE) as string;
      const agent = asked ?? row?.agent ?? MAIN_AGENT;
      const moved = row === undefined
        || authority(row.mode, JSON.parse(row.scopes) as Scope[], row.profile, row.agent)
          !== authority(mode, held, profile, agent);
      const generation = (row?.generation ?? 0) + (moved ? 1 : 0);
      const revision = expected + 1;
      // A revoke is timed when it lands; a revoked grant set again keeps its fence.
      const revokedAt = mode === "revoked" && moved ? now : row?.revoked_at ?? null;
      const named = name === "" ? row?.name ?? "" : name;
      sql.exec(
        "INSERT INTO outside_clients (client, source, mode, scopes, generation, revision, " +
          "revoked_at, name, first_seen, last_seen, owner_set, profile, agent) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) " +
          "ON CONFLICT (client) DO UPDATE SET mode = excluded.mode, scopes = excluded.scopes, " +
          "profile = excluded.profile, agent = excluded.agent, " +
          "generation = excluded.generation, revision = excluded.revision, " +
          "revoked_at = excluded.revoked_at, owner_set = 1, " +
          "name = CASE WHEN excluded.name = '' THEN name ELSE excluded.name END",
        client, source, mode, canonical(held), generation, revision, revokedAt, name, now, now,
        profile, agent,
      );
      // The receipt's digest names the set whole, so a rename's differs from a set that
      // changes nothing; its generation is the authority's.
      this.#policy.receipt("outside_grant", {
        session_id: session.hash,
        request_digest: policyDigest({ client, mode, scopes: held, name: named, revision,
                                       profile, agent }),
        generation, rule: client, decision: mode, outcome: "committed",
      }, now);
      return { client, mode, scopes: held, profile, agent, generation, revision, moved };
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
        throw new Refusal("refused",
                          "only the owner, in its session, forgets an MCP client's revoke");
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
  forgetRevoked(session: LiveSession, clients: unknown, now: number):
      { forgotten: string[]; mcp_fence: number } {
    proven(session, "session");
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
          this.#forget(row, session.hash, now);
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
      .one().n - GRANT_LIMITS.maxOwnerClients;
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

  /**
   * In a unit: every outside client revoked as `revoke` revokes one, and every MCP grant made
   * before `now` fenced, client row or none (`grant_fences`): a restore's fence (`restore.ts`),
   * since what each grant was checked against was never in the backup. The clients revoked.
   */
  revokeAll(now: number): string[] {
    const clients = this.#sql.exec<{ client: string }>(
      "SELECT client FROM outside_clients WHERE mode != 'revoked' ORDER BY client").toArray()
      .map((row) => row.client);
    for (const client of clients) {
      this.revoke(client, now);
    }
    this.#sql.exec("UPDATE grant_fences SET mcp_through = max(mcp_through, ?)", now);
    return clients;
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

export interface ClientRequest {
  readonly client: string;
}
export interface ForgetRequest {
  readonly clients: string[];
}

// -- THE DOORS: the `grants` rows' handlers (`owner/doors.json`) ----------------------------------

/**
 * What the `grants` doors reach: the grants and their keys, and what a grant change ends of
 * its client's (its wakes, its held registrations, its runs).
 */
export interface GrantsDoorNeeds extends Pick<ObjectReads, "storage"> {
  readonly grants: Grants;
  readonly keys: Pick<Keys, "revoked" | "forgotten">;
  readonly ledger: {
    suppressClient(client: string, now: number): void;
    /** One of the owner's live agents by name, else refused (`no_agent`, `agent_retired`). */
    liveNative(agent: string): unknown;
  };
  readonly registrations: { endClient(client: string): void };
  readonly launcher: Pick<Launcher, "endClient">;
}

/**
 * A grant change ends what its client set going: its agents' open wakes and
 * callbacks and its held registrations, in the caller's transaction.
 */
function endClient(o: GrantsDoorNeeds, client: string): void {
  o.ledger.suppressClient(client, clock());
  o.registrations.endClient(client);
  // And its runs (ADR-034 decision 6): their grants gone in this unit, their CLIs stopped.
  o.launcher.endClient(client, clock());
}

/** In the revoke's transaction: revoked rows past their bound forgotten, keys with them. */
function evictRevoked(o: GrantsDoorNeeds): void {
  for (const client of o.grants.evictRevoked(clock())) {
    o.keys.forgotten(client);
  }
}

export const grantsDoors = Object.freeze({
  grantList(o: GrantsDoorNeeds, _call: Call<undefined>): { clients: Client[] } {
    return { clients: o.grants.list() };
  },
  /**
   * The owner's widening, in its session, against the grant's revision. A set that moves
   * the grant's authority (its mode, scopes, profile or agent) ends the client's agents' wakes
   * and held registrations with it, as `grantRevoke` ends them; one that moves only the
   * revision, a rename, ends nothing: every stamp of the client's still stands. The agent it
   * names is one of the owner's live agents, else refused (`no_agent`, `agent_retired`).
   */
  grantSet(o: GrantsDoorNeeds, { session, r }: Call<GrantChange, "session">):
      { client: string; mode: string; scopes: Scope[]; profile: string; agent: string;
        generation: number; revision: number } {
    return o.storage.transactionSync(() => {
      const { moved, ...set } = o.grants.set(session, r as unknown as GrantChange, clock());
      if (r["agent"] !== undefined) {
        o.ledger.liveNative(set.agent);
      }
      if (moved) {
        endClient(o, set.client);
      }
      if (set.mode === "revoked") {
        o.keys.revoked(set.client, Math.floor(clock()));
        evictRevoked(o);
      }
      return set;
    });
  },
  /**
   * A narrowing the owner's account makes without a session, as a key's revoke does: the
   * client's agents' wakes and held registrations end with it, in its unit.
   */
  grantRevoke(o: GrantsDoorNeeds, { r }: Call<ClientRequest>):
      { client: string; revoked: boolean; generation: number; revision: number } {
    return o.storage.transactionSync(() => {
      const revoked = o.grants.revoke(r["client"], clock());
      endClient(o, revoked.client);
      o.keys.revoked(revoked.client, Math.floor(clock()));
      evictRevoked(o);
      return revoked;
    });
  },
  /**
   * Forget a revoked API key's grant: the binder calls it once the directory has deleted
   * the key. A narrowing, since a key with no row is
   * refused, so the owner's account makes it without a session. Never an MCP client's.
   */
  grantForget(o: GrantsDoorNeeds, { r }: Call<ClientRequest>):
      { client: string; forgotten: boolean } {
    return o.storage.transactionSync(() => {
      const forgot = o.grants.forgetKey(r["client"], clock());
      if (forgot.forgotten) {
        o.keys.forgotten(forgot.client);
      }
      return forgot;
    });
  },
  /**
   * The owner, in its session, forgets revoked clients to make room under its bound: a
   * widening, so it is the only act that lifts an MCP revoke, and even then a forgotten
   * MCP client comes back only from a sign-in after the revoke (`grants.ts`).
   */
  grantForgetRevoked(o: GrantsDoorNeeds, { session, r }: Call<ForgetRequest, "session">):
      { forgotten: string[]; mcp_fence: number } {
    return o.storage.transactionSync(() => {
      const forgot = o.grants.forgetRevoked(session, r["clients"], clock());
      for (const client of forgot.forgotten) {
        o.keys.forgotten(client);
      }
      return forgot;
    });
  },
});
