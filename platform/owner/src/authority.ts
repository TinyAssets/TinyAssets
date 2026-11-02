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
 * acts. A full grant holds every capability, as at the doors, but the explicit ones
 * (`EXPLICIT`: code mode's `run_code`), which only a scoped grant that names them holds.
 * Every check is refused while the owner is not admitted, the owner's own too.
 *
 * The runs slice (R) mints a run's grant from this, and checks it at every call the run
 * makes (ADR-034 decision 6).
 */
import { Invalid, Refused } from "./errors";
import { codePoints, isPrintable } from "./text";

/** The capabilities of a scoped grant that work needs (`grants.ts`'s `CAPABILITIES`). */
export type ToolCapability = "read_files" | "write_files" | "propose_policy" | "converse"
  | "run_graph" | "run_code";

/**
 * The capabilities a client holds only where a scoped grant names them, never by a full
 * grant: code mode's (`run_code`), many tool calls in one call with no model round trip
 * between them.
 */
export const EXPLICIT: readonly ToolCapability[] = Object.freeze(["run_code"] as const);

/** The need of what only the owner itself may do, whatever grant a client holds. */
export const OWNER_ONLY = "owner";

/** What an operation needs: each of these capabilities (none: only a standing authority), or the owner itself. */
export type Need = readonly ToolCapability[] | typeof OWNER_ONLY;

/** The fixed reasons work is refused with: the receipts see only these. */
export const AUTHORITY_CHANGED = "outside client authority no longer stands";
export const NOT_GRANTED = "outside client capability not granted";
export const NO_AUTHORITY = "this work runs under no authority";
export const NOT_OWNER = "only the owner may use this capability";

/** The receipt's rule for refused work. */
export const AUTHORITY_RULE = "outside_authority";

/** A refusal's fixed code: the part before its colon (`owner_suspended: ...`), else itself. */
export function refusalCode(message: string): string {
  return /^[a-z_]+:/.exec(message)?.[0].slice(0, -1) ?? message;
}

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

const HEX64 = /^[0-9a-f]{64}$/;
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
    throw new Invalid("an agent id is 1 to 128 letters, digits, dots, dashes and " +
                      "underscores");
  }
  return value;
}

/** A stamp as an admitted call gives it, else invalid; null is the owner's own. */
export function stamped(value: unknown): Stamped | null {
  if (value === null) {
    return null;
  }
  const refused = () => new Invalid("a stamp is a client, its grant generation, its sign-in " +
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

/** A standing grant, as a check reads it: its mode, and whether it holds a triple. */
export interface StandingGrant {
  readonly mode: string;
  holds(scope: { universe: string; agent: string; capability: string }): boolean;
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
  readonly admission?: () => string | null;
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
      if (error instanceof Refused) {
        throw new Refused(AUTHORITY_CHANGED);
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
      throw new Refused(unadmitted);
    }
    if (state?.by === "owner") {
      return;
    }
    if (state?.by !== "client") {
      throw new Refused(NO_AUTHORITY);
    }
    const stamp = state.stamp;
    const grant = this.#standing(stamp);
    if (need === OWNER_ONLY) {
      throw new Refused(NOT_OWNER);
    }
    const explicit = Array.isArray(need)
      && need.some((capability) => EXPLICIT.includes(capability));
    if (grant.mode === "full" && !explicit) {
      return;
    }
    if (grant.mode === "full") {
      throw new Refused(NOT_GRANTED);  // a full grant names no triple, so no explicit one
    }
    if (need === undefined || stamp.universe === null
        || !need.every((capability) => grant.holds({ universe: stamp.universe!,
                                                     agent: stamp.agent, capability }))) {
      throw new Refused(NOT_GRANTED);
    }
  }

  /** Why an operation needing `need` is refused under `state`, or undefined. Synchronous. */
  refusal(state: Readonly<AuthorityState> | undefined, need: Need | undefined):
      string | undefined {
    try {
      this.require(state, need);
      return undefined;
    } catch (error) {
      if (error instanceof Refused) {
        return error.message;
      }
      throw error;
    }
  }

  /** That `state` holds one of `needs`; else the first one's refusal. */
  requireOne(state: Readonly<AuthorityState> | undefined, needs: readonly Need[]): void {
    let first: string | undefined;
    for (const need of needs) {
      const reason = this.refusal(state, need);
      if (reason === undefined) {
        return;
      }
      first ??= reason;
    }
    throw new Refused(first ?? NOT_GRANTED);
  }
}
