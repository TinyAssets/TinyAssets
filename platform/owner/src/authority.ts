/**
 * Outside-client authority over the agent (agent research §4; tasks 5, sub-slice A2): what
 * a conversation may do, decided at each operation it makes.
 *
 * Every conversation runs under an authority, kept in pi's own `ta.authority` document
 * (scope conversation, the newest only, copied into a fork): the owner's, an outside
 * client's stamp, or none. None is the document's initial value, so a conversation the
 * platform did not make as a thread, or as a task's conversation below one, can do
 * nothing: the owner's case is explicit, never the absence of a stamp. A stamp is the
 * client, its grant's generation and its sign-in time, as scheduled work's is, plus where
 * the conversation acts: the universe (the command center) and the agent a scoped grant's
 * triples name, fixed when the thread is made, as the Python's run context fixed them for
 * `check_identity`. A thread's authority is written where the thread is made, in pi's
 * creating commit, and recorded in the object's threads (`threads.ts`); every
 * conversation a task makes takes its owner conversation's through pi's
 * `conversationCreated`, over any copy a fork brought, and a fork holds its parent's
 * (`agent.ts`).
 *
 * The authority is enforced where every effect passes, which no conversation can select
 * away: the environment pi builds for each tool call, replays included (`files-env.ts`),
 * re-checks it on every operation (a read needs `read_files`, a change `write_files` in its
 * own unit, `exec` a grant that is not scoped), and the model provider re-checks it in
 * each call's reservation and dispatch units (`usage.ts`, `gateway-provider.ts`), so a
 * revoked client's turn spends nothing more, whatever abort it missed. Under a client's
 * stamp each check reads, synchronously, that the client's grant still stands at the
 * stamped generation (`Grants.standing`: not changed, not revoked, no revoke after the
 * sign-in) and, for a scoped grant, that it holds the capability where the conversation
 * acts. A full grant holds every capability, as at the doors.
 *
 * `ta.authority`, a trusted extension installed first, is the early gate: its
 * `beforeTool` refuses a call before its intent is recorded, for a model's call and a
 * nested one (`call.parent`) alike, with a fixed reason and a refusal receipt. Each tool's
 * need: `read` needs `read_files`; `write` needs `write_files`; `edit`, which reads the
 * file it changes, needs both; `bash` is refused to a scoped grant; `ta` takes its own
 * map's, by the capability called (A3 fills it, so every one is refused now, and the
 * `ta:` names are reserved so no call aliases an op); any other tool is refused.
 *
 * Out of scope now: only platform code makes a top-level conversation (a guard holds it to
 * `OwnerAgent.thread`), and only platform code selects extensions (a guard keeps every
 * selection out of the sources), since a conversation's own selection decides which hooks
 * run; the enforcement points above do not depend on either.
 */
import { Invalid, Refused } from "./errors";
import { codePoints, isPrintable } from "./text";

/** The capabilities of a scoped grant the agent's tools need (`grants.ts`'s `CAPABILITIES`). */
export type ToolCapability = "read_files" | "write_files";

/** What an operation needs: each of these capabilities, or the shell (`null`). */
export type Need = readonly ToolCapability[] | null;

/**
 * Each core tool's need; `null`: refused to a scoped client whatever it holds (the shell
 * reaches the owner's whole sandbox). A tool missing from the map has none.
 */
export const TOOL_CAPABILITIES: Readonly<Record<string, Need>> = Object.freeze({
  read: Object.freeze(["read_files"] as const),
  write: Object.freeze(["write_files"] as const),
  edit: Object.freeze(["read_files", "write_files"] as const),
  bash: null,
});

/**
 * Each `ta` capability's need, by the name `ta` calls (`ta:catalog` for the catalog, as the
 * Python's `check_identity` named it). A3 fills it; one missing is refused. The `ta:`
 * names are the ops' own: a call naming one is refused.
 */
export const TA_CAPABILITIES: Readonly<Record<string, Need>> = Object.freeze({});
const TA_OP = "ta:";

/** The fixed reasons a call is refused with: the model and the receipts see only these. */
export const AUTHORITY_CHANGED = "outside client authority no longer stands";
export const NOT_GRANTED = "outside client capability not granted";
export const NO_SHELL = "the shell is not granted to a scoped outside client";
export const NO_AUTHORITY = "this conversation runs under no authority";

/** The receipt's rule for a refused tool call, and a refused model call's code. */
export const AUTHORITY_RULE = "outside_authority";
/** A model call's refusal code when its request names no conversation the object made. */
export const NO_CONVERSATION = "no_conversation";

/** The authority a conversation runs under: the client's, as it was admitted, and where. */
export type Stamped = {
  client: string;
  generation: number;
  authenticated_at: number | null;
  /** The universe (command center) a scoped grant's triples name; none for a full grant's. */
  universe: string | null;
  /** The agent the conversation acts as. */
  agent: string;
};

/** The `ta.authority` document's content: the owner's, a client's stamp, or none. */
export type AuthorityState =
  | { by: "owner" }
  | { by: "client"; stamp: Stamped }
  | { by: "none" };

/** A thread's authority: the owner's own (`null`), or the stamp of a client's. */
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

/** An agent's id as a thread names it. */
export function agentId(value: unknown): string {
  if (!label(value, 128)) {
    throw new Invalid("an agent id is 1 to 128 printable characters");
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

/** A standing grant, as the gate reads it: its mode, and whether it holds a triple. */
export interface StandingGrant {
  readonly mode: string;
  holds(scope: { universe: string; agent: string; capability: string }): boolean;
}

/** What the gate reads of the object's grants: `Grants.standing`, with `holds`. */
export interface AuthorityGrants {
  standing(client: unknown, generation: unknown, authenticatedAt: number | null): StandingGrant;
}

/** A tool call as the gate sees it: pi's `ToolHookCall`. */
export interface GatedCall {
  readonly name: string;
  readonly arguments: unknown;
  readonly parent?: unknown;
}

/**
 * What a call needs: a core tool's, a `ta` capability's by `ta`'s map (the object's own,
 * `TA_CAPABILITIES`), or nothing known (undefined).
 */
export function capabilityOf(call: GatedCall,
                             ta: Readonly<Record<string, Need>> = TA_CAPABILITIES):
    Need | undefined {
  if (call.name === "ta") {
    const args = call.arguments as Record<string, unknown> | null;
    const op = typeof args === "object" && args !== null ? args["op"] : undefined;
    const name = op === "call" ? args!["name"] : undefined;
    const called = op === "catalog" ? `${TA_OP}catalog`
      : typeof name === "string" && !name.startsWith(TA_OP) ? name : undefined;
    return called !== undefined && Object.hasOwn(ta, called) ? ta[called] : undefined;
  }
  return Object.hasOwn(TOOL_CAPABILITIES, call.name) ? TOOL_CAPABILITIES[call.name] : undefined;
}

export interface AuthorityParts {
  readonly grants: AuthorityGrants;
  /**
   * Keep a refusal receipt of a blocked call, in a unit of its own: the object's policy
   * refusal, under `AUTHORITY_RULE`.
   */
  readonly receipt: (request: { client: string | null; tool: string; reason: string }) =>
    Promise<void>;
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
   * That `state` holds `need` (none: only that it stands) where its conversation acts; else
   * a refusal with the fixed reason. The owner's holds everything, none nothing; a client's
   * stamp must stand, and a scoped grant hold each capability (`null`: never).
   */
  require(state: Readonly<AuthorityState> | undefined, need: Need | undefined): void {
    if (state?.by === "owner") {
      return;
    }
    if (state?.by !== "client") {
      throw new Refused(NO_AUTHORITY);
    }
    const stamp = state.stamp;
    const grant = this.#standing(stamp);
    if (grant.mode === "full") {
      return;
    }
    if (need === null) {
      throw new Refused(NO_SHELL);
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

  /** A model call's refusal code under `state`, or null when it may spend. */
  modelRule(state: Readonly<AuthorityState> | undefined): string | null {
    return this.refusal(state, []) === undefined ? null : AUTHORITY_RULE;
  }

  /**
   * `ta.authority`'s `beforeTool`: the call's block, with its receipt kept, or undefined.
   * The receipt is kept, or its failure logged, before the block returns.
   */
  async decide(state: Readonly<AuthorityState> | undefined, call: GatedCall):
      Promise<{ block: string } | undefined> {
    const reason = this.refusal(state, capabilityOf(call));
    if (reason === undefined) {
      return undefined;
    }
    try {
      await this.#parts.receipt({ client: stampOf(state)?.client ?? null, tool: call.name,
                                  reason });
    } catch {
      console.error("a tool call's refusal receipt could not be kept");  // refused regardless
    }
    return { block: reason };
  }
}
