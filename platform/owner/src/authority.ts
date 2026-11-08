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
import { HEX64 } from "../../contract/src/ids";
import { Refusal } from "../../contract/src/refusal";
import { codePoints, isPrintable } from "../../contract/src/text";


/** The need of what only the owner itself may do, whatever grant a client holds. */
export const OWNER_ONLY = "owner";

/** What an operation needs: each of these capabilities (none: only a standing authority), or the owner itself. */
export type Need = readonly RunCapability[] | typeof OWNER_ONLY;

/** The fixed reasons work is refused with: the receipts see only these. */
export const AUTHORITY_CHANGED = "outside client authority no longer stands";
export const NOT_GRANTED = "outside client capability not granted";
export const NO_AUTHORITY = "this work runs under no authority";
export const NOT_OWNER = "only the owner may use this capability";

/** The receipt's rule for refused work. */
export const AUTHORITY_RULE = "outside_authority";

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
export type AuthorityState =
  | { by: "owner" }
  | { by: "client"; stamp: Stamped }
  | { by: "none" };

/** Work's authority: the owner's own (`null`), or the stamp of a client's. */
export function authorityOf(stamp: Stamped | null): AuthorityState {
  return stamp === null ? { by: "owner" } : { by: "client", stamp: { ...stamp } };
}

/** The stamp of an authority, if it is a client's. */
export function stampOf(state: Readonly<AuthorityState> | undefined): Stamped | null {
  return state?.by === "client" ? state.stamp : null;
}

const STAMP_KEYS = "agent,authenticated_at,client,generation,universe";

/** An agent id, or a universe: 1 to `max` printable characters, never a wildcard. */
function label(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value !== "*"
    && codePoints(value) <= max && isPrintable(value);
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

/** A stamp as an admitted call gives it, else invalid; null is the owner's own. */
export function stamped(value: unknown): Stamped | null {
  if (value === null) {
    return null;
  }
  const refused = () => new Refusal("invalid",
                                    "a stamp is a client, its grant generation, its sign-in " +
                                    "time, a universe and an agent");
  if (typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join() !== STAMP_KEYS) {
    throw refused();
  }
  const { client, generation, authenticated_at: at, universe, agent } =
    value as Record<string, unknown>;
  if (typeof client !== "string" || !HEX64.test(client) || typeof generation !== "number"
      || !Number.isSafeInteger(generation) || generation < 1
      || (at !== null && (typeof at !== "number" || !Number.isFinite(at) || at < 0))
      || (universe !== null && !label(universe, 512)) || !label(agent, 128)) {
    throw refused();
  }
  return { client, generation, authenticated_at: at as number | null,
           universe: universe as string | null, agent };
}

/** A capability where work acts: a scoped grant's triple. */
export interface Triple {
  readonly universe: string;
  readonly agent: string;
  readonly capability: string;
}

/** A grant, as a check reads it: its mode, and a scoped grant's triples. */
export interface StandingGrant {
  readonly mode: string;
  readonly scopes: readonly Triple[];
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
export interface AuthorityGrants {
  standing(client: unknown, generation: unknown, authenticatedAt: number | null): StandingGrant;
}

export interface AuthorityParts {
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
    if (need === OWNER_ONLY) {
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
