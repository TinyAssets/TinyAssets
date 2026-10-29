/**
 * Outside-client authority over the agent (agent research §4; tasks 5, sub-slice A2): what
 * a conversation made for an outside client may do, decided at each tool call.
 *
 * A conversation runs under a stamp: the authority it was made under, kept in pi's own
 * `ta.authority` document (scope conversation, the newest only, copied into a fork). The
 * owner's conversations carry none. A stamp is the client, its grant's generation and its
 * sign-in time, as scheduled work's is, plus where the conversation acts: the universe (the
 * command center) and the agent a scoped grant's triples name, fixed when the thread is
 * made, as the Python's run context fixed them for `check_identity`. A thread's stamp is
 * written where the thread is made, in pi's creating commit, and recorded in the object's
 * threads (`threads.ts`); every conversation a task makes takes it through pi's
 * `conversationCreated`, and a fork holds its parent's copy (`agent.ts`), so neither a
 * subagent nor a fork sheds it.
 *
 * Declared differences from the Python: a model request is not gated (below). Out of
 * scope now: only platform code makes a top-level conversation, so only it could make an
 * unstamped one from inside a stamped one; pi's coding tools have no such path, and a
 * platform capability that makes one (A3, A4) makes it a thread, stamped.
 *
 * The gate is `ta.authority`, a trusted extension installed first, so its `block` wins:
 * pi runs `beforeTool` hooks in extension order, the first block wins, and a throw blocks
 * (pi-durable's tool task). It is pi's one hook that can refuse a step: `beforeRequest`'s
 * throw is only reported, and `onYield` can only continue. It runs before the call's intent
 * is recorded, for a model's call and a nested one (`call.parent`, a tool's `executeTool`)
 * alike. Under a stamp it checks, synchronously, that the client's grant still stands at
 * the stamped generation (`Grants.standing`: not changed, not revoked, no revoke after the
 * sign-in) and, for a scoped grant, that it holds the tool's capability where the
 * conversation acts: `read` needs `read_files`; `write` and `edit` need `write_files`;
 * `bash` is refused; `ta` takes its capability from its own map, by the capability called
 * (A3 fills it, so every one is refused now); any other tool is refused. A full grant holds
 * every capability, as at the doors. A refusal is a block with a fixed reason, and leaves a
 * refusal receipt in the policy receipts, written in a unit of its own (the call is refused
 * whether or not the receipt could be kept).
 *
 * A model request is not gated: it has no effect beyond spend, which the object reserves
 * per call (`usage.ts`). A request already in flight when a grant changes is ended by the
 * abort that follows the change (`agent.ts`), and its tool calls are blocked here.
 *
 * Each change the environment makes for a tool call re-checks the same authority inside
 * its own unit (`files-env.ts`, `authorize`), as a door's whole write re-checks its
 * caller's in each unit it writes: a grant that narrowed after the call was admitted, and
 * before its write's unit ran, refuses the write.
 */
import { Invalid, Refused } from "./errors";
import { codePoints, isPrintable } from "./text";

/** The capabilities of a scoped grant the agent's tools need (`grants.ts`'s `CAPABILITIES`). */
export type ToolCapability = "read_files" | "write_files";

/**
 * Each core tool's capability; `null`: refused to a scoped client whatever it holds (the
 * shell reaches the owner's whole sandbox). A tool missing from the map has none.
 */
export const TOOL_CAPABILITIES: Readonly<Record<string, ToolCapability | null>> = Object.freeze({
  read: "read_files",
  write: "write_files",
  edit: "write_files",
  bash: null,
});

/**
 * Each `ta` capability's grant capability, by the name `ta` calls (`ta:catalog` for the
 * catalog, as the Python's `check_identity` named it). A3 fills it; one missing is refused.
 */
export const TA_CAPABILITIES: Readonly<Record<string, ToolCapability>> = Object.freeze({});

/** The fixed reasons a call is blocked with: the model and the receipts see only these. */
export const AUTHORITY_CHANGED = "outside client authority no longer stands";
export const NOT_GRANTED = "outside client capability not granted";
export const NO_SHELL = "the shell is not granted to a scoped outside client";

/** The receipt's rule for a refused tool call. */
export const AUTHORITY_RULE = "outside_authority";

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

/** The `ta.authority` document's content: a conversation's stamp, or none (the owner's). */
export type AuthorityState = { stamp: Stamped | null };

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

/** The capability a call needs: a core tool's, a `ta` capability's, or none (undefined). */
export function capabilityOf(call: GatedCall): ToolCapability | null | undefined {
  if (call.name === "ta") {
    const args = call.arguments as Record<string, unknown> | null;
    const op = typeof args === "object" && args !== null ? args["op"] : undefined;
    const called = op === "catalog" ? "ta:catalog"
      : op === "call" && typeof args!["name"] === "string" ? args!["name"] : undefined;
    return called !== undefined && Object.hasOwn(TA_CAPABILITIES, called)
      ? TA_CAPABILITIES[called] : undefined;
  }
  return Object.hasOwn(TOOL_CAPABILITIES, call.name) ? TOOL_CAPABILITIES[call.name] : undefined;
}

export interface AuthorityParts {
  readonly grants: AuthorityGrants;
  /**
   * Keep a refusal receipt of a blocked call, in a unit of its own: the object's policy
   * refusal, under `AUTHORITY_RULE`.
   */
  readonly receipt: (request: { client: string; tool: string; reason: string }) => Promise<void>;
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

  /** That `stamp`'s authority still stands (the owner's always does); else a refusal. */
  stands(stamp: Stamped | null): void {
    if (stamp !== null) {
      this.#standing(stamp);
    }
  }

  /**
   * That `stamp`'s authority stands and holds `capability` where its conversation acts
   * (`null`: refused to a scoped grant); else a refusal with the fixed reason. The owner's
   * and a full grant's hold every capability.
   */
  require(stamp: Stamped | null, capability: ToolCapability | null | undefined): void {
    if (stamp === null) {
      return;
    }
    const grant = this.#standing(stamp);
    if (grant.mode === "full") {
      return;
    }
    if (capability === null) {
      throw new Refused(NO_SHELL);
    }
    if (capability === undefined || stamp.universe === null
        || !grant.holds({ universe: stamp.universe, agent: stamp.agent, capability })) {
      throw new Refused(NOT_GRANTED);
    }
  }

  /** Why `call` is refused under `stamp`, or undefined when it may run. Synchronous. */
  refusal(stamp: Stamped | null, call: GatedCall): string | undefined {
    try {
      this.require(stamp, capabilityOf(call));
      return undefined;
    } catch (error) {
      if (error instanceof Refused) {
        return error.message;
      }
      throw error;
    }
  }

  /**
   * `ta.authority`'s `beforeTool`: the call's block, with its receipt kept, or undefined.
   * The receipt is kept, or its failure logged, before the block returns.
   */
  async decide(stamp: Stamped | null, call: GatedCall): Promise<{ block: string } | undefined> {
    const reason = this.refusal(stamp, call);
    if (reason === undefined) {
      return undefined;
    }
    try {
      await this.#parts.receipt({ client: stamp!.client, tool: call.name, reason });
    } catch {
      console.error("a tool call's refusal receipt could not be kept");  // refused regardless
    }
    return { block: reason };
  }
}
