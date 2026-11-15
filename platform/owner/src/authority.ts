/**
 * Outside-client authority over the owner's agents (agent research §4; tasks 5, A2; kept
 * by ADR-034 as a run's authority): what work an outside client's stamp may still do,
 * decided at each check.
 *
 * Work runs under an authority: the owner's, an outside client's stamp, or none. None can
 * do nothing, so the owner's case is explicit, never the absence of a stamp. A stamp is the
 * client, its grant's generation and its sign-in time, plus where the work acts: the
 * universe (the command center) and the agent a scoped grant's triples name, fixed when
 * the work is admitted, as the Python's run context fixed them for `check_identity`.
 *
 * Under a client's stamp each check reads, synchronously, that the client's grant still
 * stands at the stamped generation (`Grants.standing`: not changed, not revoked, no revoke
 * after the sign-in) and, for a scoped grant, that it holds each capability where the work
 * acts. A full grant holds every capability, as at the doors. Code mode is no capability:
 * the owner chooses it by the client's tool profile (`grants.ts`).
 * Every check is refused while the owner is not admitted, the owner's own too.
 *
 * The runs slice (R) mints a run's grant from this, and checks it at every call the run
 * makes (ADR-034 decision 6).
 */
import type { RunCapability } from "../../contract/src/capabilities";
import type { ProfileName } from "../../contract/src/doors";
import { Refusal } from "../../contract/src/refusal";
import type { Account } from "./door";
import { sha256Hex } from "../../contract/src/digest";


/**
 * The owner itself, wherever a party may instead be an outside client (by its id): the need
 * of what only the owner may do, whatever grant a client holds; a write's files scope and a
 * row's filer (`scopeOf`); a sandbox's authority (`contract/src/ids.ts` `AUTHORITY`); a room
 * member, a mail's reader or sender, a subscription's principal. Frozen: the owner's schema
 * spells it (the frozen-bounds guard holds the two equal).
 */
export const OWNER = "owner";

/** What an operation needs: each of these capabilities (none: only a standing authority), or the owner itself. */
type Need = readonly RunCapability[] | typeof OWNER;

/** The fixed reasons work is refused with: the receipts see only these. */
export const AUTHORITY_CHANGED = "outside client authority no longer stands";
export const NOT_GRANTED = "outside client capability not granted";
export const NO_AUTHORITY = "this work runs under no authority";
export const NOT_OWNER = "only the owner may use this capability";

/** The authority work runs under: the client's, as it was admitted, and where. */
export type Stamped = {
  client: string;
  generation: number;
  authenticated_at: number | null;
  /** The universe (command center) a scoped grant's triples name; none for a full grant's. */
  universe: string | null;
  /** The agent the work acts as. */
  agent: string;
};

/** An authority: the owner's, a client's stamp, or none. */
type AuthorityState =
  | { by: "owner" }
  | { by: "client"; stamp: Stamped }
  | { by: "none" };

/** Work's authority: the owner's own (`null`), or the stamp of a client's. */
export function authorityOf(stamp: Stamped | null): AuthorityState {
  return stamp === null ? { by: "owner" } : { by: "client", stamp: { ...stamp } };
}

/** An agent's id: its directory's name under `/workspace/agents/`. */
export const AGENT_DIR = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/** An agent's id: its directory's name (`AGENT_DIR`). */
export function agentId(value: unknown): string {
  if (typeof value !== "string" || !AGENT_DIR.test(value)) {
    throw new Refusal("invalid", "an agent id is 1 to 128 letters, digits, dots, dashes and " +
                      "underscores");
  }
  return value;
}

/** A capability where work acts: a scoped grant's triple. */
export interface Scope {
  readonly universe: string;
  readonly agent: string;
  readonly capability: string;
}

/** A grant, as a check reads it: its mode, and a scoped grant's triples. */
export interface StandingGrant {
  readonly mode: string;
  readonly scopes: readonly Scope[];
}

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

/** The grant a call is admitted under (`grants.ts`). */
export interface Grant extends StandingGrant {
  readonly mode: "full" | "scoped" | "revoked";
  readonly scopes: Scope[];
  readonly generation: number;
  /** The tool profile the client is served (`directory` or `custom`). */
  readonly profile: ProfileName;
}

/** What holds capabilities: a grant, or a run's own scopes (its grant's, at admission). */
export type Holder = StandingGrant | { readonly run: readonly string[] };

/**
 * Where work acts: a universe (the command center; null, none: a scoped grant holds nothing
 * there) and the agent it acts as; or anywhere, for what a grant holds somewhere.
 */
export type Where = { readonly universe: string | null; readonly agent: string } | "anywhere";

/**
 * Whether `holder` holds `capability` where its work acts: the one rule of who may do what,
 * at every door and every check. No holder (`null`) is the owner's own work, which holds
 * everything; a run holds its own scopes, wherever it acts; a full grant every capability,
 * a scoped one only the triples it names (anywhere: any of them), and a revoked one none.
 */
export function may(holder: Holder | null, capability: string, where: Where): boolean {
  if (holder === null) {
    return true;
  }
  if ("run" in holder) {
    return holder.run.includes(capability);
  }
  if (holder.mode === "full") {
    return true;
  }
  if (holder.mode !== "scoped") {
    return false;
  }
  return holder.scopes.some((held) => held.capability === capability && (where === "anywhere"
    || (where.universe !== null && held.universe === where.universe
        && held.agent === where.agent)));
}

/** What a check reads of the object's grants: `Grants.standing`, with `holds`. */
interface AuthorityGrants {
  standing(client: unknown, generation: unknown, authenticatedAt: number | null): StandingGrant;
}

interface AuthorityParts {
  readonly grants: AuthorityGrants;
  /**
   * The owner's admission's `require_work` refusal, or null while it is admitted: a
   * synchronous read of the object's own row (`admission.ts`). Every check is refused
   * while the owner is not admitted, the owner's own too.
   */
  readonly admission?: () => Refusal | null;
}

export class Authority {
  readonly #parts: AuthorityParts;

  constructor(parts: AuthorityParts) {
    this.#parts = parts;
  }

  /** The stamp's grant, standing; else a refusal with the fixed reason. */
  #standing(stamp: Stamped): StandingGrant {
    try {
      return this.#parts.grants.standing(stamp.client, stamp.generation, stamp.authenticated_at);
    } catch (error) {
      if (error instanceof Refusal && !error.invalid) {
        throw new Refusal("authority_changed", AUTHORITY_CHANGED);
      }
      throw error;
    }
  }

  /**
   * That `state` holds `need` (none: only that it stands) where its work acts; else a
   * refusal with the fixed reason. The owner's holds everything, none nothing; a client's
   * stamp must stand, never holds what is the owner's alone, and a scoped grant holds each
   * capability.
   */
  require(state: Readonly<AuthorityState> | undefined, need: Need | undefined): void {
    const unadmitted = this.#parts.admission?.() ?? null;
    if (unadmitted !== null) {
      throw unadmitted;
    }
    if (state?.by === "owner") {
      return;
    }
    if (state?.by !== "client") {
      throw new Refusal("no_authority", NO_AUTHORITY);
    }
    const stamp = state.stamp;
    const grant = this.#standing(stamp);
    if (need === OWNER) {
      throw new Refusal("not_owner", NOT_OWNER);
    }
    if (grant.mode === "full") {
      return;
    }
    if (need === undefined || stamp.universe === null
        || !need.every((capability) => may(grant, capability, stamp))) {
      throw new Refusal("not_granted", NOT_GRANTED);
    }
  }

  /** Why an operation needing `need` is refused under `state`, or undefined. Synchronous. */
  refusal(state: Readonly<AuthorityState> | undefined, need: Need | undefined):
      Refusal | undefined {
    try {
      this.require(state, need);
      return undefined;
    } catch (error) {
      if (error instanceof Refusal && !error.invalid) {
        return error;
      }
      throw error;
    }
  }

  /** That `state` holds one of `needs`; else the first one's refusal. */
  requireOne(state: Readonly<AuthorityState> | undefined, needs: readonly Need[]): void {
    let first: Refusal | undefined;
    for (const need of needs) {
      const reason = this.refusal(state, need);
      if (reason === undefined) {
        return;
      }
      first ??= reason;
    }
    throw first ?? new Refusal("not_granted", NOT_GRANTED);
  }
}

/** The agent a scoped client acts as when its call names none. */
export const MAIN_AGENT = "main";

/**
 * Whose files scope a door's rows count in (ADR-035): the owner's, an outside client's
 * (`client:<id>`), or a visiting owner's own, `client:` and its principal's hash, so a
 * visitor is held to the owner's `client_share` as a client is.
 */
export function filerOf(account: Account): string {
  if (account.origin !== null) {
    return scopeOf(account.origin.client);
  }
  return scopeOf(account.caller === account.owner ? null
    : sha256Hex(`visitor\n${account.caller}`));
}

/** The files scope of the owner's own work (`OWNER`), or of the outside client `client`. */
export function scopeOf(client: string | null): string {
  return client === null ? OWNER : `client:${client}`;
}
